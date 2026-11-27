# -*- coding: utf-8 -*-
"""端到端接口测试：真起 HTTP 请求，覆盖鉴权、增删改、以及三条铁律的强制校验。

隔离设计：自己拉起一个测试服务（独立端口 + 独立数据库 data/test.db），
测完就杀掉并删库。**不会碰 data/mirror.db 里的真实记录**。
为什么必须这样：测试是走 HTTP 的，光在本进程设环境变量影响不到已经跑着的服务，
那样会把你真实的库灌满测试数据（踩过）。
"""
import atexit
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TEST_PORT = 8798
TEST_DB = os.path.join("data", "test.db")
BASE = f"http://127.0.0.1:{TEST_PORT}"

PIN = None
_env = os.path.join(HERE, ".env")
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8"):
        if line.startswith("MIRROR_PIN="):
            PIN = line.split("=", 1)[1].strip()

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
PASS, FAIL = [], []


def _port_free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _start_test_server():
    """起一个用独立数据库的测试服务；端口被占则直接报错退出。"""
    # 清掉上次残留
    for suffix in ("", "-wal", "-shm"):
        f = os.path.join(HERE, TEST_DB + suffix)
        if os.path.exists(f):
            os.remove(f)
    if not _port_free(TEST_PORT):
        print(f"[X] 端口 {TEST_PORT} 被占用，无法启动测试服务。请先关掉它再跑。")
        sys.exit(2)
    env = dict(os.environ)
    env.update({
        "MIRROR_DB": TEST_DB,
        "MIRROR_PORT": str(TEST_PORT),
        "MIRROR_OPEN_BROWSER": "0",
        "MIRROR_CONSOLE": "0",
        "PYTHONIOENCODING": "utf-8",
    })
    proc = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=HERE, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):                      # 最多等 24 秒
        if proc.poll() is not None:
            print("[X] 测试服务启动失败（进程已退出）")
            sys.exit(2)
        if not _port_free(TEST_PORT):
            return proc
        time.sleep(0.2)
    proc.terminate()
    print("[X] 测试服务启动超时")
    sys.exit(2)


def _stop_test_server(proc):
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    for suffix in ("", "-wal", "-shm"):
        f = os.path.join(HERE, TEST_DB + suffix)
        try:
            if os.path.exists(f):
                os.remove(f)
        except OSError:
            pass


_srv = _start_test_server()
atexit.register(lambda: _stop_test_server(_srv))   # 中途断言失败也不留残进程


def call(method, path, body=None, expect=200, label=""):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        # 聊天接口要真调模型，30 秒不够（会 TypeError 变成超时失败）
        with op.open(req, timeout=180) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    ok = (code == expect)
    tag = f"{method} {path}"
    (PASS if ok else FAIL).append(f"{tag} -> {code} (want {expect})")
    print(("  OK  " if ok else " FAIL ") + f"{tag} -> {code}" + ("" if ok else f" want {expect}"))
    try:
        return json.loads(raw)
    except ValueError:
        return {"_raw": raw[:80]}


print("=== 1. 静态与鉴权 ===")
call("GET", "/", expect=200)
call("GET", "/static/style.css", expect=200)
call("GET", "/static/app.js", expect=200)
call("GET", "/manifest.webmanifest", expect=200)
call("GET", "/api/dashboard", expect=401)              # 未登录必须拒绝
call("POST", "/api/login", {"pin": "000000"}, expect=401)
call("POST", "/api/login", {"pin": PIN}, expect=200)
me = call("GET", "/api/me", expect=200)
print("     ai available:", me.get("ai"))

print("=== 2. 读接口 ===")
call("GET", "/api/dashboard")
call("GET", "/api/events")
call("GET", "/api/topics")
call("GET", "/api/decisions")
call("GET", "/api/people")
call("GET", "/api/spend")
call("GET", "/api/alerts")
call("GET", "/api/rules")
call("GET", "/api/traits")
call("GET", "/api/export")

print("=== 3. 事件增删 ===")
ev = call("POST", "/api/events", {"title": "测试事件：晚上又刷了 2 小时短视频", "domain": "精力",
                                  "emotion": 2, "outcome": "bad", "initiative": 0})
call("POST", "/api/events", {}, expect=400)            # 无标题必须拒绝
call("GET", "/api/events?domain=" + urllib.parse.quote("精力"))

print("=== 4. 铁律校验：决策必须写否决理由与三层二阶效应 ===")
bad = {"title": "测试决策：要不要接这个外包", "kind": "进入",
       "criteria": [{"name": "钱", "weight": 3}, {"name": "时间", "weight": 5}],
       "options": [{"name": "接", "scores": {"钱": 8, "时间": 2}},
                   {"name": "不接", "scores": {"钱": 2, "时间": 9}}],
       "chosen": "不接", "rejected": [{"option": "接", "reason": "太累"}],  # 少于 8 字
       "second_order": [], "status": "decided"}
call("POST", "/api/decisions", bad, expect=400)
bad2 = dict(bad, rejected=[{"option": "接", "reason": "时间成本高于收益，且甲方已改稿十版，契约不可控"}],
            second_order=[])
call("POST", "/api/decisions", bad2, expect=400)       # 缺三层二阶效应
good = dict(bad2, second_order=[{"option": "不接", "chain": [
    "这个月能专心推进自己的主线",
    "避免了十版改稿带来的情绪消耗",
    "把时间换成了长期方向的积累，议价权上升"]}])
d = call("POST", "/api/decisions", good)
dec_id = d.get("id")
call("POST", f"/api/decisions/{dec_id}/review", {"actual": "确实没接上", "score": 2, "lesson": "先算时间成本再谈钱"})
call("POST", f"/api/decisions/{dec_id}/review", {"actual": "", "score": 1, "lesson": "短"}, expect=400)

print("=== 5. 支出冷静期 ===")
sp = call("POST", "/api/spend", {"item": "机械键盘", "amount": 1200, "need": 2})
print("     ->", sp.get("status"), sp.get("cool_until"), sp.get("note"))
call("POST", f"/api/spend/{sp['id']}/verdict", {"status": "approved"}, expect=400)  # 冷静期内不许批
call("POST", f"/api/spend/{sp['id']}/verdict", {"status": "cancelled", "reason": "冷静完发现没必要"}, expect=200)
call("POST", "/api/spend", {"item": "口香糖", "amount": 12})   # 低于门槛直接记录

print("=== 6. 主题与封存必须写理由 ===")
t = call("POST", "/api/topics", {"name": "测试主题 RISC-V", "domain": "嵌入式", "depth_level": 1})
call("POST", "/api/topics", {"id": t["id"], "name": "测试主题 RISC-V", "status": "parked",
                             "archive_reason": "懒"}, expect=400)
call("POST", "/api/topics", {"id": t["id"], "name": "测试主题 RISC-V", "status": "parked",
                             "archive_reason": "只跑了两个 demo，没有产出欲望，不如把时间给主线"})
call("POST", f"/api/topics/{t['id']}/session", {"hours": 2, "note": "看了手册前两章"})

print("=== 7. 人际 ===")
p = call("POST", "/api/people", {"name": "测试对象 D", "relation": "同事"})
call("POST", f"/api/people/{p['id']}/interaction", {"direction": "give", "weight": 5, "what": "帮他值了两天班"})
call("POST", f"/api/people/{p['id']}/interaction", {"direction": "take", "weight": 0.5, "what": "请了杯奶茶"})
call("POST", "/api/people", {"id": p["id"], "name": "测试对象 D", "status": "cut"}, expect=400)  # 缺理由
call("POST", "/api/people", {"id": p["id"], "name": "测试对象 D", "status": "cut",
                             "status_reason": "三个月内三次单向索要，零回馈"})

print("=== 8. 画像必须挂真实证据 ===")
call("POST", "/api/traits", {"name": "假模式", "statement": "无证据断言"}, expect=400)
call("POST", "/api/traits", {"name": "假模式", "statement": "引用不存在的事件", "evidence": [9999]}, expect=400)
tr = call("POST", "/api/traits", {"name": "回避结算", "statement": "别人欠你东西时你不提",
                                  "evidence": [1], "direction": "有害"})
call("POST", f"/api/traits/{tr['id']}/decide", {"status": "rejected"}, expect=400)
call("POST", f"/api/traits/{tr['id']}/decide", {"status": "rejected", "reason": "样本只有一条，不足以成立"})

print("=== 9. 预警处置必须写说明 ===")
al = call("GET", "/api/alerts").get("alerts", [])
if al:
    fp = al[0]["fingerprint"]
    call("POST", "/api/alerts/ack", {"fingerprint": fp, "status": "handled", "note": "懒得写"}, expect=400)
    call("POST", "/api/alerts/ack", {"fingerprint": fp, "status": "handled",
                                     "note": "已发消息问对方什么时候推进"})
else:
    print("     (当前无预警，跳过)")

print("=== 10. 规则可改 ===")
call("POST", "/api/rules", {"rules": [{"key": "givetake_ratio", "value": 0.55, "enabled": True}]})
rs = call("GET", "/api/rules").get("rules", [])
print("     givetake_ratio =", [r["value"] for r in rs if r["key"] == "givetake_ratio"])

print("=== 11. 长期记忆（注入 AI 的前提）===")
call("GET", "/api/memory")
call("POST", "/api/memory", {"statement": "太短"}, expect=400)          # 少于 6 字拒绝
m = call("POST", "/api/memory", {"statement": "我在平台期会想换方向，那不是发现更好的东西，是想逃离枯燥",
                                 "kind": "自我认知"})
call("POST", f"/api/memory/{m['id']}/retire", {}, expect=400)              # 缺理由拒绝
call("POST", f"/api/memory/{m['id']}/retire", {"reason": "懒"}, expect=400)  # 理由太短
call("POST", f"/api/memory/{m['id']}/retire", {"reason": "后来发现这不是普遍规律，只在编程类任务上成立"})
mem = call("GET", "/api/memory").get("memory", [])
print("     记忆条数:", len(mem), "| 状态:", [x["status"] for x in mem])

print("=== 12. 画像去重标记 dup_of ===")
base = call("POST", "/api/traits", {"name": "测试基准结论", "statement": "方向进入平台期会被新工具吸引",
                                    "evidence": [1], "direction": "有害"})
call("POST", f"/api/traits/{base['id']}/decide", {"status": "accepted"})
dup = call("POST", "/api/traits", {"name": "换措辞版本", "statement": "同一个意思换个说法",
                                   "evidence": [1], "dup_of": base["id"]})
trs = call("GET", "/api/traits").get("traits", [])
d = [t for t in trs if t["id"] == dup["id"]]
print("     dup_of =", d[0].get("dup_of"), "| dup_name =", d[0].get("dup_name"))

print("=== 13. 记忆确认与巩固 ===")
call("POST", "/api/memory/999999/approve", {}, expect=404)      # 不存在
# 无 30 天前事件时，巩固应报错而不是静默成功
r = call("POST", "/api/analyze/consolidate", {"older_than_days": 30}, expect=400)
print("     无旧事件时:", str(r.get("error"))[:40])
# 造两条 60 天前的事件再试
for i in range(2):
    call("POST", "/api/events", {"on_date": "2025-01-0" + str(i + 1), "domain": "学习",
                                 "title": f"很久以前又开了个项目{i}", "my_actions": "写了几天就停了"})
r2 = call("POST", "/api/analyze/consolidate", {"older_than_days": 30})
if "error" not in r2:
    print("     巩固结果: 覆盖", r2.get("covered_events"), "条, 草稿",
          len(r2.get("created") or []), "条")
    mem = call("GET", "/api/memory").get("memory", [])
    drafts = [m for m in mem if m["status"] == "draft"]
    print("     draft 记忆:", len(drafts))
    if drafts:
        call("POST", f"/api/memory/{drafts[0]['id']}/approve", {})
        mem2 = call("GET", "/api/memory").get("memory", [])
        print("     确认后 active 数:",
              len([m for m in mem2 if m["status"] == "active"]))

print("=== 14. 对话 / Skill / 引用校验 ===")
sk = call("GET", "/api/skills")
print("     skill 数:", len(sk.get("skills") or []))
assert len(sk.get("skills") or []) >= 5
ct = call("POST", "/api/conversations", {})
cid2 = ct.get("id")
call("POST", f"/api/conversations/{cid2}/skill", {"skill": "weekly-review"})
call("POST", f"/api/conversations/{cid2}/skill", {"skill": "不存在的skill"}, expect=400)
call("GET", f"/api/conversations/{cid2}")
call("POST", f"/api/chat/{cid2}/send", {"text": ""}, expect=400)   # 空消息拒绝
r3 = call("POST", f"/api/chat/{cid2}/send", {"text": "一句话说说我现在最该盯的一件事。"})
if "error" not in r3:
    print("     上下文:", r3.get("context"))
    print("     引用:", len(r3.get("refs") or []), "伪造:", len(r3.get("bad_refs") or []))
call("GET", "/api/messages/search?q=" + urllib.parse.quote("盯"))
call("GET", "/api/health")
call("POST", "/api/backup")

print("=== 15. 知识库 ===")
th = call("GET", "/api/theory")
print("     条数:", len(th.get("theory") or []), "| 领域:", th.get("fields"))
assert len(th.get("theory") or []) >= 30, "知识库没灌进去"
th2 = call("GET", "/api/theory?q=" + urllib.parse.quote("拖延"))
print("     搜「拖延」命中:", [t["name"] for t in (th2.get("theory") or [])])
call("POST", "/api/theory", {"name": "测试条目", "summary": "太短"}, expect=400)      # 核心太短
call("POST", "/api/theory", {"name": "测试条目", "summary": "这是一条足够长的核心描述文本", "misuse": ""}, expect=400)  # 缺误用风险
tid = call("POST", "/api/theory", {"name": "测试条目", "field": "方法论",
                                   "summary": "这是一条用于回归测试的核心描述文本",
                                   "misuse": "只用于测试，别当真"}).get("id")
call("POST", f"/api/theory/{tid}/toggle", {})
call("POST", "/api/theory/999999/toggle", {}, expect=404)

print("=== 16. 联网检索（只测确定性部分，真实联网见 devwebtest.py）===")
call("GET", "/api/web/check")
cw = call("POST", "/api/conversations", {})
cwd = cw.get("id")
call("POST", f"/api/conversations/{cwd}/web", {"on": True})
call("POST", f"/api/conversations/{cwd}/web", {"on": False})
call("POST", "/api/web/search", {"q": "x"}, expect=400)          # 检索词太短
call("POST", "/api/theory/999999/verify", {}, expect=404)         # 条目不存在

print("=== 17. 共情体例开关 ===")
cc = call("POST", "/api/conversations", {})
ccid = cc.get("id")
r = call("POST", f"/api/conversations/{ccid}/comfort", {"mode": "off"})
print("     mode ->", r.get("mode"))
call("POST", f"/api/conversations/{ccid}/comfort", {"mode": "bogus"}, expect=400)
call("POST", f"/api/conversations/{ccid}/comfort", {"mode": "on"})
call("POST", f"/api/conversations/{ccid}/comfort", {"mode": "auto"})
d17 = call("GET", f"/api/conversations/{ccid}")
print("     会话字段 comfort =", (d17.get("conversation") or {}).get("comfort"))

print("\n" + "=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for f in FAIL:
    print("  FAIL:", f)
_stop_test_server(_srv)
print("  已关闭测试服务并删除 data/test.db（你的真实数据库未受影响）")
sys.exit(0 if not FAIL else 1)
