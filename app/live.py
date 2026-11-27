# -*- coding: utf-8 -*-
"""即时记忆：每轮对话结束就提炼一次，不等攒够阈值。

为什么单独做，而不是复用 autopilot：
  autopilot 是「攒到 30 条再批量整理」，适合事件/方向/决策那种需要上下文才看得出的东西。
  但有一类信息必须当场记：他刚说的稳定事实（年龄、身份、约束、明确的原则）。
  这些拖到第十轮才记，中间那十轮就没法用它。

三条约束：
1. 只写 draft，进不进上下文由他确认——这条不变。
2. 后台线程跑，不阻塞回复；失败静默。
3. 开销要小：一个几百 token 的小请求，不是再跑一遍完整整理。
"""
import json
import threading

from . import ai, db

_RUNNING = set()
_LOCK = threading.Lock()

PROMPT = """他已经有的记忆（**不要再重复这些**，哪怕换个说法也不要）：
{existing}

他从刚才那句话里说的内容：
「{user_text}」

任务：只挑出**值得长期记住的稳定事实**（身份、年龄、职业、约束、长期偏好、
他明确表达过的原则或底线）。没有就返回空数组。

不要挑：一次性的情绪、今天做了什么、对某件事的临时判断、以及你自己说过的话。

输出 JSON：
{{"memory":[{{"statement":"一句陈述，20-60字，用第二人称，不加评价",
  "quote":"他原话里逐字出现的一个片段","confidence":0.0到1.0}}]}}

宁可给空数组，也不要为了交差硬凑。"""


def _norm(s):
    return "".join(ch for ch in (s or "") if ch.isalnum())


def _bigrams(s):
    s = _norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def _similar(a, b):
    """两个指标取大者：Jaccard 抓「换词序/加修饰」，包含度抓「说短了」。

    只用 Jaccard 会漏：模型把「你家里给的生活费是每月一千五」缩成
    「你每月生活费一千五」时 Jaccard 只有 0.31（短句二元组少，并集被拉大），
    但后者的词几乎全都出现在前者里——包含度能量出来。
    """
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    inter = len(A & B)
    jac = inter / len(A | B)
    contain = inter / min(len(A), len(B))
    return max(jac, contain)


def _rule_on(conn, key="live_memory", default=1):
    try:
        row = db.one(conn.execute("SELECT value FROM rules WHERE key=?", (key,)))
        if row:
            return int(float(row["value"])) == 1
    except Exception:      # noqa: BLE001
        pass
    return bool(default)


def _work(conv_id, user_text, user_msg_id):
    conn = db.connect()
    try:
        if not _rule_on(conn):
            return
        # 把已有的记忆喂给模型，让它自己判断「这条已经有了」。
        # 字符相似度对「换种说法」无能为力（实测「接单赚外快」vs「自己接单还能赚一点钱」
        # 只有 0.37），而模型读得懂这是同一件事——用一次提示词省掉一堆重复草稿。
        have = [r["statement"] for r in conn.execute(
            "SELECT statement FROM memory WHERE status IN ('draft','active') "
            "ORDER BY id DESC LIMIT 30")]
        existing_txt = "\n".join(f"- {s}" for s in have) if have else "（暂无）"
        text = ai.chat([{"role": "system", "content": "你是一个只做信息抽取的助手。"},
                        {"role": "user", "content": PROMPT.format(
                            existing=existing_txt, user_text=user_text[:1500])}],
                       temperature=0.2, max_tokens=500)
        data = ai._parse_json(text)
        rows = data.get("memory") or []
        if not rows:
            conn.execute("INSERT INTO reports(kind,payload,result,ok,created_at) VALUES(?,?,?,1,?)",
                         ("live-memory", json.dumps({"conv": conv_id}),
                          json.dumps({"added": 0}), db.now()))
            conn.commit()
            return
        existing = [r["statement"] for r in conn.execute(
            "SELECT statement FROM memory WHERE status IN ('draft','active')")]
        added = []
        for m in rows:
            stmt = (m.get("statement") or "").strip()
            q = (m.get("quote") or "").strip()
            if len(stmt) < 6:
                continue
            # 引用必须真出现在他原话里，否则当模型脑补丢掉
            if not q or q not in (user_text or ""):
                continue
            # 和已有记忆太像就不写（实测同一件事会被反复提炼成不同说法）
            if any(_similar(stmt, e) > 0.55 for e in existing):
                continue
            cur = conn.execute(
                "INSERT INTO memory(statement,kind,source,status,evidence,created_at) "
                "VALUES(?,?,?,'draft',?,?)",
                (stmt, "即时提炼", f"live:{user_msg_id}",
                 json.dumps([user_msg_id]), db.now()))
            added.append({"id": cur.lastrowid, "statement": stmt})
            existing.append(stmt)
        conn.execute("INSERT INTO reports(kind,payload,result,ok,created_at) VALUES(?,?,?,1,?)",
                     ("live-memory", json.dumps({"conv": conv_id}),
                      json.dumps({"added": len(added), "items": added}, ensure_ascii=False),
                      db.now()))
        conn.commit()
    except Exception:      # noqa: BLE001 - 即时记忆失败绝不影响聊天
        pass
    finally:
        with _LOCK:
            _RUNNING.discard(conv_id)
        conn.close()


def tick(conn, conv_id, user_text, user_msg_id):
    """聊天后调用。起后台线程，立即返回，不阻塞回复。"""
    if not user_text or len(user_text.strip()) < 8:
        return ""
    if not _rule_on(conn):
        return ""
    with _LOCK:
        if conv_id in _RUNNING:
            return ""
        _RUNNING.add(conv_id)
    threading.Thread(target=_work,
                     args=(conv_id, user_text, user_msg_id), daemon=True).start()
    return ""


def recent(conn, limit=5):
    """最近即时提炼出的记忆，给界面提示用。"""
    rows = db.rows(conn.execute(
        "SELECT id,statement,status,source FROM memory WHERE source LIKE 'live:%' "
        "ORDER BY id DESC LIMIT ?", (limit,)))
    return rows
