# -*- coding: utf-8 -*-
"""知识库：可引用的心理学 / 社会学 / 行为经济学理论条目。

为什么要建这个，而不是让模型「凭记忆用心理学」：
1. 模型自带的理论知识不可审计——你无法核对它引的到底是哪条、有没有编。
2. 泛泛而谈的「心理学告诉我们」是本系统明令禁止的模糊表达。
3. 有了库，就能像 [事件#12] 那样强制它写 [理论#3]，我可以逐条校验编号是否存在。

重要说明（必须诚实）：
- 下面是**我整理的概念摘要**，不是原文引用；`source` 列是让你去核对的出处。
- 每条都写了 `misuse`（误用风险），因为这类概念最大的危害是被拿来给别人贴标签。
- 少数条目存在可重复性争议（如成长型心态），已在 misuse 里标明，不要当铁律用。
"""
import json

from . import db

THEORIES = [
    # ---------------------------------------------------------- 认知与情绪
    dict(name="认知失调", en="Cognitive Dissonance", field="心理学",
         source="Festinger, 1957《A Theory of Cognitive Dissonance》",
         summary="当行为和自我认知冲突时，人倾向于修改信念来解释行为，而不是修改行为——因为改行为成本更高。",
         applies="你已经付出代价、却还在为这个选择找理由时",
         misuse="不能用来解释别人的一切自我辩解，否则变成万能归因",
         signal="事件里出现「其实也还行」「至少……」这类事后合理化措辞",
         tags="合理化 事后归因 自我辩护 沉没"),
    dict(name="基本归因错误", en="Fundamental Attribution Error", field="心理学",
         source="Ross, 1977",
         summary="解释别人时高估性格因素、低估情境因素；解释自己时正好相反。",
         applies="你在评判某人是「人品问题」而没看他的处境约束时",
         misuse="反过来用也会错——有些行为确实是性格，情境不是万能挡箭牌",
         signal="人际事件里对他人动机的推断远多于对处境的记录",
         tags="归因 双标 评判他人"),
    dict(name="习得性无助", en="Learned Helplessness", field="心理学",
         source="Seligman & Maier, 1967",
         summary="反复经历无法控制的失败后，即使后来有出路也不再尝试。",
         applies="某个方向连续失败后你就直接不碰、连试都不试",
         misuse="不是所有放弃都是习得性无助，有些是合理的止损",
         signal="同一领域出现「反正没用」的表述，且之后无任何尝试记录",
         tags="放弃 无力感 止损 失败 换方向"),
    dict(name="自我决定论", en="Self-Determination Theory", field="心理学",
         source="Deci & Ryan, 1985",
         summary="持续动机来自三个需求：自主（我选的）、胜任（我能变强）、联结（有人在乎）。缺一个，动机就会衰。",
         applies="判断一个方向你是真喜欢，还是只是被推着走",
         misuse="别把它当成「只要热爱就能坚持」的鸡汤，它讲的是需求结构不是热情",
         signal="投入退潮时，对照哪一项需求没被满足：是被安排的、还是看不到进步",
         tags="动机 自主 胜任 坚持 兴趣"),
    dict(name="沉没成本谬误", en="Sunk Cost Fallacy", field="行为经济学",
         source="Arkes & Blumer, 1985",
         summary="已经付出的、不可收回的成本，不该影响下一步决策；但人会用「不能白费」绑架自己。",
         applies="你在纠结「都投入这么多了要不要继续」",
         misuse="已投入的时间不是完全无信息——它可能证明了你在这个领域的能力上限",
         signal="决策背景里出现「已经花了 X 小时 / X 元」作为继续的理由",
         tags="止损 纠结 投入 沉没"),
    dict(name="承诺与一致性", en="Commitment & Consistency", field="心理学",
         source="Cialdini, 1984《Influence》",
         summary="一旦公开承诺过某立场，人会倾向于维持一致，哪怕新证据不利。",
         applies="你为了维持「我说过的话」而坚持一个已经不成立的判断",
         misuse="一致性本身是优点，问题只在它压过证据时",
         signal="决策复盘里出现「我当初说过」而不是「数据显示」",
         tags="公开承诺 面子 一致 改口"),
    dict(name="间歇性强化", en="Intermittent Reinforcement", field="心理学",
         source="Skinner 操作性条件反射研究",
         summary="不确定的奖励比稳定的奖励更容易形成顽固习惯——这正是赌博和「秒回才值得」的心理机制。",
         applies="解释你戒不掉的刷手机、或对某人忽冷忽热的执念",
         misuse="不要用它把对方的行为完全病理化，这是机制描述不是动机揣测",
         signal="精力事件里出现高频低效的重复行为，且期望落空时情绪波动更大",
         tags="上瘾 刷手机 忽冷忽热 执念"),
    dict(name="损失厌恶", en="Loss Aversion", field="行为经济学",
         source="Kahneman & Tversky, 1979 前景理论",
         summary="同样的量，失去带来的痛大于得到带来的快，因此人过度规避损失。",
         applies="你明明该止损，却因为「会失去」而拖着",
         misuse="厌恶损失有时是对的——不是所有规避都是非理性",
         signal="止损条件写不出来，或写了却反复往后推",
         tags="止损 拖延 风险 失去"),
    dict(name="双曲贴现", en="Hyperbolic Discounting", field="行为经济学",
         source="Laibson, 1997 现时偏好研究",
         summary="人对近期的奖赏打折很小、对远期的打折很大，所以「现在爽一下」总能赢过「半年后变强」。",
         applies="解释为什么长期计划总是输给即时快感",
         misuse="不是意志力缺陷，是时间偏好的结构性偏差；对策是改变选择架构而非自责",
         signal="计划与执行长期分离，且失败集中在夜间/独处时段",
         tags="拖延 即时满足 长期 时间偏好"),
    dict(name="心理账户", en="Mental Accounting", field="行为经济学",
         source="Thaler, 1985",
         summary="人把同一笔钱按来源和用途分进不同「账户」，因此做出互相矛盾的消费决定。",
         applies="你舍得给设备花几千、却舍不得给学习内容花钱",
         misuse="心理账户也能是有效自律工具（专款专用），不必然是错误",
         signal="支出记录里同类用途的额度差异巨大且无依据",
         tags="消费 金钱 预算 双标"),
    dict(name="锚定效应", en="Anchoring", field="行为经济学",
         source="Tversky & Kahneman, 1974",
         summary="最先出现的数字会成为判断基准，之后的调整总是不够。",
         applies="对方先报价、或你先看到某个薪资数字之后的所有估值",
         misuse="锚只要被意识到就部分失效，别高估自己的免疫",
         signal="决策的依据数字来自单一首次接触的信息",
         tags="报价 估值 谈判 第一印象"),
    dict(name="邓宁-克鲁格效应", en="Dunning-Kruger Effect", field="心理学",
         source="Kruger & Dunning, 1999",
         summary="能力最低的人高估自己，因为评估能力本身就需要能力；而高手常低估自己。",
         applies="判断你的「我觉得我会了」是不是真的会了",
         misuse="常被拿来嘲讽别人，这是滥用；统计上它主要体现为回归效应的错觉，别当人格判定",
         signal="深度等级自评高、但被外部考核（面试、评审）当场击穿的记录",
         tags="自评 高估 面试 能力错觉"),
    dict(name="执行意图", en="Implementation Intentions", field="心理学",
         source="Gollwitzer, 1999",
         summary="把「我要 X」写成「如果情境 Y，我就做 Z」，完成率显著高于空泛目标。",
         applies="你说要改但没有任何 if-then 结构时",
         misuse="if-then 写得太笼统等于没写，必须是可观察的触发条件",
         signal="你的止损条件或反制动作是否具备可观察的触发点",
         tags="计划 if-then 习惯 执行"),
    dict(name="认知重评与表达抑制", en="Cognitive Reappraisal vs Suppression", field="心理学",
         source="Gross, 1998 情绪调节过程模型",
         summary="重评（改变对事件的解释）长期效果更好；抑制（压住不表现）会累积生理与关系成本。",
         applies="你习惯「忍着不说」而不是重新解释这件事",
         misuse="重评也可能变成自我欺骗，区别在于新解释是否有事实支撑",
         signal="情绪低但言行克制，且事后无解释性记录，只有压抑",
         tags="情绪 压抑 忍 情绪劳动"),
    dict(name="蔡格尼克效应", en="Zeigarnik Effect", field="心理学",
         source="Zeigarnik, 1927",
         summary="未完成的任务会持续占用注意力，比已完成的记得更牢。",
         applies="为什么放下没做完的事会让你一直累",
         misuse="「未完成」必须有明确的关闭动作才会释放，单纯搁着不会",
         signal="多个项目停在半途，注意力事件里反复出现「又想起那个项目」",
         tags="未完成 分心 项目 关闭 没做成"),
    dict(name="心流", en="Flow", field="心理学",
         source="Csikszentmihalyi, 1990",
         summary="当挑战难度与当前技能水平大致匹配（略高一点）时，人会进入高度专注且愉悦的状态。",
         applies="判断某个方向是不是「对你是可深的」，而非只是有趣",
         misuse="心流不是快乐，是专注；把它等同爽感会误导方向选择",
         signal="投入记录里出现时长明显偏长、且事后情绪偏高的片段",
         tags="专注 深度 兴趣 匹配"),
    dict(name="成长型心态（有争议）", en="Growth Mindset", field="心理学",
         source="Dweck, 2006；后续大规模重复实验效果偏小（如 Yeager et al. 2019）",
         summary="相信能力可培养的人更愿意面对困难；但这个效应的实际强度和可迁移性被后续研究下调。",
         applies="判断你面对「我不擅长这个」时的默认反应",
         misuse="**争议项**：不要把它当铁律，也不要拿它要求别人「只要努力就行」",
         signal="遇到平台期就换方向的频次",
         tags="心态 努力 天赋 争议"),

    # ---------------------------------------------------------- 关系与边界
    dict(name="社会交换理论", en="Social Exchange Theory", field="社会学",
         source="Homans, 1958；Blau, 1964",
         summary="人际关系可视为成本—报酬的交换，长期失衡的关系会由受益方维持、受损方退出。",
         applies="算清楚「我付出/我得到」，而不是凭感觉说「他对我还行」",
         misuse="不是所有关系都该算账，亲密关系里过度计算本身会破坏关系；它适合用来识别**结构性**失衡",
         signal="付出与得到权重长期严重偏离且有明确对象",
         tags="互惠 付出 失衡 血包 关系 帮忙 拒绝 不好意思 同事"),
    dict(name="互惠规范", en="Norm of Reciprocity", field="社会学",
         source="Gouldner, 1960",
         summary="社会默认「受惠应回报」，拒绝回报者会被道德压力惩罚；因此单向受惠是一种隐蔽的权力。",
         applies="识别「先用小恩小惠让你难以拒绝」的结构",
         misuse="小恩小惠不一定有算计，判据是**是否形成持续的、可预期的索取模式**",
         signal="同一对象多次「先给小的、后要大的」的配对记录",
         tags="人情 亏欠 道德绑架 索取 帮忙 拒绝 不好意思 老同学"),
    dict(name="弱关系的力量", en="Strength of Weak Ties", field="社会学",
         source="Granovetter, 1973",
         summary="新机会和信息更多来自弱关系（点头之交），因为强关系的信息高度重叠。",
         applies="你在纠结要不要维护那些「不熟」的人脉",
         misuse="弱关系给信息，强关系给支持，两者不能互相替代",
         signal="人际记录里强关系密集、弱关系几乎为零",
         tags="人脉 信息 机会 社交"),
    dict(name="拟剧论与印象管理", en="Dramaturgy / Impression Management", field="社会学",
         source="Goffman, 1959《The Presentation of Self in Everyday Life》",
         summary="人在不同「舞台」上管理自己呈现的形象，前台表演与后台真实状态可以很不一样。",
         applies="解释你在某些场合的言行和私下判若两人",
         misuse="不是「所以人是虚伪的」，印象管理是社会互动的必要条件",
         signal="同一模式在公开场合与独处时的记录差异",
         tags="形象 表演 场合 人设"),
    dict(name="情感劳动", en="Emotional Labor", field="社会学",
         source="Hochschild, 1983《The Managed Heart》",
         summary="为满足他人期待而管理自己的情绪，是一种真实消耗，但常被当作「应该的」而不计入付出。",
         applies="你的人际付出明显偏「情绪照顾」而非实际帮助时",
         misuse="别用它给所有不情愿的社交开脱",
         signal="人际事件里大量记录「安慰」「顺着说」「忍住没发火」",
         tags="情绪 消耗 照顾 付出 同事 帮忙"),
    dict(name="自我分化", en="Differentiation of Self", field="心理学",
         source="Bowen 家庭系统理论",
         summary="能在亲密关系中保持自己的判断、不被对方的情绪卷走的能力。分化低的人靠疏远或黏附来应对冲突。",
         applies="你一被对方情绪牵动就改变自己的判断",
         misuse="分化不等于冷漠，高分化是能亲近又不失判断",
         signal="人际事件里「因为他生气所以我改了主意」的记录",
         tags="边界 亲密 依赖 判断 拒绝 后悔 不好意思"),
    dict(name="依恋类型", en="Attachment Styles", field="心理学",
         source="Bowlby, 1969；Ainsworth, 1978；Hazan & Shaver, 1987 扩展到成人",
         summary="早期照护经验形成对亲密关系的预期模式（安全/焦虑/回避），影响成年后的亲近与撤退方式。",
         applies="理解你在关系紧张时的默认动作（追着要确认，还是抽身不理）",
         misuse="**高频滥用项**：它是模式描述，不是给人贴标签，也非不可改变",
         signal="关系冲突时你的第一反应在多个对象上高度一致",
         tags="亲密 回避 焦虑 关系模式 拒绝 后悔"),
    dict(name="心理抗拒", en="Reactance Theory", field="心理学",
         source="Brehm, 1966",
         summary="当自由被限制时，人会产生反抗动机，甚至故意选更差但更自由的那个。",
         applies="别人越劝你，你越不想做——包括你自己给自己定的规则",
         misuse="不是所有抵触都是抗拒，有些抵触是正确的判断",
         signal="被建议后的行为与该建议相反，且无新证据支持",
         tags="反感 被管 逆反 自由"),
    dict(name="参照群体", en="Reference Group", field="社会学",
         source="Merton, 1957 参照群体理论",
         summary="人用自己选择的比较群体来定位自我评价，群体的选择本身就决定了你会不会觉得自己失败。",
         applies="你的焦虑到底是绝对水平不足，还是比错了对象",
         misuse="换参照群体不是逃避，但用它否认差距就是自欺",
         signal="情绪事件里出现与他人比较，且比较对象远超你的现实可得区间",
         tags="比较 焦虑 同辈 标准"),
    dict(name="沉默的螺旋", en="Spiral of Silence", field="社会学",
         source="Noelle-Neumann, 1974",
         summary="人害怕被孤立，会先判断「哪种观点占上风」，弱势观点因此越来越不敢表达，形成螺旋。",
         applies="解释你在群体中明明不同意却不说",
         misuse="个体差异很大，不适用于所有场合",
         signal="事件里「大家都这么说所以我也没反对」的记录",
         tags="群体 从众 表达 沉默"),
    dict(name="从众与规范性影响", en="Conformity / Normative Influence", field="社会心理学",
         source="Asch, 1951 线段实验",
         summary="即使答案显而易见，群体一致的压力也会让人给出明显错误的判断。",
         applies="你在多人场合的判断被集体意见带偏",
         misuse="不是所有跟随都是错的，专家共识有时就是对的",
         signal="独立判断与群体结论冲突时的最终选择记录",
         tags="从众 群体 判断 压力"),
    dict(name="社会资本", en="Social Capital", field="社会学",
         source="Bourdieu, 1986；Coleman, 1988；Putnam, 2000",
         summary="关系网络本身是一种可转化为机会的资源，但它的形成依赖长期互惠，无法临时购买。",
         applies="判断你在某个领域缺的到底是能力还是「认识谁」",
         misuse="社会资本不能替代能力，过度强调会滑向「关系决定论」",
         signal="机会类事件里是否出现引荐/推荐，还是全靠公开投递",
         tags="人脉 机会 资源 网络"),
    dict(name="角色冲突与角色超载", en="Role Conflict / Overload", field="社会学",
         source="Kahn et al., 1964 角色理论",
         summary="一个人同时承担多个角色的期待，当期待互相矛盾或总量超出容量时，会产生持续紧张。",
         applies="你的精力问题不是懒，是角色总量超过容量",
         misuse="不是所有压力都能归给角色，个人选择也在其中",
         signal="同一时段多个领域事件并发，且质量同时下降",
         tags="精力 多重身份 压力 冲突 同事 推活"),
    dict(name="结构性不平等与机会结构", en="Structure & Opportunity", field="社会学",
         source="社会学经典议题（Merton 机会结构、Bourdieu 资本理论）",
         summary="个体的结果不只由努力决定，还受所处结构（资源、门槛、信息渠道）限制。",
         applies="复盘失败时，区分「我做错了」和「这条路的门槛对我不利」",
         misuse="**不能滥用为免责**：结构解释环境，不解释你的每一次具体选择",
         signal="同类失败在不同尝试中重复，且外部约束记录一致",
         tags="归因 环境 门槛 机会 失败"),
    dict(name="搭便车与公共物品困境", en="Free-Rider Problem", field="社会学",
         source="Olson, 1965《The Logic of Collective Action》",
         summary="在共享收益的群体中，个体有动机少付出而多享受，最终共同资源被消耗。",
         applies="小组作业、家庭分工、朋友圈里的「总有人兜底」",
         misuse="先要确认确实存在共享收益结构，否则是误判",
         signal="多人场景里付出集中在同一人（常常是你）",
         tags="分工 占便宜 团队 付出 同事 推活 甩锅"),

    # ---------------------------------------------------------- 自我与改变
    dict(name="拖延的情绪调节视角", en="Procrastination as Emotion Regulation", field="心理学",
         source="Sirois & Pychyl, 2013",
         summary="拖延本质上不是时间管理失败，而是用回避来短期修复任务引发的负面情绪。",
         applies="你明知道该做却动不了时，先找那个情绪而不是找日程表",
         misuse="不是所有拖延都是情绪问题，任务过载、指令不清也会导致停滞",
         signal="事件里「打算做 X 却做了 Y」的记录伴随情绪低分",
         tags="拖延 情绪 回避 时间管理 论文 明知该做"),
    dict(name="自我效能感", en="Self-Efficacy", field="心理学",
         source="Bandura, 1977",
         summary="对「我能不能完成这件事」的信念，主要来自过往成功经验，因此小胜比鼓励更有效。",
         applies="你缺的不是方法而是「我做成过」的证据",
         misuse="效能感不能替代能力，只靠信念是自我暗示",
         signal="同一类任务的成功记录数与尝试意愿的相关性",
         tags="信心 能力 成功经验 鼓励"),
    dict(name="固定与可变目标", en="Learning vs Performance Goals", field="心理学",
         source="Dweck & Elliott, 1983；Elliot 目标取向理论",
         summary="追求「学会」的人愿意暴露不会；追求「表现好」的人会回避可能失败的场合。",
         applies="你不敢开始，是因为怕被看穿不会",
         misuse="表现目标在考核场合是必要的，不必然有害",
         signal="是否回避有外部评价的场合（面试、公开输出）",
         tags="目标 失败 面子 学习"),
    dict(name="行为激活", en="Behavioral Activation", field="心理学",
         source="Lewinsohn 行为主义抑郁干预；Jacobson et al., 1996 验证",
         summary="情绪低落时，先做小行动再谈动力，比等心情好了再动更有效——行动先行能反向拉动情绪。",
         applies="你在等「状态好了再开始」",
         misuse="中重度情绪问题需要专业干预，行为激活不是替代治疗",
         signal="低情绪日之后是否出现任何小行动记录",
         tags="情绪 行动 动力 低落"),
    dict(name="最小可行实验", en="Minimum Viable Experiment", field="方法论",
         source="精益创业（Ries, 2011）在个人决策上的移植",
         summary="用最小成本获取能改变决策的那一条信息，而不是先完整评估再行动。",
         applies="你在无限期地「再了解一下」某个方向时",
         misuse="可逆的小实验有价值，不可逆的事不能拿来做实验",
         signal="决策长期悬置且理由为「信息不足」",
         tags="实验 验证 悬置 信息"),
]


def ensure_seeded(conn):
    """首次启动灌入理论条目。已有数据不覆盖，保留你的修改。

    自己先确保表存在：db.SCHEMA 里漏过这张表，结果全新数据库上这里第一句 SELECT
    就抛 no such table，而 init() 把异常吞了，知识库静默变空。所以不依赖外部建表。
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS theory(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, en TEXT DEFAULT '',
        field TEXT DEFAULT '心理学', source TEXT DEFAULT '', summary TEXT NOT NULL,
        applies TEXT DEFAULT '', misuse TEXT DEFAULT '', signal TEXT DEFAULT '',
        tags TEXT DEFAULT '', enabled INTEGER DEFAULT 1, created_at TEXT NOT NULL)""")
    n = conn.execute("SELECT COUNT(*) FROM theory").fetchone()[0]
    if n:
        return 0
    now = db.now()
    added = 0
    for t in THEORIES:
        conn.execute(
            "INSERT INTO theory(name,en,field,source,summary,applies,misuse,signal,tags,enabled,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,1,?)",
            (t["name"], t.get("en", ""), t.get("field", "心理学"), t.get("source", ""),
             t["summary"], t.get("applies", ""), t.get("misuse", ""), t.get("signal", ""),
             t.get("tags", ""), now))
        added += 1
    conn.commit()
    return added


def catalog_block(conn, limit=60, compact=True):
    """紧凑索引：让模型知道库里有什么，能引用 [理论#编号]。

    compact=True 时只列「编号 + 名称」，不列适用条件。
    为什么要砍：实测 37 条带适用条件的索引是 1092 字，而每轮 system prompt 总共
    才 7.4k 字——光这一块就占了 15%，而且绝大多数轮次根本用不到具体条目。
    真要用的那几条，会由 match() 单独完整注入，索引只是「目录」。
    """
    rows = db.rows(conn.execute(
        "SELECT id,name,applies FROM theory WHERE enabled=1 ORDER BY field,id LIMIT ?", (limit,)))
    if not rows:
        return "", []
    if compact:
        body = "；".join(f"#{r['id']}{r['name']}" for r in rows)
        lines = ["【本地知识库目录（要引用就写 [理论#编号]；索引里有的别说没有）】", body]
        return "\n".join(lines), [r["id"] for r in rows]
    lines = ["【本地知识库（只列名称与适用条件；你要用它时必须写 [理论#编号]）】"]
    for r in rows:
        lines.append(f"- 理论#{r['id']} {r['name']}：{r['applies']}")
    return "\n".join(lines), [r["id"] for r in rows]


def _bigrams(s):
    """中文没有空格，按字符二元组切。"""
    s = "".join((s or "").split())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def match(conn, query, limit=3):
    """给理论条目打分，取最相关的几条完整注入。

    打分 = 直接命中（名称/标签出现在问题里，或问题出现在名称/标签里）×5
          + 字符二元组重叠数
    为什么不用 FTS：理论库只有几十条，且要按「语义相近」而不是「包含同一个词」命中；
    二元组重叠在这个规模上更稳，也不依赖分词器。短查询（如「拖延」）走直接命中。
    """
    q = "".join((query or "").split())
    if len(q) < 2:
        return []
    qb = _bigrams(q)
    rows = db.rows(conn.execute("SELECT * FROM theory WHERE enabled=1"))
    scored = []
    for r in rows:
        kw = [t for t in (r.get("tags") or "").split() if len(t) >= 2]
        name = r.get("name") or ""
        direct = sum(1 for t in kw if t in q)
        if len(name) >= 2 and (name in q or q in name):
            direct += 2
        hay = " ".join([name, r.get("en") or "", r.get("tags") or "",
                        r.get("applies") or "", r.get("summary") or "", r.get("signal") or ""])
        inter = len(qb & _bigrams(hay)) if len(q) >= 4 else 0
        score = direct * 5 + inter
        if direct or inter >= 3:
            scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [r for _, r in scored[:limit]]


def detail_block(rows):
    if not rows:
        return "", []
    out = ["【匹配到的理论条目（用它们时必须写 [理论#编号]，并区分是理论推断还是事实）】"]
    for r in rows:
        out.append(
            f"[理论#{r['id']}] {r['name']}（{r['field']}）\n"
            f"  核心：{r['summary']}\n"
            f"  适用：{r['applies']}\n"
            f"  你的数据里对应信号：{r['signal']}\n"
            f"  误用风险：{r['misuse']}\n"
            f"  出处：{r['source']}")
    return "\n".join(out), [r["id"] for r in rows]


def get(conn, tid):
    return db.one(conn.execute("SELECT * FROM theory WHERE id=?", (tid,)))
