"""
Notion 数据导出脚本
从 Notion 数据库拉取最新数据，生成 docs/data.json
由 GitHub Actions 定时触发，生成后推送到 gh-pages 供 NoCode 看板读取
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "crawlers"))
from notion_client import (
    NotionClient, DB_IDS,
    SCHEMA_NEWS, SCHEMA_MACRO_EVENTS, SCHEMA_MACRO_STATS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
OUTPUT_DIR = Path(__file__).parent.parent / "docs"
OUTPUT_DIR.mkdir(exist_ok=True)


def fetch_news(client: NotionClient, limit=50) -> list[dict]:
    """拉取最新行业快讯，按发布时间倒序"""
    records = client.query_all(DB_IDS["news"], SCHEMA_NEWS)
    records.sort(key=lambda x: x.get("发布时间", ""), reverse=True)
    result = []
    for r in records[:limit]:
        result.append({
            "id": r.get("_id", ""),
            "title": r.get("标题", ""),
            "summary": r.get("摘要", ""),
            "source": r.get("来源", ""),
            "publishTime": r.get("发布时间", "")[:10],
            "link": r.get("链接", ""),
        })
    return result


def fetch_macro_events(client: NotionClient, limit=15) -> list[dict]:
    """拉取关键宏观事件，按发布日期倒序"""
    records = client.query_all(DB_IDS["macro_events"], SCHEMA_MACRO_EVENTS)
    records.sort(key=lambda x: x.get("发布日期", ""), reverse=True)
    result = []
    for r in records[:limit]:
        result.append({
            "id": r.get("_id", ""),
            "title": r.get("标题", ""),
            "summary": r.get("摘要", ""),
            "source": r.get("来源", ""),
            "date": r.get("发布日期", "")[:10],
            "importance": r.get("重要程度", ""),
            "link": r.get("链接", ""),
        })
    return result


def fetch_macro_stats(client: NotionClient) -> list[dict]:
    """拉取月度宏观指标，每个指标只取最新一期"""
    records = client.query_all(DB_IDS["macro_stats"], SCHEMA_MACRO_STATS)
    latest = {}
    for r in records:
        name = r.get("指标名称", "")
        ym = r.get("年月", "")
        if name and (name not in latest or ym > latest[name].get("年月", "")):
            latest[name] = r

    order = ["GDP增速", "CPI同比", "PPI同比", "PMI制造业", "PMI非制造业",
             "社零增速", "固定资产投资增速", "出口增速", "进口增速",
             "城镇调查失业率", "工业增加值增速"]
    result = []
    for name in order:
        if name in latest:
            r = latest[name]
            result.append({
                "name": name,
                "yearMonth": r.get("年月", ""),
                "value": r.get("数值"),
                "unit": r.get("单位", ""),
                "yoy": r.get("同比变化", ""),
                "mom": r.get("环比变化", ""),
            })
    return result


def main():
    logger.info("初始化 Notion 客户端...")
    client = NotionClient()

    logger.info("拉取行业快讯...")
    news = fetch_news(client, limit=50)
    logger.info(f"  → {len(news)} 条")

    logger.info("拉取关键宏观事件...")
    macro_events = fetch_macro_events(client, limit=15)
    logger.info(f"  → {len(macro_events)} 条")

    logger.info("拉取月度宏观指标...")
    macro_stats = fetch_macro_stats(client)
    logger.info(f"  → {len(macro_stats)} 项")

    updated_at = datetime.now(CST).strftime("%Y-%m-%d %H:%M")

    data = {
        "updatedAt": updated_at,
        "news": news,
        "macroEvents": macro_events,
        "macroStats": macro_stats,
    }

    output_path = OUTPUT_DIR / "data.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    logger.info(f"✅ 数据已导出：{output_path}（共 {len(news)} 条快讯，{len(macro_events)} 条宏观事件，{len(macro_stats)} 项指标）")


if __name__ == "__main__":
    main()
