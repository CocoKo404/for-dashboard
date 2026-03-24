"""
宏观事件爬虫
信源：
  1. 国家统计局 - 统计数据发布 https://www.stats.gov.cn/sj/zxfb/
  2. 国家统计局 - 新闻发布会 https://www.stats.gov.cn/xw/xwfbh/
抓取最新统计数据发布写入 Notion「关键宏观事件」数据库
"""

import os
import sys
import time
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from notion_client import NotionClient, DB_IDS, SCHEMA_MACRO_EVENTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.stats.gov.cn/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

BASE_URL = "https://www.stats.gov.cn"

# 信源列表：(列表页URL, 来源标签)
SOURCES = [
    ("https://www.stats.gov.cn/sj/zxfb/", "国家统计局-数据发布"),   # 最新数据发布
    ("https://www.stats.gov.cn/xw/xwfbh/", "国家统计局-新闻发布会"),  # 新闻发布会
]

# 经济统计相关关键词（用于过滤非统计内容）
ECON_KEYWORDS = [
    "GDP", "CPI", "PPI", "PMI", "工业", "消费", "投资", "出口", "进口", "贸易",
    "就业", "失业", "居民收入", "社会消费品", "固定资产", "房地产", "价格指数",
    "增速", "增长", "同比", "环比", "季度", "月份", "统计数据", "经济运行",
    "制造业", "服务业", "农业", "粮食", "能源", "电力", "货运", "外汇",
]


def make_uid(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]


def is_econ_related(title: str, summary: str = "") -> bool:
    """判断是否为经济统计相关内容"""
    text = title + summary
    return any(kw in text for kw in ECON_KEYWORDS)


def fetch_list(list_url: str, source_label: str) -> list[dict]:
    """抓取列表页，返回文章基本信息"""
    try:
        resp = requests.get(list_url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"列表页请求失败 {list_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []

    # 国家统计局列表结构：<ul class="list_009"> 或 <ul class="news_list">
    list_items = (
        soup.select("ul.list_009 li") or
        soup.select("ul.news_list li") or
        soup.select(".news-list li") or
        soup.select("ul li")
    )

    for item in list_items:
        a_tag = item.find("a")
        if not a_tag:
            continue
        title = a_tag.get_text(strip=True)
        if not title or len(title) < 4:
            continue

        href = a_tag.get("href", "")
        if not href:
            continue
        if href.startswith("http"):
            link = href
        elif href.startswith("/"):
            link = BASE_URL + href
        else:
            # 相对路径，基于列表页 URL 拼接
            base_dir = list_url.rsplit("/", 1)[0]
            link = base_dir + "/" + href

        # 日期
        pub_date = ""
        date_tag = item.find("span") or item.find("em")
        if date_tag:
            date_text = date_tag.get_text(strip=True)
            if len(date_text) >= 8 and any(c.isdigit() for c in date_text):
                pub_date = date_text.replace(".", "-").replace("/", "-")[:10]

        articles.append({
            "title": title,
            "link": link,
            "pub_date": pub_date,
            "source": source_label,
        })

    logger.info(f"[{source_label}] 找到 {len(articles)} 条")
    return articles


def fetch_detail(url: str) -> str:
    """抓取文章详情页，提取正文摘要（只抓 stats.gov.cn 域名）"""
    if "stats.gov.cn" not in url:
        return ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for selector in [".TRS_Editor", ".article-content", "#zoom", ".content", "article"]:
            content = soup.select_one(selector)
            if content:
                text = content.get_text(separator=" ", strip=True)
                if len(text) > 50:
                    return text[:500]

        paragraphs = soup.find_all("p")
        if paragraphs:
            longest = max(paragraphs, key=lambda p: len(p.get_text()))
            return longest.get_text(strip=True)[:500]
    except Exception as e:
        logger.warning(f"详情页抓取失败 {url}: {e}")
    return ""


def judge_importance(title: str, summary: str) -> str:
    """根据标题和摘要判断重要程度"""
    text = title + summary
    high_keywords = [
        "GDP", "国内生产总值", "CPI", "PPI", "PMI", "就业", "失业",
        "经济增速", "年度", "季度", "全年", "全国", "经济运行",
        "居民收入", "固定资产投资", "社会消费品零售",
    ]
    low_keywords = ["通知", "公告", "征求意见", "培训", "座谈", "会议纪要"]
    for kw in high_keywords:
        if kw in text:
            return "高"
    for kw in low_keywords:
        if kw in text:
            return "低"
    return "中"


def run(pages: int = 2):
    """主入口：抓取各信源写入 Notion"""
    client = NotionClient()
    db_id = DB_IDS["macro_events"]

    # 加载已有记录去重
    existing = client.query_all(db_id, SCHEMA_MACRO_EVENTS)
    existing_links = {r.get("链接", "") for r in existing if r.get("链接")}
    logger.info(f"已有 {len(existing_links)} 条记录")

    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    all_new = []

    for list_url, source_label in SOURCES:
        articles = fetch_list(list_url, source_label)
        for art in articles:
            if art["link"] in existing_links:
                continue
            # 只保留经济统计相关内容
            if not is_econ_related(art["title"]):
                continue

            summary = fetch_detail(art["link"])
            importance = judge_importance(art["title"], summary)

            all_new.append({
                "标题": art["title"],
                "摘要": (summary[:500] if summary else art["title"])[:500],
                "来源": art["source"],
                "发布日期": art["pub_date"],
                "链接": art["link"],
                "重要程度": importance,
                "更新时间": now,
            })
            existing_links.add(art["link"])
            logger.info(f"  [{importance}] {art['title'][:40]}")
            time.sleep(0.3)
        time.sleep(1)

    if all_new:
        logger.info(f"写入 {len(all_new)} 条宏观事件到 Notion...")
        count = client.batch_create(db_id, all_new, SCHEMA_MACRO_EVENTS)
        logger.info(f"✅ 完成！写入 {count} 条")
    else:
        logger.info("无新增宏观事件（可能已全部存在或无经济相关内容）")


if __name__ == "__main__":
    run()
