"""
Notion Database 字段初始化脚本
自动为 5 个 Database 添加所有需要的字段（属性）
运行一次即可，已存在的字段会跳过
"""

import sys
import requests

TOKEN = "ntn_Z10396746591CGRfib1qODxh9ZHHjBnIb8iNe5UHNpMe8l"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

BASE = "https://api.notion.com/v1"

# ── 各表字段定义 ──────────────────────────────────────────────────────────────
# 格式: (字段名, Notion类型, 额外配置)
# 注意: title 类型字段 Notion 每个 Database 只能有一个，且默认已存在（名叫"名称"），需要重命名

DATABASES = {
    "行业快讯": {
        "id": "32d03df82d2380b59cb4ed6c9ba29234",
        "title_field": "标题",   # 把默认 title 字段重命名为这个
        "fields": [
            ("摘要",   "rich_text", {}),
            ("来源",   "select",    {}),
            ("地区",   "select",    {}),
            ("发布时间", "rich_text", {}),
            ("链接",   "url",       {}),
            ("唯一ID", "rich_text", {}),
            ("抓取时间", "rich_text", {}),
        ]
    },
    "咨询报告": {
        "id": "32d03df82d2380dd8552c5c6512d7e56",
        "title_field": "报告标题",
        "fields": [
            ("机构",   "select",    {}),
            ("分类",   "select",    {}),
            ("摘要",   "rich_text", {}),
            ("发布日期", "rich_text", {}),
            ("报告链接", "url",       {}),
            ("唯一ID", "rich_text", {}),
            ("抓取时间", "rich_text", {}),
        ]
    },
    "宏观事件筛选": {
        "id": "32d03df82d238084b9d9d779b79f5356",
        "title_field": "标题",
        "fields": [
            ("摘要",   "rich_text", {}),
            ("来源",   "select",    {}),
            ("发布日期", "rich_text", {}),
            ("链接",   "url",       {}),
            ("状态",   "select",    {"options": [
                {"name": "待审核", "color": "yellow"},
                {"name": "已采纳", "color": "green"},
                {"name": "已忽略", "color": "gray"},
            ]}),
            ("唯一ID", "rich_text", {}),
            ("抓取时间", "rich_text", {}),
        ]
    },
    "关键宏观事件": {
        "id": "32d03df82d23805ba4b7c6300e8061a4",
        "title_field": "标题",
        "fields": [
            ("摘要",    "rich_text", {}),
            ("来源",    "rich_text", {}),
            ("发布日期", "rich_text", {}),
            ("链接",    "url",       {}),
            ("重要程度", "select",    {"options": [
                {"name": "高", "color": "red"},
                {"name": "中", "color": "orange"},
                {"name": "低", "color": "blue"},
            ]}),
            ("更新时间", "rich_text", {}),
        ]
    },
    "月度宏观指标": {
        "id": "32d03df82d2380c0ac10f3268800b4f1",
        "title_field": "指标名称",
        "fields": [
            ("年月",    "rich_text", {}),
            ("数值",    "number",    {}),
            ("单位",    "rich_text", {}),
            ("同比变化", "rich_text", {}),
            ("环比变化", "rich_text", {}),
            ("数据来源", "rich_text", {}),
            ("更新时间", "rich_text", {}),
            ("唯一ID",  "rich_text", {}),
        ]
    },
}

# Notion 字段类型映射
TYPE_MAP = {
    "rich_text": {"rich_text": {}},
    "select":    {"select": {}},
    "url":       {"url": {}},
    "number":    {"number": {"format": "number"}},
    "date":      {"date": {}},
}


def get_existing_props(db_id: str) -> dict:
    """获取 Database 已有字段"""
    resp = requests.get(f"{BASE}/databases/{db_id}", headers=HEADERS)
    resp.raise_for_status()
    return resp.json().get("properties", {})


def rename_title_field(db_id: str, old_name: str, new_name: str):
    """重命名 title 字段"""
    if old_name == new_name:
        return
    body = {"properties": {old_name: {"name": new_name}}}
    resp = requests.patch(f"{BASE}/databases/{db_id}", headers=HEADERS, json=body)
    if resp.status_code == 200:
        print(f"    ✅ title 字段重命名: {old_name} → {new_name}")
    else:
        print(f"    ⚠️  title 重命名失败: {resp.json().get('message')}")


def add_field(db_id: str, field_name: str, field_type: str, extra: dict):
    """添加一个字段"""
    type_config = TYPE_MAP.get(field_type, {"rich_text": {}}).copy()

    # select 类型带预设选项
    if field_type == "select" and extra.get("options"):
        type_config = {"select": {"options": extra["options"]}}

    body = {"properties": {field_name: type_config}}
    resp = requests.patch(f"{BASE}/databases/{db_id}", headers=HEADERS, json=body)
    if resp.status_code == 200:
        print(f"    ✅ 添加字段: {field_name} ({field_type})")
    else:
        msg = resp.json().get("message", "未知错误")
        print(f"    ❌ 添加失败: {field_name} — {msg}")


def init_database(name: str, config: dict):
    db_id = config["id"]
    print(f"\n📋 初始化「{name}」(ID: {db_id})")

    # 获取已有字段
    try:
        existing = get_existing_props(db_id)
    except Exception as e:
        print(f"  ❌ 无法访问 Database: {e}")
        return

    existing_names = set(existing.keys())
    print(f"  已有字段: {', '.join(existing_names)}")

    # 重命名 title 字段（Notion 默认叫"名称"或"Name"）
    title_field = config["title_field"]
    for name_candidate in ["名称", "Name", "Title", "标题", "报告标题", "指标名称"]:
        if name_candidate in existing_names:
            prop = existing[name_candidate]
            if prop.get("type") == "title":
                rename_title_field(db_id, name_candidate, title_field)
                existing_names.discard(name_candidate)
                existing_names.add(title_field)
                break

    # 添加缺失字段
    for field_name, field_type, extra in config["fields"]:
        if field_name in existing_names:
            print(f"    ⏭️  跳过已存在: {field_name}")
        else:
            add_field(db_id, field_name, field_type, extra)


def main():
    print("=== Notion Database 字段初始化 ===")
    for db_name, config in DATABASES.items():
        init_database(db_name, config)
    print("\n\n🎉 初始化完成！请前往 Notion 检查各表字段是否正确。")


if __name__ == "__main__":
    main()
