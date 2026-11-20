# -*- coding: utf-8 -*-
"""HTTP 服务：零第三方依赖（标准库 http.server + sqlite3）。

安全模型（因为要绑 0.0.0.0 给手机用）：
- 单 PIN 登录，HMAC 签名 cookie，HttpOnly + SameSite=Strict。
- 除 /api/login 与静态文件外，所有接口必须已登录。
- 数据只在本地 SQLite，不发送到任何第三方（除了你主动点「AI 分析」时的 DeepSeek 请求）。
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import bootstrap  # noqa: E402

# 必须在导入 ai 之前生成 .env —— ai 在 import 时就会读环境变量，
# 放到 main() 里再生成就来不及了（第一次启动会读到空的 API Key）。
bootstrap.ensure_env()

from app import ai, chat, db, engine, knowledge, skills, websearch  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.environ.get("MIRROR_PORT", "8765"))
COOKIE = "mirror_session"
SESSION_TTL = 60 * 60 * 24 * 30

MIN_REASON = 8  # 理由最少字数：低于这个数的「理由」都是敷衍

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".webmanifest": "application/manifest+json; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


# ---------------------------------------------------------------- 认证
def _secret():
    return os.environ.get("MIRROR_SECRET", "") or "mirror-local-fallback-secret"


def _pin():
    return os.environ.get("MIRROR_PIN", "").strip()


def make_token():
    exp = str(int(time.time()) + SESSION_TTL)
    sig = hmac.new(_secret().encode(), exp.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{exp}.{sig}"


def check_token(tok):
    if not tok or "." not in tok:
        return False
    exp, sig = tok.split(".", 1)
    good = hmac.new(_secret().encode(), exp.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, good):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


class Err(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.msg, self.code = msg, code


# ---------------------------------------------------------------- 校验
def _need_reason(val, field="理由", n=MIN_REASON):
    v = (val or "").strip()
    if len(v) < n:
        raise Err(f"{field}至少要写 {n} 个字（现在 {len(v)} 个）。敷衍的理由等于没有理由。")
    return v


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _s(v, default=""):
    return (v if isinstance(v, str) else ("" if v is None else str(v))).strip() or default


# ---------------------------------------------------------------- 业务
def save_decision(conn, body):
    did = _int(body.get("id"), 0)
    criteria = body.get("criteria") or []
    options = body.get("options") or []
    rejected = body.get("rejected") or []
    second = body.get("second_order") or []
    chosen = _s(body.get("chosen"))
    status = _s(body.get("status"), "open")

    if not _s(body.get("title")):
        raise Err("决策标题不能为空")

    names = [_s(o.get("name")) for o in options if isinstance(o, dict) and _s(o.get("name"))]
    if len(names) < 2:
        raise Err("至少要有 2 个备选方案。只有一个选项的决定不叫决策，叫执行。")
    if len(set(names)) != len(names):
        raise Err("备选方案重名，无法区分。")
    if chosen and chosen not in names:
        raise Err("选中的方案不在备选列表里。")

    # 铁律①：每个没被选中的分支，必须留下否决理由。
    rej_map = {}
    for r in rejected:
        if isinstance(r, dict) and _s(r.get("option")):
            rej_map[_s(r.get("option"))] = (r.get("reason") or "")
    if status == "decided":
        if not chosen:
            raise Err("状态为已决定时，必须指定选中的方案。")
        missing = [n for n in names if n != chosen and
                   len((rej_map.get(n) or "").strip()) < MIN_REASON]
        if missing:
            raise Err("以下分支没有写清否决理由（每个至少 %d 字）：%s。"
                      "被否决的选项必须留在案上，否则你以后无法复盘为什么断了它。"
                      % (MIN_REASON, "、".join(missing)))
        # 铁律②：选中方案的二阶效应必须连推到第三层。
        chain = []
        for s in second:
            if isinstance(s, dict) and _s(s.get("option")) == chosen:
                chain = s.get("chain") or []
        if len([c for c in chain if len(_s(c)) >= 5]) < 3:
            raise Err("选中方案的二阶效应必须连推三层（每层至少 5 字）。你只想到第一层，就会在第二层被打。")

    crit_json = json.dumps([{"name": _s(c.get("name")), "weight": _float(c.get("weight"), 1)}
                            for c in criteria if isinstance(c, dict) and _s(c.get("name"))],
                           ensure_ascii=False)
    opt_json = json.dumps([{"name": _s(o.get("name")),
                            "scores": {k: _float(v) for k, v in (o.get("scores") or {}).items()},
                            "note": _s(o.get("note"))} for o in options if _s(o.get("name"))],
                          ensure_ascii=False)
    rej_out = [{"option": k, "reason": (v or "").strip()} for k, v in rej_map.items()]
    fields = (_s(body.get("on_date"), db.today()), _s(body.get("title")), _s(body.get("kind"), "其他"),
              _s(body.get("context")), crit_json, opt_json,
              json.dumps(second, ensure_ascii=False), _s(body.get("stop_loss")),
              json.dumps(rej_out, ensure_ascii=False), chosen, _s(body.get("reversibility"), "unknown"),
              _s(body.get("review_due")), status)
    if did:
        conn.execute("UPDATE decisions SET on_date=?,title=?,kind=?,context=?,criteria=?,options=?,"
                     "second_order=?,stop_loss=?,rejected=?,chosen=?,reversibility=?,review_due=?,"
                     "status=? WHERE id=?", fields + (did,))
    else:
        cur = conn.execute("INSERT INTO decisions(on_date,title,kind,context,criteria,options,"
                           "second_order,stop_loss,rejected,chosen,reversibility,review_due,status,"
                           "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           fields + (db.now(),))
        did = cur.lastrowid
    conn.commit()
    return {"id": did}


def save_spend(conn, body):
    amount = _float(body.get("amount"))
    item = _s(body.get("item"))
    if not item:
        raise Err("要买什么？写清楚。")
    if amount <= 0:
        raise Err("金额必须大于 0")
    R = db.load_rules(conn)
    thr = R["spend_threshold"]["value"] if R["spend_threshold"]["enabled"] else 1e18
    days = int(R["spend_cool_days"]["value"] or 0)
    on = _s(body.get("on_date"), db.today())
    cool = None
    status = "approved"
    if amount > thr and days > 0:
        base = date.fromisoformat(on[:10])
        cool = (base + timedelta(days=days)).isoformat()
        status = "pending"
    cur = conn.execute("INSERT INTO spend(on_date,item,amount,category,need,cool_until,status,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?)",
                       (on, item, amount, _s(body.get("category")), _int(body.get("need"), 3),
                        cool, status, db.now()))
    conn.commit()
    return {"id": cur.lastrowid, "status": status, "cool_until": cool,
            "note": f"超过 {thr:g} 元，强制冷静 {days} 天到 {cool}。" if cool else "低于门槛，直接记录。"}


def save_topic(conn, body):
    tid = _int(body.get("id"), 0)
    name = _s(body.get("name"))
    if not name:
        raise Err("主题名不能为空")
    status = _s(body.get("status"), "active")
    archive_reason = body.get("archive_reason") or ""
    # 铁律③：封存/放弃必须写理由。
    if status in ("archived", "parked") and tid:
        old = db.one(conn.execute("SELECT status,archive_reason FROM topics WHERE id=?", (tid,)))
        if old and old["status"] != status:
            archive_reason = _need_reason(archive_reason, "封存理由")
    depth = max(0, min(5, _int(body.get("depth_level"), 0)))
    maint = 1 if body.get("mainline") else 0

    if maint:
        R = db.load_rules(conn)
        cap = int(R["mainline_cap"]["value"])
        n = conn.execute("SELECT COUNT(*) FROM topics WHERE mainline=1 AND status='active' AND id!=?",
                         (tid,)).fetchone()[0]
        if n + 1 > cap:
            raise Err(f"主线已达上限 {cap} 条。要立新的主线，先封存一条旧的并写清理由。")

    vals = (name, _s(body.get("domain")), _s(body.get("started_on"), db.today()), depth,
            _float(body.get("hours")), _int(body.get("artifacts")), max(1, min(5, _int(body.get("interest"), 3))),
            _s(body.get("last_touch_on"), db.today()), maint, status, _s(body.get("why")),
            archive_reason)
    if tid:
        old = db.one(conn.execute("SELECT depth_level,last_depth_up_on FROM topics WHERE id=?", (tid,)))
        last_up = old["last_depth_up_on"] if old else None
        if old and depth > (old["depth_level"] or 0):
            last_up = db.today()
        conn.execute("UPDATE topics SET name=?,domain=?,started_on=?,depth_level=?,hours=?,artifacts=?,"
                     "interest=?,last_touch_on=?,mainline=?,status=?,why=?,archive_reason=?,"
                     "last_depth_up_on=? WHERE id=?", vals + (last_up, tid))
    else:
        cur = conn.execute("INSERT INTO topics(name,domain,started_on,depth_level,hours,artifacts,interest,"
                           "last_touch_on,mainline,status,why,archive_reason,last_depth_up_on,created_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + (db.today(), db.now()))
        tid = cur.lastrowid
    conn.commit()
    return {"id": tid}


def save_trait(conn, body):
    ev = body.get("evidence") or []
    if not isinstance(ev, list):
        ev = []
    valid = {r["id"] for r in conn.execute("SELECT id FROM events")}
    ev = [int(x) for x in ev if _int(x, -1) in valid]
    if not ev:
        raise Err("这条画像没有挂任何真实事件编号，属于无证据断言，不允许入库。")
    cur = conn.execute("INSERT INTO traits(name,direction,statement,evidence,blind_spot,cost,counter,"
                       "confidence,status,source,dup_of,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (_s(body.get("name"), "未命名模式"), _s(body.get("direction")), _s(body.get("statement")),
                        json.dumps(ev), _s(body.get("blind_spot")), _s(body.get("cost")), _s(body.get("counter")),
                        _float(body.get("confidence"), 0.5), "draft", _s(body.get("source"), "manual"),
                        max(0, _int(body.get("dup_of"), 0)), db.now()))
    conn.commit()
    return {"id": cur.lastrowid}


ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


# ---------------------------------------------------------------- 读取接口
@route("GET", r"/api/dashboard")
def _dashboard(h, ctx):
    return engine.dashboard(ctx["conn"])


@route("GET", r"/api/events")
def _events(h, ctx):
    q = ctx["query"]
    where, args = [], []
    if q.get("domain"):
        where.append("domain=?")
        args.append(q["domain"][0])
    if q.get("limit"):
        lim = _int(q["limit"][0], 100)
    else:
        lim = 300
    sql = "SELECT * FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY on_date DESC, id DESC LIMIT ?"
    return {"events": db.rows(ctx["conn"].execute(sql, args + [lim]))}


@route("POST", r"/api/events")
def _event_add(h, ctx):
    b = ctx["body"]
    if not _s(b.get("title")):
        raise Err("事件标题不能为空。一句话说清发生了什么。")
    cur = ctx["conn"].execute(
        "INSERT INTO events(on_date,domain,title,my_words,my_actions,other_response,emotion,outcome,"
        "initiative,hours,money,replay,tags,topic_id,person_id,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (_s(b.get("on_date"), db.today()), _s(b.get("domain"), "其他"), _s(b.get("title")),
         _s(b.get("my_words")), _s(b.get("my_actions")), _s(b.get("other_response")),
         max(1, min(5, _int(b.get("emotion"), 3))), _s(b.get("outcome"), "neutral"),
         max(0, min(4, _int(b.get("initiative"), 2))), _float(b.get("hours")), _float(b.get("money")),
         _s(b.get("replay")), _s(b.get("tags")), _int(b.get("topic_id"), 0) or None,
         _int(b.get("person_id"), 0) or None, db.now()))
    ctx["conn"].commit()
    return {"id": cur.lastrowid}


@route("DELETE", r"/api/events/(\d+)")
def _event_del(h, ctx, eid):
    ctx["conn"].execute("DELETE FROM events WHERE id=?", (_int(eid),))
    ctx["conn"].commit()
    return {"deleted": _int(eid)}


@route("GET", r"/api/topics")
def _topics(h, ctx):
    return engine.topic_metrics(ctx["conn"])


@route("POST", r"/api/topics")
def _topic_post(h, ctx):
    return save_topic(ctx["conn"], ctx["body"])


@route("POST", r"/api/topics/(\d+)/session")
def _topic_session(h, ctx, tid):
    b = ctx["body"]
    hrs = _float(b.get("hours"))
    if hrs <= 0:
        raise Err("这次投入多少小时？必须大于 0。")
    c = ctx["conn"]
    c.execute("INSERT INTO topic_sessions(topic_id,on_date,hours,note,created_at) VALUES(?,?,?,?,?)",
              (_int(tid), _s(b.get("on_date"), db.today()), hrs, _s(b.get("note")), db.now()))
    c.execute("UPDATE topics SET hours=COALESCE(hours,0)+?, last_touch_on=? WHERE id=?",
              (hrs, _s(b.get("on_date"), db.today()), _int(tid)))
    if b.get("depth_delta"):
        c.execute("UPDATE topics SET depth_level=MIN(5,depth_level+?), last_depth_up_on=? WHERE id=?",
                  (_int(b.get("depth_delta")), db.today(), _int(tid)))
    c.commit()
    return {"ok": True}


@route("GET", r"/api/decisions")
def _decisions(h, ctx):
    rows = db.rows(ctx["conn"].execute("SELECT * FROM decisions ORDER BY on_date DESC, id DESC LIMIT 200"))
    for r in rows:
        for k in ("criteria", "options", "second_order", "rejected"):
            r[k] = db.jloads(r[k], [])
        r["mcda"] = engine.mcda(r["criteria"], r["options"]) if r["criteria"] and r["options"] else None
    return {"decisions": rows}


@route("POST", r"/api/decisions")
def _decision_post(h, ctx):
    return save_decision(ctx["conn"], ctx["body"])


@route("POST", r"/api/decisions/(\d+)/review")
def _decision_review(h, ctx, did):
    b = ctx["body"]
    score = _int(b.get("score"), 0)
    lesson = _need_reason(b.get("lesson"), "复盘结论")
    actual = _s(b.get("actual"))
    ctx["conn"].execute("UPDATE decisions SET review_actual=?,review_score=?,review_lesson=?,status='closed' "
                        "WHERE id=?", (actual, max(-2, min(2, score)), lesson, _int(did)))
    ctx["conn"].commit()
    return {"ok": True}


@route("POST", r"/api/traits")
def _trait_post(h, ctx):
    return save_trait(ctx["conn"], ctx["body"])


@route("GET", r"/api/traits")
def _traits(h, ctx):
    rows = db.rows(ctx["conn"].execute("SELECT * FROM traits ORDER BY status='draft' DESC, id DESC"))
    by = {r["id"]: r for r in ctx["conn"].execute("SELECT id,on_date,title FROM events")}
    names = {r["id"]: r["name"] for r in ctx["conn"].execute("SELECT id,name FROM traits")}
    for r in rows:
        ids = db.jloads(r["evidence"], [])
        r["evidence"] = [{"id": i, "on_date": by[i]["on_date"], "title": by[i]["title"]}
                         for i in ids if i in by]
        dup = r.get("dup_of") or 0
        r["dup_name"] = names.get(dup, "") if dup else ""
    return {"traits": rows}


@route("POST", r"/api/traits/(\d+)/decide")
def _trait_decide(h, ctx, tid):
    b = ctx["body"]
    st = _s(b.get("status"))
    if st not in ("accepted", "rejected"):
        raise Err("状态只能是 accepted 或 rejected")
    reason = ""
    if st == "rejected":
        reason = _need_reason(b.get("reason"), "驳回理由")
    ctx["conn"].execute("UPDATE traits SET status=?,reject_reason=? WHERE id=?", (st, reason, _int(tid)))
    ctx["conn"].commit()
    return {"ok": True}


@route("GET", r"/api/people")
def _people(h, ctx):
    c = ctx["conn"]
    out = []
    for p in db.rows(c.execute("SELECT * FROM people ORDER BY name")):
        g = c.execute("SELECT COALESCE(SUM(weight),0) FROM interactions WHERE person_id=? AND direction='give'",
                      (p["id"],)).fetchone()[0]
        t = c.execute("SELECT COALESCE(SUM(weight),0) FROM interactions WHERE person_id=? AND direction='take'",
                      (p["id"],)).fetchone()[0]
        p.update({"give": round(g, 2), "take": round(t, 2),
                  "net": round(g - t, 2),
                  "ratio": round((g - t) / g, 3) if g else None,
                  "idle_days": engine._days_since(p["last_contact_on"]),
                  "interactions": db.rows(c.execute(
                      "SELECT * FROM interactions WHERE person_id=? ORDER BY on_date DESC LIMIT 30", (p["id"],)))})
        out.append(p)
    return {"people": out}


@route("POST", r"/api/people")
def _person_post(h, ctx):
    b = ctx["body"]
    name = _s(b.get("name"))
    if not name:
        raise Err("名字不能为空")
    pid = _int(b.get("id"), 0)
    if pid:
        old = db.one(ctx["conn"].execute("SELECT status FROM people WHERE id=?", (pid,)))
        status = _s(b.get("status"), "normal")
        reason = b.get("status_reason") or ""
        if old and old["status"] != status and status in ("downgraded", "cut"):
            reason = _need_reason(reason, "降级/切断理由")
        ctx["conn"].execute("UPDATE people SET name=?,relation=?,status=?,status_reason=?,notes=? WHERE id=?",
                            (name, _s(b.get("relation")), status, reason, _s(b.get("notes")), pid))
    else:
        cur = ctx["conn"].execute("INSERT INTO people(name,relation,status,last_contact_on,notes,created_at) "
                                  "VALUES(?,?,?,?,?,?)",
                                  (name, _s(b.get("relation")), "normal", _s(b.get("last_contact_on"), db.today()),
                                   _s(b.get("notes")), db.now()))
        pid = cur.lastrowid
    ctx["conn"].commit()
    return {"id": pid}


@route("POST", r"/api/people/(\d+)/interaction")
def _interaction(h, ctx, pid):
    b = ctx["body"]
    d = _s(b.get("direction"), "give")
    if d not in ("give", "take", "neutral"):
        raise Err("方向只能是 give / take / neutral")
    on = _s(b.get("on_date"), db.today())
    w = _float(b.get("weight"), 1)
    if w <= 0:
        raise Err("权重要大于 0")
    ctx["conn"].execute("INSERT INTO interactions(person_id,on_date,direction,weight,what,created_at) "
                        "VALUES(?,?,?,?,?,?)", (_int(pid), on, d, w, _s(b.get("what")), db.now()))
    ctx["conn"].execute("UPDATE people SET last_contact_on=MAX(COALESCE(last_contact_on,''),?) WHERE id=?",
                        (on, _int(pid)))
    ctx["conn"].commit()
    return {"ok": True}


@route("GET", r"/api/spend")
def _spend(h, ctx):
    rows = db.rows(ctx["conn"].execute("SELECT * FROM spend ORDER BY on_date DESC, id DESC LIMIT 300"))
    for r in rows:
        r["cool_due"] = bool(r["status"] == "pending" and
                             (engine._d(r["cool_until"]) or date.today()) <= date.today())
    return {"spend": rows,
            "month_total": round(ctx["conn"].execute(
                "SELECT COALESCE(SUM(amount),0) FROM spend WHERE status!='cancelled' AND on_date>=?",
                (date.today().replace(day=1).isoformat(),)).fetchone()[0], 2)}


@route("POST", r"/api/spend")
def _spend_post(h, ctx):
    return save_spend(ctx["conn"], ctx["body"])


@route("POST", r"/api/spend/(\d+)/verdict")
def _spend_verdict(h, ctx, sid):
    b = ctx["body"]
    st = _s(b.get("status"))
    if st not in ("approved", "cancelled"):
        raise Err("裁决只能是 approved 或 cancelled")
    reason = "" if st == "approved" else _need_reason(b.get("reason"), "放弃理由")
    row = db.one(ctx["conn"].execute("SELECT cool_until FROM spend WHERE id=?", (_int(sid),)))
    if not row:
        raise Err("记录不存在", 404)
    if st == "approved" and row["cool_until"] and (engine._d(row["cool_until"]) or date.today()) > date.today():
        raise Err(f"冷静期到 {row['cool_until']} 才结束。现在批准等于绕过自己的规则。")
    ctx["conn"].execute("UPDATE spend SET status=?,verdict_reason=? WHERE id=?", (st, reason, _int(sid)))
    ctx["conn"].commit()
    return {"ok": True}


@route("GET", r"/api/alerts")
def _alerts(h, ctx):
    return {"alerts": engine.scan_alerts(ctx["conn"])}


@route("POST", r"/api/alerts/ack")
def _alert_ack(h, ctx):
    b = ctx["body"]
    fp = _s(b.get("fingerprint"))
    if not fp:
        raise Err("缺少 fingerprint")
    st = _s(b.get("status"), "handled")
    if st not in ("handled", "ignored"):
        raise Err("状态只能是 handled 或 ignored")
    note = _need_reason(b.get("note"), "处置说明")
    ctx["conn"].execute("INSERT INTO alert_acks(fingerprint,status,note,acked_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(fingerprint) DO UPDATE SET status=excluded.status,note=excluded.note,"
                        "acked_at=excluded.acked_at", (fp, st, note, db.now()))
    ctx["conn"].commit()
    return {"ok": True}


@route("GET", r"/api/rules")
def _rules_get(h, ctx):
    return {"rules": [{"key": r["key"], "name": r["name"], "descr": r["descr"], "value": r["value"],
                       "unit": r["unit"], "enabled": bool(r["enabled"])}
                      for r in ctx["conn"].execute("SELECT * FROM rules")]}


@route("POST", r"/api/rules")
def _rules_post(h, ctx):
    for r in (ctx["body"].get("rules") or []):
        if not isinstance(r, dict):
            continue
        ctx["conn"].execute("UPDATE rules SET value=?,enabled=? WHERE key=?",
                            (_float(r.get("value")), 1 if r.get("enabled") else 0, _s(r.get("key"))))
    ctx["conn"].commit()
    return {"ok": True}


@route("GET", r"/api/memory")
def _memory_get(h, ctx):
    rows = db.rows(ctx["conn"].execute(
        "SELECT * FROM memory ORDER BY status='draft' DESC, status='active' DESC, id DESC"))
    for r in rows:
        r["evidence"] = db.jloads(r.get("evidence"), [])
    return {"memory": rows}


@route("POST", r"/api/memory")
def _memory_post(h, ctx):
    b = ctx["body"]
    stmt = _s(b.get("statement"))
    if len(stmt) < 6:
        raise Err("长期记忆至少要写 6 个字，否则它没法约束以后的判断。")
    kind = _s(b.get("kind"), "自我认知")
    mid = _int(b.get("id"), 0)
    if mid:
        ctx["conn"].execute("UPDATE memory SET statement=?,kind=? WHERE id=?", (stmt, kind, mid))
    else:
        cur = ctx["conn"].execute(
            "INSERT INTO memory(statement,kind,source,created_at) VALUES(?,?,?,?)",
            (stmt, kind, _s(b.get("source"), "manual"), db.now()))
        mid = cur.lastrowid
    ctx["conn"].commit()
    return {"id": mid}


@route("POST", r"/api/memory/(\d+)/retire")
def _memory_retire(h, ctx, mid):
    reason = _need_reason(ctx["body"].get("reason"), "作废理由")
    ctx["conn"].execute("UPDATE memory SET status='retired',retired_reason=? WHERE id=?",
                        (reason, _int(mid)))
    ctx["conn"].commit()
    return {"ok": True}


@route("GET", r"/api/export")
def _export(h, ctx):
    c = ctx["conn"]
    dump = {}
    for t in ("events", "traits", "topics", "topic_sessions", "decisions", "people",
              "interactions", "spend", "rules", "alert_acks", "reports", "memory"):
        dump[t] = db.rows(c.execute(f"SELECT * FROM {t}"))
    dump["_exported_at"] = db.now()
    return dump


# ---------------------------------------------------------------- AI 接口
@route("POST", r"/api/ai/ping")
def _ai_ping(h, ctx):
    if not ai.available():
        raise Err("未配置 DEEPSEEK_API_KEY", 400)
    return ai.ping()


@route("POST", r"/api/analyze/personality")
def _an_personality(h, ctx):
    return ai.analyze_personality(ctx["conn"], _int(ctx["body"].get("limit"), 60))


@route("POST", r"/api/analyze/direction")
def _an_direction(h, ctx):
    return ai.analyze_direction(ctx["conn"])


@route("POST", r"/api/analyze/consolidate")
def _an_consolidate(h, ctx):
    days = _int(ctx["body"].get("older_than_days"), 30)
    try:
        return ai.consolidate(ctx["conn"], max(1, min(days, 3650)))
    except RuntimeError as e:
        # 「没有可巩固的事件」是正常的业务状态，不是内部错误
        raise Err(str(e), 400)


@route("POST", r"/api/memory/(\d+)/approve")
def _memory_approve(h, ctx, mid):
    row = db.one(ctx["conn"].execute("SELECT id,status FROM memory WHERE id=?", (_int(mid),)))
    if not row:
        raise Err("记忆不存在", 404)
    ctx["conn"].execute("UPDATE memory SET status='active' WHERE id=?", (_int(mid),))
    ctx["conn"].commit()
    return {"ok": True}


# ---------------------------------------------------------------- 联网检索
@route("GET", r"/api/web/check")
def _web_check(h, ctx):
    return {"sources": websearch.probe()}


@route("POST", r"/api/conversations/(\d+)/web")
def _conv_web(h, ctx, cid):
    """开关联网；同时可设置语言：zh / en / both。"""
    b = ctx["body"]
    on = 1 if b.get("on") else 0
    lang = _s(b.get("lang"), "")
    if lang and lang not in ("zh", "en", "both"):
        raise Err("语言只能是 zh / en / both")
    ctx["conn"].execute("UPDATE conversations SET web=? WHERE id=?", (on, _int(cid)))
    if lang:
        ctx["conn"].execute("UPDATE conversations SET web_lang=? WHERE id=?", (lang, _int(cid)))
    ctx["conn"].commit()
    row = db.one(ctx["conn"].execute(
        "SELECT web,web_lang FROM conversations WHERE id=?", (_int(cid),)))
    return {"ok": True, "web": bool(row["web"]), "lang": row["web_lang"] or "both"}


@route("POST", r"/api/conversations/(\d+)/comfort")
def _conv_comfort(h, ctx, cid):
    """设置本轮体例：auto（按情绪自动）/ on（先共情）/ off（直接分析）。"""
    mode = _s(ctx["body"].get("mode"), "auto")
    if mode not in ("auto", "on", "off"):
        raise Err("只能是 auto / on / off")
    ctx["conn"].execute("UPDATE conversations SET comfort=? WHERE id=?", (mode, _int(cid)))
    ctx["conn"].commit()
    return {"ok": True, "mode": mode}


@route("POST", r"/api/web/search")
def _web_search(h, ctx):
    q = _s(ctx["body"].get("q"))
    if len(q) < 2:
        raise Err("检索词太短")
    lang = _s(ctx["body"].get("lang"), "en")
    if lang not in ("zh", "en", "both"):
        raise Err("语言只能是 zh / en / both")
    try:
        if lang == "zh":
            rows, note = websearch.search(ctx["conn"], q, limit=_int(ctx["body"].get("limit"), 5),
                                          fetchers=(websearch.openalex_zh,), lang="zh")
        elif lang == "en":
            rows, note = websearch.search(ctx["conn"], q, limit=_int(ctx["body"].get("limit"), 5))
        else:
            rows, note, _weak = websearch.search_multi(
                ctx["conn"], q, q, lang="both", limit=_int(ctx["body"].get("limit"), 5))
    except Exception as e:
        raise Err(f"检索失败：{type(e).__name__}: {str(e)[:120]}", 500)
    return {"query": q, "lang": lang, "source": note, "references": rows}


@route("POST", r"/api/theory/(\d+)/verify")
def _theory_verify(h, ctx, tid):
    """用真实文献核验一条理论：中英文都查，中文排前。"""
    t = knowledge.get(ctx["conn"], _int(tid))
    if not t:
        raise Err("条目不存在", 404)
    zh_q = (t.get("name") or "").strip()
    en_q = (t.get("en") or "").strip()
    if len(zh_q) < 2 and len(en_q) < 4:
        raise Err("这条名称太短，无法有效检索。")
    try:
        rows, note, weak = websearch.search_multi(
            ctx["conn"], zh_q, en_q, lang="both", limit=4, theory_id=_int(tid))
    except Exception as e:
        raise Err(f"检索失败：{type(e).__name__}: {str(e)[:120]}", 500)
    return {"theory": t["name"], "query": f"中「{zh_q}」+ 英「{en_q}」",
            "source": note, "weak": bool(weak), "references": rows}


# ---------------------------------------------------------------- 对话
@route("GET", r"/api/skills")
def _skills(h, ctx):
    items = skills.load_all()
    return {"skills": [{"name": s["name"], "description": s["description"],
                        "chars": len(s["body"])} for s in items],
            "dir": skills.SKILLS_DIR}


# ---------------------------------------------------------------- 知识库
@route("GET", r"/api/theory")
def _theory_list(h, ctx):
    q = (ctx["query"].get("q") or [""])[0].strip()
    if q:
        conn = ctx["conn"]
        ids = [t["id"] for t in knowledge.match(conn, q, limit=30)]
        if not ids:
            return {"theory": [], "q": q}
        ph = ",".join("?" * len(ids))
        rows = db.rows(conn.execute(f"SELECT * FROM theory WHERE id IN ({ph}) ORDER BY field,id", ids))
        return {"theory": rows, "q": q}
    rows = db.rows(ctx["conn"].execute("SELECT * FROM theory ORDER BY field,id"))
    return {"theory": rows, "fields": sorted({r["field"] for r in rows})}


@route("POST", r"/api/theory")
def _theory_save(h, ctx):
    b = ctx["body"]
    tid = _int(b.get("id"), 0)
    vals = (_s(b.get("name")), _s(b.get("en")), _s(b.get("field"), "心理学"), _s(b.get("source")),
            _s(b.get("summary")), _s(b.get("applies")), _s(b.get("misuse")),
            _s(b.get("signal")), _s(b.get("tags")))
    if len(vals[0]) < 2:
        raise Err("名称太短")
    if len(vals[4]) < 10:
        raise Err("「核心」至少写 10 个字，否则没法被引用")
    if not vals[6]:
        raise Err("必须写「误用风险」——这类概念最大的危害是被拿去给人贴标签")
    if tid:
        ctx["conn"].execute(
            "UPDATE theory SET name=?,en=?,field=?,source=?,summary=?,applies=?,misuse=?,signal=?,tags=? "
            "WHERE id=?", vals + (tid,))
    else:
        cur = ctx["conn"].execute(
            "INSERT INTO theory(name,en,field,source,summary,applies,misuse,signal,tags,enabled,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,1,?)", vals + (db.now(),))
        tid = cur.lastrowid
    ctx["conn"].commit()
    return {"id": tid}


@route("POST", r"/api/theory/(\d+)/toggle")
def _theory_toggle(h, ctx, tid):
    row = db.one(ctx["conn"].execute("SELECT enabled FROM theory WHERE id=?", (_int(tid),)))
    if not row:
        raise Err("条目不存在", 404)
    ctx["conn"].execute("UPDATE theory SET enabled=? WHERE id=?",
                        (0 if row["enabled"] else 1, _int(tid)))
    ctx["conn"].commit()
    return {"ok": True, "enabled": not row["enabled"]}


@route("GET", r"/api/conversations")
def _convs(h, ctx):
    rows = db.rows(ctx["conn"].execute(
        "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS msg_count,"
        " (SELECT COUNT(*) FROM summaries s WHERE s.conversation_id=c.id) AS sum_count "
        "FROM conversations c WHERE c.archived=0 ORDER BY c.updated_at DESC LIMIT 60"))
    return {"conversations": rows}


@route("POST", r"/api/conversations")
def _conv_new(h, ctx):
    cur = ctx["conn"].execute(
        "INSERT INTO conversations(title,skill,created_at,updated_at) VALUES(?,?,?,?)",
        (_s(ctx["body"].get("title"), "新对话"), _s(ctx["body"].get("skill")), db.now(), db.now()))
    ctx["conn"].commit()
    return {"id": cur.lastrowid}


@route("POST", r"/api/conversations/(\d+)/archive")
def _conv_archive(h, ctx, cid):
    ctx["conn"].execute("UPDATE conversations SET archived=1 WHERE id=?", (_int(cid),))
    ctx["conn"].commit()
    return {"ok": True}


@route("POST", r"/api/conversations/(\d+)/skill")
def _conv_skill(h, ctx, cid):
    sk = _s(ctx["body"].get("skill"))
    if sk and not skills.get(sk):
        raise Err(f"没有这个 skill：{sk}")
    ctx["conn"].execute("UPDATE conversations SET skill=? WHERE id=?", (sk, _int(cid)))
    ctx["conn"].commit()
    return {"ok": True, "skill": sk}


@route("GET", r"/api/conversations/(\d+)")
def _conv_get(h, ctx, cid):
    c = db.one(ctx["conn"].execute("SELECT * FROM conversations WHERE id=?", (_int(cid),)))
    if not c:
        raise Err("会话不存在", 404)
    msgs = db.rows(ctx["conn"].execute(
        "SELECT id,role,content,refs,bad_refs,skill,ok,err,created_at FROM messages "
        "WHERE conversation_id=? ORDER BY id", (_int(cid),)))
    for m in msgs:
        m["refs"] = db.jloads(m["refs"], [])
        m["bad_refs"] = db.jloads(m["bad_refs"], [])
    sums = db.rows(ctx["conn"].execute(
        "SELECT id,upto_message_id,content,created_at FROM summaries WHERE conversation_id=? "
        "ORDER BY id", (_int(cid),)))
    return {"conversation": c, "messages": msgs, "summaries": sums}


@route("POST", r"/api/chat/(\d+)/send")
def _chat_send(h, ctx, cid):
    body = ctx["body"]
    skill = body.get("skill")
    if skill is not None and skill and not skills.get(skill):
        raise Err(f"没有这个 skill：{skill}")
    try:
        r = chat.send(ctx["conn"], _int(cid), _s(body.get("text")), skill)
    except ValueError as e:
        raise Err(str(e))
    except RuntimeError as e:
        raise Err(str(e), 500)
    # 自动压缩：长对话不让上下文无限膨胀
    auto = None
    try:
        auto = chat.maybe_auto_summarize(ctx["conn"], _int(cid))
    except Exception:
        auto = None
    r["auto_summary"] = bool(auto)
    return r


@route("POST", r"/api/conversations/(\d+)/summarize")
def _conv_summarize(h, ctx, cid):
    try:
        return chat.summarize(ctx["conn"], _int(cid),
                              keep_recent=_int(ctx["body"].get("keep_recent"), 14))
    except RuntimeError as e:
        raise Err(str(e))


@route("GET", r"/api/messages/search")
def _msg_search(h, ctx):
    q = (ctx["query"].get("q") or [""])[0]
    return {"query": q, "messages": chat.search_messages(ctx["conn"], q, limit=20)}


# ---------------------------------------------------------------- 维护
@route("POST", r"/api/backup")
def _backup(h, ctx):
    path = db.backup("manual")
    return {"ok": True, "path": path,
            "check": db.integrity_check(),
            "size": os.path.getsize(path) if os.path.exists(path) else 0}


@route("GET", r"/api/health")
def _health(h, ctx):
    bdir = db.BACKUP_DIR
    backups = []
    if os.path.isdir(bdir):
        for f in sorted(os.listdir(bdir), reverse=True)[:5]:
            p = os.path.join(bdir, f)
            backups.append({"name": f, "size": os.path.getsize(p),
                            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    counts = {}
    for t in ("events", "messages", "conversations", "memory", "traits", "decisions", "topics"):
        counts[t] = ctx["conn"].execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    dbfile = db.DB_PATH
    return {"integrity": db.integrity_check(),
            "db_size": os.path.getsize(dbfile) if os.path.exists(dbfile) else 0,
            "counts": counts, "backups": backups,
            "skills": len(skills.load_all()),
            "ai": ai.available()}


@route("POST", r"/api/decisions/(\d+)/stress")
def _an_stress(h, ctx, did):
    return ai.stress_test(ctx["conn"], _int(did))


@route("POST", r"/api/people/(\d+)/review")
def _an_person(h, ctx, pid):
    return ai.review_person(ctx["conn"], _int(pid))


# ---------------------------------------------------------------- HTTP 层
class Handler(BaseHTTPRequestHandler):
    server_version = "MirrorOS"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---- 工具
    def _cookies(self):
        raw = self.headers.get("Cookie") or ""
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def authed(self):
        if not _pin():
            return True
        return check_token(self._cookies().get(COOKIE))

    def send_json(self, obj, status=200, cookie=None):
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, ctype=None):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return self.send_json({"error": "not found"}, 404)
        ext = os.path.splitext(path)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", ctype or MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def body_json(self):
        n = _int(self.headers.get("Content-Length"), 0)
        if n <= 0:
            return {}
        if n > 8 * 1024 * 1024:
            raise Err("请求体过大", 413)
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise Err("JSON 格式错误")

    # ---- 分发
    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        u = urlparse(self.path)
        path = u.path
        try:
            if method == "GET" and (path == "/" or path.startswith("/static/")):
                return self.serve_static(path)
            if path == "/manifest.webmanifest":
                return self.send_file(os.path.join(STATIC_DIR, "manifest.webmanifest"))
            if path == "/api/login" and method == "POST":
                return self.do_login()
            if not path.startswith("/api/"):
                return self.send_json({"error": "not found"}, 404)
            if not self.authed():
                return self.send_json({"error": "未登录", "need_login": True}, 401)
            if path == "/api/me":
                return self.send_json({"ok": True, "ai": ai.available(), "pin": bool(_pin())})
            if path == "/api/logout":
                return self.send_json({"ok": True},
                                      cookie=f"{COOKIE}=; Path=/; Max-Age=0; SameSite=Strict")
            ctx = {"conn": db.connect(), "body": {}, "query": parse_qs(u.query)}
            try:
                if method in ("POST", "DELETE"):
                    ctx["body"] = self.body_json()
                for m, rx, fn in ROUTES:
                    if m != method:
                        continue
                    mt = rx.match(path)
                    if mt:
                        res = fn(self, ctx, *mt.groups())
                        return self.send_json(res)
                return self.send_json({"error": f"接口不存在：{method} {path}"}, 404)
            finally:
                ctx["conn"].close()
        except Err as e:
            # 业务校验失败是正常路径，只记一行，不打 traceback
            try:
                print(f"[{db.now()}] {method} {path} -> {e.code} {e.msg[:120]}")
            except Exception:
                pass
            return self.send_json({"error": e.msg}, e.code)
        except Exception as e:
            try:
                print(f"[{db.now()}] {method} {path} 异常: {type(e).__name__}: {ai._redact(str(e))}")
                traceback.print_exc()
            except Exception:
                pass
            return self.send_json({"error": ai._redact(str(e)) or "服务器内部错误"}, 500)

    def do_login(self):
        try:
            b = self.body_json()
        except Err as e:
            return self.send_json({"error": e.msg}, e.code)
        pin = _s(b.get("pin"))
        want = _pin()
        if not want:
            return self.send_json({"ok": True}, cookie=f"{COOKIE}={make_token()}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}")
        if hmac.compare_digest(pin, want):
            ck = f"{COOKIE}={make_token()}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}"
            return self.send_json({"ok": True}, cookie=ck)
        time.sleep(0.6)
        return self.send_json({"error": "PIN 不对"}, 401)

    def serve_static(self, path):
        rel = "index.html" if path == "/" else path[len("/static/"):]
        rel = rel.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.normpath(STATIC_DIR)):
            return self.send_json({"error": "forbidden"}, 403)
        return self.send_file(full)


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


class _Tee:
    """同时写控制台和日志文件。任何一路失败都不影响另一路。"""

    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def _setup_logging():
    """诊断信息只写 data/server.log。

    为什么不同时写控制台：桌面快捷方式是无窗口启动的，那个控制台缓冲区没人读。
    一旦写满（比如每个业务校验失败都打一次 traceback），write 会直接阻塞，
    整个请求就永远不返回——表现为客户端超时。这个坑踩过一次，不再踩。
    需要看控制台时设 MIRROR_CONSOLE=1（启动.bat 已设）。
    """
    try:
        os.makedirs(db.DATA_DIR, exist_ok=True)
        log = open(os.path.join(db.DATA_DIR, "server.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    try:
        log.write(f"\n===== {db.now()} 启动 =====\n")
        log.flush()
    except Exception:
        pass
    console = None
    if os.environ.get("MIRROR_CONSOLE", "").strip().lower() in ("1", "true", "yes"):
        try:
            if sys.__stdout__ is not None:
                console = sys.__stdout__
        except Exception:
            console = None
    sys.stdout = _Tee(console, log) if console else log
    sys.stderr = _Tee(console, log) if console else log


def _open_browser():
    """MIRROR_OPEN_BROWSER=1 时，启动后自动打开浏览器。"""
    if os.environ.get("MIRROR_OPEN_BROWSER", "").strip() not in ("1", "true", "yes"):
        return
    url = f"http://127.0.0.1:{PORT}/"
    try:
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    except Exception:
        pass


def _port_busy(host, port):
    import socket as _s
    s = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1" if host == "0.0.0.0" else host, port)) == 0
    finally:
        s.close()


def main():
    _setup_logging()
    db.init()
    new_skills = skills.ensure_builtins()

    # 已经有一个实例在跑：不报错，直接开浏览器（双击两次也不会起两个进程）
    if _port_busy(os.environ.get("MIRROR_HOST", "0.0.0.0"), PORT):
        print(f"镜已在运行（端口 {PORT}），直接打开浏览器。")
        _open_browser()
        return

    chk = db.integrity_check()
    if chk != "ok":
        print(f"[!] 数据库自检异常：{chk}")
        bak = db.backup("integrity-fail")
        print(f"    已先备份到 {bak}，请勿继续写入。")
        return
    try:
        db.backup("startup")
    except Exception as e:
        print(f"[!] 启动备份失败（不影响运行）：{e}")

    host = os.environ.get("MIRROR_HOST", "0.0.0.0")
    srv = ThreadingHTTPServer((host, PORT), Handler)
    print("=" * 60)
    print("  镜 MirrorOS  ·  本地决策与长期对话系统")
    print("=" * 60)
    print(f"  本机：   http://127.0.0.1:{PORT}")
    for ip in lan_ips():
        print(f"  手机：   http://{ip}:{PORT}")
    print(f"  数据库： {db.DB_PATH}  (自检 {chk})")
    print(f"  Skill：  {len(skills.load_all())} 个" + (f"（新增 {len(new_skills)}）" if new_skills else ""))
    print(f"  AI：     {'已配置 DeepSeek' if ai.available() else '未配置（.env 里填 DEEPSEEK_API_KEY）'}")
    print(f"  登录 PIN：{'已设置' if _pin() else '未设置（当前无鉴权，仅本机安全）'}")
    print("=" * 60)
    _open_browser()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
