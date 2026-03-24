"""
行业快讯爬虫 — 17 个信源
每小时由 GitHub Actions 触发，抓取最新文章写入飞书多维表格
表名：行业快讯
字段：标题 | 摘要 | 来源 | 发布时间 | 链接 | 抓取时间 | 地区（国内/海外）
"""

import os
import re
import time
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup
from notion_client import NotionClient, DB_IDS, SCHEMA_NEWS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 信源配置 ────────────────────────────────────────────────────────────────
SOURCES = [
    # 国内媒体
    {"name": "36氪",       "region": "国内", "url": "https://36kr.com",                          "type": "html",  "selector": {"list": "div.article-item-wrap", "title": "a.article-item-title", "link": "a.article-item-title", "time": "span.time-tag"}},
    {"name": "虎嗅",       "region": "国内", "url": "https://www.huxiu.com",                      "type": "html",  "selector": {"list": "div.mod-b-info", "title": "h2 a", "link": "h2 a", "time": "span.time"}},
    {"name": "少数派",     "region": "国内", "url": "https://sspai.com/feed",                     "type": "rss"},
    {"name": "IT之家",     "region": "国内", "url": "https://www.ithome.com/rss/",                "type": "rss"},
    {"name": "澎湃科技",   "region": "国内", "url": "https://www.thepaper.cn/channel_25950",      "type": "html",  "selector": {"list": "div.news_li", "title": "h2", "link": "a", "time": "p.time"}},
    {"name": "界面新闻",   "region": "国内", "url": "https://www.jiemian.com/lists/2.html",       "type": "html",  "selector": {"list": "div.article-item", "title": "h3 a", "link": "h3 a", "time": "span.time"}},
    {"name": "晚点LatePost","region": "国内","url": "https://www.latepost.com",                   "type": "html",  "selector": {"list": "div.article-item", "title": "h3 a", "link": "h3 a", "time": "span.date"}},
    {"name": "鞭牛士",     "region": "国内", "url": "https://www.bianews.com/news/flash",         "type": "html",  "selector": {"list": "div.news-item", "title": "a.title", "link": "a.title", "time": "span.time"}},
    {"name": "DoNews",     "region": "国内", "url": "https://www.donews.com/feed",                "type": "rss"},
    {"name": "TechWeb",    "region": "国内", "url": "https://www.techweb.com.cn/feed",            "type": "rss"},
    # 海外媒体
    {"name": "TechCrunch", "region": "海外", "url": "https://techcrunch.com/feed/",               "type": "rss"},
    {"name": "CNBC Tech",  "region": "海外", "url": "https://www.cnbc.com/id/19854910/device/rss/rss.html", "type": "rss"},
    {"name": "BBC Tech",   "region": "海外", "url": "http://feeds.bbci.co.uk/news/technology/rss.xml",      "type": "rss"},
    {"name": "The Verge",  "region": "海外", "url": "https://www.theverge.com/rss/index.xml",     "type": "rss"},
    {"name": "The Information", "region": "海外", "url": "https://www.theinformation.com/feed",   "type": "rss"},
    {"name": "Business Insider", "region": "海外", "url": "https://feeds.businessinsider.com/custom/all", "type": "rss"},
    {"name": "Wired",      "region": "海外", "url": "https://www.wired.com/feed/rss",             "type": "rss"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

CST = timezone(timedelta(hours=8))


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def make_uid(url: str) -> str:
    """用 URL 生成唯一 ID，用于去重"""
    return hashlib.md5(url.encode()).hexdigest()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def now_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


def fetch_html(url: str, timeout: int = 15) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"HTML 抓取失败 {url}: {e}")
        return None


def fetch_rss(url: str, timeout: int = 15) -> list[dict]:
    """解析 RSS/Atom feed，返回文章列表"""
    try:
        import feedparser
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:30]:  # 每次最多取 30 条
            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")
            summary = clean_text(BeautifulSoup(entry.get("summary", ""), "html.parser").get_text())[:300]
            # 解析发布时间
            pub_time = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_time = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M")
            if title and link:
                articles.append({"title": title, "link": link, "summary": summary, "pub_time": pub_time})
        return articles
    except Exception as e:
        logger.warning(f"RSS 解析失败 {url}: {e}")
        return []


# ── 各信源解析器 ──────────────────────────────────────────────────────────────

def parse_36kr(soup: BeautifulSoup) -> list[dict]:
    articles = []
    for item in soup.select("div.article-item-wrap")[:20]:
        a = item.select_one("a.article-item-title")
        if not a:
            continue
        title = clean_text(a.get_text())
        href = a.get("href", "")
        link = href if href.startswith("http") else f"https://36kr.com{href}"
        time_tag = item.select_one("span.time-tag")
        pub_time = clean_text(time_tag.get_text()) if time_tag else ""
        summary_el = item.select_one("p.article-item-description")
        summary = clean_text(summary_el.get_text()) if summary_el else ""
        if title and link:
            articles.append({"title": title, "link": link, "summary": summary[:300], "pub_time": pub_time})
    return articles


def parse_huxiu(soup: BeautifulSoup) -> list[dict]:
    articles = []
    for item in soup.select("div.mod-b-info")[:20]:
        a = item.select_one("h2 a")
        if not a:
            continue
        title = clean_text(a.get_text())
        href = a.get("href", "")
        link = href if href.startswith("http") else f"https://www.huxiu.com{href}"
        time_el = item.select_one("span.time")
        pub_time = clean_text(time_el.get_text()) if time_el else ""
        summary_el = item.select_one("div.article-summary")
        summary = clean_text(summary_el.get_text()) if summary_el else ""
        if title and link:
            articles.append({"title": title, "link": link, "summary": summary[:300], "pub_time": pub_time})
    return articles


def parse_thepaper(soup: BeautifulSoup) -> list[dict]:
    articles = []
    for item in soup.select("div.news_li")[:20]:
        a = item.select_one("h2 a") or item.select_one("a")
        if not a:
            continue
        title = clean_text(a.get_text())
        href = a.get("href", "")
        link = href if href.startswith("http") else f"https://www.thepaper.cn{href}"
        time_el = item.select_one("p.time")
        pub_time = clean_text(time_el.get_text()) if time_el else ""
        if title and link:
            articles.append({"title": title, "link": link, "summary": "", "pub_time": pub_time})
    return articles


def parse_jiemian(soup: BeautifulSoup) -> list[dict]:
    articles = []
    for item in soup.select("div.article-item, li.article-item")[:20]:
        a = item.select_one("h3 a") or item.select_one("a.title")
        if not a:
            continue
        title = clean_text(a.get_text())
        href = a.get("href", "")
        link = href if href.startswith("http") else f"https://www.jiemian.com{href}"
        time_el = item.select_one("span.time, span.date")
        pub_time = clean_text(time_el.get_text()) if time_el else ""
        if title and link:
            articles.append({"title": title, "link": link, "summary": "", "pub_time": pub_time})
    return articles


def parse_latepost(soup: BeautifulSoup) -> list[dict]:
    articles = []
    for item in soup.select("div.article-item, article")[:20]:
        a = item.select_one("h3 a, h2 a, a.title")
        if not a:
            continue
        title = clean_text(a.get_text())
        href = a.get("href", "")
        link = href if href.startswith("http") else f"https://www.latepost.com{href}"
        time_el = item.select_one("span.date, time")
        pub_time = clean_text(time_el.get_text()) if time_el else ""
        summary_el = item.select_one("p.summary, p.desc")
        summary = clean_text(summary_el.get_text()) if summary_el else ""
        if title and link:
            articles.append({"title": title, "link": link, "summary": summary[:300], "pub_time": pub_time})
    return articles


def parse_bianews(soup: BeautifulSoup) -> list[dict]:
    articles = []
    for item in soup.select("div.news-item, li.news-item")[:20]:
        a = item.select_one("a.title, h3 a")
        if not a:
            continue
        title = clean_text(a.get_text())
        href = a.get("href", "")
        link = href if href.startswith("http") else f"https://www.bianews.com{href}"
        time_el = item.select_one("span.time, span.date")
        pub_time = clean_text(time_el.get_text()) if time_el else ""
        if title and link:
            articles.append({"title": title, "link": link, "summary": "", "pub_time": pub_time})
    return articles


HTML_PARSERS = {
    "36氪": parse_36kr,
    "虎嗅": parse_huxiu,
    "澎湃科技": parse_thepaper,
    "界面新闻": parse_jiemian,
    "晚点LatePost": parse_latepost,
    "鞭牛士": parse_bianews,
}


# ── 主抓取逻辑 ────────────────────────────────────────────────────────────────

def crawl_source(source: dict) -> list[dict]:
    name = source["name"]
    region = source["region"]
    logger.info(f"抓取 {name} ...")

    raw_articles = []
    if source["type"] == "rss":
        raw_articles = fetch_rss(source["url"])
    else:
        soup = fetch_html(source["url"])
        if soup and name in HTML_PARSERS:
            raw_articles = HTML_PARSERS[name](soup)

    # 统一格式化为飞书字段
    result = []
    for art in raw_articles:
        if not art.get("title") or not art.get("link"):
            continue
        result.append({
            "标题": art["title"],
            "摘要": art.get("summary", "")[:500],
            "来源": name,
            "地区": region,
            "发布时间": art.get("pub_time", ""),
            "链接": art["link"],
            "唯一ID": make_uid(art["link"]),
            "抓取时间": now_cst(),
        })
    logger.info(f"  → {name} 获取 {len(result)} 条")
    return result


def run():
    client = NotionClient()
    db_id = DB_IDS["news"]

    # 获取已有记录的唯一ID集合，用于去重
    logger.info("加载已有快讯记录...")
    existing = client.query_all(db_id, SCHEMA_NEWS)
    existing_ids = {r.get("唯一ID") for r in existing if r.get("唯一ID")}
    logger.info(f"已有 {len(existing_ids)} 条记录")

    # 抓取所有信源
    all_new = []
    for source in SOURCES:
        try:
            articles = crawl_source(source)
            for art in articles:
                if art["唯一ID"] not in existing_ids:
                    all_new.append(art)
                    existing_ids.add(art["唯一ID"])
        except Exception as e:
            logger.error(f"信源 {source['name']} 抓取异常: {e}")
        time.sleep(1)

    # 写入 Notion
    if all_new:
        logger.info(f"写入 {len(all_new)} 条新快讯到 Notion...")
        count = client.batch_create(db_id, all_new, SCHEMA_NEWS)
        logger.info(f"写入完成，成功 {count} 条")
    else:
        logger.info("无新快讯")

    # 清理 7 天前旧记录
    cleaned = client.cleanup_old(db_id, SCHEMA_NEWS, date_field="抓取时间", keep_days=7)
    if cleaned:
        logger.info(f"已清理 {cleaned} 条旧记录")


if __name__ == "__main__":
    run()
