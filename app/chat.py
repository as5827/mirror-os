# -*- coding: utf-8 -*-
"""长期对话引擎。

要解决的问题：时间线会很长很大（几个月、上万字），
但上下文窗口有限。所以分五层拼装上下文，并按优先级裁剪：

  1. 系统规则（固定）
  2. 已确认长期记忆（active）
  3. 已采纳画像（accepted）
  4. 滚动摘要（把老对话压缩，原文仍在 messages 里）
  5. 最近 N 轮原话 + 按当前问题检索到的历史事件

准确性的关键：要求模型用 [事件#12]/[决策#3]/[记忆#5]/[画像#2] 标注引用，
服务端再逐条校验编号是否真实存在。编造引用会被记录并在界面上标红。
"""
import json
import re

from . import ai, comfort, db, evidence, knowledge, lexicon, persona, phrasing, skills, tone, websearch

CHAR_BUDGET = 26000      # 单次上下文总字符上限（含历史，不含回复）
TURN_KEEP = 16           # 默认保留最近多少条消息原话
PER_MSG_CAP = 1400       # 单条历史消息最多注入多少字

SYS = (
    "你是「镜」——一个长期对话伙伴，底层只认证据。你的客户是：" + persona.load() + "。\n"
    "关于情绪与安慰（这条改过，之前的版本一律禁止安慰，是错的）：\n"
    "- 他会在不同情境下需要不同的东西：有时要刀，有时要被接住。判断依据是下面给你的"
    "【本轮体例】指令，不是你猜。\n"
    "- 安慰必须是具体的：指向他说的那句原话，准确命名感受。通用套话（「你已经很棒了」"
    "「很多人也这样」）一律禁止——那不是共情，是敷衍。\n"
    "- 共情与锋利不矛盾：先接住情绪，然后该说的硬话照样说。绝不因为安慰就把结论说软。\n"
    "规则：\n"
    "1. 凡是引用他过去的事，必须标注编号：事件用 [事件#12]，决策用 [决策#3]，"
    "长期记忆用 [记忆#5]，画像用 [画像#2]。编号必须来自下面给你的材料，绝不许编。\n"
    "2. 关于他**过去**的事实，材料里没有就说「记录里没有这条」，不要推测成事实。"
    "但他**本轮刚说的**就是材料本身——不许因为「库里没有」而否定他刚说的事，"
    "「记录没有」不等于「事情没发生」。\n"
    "3. 区分三件事：他说的、你推断的、纯假设。混着说等于撒谎。"
    "但分清是为了标清楚，不是为了盘问他——详见下面的【证据立场】。\n"
    "4. 结论要落到可执行动作，且带阈值或时间点。\n"
    "5. 中文回答，不要长篇大论，但你若发现矛盾必须直说。\n"
    "6. 分析社会行为与心理机制时，优先用下面【本地知识库】里的条目，并写 [理论#编号]。"
    "**用之前先查索引**：索引里已经有的，绝不许说「不在你的库里」。"
    "索引里确实没有的概念，如果你仍要用，必须明说「这条来自通用知识，不在你的库里」，"
    "并降低其可信度——不许把模型记忆包装成理论依据。\n"
    "7. 用了理论就要同时写清它的【误用风险】边界：是机制描述，不是给人贴标签。\n"
    "8. 如果下面给了【联网检索到的文献】，你可以用 [文献#编号] 引用。但记住："
    "被引次数只说明关注度，不证明结论成立；文献支持的是「机制层面存在这种可能」，"
    "**推不出「你就是这种机制」**。arXiv 是预印本，未经同行评审，引用时必须标明。"
)


def _clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def build_context(conn, conv, exclude_id=None, recall_query=None):
    """返回 (messages_list, meta)。meta 记录注入了什么，便于审计。

    exclude_id：拼上下文前刚落库的那条消息 id，要从历史里排除，避免重复注入。
    recall_query：排除之后历史里没有当前这句话了，用它补上检索关键词，
                  否则「按最近用户消息找相关事件」会退化成用上一句去搜。
    """
    parts = []
    meta = {"memory": [], "traits": [], "events": [], "summary_ids": [], "skill": conv.get("skill") or ""}

    # 2. 长期记忆
    mem = db.rows(conn.execute(
        "SELECT id,statement,kind FROM memory WHERE status='active' ORDER BY id LIMIT 40"))
    if mem:
        meta["memory"] = [m["id"] for m in mem]
        parts.append("【他确认过的长期事实（当作前提）】")
        parts += [f"- [记忆#{m['id']}] {m['statement']}" for m in mem]

    # 3. 已采纳画像
    tr = db.rows(conn.execute(
        "SELECT id,name,statement,counter FROM traits WHERE status='accepted' "
        "ORDER BY confidence DESC LIMIT 12"))
    if tr:
        meta["traits"] = [t["id"] for t in tr]
        parts.append("\n【他采纳过的行为模式（可被新证据推翻）】")
        for t in tr:
            s = f"- [画像#{t['id']}] {t['name']}：{t['statement']}"
            if t["counter"]:
                s += f"（反制：{t['counter']}）"
            parts.append(s)

    # 4. 滚动摘要
    sums = db.rows(conn.execute(
        "SELECT id,upto_message_id,content FROM summaries WHERE conversation_id=? "
        "ORDER BY upto_message_id DESC LIMIT 3", (conv["id"],)))
    last_cut = 0
    if sums:
        meta["summary_ids"] = [s["id"] for s in sums]
        last_cut = max(s["upto_message_id"] for s in sums)
        parts.append("\n【此前对话的压缩摘要（原文仍在，需要时可检索）】")
        for s in reversed(sums):
            parts.append(_clip(s["content"], 1800))

    # 5. 历史消息
    # exclude_id：send() 会先把用户消息落库再拼上下文，如果不排除，这条会既出现在
    # 历史里、又在 send() 里 append 一次，模型看到同一句话两遍（实测它会回「你发了两遍」）。
    sql = "SELECT id,role,content FROM messages WHERE conversation_id=? AND ok=1"
    args = [conv["id"]]
    if last_cut:
        sql += " AND id>?"
        args.append(last_cut)
    if exclude_id:
        sql += " AND id<>?"
        args.append(exclude_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(TURN_KEEP)
    hist = list(reversed(db.rows(conn.execute(sql, args))))

    # 5b. 检索：用最近几条用户消息当查询，找回更早的相关事件
    q = recall_query or " ".join([m["content"] for m in hist if m["role"] == "user"][-2:]) or \
        (hist[-1]["content"] if hist else "")
    ev_txt, ev_ids = ai.recall_events(conn, q, limit=8)
    if ev_txt:
        meta["events"] = ev_ids
        parts.append("\n" + ev_txt)

    # 5c. 知识库：先给紧凑索引（让它知道库里有啥），再给匹配到的完整条目
    cat, cat_ids = knowledge.catalog_block(conn)
    if cat:
        parts.append("\n" + cat)
        meta["theory_catalog"] = len(cat_ids)
    th = knowledge.match(conn, q, limit=3)
    if th:
        detail, th_ids = knowledge.detail_block(th)
        parts.append("\n" + detail)
        meta["theory"] = th_ids

    # 5d. 联网检索（仅当该会话显式开启）。中英双语：中文优先，英文经典兜底。
    if conv.get("web"):
        wlang = (conv.get("web_lang") or "both").strip() or "both"
        zh_q, en_q, _wq_theory = websearch.query_for(conn, q)
        if zh_q or en_q:
            try:
                refs, note, weak = websearch.search_multi(
                    conn, zh_q, en_q, lang=wlang, limit=4)
                if refs:
                    wblk, wids, weak2 = websearch.block(refs)
                    parts.append("\n" + wblk)
                    meta["refs"] = wids
                    meta["web_weak"] = bool(weak or weak2)
                    if wlang == "zh":
                        meta["web_query"] = f"中「{zh_q}」"
                    elif wlang == "en":
                        meta["web_query"] = f"英「{en_q}」"
                    else:
                        meta["web_query"] = f"中「{zh_q}」+ 英「{en_q}」"
                    meta["web_source"] = note
                    meta["web_lang"] = wlang
            except Exception as e:
                meta["web_error"] = f"{type(e).__name__}"
        else:
            meta["web_skipped"] = "没有可用的检索词"

    # 5e. 本轮体例：先共情还是直接分析（按会话设置 + 情绪信号决定）
    cmode = (conv.get("comfort") or "auto").strip() or "auto"
    cblock, cmeta = comfort.instruction(cmode, q)
    parts.append("\n=== 本轮体例 ===\n" + cblock)
    meta["comfort"] = cmeta

    # 5e2. 语气层：同一套判断，用对事不对人的方式说
    tblock = tone.block(conv.get("tone") or "warm")
    parts.append("\n=== 语气 ===\n" + tblock)
    meta["tone"] = (conv.get("tone") or "warm")

    # 5e2b. 背景参照：他说一件事时，先给「这类情形通常是什么样」的背景
    # 只在联网开启时取。拿不到就跳过，绝不能因此不回答。
    if (conv.get("web") or 0) and q and len(q.strip()) >= 4:
        try:
            # 不能用整句话去查维基：实测「昨天下午我去找她，想把话说清楚」
            # 会命中「林宅血案 / 林志颖」（因为姓林）。只取词表认得的短概念。
            zh_kw = lexicon.life_terms(q, limit=2) or lexicon.zh_terms(q, limit=2)
            en_kw = lexicon.life_en(zh_kw)
            bg_rows = websearch.background(" ".join(zh_kw), " ".join(en_kw)) if zh_kw else []
            bg = websearch.background_block(bg_rows)
            if bg:
                parts.append(bg)
                meta["background"] = [r["title"] for r in bg_rows]
        except Exception as e:      # noqa: BLE001 - 背景是加分项，失败不影响回答
            meta["background_error"] = _clip(str(e), 120)

    # 5e3. 证据立场：他的自述是最好证据，不许反复盘问
    parts.append("\n=== 证据立场 ===\n" + evidence.block())

    # 5f. 话术层：他在问「这句话该怎么说」时，按情境给可照说的原话
    phr_block, phr_meta = phrasing.instruction(q)
    if phr_block:
        parts.append("\n=== 话术规程 ===\n" + phr_block)
    meta["phrasing"] = phr_meta

    # skill 正文（选中才注入）
    if conv.get("skill"):
        sb = skills.body_block(conv["skill"])
        if sb:
            parts.append("\n" + sb)
    else:
        cat = skills.catalog_block()
        if cat:
            parts.append("\n" + cat +
                         "\n（如果他的问题正好适合某个 skill，先说明要用哪个，再按它的规程回答。）")

    system = SYS + ("\n\n=== 材料 ===\n" + "\n".join(parts) if parts else "")

    msgs = [{"role": "system", "content": system}]
    for m in hist:
        msgs.append({"role": m["role"], "content": _clip(m["content"], PER_MSG_CAP)})

    # 预算裁剪：从最老的对话轮次开始丢，但保 system 与最后一条用户消息
    def total():
        return sum(len(x["content"]) for x in msgs)

    while total() > CHAR_BUDGET and len(msgs) > 3:
        del msgs[1]
    meta["chars"] = total()
    meta["turns"] = len(msgs) - 1
    return msgs, meta


REF_RE = re.compile(r"\[(事件|决策|记忆|画像|理论|文献)#(\d+)\]")


def validate_refs(conn, text):
    """校验模型标注的引用是否真实存在。返回 (ok列表, 伪造列表)。"""
    tables = {"事件": "events", "决策": "decisions", "记忆": "memory",
              "画像": "traits", "理论": "theory", "文献": "reference"}
    ok, bad = [], []
    seen = set()
    for typ, sid in REF_RE.findall(text or ""):
        i = int(sid)
        key = (typ, i)
        if key in seen:
            continue
        seen.add(key)
        tbl = tables[typ]
        row = conn.execute(f"SELECT 1 FROM {tbl} WHERE id=?", (i,)).fetchone()
        item = {"type": typ, "id": i}
        if row:
            ok.append(item)
        else:
            bad.append(item)
    return ok, bad


def send(conn, conv_id, user_text, skill=None):
    """发一条消息：先落库用户消息，再调用模型，最后落库回复。失败不丢用户输入。"""
    text = (user_text or "").strip()
    if not text:
        raise ValueError("消息不能为空")
    if len(text) > 8000:
        text = text[:8000]

    conv = db.one(conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)))
    if not conv:
        raise RuntimeError("会话不存在")
    if skill is not None:
        conn.execute("UPDATE conversations SET skill=? WHERE id=?", (skill or "", conv_id))
        conv["skill"] = skill or ""

    cur = conn.execute(
        "INSERT INTO messages(conversation_id,role,content,skill,created_at) VALUES(?,?,'','',?)",
        (conv_id, "user", db.now()))
    conn.execute("UPDATE messages SET content=?,skill=? WHERE id=?", (text, conv["skill"] or "", cur.lastrowid))
    conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (db.now(), conv_id))
    conn.commit()
    uid = cur.lastrowid

    # 用户消息已经落库（下面 build_context 会读到它），所以用 exclude_id 排除，
    # 再手动 append 一次——不是两次。之前漏了排除，模型会看到同一句话两遍。
    msgs, meta = build_context(conn, conv, exclude_id=uid, recall_query=text)
    msgs.append({"role": "user", "content": text})
    # build_context 里的字数/轮次是在追加当前这句之前算的，这里补上，
    # 否则界面上第一句会显示「0 轮」，字数也会少算当前这句。
    meta["turns"] = len(msgs) - 1
    meta["chars"] = sum(len(m["content"]) for m in msgs)

    try:
        reply = ai.chat(msgs, temperature=0.5, max_tokens=2200, json_mode=False)
    except Exception as e:
        conn.execute("INSERT INTO messages(conversation_id,role,content,ok,err,created_at) "
                     "VALUES(?,?,?,0,?,?)",
                     (conv_id, "assistant", "", ai._redact(str(e)), db.now()))
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (db.now(), conv_id))
        conn.commit()
        raise

    good, bad = validate_refs(conn, reply)
    c2 = conn.execute(
        "INSERT INTO messages(conversation_id,role,content,refs,bad_refs,skill,ok,created_at) "
        "VALUES(?,?,?,?,?,?,1,?)",
        (conv_id, "assistant", reply, json.dumps(good), json.dumps(bad),
         conv["skill"] or "", db.now()))
    # 首条用户消息用来给会话起名
    if conv["title"] in ("新对话", "", None):
        conn.execute("UPDATE conversations SET title=? WHERE id=?", (_clip(text, 22), conv_id))
    conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (db.now(), conv_id))
    conn.commit()

    return {"message_id": c2.lastrowid, "user_message_id": uid, "reply": reply,
            "refs": good, "bad_refs": bad,
            "context": {"chars": meta["chars"], "turns": meta["turns"],
                        "memory": len(meta["memory"]), "traits": len(meta["traits"]),
                        "events": len(meta["events"]), "summaries": len(meta["summary_ids"]),
                        "theory": len(meta.get("theory") or []),
                        "theory_catalog": meta.get("theory_catalog", 0),
                        "refs": len(meta.get("refs") or []),
                        "web_query": meta.get("web_query") or "",
                        "web_source": meta.get("web_source") or "",
                        "web_lang": meta.get("web_lang") or "",
                        "web_weak": bool(meta.get("web_weak")),
                        "web_skipped": meta.get("web_skipped") or "",
                        "comfort_mode": (conv.get("comfort") or "auto"),
                        "comfort_applied": (meta.get("comfort") or {}).get("applied", ""),
                        "comfort_level": (meta.get("comfort") or {}).get("level", 0),
                        "comfort_signals": (meta.get("comfort") or {}).get("signals", []),
                        "phrasing_ask": bool((meta.get("phrasing") or {}).get("ask")),
                        "phrasing_kind": (meta.get("phrasing") or {}).get("kind", ""),
                        "background": meta.get("background") or [],
                        "background_error": meta.get("background_error") or "",
                        "phrasing_ratio": [(meta.get("phrasing") or {}).get("warm", 0),
                                           (meta.get("phrasing") or {}).get("direct", 0)],
                        "web_error": meta.get("web_error") or ""}}


def maybe_auto_summarize(conn, conv_id, threshold=60):
    """消息数量超过阈值时自动压缩，避免上下文无限膨胀。"""
    n = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? AND ok=1",
                     (conv_id,)).fetchone()[0]
    if n < threshold:
        return None
    return summarize(conn, conv_id, keep_recent=14)


SUMMARIZE_PROMPT = """下面是一段长期对话（编号 #id 为消息编号）。把它压缩成交接摘要，要求：

1. 分四段：已确定的结论 / 未决的问题 / 他观点变化过的地方 / 下一步唯一动作。
2. 只写结论和状态，不要复述过程，不要客套话。
3. 总量 300 字以内，宁可漏细节也要保住结构。
4. 如果某段没有内容，写「无」。

对话：
{digest}

输出 JSON：{{"summary":"四段式摘要文本"}}"""


def summarize(conn, conv_id, keep_recent=14):
    """把除最近 keep_recent 条之外的消息压成一条摘要。"""
    rows = db.rows(conn.execute(
        "SELECT id,role,content FROM messages WHERE conversation_id=? AND ok=1 ORDER BY id",
        (conv_id,)))
    if len(rows) <= keep_recent + 2:
        raise RuntimeError(f"消息还不够多（当前 {len(rows)} 条），没必要压缩。")
    cut_idx = len(rows) - keep_recent
    chunk = rows[:cut_idx]
    upto = chunk[-1]["id"]
    digest = "\n".join(f"#{r['id']} [{r['role']}] {_clip(r['content'], 700)}" for r in chunk)
    data = ai._parse_json(ai.chat(
        [{"role": "system", "content": SYS},
         {"role": "user", "content": SUMMARIZE_PROMPT.format(digest=digest)}],
        temperature=0.3, max_tokens=1200))
    summary = (data.get("summary") or "").strip()
    if len(summary) < 20:
        raise RuntimeError("摘要生成质量不合格，已放弃本次压缩。")
    conn.execute("DELETE FROM summaries WHERE conversation_id=? AND upto_message_id<=?",
                 (conv_id, upto))
    conn.execute("INSERT INTO summaries(conversation_id,upto_message_id,content,created_at) "
                 "VALUES(?,?,?,?)", (conv_id, upto, summary, db.now()))
    conn.commit()
    ai._save_report(conn, "summarize", {"conv": conv_id, "upto": upto, "count": len(chunk)},
                    {"summary": summary})
    return {"upto_message_id": upto, "messages_compressed": len(chunk), "summary": summary}


def search_messages(conn, query, limit=10):
    """在长对话历史里按关键词翻旧账（3 字以上走 FTS，2 字走 LIKE）。"""
    terms = ai._split_terms(query)
    if not terms:
        return []
    hit_ids = []
    long_terms = [t for t in terms if len(t) >= 3][:5]
    if long_terms:
        m = " OR ".join('"%s"' % t.replace('"', "") for t in long_terms)
        try:
            hit_ids += [r[0] for r in conn.execute(
                "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? LIMIT ?",
                (m, limit))]
        except Exception:
            pass
    short = [t for t in terms if len(t) == 2][:5]
    for t in short:
        hit_ids += [r[0] for r in conn.execute(
            "SELECT id FROM messages WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{t}%", limit))]
    if not hit_ids:
        return []
    uniq = list(dict.fromkeys(hit_ids))[:limit]
    q = ",".join("?" * len(uniq))
    return db.rows(conn.execute(
        f"SELECT id,conversation_id,role,content,created_at FROM messages "
        f"WHERE id IN ({q}) ORDER BY id DESC", uniq))
