# -*- coding: utf-8 -*-
"""自动整理：上下文积累到阈值后，自动提炼并给出方向草稿。

为什么要有这一层：
  手动点「整理对话」的问题不是麻烦，是**你不会记得点**。聊到第 80 条消息时，
  库里还是空的，看板一片零。系统应该自己判断「差不多可以收一次了」。

三条设计约束（都是为了防止它变成噪音）：
1. 自动跑的只是**候选**：写进 proposals 和 reports，绝不直接写正式表。
2. 后台线程跑，不卡住他的对话框。失败就失败，不影响聊天。
3. 同一会话同时只跑一个；阈值从 rules 表读，用户能自己改。
"""
import threading

from . import ai, db

# 正在跑的会话 id，防止重复触发
_RUNNING = set()
_LOCK = threading.Lock()

DEFAULT_ORGANIZE_EVERY = 30      # 累计多少条未整理消息触发一次
DEFAULT_DIRECTION_EVERY = 60     # 累计多少条消息后给一次方向草稿


def _rule(conn, key, default):
    try:
        row = db.one(conn.execute("SELECT value FROM rules WHERE key=?", (key,)))
        if row and row["value"] is not None:
            return int(float(row["value"]))
    except Exception:      # noqa: BLE001 - 规则读不到就用默认值
        pass
    return default


def status(conn, conv_id):
    """当前积累情况：还要聊多少条才会自动整理。"""
    conv = db.one(conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)))
    if not conv:
        return {"ready": False, "pending_messages": 0, "threshold": DEFAULT_ORGANIZE_EVERY}
    upto = int(conv.get("extracted_upto") or 0)
    n = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND ok=1 AND id>?",
        (conv_id, upto)).fetchone()[0]
    th = _rule(conn, "auto_organize_every", DEFAULT_ORGANIZE_EVERY)
    return {"ready": n >= th, "pending_messages": n, "threshold": th,
            "remaining": max(0, th - n)}


def _work(conv_id):
    """后台任务：整理 -> 视情况给方向草稿。全程只写暂存区和报告。"""
    conn = db.connect()
    try:
        result = ai.extract_from_chat(conn, conv_id)
        total = result.get("proposals_total") or 0
        # 方向草稿：证据够了才跑，否则白烧 token
        have = conn.execute(
            "SELECT (SELECT COUNT(*) FROM events) + (SELECT COUNT(*) FROM topics)"
        ).fetchone()[0]
        th = _rule(conn, "auto_direction_every", DEFAULT_DIRECTION_EVERY)
        msgs = conn.execute("SELECT COUNT(*) FROM messages WHERE ok=1").fetchone()[0]
        if msgs >= th and have >= 1:
            try:
                ai.suggest_direction(conn)
            except Exception:      # noqa: BLE001 - 方向草稿失败不该影响整理结果
                pass
        _save_note(conn, conv_id, f"自动整理完成：{total} 条候选")
    except Exception as e:         # noqa: BLE001 - 后台任务不许把异常抛到聊天里
        try:
            _save_note(conn, conv_id, f"自动整理失败：{ai._redact(str(e))[:120]}")
        except Exception:          # noqa: BLE001
            pass
    finally:
        with _LOCK:
            _RUNNING.discard(conv_id)
        conn.close()


def _save_note(conn, conv_id, text):
    conn.execute("INSERT INTO reports(kind,payload,result,ok,created_at) VALUES(?,?,?,?,?)",
                 ("autopilot", f'{{"conversation_id":{int(conv_id)}}}',
                  f'{{"note":"{text[:200]}"}}', 1, db.now()))
    conn.commit()


def tick(conn, conv_id):
    """聊天后调用。达到阈值就在后台起一次整理，立刻返回，不阻塞回复。

    返回一句可显示给他的提示（或空串）。
    """
    st = status(conn, conv_id)
    if not st["ready"]:
        return ""
    with _LOCK:
        if conv_id in _RUNNING:
            return ""
        _RUNNING.add(conv_id)
    t = threading.Thread(target=_work, args=(conv_id,), daemon=True)
    t.start()
    return f"消息攒到 {st['pending_messages']} 条了，正在后台整理（不挡你聊天）"


def latest_direction(conn):
    """最近一次方向草稿。返回 dict 或 None。"""
    row = db.one(conn.execute(
        "SELECT * FROM reports WHERE kind='direction-draft' ORDER BY id DESC LIMIT 1"))
    if not row:
        return None
    import json
    try:
        data = json.loads(row["result"] or "{}")
    except ValueError:
        return None
    data["_created_at"] = row["created_at"]
    data["_report_id"] = row["id"]
    return data
