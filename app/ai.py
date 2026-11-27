# -*- coding: utf-8 -*-
"""AI 分析层：调用 DeepSeek，但绝不信它。

三条铁律：
1. 每条关于「你」的结论必须挂真实的 evidence 事件 id，库里查不到的直接丢弃并计数。
2. 输出必须是 JSON；解析失败则原文入库、标记失败，不猜。
3. API Key 只从环境/​.env 读，永不回显、永不写日志。
"""
import json
import os
import re
import urllib.error
import urllib.request

from . import db, persona

# ---------------------------------------------------------------- 模型可切换
# 默认走 DeepSeek，但任何「OpenAI 兼容」的服务都能接：
#   通义千问（dashscope compatible-mode）、智谱 GLM、月之暗面 Kimi、
#   硅基流动 SiliconFlow、本地 Ollama / vLLM / LM Studio……
# 只改 .env，不用动代码：
#   LLM_BASE_URL=https://api.siliconflow.cn/v1      # 服务地址（不带 /chat/completions）
#   LLM_MODEL=Qwen/Qwen2.5-72B-Instruct
#   LLM_API_KEY=sk-xxx                              # 不给就用 DEEPSEEK_API_KEY
# 本地 Ollama 示例：LLM_BASE_URL=http://127.0.0.1:11434/v1  LLM_MODEL=qwen2.5:14b  LLM_API_KEY=ollama
PRESETS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-chat"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-plus"),
    "kimi": ("https://api.moonshot.cn/v1", "moonshot-v1-32k"),
    "siliconflow": ("https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-72B-Instruct"),
    "ollama": ("http://127.0.0.1:11434/v1", "qwen2.5:14b"),
}

_PROVIDER = (os.environ.get("LLM_PROVIDER") or "deepseek").strip().lower()
_base = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
_model = (os.environ.get("LLM_MODEL") or "").strip()
if not _base:
    _base = PRESETS.get(_PROVIDER, PRESETS["deepseek"])[0]
if not _model:
    _model = PRESETS.get(_PROVIDER, PRESETS["deepseek"])[1]
API_BASE = _base
API_URL = _base + "/chat/completions"
MODEL = _model
PROVIDER = _PROVIDER
TIMEOUT = 180

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env():
    p = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(p):
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_env()


def api_key():
    # LLM_API_KEY 优先（换服务商时用它），否则回落到 DEEPSEEK_API_KEY
    return (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def available():
    return bool(api_key())


def _redact(text):
    k = api_key()
    if k and k in text:
        text = text.replace(k, "sk-***")
    return re.sub(r"sk-[A-Za-z0-9]{8,}", "sk-***", text)


def _opener():
    """默认绕过系统代理。

    这台机器的 WinINET 代理(127.0.0.1:7897)对 api.deepseek.com 是坏的（超时），
    而 urllib 在 Windows 上会自动读注册表代理设置，导致 SSL EOF。
    需要走代理时在 .env 里设 MIRROR_PROXY=http://127.0.0.1:7897。
    """
    proxy = os.environ.get("MIRROR_PROXY", "").strip()
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def chat(messages, temperature=0.4, max_tokens=3000, json_mode=True):
    key = api_key()
    if not key:
        raise RuntimeError(
            f"未配置 API Key（当前服务商：{PROVIDER}）。"
            "请在项目根目录 .env 里填写 LLM_API_KEY 或 DEEPSEEK_API_KEY")
    body = {"model": MODEL, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens, "stream": False}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    opener = _opener()
    last = None
    for _ in range(2):
        try:
            with opener.open(req, timeout=TIMEOUT) as r:
                payload = json.loads(r.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            last = f"HTTP {e.code}: {_redact(detail)}"
            if e.code in (401, 402, 403):
                break
        except Exception as e:  # 网络/超时
            last = _redact(str(e))
    raise RuntimeError(f"DeepSeek 调用失败：{last}")


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _event_digest(conn, limit=60):
    evs = db.rows(conn.execute(
        "SELECT id,on_date,domain,title,my_words,my_actions,other_response,emotion,outcome,"
        "initiative,hours,money,replay FROM events ORDER BY on_date DESC, id DESC LIMIT ?", (limit,)))
    lines = []
    for e in evs:
        parts = [f"#{e['id']} {e['on_date']} [{e['domain']}] {e['title']}",
                 f"情绪{e['emotion']}/5 结果{e['outcome']} 主动性{e['initiative']}/4"]
        if e["my_words"]:
            parts.append(f"我说:{e['my_words'][:160]}")
        if e["my_actions"]:
            parts.append(f"我做:{e['my_actions'][:160]}")
        if e["other_response"]:
            parts.append(f"反应:{e['other_response'][:120]}")
        if e["replay"]:
            parts.append(f"重来:{e['replay'][:120]}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _save_report(conn, kind, payload, result, ok=1, err=""):
    conn.execute("INSERT INTO reports(kind,payload,result,ok,err,created_at) VALUES(?,?,?,?,?,?)",
                 (kind, json.dumps(payload, ensure_ascii=False)[:200000],
                  json.dumps(result, ensure_ascii=False)[:200000], ok, err, db.now()))
    conn.commit()


# ------------------------------------------------------------ 推理记忆：结论回灌
# 铁律：只回灌 status='accepted' 的画像，draft 绝不进上下文。
# 理由：一条还没被人确认的 AI 猜测如果被反复注入，会自我强化成"既定事实"。
def known_block(conn, limit_acc=12, limit_rej=6):
    """把「已经形成的结论」编成上下文块。返回 (文本, 元数据)。"""
    acc = db.rows(conn.execute(
        "SELECT id,name,direction,statement,blind_spot,cost,counter,confidence FROM traits "
        "WHERE status='accepted' ORDER BY confidence DESC LIMIT ?", (limit_acc,)))
    rej = db.rows(conn.execute(
        "SELECT id,name,reject_reason FROM traits WHERE status='rejected' "
        "ORDER BY id DESC LIMIT ?", (limit_rej,)))
    mem = db.rows(conn.execute(
        "SELECT id,statement,kind FROM memory WHERE status='active' ORDER BY id LIMIT 40"))

    parts = []
    if mem:
        parts.append("【用户亲自确认的长期事实（最高可信，不必再验证）】")
        for m in mem:
            parts.append(f"- [{m['kind']}#{m['id']}] {m['statement']}")
    if acc:
        parts.append("【用户已采纳的历史结论（可被新证据推翻，不要当既定事实复述）】")
        for t in acc:
            s = f"- #{t['id']} {t['name']}（{t['direction']}，置信{int((t['confidence'] or 0) * 100)}%）：{t['statement']}"
            if t["counter"]:
                s += f" ｜已定反制：{t['counter']}"
            parts.append(s)
    if rej:
        parts.append("【用户驳回过的结论（用户认为不成立；除非有新证据，不要重复提出）】")
        for t in rej:
            parts.append(f"- #{t['id']} {t['name']}｜驳回理由：{t['reject_reason']}")

    meta = {"memory_ids": [m["id"] for m in mem],
            "accepted_trait_ids": [t["id"] for t in acc],
            "rejected_trait_ids": [t["id"] for t in rej]}
    return ("\n".join(parts) if parts else "", meta)


KNOWN_RULES = """
关于上面这些既有结论，你必须遵守：
1. 已经被采纳、且新证据没有变化的结论，不要换个说法再讲一遍——那是浪费他的时间。
2. 如果新证据加强、修正或推翻了某条既有结论，请在 updates_known 里明确写出改什么、为什么。
3. 用户驳回过的结论，除非你有新的、独立的事件证据，否则不许再提。
4. 用户亲自确认的长期事实，视为前提，不要质疑，但可以用它去解释新事件。"""


def _past_lessons(conn, kind, exclude_id, limit=6):
    """过去决策的复盘教训。负分=判断偏差，这是最有价值的部分。"""
    rows = db.rows(conn.execute(
        "SELECT id,title,kind,on_date,review_score,review_lesson,chosen FROM decisions "
        "WHERE status='closed' AND TRIM(COALESCE(review_lesson,''))!='' AND id!=? "
        "ORDER BY (kind=?) DESC, ABS(COALESCE(review_score,0)) DESC, on_date DESC LIMIT ?",
        (exclude_id, kind, limit)))
    if not rows:
        return "", []
    out = ["【他过去做过的决策复盘（review_score 负数=当时判断有偏差）】"]
    for r in rows:
        verdict = "判断到位" if (r["review_score"] or 0) > 0 else (
            "有偏差" if (r["review_score"] or 0) < 0 else "中性")
        out.append(f"- #{r['id']} [{r['kind']}]「{r['title']}」选了「{r['chosen']}」"
                   f" → {verdict}：{r['review_lesson']}")
    return "\n".join(out), [r["id"] for r in rows]


SYS_COMMON = (
    "你是一个冷峻、不讨好、只认证据的行为分析师。你的客户是：" + persona.load() + "。"
    "注意：这是分析报告场合，不要安慰、不要情绪铺垫，他要的是结论。"
    "你的每一句结论都必须能指回具体事件编号。宁可说「证据不足」，也不要编。"
    "全部输出必须是合法 JSON。"
)

UPDATES_SCHEMA = (
    '"updates_known":[{"target_id":既有结论编号或0,"target":"结论名",'
    '"change":"加强|修正|推翻","why":"依据哪些新事件","evidence":[事件id数组]}]'
)

DUP_RULE = (
    '每条 pattern 必须带 "duplicate_of_id"：如果它只是上面某条已采纳结论的换措辞，'
    '填那条结论的编号（并在 updates_known 里写加强）；确实是新机制则填 0。'
)


# ------------------------------------------------------------ 检索（recall）
# 开源记忆框架的核心能力是「按相关度召回」而不是「只看最近的」。
# 这里用 SQLite FTS5 + trigram 做，但 trigram 有个硬限制：查询必须 ≥3 个字符。
# 中文里「面试」「边界」这类双字词极常用，若只用 FTS 会永远查不到，
# 所以做混合检索：≥3 字走 FTS（按 bm25 相关度排序），2 字走 LIKE 兜底。
FTS_SQL = ("SELECT e.id, e.on_date, e.domain, e.title, e.my_actions, e.other_response "
           "FROM events_fts f JOIN events e ON e.id = f.rowid "
           "WHERE events_fts MATCH ? ORDER BY bm25(events_fts) LIMIT ?")


def _split_terms(query):
    out = []
    for t in re.split(r"[\s,，、;；/|·]+", str(query or "")):
        t = t.strip().strip('"')
        if len(t) >= 2 and t not in out:
            out.append(t)
    return out


def recall_events(conn, query, limit=10, exclude_ids=None):
    """按关键字/子串召回历史事件。长词走 FTS 相关度排序，双字词走 LIKE 兜底。"""
    terms = _split_terms(query)
    if not terms:
        return "", []
    skip = set(exclude_ids or [])
    long_terms = [t for t in terms if len(t) >= 3][:6]
    short_terms = [t for t in terms if len(t) == 2][:6]

    hits, seen = [], set()

    def add(r):
        if r["id"] in skip or r["id"] in seen:
            return
        seen.add(r["id"])
        hits.append(dict(r))

    if long_terms:
        match = " OR ".join('"%s"' % t.replace('"', "") for t in long_terms)
        try:
            for r in conn.execute(FTS_SQL, (match, limit + 8)):
                add(r)
        except Exception:
            pass

    if short_terms:
        cols = ("title", "my_words", "my_actions", "other_response", "replay", "tags")
        ors, params = [], []
        for t in short_terms:
            like = f"%{t}%"
            ors.append("(" + " OR ".join(f"COALESCE({c},'') LIKE ?" for c in cols) + ")")
            params += [like] * len(cols)
        try:
            for r in conn.execute(
                    "SELECT id,on_date,domain,title,my_actions,other_response FROM events "
                    "WHERE " + " OR ".join(ors) + " ORDER BY on_date DESC LIMIT ?",
                    params + [limit + 8]):
                add(r)
        except Exception:
            pass

    hits = hits[:limit]
    if not hits:
        return "", []
    lines = ["【按相关度召回的历史事件（不是最近的，是和你当前问题相关的）】"]
    for r in hits:
        s = f"#{r['id']} {r['on_date']} [{r['domain']}] {r['title']}"
        if r.get("my_actions"):
            s += f" ｜我做:{r['my_actions'][:110]}"
        if r.get("other_response"):
            s += f" ｜反应:{r['other_response'][:80]}"
        lines.append(s)
    return "\n".join(lines), [r["id"] for r in hits]


# ------------------------------------------------------------ 巩固（consolidation）
# mem0/Letta 的另一个核心能力：把零散历史压成持久记忆条目。
# 这里用 DeepSeek 做压缩，但产物是 status='draft'，必须你确认才进入上下文。
CONSOLIDATE_PROMPT = """下面是客户在某个领域的历史事件（编号 #id 为证据编号）：

{digest}

任务：把这些零散事件压缩成**少数几条**长期有效的记忆。要求：
1. 只写**跨事件重复出现**的规律，不写单次事件；单次事件不算记忆。
2. 每条记忆必须是关于「他本人」的陈述，不是事件复述。
3. 必须挂证据编号，至少 2 个，必须真实存在。
4. 如果证据不够形成规律，就返回空数组——不要为了凑数而编。

输出 JSON：
{{"memories":[{{"statement":"一句关于他的陈述，20-60字","evidence":[事件id...],"confidence":0.0到1.0}}]}}"""


def consolidate(conn, older_than_days=30, max_domains=4):
    """把很久以前的、尚未巩固的事件压成记忆草稿。"""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=older_than_days)).isoformat()
    rows = db.rows(conn.execute(
        "SELECT id,on_date,domain,title,my_words,my_actions,other_response,emotion,outcome,replay "
        "FROM events WHERE COALESCE(consolidated,0)=0 AND on_date<=? ORDER BY on_date", (cutoff,)))
    if not rows:
        raise RuntimeError(f"没有可巩固的事件。需要 {older_than_days} 天以前、且尚未巩固的记录。")

    by_domain = {}
    for r in rows:
        by_domain.setdefault(r["domain"] or "其他", []).append(r)
    # 事件多的领域优先，单领域最多 40 条，避免撑爆上下文
    picked = sorted(by_domain.items(), key=lambda kv: -len(kv[1]))[:max_domains]

    valid_ids = {r["id"] for r in conn.execute("SELECT id FROM events")}
    created, covered, skipped = [], [], 0
    for domain, evs in picked:
        chunk = evs[:40]
        digest = "\n".join(
            f"#{e['id']} {e['on_date']} {e['title']} 情绪{e['emotion']}/5 结果{e['outcome']}"
            + (f" 我做:{e['my_actions'][:100]}" if e["my_actions"] else "")
            + (f" 重来:{e['replay'][:80]}" if e["replay"] else "")
            for e in chunk)
        try:
            text = chat([{"role": "system", "content": SYS_COMMON},
                         {"role": "user", "content": CONSOLIDATE_PROMPT.format(digest=digest)}],
                        temperature=0.3, max_tokens=1500)
            data = _parse_json(text)
        except Exception:
            skipped += 1
            continue
        for m in (data.get("memories") or []):
            ev = [int(x) for x in (m.get("evidence") or []) if str(x).isdigit() and int(x) in valid_ids]
            stmt = (m.get("statement") or "").strip()
            if len(ev) < 2 or len(stmt) < 6:
                skipped += 1
                continue
            cur = conn.execute(
                "INSERT INTO memory(statement,kind,source,status,evidence,created_at) "
                "VALUES(?,?,?,'draft',?,?)",
                (stmt, f"巩固·{domain}", "consolidated", json.dumps(ev), db.now()))
            created.append({"id": cur.lastrowid, "statement": stmt, "kind": f"巩固·{domain}",
                            "evidence": ev, "confidence": m.get("confidence")})
        # 这一批事件标记为已巩固（无论是否产出记忆，避免反复烧 token）
        conn.executemany("UPDATE events SET consolidated=1 WHERE id=?",
                         [(e["id"],) for e in chunk])
        covered.extend(e["id"] for e in chunk)
    conn.commit()
    _save_report(conn, "consolidate",
                 {"older_than_days": older_than_days, "covered": len(covered)},
                 {"created": created, "skipped": skipped})
    return {"created": created, "covered_events": len(covered), "skipped": skipped,
            "note": "巩固结果是 draft 状态，不会进入上下文，需要你在风控页逐条确认。"}


# ------------------------------------------------ 0.5 从对话里提炼记忆与画像
CHAT_EXTRACT_SCHEMA = """{"memory":[{
  "statement":"一句关于他的陈述，20-60字，用第二人称，必须是他自己说过的意思，不许拔高",
  "evidence":[消息id整数数组，至少1个，必须真实存在],
  "quote":"他原话里的一个片段，必须逐字出现在被引用的那条消息里"
}],
"traits":[{
  "name":"模式名，6字以内",
  "statement":"你倾向于……（具体到触发条件）",
  "evidence":[消息id整数数组，至少1个],
  "direction":"有益|有害|中性",
  "blind_spot":"在这个模式里他看不见什么",
  "counter":"一个具体到能今天执行的反制动作",
  "confidence":0.0到1.0
}],
"events":[{
  "on_date":"YYYY-MM-DD，他明确说过的日期才填，没说过就留空字符串",
  "domain":"学习|工作|人际|健康|财务|其他",
  "title":"一句事实，15字以内，写发生了什么，不写评价",
  "my_actions":"他说自己做了什么，没有就留空",
  "other_response":"对方怎么反应的，没有就留空",
  "emotion":1到5的整数，他自述的情绪强度，没提就填3,
  "outcome":"good|bad|neutral",
  "evidence":[消息id],
  "quote":"逐字原话片段",
  "why":"为什么这条值得记成事件（一句话）"
}],
"people":[{
  "name":"他提到的人，用他叫这个名字，不要自己起名",
  "relation":"同学|朋友|同事|家人|上级|其他",
  "note":"这个人身上发生的关键事实，一句话",
  "direction":"give|take",
  "evidence":[消息id],
  "quote":"逐字原话片段"
}],
"topics":[{
  "name":"方向名，8字以内",
  "domain":"学习|工作|其他",
  "why":"他为什么在这条方向上投入或犹豫（一句话）",
  "evidence":[消息id],
  "quote":"逐字原话片段"
}],
"decisions":[{
  "title":"在决定什么，15字以内",
  "context":"当时的处境，一句话",
  "options":["出现过的选项"],
  "chosen":"他最终选了哪个；没选就留空",
  "rejected":[{"option":"被否掉的选项","reason":"他说的原因；他没说就留空"}],
  "second_order":["二阶后果，从他的原话里来"],
  "stop_loss":"他设的止损条件，没提就留空",
  "reversibility":"easy|hard|unknown",
  "complete":true或false，见下方判定,
  "evidence":[消息id],
  "quote":"逐字原话片段"
}],
"skipped":"为什么有些内容你判断不该沉淀（一句话，可为空）"}"""

DECISION_COMPLETE_RULE = """
关于 decisions 的 complete 字段：只有当对话里**同时**出现了
「被否掉的选项 + 否掉的理由」和「至少一条二阶后果或止损条件」时，才填 true。
缺任何一项就填 false —— 这套系统要求决策必须写清否决理由和二阶效应，
不完整的记录不能入库，用户会去手工补全。宁可不给，也不要凑。"""


def extract_from_chat(conn, conv_id, limit=40):
    """从一段对话里提炼可以沉淀的长期记忆与行为画像候选。

    为什么需要它：看板、上下文、画像全都只读结构化记录，而用户真正说事情的地方是对话。
    没有这条通路，聊得再多也只是一段聊天记录，沉淀为零。

    写库时一律 draft —— 必须由他自己确认才进入上下文。
    这不是保守：模型从对话里读出的「你是个怎样的人」只是推测，
    直接当事实会污染之后所有判断。
    """
    conv = db.one(conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)))
    if not conv:
        raise RuntimeError("会话不存在")
    upto = int(conv.get("extracted_upto") or 0)

    # 增量：只整理上次之后的新消息。
    # 为什么不能整段重跑——模型每次会把同一件事换个说法（标题、引用的消息编号都会变），
    # 内容比对抓不住，候选会越堆越多。按消息进度切分才是确定的。
    rows = db.rows(conn.execute(
        "SELECT id,role,content FROM messages WHERE conversation_id=? AND ok=1 AND id>? "
        "ORDER BY id DESC LIMIT ?", (conv_id, upto, limit)))
    if not rows:
        # 不是错误，是「没活干」：返回 200 + 一句说明，界面上用中性提示，
        # 不要用红色报错吓人。
        if upto:
            return {"skipped": f"这段对话已经整理到第 {upto} 条消息，后面没有新内容。",
                    "proposals_total": 0, "proposals": {}, "memory": [], "traits": [],
                    "dropped": 0, "scanned_messages": 0}
        return {"skipped": "这段对话还没有内容可整理。", "proposals_total": 0,
                "proposals": {}, "memory": [], "traits": [], "dropped": 0,
                "scanned_messages": 0}
    rows = list(reversed(rows))
    valid_ids = {r["id"] for r in rows}
    by_id = {r["id"]: r["content"] for r in rows}

    lines = []
    for r in rows:
        who = "他" if r["role"] == "user" else "你"
        lines.append(f"[#{r['id']}] {who}：{r['content'][:900]}")
    transcript = "\n".join(lines)

    known, _ = known_block(conn)
    known_section = (f"\n\n=== 已经沉淀过的结论（不要重复提炼）===\n{known}\n") if known else ""

    prompt = f"""下面是一段真实对话，每行开头 [编号] 是消息编号，可作证据引用。

{transcript}
{known_section}
任务：只根据上述对话，提炼值得长期记住的东西。三条硬约束：

1. **只提取他自己说的**。你（镜）说过的话不是证据——你的推测可能正是错的。
2. 每条结论必须能被引用编号支持，且 quote 必须逐字出自那条消息。
   做不到就不写，宁可少给。
3. 只提炼「会长期影响判断」的内容：稳定的偏好、约束、反复出现的行为模式。
   一次性的情绪、临时安排、当下心情，一律不要。

类别限定：
- memory：关于他的稳定事实（身份、约束、资源、长期偏好、明确说过的原则）
- traits：反复出现的行为模式（这段对话里出现过两次，或他有明确自我描述）

输出 JSON：
{CHAT_EXTRACT_SCHEMA}

如果没有值得沉淀的，数组都给空。不要为了交差硬凑。

{DECISION_COMPLETE_RULE}"""

    text = chat([{"role": "system", "content": SYS_COMMON},
                 {"role": "user", "content": prompt}])
    data = _parse_json(text)

    created_m, created_t, dropped = [], [], 0

    def _clean_ev(arr):
        return [int(x) for x in (arr or []) if str(x).isdigit() and int(x) in valid_ids]

    for m in (data.get("memory") or []):
        stmt = (m.get("statement") or "").strip()
        ev = _clean_ev(m.get("evidence"))
        q = (m.get("quote") or "").strip()
        # 引用必须真实出现在被引用的消息里，否则视为编造
        grounded = bool(q) and any(q in by_id.get(i, "") for i in ev)
        if len(stmt) < 6 or not ev or not grounded:
            dropped += 1
            continue
        cur = conn.execute(
            "INSERT INTO memory(statement,kind,source,status,evidence,created_at) "
            "VALUES(?,?,?,'draft',?,?)",
            (stmt, "对话提炼", f"chat:{conv_id}", json.dumps(ev), db.now()))
        created_m.append({"id": cur.lastrowid, "statement": stmt, "evidence": ev, "quote": q})

    for t in (data.get("traits") or []):
        name = (t.get("name") or "").strip()[:12]
        stmt = (t.get("statement") or "").strip()
        ev = _clean_ev(t.get("evidence"))
        if not name or len(stmt) < 6 or not ev:
            dropped += 1
            continue
        cur = conn.execute(
            "INSERT INTO traits(name,direction,statement,evidence,blind_spot,cost,counter,"
            "confidence,status,source,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,'draft',?,?)",
            (name, (t.get("direction") or "中性")[:6], stmt, json.dumps(ev),
             (t.get("blind_spot") or "")[:200], (t.get("cost") or "")[:200],
             (t.get("counter") or "")[:200],
             float(t.get("confidence") or 0.5), f"chat:{conv_id}", db.now()))
        created_t.append({"id": cur.lastrowid, "name": name, "statement": stmt, "evidence": ev})

    # ---- 事件 / 人物 / 方向 / 决策：落进 proposals 暂存区，采纳后才进正式表 ----
    created_p = {"event": [], "person": [], "topic": [], "decision": []}

    def _grounded(item):
        """候选必须能被逐字引用支撑，否则当作模型脑补丢掉。"""
        ev = _clean_ev(item.get("evidence"))
        q = (item.get("quote") or "").strip()
        if not ev or not q:
            return None
        if not any(q in by_id.get(i, "") for i in ev):
            return None
        return ev

    def _prop(kind, payload, ev, note):
        # 去重：同一段对话反复点「整理」，不该把同一条候选堆成十几份。
        #
        # 不能用标题匹配：模型每次会把同一件事换个说法
        # （「约饭一天未回后约成」vs「约饭一天没回后约成」），标题永远对不上。
        # 改用证据来源：同类候选如果引用了同一批消息中的任意一条，即视为重复。
        ev_set = set(ev or [])
        if ev_set:
            for row in conn.execute(
                    "SELECT payload FROM proposals WHERE kind=? AND status IN ('pending','accepted')",
                    (kind,)).fetchall():
                try:
                    old = json.loads(row["payload"] or "{}")
                except (ValueError, TypeError):
                    continue
                if ev_set & set(old.get("_evidence") or []):
                    return None
        payload = dict(payload)
        payload["_evidence"] = list(ev or [])   # 供去重与追溯，入库时不会用到
        cur = conn.execute(
            "INSERT INTO proposals(kind,payload,source,status,note,created_at) "
            "VALUES(?,?,?,'pending',?,?)",
            (kind, json.dumps(payload, ensure_ascii=False), f"chat:{conv_id}",
             (note or "")[:300], db.now()))
        return cur.lastrowid

    for e in (data.get("events") or []):
        title = (e.get("title") or "").strip()
        ev = _grounded(e)
        if len(title) < 4 or not ev:
            dropped += 1
            continue
        payload = {k: e.get(k) for k in
                   ("on_date", "domain", "title", "my_actions", "other_response",
                    "emotion", "outcome") if e.get(k) not in (None, "")}
        payload.setdefault("title", title)
        pid = _prop("event", payload, ev, e.get("why") or "对话里说到的具体发生的事")
        if pid:
            created_p["event"].append({"id": pid, "title": title, "on_date": payload.get("on_date", "")})

    for p_ in (data.get("people") or []):
        name = (p_.get("name") or "").strip()
        ev = _grounded(p_)
        if not name or not ev:
            dropped += 1
            continue
        payload = {"name": name[:20], "relation": p_.get("relation") or "",
                   "note": p_.get("note") or "", "direction": p_.get("direction") or "give"}
        pid = _prop("person", payload, ev, p_.get("note") or "")
        if pid:
            created_p["person"].append({"id": pid, "name": name})

    for t_ in (data.get("topics") or []):
        name = (t_.get("name") or "").strip()
        ev = _grounded(t_)
        if not name or not ev:
            dropped += 1
            continue
        payload = {"name": name[:30], "domain": t_.get("domain") or "", "why": t_.get("why") or ""}
        pid = _prop("topic", payload, ev, t_.get("why") or "")
        if pid:
            created_p["topic"].append({"id": pid, "name": name})

    for d_ in (data.get("decisions") or []):
        title = (d_.get("title") or "").strip()
        ev = _grounded(d_)
        if not title or not ev:
            dropped += 1
            continue
        complete = bool(d_.get("complete")) and bool(d_.get("chosen"))
        payload = {k: d_.get(k) for k in
                   ("title", "context", "options", "chosen", "rejected", "second_order",
                    "stop_loss", "reversibility") if d_.get(k) not in (None, "", [])}
        payload["title"] = title
        payload["_complete"] = complete
        note = "对话里做出的决定" if complete else "⚠ 缺否决理由或二阶效应，采纳时会被拒绝，需手工补全"
        pid = _prop("decision", payload, ev, note)
        if pid:
            created_p["decision"].append({"id": pid, "title": title, "complete": complete})

    conn.execute("UPDATE conversations SET extracted_upto=? WHERE id=?",
                 (max(r["id"] for r in rows), conv_id))
    conn.commit()
    _save_report(conn, "extract-chat", {"conversation_id": conv_id, "messages": len(rows)},
                 {"memory": created_m, "traits": created_t, "proposals": created_p,
                  "dropped": dropped})
    total_p = sum(len(v) for v in created_p.values())
    return {"memory": created_m, "traits": created_t, "proposals": created_p,
            "proposals_total": total_p, "dropped": dropped,
            "scanned_messages": len(rows),
            "note": ("提炼结果都是待确认状态：记忆/画像不会进上下文，"
                     "事件/人物/方向/决策在各自页面顶部等着你采纳。")}


# ---------------------------------------------------------------- 1. 性格归因
def analyze_personality(conn, limit=60):
    digest = _event_digest(conn, limit)
    if not digest:
        raise RuntimeError("没有事件可分析。先在「事件」里记几条真实发生的事。")
    known, known_meta = known_block(conn)
    known_section = (f"\n\n=== 此前已经形成的结论 ===\n{known}\n{KNOWN_RULES}\n") if known else ""
    prompt = f"""以下是客户自己记录的真实事件（编号 #id 为证据编号）：

{digest}
{known_section}
任务：只根据上面这些事件，反推他的行为模式。禁止编造未出现的事件编号。

输出 JSON：
{{"patterns":[{{
  "name":"模式名，6字以内",
  "statement":"你倾向于……（用第二人称，具体到触发条件，不要形容词堆砌）",
  "evidence":[事件id整数数组，至少2个，必须真实存在],
  "direction":"有益|有害|中性",
  "blind_spot":"在这个模式里他看不见什么",
  "cost":"这个模式已经让他付出过什么代价（引用事件里的事实）",
  "counter":"一个具体到能今天执行的反制动作",
  "confidence":0.0到1.0,
  "duplicate_of_id":0
}}],
{UPDATES_SCHEMA},
"meta":{{"sample_size":事件数,"weakest_area":"样本最少、最不该下结论的领域","recommend_more_data":"还需要记录哪类事件才能判断"}}}}

要求：patterns 只给**新出现的**模式，3-6 条；已经在上文列过且证据没变的不要重讲。
{DUP_RULE}
updates_known 可以为空数组，但如果你发现既有结论有变化，必须写。"""
    text = chat([{"role": "system", "content": SYS_COMMON}, {"role": "user", "content": prompt}])
    data = _parse_json(text)
    valid_ids = {r["id"] for r in conn.execute("SELECT id FROM events")}
    kept, dropped = [], 0
    for p in (data.get("patterns") or []):
        ev = [int(x) for x in (p.get("evidence") or []) if str(x).isdigit() and int(x) in valid_ids]
        if len(ev) < 1:
            dropped += 1
            continue
        p["evidence"] = ev
        kept.append(p)

    # updates_known 同样校验证据，编造的一律丢弃
    updates, up_dropped = [], 0
    for u in (data.get("updates_known") or []):
        if not isinstance(u, dict):
            continue
        ev = [int(x) for x in (u.get("evidence") or []) if str(x).isdigit() and int(x) in valid_ids]
        if not ev:
            up_dropped += 1
            continue
        u["evidence"] = ev
        updates.append(u)

    # duplicate_of_id 必须是真实存在的已采纳结论，否则归零
    acc_ids = set(known_meta["accepted_trait_ids"])
    for p in kept:
        try:
            dup = int(p.get("duplicate_of_id") or 0)
        except (TypeError, ValueError):
            dup = 0
        p["duplicate_of_id"] = dup if dup in acc_ids else 0

    _save_report(conn, "personality",
                 {"limit": limit, "injected": known_meta},
                 {"patterns": kept, "dropped": dropped, "updates": updates,
                  "updates_dropped": up_dropped, "meta": data.get("meta")})
    note = ""
    if dropped or up_dropped:
        note = f"{dropped + up_dropped} 条结论因引用的事件编号在库里不存在被丢弃（防 AI 编证据）。"
    return {"patterns": kept, "dropped_unverifiable": dropped, "meta": data.get("meta"),
            "updates": updates, "note": note,
            "injected": {"traits": len(known_meta["accepted_trait_ids"]),
                         "rejected": len(known_meta["rejected_trait_ids"]),
                         "memory": len(known_meta["memory_ids"])}}


# ---------------------------------------------------------------- 2. 方向诊断
def analyze_direction(conn):
    from . import engine
    tm = engine.topic_metrics(conn)
    ts = tm["topics"]
    if not ts:
        raise RuntimeError("还没有任何学习方向记录。先在「方向」里建主题。")
    lines = []
    for t in ts:
        lines.append(
            f"#{t['id']} {t['name']} | 深度{t['depth_level']}/5({t['depth_label']}) 投入{t['hours']:g}h "
            f"作品{t['artifacts']} 兴趣{t['interest']}/5 衰减{t['decay']} 持续性{t['persistence']} "
            f"距上次{t['idle_days']}天 状态{t['status']} 主线={bool(t['mainline'])} 为什么开始:{t['why'][:80]}")
    known, known_meta = known_block(conn)
    known_section = (f"\n=== 此前已经形成的结论 ===\n{known}\n{KNOWN_RULES}\n") if known else ""
    # 检索：方向分析原来只看得到数字，看不到「学这些东西时实际发生了什么」
    recall_txt, recall_ids = recall_events(conn, " ".join(t["name"] for t in ts), limit=10)
    recall_section = (f"\n=== 相关历史事件（按相关度召回） ===\n{recall_txt}\n"
                      if recall_txt else "")
    prompt = f"""客户的原话：「我很喜欢学习各种东西，但是浅而不精。我需要找到我最喜欢而且可以深入持续学习的方向。」

他的方向记录（指标已算好）：
{chr(10).join(lines)}
{recall_section}{known_section}
指标含义：decay=投入退潮(1表示近30天投入速度远低于历史月均)，persistence=跨月持续性，depth_level 0接触/1能聊/2能讲清/3能产出/4专业/5能谋生。

输出 JSON：
{{"diagnosis":"一段 150 字以内的总体判断：他到底是在追求掌握，还是在追求『刚开始学』的那个感觉？给出你判断的依据。",
"candidates":[{{
  "topic_id":整数,"topic":"名称",
  "sustainability":0到1的可持续性评分,
  "go_deep":"值得深挖|不值得|先做小实验",
  "why_you_stop":"如果你在这个主题上停手，真实原因是什么（结合 decay/投入/情绪数据推断，不要套模板）",
  "proof_needed":"什么信号出现才说明你是真的喜欢它，而不是喜欢新鲜感",
  "next_90_days":"90 天具体动作，3 条，每条要可验证（有产出物或数字）"
}}],
"pick":"只选一个最该深挖的 topic 名称",
"pick_reason":"为什么是它而不是别的",
"park":"建议封存的 topic 名称数组",
"one_rule":"给他一条防止再次浅尝的硬规则（带阈值和后果，例如：同时最多X个主题…）"}}

要求：candidates 覆盖所有主题；pick 必须只有一个；不要平均主义地夸每个主题。"""
    text = chat([{"role": "system", "content": SYS_COMMON},
                 {"role": "user", "content": prompt}], max_tokens=4000)
    data = _parse_json(text)
    valid = {t["id"] for t in ts}
    for c in (data.get("candidates") or []):
        if c.get("topic_id") not in valid:
            c["topic_id"] = None
    _save_report(conn, "direction",
                 {"topics": len(ts), "injected": known_meta, "recalled_event_ids": recall_ids}, data)
    return data


# ---------------------------------------------------------------- 3. 决策压力测试
def stress_test(conn, decision_id):
    d = db.one(conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)))
    if not d:
        raise RuntimeError("决策不存在")
    criteria = db.jloads(d["criteria"], [])
    options = db.jloads(d["options"], [])
    second = db.jloads(d["second_order"], [])
    rejected = db.jloads(d["rejected"], [])
    opt_txt = "\n".join(
        f"- {o.get('name')} 得分{json.dumps(o.get('scores') or {}, ensure_ascii=False)} 备注:{o.get('note','')}"
        for o in options)
    crit_txt = "\n".join(f"- {c.get('name')} 权重{c.get('weight')}" for c in criteria)
    rej_txt = "\n".join(f"- {r.get('option')}：{r.get('reason')}" for r in rejected) or "（无）"
    lessons, lesson_ids = _past_lessons(conn, d["kind"], decision_id)
    known, known_meta = known_block(conn)
    # 检索：这件决策在过去有没有类似的情境？（人际/金钱/职业都可能相关）
    q = " ".join([d["title"] or "", d["kind"] or "", (d["context"] or "")[:80]]
                 + [str(o.get("name") or "") for o in options])
    recall_txt, recall_ids = recall_events(conn, q, limit=8)
    past_section = ""
    if recall_txt:
        past_section += f"\n=== 与此决策相关的历史事件 ===\n{recall_txt}\n"
    if lessons:
        past_section += f"\n=== 他过去的决策复盘 ===\n{lessons}\n"
    if known:
        past_section += f"\n=== 他已被确认的行为模式 ===\n{known}\n"
    if past_section:
        past_section += (
            "\n如果本次决策正在重犯上面某条历史错误，必须在 repeating_mistakes 里点名指出"
            "（引用决策编号、结论编号或事件编号），不要客气。\n")
    prompt = f"""决策：{d['title']}
类型：{d['kind']}　可逆性：{d['reversibility']}
背景：{d['context']}
准则与权重：
{crit_txt or '（无）'}
备选与我的打分：
{opt_txt or '（无）'}
已否决的分支与理由：
{rej_txt}
我写的二阶效应：{json.dumps(second, ensure_ascii=False)[:800]}
我写的止损条件：{d['stop_loss'] or '（空）'}
{past_section}
任务：不做和事佬。从这个决策里找出最脆弱的假设，用四种立场轮番攻击它，然后预演失败。

输出 JSON：
{{"hidden_assumptions":[{{"assumption":"我隐含假设了什么","if_false":"如果它不成立，后果是什么","test":"怎么用一周内能做完的动作验证它"}}],
"personas":[
 {{"role":"风险厌恶者","strongest_objection":"……","what_i_would_do":"……"}},
 {{"role":"增长乐观者","strongest_objection":"……","what_i_would_do":"……"}},
 {{"role":"你的对手/竞争者","strongest_objection":"……","what_i_would_do":"……"}},
 {{"role":"三年后的你","strongest_objection":"……","what_i_would_do":"……"}}],
"second_order":[{{"layer":1,"effect":"直接结果"}},{{"layer":2,"effect":"然后呢"}},{{"layer":3,"effect":"再然后呢"}}],
"repeating_mistakes":[{{"past_id":历史决策或结论编号,"pattern":"正在重犯的模式","evidence":"本次决策里的哪句话或哪个打分"}}],
"premortem":"假设 18 个月后这件事彻底失败，最可能的失败路径（写成一个连贯的故事，不要罗列）",
"kill_signals":["出现就必须立刻退出的可观察信号，3条，要具体到数字或行为"],
"verdict":"支持执行|需要补信息|应当否决",
"one_question":"在做最终决定前，你必须先回答的那一个问题"}}

要求：personas 四条的立场必须真的互相冲突，不要四条都在说同一件事。
repeating_mistakes 如果确实没有，给空数组——不要为了凑而编。"""
    text = chat([{"role": "system", "content": SYS_COMMON},
                 {"role": "user", "content": prompt}], temperature=0.6, max_tokens=4000)
    data = _parse_json(text)
    _save_report(conn, "stress_test",
                 {"decision_id": decision_id, "injected": known_meta,
                  "past_decision_ids": lesson_ids, "recalled_event_ids": recall_ids}, data)
    return data


# ---------------------------------------------------------------- 4. 人际边界评审
def review_person(conn, person_id):
    p = db.one(conn.execute("SELECT * FROM people WHERE id=?", (person_id,)))
    if not p:
        raise RuntimeError("对象不存在")
    ints = db.rows(conn.execute(
        "SELECT on_date,direction,weight,what FROM interactions WHERE person_id=? ORDER BY on_date", (person_id,)))
    g = sum(i["weight"] for i in ints if i["direction"] == "give")
    t = sum(i["weight"] for i in ints if i["direction"] == "take")
    evs = db.rows(conn.execute(
        "SELECT id,on_date,title,my_actions,other_response,emotion FROM events WHERE person_id=? "
        "ORDER BY on_date DESC LIMIT 30", (person_id,)))
    rel = "\n".join(f"{i['on_date']} {'我付出' if i['direction']=='give' else ('我得到' if i['direction']=='take' else '中性')}"
                    f"×{i['weight']:g} {i['what'][:100]}" for i in ints) or "（无流水）"
    ev = "\n".join(f"#{e['id']} {e['on_date']} {e['title']} 我做:{e['my_actions'][:80]} "
                   f"反应:{e['other_response'][:80]}" for e in evs) or "（无）"
    known, known_meta = known_block(conn)
    known_section = (f"\n=== 他已被确认的行为模式（判断边界问题时必须参考）===\n{known}\n{KNOWN_RULES}\n"
                     if known else "")
    # 检索：这个人的名字/关系里出现过的历史事件（不限于 person_id 已关联的那些）
    recall_txt, recall_ids = recall_events(
        conn, " ".join([p["name"] or "", p["relation"] or "", p["notes"] or ""]),
        limit=8, exclude_ids=[e["id"] for e in evs])
    recall_section = (f"\n=== 提到他、但未归档到他名下的历史事件 ===\n{recall_txt}\n"
                      if recall_txt else "")
    prompt = f"""对象：{p['name']}（{p['relation']}，当前状态 {p['status']}，最近联系 {p['last_contact_on']}）
我付出合计 {g:g}，我得到合计 {t:g}。
互动流水：
{rel}
相关事件：
{ev}
{recall_section}{known_section}
任务：判断这段关系的结构是否对等，以及我是否在扮演血包。不要预设对方是坏人，也不要预设我没错。
输出 JSON：
{{"structure":"对等|我方净输出|对方净输出|不清晰",
"net_balance":{g-t} ,
"evidence":["引用上面的日期或事件编号"],
"risk_level":"低|中|高",
"diagnosis":"这段关系实际在换取什么：情绪价值/资源/身份/惯性？直说。",
"suspension":"对方是否存在『悬置』行为（享受我推进但不给确定答复）？有/无/证据不足，并说明",
"pattern_link":"这段关系是否印证了上面某条已确认的行为模式？引用编号，没有就写无",
"boundary_line":"一条可执行的红线，写成 if-then 形式",
"script":"一句我可以照着说的原话，不带指责、不留解释空间、也不讨好",
"keep_or_cut":"维持|降级|切断"}}"""
    text = chat([{"role": "system", "content": SYS_COMMON},
                 {"role": "user", "content": prompt}], max_tokens=1500)
    data = _parse_json(text)
    _save_report(conn, "person_review",
                 {"person_id": person_id, "injected": known_meta, "recalled_event_ids": recall_ids}, data)
    return data


def ping():
    """轻量连通性自检。只要 HTTP 通了就算通，解析失败不算失败。"""
    txt = chat([{"role": "user", "content": '只回一个 JSON：{"ok":true}'}],
               max_tokens=64, temperature=0)
    try:
        parsed = _parse_json(txt)
    except (ValueError, AttributeError):
        parsed = {"raw": (txt or "")[:80]}
    return {"ok": True, "reply": parsed}


# ------------------------------------------------ 0.6 方向草稿（不依赖方向记录）
def suggest_direction(conn):
    """基于已积累的事件、已确认的记忆/画像、以及对话中的候选，给出「大致最优方向」。

    和 analyze_direction 的区别：那个要求先在「方向」里建主题记录；
    这个只用对话和事件就能出草稿，适合「聊了很久但一条方向都没建」的状态。
    """
    from . import organize
    ev_digest = _event_digest(conn, 40)
    mem, _ = known_block(conn)
    pend = organize.pending(conn)
    topic_hints = [p["payload"] for p in pend if p["kind"] == "topic"]
    ev_hints = [p["payload"] for p in pend if p["kind"] == "event"]

    if not ev_digest and not topic_hints and not ev_hints:
        raise RuntimeError("积累的材料还不够给方向判断：先聊几轮、或整理一次对话。")

    hint_lines = []
    for h in topic_hints[:6]:
        hint_lines.append(f"- 候选方向：{h.get('name')}（他的说法：{(h.get('why') or '')[:80]}）")
    for h in ev_hints[:6]:
        hint_lines.append(f"- 他最近的事：{h.get('title')}（{(h.get('domain') or '')}）")

    prompt = f"""客户的原始诉求：「我很喜欢学习各种东西，但是浅而不精。
我需要找到我最喜欢而且可以深入持续学习的方向。」

=== 他记录过的事件 ===
{ev_digest or '（暂无）'}

=== 已确认的长期事实与画像 ===
{mem or '（暂无）'}

=== 尚未确认的候选（来自对话，仅供参考，不能当成事实）===
{chr(10).join(hint_lines) if hint_lines else '（暂无）'}

任务：给出「大致的最优方向」。三条硬约束：
1. 只能基于上面材料。材料不足就直说不足，不要编一个听起来合理的方向。
2. 每个方向的依据要说清是哪条事件/记忆支撑的；没有支撑的不要列。
3. 这是一个草稿，不是判决。置信度要诚实。

输出 JSON：
{{"diagnosis":"120字以内：他现在实际在追求什么（掌握，还是「刚开始学」的感觉），依据是什么",
"directions":[{{"name":"方向名，8字以内","why":"为什么是它，引用具体事件或记忆",
  "sustainability":0到1,"evidence":"支撑它的事实（不行就写「材料不足」）",
  "risk":"这条方向最容易在哪一步放弃"}}],
"pick":"只选一个最该先押的方向名",
"why_pick":"为什么是它而不是别的，80字以内",
"confidence":0到1,
"missing":"还缺什么材料才能更有把握（一句话）",
"next_30_days":["30天内可验证的动作，2-3条，要有产出物或数字"]}}"""

    text = chat([{"role": "system", "content": SYS_COMMON},
                 {"role": "user", "content": prompt}])
    data = _parse_json(text)
    data["_generated_at"] = db.now()
    _save_report(conn, "direction-draft", {"events": bool(ev_digest)}, data)
    return data