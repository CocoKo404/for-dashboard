"""
宏观新闻周度抓取 — 国家新闻网 + 新华社 + 人民日报
每周一早 8 点由 GitHub Actions 触发
抓取上周（周一~周日）的宏观政策/经济新闻
写入飞书「宏观事件待筛选」表，等待人工确认后再推送到「关键宏观事件」表

表：宏观事件待筛选
字段：标题 | 摘要 | 来源 | 发布日期 | 链接 | 状态（待审核/已采纳/已忽略）| 抓取时间

表：关键宏观事件（人工确认后的正式表）
字段：标题 | 摘要 | 来源 | 发布日期 | 链接 | 重要程度（高/中/低）| 更新时间
"""

import os
import re
import time
import hashlib
import logging
from datetime import datetime, timezone, timedelta, date

import requests
from bs4 import BeautifulSoup
from notion_client import NotionClient, DB_IDS, SCHEMA_MACRO_PENDING

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ── 宏观关键词过滤（只保留宏观政策/经济相关新闻）────────────────────────────
MACRO_KEYWORDS = [
    "GDP", "CPI", "PPI", "PMI", "就业", "失业", "通胀", "通缩",
    "货币政策", "财政政策", "利率", "降息", "加息", "存款准备金",
    "经济增长", "经济复苏", "经济下行", "经济数据",
    "国务院", "中央经济工作会议", "政府工作报告", "五年规划",
    "贸易", "出口", "进口", "外贸", "顺差", "逆差",
    "人民币", "汇率", "外汇", "外储",
    "房地产", "楼市", "土地", "房价",
    "消费", "零售", "社会消费品",
    "工业", "制造业", "服务业",
    "就业市场", "劳动力", "人口",
    "科技政策", "产业政策", "数字经济", "新质生产力",
    "美联储", "欧央行", "全球经济", "国际贸易",
]

# ── 信源配置 ─────────────────────────────────────────────────────────────────
SOURCES = [
    {
        "name": "国家新闻网",
        "url": "http://www.news.cn/politics/",
        "type": "html",
        "list_sel": "ul.list li, div.item",
        "title_sel": "a",
        "link_sel": "a",
        "date_sel": "span.time, span.date",
        "base_url": "http://www.news.cn",
    },
    {
        "name": "新华社",
        "url": "http://www.xinhuanet.com/politics/",
        "type": "html",
        "list_sel": "ul.list li, div.item",
        "title_sel": "a",
        "link_sel": "a",
        "date_sel": "span.time",
        "base_url": "http://www.xinhuanet.com",
    },
    {
        "name": "人民日报",
        "url": "http://politics.people.com.cn/GB/1024/index.html",
        "type": "html",
        "list_sel": "ul li, div.list_item",
        "title_sel": "a",
        "link_sel": "a",
        "date_sel": "span.date, span.time",
        "base_url": "http://politics.people.com.cn",
    },
    {
        "name": "国家发改委",
        "url": "https://www.ndrc.gov.cn/xwdt/xwfb/",
        "type": "html",
        "list_sel": "ul.u-list li, div.list-item",
        "title_sel": "a",
        "link_sel": "a",
        "date_sel": "span.date, span.time",
        "base_url": "https://www.ndrc.gov.cn",
    },
    {
        "name": "中国人民银行",
        "url": "http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html",
        "type": "html",
        "list_sel": "ul li, div.list-item",
        "title_sel": "a",
        "link_sel": "a",
        "date_sel": "span.date",
        "base_url": "http://www.pbc.gov.cn",
    },
]


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def make_uid(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def now_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


def get_last_week_range() -> tuple[date, date]:
    """返回上周一和上周日的日期"""
    today = datetime.now(CST).date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday


def is_macro_relevant(title: str) -> bool:
    """判断标题是否与宏观经济相关"""
    return any(kw in title for kw in MACRO_KEYWORDS)


def parse_date_from_text(text: str) -> str:
    """从文本中提取日期，格式化为 YYYY-MM-DD"""
    patterns = [
        r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})",
        r"(\d{2})[年\-/](\d{1,2})[月\-/](\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            year = m.group(1)
            if len(year) == 2:
                year = "20" + year
            return f"{year}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def fetch_html(url: str, timeout: int = 15):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"抓取失败 {url}: {e}")
        return None


# ── 抓取逻辑 ──────────────────────────────────────────────────────────────────

def crawl_source(source: dict, week_start: date, week_end: date) -> list[dict]:
    name = source["name"]
    logger.info(f"抓取 {name} ...")
    soup = fetch_html(source["url"])
    if not soup:
        return []

    articles = []
    items = soup.select(source["list_sel"])[:50]

    for item in items:
        title_el = item.select_one(source["title_sel"])
        if not title_el:
            continue
        title = clean_text(title_el.get_text())
        if not title or len(title) < 5:
            continue

        # 宏观关键词过滤
        if not is_macro_relevant(title):
            continue

        link_el = item.select_one(source["link_sel"])
        href = link_el.get("href", "") if link_el else ""
        if not href:
            continue
        link = href if href.startswith("http") else f"{source['base_url']}{href}"

        date_el = item.select_one(source["date_sel"])
        date_text = clean_text(date_el.get_text()) if date_el else ""
        pub_date = parse_date_from_text(date_text)

        # 日期过滤：只保留上周的新闻
        if pub_date:
            try:
                d = datetime.strptime(pub_date, "%Y-%m-%d").date()
                if not (week_start <= d <= week_end):
                    continue
            except Exception:
                pass

        articles.append({
            "标题": title,
            "摘要": "",  # 摘要需要进入详情页抓取，此处留空，后续可扩展
            "来源": name,
            "发布日期": pub_date,
            "链接": link,
            "状态": "待审核",
            "唯一ID": make_uid(link),
            "抓取时间": now_cst(),
        })

    logger.info(f"  → {name} 获取 {len(articles)} 条宏观新闻")
    return articles


def run():
    client = NotionClient()
    db_id = DB_IDS["macro_pending"]
    week_start, week_end = get_last_week_range()
    logger.info(f"抓取上周新闻: {week_start} ~ {week_end}")

    # 加载已有记录去重
    existing = client.query_all(db_id, SCHEMA_MACRO_PENDING)
    existing_ids = {r.get("唯一ID") for r in existing if r.get("唯一ID")}

    all_new = []
    for source in SOURCES:
        try:
            articles = crawl_source(source, week_start, week_end)
            for art in articles:
                if art["唯一ID"] not in existing_ids:
                    all_new.append(art)
                    existing_ids.add(art["唯一ID"])
        except Exception as e:
            logger.error(f"信源 {source['name']} 异常: {e}")
        time.sleep(1)

    if all_new:
        logger.info(f"写入 {len(all_new)} 条待审核宏观新闻到 Notion...")
        count = client.batch_create(db_id, all_new, SCHEMA_MACRO_PENDING)
        logger.info(f"✅ 完成！写入 {count} 条，请前往 Notion「宏观事件待筛选」表审核，将需要的条目状态改为「已采纳」")
    else:
        logger.info("本周无新宏观新闻")


if __name__ == "__main__":
    run()
