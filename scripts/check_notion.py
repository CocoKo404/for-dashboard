"""检查 Notion Token 和 Database 连接状态"""
import requests
import json

TOKEN = "ntn_Z10396746591CGRfib1qODxh9ZHHjBnIb8iNe5UHNpMe8l"

DATABASES = {
    "行业快讯":    "32d03df82d2380b59cb4ed6c9ba29234",
    "咨询报告":    "32d03df82d2380dd8552c5c6512d7e56",
    "宏观事件筛选": "32d03df82d238084b9d9d779b79f5356",
    "关键宏观事件": "32d03df82d23805ba4b7c6300e8061a4",
    "月度宏观指标": "32d03df82d2380c0ac10f3268800b4f1",
}

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

print("=== 逐一验证 Database 连接 ===\n")
all_ok = True
for name, db_id in DATABASES.items():
    resp = requests.get(
        f"https://api.notion.com/v1/databases/{db_id}",
        headers=headers,
    )
    if resp.status_code == 200:
        data = resp.json()
        title = data.get("title", [{}])
        title_text = title[0].get("plain_text", "（无标题）") if title else "（无标题）"
        print(f"  ✅ {name} — ID: {db_id} — Notion标题: {title_text}")
    else:
        err = resp.json().get("message", "未知错误")
        print(f"  ❌ {name} — 失败: {err}")
        all_ok = False

print()
if all_ok:
    print("🎉 所有 Database 连接正常，可以开始写入数据！")
else:
    print("⚠️  有 Database 连接失败，请确认已在 Notion 中授权 Integration 访问该页面。")
