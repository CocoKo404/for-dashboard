"""
看板静态页面生成器
从 Notion 数据库拉取最新数据，渲染成 docs/index.html
由 GitHub Actions 定时触发，生成后部署到 GitHub Pages
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 把 crawlers 目录加入路径，复用 NotionClient
sys.path.insert(0, str(Path(__file__).parent.parent / "crawlers"))
from notion_client import (
    NotionClient, DB_IDS,
    SCHEMA_NEWS, SCHEMA_REPORTS,
    SCHEMA_MACRO_EVENTS, SCHEMA_MACRO_STATS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
OUTPUT_DIR = Path(__file__).parent.parent / "docs"
OUTPUT_DIR.mkdir(exist_ok=True)


# ── 数据拉取 ──────────────────────────────────────────────────────────────────

def fetch_news(client: NotionClient, limit=30) -> list[dict]:
    """拉取最新行业快讯，按抓取时间倒序，取前 limit 条"""
    records = client.query_all(DB_IDS["news"], SCHEMA_NEWS)
    records.sort(key=lambda x: x.get("发布时间", ""), reverse=True)
    return records[:limit]


def fetch_reports(client: NotionClient, limit=12) -> list[dict]:
    """拉取最新咨询报告"""
    records = client.query_all(DB_IDS["reports"], SCHEMA_REPORTS)
    records.sort(key=lambda x: x.get("发布日期", ""), reverse=True)
    return records[:limit]


def fetch_macro_events(client: NotionClient, limit=10) -> list[dict]:
    """拉取已采纳的关键宏观事件"""
    records = client.query_all(DB_IDS["macro_events"], SCHEMA_MACRO_EVENTS)
    records.sort(key=lambda x: x.get("发布日期", ""), reverse=True)
    return records[:limit]


def fetch_macro_stats(client: NotionClient) -> list[dict]:
    """拉取最新一期各指标数据"""
    records = client.query_all(DB_IDS["macro_stats"], SCHEMA_MACRO_STATS)
    # 每个指标只取最新一期
    latest = {}
    for r in records:
        name = r.get("指标名称", "")
        ym = r.get("年月", "")
        if name and (name not in latest or ym > latest[name].get("年月", "")):
            latest[name] = r
    # 按预设顺序排列
    order = ["GDP增速", "CPI同比", "PPI同比", "PMI制造业", "PMI非制造业",
             "社零增速", "固定资产投资增速", "出口增速", "进口增速",
             "城镇调查失业率", "工业增加值增速"]
    result = []
    for name in order:
        if name in latest:
            result.append(latest[name])
    # 补充 order 里没有的
    for name, r in latest.items():
        if name not in order:
            result.append(r)
    return result


# ── HTML 片段生成 ─────────────────────────────────────────────────────────────

def esc(s: str) -> str:
    """HTML 转义"""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


SOURCE_COLORS = {
    "36氪": "#f97316", "虎嗅": "#8b5cf6", "界面新闻": "#0ea5e9",
    "澎湃": "#ef4444", "IT之家": "#10b981", "晚点LatePost": "#6366f1",
    "TechCrunch": "#f59e0b", "The Information": "#64748b",
    "CNBC": "#dc2626", "Reuters": "#b45309", "鞭牛士": "#7c3aed",
    "DoNews": "#0891b2", "TechWeb": "#059669", "网易新闻": "#d97706",
    "新浪财经": "#dc2626",
}

CATEGORY_COLORS = {
    "AI进展": "#6366f1", "云计算": "#0ea5e9", "组织变动": "#f97316",
    "人才变动": "#f59e0b", "业务变动": "#10b981",
}

IMPORTANCE_COLORS = {
    "高": "#ef4444", "中": "#f97316", "低": "#10b981",
}


def source_badge(source: str) -> str:
    color = SOURCE_COLORS.get(source, "#64748b")
    return f'<span class="badge" style="background:{color}20;color:{color};border:1px solid {color}40">{esc(source)}</span>'


def category_badge(cat: str) -> str:
    color = CATEGORY_COLORS.get(cat, "#64748b")
    return f'<span class="badge" style="background:{color}20;color:{color};border:1px solid {color}40">{esc(cat)}</span>'


def importance_badge(imp: str) -> str:
    color = IMPORTANCE_COLORS.get(imp, "#64748b")
    return f'<span class="badge" style="background:{color}20;color:{color};border:1px solid {color}40">{"●"} {esc(imp)}</span>'


def render_news_cards(news_list: list[dict]) -> str:
    if not news_list:
        return '<p class="empty">暂无数据</p>'
    cards = []
    for item in news_list:
        title = esc(item.get("标题", "（无标题）"))
        summary = esc(item.get("摘要", ""))[:120]
        source = item.get("来源", "")
        pub_time = item.get("发布时间", "")[:10]
        link = item.get("链接", "#") or "#"
        cards.append(f"""
        <a class="news-card" href="{esc(link)}" target="_blank" rel="noopener">
          <div class="news-card-meta">
            {source_badge(source)}
            <span class="news-date">{esc(pub_time)}</span>
          </div>
          <h3 class="news-title">{title}</h3>
          <p class="news-summary">{summary}{"…" if len(item.get("摘要","")) > 120 else ""}</p>
        </a>""")
    return "\n".join(cards)


def render_report_cards(reports: list[dict]) -> str:
    if not reports:
        return '<p class="empty">暂无数据</p>'
    cards = []
    for item in reports:
        title = esc(item.get("报告标题", "（无标题）"))
        org = esc(item.get("机构", ""))
        cat = item.get("分类", "")
        summary = esc(item.get("摘要", ""))[:100]
        pub_date = item.get("发布日期", "")[:10]
        link = item.get("报告链接", "#") or "#"
        cards.append(f"""
        <a class="report-card" href="{esc(link)}" target="_blank" rel="noopener">
          <div class="report-card-header">
            <span class="report-org">{org}</span>
            {category_badge(cat) if cat else ""}
          </div>
          <h3 class="report-title">{title}</h3>
          <p class="report-summary">{summary}{"…" if len(item.get("摘要","")) > 100 else ""}</p>
          <span class="report-date">{esc(pub_date)}</span>
        </a>""")
    return "\n".join(cards)


def render_macro_events(events: list[dict]) -> str:
    if not events:
        return '<p class="empty">暂无数据</p>'
    items = []
    for item in events:
        title = esc(item.get("标题", "（无标题）"))
        summary = esc(item.get("摘要", ""))[:150]
        source = esc(item.get("来源", ""))
        pub_date = item.get("发布日期", "")[:10]
        importance = item.get("重要程度", "")
        link = item.get("链接", "#") or "#"
        items.append(f"""
        <a class="event-item" href="{esc(link)}" target="_blank" rel="noopener">
          <div class="event-left">
            <div class="event-date">{esc(pub_date)}</div>
            {importance_badge(importance) if importance else ""}
          </div>
          <div class="event-right">
            <h3 class="event-title">{title}</h3>
            <p class="event-summary">{summary}{"…" if len(item.get("摘要","")) > 150 else ""}</p>
            <span class="event-source">{source}</span>
          </div>
        </a>""")
    return "\n".join(items)


def render_macro_stats(stats: list[dict]) -> str:
    if not stats:
        return '<p class="empty">暂无数据</p>'
    cards = []
    for item in stats:
        name = esc(item.get("指标名称", ""))
        ym = esc(item.get("年月", ""))
        value = item.get("数值")
        unit = esc(item.get("单位", ""))
        yoy = item.get("同比变化", "")
        mom = item.get("环比变化", "")

        value_str = f"{value}{unit}" if value is not None else "—"

        # 同比颜色
        yoy_class = ""
        if yoy:
            try:
                yoy_class = "positive" if float(yoy) >= 0 else "negative"
            except Exception:
                pass

        mom_class = ""
        if mom:
            try:
                mom_class = "positive" if float(mom) >= 0 else "negative"
            except Exception:
                pass

        cards.append(f"""
        <div class="stat-card">
          <div class="stat-name">{name}</div>
          <div class="stat-value">{esc(value_str)}</div>
          <div class="stat-period">{ym}</div>
          <div class="stat-changes">
            <span class="stat-change {yoy_class}">同比 {esc(str(yoy)) if yoy else "—"}</span>
            <span class="stat-change {mom_class}">环比 {esc(str(mom)) if mom else "—"}</span>
          </div>
        </div>""")
    return "\n".join(cards)


# ── 完整 HTML 渲染 ────────────────────────────────────────────────────────────

def render_html(news, reports, events, stats, updated_at: str) -> str:
    news_html = render_news_cards(news)
    reports_html = render_report_cards(reports)
    events_html = render_macro_events(events)
    stats_html = render_macro_stats(stats)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>情报看板</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg: #f8fafc;
      --surface: #ffffff;
      --border: #e2e8f0;
      --text-primary: #0f172a;
      --text-secondary: #64748b;
      --text-muted: #94a3b8;
      --accent: #2563eb;
      --accent-light: #eff6ff;
      --radius: 12px;
      --shadow: 0 1px 3px rgba(0,0,0,.06), 0 4px 16px rgba(0,0,0,.04);
      --shadow-hover: 0 4px 12px rgba(0,0,0,.10), 0 8px 32px rgba(0,0,0,.06);
    }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB",
                   "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text-primary);
      line-height: 1.6;
      min-height: 100vh;
    }}

    /* ── 顶部导航 ── */
    .navbar {{
      position: sticky; top: 0; z-index: 100;
      background: rgba(255,255,255,0.85);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      padding: 0 32px;
      display: flex; align-items: center; justify-content: space-between;
      height: 60px;
    }}
    .navbar-brand {{
      font-size: 17px; font-weight: 700; color: var(--text-primary);
      letter-spacing: -0.3px;
    }}
    .navbar-brand span {{ color: var(--accent); }}
    .navbar-nav {{
      display: flex; gap: 4px;
    }}
    .navbar-nav a {{
      text-decoration: none; color: var(--text-secondary);
      font-size: 14px; font-weight: 500;
      padding: 6px 14px; border-radius: 8px;
      transition: all .15s;
    }}
    .navbar-nav a:hover {{ background: var(--accent-light); color: var(--accent); }}
    .navbar-updated {{
      font-size: 12px; color: var(--text-muted);
    }}

    /* ── 主体布局 ── */
    .main {{
      max-width: 1280px; margin: 0 auto;
      padding: 32px 24px 64px;
      display: flex; flex-direction: column; gap: 48px;
    }}

    /* ── 区块标题 ── */
    .section-header {{
      display: flex; align-items: baseline; gap: 12px;
      margin-bottom: 20px;
    }}
    .section-title {{
      font-size: 20px; font-weight: 700; color: var(--text-primary);
    }}
    .section-subtitle {{
      font-size: 13px; color: var(--text-muted);
    }}
    .section-dot {{
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--accent); flex-shrink: 0;
      margin-bottom: 2px;
    }}

    /* ── 通用 badge ── */
    .badge {{
      display: inline-block;
      font-size: 11px; font-weight: 600;
      padding: 2px 8px; border-radius: 20px;
      white-space: nowrap;
    }}

    /* ── 行业快讯 ── */
    .news-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 16px;
    }}
    .news-card {{
      display: block; text-decoration: none; color: inherit;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px 20px;
      transition: all .2s;
    }}
    .news-card:hover {{
      box-shadow: var(--shadow-hover);
      border-color: #cbd5e1;
      transform: translateY(-2px);
    }}
    .news-card-meta {{
      display: flex; align-items: center; gap: 8px;
      margin-bottom: 10px;
    }}
    .news-date {{ font-size: 12px; color: var(--text-muted); margin-left: auto; }}
    .news-title {{
      font-size: 14px; font-weight: 600; line-height: 1.5;
      color: var(--text-primary); margin-bottom: 8px;
      display: -webkit-box; -webkit-line-clamp: 2;
      -webkit-box-orient: vertical; overflow: hidden;
    }}
    .news-summary {{
      font-size: 13px; color: var(--text-secondary); line-height: 1.6;
      display: -webkit-box; -webkit-line-clamp: 3;
      -webkit-box-orient: vertical; overflow: hidden;
    }}

    /* ── 咨询报告 ── */
    .reports-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 16px;
    }}
    .report-card {{
      display: flex; flex-direction: column;
      text-decoration: none; color: inherit;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
      transition: all .2s;
    }}
    .report-card:hover {{
      box-shadow: var(--shadow-hover);
      border-color: #cbd5e1;
      transform: translateY(-2px);
    }}
    .report-card-header {{
      display: flex; align-items: center; gap: 8px; margin-bottom: 10px;
    }}
    .report-org {{
      font-size: 12px; font-weight: 700; color: var(--accent);
    }}
    .report-title {{
      font-size: 14px; font-weight: 600; line-height: 1.5;
      color: var(--text-primary); margin-bottom: 8px; flex: 1;
      display: -webkit-box; -webkit-line-clamp: 3;
      -webkit-box-orient: vertical; overflow: hidden;
    }}
    .report-summary {{
      font-size: 12px; color: var(--text-secondary); line-height: 1.6;
      margin-bottom: 12px; flex: 1;
      display: -webkit-box; -webkit-line-clamp: 3;
      -webkit-box-orient: vertical; overflow: hidden;
    }}
    .report-date {{
      font-size: 11px; color: var(--text-muted); margin-top: auto;
    }}

    /* ── 宏观事件 ── */
    .events-list {{
      display: flex; flex-direction: column; gap: 12px;
    }}
    .event-item {{
      display: flex; gap: 20px;
      text-decoration: none; color: inherit;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px 20px;
      transition: all .2s;
    }}
    .event-item:hover {{
      box-shadow: var(--shadow-hover);
      border-color: #cbd5e1;
    }}
    .event-left {{
      display: flex; flex-direction: column;
      align-items: center; gap: 8px;
      min-width: 72px;
    }}
    .event-date {{
      font-size: 12px; color: var(--text-muted);
      white-space: nowrap;
    }}
    .event-right {{ flex: 1; }}
    .event-title {{
      font-size: 15px; font-weight: 600;
      color: var(--text-primary); margin-bottom: 6px;
    }}
    .event-summary {{
      font-size: 13px; color: var(--text-secondary); line-height: 1.6;
      margin-bottom: 8px;
    }}
    .event-source {{
      font-size: 12px; color: var(--text-muted);
    }}

    /* ── 宏观指标 ── */
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 14px;
    }}
    .stat-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px 16px;
      transition: all .2s;
    }}
    .stat-card:hover {{
      box-shadow: var(--shadow-hover);
      border-color: #cbd5e1;
    }}
    .stat-name {{
      font-size: 12px; color: var(--text-muted);
      font-weight: 500; margin-bottom: 8px;
    }}
    .stat-value {{
      font-size: 26px; font-weight: 700;
      color: var(--text-primary); line-height: 1.2;
      margin-bottom: 4px;
    }}
    .stat-period {{
      font-size: 11px; color: var(--text-muted); margin-bottom: 10px;
    }}
    .stat-changes {{
      display: flex; gap: 8px; flex-wrap: wrap;
    }}
    .stat-change {{
      font-size: 11px; font-weight: 600;
      padding: 2px 6px; border-radius: 4px;
      background: #f1f5f9; color: var(--text-secondary);
    }}
    .stat-change.positive {{ background: #f0fdf4; color: #16a34a; }}
    .stat-change.negative {{ background: #fef2f2; color: #dc2626; }}

    /* ── 空状态 ── */
    .empty {{
      color: var(--text-muted); font-size: 14px;
      padding: 32px; text-align: center;
    }}

    /* ── 响应式 ── */
    @media (max-width: 768px) {{
      .navbar {{ padding: 0 16px; }}
      .navbar-nav {{ display: none; }}
      .main {{ padding: 20px 16px 48px; gap: 36px; }}
      .news-grid, .reports-grid {{ grid-template-columns: 1fr; }}
      .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
    }}
  </style>
</head>
<body>

<nav class="navbar">
  <div class="navbar-brand">情报<span>看板</span></div>
  <nav class="navbar-nav">
    <a href="#news">行业快讯</a>
    <a href="#reports">咨询报告</a>
    <a href="#events">宏观事件</a>
    <a href="#stats">宏观指标</a>
  </nav>
  <div class="navbar-updated">更新于 {updated_at}</div>
</nav>

<main class="main">

  <!-- 行业快讯 -->
  <section id="news">
    <div class="section-header">
      <div class="section-dot"></div>
      <h2 class="section-title">行业快讯</h2>
      <span class="section-subtitle">每小时自动更新 · 最新 30 条</span>
    </div>
    <div class="news-grid">
      {news_html}
    </div>
  </section>

  <!-- 咨询报告 -->
  <section id="reports">
    <div class="section-header">
      <div class="section-dot" style="background:#8b5cf6"></div>
      <h2 class="section-title">咨询报告</h2>
      <span class="section-subtitle">每日更新 · 最新 12 份</span>
    </div>
    <div class="reports-grid">
      {reports_html}
    </div>
  </section>

  <!-- 宏观事件 -->
  <section id="events">
    <div class="section-header">
      <div class="section-dot" style="background:#f97316"></div>
      <h2 class="section-title">关键宏观事件</h2>
      <span class="section-subtitle">每周更新 · 人工筛选采纳</span>
    </div>
    <div class="events-list">
      {events_html}
    </div>
  </section>

  <!-- 宏观指标 -->
  <section id="stats">
    <div class="section-header">
      <div class="section-dot" style="background:#10b981"></div>
      <h2 class="section-title">月度宏观指标</h2>
      <span class="section-subtitle">每月 15 日更新 · 国家统计局</span>
    </div>
    <div class="stats-grid">
      {stats_html}
    </div>
  </section>

</main>

</body>
</html>"""


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    logger.info("初始化 Notion 客户端...")
    client = NotionClient()

    logger.info("拉取行业快讯...")
    news = fetch_news(client)
    logger.info(f"  → {len(news)} 条")

    logger.info("拉取咨询报告...")
    reports = fetch_reports(client)
    logger.info(f"  → {len(reports)} 份")

    logger.info("拉取关键宏观事件...")
    events = fetch_macro_events(client)
    logger.info(f"  → {len(events)} 条")

    logger.info("拉取月度宏观指标...")
    stats = fetch_macro_stats(client)
    logger.info(f"  → {len(stats)} 项")

    updated_at = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    logger.info("渲染 HTML...")
    html = render_html(news, reports, events, stats, updated_at)

    output_path = OUTPUT_DIR / "index.html"
    output_path.write_text(html, encoding="utf-8")
    logger.info(f"✅ 看板已生成：{output_path}")


if __name__ == "__main__":
    main()
