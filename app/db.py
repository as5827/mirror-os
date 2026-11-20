# -*- coding: utf-8 -*-
"""数据层：SQLite schema + 连接 + 默认风控阈值。

设计原则（对应使用者的性格要求）：
1. 只追加，不改写历史：events / interactions / decisions 一旦写下，靠新记录或状态字段表达变化。
2. 否决必须有理由：schema 层 CHECK 约束，不让空理由落库。
3. 数据主权：单文件 SQLite，无云、无迁移、可直接复制备份。
"""
import json
import os
import sqlite3
import time
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "mirror.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ① 事实层：发生了什么。所有分析都只能从这里长出来。
CREATE TABLE IF NOT EXISTS events(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    on_date       TEXT NOT NULL,
    domain        TEXT NOT NULL DEFAULT '其他',   -- 人际/职业/金钱/精力/学习/其他
    title         TEXT NOT NULL,
    my_words      TEXT DEFAULT '',                -- 我当时说了什么（原话）
    my_actions    TEXT DEFAULT '',                -- 我当时做了什么
    other_response TEXT DEFAULT '',               -- 对方/环境如何反应
    emotion       INTEGER DEFAULT 3,              -- 1..5
    outcome       TEXT DEFAULT 'neutral',         -- good/neutral/bad
    initiative    INTEGER DEFAULT 2,              -- 0回避 1被动 2被推着 3主动 4强推
    hours         REAL DEFAULT 0,
    money         REAL DEFAULT 0,
    replay        TEXT DEFAULT '',                -- 重来一次我会怎么做
    tags          TEXT DEFAULT '',
    topic_id      INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    person_id     INTEGER REFERENCES people(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_date ON events(on_date);
CREATE INDEX IF NOT EXISTS ix_events_domain ON events(domain);

-- ② 性格画像：每条假设必须挂证据事件 id，否则视为 AI 幻觉，不入库。
CREATE TABLE IF NOT EXISTS traits(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    direction     TEXT DEFAULT '',    -- 这条模式的走向：对你有用 / 对你有害 / 中性
    statement     TEXT NOT NULL,      -- 「你倾向于……」
    evidence      TEXT NOT NULL,      -- JSON 数组：event id
    blind_spot    TEXT DEFAULT '',    -- 你在这里看不见什么
    cost          TEXT DEFAULT '',    -- 这个模式已经让你付出了什么
    counter       TEXT DEFAULT '',    -- 反制动作（要具体到可执行）
    confidence    REAL DEFAULT 0.5,
    status        TEXT DEFAULT 'draft',  -- draft/accepted/rejected
    reject_reason TEXT DEFAULT '',
    source        TEXT DEFAULT 'ai',     -- ai/rule/manual
    created_at    TEXT NOT NULL
);

-- ③ 方向聚焦：解决「什么都学、什么都不深」。
CREATE TABLE IF NOT EXISTS topics(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    domain        TEXT DEFAULT '',
    started_on    TEXT,
    depth_level   INTEGER DEFAULT 0,   -- 0接触 1能聊 2能教 3能产出 4专业 5可谋生
    hours         REAL DEFAULT 0,
    artifacts     INTEGER DEFAULT 0,   -- 交付物数量（文章/项目/作品）
    interest      INTEGER DEFAULT 3,   -- 1..5 主观兴趣
    last_touch_on TEXT,
    last_depth_up_on TEXT,             -- 上次深度等级提升日（用于停滞检测）
    mainline      INTEGER DEFAULT 0,   -- 是否主线
    status        TEXT DEFAULT 'active', -- active/parked/archived
    why           TEXT DEFAULT '',     -- 为什么开始
    archive_reason TEXT DEFAULT '',    -- 封存/放弃必须写理由
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_sessions(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id   INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    on_date    TEXT NOT NULL,
    hours      REAL DEFAULT 0,
    note       TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- ④ 决策：备选 × 准则 → 加权分；否决分支必须写理由；二阶效应必须三层。
CREATE TABLE IF NOT EXISTS decisions(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    on_date       TEXT NOT NULL,
    title         TEXT NOT NULL,
    kind          TEXT DEFAULT '其他',   -- 进入/止损/人际降级/支出/方向选择/其他
    context       TEXT DEFAULT '',
    criteria      TEXT NOT NULL DEFAULT '[]',  -- [{name,weight}]
    options       TEXT NOT NULL DEFAULT '[]',  -- [{name,scores:{准则:0-10},note}]
    second_order  TEXT DEFAULT '[]',  -- [{option,chain:[l1,l2,l3]}]
    stop_loss     TEXT DEFAULT '',    -- 可验证的退出条件
    rejected      TEXT NOT NULL DEFAULT '[]', -- [{option,reason}] reason 不得少于 8 字
    chosen        TEXT DEFAULT '',    -- 被选中的 option 名
    reversibility TEXT DEFAULT 'unknown', -- 可逆/半可逆/不可逆
    review_due    TEXT,
    review_actual TEXT DEFAULT '',
    review_score  INTEGER,            -- -2 严重错判 .. +2 判对了
    review_lesson TEXT DEFAULT '',
    status        TEXT DEFAULT 'open', -- open/decided/closed
    created_at    TEXT NOT NULL
);

-- ⑤ 人际：付出/回馈两面记账，比值驱动风控。
CREATE TABLE IF NOT EXISTS people(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    relation      TEXT DEFAULT '',
    status        TEXT DEFAULT 'normal', -- normal/cooling/downgraded/cut
    last_contact_on TEXT,
    idle_since    TEXT,                -- 对方悬置你（等你回音却不推进）的起点
    notes         TEXT DEFAULT '',
    status_reason TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interactions(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    on_date    TEXT NOT NULL,
    direction  TEXT NOT NULL DEFAULT 'give', -- give=我付出 / take=我得到 / neutral
    weight     REAL DEFAULT 1,
    what       TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- ⑥ 金钱与精力：冷静期队列。
CREATE TABLE IF NOT EXISTS spend(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    on_date    TEXT NOT NULL,
    item       TEXT NOT NULL,
    amount     REAL NOT NULL,
    category   TEXT DEFAULT '',
    need       INTEGER DEFAULT 3,     -- 1..5 必要性
    cool_until TEXT,                  -- 冷静期到期日
    status     TEXT DEFAULT 'pending',-- pending/approved/cancelled
    verdict_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- ⑦ 风控阈值：可调，不写死在代码里。
CREATE TABLE IF NOT EXISTS rules(
    key       TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    descr     TEXT DEFAULT '',
    value     REAL NOT NULL,
    unit      TEXT DEFAULT '',
    enabled   INTEGER DEFAULT 1
);

-- ⑧ 预警处置：不写理由就无法关闭一条预警。
CREATE TABLE IF NOT EXISTS alert_acks(
    fingerprint TEXT PRIMARY KEY,
    status      TEXT NOT NULL,     -- handled/ignored
    note        TEXT NOT NULL,
    acked_at    TEXT NOT NULL
);

-- ⑨ AI 原始报告留档：可复算、可追责。
CREATE TABLE IF NOT EXISTS reports(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    result     TEXT NOT NULL,
    ok         INTEGER DEFAULT 1,
    err        TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- ⑩ 长期记忆：用户亲手确认的事实，注入每一次 AI 分析。
-- 只有这里的内容被当作「前提」，traits 只算「可被推翻的结论」。
CREATE TABLE IF NOT EXISTS memory(
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    statement      TEXT NOT NULL,
    kind           TEXT DEFAULT '自我认知',  -- 自我认知/边界规则/偏好/禁忌/巩固·领域
    source         TEXT DEFAULT 'manual',    -- manual=手写 / consolidated=AI 从旧事件提炼
    status         TEXT DEFAULT 'active',    -- active/draft(待审,不进上下文)/retired
    evidence       TEXT DEFAULT '[]',        -- 巩固型记忆挂的事件编号
    retired_reason TEXT DEFAULT '',
    created_at     TEXT NOT NULL
);

-- ⑪ 全文检索索引（FTS5 + trigram）：不装 embedding 也能按关键字/子串召回旧记忆。
-- 用外部内容表模式，不复制数据；trigram 分词对中文子串同样有效。
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    title, my_words, my_actions, other_response, replay, tags,
    content='events', content_rowid='id', tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
  INSERT INTO events_fts(rowid,title,my_words,my_actions,other_response,replay,tags)
  VALUES(new.id,new.title,new.my_words,new.my_actions,new.other_response,new.replay,new.tags);
END;

CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
  INSERT INTO events_fts(events_fts,rowid,title,my_words,my_actions,other_response,replay,tags)
  VALUES('delete',old.id,old.title,old.my_words,old.my_actions,old.other_response,old.replay,old.tags);
END;

CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
  INSERT INTO events_fts(events_fts,rowid,title,my_words,my_actions,other_response,replay,tags)
  VALUES('delete',old.id,old.title,old.my_words,old.my_actions,old.other_response,old.replay,old.tags);
  INSERT INTO events_fts(rowid,title,my_words,my_actions,other_response,replay,tags)
  VALUES(new.id,new.title,new.my_words,new.my_actions,new.other_response,new.replay,new.tags);
END;

-- ⑫ 长期对话：会话 / 消息 / 滚动摘要。
-- 长对话不能无限塞进上下文，所以老消息压成摘要，原文永远留在 messages 里可回看可检索。
CREATE TABLE IF NOT EXISTS conversations(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT DEFAULT '新对话',
    skill      TEXT DEFAULT '',
    archived   INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,              -- user/assistant
    content         TEXT NOT NULL,
    refs            TEXT DEFAULT '[]',          -- 校验通过的引用 [{type,id,label}]
    bad_refs        TEXT DEFAULT '[]',          -- 引用了不存在编号的（准确性告警）
    skill           TEXT DEFAULT '',
    ok              INTEGER DEFAULT 1,
    err             TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msg_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS ix_conv_upd ON conversations(updated_at);

CREATE TABLE IF NOT EXISTS summaries(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    upto_message_id INTEGER NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sum_conv ON summaries(conversation_id, id);

-- ⑬ 会话全文检索：长对话里按关键词翻旧账。
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id', tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS msg_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid,content) VALUES(new.id,new.content);
END;
CREATE TRIGGER IF NOT EXISTS msg_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts,rowid,content) VALUES('delete',old.id,old.content);
END;
CREATE TRIGGER IF NOT EXISTS msg_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts,rowid,content) VALUES('delete',old.id,old.content);
  INSERT INTO messages_fts(rowid,content) VALUES(new.id,new.content);
END;

-- ⑮ 联网检索到的文献（本地缓存，可被引用为 [文献#id]）。
-- 为什么要缓存：一是省请求、二是断网后这些证据仍然可查可引。
CREATE TABLE IF NOT EXISTS reference(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doi        TEXT DEFAULT '',
    title      TEXT NOT NULL,
    year       INTEGER,
    cited_by   INTEGER DEFAULT 0,   -- 被引次数：这是「有多少研究支持」的粗代理
    venue      TEXT DEFAULT '',
    authors    TEXT DEFAULT '',
    abstract   TEXT DEFAULT '',
    url        TEXT DEFAULT '',
    source     TEXT DEFAULT 'openalex',
    query      TEXT DEFAULT '',
    theory_id  INTEGER,             -- 若为某条理论核验而来，记下归属
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ref_doi ON reference(doi);
CREATE INDEX IF NOT EXISTS ix_ref_theory ON reference(theory_id);
"""

DEFAULT_RULES = [
    ("mainline_cap", "并行主线上限", "同时活跃的主线方向超过此数，强制封存", 3, "个", 1),
    ("shallow_idle_days", "浅尝未深判定", "深度≤1 且超过此天数未投入，判为浅尝", 30, "天", 1),
    ("topic_stall_days", "深度停滞判定", "深度等级超过此天数未提升，判为停滞", 45, "天", 1),
    ("mainline_share_min", "主线投入占比下限", "本月主线投入占比低于此值，判为精力漂移", 0.5, "比例", 1),
    ("person_idle_days", "悬置判定", "超过此天数无往来，标为悬置", 14, "天", 1),
    ("givetake_ratio", "单向索取判定", "我付出减去我得到 占我付出 的比例超过此值，判为单向索取", 0.6, "比例", 1),
    ("spend_threshold", "冷静期门槛", "单笔支出超过此金额，强制进入冷静期", 500, "元", 1),
    ("spend_cool_days", "冷静期时长", "冷静期天数，到期才允许裁决", 3, "天", 1),
    ("decision_review_grace", "决策复盘宽限", "复盘日过后超过此天数未复盘，报警", 7, "天", 1),
]


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn):
    """轻量迁移：老库缺列时补上，不丢数据。"""
    have = {r[1] for r in conn.execute("PRAGMA table_info(traits)")}
    if "dup_of" not in have:
        conn.execute("ALTER TABLE traits ADD COLUMN dup_of INTEGER DEFAULT 0")
    ev = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    if "consolidated" not in ev:
        # 0=尚未被巩固成记忆，1=已巩固（避免重复压缩同一条事件）
        conn.execute("ALTER TABLE events ADD COLUMN consolidated INTEGER DEFAULT 0")
    mem = {r[1] for r in conn.execute("PRAGMA table_info(memory)")}
    if "evidence" not in mem:
        conn.execute("ALTER TABLE memory ADD COLUMN evidence TEXT DEFAULT '[]'")
    conv = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    if "web" not in conv:
        # 联网检索开关：默认关闭。开着意味着你的问题会发到第三方学术 API。
        conn.execute("ALTER TABLE conversations ADD COLUMN web INTEGER DEFAULT 0")
    if "web_lang" not in conv:
        # zh=只查中文文献 / en=只查英文 / both=两边都查（默认）
        conn.execute("ALTER TABLE conversations ADD COLUMN web_lang TEXT DEFAULT 'both'")
    if "comfort" not in conv:
        # auto=按情绪信号自动决定 / on=有情绪就先共情 / off=一律直接分析
        conn.execute("ALTER TABLE conversations ADD COLUMN comfort TEXT DEFAULT 'auto'")
    ref = {r[1] for r in conn.execute("PRAGMA table_info(reference)")}
    if "lang" not in ref:
        conn.execute("ALTER TABLE reference ADD COLUMN lang TEXT DEFAULT ''")
    conn.commit()


def init():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # 把已有事件灌进 FTS 索引（幂等：rebuild 会整表重建）
        for tbl in ("events_fts", "messages_fts"):
            try:
                conn.execute(f"INSERT INTO {tbl}({tbl}) VALUES('rebuild')")
            except sqlite3.Error:
                pass
        for key, name, descr, value, unit, enabled in DEFAULT_RULES:
            conn.execute(
                "INSERT INTO rules(key,name,descr,value,unit,enabled) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name, descr=excluded.descr, unit=excluded.unit",
                (key, name, descr, value, unit, enabled),
            )
        conn.commit()
    finally:
        conn.close()

    # 知识库首次灌入（放这里避免循环 import，且只在空表时执行）
    try:
        from . import knowledge
        c2 = connect()
        try:
            knowledge.ensure_seeded(c2)
        finally:
            c2.close()
    except Exception:
        pass


# ---------------------------------------------------------------- 备份与自检
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
BACKUP_KEEP = 10


def backup(reason="manual", keep=BACKUP_KEEP):
    """用 SQLite 原生 backup API 做一致性快照（WAL 下直接拷贝文件是不安全的）。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"mirror-{stamp}-{reason}.db")
    src = connect()
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    # 轮转：只留最近 keep 个
    files = sorted(
        (os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.endswith(".db")),
        key=os.path.getmtime)
    for old in files[:-keep] if len(files) > keep else []:
        try:
            os.remove(old)
        except OSError:
            pass
    return dest


def integrity_check():
    conn = connect()
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return date.today().isoformat()


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None


def load_rules(conn):
    out = {}
    for r in conn.execute("SELECT * FROM rules"):
        out[r["key"]] = {"value": float(r["value"]), "enabled": bool(r["enabled"]),
                         "name": r["name"], "descr": r["descr"], "unit": r["unit"]}
    return out


def jloads(text, default):
    if not text:
        return default
    try:
        v = json.loads(text)
        return v if v is not None else default
    except (ValueError, TypeError):
        return default
