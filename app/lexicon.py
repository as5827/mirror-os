# -*- coding: utf-8 -*-
"""中英关键词对照表。

解决什么问题：英文检索词原来只来自「理论条目的英文名」，一旦问题没命中任何理论
（实测 5 个问题里有 3 个如此），中文和英文检索词同时为空，联网检索直接失效。

这张表把中文问题里的词翻译成英文学术检索词，和理论库解耦：
即使问题不命中任何理论，也能拿「睡眠」「辞职」「存钱」这些词去查英文文献。

字数说明：只收高频、语义明确的词。一词多义的（如「看」）不收，宁可漏掉也不要搜错。
"""

# 顺序不重要，匹配时长词优先（见 extract）
PAIRS = [
    # ---------------- 学习 / 工作 / 方向
    ("拖延", "procrastination"),
    ("拖到", "procrastination"),
    ("分心", "distraction attention"),
    ("专注", "focus attention"),
    ("习惯", "habit formation"),
    ("目标", "goal setting"),
    ("计划", "planning"),
    ("放弃", "giving up goal disengagement"),
    ("坚持", "persistence grit"),
    ("兴趣", "interest intrinsic motivation"),
    ("学习", "learning"),
    ("记忆", "memory retention"),
    ("考试", "exam performance"),
    ("论文", "thesis academic writing"),
    ("毕业", "graduation"),
    ("读研", "graduate school decision"),
    ("考研", "postgraduate entrance examination"),
    ("编程", "programming"),
    ("代码", "software development"),
    ("面试", "job interview"),
    ("求职", "job search"),
    ("工作", "work"),
    ("职业", "career"),
    ("辞职", "job turnover resignation"),
    ("加班", "overwork"),
    ("领导", "leadership supervisor"),
    ("同事", "coworker workplace"),
    ("创业", "entrepreneurship"),
    ("转行", "career change"),
    ("技能", "skill acquisition"),
    ("刻意练习", "deliberate practice"),
    ("深度工作", "deep work"),

    # ---------------- 情绪 / 心理
    ("焦虑", "anxiety"),
    ("抑郁", "depression"),
    ("情绪", "emotion regulation"),
    ("压力", "stress"),
    ("疲惫", "fatigue burnout"),
    ("倦怠", "burnout"),
    ("睡眠", "sleep"),
    ("失眠", "insomnia"),
    ("熬夜", "sleep deprivation"),
    ("孤独", "loneliness"),
    ("自卑", "self-esteem"),
    ("自责", "self-blame guilt"),
    ("完美主义", "perfectionism"),
    ("愤怒", "anger"),
    ("后悔", "regret"),
    ("内耗", "rumination"),
    ("反刍", "rumination"),
    ("逃避", "avoidance coping"),
    ("恐惧", "fear"),
    ("羞耻", "shame"),
    ("自我怀疑", "self-doubt"),
    ("自我效能", "self-efficacy"),
    ("正念", "mindfulness"),
    ("意志力", "self-control willpower"),

    # ---------------- 人际 / 关系
    ("拒绝", "refusal assertiveness"),
    ("边界", "personal boundaries"),
    ("讨好", "people pleasing"),
    ("讨好型", "people pleasing"),
    ("索取", "exploitation reciprocity"),
    ("付出", "unreciprocated support"),
    ("互惠", "reciprocity"),
    ("沟通", "communication"),
    ("冲突", "conflict"),
    ("愧疚", "guilt"),
    ("朋友", "friendship"),
    ("同学", "peer relationship"),
    ("家人", "family relationship"),
    ("父母", "parenting parent-child"),
    ("恋爱", "romantic relationship"),
    ("分手", "breakup"),
    ("婚姻", "marriage"),
    ("背叛", "betrayal trust"),
    ("信任", "trust"),
    ("社交", "social interaction"),
    ("独处", "solitude"),
    ("归属", "belonging"),

    # ---------------- 金钱 / 时间 / 行为
    ("存钱", "saving behavior"),
    ("消费", "consumption spending"),
    ("冲动", "impulsive buying"),
    ("预算", "budgeting"),
    ("花钱", "spending"),
    ("沉没成本", "sunk cost"),
    ("时间管理", "time management"),
    ("上瘾", "addiction"),
    ("刷手机", "smartphone use"),
    ("短视频", "short video addiction"),
    ("游戏", "gaming"),
    ("社交媒体", "social media use"),
    ("运动", "physical exercise"),
    ("戒烟", "smoking cessation"),
    ("减肥", "weight loss dieting"),

    # ---------------- 判断 / 决策
    ("决策", "decision making"),
    ("选择", "choice"),
    ("风险", "risk perception"),
    ("判断", "judgment"),
    ("归因", "attribution"),
    ("动机", "motivation"),
    ("性格", "personality"),
    ("人格", "personality traits"),
    ("自我认知", "self-concept"),
    ("身份", "identity"),
]

_ALL = sorted(PAIRS, key=lambda p: -len(p[0]))


def extract(text, limit=3):
    """从文本里抽出 (中文词, 英文词) 对，长词优先，英文去重。"""
    t = text or ""
    out, seen_en = [], set()
    for zh, en in _ALL:
        if zh in t and en not in seen_en:
            seen_en.add(en)
            out.append((zh, en))
        if len(out) >= limit:
            break
    return out


def zh_terms(text, limit=3):
    return [zh for zh, _ in extract(text, limit)]


def en_terms(text, limit=3):
    return [en for _, en in extract(text, limit)]


def size():
    return len(PAIRS)

# 常用生活词（补在学术词之后，用于维基「背景参照」的查询词提取）
LIFE = [
    ("表白", "confession"), ("暗恋", "unrequited love"), ("单恋", "unrequited love"),
    ("失恋", "breakup"), ("暧昧", "ambiguous relationship"), ("追求", "courtship"),
    ("拒绝", "rejection"), ("冷暴力", "silent treatment"), ("边界", "personal boundaries"),
    ("讨好", "people pleasing"), ("内耗", "rumination"), ("焦虑", "anxiety"),
    ("失眠", "insomnia"), ("拖延", "procrastination"), ("孤独", "loneliness"),
    ("自责", "self-blame"), ("羞耻", "shame"), ("嫉妒", "jealousy"),
    ("信任", "trust"), ("依赖", "codependency"), ("冷落", "social rejection"),
    ("朋友", "friendship"), ("同事", "coworker"), ("上级", "supervisor"),
    ("父母", "parent"), ("亲密关系", "intimate relationship"), ("婚姻", "marriage"),
    ("分手", "breakup"), ("復合", "reconciliation"), ("求職", "job hunting"),
    ("辞职", "resignation"), ("考研", "postgraduate entrance exam"),
    ("创业", "entrepreneurship"), ("副业", "side job"), ("倦怠", "burnout"),
]


def life_terms(text, limit=2):
    """从话里挑出生活类概念词，给「背景参照」当查询用。"""
    t = (text or "").replace(" ", "")
    hits = [zh for zh, _en in LIFE if zh in t]
    return hits[:limit]


def life_en(zh_words):
    """把中文生活词翻成英文，用于英文维基。"""
    table = {zh: en for zh, en in LIFE}
    return [table[w] for w in zh_words if w in table]