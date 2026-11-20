# -*- coding: utf-8 -*-
"""部署验收：模拟「别人第一次拿到这个项目」。

从 GitHub 克隆到临时目录 -> 不给任何配置直接启动 -> 逐项检查能否正常用。

重点验证知识库（theory）是否灌满：曾经只测 health 和首页，
漏掉过 theory 表缺失导致知识库全空的 bug，坏版本因此被推上去过。

用法：python deploycheck.py
可选环境变量：MIRROR_REPO / MIRROR_CHECK_PORT / MIRROR_PROXY
"""
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PROXY = os.environ.get("MIRROR_PROXY") or ""
REPO = os.environ.get("MIRROR_REPO", "https://github.com/as5827/mirror-os.git")
DIR = os.path.join(tempfile.gettempdir(), "mirror_deploycheck")
PORT = int(os.environ.get("MIRROR_CHECK_PORT", "8801"))
BASE = f"http://127.0.0.1:{PORT}"

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
ok_count, bad = 0, []


def check(name, cond, extra=""):
    global ok_count
    if cond:
        ok_count += 1
        print(f"  OK   {name} {extra}")
    else:
        bad.append(name)
        print(f"  FAIL {name} {extra}")


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


shutil.rmtree(DIR, ignore_errors=True)
print("=== 1. 克隆 ===")
gitcfg = ["-c", f"http.proxy={PROXY}", "-c", f"https.proxy={PROXY}"] if PROXY else []
r = subprocess.run(["git"] + gitcfg + ["clone", "-q", REPO, DIR],
                   capture_output=True, text=True)
check("git clone 成功", r.returncode == 0, r.stderr[:120].strip())
if r.returncode != 0:
    sys.exit(2)

print("\n=== 2. 私密文件不得随仓库分发 ===")
for x in (".env", "data", "persona.txt", "_shots", "_shots_preview"):
    check(f"{x} 不存在", not os.path.exists(os.path.join(DIR, x)))

print("\n=== 3. 首次启动（不提供任何配置）===")
env = dict(os.environ)
env.update({"MIRROR_PORT": str(PORT), "MIRROR_OPEN_BROWSER": "0",
            "PYTHONIOENCODING": "utf-8", "MIRROR_CONSOLE": "0"})
for k in ("MIRROR_DB", "DEEPSEEK_API_KEY", "MIRROR_PIN", "MIRROR_SECRET"):
    env.pop(k, None)                       # 模拟干净的机器
proc = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=DIR, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    up = False
    for _ in range(80):
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(BASE + "/", timeout=2) as resp:
                if resp.status == 200:
                    up = True
                    break
        except Exception:
            time.sleep(0.3)
    check("服务起来了", up)

    print("\n=== 4. 自动生成的配置 ===")
    envf = os.path.join(DIR, ".env")
    check(".env 自动生成", os.path.exists(envf))
    pin = ""
    if os.path.exists(envf):
        txt = open(envf, encoding="utf-8").read()
        for line in txt.splitlines():
            if line.startswith("MIRROR_PIN="):
                pin = line.split("=", 1)[1].strip()
        check("生成了 6 位 PIN", len(pin) == 6 and pin.isdigit(), f"PIN={pin}")
        check("生成了独立会话密钥", "MIRROR_SECRET=" in txt)
    check("数据库自动创建", os.path.exists(os.path.join(DIR, "data", "mirror.db")))

    print("\n=== 5. 登录（用他自己生成的 PIN）===")
    st, _ = http("POST", "/api/login", {"pin": pin})
    check("登录成功", st == 200, f"HTTP {st}")

    print("\n=== 6. ★ 知识库必须灌满 ===")
    st, body = http("GET", "/api/theory")
    ths = body.get("theory") or []
    check("GET /api/theory 返回 200", st == 200, f"HTTP {st}")
    check("理论条目 >= 30 条", len(ths) >= 30, f"实际 {len(ths)} 条")
    fields = {}
    for t in ths:
        f = t.get("field") or "?"
        fields[f] = fields.get(f, 0) + 1
    check("覆盖多个学科", len(fields) >= 3, str(fields))

    print("\n=== 7. 其他核心接口 ===")
    for path in ("/api/events", "/api/rules", "/api/dashboard", "/api/skills", "/api/memory"):
        st, _ = http("GET", path)
        check(f"GET {path}", st == 200, f"HTTP {st}")
    st, body = http("GET", "/api/skills")
    n = len(body.get("skills") or [])
    check("skill 数 = 7", n == 7, f"实际 {n}")

    print("\n=== 8. 写入一条记录（确认可正常使用）===")
    st, _ = http("POST", "/api/events", {
        "on_date": "2026-01-01", "domain": "学习", "title": "克隆部署验收事件",
        "my_actions": "写下这条用于验证新部署可写入"})
    check("POST /api/events", st == 200, f"HTTP {st}")
    st, body = http("GET", "/api/events")
    check("事件可读回", st == 200 and len(body.get("events") or []) >= 1,
          f"{len(body.get('events') or [])} 条")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

print("\n" + "=" * 58)
print(f"验收: 通过 {ok_count} 项，失败 {len(bad)} 项")
for b in bad:
    print("  FAIL:", b)
shutil.rmtree(DIR, ignore_errors=True)
sys.exit(0 if not bad else 1)
