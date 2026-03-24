"""
从 Excel 文件导入历史宏观指标数据到 Notion macro_stats 数据库
用法：python scripts/import_macro_stats.py --file "M:/宏观数据库/2月宏观数据（含境外数据）HR月报作图用表-20260316.xlsx"
"""

import os
import sys
import argparse
import logging
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "crawlers"))
from notion_client import NotionClient, DB_IDS, SCHEMA_MACRO_STATS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# 指标名称映射：用关键词模糊匹配 → (Notion标准名称, 单位, 转换函数)
# 转换函数：None 表示直接用原值；lambda 表示需要转换（如指数转同比）
INDICATOR_MAP = [
    # CPI：指数值 → 同比（减100）
    ("CPI居民消费价格指数",        "CPI同比",      "%",    lambda v: round(v - 100, 1) if v else None),
    ("CPI（消费者价格指数）",       "CPI同比",      "%",    lambda v: round(v - 100, 1) if v else None),
    # PPI：指数值 → 同比（减100）
    ("PPI工业生产者出厂价格指数",   "PPI同比",      "%",    lambda v: round(v - 100, 1) if v else None),
    ("PPI（工业生产者出厂价格指数）","PPI同比",      "%",    lambda v: round(v - 100, 1) if v else None),
    # 社零：同比增长行（%）
    ("社会消费品零售总额同比增长",  "社零同比",     "%",    None),
    # GDP：不变价指数 → 同比
    ("国内生产总值指数（上年同期=100）", "GDP同比",  "%",    lambda v: round(v - 100, 1) if v else None),
    # 居民收入：累计增长
    ("居民人均可支配收入累计增长",  "居民收入增速", "%",    None),
    # 失业率：城镇调查失业率（平均值）
    ("城镇调查失业率（平均值）",    "城镇调查失业率","%",   None),
    # 31城失业率
    ("31个大城市城镇调查失业率",    "31城失业率",   "%",    None),
    # 青年失业率（16-24岁）
    ("16—24岁劳动力失业率",        "青年失业率",   "%",    None),
    # PMI（如果有）
    ("制造业PMI",                  "PMI制造业",    "",     None),
    ("非制造业PMI",                "PMI非制造业",  "",     None),
    # 工业增加值
    ("工业增加值增速",             "工业增加值增速","%",   None),
    # 进出口
    ("出口总额",                   "出口总额",     "亿美元", None),
    ("进口总额",                   "进口总额",     "亿美元", None),
]


def parse_year_month(val) -> str | None:
    """把 202301、202301.0 等格式转为 2023-01"""
    try:
        s = str(val).strip().split(".")[0]
        if len(s) == 6 and s.isdigit():
            return f"{s[:4]}-{s[4:6]}"
    except Exception:
        pass
    return None


def match_indicator(name: str):
    """模糊匹配指标名，返回 (std_name, unit, transform) 或 None"""
    for keyword, std_name, unit, transform in INDICATOR_MAP:
        if keyword in name:
            return std_name, unit, transform
    return None


def read_data_sheet(filepath: str) -> list[dict]:
    """读取「数据源」sheet，提取月度指标数据"""
    xl = pd.ExcelFile(filepath)

    # 找数据源 sheet
    data_sheet = None
    for name in xl.sheet_names:
        if "数据源" in name and "2" not in name:
            data_sheet = name
            break
    if not data_sheet:
        data_sheet = xl.sheet_names[0]

    logger.info(f"读取 sheet: {data_sheet}")
    df = xl.parse(data_sheet, header=None)

    # 找时间轴行（包含 202301 这样格式的行）
    time_row_idx = None
    for i, row in df.iterrows():
        vals = [str(v) for v in row if pd.notna(v)]
        ym_count = sum(1 for v in vals if parse_year_month(v))
        if ym_count >= 10:
            time_row_idx = i
            break

    if time_row_idx is None:
        logger.error("找不到时间轴行")
        return []

    time_row = df.iloc[time_row_idx]
    # 找月度列索引
    month_cols = {}
    for col_idx, val in enumerate(time_row):
        ym = parse_year_month(val)
        if ym:
            month_cols[col_idx] = ym

    logger.info(f"找到 {len(month_cols)} 个月度列，范围：{min(month_cols.values())} ~ {max(month_cols.values())}")

    records = []
    matched_indicators = set()

    # 遍历数据行
    for i in range(time_row_idx + 1, len(df)):
        row = df.iloc[i]
        indicator_raw = str(row.iloc[2]).strip() if pd.notna(row.iloc[2]) else ""
        if not indicator_raw or indicator_raw == "nan":
            continue

        matched = match_indicator(indicator_raw)
        if not matched:
            continue

        std_name, unit, transform = matched
        # 同一指标只取第一次出现（避免重复行）
        if std_name in matched_indicators:
            continue
        matched_indicators.add(std_name)

        logger.info(f"  处理指标: {std_name}（原名: {indicator_raw[:40]}）")

        # 提取各月数据
        month_values = {}
        for col_idx, ym in month_cols.items():
            raw_val = row.iloc[col_idx] if col_idx < len(row) else None
            if pd.notna(raw_val) and str(raw_val).strip() not in ("", "/", "nan", "暂未公布"):
                try:
                    v = float(raw_val)
                    if transform:
                        v = transform(v)
                    if v is not None:
                        month_values[ym] = v
                except (ValueError, TypeError):
                    pass

        # 计算同比环比
        sorted_months = sorted(month_values.keys())
        for idx, ym in enumerate(sorted_months):
            value = month_values[ym]
            yoy = ""
            mom = ""
            if idx >= 12:
                prev_ym = sorted_months[idx - 12]
                if prev_ym in month_values:
                    try:
                        yoy = f"{value - month_values[prev_ym]:+.2f}"
                    except Exception:
                        pass
            if idx >= 1:
                prev_ym = sorted_months[idx - 1]
                if prev_ym in month_values:
                    try:
                        mom = f"{value - month_values[prev_ym]:+.2f}"
                    except Exception:
                        pass

            records.append({
                "指标名称": std_name,
                "年月": ym,
                "数值": value,
                "单位": unit,
                "同比变化": yoy,
                "环比变化": mom,
                "数据来源": "Excel导入",
                "更新时间": "2026-03-24",
                "唯一ID": f"{std_name}_{ym}",
            })

    logger.info(f"共解析 {len(records)} 条记录，涵盖 {len(matched_indicators)} 个指标")
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Excel 文件路径")
    args = parser.parse_args()

    logger.info(f"读取文件：{args.file}")
    records = read_data_sheet(args.file)

    if not records:
        logger.error("未解析到任何数据，请检查 Excel 格式")
        sys.exit(1)

    logger.info(f"开始写入 {len(records)} 条记录到 Notion...")
    client = NotionClient()
    db_id = DB_IDS["macro_stats"]
    result = client.upsert(db_id, records, SCHEMA_MACRO_STATS, "唯一ID")
    logger.info(f"完成！新增 {result['created']} 条，更新 {result['updated']} 条")


if __name__ == "__main__":
    main()
