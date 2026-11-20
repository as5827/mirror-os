# -*- coding: utf-8 -*-
"""演示数据：仅用于开发验证与「先看看长什么样」。

用法：
    python seed_demo.py      # 灌入演示数据
    python seed_demo.py wipe # 清空所有业务数据（保留规则表）
真实使用前请务必 wipe，否则你的画像会被演示数据污染。
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import db  # noqa: E402

MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "DEMO_MARKER")

D = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731

EVENTS = [
    (6, "学习", "花 3 小时搭了个 ArduPilot 环境，跑通了仿真", "今天开始搞无人机了", "按教程装环境，编译通过",
     "仿真起飞成功，很爽", 4, "good", 3, 3, 0, "如果重来，先在纸上画清飞控链路再看代码", "无人机,嵌入"),
    (5, "人际", "老同学第三次找我帮忙做 PPT，我又答应了", "行吧，我帮你弄", "改了两版，对方说再改改",
     "谢谢啊，还有个小地方", 2, "bad", 1, 4, 0, "应该说：这次不行，我在赶自己的事", "边界"),
    (5, "学习", "刷到 LangGraph 教程，跟着跑了个 demo", "这个也很有意思", "复制粘贴跑通",
     "文档里说能编排 agent", 3, "neutral", 3, 2, 0, "", "AI,agent"),
    (4, "金钱", "下单了一台 3D 打印机，2680", "以后可以打零件", "刷信用卡", "已发货", 4, "neutral", 2, 0, 2680,
     "先问自己：这个月真会用到 3 次以上吗", "消费"),
    (4, "职业", "面试了一家公司的算法岗，二面被问住", "我项目里用过 Transformer", "没答出 attention 复杂度推导",
     "回去等通知", 2, "bad", 3, 0, 0, "基础知识不牢，是因为每个方向都只学了皮毛", "面试"),
    (3, "学习", "报了华为 ICT 网络赛道的题，刷了 200 道", "", "背题库", "有些题根本没懂原理", 3, "neutral", 2, 8, 0,
     "", "网络,证书"),
    (3, "精力", "晚上本想学 RISC-V，结果刷短视频到 1 点", "", "刷手机", "", 2, "bad", 0, 0, 0,
     "手机放另一个房间", "注意力"),
    (2, "人际", "学姐借了 500 说下周还，两周了没提", "", "我没提", "她也没提", 2, "bad", 0, 0, 0,
     "直接问一句：那 500 方便的时候还我", "金钱,边界"),
    (2, "学习", "重新打开三个月前建了一半的 Rust 项目", "", "看了 20 分钟，忘了当初写到哪", "", 2, "neutral", 2, 0.3, 0,
     "每个项目开工前先写 README 断点", "Rust"),
    (1, "职业", "决定先不接那个外包，钱不多且要改十版", "这个不接", "礼貌拒绝", "对方说那可惜了", 4, "good", 3, 0, 0,
     "", "决策"),
    (0, "学习", "跟人聊起 HBase 的 region split，能讲清楚", "", "", "对方说我讲得挺明白", 4, "good", 3, 0.5, 0,
     "", "大数据"),
]

TOPICS = [
    # name, domain, started, depth, hours, artifacts, interest, last_touch, mainline, why
    ("HBase / 分布式存储", "后端", 120, 3, 85, 4, 4, 0, 1, "做过一个 region 迁移的完整实验，写过两篇笔记"),
    ("网络工程(华为ICT)", "网络", 55, 2, 40, 1, 3, 3, 0, "为了打比赛拿证书"),
    ("Rust", "后端", 100, 1, 18, 1, 4, 2, 0, "想写更靠底层的代码"),
    ("ArduPilot / 无人机", "嵌入式", 6, 1, 3, 0, 5, 6, 0, "觉得酷，能飞起来"),
    ("LangGraph / AI Agent", "AI", 5, 1, 2, 0, 4, 5, 0, "刷到教程，觉得是趋势"),
    ("3D 打印", "硬件", 4, 0, 0, 0, 3, 4, 0, "买了打印机顺便学"),
]


def wipe(conn):
    for t in ("events", "traits", "topic_sessions", "topics", "decisions", "interactions", "people",
              "spend", "alert_acks", "reports", "memory",
              "messages", "summaries", "conversations", "reference"):
        conn.execute(f"DELETE FROM {t}")
    # 回归测试会临时建一条测试理论，顺手清掉，免得混进正式知识库
    conn.execute("DELETE FROM theory WHERE name LIKE '测试%'")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    try:
        os.remove(MARKER)
    except OSError:
        pass


def seed(conn):
    for (d, dom, title, words, acts, resp, emo, out, ini, hrs, money, replay, tags) in EVENTS:
        conn.execute(
            "INSERT INTO events(on_date,domain,title,my_words,my_actions,other_response,emotion,outcome,"
            "initiative,hours,money,replay,tags,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (D(d), dom, title, words, acts, resp, emo, out, ini, hrs, money, replay, tags, db.now()))

    for (name, dom, start_days, depth, hours, art, interest, touch, main, why) in TOPICS:
        cur = conn.execute(
            "INSERT INTO topics(name,domain,started_on,depth_level,hours,artifacts,interest,last_touch_on,"
            "last_depth_up_on,mainline,status,why,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, dom, D(start_days), depth, hours, art, interest, D(touch),
             D(start_days) if depth >= 2 else D(max(touch, start_days)), main, "active", why, db.now()))
        tid = cur.lastrowid
        # 伪造投入曲线：早期密集，后期稀疏（正是「浅尝」的特征）
        plan = [(start_days, 0.3), (max(start_days - 2, 0), 0.5), (max(start_days - 5, 0), 0.4),
                (max(start_days - 9, 0), 0.3), (max(start_days - 20, 0), 0.2), (max(start_days - 35, 0), 0.1)]
        if depth >= 3:
            plan = [(10, 1.0), (8, 1.2), (5, 0.8), (3, 1.0), (1, 0.6), (start_days, 0.5)]
        for (days, h) in plan:
            if days < 0:
                continue
            conn.execute("INSERT INTO topic_sessions(topic_id,on_date,hours,note,created_at) VALUES(?,?,?,?,?)",
                         (tid, D(days), max(hours / 12.0, 0.2) * h, "", db.now()))

    pid = conn.execute("INSERT INTO people(name,relation,status,last_contact_on,notes,created_at) "
                       "VALUES(?,?,?,?,?,?)",
                       ("老同学 A", "大学室友", "normal", D(5), "做市场，常需要我帮忙做材料", db.now())).lastrowid
    for (d, dr, w, what) in [(60, "take", 1, "请我吃过饭"), (30, "give", 3, "帮他做述职 PPT"),
                             (20, "give", 4, "帮他改第二版 PPT"), (12, "give", 3, "帮他做竞品分析"),
                             (5, "give", 4, "第三次 PPT，改两轮")]:
        conn.execute("INSERT INTO interactions(person_id,on_date,direction,weight,what,created_at) "
                     "VALUES(?,?,?,?,?,?)", (pid, D(d), dr, w, what, db.now()))

    pid2 = conn.execute("INSERT INTO people(name,relation,status,last_contact_on,notes,created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        ("学姐 B", "同校", "normal", D(20), "借过钱", db.now())).lastrowid
    for (d, dr, w, what) in [(25, "give", 5, "借 500 至今未还"), (20, "neutral", 1, "问候了一句")]:
        conn.execute("INSERT INTO interactions(person_id,on_date,direction,weight,what,created_at) "
                     "VALUES(?,?,?,?,?,?)", (pid2, D(d), dr, w, what, db.now()))

    pid3 = conn.execute("INSERT INTO people(name,relation,status,last_contact_on,notes,created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        ("导师 C", "导师", "normal", D(3), "给方向建议，对等", db.now())).lastrowid
    for (d, dr, w, what) in [(40, "take", 4, "给了我一篇关键论文"), (15, "take", 3, "帮我改简历"),
                             (3, "give", 2, "帮他跑了一组数据")]:
        conn.execute("INSERT INTO interactions(person_id,on_date,direction,weight,what,created_at) "
                     "VALUES(?,?,?,?,?,?)", (pid3, D(d), dr, w, what, db.now()))

    conn.execute("INSERT INTO spend(on_date,item,amount,category,need,cool_until,status,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?)",
                 (D(4), "3D 打印机", 2680, "硬件", 2, D(1), "pending", db.now()))
    conn.execute("INSERT INTO spend(on_date,item,amount,category,need,status,created_at) "
                 "VALUES(?,?,?,?,?,?,?)", (D(10), "网课订阅", 299, "学习", 3, "approved", db.now()))

    crit = [{"name": "长期回报", "weight": 5}, {"name": "兴趣强度", "weight": 4},
            {"name": "现有基础", "weight": 3}, {"name": "可变现速度", "weight": 2}]
    opts = [{"name": "深挖 HBase/分布式存储", "scores": {"长期回报": 8, "兴趣强度": 7, "现有基础": 9, "可变现速度": 6},
             "note": "已有 85h 与 4 个交付物"},
            {"name": "转攻 ArduPilot/无人机", "scores": {"长期回报": 7, "兴趣强度": 9, "现有基础": 2, "可变现速度": 3},
             "note": "兴趣最高但零基础"},
            {"name": "继续广撒网逐个试", "scores": {"长期回报": 3, "兴趣强度": 6, "现有基础": 5, "可变现速度": 2},
             "note": "现状"}]
    second = [{"option": "深挖 HBase/分布式存储", "chain": [
        "半年内能写出可复用的存储组件",
        "在团队里成为这块的默认人选，机会开始找我",
        "把『什么都会一点』的标签换成『这块最懂』，议价权来自稀缺性"]}]
    rej = [{"option": "转攻 ArduPilot/无人机", "reason": "兴趣 9 分但基础 2 分，且已有 4 个方向在同时消耗，再开一条只会重复浅尝"},
           {"option": "继续广撒网逐个试", "reason": "过去 100 天已经证伪：6 个方向平均深度 1.3，没有一个到能产出"}]
    conn.execute(
        "INSERT INTO decisions(on_date,title,kind,context,criteria,options,second_order,stop_loss,rejected,"
        "chosen,reversibility,review_due,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (D(1), "未来一年把深度押在哪个方向", "方向选择",
         "同时有 6 条线在跑，HBase 有基础、无人机有兴趣、AI Agent 有热度。精力只够押一条。",
         json.dumps(crit, ensure_ascii=False), json.dumps(opts, ensure_ascii=False),
         json.dumps(second, ensure_ascii=False),
         "如果 3 个月内 HBase 方向没有产出可展示的东西（组件/文章/内部复用），立即视为押错并重开决策",
         json.dumps(rej, ensure_ascii=False), "深挖 HBase/分布式存储", "半可逆",
         (date.today() + timedelta(days=60)).isoformat(), "decided", db.now()))

    conn.execute("INSERT INTO traits(name,direction,statement,evidence,blind_spot,cost,counter,confidence,"
                 "status,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 ("以新奇替代深耕", "有害",
                  "当某个方向进入需要重复练习的平台期时，你会立刻被新工具的教程吸引，用『开始学习』的快感替代『完成掌握』的枯燥。",
                  json.dumps([1, 3, 9, 11]), "你把『了解过』记成了『会』，所以每次面试都卡在推导细节。",
                  "已同时在 6 个方向投入，总深度不足以通过一次二面。",
                  "新主题进入前 14 天不设立项；任何新方向必须先封存一条旧主线并写理由。",
                  0.82, "draft", "ai", db.now()))
    conn.commit()


def main():
    db.init()
    conn = db.connect()
    if len(sys.argv) > 1 and sys.argv[1] == "wipe":
        wipe(conn)
        print("已清空所有业务数据（规则表保留）。")
        return
    seed(conn)
    os.makedirs(os.path.dirname(MARKER), exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as f:
        f.write("演示数据标记。启动.bat 靠这个文件判断是否需要提示清除。\n")
    print("演示数据已灌入：", {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                          for t in ("events", "topics", "people", "interactions", "decisions", "spend", "traits")})
    print("提醒：正式使用前运行  python seed_demo.py wipe")


if __name__ == "__main__":
    main()
