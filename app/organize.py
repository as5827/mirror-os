# -*- coding: utf-8 -*-
"""候选条目（proposals）的采纳 / 丢弃。

为什么要有这一层：
  对话里说的事情，模型能提取，但它提取的是「它的阅读理解」，不是事实。
  直接写进 events/decisions/people，会让十几处查询（看板、预警、检索、上下文）
  全部读到模型猜的东西——整套系统的证据链就废了。
  所以候选先落 proposals，用户点采纳时才写正式表。

采纳时不做「无脑搬运」：
  - 决策必须带否决理由与二阶效应，AI 给不全就拒绝入库（这是系统的铁律）
  - 人名重复时不新建，改为挂到已有人物上
  - 事件必须有日期；没有就用手工确认的当天，不猜
"""
import json

from . import db


class Reject(Exception):
    """候选内容不满足入库条件。"""
    pass


def _j(v, default=None):
    if v is None:
        return default if default is not None else []
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default if default is not None else []


def accept(conn, pid):
    """把一个候选写入正式表。返回 (kind, new_id)。"""
    row = db.one(conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)))
    if not row:
        raise Reject("候选不存在")
    if row["status"] != "pending":
        raise Reject(f"这条已经处理过了（{row['status']}）")

    kind = row["kind"]
    p = _j(row["payload"], {})
    now = db.now()

    if kind == "event":
        date_ = (p.get("on_date") or "").strip()
        title = (p.get("title") or "").strip()
        if len(title) < 4:
            raise Reject("事件标题太短，至少要写清发生了什么")
        if not date_:
            date_ = now[:10]          # 不猜：用确认当天的日期
        cur = conn.execute(
            "INSERT INTO events(on_date,domain,title,my_words,my_actions,other_response,"
            "emotion,outcome,initiative,hours,money,replay,tags,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (date_, (p.get("domain") or "其他")[:8], title[:80],
             (p.get("my_words") or "")[:600], (p.get("my_actions") or "")[:600],
             (p.get("other_response") or "")[:600],
             int(p.get("emotion") or 3), (p.get("outcome") or "neutral")[:10],
             int(p.get("initiative") or 2), float(p.get("hours") or 0),
             float(p.get("money") or 0), (p.get("replay") or "")[:600],
             (p.get("tags") or "")[:120], now))
        new_id = cur.lastrowid

    elif kind == "decision":
        rejected = _j(p.get("rejected"), [])
        second = _j(p.get("second_order"), [])
        chosen = (p.get("chosen") or "").strip()
        title = (p.get("title") or "").strip()
        if not title or not chosen:
            raise Reject("决策缺少标题或最终选择，没法入库")
        # 铁律：必须写清被否掉的方案与三层二阶效应
        if not rejected or not second:
            raise Reject("这条决策缺少「否决理由」或「二阶效应」，AI 没能从对话里读全。"
                         "请去「决策」页手工补全——这两项是这套系统的硬要求，不能省。")
        cur = conn.execute(
            "INSERT INTO decisions(on_date,title,kind,context,criteria,options,second_order,"
            "stop_loss,rejected,chosen,reversibility,status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ((p.get("on_date") or now[:10]), title[:80], (p.get("kind") or "其他")[:8],
             (p.get("context") or "")[:600], json.dumps(_j(p.get("criteria"), []), ensure_ascii=False),
             json.dumps(_j(p.get("options"), []), ensure_ascii=False),
             json.dumps(second, ensure_ascii=False), (p.get("stop_loss") or "")[:300],
             json.dumps(rejected, ensure_ascii=False), chosen[:60],
             (p.get("reversibility") or "unknown")[:10], "decided", now))
        new_id = cur.lastrowid

    elif kind == "person":
        name = (p.get("name") or "").strip()
        if not name:
            raise Reject("缺少人名")
        exist = db.one(conn.execute("SELECT id FROM people WHERE name=?", (name,)))
        if exist:
            # 已有同名人物：不重复建，只追加一条往来记录
            note = (p.get("note") or "").strip()
            if note:
                conn.execute(
                    "INSERT INTO interactions(person_id,on_date,direction,weight,what,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (exist["id"], (p.get("on_date") or now[:10]),
                     (p.get("direction") or "give")[:6], float(p.get("weight") or 1),
                     note[:300], now))
            new_id = exist["id"]
        else:
            cur = conn.execute(
                "INSERT INTO people(name,relation,status,last_contact_on,notes,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (name[:20], (p.get("relation") or "")[:20], "normal",
                 (p.get("on_date") or now[:10]), (p.get("note") or "")[:600], now))
            new_id = cur.lastrowid

    elif kind == "topic":
        name = (p.get("name") or "").strip()
        if not name:
            raise Reject("缺少方向名")
        exist = db.one(conn.execute("SELECT id FROM topics WHERE name=?", (name,)))
        if exist:
            new_id = exist["id"]
        else:
            cur = conn.execute(
                "INSERT INTO topics(name,domain,started_on,why,status,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (name[:30], (p.get("domain") or "")[:8], now[:10],
                 (p.get("why") or "")[:600], "active", now))
            new_id = cur.lastrowid

    else:
        raise Reject(f"不认识的候选类型：{kind}")

    conn.execute("UPDATE proposals SET status='accepted' WHERE id=?", (pid,))
    conn.commit()
    return kind, new_id


def reject(conn, pid, reason=""):
    row = db.one(conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)))
    if not row:
        raise Reject("候选不存在")
    conn.execute("UPDATE proposals SET status='rejected', note=? WHERE id=?",
                 (((row["note"] or "") + " ｜ 人工丢弃：" + (reason or ""))[:400], pid))
    conn.commit()
    return True


def pending(conn, kind=None):
    sql = "SELECT * FROM proposals WHERE status='pending'"
    args = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY kind, id"
    rows = db.rows(conn.execute(sql, args))
    for r in rows:
        r["payload"] = _j(r["payload"], {})
    return rows


def counts(conn):
    rows = db.rows(conn.execute(
        "SELECT kind, COUNT(*) AS n FROM proposals WHERE status='pending' GROUP BY kind"))
    return {r["kind"]: r["n"] for r in rows}
