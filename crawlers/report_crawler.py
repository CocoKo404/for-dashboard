"""
咨询报告爬虫 — 26 个机构
每天由 GitHub Actions 触发，抓取最新报告写入飞书多维表格
表名：咨询报告
字段：报告标题 | 机构 | 摘要 | 发布日期 | 报告链接 | 分类 | 唯一ID | 抓取时间
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
from notion_client import NotionClient, DB_IDS, SCHEMA_REPORTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}

# ── 机构配置 ─────────────────────────────────────────────────────────────────
# type: rss | html | search
# category: 战略咨询 | 人力资源 | 科技研究 | 职场趋势
SOURCES = [
    # 战略咨询
    {"name": "McKinsey",      "category": "战略咨询", "type": "html",
     "url": "https://www.mckinsey.com/featured-insights",
     "list_sel": "article.BasicCard", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "time, span.date", "summary_sel": "p"},

    {"name": "BCG",           "category": "战略咨询", "type": "html",
     "url": "https://www.bcg.com/publications",
     "list_sel": "div.card-body, article.card", "title_sel": "h3, h4, .card-title", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p.card-text, p.description"},

    {"name": "Bain",          "category": "战略咨询", "type": "html",
     "url": "https://www.bain.com/insights/",
     "list_sel": "div.insight-card, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "Roland Berger", "category": "战略咨询", "type": "html",
     "url": "https://www.rolandberger.com/en/Insights/Publications/",
     "list_sel": "div.publication-item, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "Oliver Wyman",  "category": "战略咨询", "type": "html",
     "url": "https://www.oliverwyman.com/our-expertise/insights.html",
     "list_sel": "div.insight-card, article.card", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    # 四大会计师事务所
    {"name": "Deloitte",      "category": "战略咨询", "type": "html",
     "url": "https://www2.deloitte.com/us/en/insights/topics/talent/human-capital-trends.html",
     "list_sel": "article, div.article-card", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "PwC",           "category": "战略咨询", "type": "html",
     "url": "https://www.pwc.com/gx/en/research-insights.html",
     "list_sel": "div.card, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "KPMG",          "category": "战略咨询", "type": "html",
     "url": "https://kpmg.com/xx/en/home/insights.html",
     "list_sel": "div.card, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "EY",            "category": "战略咨询", "type": "html",
     "url": "https://www.ey.com/en_gl/insights",
     "list_sel": "div.card, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    # 人力资源咨询
    {"name": "Mercer",        "category": "人力资源", "type": "html",
     "url": "https://www.mercer.com/insights/",
     "list_sel": "div.insight-card, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "Korn Ferry",    "category": "人力资源", "type": "html",
     "url": "https://www.kornferry.com/insights",
     "list_sel": "div.insight-card, article.card", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "Aon Hewitt",    "category": "人力资源", "type": "html",
     "url": "https://www.aon.com/human-capital-consulting/thought-leadership.aspx",
     "list_sel": "div.article-item, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "WTW",           "category": "人力资源", "type": "html",
     "url": "https://www.wtwco.com/en-US/Insights",
     "list_sel": "div.card, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "ManpowerGroup", "category": "人力资源", "type": "html",
     "url": "https://www.manpowergroup.com/workforce-insights/",
     "list_sel": "div.insight-card, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "Randstad",      "category": "人力资源", "type": "html",
     "url": "https://www.randstad.com/workforce-insights/",
     "list_sel": "div.card, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    # 职场/招聘平台
    {"name": "LinkedIn",      "category": "职场趋势", "type": "html",
     "url": "https://www.linkedin.com/business/talent/blog/",
     "list_sel": "article, div.blog-card", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "time, span.date", "summary_sel": "p"},

    {"name": "脉脉",          "category": "职场趋势", "type": "html",
     "url": "https://maimai.cn/article/dynamic?type=report",
     "list_sel": "div.article-item", "title_sel": "h3, a.title", "link_sel": "a", "date_sel": "span.time", "summary_sel": "p"},

    {"name": "智联招聘",      "category": "职场趋势", "type": "html",
     "url": "https://www.zhaopin.com/research/",
     "list_sel": "div.report-item, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "猎聘",          "category": "职场趋势", "type": "html",
     "url": "https://www.liepin.com/zhaopin/report/",
     "list_sel": "div.report-item, li.item", "title_sel": "h3, a.title", "link_sel": "a", "date_sel": "span.date", "summary_sel": "p"},

    {"name": "BOSS直聘",      "category": "职场趋势", "type": "html",
     "url": "https://www.zhipin.com/public/report/",
     "list_sel": "div.report-item, li", "title_sel": "h3, a", "link_sel": "a", "date_sel": "span.date", "summary_sel": "p"},

    {"name": "牛客",          "category": "职场趋势", "type": "html",
     "url": "https://www.nowcoder.com/discuss/career",
     "list_sel": "div.discuss-item, li.item", "title_sel": "a.title", "link_sel": "a.title", "date_sel": "span.time", "summary_sel": "p"},

    # 科技研究
    {"name": "Gartner",       "category": "科技研究", "type": "html",
     "url": "https://www.gartner.com/en/newsroom",
     "list_sel": "article, div.press-release-item", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "time, span.date", "summary_sel": "p"},

    {"name": "IDC",           "category": "科技研究", "type": "html",
     "url": "https://www.idc.com/getdoc.jsp?containerId=prUS52600424",
     "list_sel": "div.press-release, article", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "span.date, time", "summary_sel": "p"},

    {"name": "Microsoft Work Trend", "category": "科技研究", "type": "html",
     "url": "https://www.microsoft.com/en-us/worklab/work-trend-index",
     "list_sel": "article, div.card", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "time, span.date", "summary_sel": "p"},

    {"name": "Anthropic",     "category": "科技研究", "type": "html",
     "url": "https://www.anthropic.com/research",
     "list_sel": "article, div.research-item", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "time, span.date", "summary_sel": "p"},

    {"name": "OpenAI",        "category": "科技研究", "type": "html",
     "url": "https://openai.com/research",
     "list_sel": "article, div.research-card", "title_sel": "h3, h4", "link_sel": "a", "date_sel": "time, span.date", "summary_sel": "p"},

    {"name": "Google",        "category": "科技研究", "type": "html",
     "url": "https://research.google/pubs/",
     "list_sel": "div.pub-item, article", "title_sel": "h3, h4, a.pub-title", "link_sel": "a", "date_sel": "span.year, time", "summary_sel": "p"},
]


# ── 通用 HTML 解析器 ──────────────────────────────────────────────────────────

def make_uid(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def now_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


def fetch_html(url: str, timeout: int = 20) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"HTML 抓取失败 {url}: {e}")
        return None


def parse_generic(soup: BeautifulSoup, source: dict, base_url: str) -> list[dict]:
    """通用解析器，基于 CSS selector 配置"""
    articles = []
    items = soup.select(source["list_sel"])[:15]
    for item in items:
        # 标题
        title_el = item.select_one(source["title_sel"])
        if not title_el:
            continue
        title = clean_text(title_el.get_text())
        if not title or len(title) < 5:
            continue

        # 链接
        link_el = item.select_one(source["link_sel"])
        href = link_el.get("href", "") if link_el else ""
        if not href:
            continue
        link = href if href.startswith("http") else f"{base_url.rstrip('/')}/{href.lstrip('/')}"

        # 日期
        date_el = item.select_one(source["date_sel"])
        pub_date = clean_text(date_el.get_text()) if date_el else ""

        # 摘要
        summary_el = item.select_one(source["summary_sel"])
        summary = clean_text(summary_el.get_text())[:400] if summary_el else ""

        articles.append({
            "报告标题": title,
            "机构": source["name"],
            "分类": source["category"],
            "摘要": summary,
            "发布日期": pub_date,
            "报告链接": link,
            "唯一ID": make_uid(link),
            "抓取时间": now_cst(),
        })
    return articles


def get_base_url(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# ── 主逻辑 ────────────────────────────────────────────────────────────────────

def crawl_source(source: dict) -> list[dict]:
    name = source["name"]
    logger.info(f"抓取报告: {name} ...")
    soup = fetch_html(source["url"])
    if not soup:
        return []
    base_url = get_base_url(source["url"])
    articles = parse_generic(soup, source, base_url)
    logger.info(f"  → {name} 获取 {len(articles)} 篇报告")
    return articles


def run():
    client = NotionClient()
    db_id = DB_IDS["reports"]

    logger.info("加载已有报告记录...")
    existing = client.query_all(db_id, SCHEMA_REPORTS)
    existing_ids = {r.get("唯一ID") for r in existing if r.get("唯一ID")}
    logger.info(f"已有 {len(existing_ids)} 条记录")

    all_new = []
    for source in SOURCES:
        try:
            reports = crawl_source(source)
            for rep in reports:
                if rep["唯一ID"] not in existing_ids:
                    all_new.append(rep)
                    existing_ids.add(rep["唯一ID"])
        except Exception as e:
            logger.error(f"机构 {source['name']} 抓取异常: {e}")
        time.sleep(2)

    if all_new:
        logger.info(f"写入 {len(all_new)} 篇新报告到 Notion...")
        count = client.batch_create(db_id, all_new, SCHEMA_REPORTS)
        logger.info(f"写入完成，成功 {count} 篇")
    else:
        logger.info("无新报告")


if __name__ == "__main__":
    run()
