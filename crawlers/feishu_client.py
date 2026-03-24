"""
飞书多维表格 API 封装层
支持：获取 token、读写多维表格记录、批量操作
"""

import os
import time
import requests
from typing import Optional


class FeishuClient:
    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str = None, app_secret: str = None):
        self.app_id = app_id or os.environ["FEISHU_APP_ID"]
        self.app_secret = app_secret or os.environ["FEISHU_APP_SECRET"]
        self._token: Optional[str] = None
        self._token_expires: float = 0

    # ── Token 管理 ──────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """获取 tenant_access_token，自动续期"""
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        resp = requests.post(
            f"{self.BASE_URL}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 Token 获取失败: {data}")
        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200)
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}

    # ── 多维表格 CRUD ────────────────────────────────────────────────────────

    def list_records(self, app_token: str, table_id: str, page_size: int = 500, filter_str: str = None) -> list:
        """获取表格所有记录（自动翻页）"""
        records = []
        page_token = None
        while True:
            params = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            if filter_str:
                params["filter"] = filter_str
            resp = requests.get(
                f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                headers=self._headers(),
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"list_records 失败: {data}")
            items = data["data"].get("items", [])
            records.extend(items)
            if not data["data"].get("has_more"):
                break
            page_token = data["data"].get("page_token")
        return records

    def create_record(self, app_token: str, table_id: str, fields: dict) -> dict:
        """新增一条记录"""
        resp = requests.post(
            f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            headers=self._headers(),
            json={"fields": fields},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"create_record 失败: {data}")
        return data["data"]["record"]

    def batch_create_records(self, app_token: str, table_id: str, records: list[dict]) -> list:
        """批量新增记录（每批最多 500 条）"""
        results = []
        for i in range(0, len(records), 500):
            batch = records[i : i + 500]
            resp = requests.post(
                f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                headers=self._headers(),
                json={"records": [{"fields": r} for r in batch]},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"batch_create_records 失败: {data}")
            results.extend(data["data"].get("records", []))
        return results

    def update_record(self, app_token: str, table_id: str, record_id: str, fields: dict) -> dict:
        """更新一条记录"""
        resp = requests.put(
            f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            headers=self._headers(),
            json={"fields": fields},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"update_record 失败: {data}")
        return data["data"]["record"]

    def batch_delete_records(self, app_token: str, table_id: str, record_ids: list[str]) -> None:
        """批量删除记录"""
        for i in range(0, len(record_ids), 500):
            batch = record_ids[i : i + 500]
            resp = requests.post(
                f"{self.BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
                headers=self._headers(),
                json={"records": batch},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"batch_delete_records 失败: {data}")

    def upsert_by_key(self, app_token: str, table_id: str, key_field: str, records: list[dict]) -> dict:
        """
        按 key_field 去重 upsert：
        - 已存在 → 更新
        - 不存在 → 新增
        返回 {"created": n, "updated": n}
        """
        existing = self.list_records(app_token, table_id)
        existing_map = {r["fields"].get(key_field): r["record_id"] for r in existing if key_field in r["fields"]}

        to_create, to_update = [], []
        for rec in records:
            key = rec.get(key_field)
            if key and key in existing_map:
                to_update.append((existing_map[key], rec))
            else:
                to_create.append(rec)

        created = len(self.batch_create_records(app_token, table_id, to_create)) if to_create else 0
        updated = 0
        for record_id, fields in to_update:
            self.update_record(app_token, table_id, record_id, fields)
            updated += 1

        return {"created": created, "updated": updated}
