# -*- coding: utf-8 -*-
"""打印数据库现状：有哪些表、各多少行、分别干什么。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import db  # noqa: E402

PURPOSE = {
    "events": "事实层：发生了什么",
    "traits": "性格画像（AI 结论，需你采纳）",
    "topics": "学习方向",
    "topic_sessions": "方向投入流水",
    "decisions": "决策（含否决理由 / 二阶效应）",
    "people": "人际对象",
    "interactions": "付出/回报流水",
    "spend": "支出与冷静期",
    "memory": "长期记忆（你确认的事实）",
    "conversations": "对话会话",
    "messages": "对话消息",
    "summaries": "对话滚动摘要",
    "rules": "风控阈值",
    "alert_acks": "预警处置留档",
    "reports": "AI 原始返回留档",
    "events_fts": "事件全文索引",
    "messages_fts": "对话全文索引",
}

c = db.connect()
print("数据库文件:", db.DB_PATH)
print()
print("%-18s %-6s %s" % ("表名", "行数", "用途"))
print("-" * 62)
names = [r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
for n in names:
    if n.endswith(("_data", "_idx", "_config", "_docsize", "_content")):
        continue
    try:
        cnt = c.execute("SELECT COUNT(*) FROM %s" % n).fetchone()[0]
    except Exception:
        cnt = "-"
    print("%-18s %-6s %s" % (n, cnt, PURPOSE.get(n, "")))
print()
print("库大小: %.1f KB" % (os.path.getsize(db.DB_PATH) / 1024))
print("全文索引条数: events_fts=%d messages_fts=%d" % (
    c.execute("SELECT COUNT(*) FROM events_fts").fetchone()[0],
    c.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]))
