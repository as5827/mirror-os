# -*- coding: utf-8 -*-
"""上下文重复检查：同一条用户消息在送给模型的 payload 里只能出现一次。

做法：把 chat.ai.chat 换成假的，截获真正发给模型的 messages 列表，数一句话出现几次。
用独立测试库，不碰真实数据。

为什么要有这个：send() 先把用户消息落库再拼上下文，如果拼上下文时不排除刚落库的
那条，它就会既出现在历史里、又被手动 append 一次。模型会回答「你发了两遍」——
而用户只发了一遍。这个问题在界面上看不出来，只能从 payload 里查。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["MIRROR_DB"] = os.path.join("data", "test_dup.db")
for suffix in ("", "-wal", "-shm"):
    f = os.path.join(HERE, "data", "test_dup.db" + suffix)
    if os.path.exists(f):
        os.remove(f)
sys.path.insert(0, HERE)

from app import chat, db  # noqa: E402

db.init()
conn = db.connect()

captured = []


real_ai = chat.ai


class FakeAI:
    """只覆盖 chat()，其余（recall_events / _redact / 等）转发给真实的 ai 模块。"""

    def chat(self, messages, **kw):
        captured.append([dict(m) for m in messages])
        return "收到。"

    def __getattr__(self, name):
        return getattr(real_ai, name)


chat.ai = FakeAI()

ok, bad = 0, []


def check(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  OK   {name} {extra}")
    else:
        bad.append(name)
        print(f"  FAIL {name} {extra}")


try:
    # 建会话
    conn.execute("INSERT INTO conversations(title,created_at,updated_at) VALUES('测试',?,?)",
                 (db.now(), db.now()))
    conn.commit()
    conv = db.one(conn.execute("SELECT * FROM conversations ORDER BY id DESC LIMIT 1"))
    cid = conv["id"]

    print("=== 单轮：发一句话，看模型收到几次 ===")
    text = "你是否可以根据以上信息对我进行简单画像？"
    chat.send(conn, cid, text)
    msgs = captured[-1]
    user_msgs = [m for m in msgs if m["role"] == "user"]
    hits = sum(1 for m in user_msgs if text in m["content"])
    check("用户消息只出现 1 次", hits == 1, f"实际 {hits} 次")
    check("消息序列以 system 开头", msgs[0]["role"] == "system")
    check("最后一条是 user", msgs[-1]["role"] == "user", msgs[-1]["role"])
    check("总条数合理（system+1user=2）", len(msgs) == 2, f"实际 {len(msgs)}")
    print("       实际序列:", [(m["role"], m["content"][:24]) for m in msgs])

    print("\n=== 多轮：连发三句，每句都只能出现一次 ===")
    for i, t in enumerate(["第二句话", "第三句话", "第四句话"], start=2):
        chat.send(conn, cid, t)
        ms = captured[-1]
        um = [m for m in ms if m["role"] == "user"]
        h = sum(1 for m in um if t in m["content"])
        check(f"第{i}轮「{t}」只出现 1 次", h == 1, f"实际 {h} 次")
        check(f"第{i}轮总条数 = system+{i*2-1}", len(ms) == 1 + (i * 2 - 1), f"实际 {len(ms)}")

    print("\n=== 历史确实被带上了（不是靠丢弃历史来去重）===")
    ms = captured[-1]
    joined = " ".join(m["content"] for m in ms)
    for t in ["你是否可以根据以上信息对我进行简单画像？", "第二句话", "第三句话", "第四句话"]:
        check(f"上下文含「{t[:10]}」", t in joined)
finally:
    chat.ai = real_ai
    conn.close()

print("\n" + "=" * 54)
print(f"通过 {ok} 项，失败 {len(bad)} 项")
for b in bad:
    print("  FAIL:", b)
for suffix in ("", "-wal", "-shm"):
    f = os.path.join(HERE, "data", "test_dup.db" + suffix)
    if os.path.exists(f):
        try:
            os.remove(f)
        except OSError:
            pass
sys.exit(0 if not bad else 1)
