# -*- coding: utf-8 -*-
"""确定性引擎：不依赖任何 AI，可复算、可解释。

三块：
1. scan_alerts  —— 冷血风控。阈值全部来自 rules 表，可在界面上改。
2. mcda         —— 多准则决策 + 敏感性分析（结论会不会因为权重微调就翻盘）。
3. topic_metrics—— 方向深度/新鲜感衰减/主线候选分，用来回答「我该往哪深挖」。
"""
from datetime import date, datetime, timedelta

from . import db

SEV_ORDER = {"high": 0, "warn": 1, "info": 2}


def _d(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _days_since(s, ref=None):
    d = _d(s)
    if not d:
        return None
    return ((ref or date.today()) - d).days


def _month_start(ref=None):
    ref = ref or date.today()
    return ref.replace(day=1)


# ---------------------------------------------------------------- 风控扫描
def scan_alerts(conn):
    R = db.load_rules(conn)
    out = []

    def rule(key):
        r = R.get(key, {"value": 0, "enabled": False})
        return r["value"] if r["enabled"] else None

    def add(key, severity, subject_type, subject_id, subject_name, message, metric, threshold, detail=""):
        out.append({
            "fingerprint": f"{key}:{subject_type}:{subject_id}",
            "rule_key": key,
            "severity": severity,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "subject_name": subject_name,
            "message": message,
            "metric": round(metric, 3) if isinstance(metric, (int, float)) else metric,
            "threshold": threshold,
            "detail": detail,
        })

    topics = db.rows(conn.execute("SELECT * FROM topics"))
    active = [t for t in topics if t["status"] == "active"]

    # 规则 1：并行主线上限 —— 同时深挖超过 N 个方向，等于一个都没深。
    cap = rule("mainline_cap")
    mains = [t for t in active if t["mainline"]]
    if cap is not None and len(mains) > cap:
        add("mainline_cap", "high", "system", 0, "方向",
            f"主线 {len(mains)} 条，超过上限 {int(cap)} 条。你的注意力已经碎了。",
            len(mains), cap, "超出的必须封存并写理由：" + "、".join(t["name"] for t in mains))

    # 规则 2：浅尝积压 —— 深度≤1 且长期没动。
    idle_days = rule("shallow_idle_days")
    if idle_days is not None:
        shallow = []
        for t in active:
            if t["depth_level"] <= 1 and not t["mainline"]:
                d = _days_since(t["last_touch_on"] or t["started_on"])
                if d is not None and d > idle_days:
                    shallow.append((t, d))
        if shallow:
            shallow.sort(key=lambda x: -x[1])
            add("shallow_idle_days", "warn", "system", 0, "方向",
                f"{len(shallow)} 个主题停在「只能聊两句」且超过 {int(idle_days)} 天没动。",
                len(shallow), idle_days,
                "；".join(f"{t['name']}(深度{t['depth_level']},已闲置{d}天)" for t, d in shallow[:12]))

    # 规则 3：深度停滞 —— 已经在挖，但很久没升级。
    stall = rule("topic_stall_days")
    if stall is not None:
        stalled = []
        for t in active:
            if t["depth_level"] >= 2:
                d = _days_since(t["last_depth_up_on"] or t["last_touch_on"])
                if d is not None and d > stall:
                    stalled.append((t, d))
        if stalled:
            add("topic_stall_days", "warn", "system", 0, "方向",
                f"{len(stalled)} 个已进场的方向超过 {int(stall)} 天没有明显提升。",
                len(stalled), stall,
                "；".join(f"{t['name']}(深度{t['depth_level']},停滞{d}天)" for t, d in stalled[:12]))

    # 规则 4：精力漂移 —— 本月投入没落在主线上。
    share_min = rule("mainline_share_min")
    if share_min is not None:
        ms = _month_start().isoformat()
        rows = db.rows(conn.execute(
            "SELECT ts.hours AS h, t.mainline AS m FROM topic_sessions ts "
            "JOIN topics t ON t.id=ts.topic_id WHERE ts.on_date>=?", (ms,)))
        total = sum(r["h"] or 0 for r in rows)
        if total > 0:
            main_h = sum(r["h"] or 0 for r in rows if r["m"])
            share = main_h / total
            if share < share_min:
                add("mainline_share_min", "high", "system", 0, "方向",
                    f"本月主线投入占比 {share:.0%}，低于底线 {share_min:.0%}。精力在被别的东西吃掉。",
                    share, share_min, f"主线 {main_h:g}h / 总计 {total:g}h")

    # 规则 5：悬置 —— 超过 N 天无往来。
    pidle = rule("person_idle_days")
    if pidle is not None:
        idle_people = []
        for p in db.rows(conn.execute("SELECT * FROM people WHERE status!='cut'")):
            d = _days_since(p["last_contact_on"])
            if d is not None and d > pidle:
                idle_people.append((p, d))
        for p, d in idle_people:
            add("person_idle_days", "info", "person", p["id"], p["name"],
                f"与 {p['name']} 已 {d} 天无往来，超过悬置阈值 {int(pidle)} 天。要么推进，要么降级。",
                d, pidle, f"当前状态：{p['status']}")

    # 规则 6：单向索取 —— 付出/回报严重失衡。
    ratio_max = rule("givetake_ratio")
    if ratio_max is not None:
        for p in db.rows(conn.execute("SELECT * FROM people WHERE status!='cut'")):
            g = conn.execute("SELECT COALESCE(SUM(weight),0) FROM interactions "
                             "WHERE person_id=? AND direction='give'", (p["id"],)).fetchone()[0]
            t = conn.execute("SELECT COALESCE(SUM(weight),0) FROM interactions "
                             "WHERE person_id=? AND direction='take'", (p["id"],)).fetchone()[0]
            if g > 0:
                ratio = (g - t) / g
                if ratio > ratio_max:
                    add("givetake_ratio", "high", "person", p["id"], p["name"],
                        f"与 {p['name']} 的付出/回报差 {ratio:.0%}，超过阈值 {ratio_max:.0%}。你在被单向消耗。",
                        ratio, ratio_max, f"我付出 {g:g} / 我得到 {t:g}")

    # 规则 7：冷静期到期待裁决。
    pend = db.rows(conn.execute(
        "SELECT * FROM spend WHERE status='pending' ORDER BY amount DESC"))
    due = [s for s in pend if (_d(s["cool_until"]) or date.today()) <= date.today()]
    if due:
        add("spend_cool_due", "warn", "system", 0, "金钱",
            f"{len(due)} 笔支出冷静期已到，等你裁决（不是等你忘记）。",
            len(due), 0, "；".join(f"{s['item']} ¥{s['amount']:g}" for s in due[:12]))

    cool_days = rule("spend_cool_days")
    if cool_days is not None:
        inflight = [s for s in pend if (_d(s["cool_until"]) or date.today()) > date.today()]
        if inflight:
            add("spend_cool_pending", "info", "system", 0, "金钱",
                f"{len(inflight)} 笔支出在冷静期内，到期前不许下单。", len(inflight), cool_days,
                "；".join(f"{s['item']} ¥{s['amount']:g} 到 {s['cool_until']}" for s in inflight[:12]))

    # 规则 8：决策复盘逾期。
    grace = rule("decision_review_grace") or 0
    for d in db.rows(conn.execute(
            "SELECT * FROM decisions WHERE status!='closed' AND review_due IS NOT NULL AND review_due!=''")):
        overdue = _days_since(d["review_due"])
        if overdue is not None and overdue > grace:
            add("decision_review_grace", "warn", "decision", d["id"], d["title"],
                f"「{d['title']}」复盘日已过 {overdue} 天仍未复盘。不复盘的决策等于没做。",
                overdue, grace, f"复盘日 {d['review_due']}")

    # 规则 9：悬而未决 —— 生成了决策却迟迟不定。
    for d in db.rows(conn.execute("SELECT * FROM decisions WHERE status='open'")):
        age = _days_since(d["on_date"])
        if age is not None and age > 14:
            add("decision_stuck", "high", "decision", d["id"], d["title"],
                f"「{d['title']}」已悬置 {age} 天未决。悬置本身就是一种选择，而且是代价最高的一种。",
                age, 14, "")

    acks = {r["fingerprint"]: dict(r) for r in conn.execute("SELECT * FROM alert_acks")}
    for a in out:
        ack = acks.get(a["fingerprint"])
        a["ack_status"] = ack["status"] if ack else "open"
        a["ack_note"] = ack["note"] if ack else ""
        a["ack_at"] = ack["acked_at"] if ack else ""
    out.sort(key=lambda a: (a["ack_status"] != "open", SEV_ORDER.get(a["severity"], 9)))
    return out


# ---------------------------------------------------------------- 多准则决策
def mcda(criteria, options):
    """返回排序、三种决策规则的结果、以及敏感性/稳健性判断。"""
    crit = [c for c in criteria if isinstance(c, dict) and c.get("name")]
    opts = [o for o in options if isinstance(o, dict) and o.get("name")]
    if not crit or not opts:
        return {"ok": False, "reason": "准则或备选方案为空"}

    wsum = sum(max(float(c.get("weight") or 0), 0) for c in crit) or 1.0
    weights = {c["name"]: max(float(c.get("weight") or 0), 0) / wsum for c in crit}

    def score_of(opt, name):
        try:
            return max(0.0, min(10.0, float((opt.get("scores") or {}).get(name, 0) or 0)))
        except (TypeError, ValueError):
            return 0.0

    def weighted(opt, w):
        return sum(score_of(opt, n) * w[n] for n in w)

    base = {o["name"]: weighted(o, weights) for o in opts}

    # maximin：只看每个方案最差的那一项，悲观者视角。
    maximin = {o["name"]: min(score_of(o, n) for n in weights) for o in opts}
    # minimax regret：与每个准则上的最优值比较，取最大后悔值最小者。
    best = {n: max(score_of(o, n) for o in opts) for n in weights}
    regret = {o["name"]: max(best[n] - score_of(o, n) for n in weights) for o in opts}

    ranking = sorted(base.items(), key=lambda kv: -kv[1])
    w_winner = ranking[0][0]
    mm_winner = max(maximin.items(), key=lambda kv: (kv[1], base[kv[0]]))[0]
    rg_winner = min(regret.items(), key=lambda kv: (kv[1], -base[kv[0]]))[0]

    # 敏感性：单个准则权重 ±20%，冠军是否会翻盘。
    fragile = []
    for name in weights:
        for factor in (0.8, 1.2):
            w2 = dict(weights)
            w2[name] = weights[name] * factor
            s = sum(w2.values()) or 1.0
            w2 = {k: v / s for k, v in w2.items()}
            alt = {o["name"]: weighted(o, w2) for o in opts}
            new_winner = max(alt.items(), key=lambda kv: kv[1])[0]
            if new_winner != w_winner:
                fragile.append({"criterion": name, "factor": factor,
                                "winner": new_winner, "margin": round(alt[new_winner] - alt[w_winner], 3)})
                break

    margin = ranking[0][1] - (ranking[1][1] if len(ranking) > 1 else 0.0)
    agree = len({w_winner, mm_winner, rg_winner})

    if fragile:
        verdict = ("脆弱：权重只要微调，赢家就换人。说明你的准则权重是随手拍的，"
                   "先把权重理由写清楚，或者承认这两个方案对你其实差不多。")
    elif agree == 1:
        verdict = "稳健：加权、悲观、后悔最小三种规则指向同一方案，可以直接执行。"
    elif margin < 0.3:
        verdict = "胶着：三种规则不完全一致且分差很小。这种差距不值得继续算，去补信息或做小规模试验。"
    else:
        verdict = "基本稳健：加权分领先明显，但悲观/后悔视角另有倾向，注意下行风险。"

    return {
        "ok": True,
        "weights": {k: round(v, 3) for k, v in weights.items()},
        "ranking": [{"option": k, "weighted": round(v, 3),
                     "maximin": round(maximin[k], 3), "regret": round(regret[k], 3)} for k, v in ranking],
        "weighted_winner": w_winner, "maximin_winner": mm_winner, "minimax_regret_winner": rg_winner,
        "agreement": agree == 1,
        "fragile": fragile, "margin": round(margin, 3),
        "robust": "稳健" if (not fragile and margin >= 0.3) else ("脆弱" if fragile else "胶着"),
        "verdict": verdict,
    }


# ---------------------------------------------------------------- 方向深度
DEPTH_LABELS = ["刚接触", "能聊两句", "能讲清楚", "能产出东西", "专业水平", "能靠它吃饭"]


def _hours_by_window(conn, topic_id, start, end):
    row = conn.execute(
        "SELECT COALESCE(SUM(hours),0) FROM topic_sessions WHERE topic_id=? AND on_date>=? AND on_date<?",
        (topic_id, start.isoformat(), end.isoformat())).fetchone()
    return float(row[0] or 0)


def topic_metrics(conn):
    topics = db.rows(conn.execute("SELECT * FROM topics ORDER BY mainline DESC, depth_level DESC"))
    today_ = date.today()
    ms = _month_start()
    main_h_month = 0.0
    total_h_month = 0.0
    out = []

    for t in topics:
        tid = t["id"]
        started = _d(t["started_on"]) or today_
        # 投入退潮：用「近 30 天速度」对比「历史月均速度」，与主题年龄无关。
        # （早期做法只看开工后前 14 天，对长期主题毫无意义——一个跑了 4 个月、
        #  85 小时的主线会被判成「追新鲜感」，属于纯误报，已废弃。）
        days_alive = max((today_ - started).days, 1)
        monthly_avg = (t["hours"] or 0) / max(days_alive / 30.0, 1.0)
        recent = _hours_by_window(conn, tid, today_ - timedelta(days=30), today_ + timedelta(days=1))
        decay = 0.0 if monthly_avg <= 0 else max(0.0, min(1.0, 1 - recent / monthly_avg))
        # 持续性：有多少个不同的周真的碰过它。
        weeks = conn.execute(
            "SELECT COUNT(DISTINCT substr(on_date,1,7)||'-'||"
            "((CAST(substr(on_date,9,2) AS INTEGER)-1)/7)) FROM topic_sessions WHERE topic_id=?",
            (tid,)).fetchone()[0] or 0
        persistence = min(weeks / 20.0, 1.0)
        h_month = _hours_by_window(conn, tid, ms, ms + timedelta(days=32))
        total_h_month += h_month
        if t["mainline"]:
            main_h_month += h_month

        depth_index = (0.35 * (t["depth_level"] / 5.0)
                       + 0.30 * min((t["hours"] or 0) / 200.0, 1.0)
                       + 0.20 * min((t["artifacts"] or 0) / 10.0, 1.0)
                       + 0.15 * min(recent / 20.0, 1.0))
        focus = (0.40 * depth_index
                 + 0.25 * persistence
                 + 0.20 * ((t["interest"] or 3) / 5.0)
                 + 0.15 * (1 - decay))

        idle = _days_since(t["last_touch_on"] or t["started_on"])
        verdict = []
        if t["depth_level"] <= 1 and idle is not None and idle > 30 and not t["mainline"]:
            verdict.append("浅尝类：停在表面且已闲置，要么给它一个 30 天的深挖窗口，要么封存。")
        if decay > 0.7 and (t["hours"] or 0) > 1:
            verdict.append(f"退潮明显：历史月均 {monthly_avg:.1f}h，近 30 天只投 {recent:.1f}h。"
                           "投入速度在掉，不是兴趣在涨。")
        if t["depth_level"] >= 2 and (t["hours"] or 0) > 40 and decay < 0.45:
            verdict.append("有复合增长迹象：投入没有明显退潮，这是可以做深的结构。")
        if focus >= 0.55 and decay < 0.6:
            verdict.append("主线候选：深度、持续性、兴趣三项同时在线。")

        out.append({
            **t,
            "monthly_avg": round(monthly_avg, 1),
            "decay": round(decay, 3), "persistence": round(persistence, 3),
            "depth_index": round(depth_index, 3), "focus": round(focus, 3),
            "hours_30d": round(recent, 1), "hours_month": round(h_month, 1),
            "idle_days": idle,
            "depth_label": DEPTH_LABELS[min(max(t["depth_level"], 0), 5)],
            "verdict": verdict,
        })

    out.sort(key=lambda x: (-x["focus"], -x["depth_level"]))
    scatter = [t for t in out if t["status"] == "active" and t["depth_level"] <= 1]
    return {
        "topics": out,
        "mainline_share": round(main_h_month / total_h_month, 3) if total_h_month else None,
        "total_hours_month": round(total_h_month, 1),
        "shallow_count": len(scatter),
        "active_count": len([t for t in out if t["status"] == "active"]),
        "mainline_count": len([t for t in out if t["mainline"] and t["status"] == "active"]),
        "depth_labels": DEPTH_LABELS,
    }


# ---------------------------------------------------------------- 总览
def dashboard(conn):
    today_ = date.today()
    wk = today_ - timedelta(days=6)
    alerts = scan_alerts(conn)
    open_alerts = [a for a in alerts if a["ack_status"] == "open"]

    # 近 14 天情绪与事件序列
    series = []
    for i in range(13, -1, -1):
        d = (today_ - timedelta(days=i)).isoformat()
        ev = db.rows(conn.execute("SELECT emotion, outcome FROM events WHERE on_date=?", (d,)))
        emo = [e["emotion"] for e in ev if e["emotion"]]
        series.append({"date": d, "count": len(ev),
                       "emotion": round(sum(emo) / len(emo), 2) if emo else None})

    week_events = conn.execute("SELECT COUNT(*) FROM events WHERE on_date>=?", (wk.isoformat(),)).fetchone()[0]
    week_spend = conn.execute("SELECT COALESCE(SUM(amount),0) FROM spend WHERE on_date>=? AND status!='cancelled'",
                              (wk.isoformat(),)).fetchone()[0]
    avg_emotion = conn.execute("SELECT AVG(emotion) FROM events WHERE on_date>=?", (wk.isoformat(),)).fetchone()[0]
    totals = {
        "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "week_events": week_events,
        "week_spend": round(week_spend, 2),
        "week_emotion": round(avg_emotion, 2) if avg_emotion else None,
        "open_alerts": len(open_alerts),
        "high_alerts": len([a for a in open_alerts if a["severity"] == "high"]),
        "pending_spend": conn.execute("SELECT COUNT(*) FROM spend WHERE status='pending'").fetchone()[0],
        "open_decisions": conn.execute("SELECT COUNT(*) FROM decisions WHERE status!='closed'").fetchone()[0],
        "draft_traits": conn.execute("SELECT COUNT(*) FROM traits WHERE status='draft'").fetchone()[0],
        "people": conn.execute("SELECT COUNT(*) FROM people WHERE status!='cut'").fetchone()[0],
        # 对话与沉淀：聊天不直接进看板，但它是这套系统最主要的输入源，
        # 所以至少要让它可见——否则聊了几十轮，看板还是一片空白，看不出系统在工作。
        "conversations": conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
        "messages": conn.execute("SELECT COUNT(*) FROM messages WHERE ok=1").fetchone()[0],
        "draft_memory": conn.execute("SELECT COUNT(*) FROM memory WHERE status='draft'").fetchone()[0],
        "reports": conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
        "last_chat": (conn.execute(
            "SELECT MAX(created_at) FROM messages WHERE ok=1").fetchone()[0] or ""),
        # 对话整理出来的候选条目（暂存区），确认后才进各自正式表
        "proposals": conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0],
    }
    tm = topic_metrics(conn)
    return {
        "totals": totals, "series": series, "alerts": open_alerts[:8],
        "topics": tm["topics"][:6], "mainline_share": tm["mainline_share"],
        "shallow_count": tm["shallow_count"], "active_count": tm["active_count"],
        "mainline_count": tm["mainline_count"],
    }
