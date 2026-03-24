"""
Notion API 封装层
支持：读取/新增/更新/批量操作 Database 记录
完全替代 feishu_client.py，其他爬虫脚本接口保持一致
"""

import os
import time
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"

# ── Database ID 配置 ──────────────────────────────────────────────────────────
DB_IDS = {
    "news":          "32d03df82d2380b59cb4ed6c9ba29234",  # 行业快讯
    "reports":       "32d03df82d2380dd8552c5c6512d7e56",  # 咨询报告
    "macro_pending": "32d03df82d238084b9d9d779b79f5356",  # 宏观事件筛选
    "macro_events":  "32d03df82d23805ba4b7c6300e8061a4",  # 关键宏观事件
    "macro_stats":   "32d03df82d2380c0ac10f3268800b4f1",  # 月度宏观指标
}


class NotionClient:
    def __init__(self, token: str = None):
        self.token = token or os.environ.get("NOTION_TOKEN", "")
        if not self.token:
            raise ValueError("NOTION_TOKEN 未设置")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        })

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _req(self, method: str, path: str, **kwargs) -> dict:
        """统一请求入口，自动重试"""
        url = f"{BASE_URL}{path}"
        for attempt in range(3):
            try:
                resp = self.session.request(method, url, timeout=20, **kwargs)
                if resp.status_code == 429:  # Rate limit
                    wait = int(resp.headers.get("Retry-After", 10))
                    logger.warning(f"Rate limit，等待 {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.HTTPError as e:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        return {}

    # ── 字段构建器（Python dict → Notion properties）────────────────────────

    @staticmethod
    def _build_props(fields: dict, schema: dict) -> dict:
        """
        根据字段 schema 把普通 dict 转成 Notion properties 格式
        schema 格式: {"字段名": "title"|"rich_text"|"select"|"url"|"date"|"number"}
        """
        props = {}
        for key, value in fields.items():
            if key not in schema:
                continue
            ftype = schema[key]
            val = str(value) if value is not None else ""

            if ftype == "title":
                props[key] = {"title": [{"text": {"content": val[:2000]}}]}
            elif ftype == "rich_text":
                props[key] = {"rich_text": [{"text": {"content": val[:2000]}}]}
            elif ftype == "select":
                props[key] = {"select": {"name": val}} if val else {"select": None}
            elif ftype == "url":
                props[key] = {"url": val} if val else {"url": None}
            elif ftype == "number":
                try:
                    props[key] = {"number": float(val)}
                except (ValueError, TypeError):
                    props[key] = {"number": None}
            elif ftype == "date":
                props[key] = {"date": {"start": val}} if val else {"date": None}
        return props

    @staticmethod
    def _extract_fields(page: dict, schema: dict) -> dict:
        """把 Notion page properties 转回普通 dict"""
        result = {"_id": page["id"]}
        props = page.get("properties", {})
        for key, ftype in schema.items():
            prop = props.get(key, {})
            if ftype == "title":
                items = prop.get("title", [])
                result[key] = items[0]["plain_text"] if items else ""
            elif ftype == "rich_text":
                items = prop.get("rich_text", [])
                result[key] = items[0]["plain_text"] if items else ""
            elif ftype == "select":
                sel = prop.get("select")
                result[key] = sel["name"] if sel else ""
            elif ftype == "url":
                result[key] = prop.get("url") or ""
            elif ftype == "number":
                result[key] = prop.get("number")
            elif ftype == "date":
                d = prop.get("date")
                result[key] = d["start"] if d else ""
        return result

    # ── 核心 CRUD ─────────────────────────────────────────────────────────────

    def query_all(self, db_id: str, schema: dict, filter_obj: dict = None) -> list[dict]:
        """查询 Database 所有记录，自动翻页，返回普通 dict 列表"""
        records = []
        cursor = None
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            if filter_obj:
                body["filter"] = filter_obj
            data = self._req("POST", f"/databases/{db_id}/query", json=body)
            for page in data.get("results", []):
                records.append(self._extract_fields(page, schema))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return records

    def create_page(self, db_id: str, fields: dict, schema: dict) -> dict:
        """新增一条记录"""
        props = self._build_props(fields, schema)
        data = self._req("POST", "/pages", json={
            "parent": {"database_id": db_id},
            "properties": props,
        })
        return data

    def update_page(self, page_id: str, fields: dict, schema: dict) -> dict:
        """更新一条记录"""
        props = self._build_props(fields, schema)
        return self._req("PATCH", f"/pages/{page_id}", json={"properties": props})

    def archive_page(self, page_id: str) -> dict:
        """删除（归档）一条记录"""
        return self._req("PATCH", f"/pages/{page_id}", json={"archived": True})

    def batch_create(self, db_id: str, records: list[dict], schema: dict,
                     delay: float = 0.35) -> int:
        """
        批量新增记录
        Notion API 无批量接口，逐条写入，delay 控制速率（避免 429）
        返回成功写入数量
        """
        count = 0
        for rec in records:
            try:
                self.create_page(db_id, rec, schema)
                count += 1
                time.sleep(delay)
            except Exception as e:
                logger.error(f"写入失败: {e} | 数据: {rec.get(list(schema.keys())[0], '')[:50]}")
        return count

    def upsert(self, db_id: str, records: list[dict], schema: dict,
               key_field: str, delay: float = 0.35) -> dict:
        """
        按 key_field 去重 upsert：已存在则更新，不存在则新增
        返回 {"created": n, "updated": n}
        """
        # 加载已有记录
        existing = self.query_all(db_id, schema)
        existing_map = {r[key_field]: r["_id"] for r in existing if r.get(key_field)}

        created, updated = 0, 0
        for rec in records:
            key = rec.get(key_field)
            try:
                if key and key in existing_map:
                    self.update_page(existing_map[key], rec, schema)
                    updated += 1
                else:
                    self.create_page(db_id, rec, schema)
                    created += 1
                time.sleep(delay)
            except Exception as e:
                logger.error(f"upsert 失败: {e} | key={key}")

        return {"created": created, "updated": updated}

    def cleanup_old(self, db_id: str, schema: dict,
                    date_field: str, keep_days: int = 7) -> int:
        """归档超过 keep_days 天的旧记录，返回归档数量"""
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        records = self.query_all(db_id, schema)
        count = 0
        for r in records:
            val = r.get(date_field, "")
            if val and val[:10] < cutoff:
                try:
                    self.archive_page(r["_id"])
                    count += 1
                    time.sleep(0.35)
                except Exception as e:
                    logger.error(f"归档失败: {e}")
        return count


# ── 各表字段 Schema（供爬虫脚本直接 import 使用）────────────────────────────

SCHEMA_NEWS = {
    "标题":   "title",
    "摘要":   "rich_text",
    "来源":   "select",
    "地区":   "select",
    "发布时间": "rich_text",
    "链接":   "url",
    "唯一ID": "rich_text",
    "抓取时间": "rich_text",
}

SCHEMA_REPORTS = {
    "报告标题": "title",
    "机构":    "select",
    "分类":    "select",
    "摘要":    "rich_text",
    "发布日期": "rich_text",
    "报告链接": "url",
    "唯一ID":  "rich_text",
    "抓取时间": "rich_text",
}

SCHEMA_MACRO_PENDING = {
    "标题":   "title",
    "摘要":   "rich_text",
    "来源":   "select",
    "发布日期": "rich_text",
    "链接":   "url",
    "状态":   "select",   # 待审核 / 已采纳 / 已忽略
    "唯一ID": "rich_text",
    "抓取时间": "rich_text",
}

SCHEMA_MACRO_EVENTS = {
    "标题":    "title",
    "摘要":    "rich_text",
    "来源":    "rich_text",
    "发布日期": "rich_text",
    "链接":    "url",
    "重要程度": "select",   # 高 / 中 / 低
    "更新时间": "rich_text",
}

SCHEMA_MACRO_STATS = {
    "指标名称": "title",
    "年月":    "rich_text",
    "数值":    "number",
    "单位":    "rich_text",
    "同比变化": "rich_text",
    "环比变化": "rich_text",
    "数据来源": "rich_text",
    "更新时间": "rich_text",
    "唯一ID":  "rich_text",
}
