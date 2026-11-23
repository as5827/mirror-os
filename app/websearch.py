# -*- coding: utf-8 -*-
"""联网检索：给「理论支撑」找真实文献和被引次数。

只用无需 Key 的学术 API（实测可达性见下）：
  ✅ OpenAlex    api.openalex.org   含 cited_by_count，主源
  ✅ Crossref    api.crossref.org  元数据，兜底
  ✅ arXiv       export.arxiv.org  预印本
  ❌ Semantic Scholar  429 限流，不用
  ❌ Wikipedia / DuckDuckGo  本机连不通，不用

设计原则：
1. **缓存优先**：结果落 reference 表，同样的查询不重复打网络；断网后仍可查可引。
2. **可引用**：每条文献有本地编号，回复里用 [文献#12] 引用，编号同样被校验。
3. **不夸大**：被引次数只是「有多少关注」的粗代理，不是「结论正确」的证明。
   系统提示里明确要求：文献只支持机制层面，不能推出「你有这个问题」。
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from . import db

OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"
ARXIV = "https://export.arxiv.org/api/query"
TIMEOUT = 20
UA = "MirrorOS/1.0 (personal local decision journal)"

# 实测 OpenAlex 会 429。所以自己做节流 + 冷却，别把免费额度打爆。
MIN_INTERVAL = 1.2
_last_call = 0.0
_cooldown = {}
_probe_cache = {"ts": 0.0, "data": None}
CACHE_TTL = 90


def _throttle(name):
    import time
    global _last_call
    now = time.time()
    until = _cooldown.get(name, 0)
    if until > now:
        raise RuntimeError(f"{name} 限流冷却中，还有 {int(until - now)} 秒")
    wait = MIN_INTERVAL - (now - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _cooldown_hit(name, secs=25):
    import time
    _cooldown[name] = time.time() + secs


_pending = {}   # 简易缓存：query -> 结果，避免同一进程内重复请求


def _opener():
    """和 ai 模块同一套代理策略：默认直连绕过系统代理，需要时用 MIRROR_PROXY。"""
    proxy = os.environ.get("MIRROR_PROXY", "").strip()
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, text/xml;q=0.9,*/*;q=0.8"})
    with _opener().open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _abstract_from_inverted(inv):
    """OpenAlex 用倒排索引存摘要，这里还原成正常语序。"""
    if not inv or not isinstance(inv, dict):
        return ""
    pos = []
    for word, idxs in inv.items():
        if isinstance(idxs, list):
            for i in idxs:
                pos.append((i, word))
    pos.sort()
    return " ".join(w for _, w in pos)[:1200]


def _relevance_filter(items, query, limit):
    """OpenAlex 的 search 排序会掺进无关高被引论文：查认知失调混进 PLS-SEM；
    查 graduate school decision 混进《Dynamic capabilities》——后者摘要里正好有 decision 一词。

    所以多词查询要求**至少命中 2 个词**（单词查询命中 1 个即可）。
    一个都不满足就返回空，让调用方退到下一个源，而不是把噪声当证据。
    """
    toks = [t.lower() for t in re.split(r"[^A-Za-z\-]+", query) if len(t) >= 4]
    if not toks:
        return items[:limit]
    need = min(2, len(toks))

    def hits_of(it):
        hay = (it.get("title", "") + " " + (it.get("abstract") or "")).lower()
        return sum(1 for t in toks if t in hay)

    strict = [x for x in items if hits_of(x) >= need]
    strict.sort(key=lambda x: -hits_of(x))
    return strict[:limit]


def crossref_zh(query, limit=5):
    """中文文献兜底源。

    实测 Crossref 能返回中文标题（很多中文期刊在 Crossref 注册了 DOI），
    但相关性和影响力都差：查「拖延」会返回「疝气拖延不治」这类。
    所以只在 OpenAlex 中文源被限流时兜底，且必须过「标题含查询词」的硬过滤。
    被引普遍为 0 时，block() 会自动附加「弱支撑」警告。
    """
    _throttle("crossref")
    terms = _zh_terms(query)
    if not terms:
        return []
    url = CROSSREF + "?query=" + urllib.parse.quote(query) + "&rows=20"
    data = json.loads(_get(url))
    out = []
    for it in ((data.get("message") or {}).get("items") or []):
        title = (it.get("title") or [""])
        title = title[0] if isinstance(title, list) and title else ""
        title = title.strip()
        if not title or not any(t in title for t in terms):
            continue
        if not any("\u4e00" <= ch <= "\u9fff" for ch in title):
            continue
        yr = None
        for k in ("published-print", "published-online", "issued"):
            parts = (it.get(k) or {}).get("date-parts") or []
            if parts and parts[0] and parts[0][0]:
                yr = parts[0][0]
                break
        auths = [f"{a.get('given','')} {a.get('family','')}".strip()
                 for a in (it.get("author") or [])[:3]]
        out.append({
            "doi": it.get("DOI") or "",
            "title": title,
            "year": yr,
            "cited_by": int(it.get("is-referenced-by-count") or 0),
            "venue": (it.get("container-title") or [""])[0] if it.get("container-title") else "",
            "authors": "、".join([a for a in auths if a]),
            "abstract": re.sub(r"<[^>]+>", "", it.get("abstract") or "")[:1200],
            "url": ("https://doi.org/" + it.get("DOI", "")) if it.get("DOI") else "",
            "source": "crossref-zh",
            "lang": "zh",
        })
    out.sort(key=lambda x: -(x.get("cited_by") or 0))
    return out[:limit]


def _zh_terms(q):
    """中文没有空格，按标点和空格切，再过滤掉过短的。"""
    parts = [t.strip() for t in re.split(r"[\s,，。、；;：:()（）「」\"'\-]+", q or "")]
    return [t for t in parts if len(t) >= 2]


def _rel_zh(items, query, limit=5):
    """中文相关性过滤（严格）。

    OpenAlex 的中文全文检索按字切词，噪声很大（实测查「拒绝 同事」返回口吃研究、
    查「放弃」返回绿色发展）。这里要求标题或摘要里真的出现查询词；
    一个都不命中就返回空——宁可没有文献，也不要拿噪声冒充理论支撑。
    """
    terms = _zh_terms(query)
    if not terms:
        return items[:limit]
    scored = []
    for it in items:
        title = it.get("title") or ""
        hay = title + " " + (it.get("abstract") or "")
        in_title = sum(1 for t in terms if t in title)
        in_hay = sum(1 for t in terms if t in hay)
        if in_hay == 0:
            continue
        scored.append((in_title * 3 + in_hay, it))
    scored.sort(key=lambda x: -x[0])
    return [it for _, it in scored][:limit]


def openalex_zh(query, limit=5):
    """只取中文文献。

    这才是中文检索的正确入口：OpenAlex 的 search= 传中文几乎全是英文噪声
    （实测查「拖延」返回 DeepSeek-VL2），而 filter=language:zh +
    title_and_abstract.search=中文 能精准命中中文学术文献。
    """
    _throttle("openalex")
    if len(query) < 2:
        return []
    url = (OPENALEX + "?filter=language:zh,title_and_abstract.search:" +
           urllib.parse.quote(query) + f"&per_page={max(limit * 3, 9)}"
           "&sort=cited_by_count:desc")
    mailto = os.environ.get("MIRROR_MAILTO", "").strip()
    if mailto:
        url += "&mailto=" + urllib.parse.quote(mailto)
    # 冷却是调用方的责任（见 search）：数据源内部设冷却会挡住调用方的重试。
    data = json.loads(_get(url))
    out = []
    for w in (data.get("results") or []):
        authors = [a.get("author", {}).get("display_name", "")
                   for a in (w.get("authorships") or [])[:3]]
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        out.append({
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "title": (w.get("title") or w.get("display_name") or "").strip(),
            "year": w.get("publication_year"),
            "cited_by": int(w.get("cited_by_count") or 0),
            "venue": src.get("display_name") or "",
            "authors": "、".join([a for a in authors if a]),
            "abstract": _abstract_from_inverted(w.get("abstract_inverted_index")),
            "url": w.get("doi") or w.get("id") or "",
            "source": "openalex-zh",
            "lang": "zh",
        })
    return _rel_zh(out, query, limit)


def openalex(query, limit=5):
    _throttle("openalex")
    url = (OPENALEX + "?search=" + urllib.parse.quote(query) +
           f"&per_page={max(limit * 3, 8)}&sort=cited_by_count:desc")
    # OpenAlex 对匿名请求限流很严（实测频繁 429）。填了邮箱会进「礼貌池」，配额更宽。
    mailto = os.environ.get("MIRROR_MAILTO", "").strip()
    if mailto:
        url += "&mailto=" + urllib.parse.quote(mailto)
    # 冷却是调用方的责任（见 search）：数据源内部设冷却会挡住调用方的重试。
    data = json.loads(_get(url))
    out = []
    for w in (data.get("results") or []):
        authors = [a.get("author", {}).get("display_name", "")
                   for a in (w.get("authorships") or [])[:3]]
        loc = w.get("primary_location") or {}
        src = loc.get("source") or {}
        out.append({
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "title": (w.get("title") or w.get("display_name") or "").strip(),
            "year": w.get("publication_year"),
            "cited_by": int(w.get("cited_by_count") or 0),
            "venue": src.get("display_name") or "",
            "authors": "、".join([a for a in authors if a]),
            "abstract": _abstract_from_inverted(w.get("abstract_inverted_index")),
            "url": w.get("doi") or w.get("id") or "",
            "source": "openalex",
        })
    return _relevance_filter(out, query, limit)


def crossref(query, limit=5):
    _throttle("crossref")
    url = CROSSREF + "?query=" + urllib.parse.quote(query) + f"&rows={max(limit * 2, 8)}"
    # 冷却是调用方的责任（见 search）：数据源内部设冷却会挡住调用方的重试。
    data = json.loads(_get(url))

    out = []
    for it in ((data.get("message") or {}).get("items") or []):
        title = (it.get("title") or [""])
        title = title[0] if isinstance(title, list) and title else ""
        yr = None
        for k in ("published-print", "published-online", "issued"):
            parts = (it.get(k) or {}).get("date-parts") or []
            if parts and parts[0] and parts[0][0]:
                yr = parts[0][0]
                break
        auths = [f"{a.get('given','')} {a.get('family','')}".strip()
                 for a in (it.get("author") or [])[:3]]
        out.append({
            "doi": it.get("DOI") or "",
            "title": title.strip(),
            "year": yr,
            "cited_by": int(it.get("is-referenced-by-count") or 0),
            "venue": (it.get("container-title") or [""])[0] if it.get("container-title") else "",
            "authors": "、".join([a for a in auths if a]),
            "abstract": re.sub(r"<[^>]+>", "", it.get("abstract") or "")[:1200],
            "url": ("https://doi.org/" + it.get("DOI", "")) if it.get("DOI") else "",
            "source": "crossref",
        })
    # Crossref 的相关性排序偏弱（会返回冷门章节），按被引次数重排再用
    out.sort(key=lambda x: -(x.get("cited_by") or 0))
    return out


def arxiv(query, limit=5):
    """arXiv 是预印本，未同行评审——必须标注，不能和期刊文献混为一谈。"""
    url = ARXIV + "?search_query=all:" + urllib.parse.quote(query) + f"&max_results={limit}"
    xml = _get(url)
    out = []
    for m in re.finditer(r"<entry>(.*?)</entry>", xml, re.S):
        blk = m.group(1)

        def grab(tag):
            mm = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", blk, re.S)
            return re.sub(r"\s+", " ", mm.group(1)).strip() if mm else ""

        title = grab("title")
        if not title:
            continue
        pub = grab("published")[:4]
        out.append({
            "doi": "", "title": title, "year": int(pub) if pub.isdigit() else None,
            "cited_by": 0, "venue": "arXiv（预印本，未经同行评审）",
            "authors": "", "abstract": grab("summary")[:1200],
            "url": grab("id"), "source": "arxiv",
        })
    return out


def _save(conn, items, query, theory_id=None):
    saved = []
    for it in items:
        if not it.get("title"):
            continue
        doi = it.get("doi") or ""
        row = None
        if doi:
            row = db.one(conn.execute("SELECT id FROM reference WHERE doi=?", (doi,)))
        if row:
            conn.execute("UPDATE reference SET cited_by=?, abstract=COALESCE(NULLIF(?, ''), abstract), "
                         "theory_id=COALESCE(?, theory_id) WHERE id=?",
                         (it.get("cited_by") or 0, it.get("abstract") or "", theory_id, row["id"]))
            saved.append(row["id"])
            continue
        cur = conn.execute(
            "INSERT INTO reference(doi,title,year,cited_by,venue,authors,abstract,url,source,query,"
            "theory_id,lang,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doi, it["title"][:400], it.get("year"), it.get("cited_by") or 0,
             (it.get("venue") or "")[:200], (it.get("authors") or "")[:200],
             (it.get("abstract") or "")[:2000], (it.get("url") or "")[:400],
             it.get("source") or "", query[:200], theory_id, it.get("lang") or "", db.now()))
        saved.append(cur.lastrowid)
    conn.commit()
    return saved


def query_for(conn, text, limit=2):
    """从中文问题里生成「中文检索词」和「英文检索词」。

    英文检索词有两条来源，缺一不可：
      1. 命中的理论条目的英文名（`en`）——能拿到该领域的经典高被引论文；
      2. **中英关键词对照表**（lexicon）——把问题里的「睡眠」「辞职」「存钱」直接翻成
         英文学术检索词。这条是必需的：实测 5 个问题里有 3 个不命中任何理论，
         没有它，中文提问就完全查不到英文文献。
    中文检索词取对照表里命中的那个词（长词优先），或退到理论标签。
    """
    from . import knowledge, lexicon

    lex = lexicon.extract(text, limit=4)
    hits = knowledge.match(conn, text, limit=limit)
    en_theory = [(h.get("en") or "").strip() for h in hits]
    en_theory = [t for t in en_theory if len(t) >= 4]

    # 理论标签集合：用来判断哪个中文词才是「核心概念」
    theory_tags = set()
    for h in hits:
        theory_tags.update(t for t in (h.get("tags") or "").split() if len(t) >= 2)

    # 中文词：优先与理论标签对齐的那个，其次按在句中出现的位置（越靠前越核心）
    zh_q = ""
    if lex:
        def score(item, idx):
            zh = item[0]
            pos = (text or "").find(zh)
            return (
                (2 if zh in theory_tags else 0)      # 与理论概念一致
                + (1 if pos >= 0 and pos < 12 else 0)  # 靠前出现
                - idx * 0.1                            # 同分时保持对照表顺序
            )
        zh_q = max(((it, i) for i, it in enumerate(lex)), key=lambda p: score(p[0], p[1]))[0][0]
    if not zh_q:
        q_raw = (text or "").replace(" ", "")
        for h in hits:
            tags = [t for t in (h.get("tags") or "").split() if len(t) >= 2]
            hit = [t for t in tags if t in q_raw]
            if hit:
                zh_q = hit[0]
                break
        if not zh_q:
            for h in hits:
                tags = sorted([t for t in (h.get("tags") or "").split() if len(t) >= 2], key=len)
                if tags:
                    zh_q = tags[0]
                    break

    # 英文词：**以你问题里的关键词为准**，理论英文名只作兜底。
    # 顺序很关键：原来优先用理论英文名，导致问「睡眠」却去搜 Loss Aversion，
    # 检出的文献与问题不匹配（实测 AI 自己都拒用了）。你的原话才是检索依据。
    # 最多 2 个概念，且总词数不超过 5：OpenAlex 多词是 AND
    # （sleep 2.57M → sleep deprivation 235K），词太多会直接收窄到 0。
    en_terms = []
    for _, en in lex[:2]:
        if en not in en_terms:
            en_terms.append(en)
    if len(en_terms) > 1:
        words = len(re.findall(r"[A-Za-z\-]{3,}", " ".join(en_terms)))
        if words > 5:
            en_terms = en_terms[:1]
    if not en_terms and en_theory:
        en_terms = en_theory[:1]
    if not en_terms:
        ascii_words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", text or "")
        if len(ascii_words) >= 2:
            en_terms = [" ".join(ascii_words[:5])]

    en_q = " ".join(dict.fromkeys(en_terms))[:140]
    return zh_q, en_q, [h["id"] for h in hits]


def search(conn, query, limit=4, use_cache_hours=72, theory_id=None,
           fetchers=None, lang="en"):
    """检索文献。命中缓存优先；源依次交给 fetchers。返回 (reference 行, 来源说明)。"""
    q = (query or "").strip()
    if len(q) < 2:
        return [], "查询太短"
    fetchers = fetchers or (openalex, crossref)

    if use_cache_hours:
        since = (date.today() - timedelta(hours=use_cache_hours)).isoformat()
        cached = db.rows(conn.execute(
            "SELECT * FROM reference WHERE query=? AND fetched_at>=? "
            "ORDER BY cited_by DESC LIMIT ?", (q[:200], since, limit * 3)))
        cached = _relevance_filter(cached, q, limit)
        # 自愈：全都不是有影响力的文献，说明当初是弱源兜底的，别短路，重新联网
        if len(cached) >= min(2, limit) and max((r["cited_by"] or 0) for r in cached) >= 3:
            return cached, "cache"

    key = (q.lower(), limit, lang)
    if key in _pending:
        items, note = _pending[key], "memory"
    else:
        items, errs, note = [], [], ""
        for fn in fetchers:
            for attempt in (1, 2):
                try:
                    got = fn(q, limit)
                    if got:
                        items, note = got, fn.__name__
                        break
                    errs.append(f"{fn.__name__}: 无结果")
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt == 1:
                        import time
                        time.sleep(2.5)
                        continue
                    errs.append(f"{fn.__name__}: HTTP {e.code}")
                    break
                except Exception as e:
                    errs.append(f"{fn.__name__}: {type(e).__name__}")
                    break
            if items:
                break
        if not items:
            return [], "；".join(errs) or "检索失败"
        for it in items:
            it.setdefault("lang", lang)
        _pending[key] = items

    ids = _save(conn, items, q, theory_id)
    rows = []
    if ids:
        ph = ",".join("?" * len(ids))
        by_id = {r["id"]: r for r in db.rows(
            conn.execute(f"SELECT * FROM reference WHERE id IN ({ph})", ids))}
        rows = [by_id[i] for i in ids if i in by_id]
    rows.sort(key=lambda r: -(r["cited_by"] or 0))
    return rows[:limit], note


def search_multi(conn, zh_query, en_query, lang="both", limit=4, theory_id=None):
    """中英双语检索。返回 (合并后的文献, 说明, 是否弱支撑)。

    lang: zh 只查中文 / en 只查英文 / both 两边都查

    both 模式下**按名额分配**，不是「谁先来谁占满」：
    中文源往往能一次返回 4 条，如果直接拼接，英文文献永远进不来——
    而「中文提问也要看到相关英文文献」正是要解决的问题。
    所以各占一半，一侧不足时用另一侧补满。
    """
    zh_rows, en_rows, notes = [], [], []
    if lang in ("zh", "both") and zh_query:
        r, n = search(conn, zh_query, limit=limit, theory_id=theory_id,
                      fetchers=(openalex_zh, crossref_zh), lang="zh")
        zh_rows = r
        if r:
            notes.append("中" + n)
    if lang in ("en", "both") and en_query:
        r, n = search(conn, en_query, limit=limit, theory_id=theory_id,
                      fetchers=(openalex, crossref), lang="en")
        en_rows = r
        if r:
            notes.append("英" + n)

    if lang == "both":
        quota = max(1, limit // 2)
        picked = zh_rows[:quota] + en_rows[:limit - quota]
        if len(picked) < limit:
            rest = zh_rows[quota:] + en_rows[limit - quota:]
            for r in rest:
                if len(picked) >= limit:
                    break
                picked.append(r)
    else:
        picked = zh_rows + en_rows

    if not picked:
        return [], "；".join(notes) or "没有检索到文献", True

    # 中文排前（便于阅读），同语种内按被引降序；按 DOI/标题去重
    seen, merged = set(), []
    for r in sorted(picked, key=lambda x: (0 if (x.get("lang") == "zh") else 1,
                                           -(x.get("cited_by") or 0))):
        k = (r.get("doi") or "").strip().lower() or (r.get("title") or "").strip().lower()
        if k and k in seen:
            continue
        seen.add(k)
        merged.append(r)
    merged = merged[:limit]
    weak = max((r.get("cited_by") or 0) for r in merged) < 10
    return merged, "；".join(notes), weak


def block(rows):
    """把文献编成可注入的上下文块。"""
    if not rows:
        return "", []
    out = ["【联网检索到的文献（同行评审优先；引用时必须写 [文献#编号]）】"]
    for r in rows:
        head = f"[文献#{r['id']}] {r['title']}"
        meta = []
        if r["year"]:
            meta.append(str(r["year"]))
        if r["authors"]:
            meta.append(r["authors"])
        if r["venue"]:
            meta.append(r["venue"])
        if r["cited_by"]:
            meta.append(f"被引 {r['cited_by']} 次")
        tag = "中文文献" if (r.get("lang") == "zh") else ("英文文献" if r.get("lang") == "en" else "")
        if tag == "中文文献":
            # 不少中文期刊被 OpenAlex 存成英文标题（如《心理科学》），标题是英文但正文是中文
            title = r.get("title") or ""
            ascii_ratio = sum(1 for ch in title if ord(ch) < 128) / max(len(title), 1)
            if ascii_ratio > 0.7:
                tag = "中文期刊（OpenAlex 存的是英文标题）"
        if tag:
            meta.append(tag)
        out.append(head + ("\n  " + " · ".join(meta) if meta else ""))
        if r["abstract"]:
            out.append("  摘要：" + r["abstract"][:400])
        if r["doi"]:
            out.append("  DOI：" + r["doi"])
    out.append("注意：被引次数只说明关注度，不证明结论成立；"
               "文献支持的是「机制层面存在这种可能」，推不出「你就是这样」。"
               "预印本（arXiv）未经同行评审，必须单独标注。"
               "中文文献的期刊层级差异很大，引用时应说明它来自哪一类期刊。")
    weak = max((r.get("cited_by") or 0) for r in rows) < 10
    if weak:
        out.append("⚠ 本次文献影响力普遍很低（最高被引 < 10），可能只是弱源兜底结果。"
                   "作为「理论支撑」很弱，引用时必须向用户说明这一点，不要拿它当有力证据。")
        return "\n".join(out), [r["id"] for r in rows], True
    return "\n".join(out), [r["id"] for r in rows], False


def probe():
    """连通性自检。缓存 90 秒，避免自己把额度打爆；限流冷却中如实报告。"""
    import time
    now = time.time()
    if _probe_cache["data"] and now - _probe_cache["ts"] < CACHE_TTL:
        return _probe_cache["data"]

    res = {}
    for name, fn, q in (("openalex", openalex, "cognitive dissonance"),
                        ("crossref", crossref, "cognitive dissonance")):
        until = _cooldown.get(name, 0)
        if until > time.time():
            res[name] = {"ok": None, "throttled": True,
                         "note": f"限流冷却中，{int(until - time.time())} 秒后可重试"}
            continue
        try:
            got = fn(q, 1)
            res[name] = {"ok": bool(got), "count": len(got)}
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:80]}"
            res[name] = {"ok": False, "err": msg,
                         "throttled": "429" in msg or "限流" in msg}
    _probe_cache["ts"] = now
    _probe_cache["data"] = res
    return res

# ---------------------------------------------------------------- 背景参照（维基百科）
# 用途：他说一件事时，先给一点「这个情形在人类经验里长什么样」的背景，
# 再基于他的叙述做分析。注意它是常识背景，不是「和你一样的案例」的证据。
# 网络实测：维基直连不通、走系统代理 200；OpenAlex 反过来直连就通。
# 所以这里单独做一条「多路重试」的请求，不直接复用 _opener()。
WIKI_API = "https://{lang}.wikipedia.org/w/api.php"


def _wiki_get(url, timeout=12):
    """先按 MIRROR_PROXY，再直连，最后试本机常见代理端口。"""
    attempts = []
    proxy = os.environ.get("MIRROR_PROXY", "").strip()
    if proxy:
        attempts.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    attempts.append(urllib.request.ProxyHandler({}))
    attempts.append(urllib.request.ProxyHandler(
        {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}))
    last = None
    for handler in attempts:
        try:
            op = urllib.request.build_opener()
            op.add_handler(handler)
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with op.open(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:      # noqa: BLE001 - 多路重试，最后统一报
            last = e
    raise last or RuntimeError("wikipedia 不可达")


def wikipedia(query, lang="zh", limit=3):
    """按关键词取维基条目摘要。返回 [{title, extract, url, lang}]。"""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    params = urllib.parse.urlencode({
        "action": "query", "generator": "search", "gsrsearch": q,
        "gsrlimit": limit, "prop": "extracts", "exintro": 1,
        "explaintext": 1, "format": "json", "utf8": 1,
    })
    data = _wiki_get(WIKI_API.format(lang=lang) + "?" + params)
    try:
        j = json.loads(data)
    except ValueError:
        return []
    pages = ((j.get("query") or {}).get("pages") or {})
    out = []
    for p in pages.values():
        extract = (p.get("extract") or "").strip()
        if len(extract) < 40:
            continue
        # 过滤噪声：维基搜索常把同名影视剧、消歧义页排前面
        junk = ("电视剧", "電視劇", "电影", "電影", "综艺", "綜藝", "小说", "小說",
                "is a 20", "is a Chinese", "TV series", "film", "may refer to")
        if any(k in extract[:120] or k in (p.get("title") or "") for k in junk):
            continue
        out.append({
            "title": p.get("title") or "",
            "extract": extract[:700],
            "url": f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote((p.get("title") or "").replace(" ", "_")),
            "lang": lang,
        })
    # 相关性强校验：标题必须和查询有词面交集。
    # 不做这一步会出洋相——实测用「昨天下午我去找她」查，「林宅血案」「林志颖」
    # 都因为一个「林」字被命中。宁可没有背景，也不要塞垃圾。
    qt = [w for w in re.split(r"[\s，。、；：！？,.;:!?]+", q) if len(w) >= 2]
    kept = []
    for it in out:
        title = it["title"]
        hit = any(w in title or title in w for w in qt) or any(
            len(w) >= 2 and w in q for w in [title])
        if hit:
            kept.append(it)
    # 按 index 排序（MediaWiki 返回顺序不稳定）
    kept.sort(key=lambda x: pages_order(pages, x["title"]))
    return kept[:limit]


def pages_order(pages, title):
    for p in pages.values():
        if p.get("title") == title:
            return p.get("index", 99)
    return 99


def background(query_zh, query_en=""):
    """取中英文维基背景，各 2 条。拿不到就返回空，不抛错。"""
    rows = []
    for lang, q in (("zh", query_zh), ("en", query_en)):
        if not q:
            continue
        try:
            rows.extend(wikipedia(q, lang=lang, limit=2))
        except Exception:      # noqa: BLE001 - 背景拿不到不该挡住回答
            continue
    return rows


def background_block(rows):
    """背景参照块。刻意不给编号：它不是可引用的证据。"""
    if not rows:
        return ""
    out = ["\n【背景参照（维基百科摘要；常识背景，不是同行评审证据，也不是「和你一样」的案例）】"]
    for r in rows:
        out.append(f"- 《{r['title']}》：{r['extract']}")
        out.append(f"  {r['url']}")
    out.append("用法限制：只能用来解释「这类情形通常是什么样」，"
               "不能说成「有人也这样」或「研究证明你这种情况」。")
    return "\n".join(out)