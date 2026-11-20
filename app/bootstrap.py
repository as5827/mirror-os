# -*- coding: utf-8 -*-
"""首次启动引导：缺 .env 时自动生成一份带随机口令的配置。

为什么需要这个（而不是仓库里带一个 .env）：
  如果仓库里带 .env，所有部署者会共用同一个 PIN 和同一个会话签名密钥。
  共用签名密钥意味着一个人可以伪造另一个人的登录 Cookie——这是真实漏洞，不是理论问题。
  所以 .env 必须本机生成、本机独有，仓库里只放 .env.example 模板。

不 import 任何 app 模块，保证它能在读取环境变量之前被安全调用。
"""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")

TEMPLATE = """# 镜 MirrorOS 本地配置 —— 本文件由程序自动生成，请勿提交到任何仓库
# 你的数据全部留在这台机器上：data/mirror.db

# 填你自己的 DeepSeek API Key（https://platform.deepseek.com）
DEEPSEEK_API_KEY=

# 网页登录口令（手机和电脑打开时输入）
MIRROR_PIN={pin}

# 会话签名密钥（本机独有，不要告诉别人）
MIRROR_SECRET={secret}

MIRROR_PORT=8765
MIRROR_OPEN_BROWSER=1

# 可选：填入邮箱可让 OpenAlex 进入礼貌池，联网检索配额更宽
# MIRROR_MAILTO=you@example.com
"""


def ensure_env(quiet=False):
    """确保 .env 存在。返回生成的 PIN（已存在则返回 None）。"""
    if os.path.exists(ENV_PATH):
        return None

    pin = "".join(secrets.choice("0123456789") for _ in range(6))
    secret = secrets.token_hex(16)
    try:
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write(TEMPLATE.format(pin=pin, secret=secret))
    except OSError as e:
        if not quiet:
            print(f"[!] 无法生成 .env（{e}），请手动复制 .env.example 为 .env")
        return None

    if not quiet:
        print("=" * 60)
        print("  首次启动：已生成你的本地配置 .env")
        print(f"  登录 PIN：{pin}   （手机/电脑打开网页时输入）")
        print("  下一步：把 DeepSeek API Key 填进 .env 的 DEEPSEEK_API_KEY")
        print("  数据位置：data/mirror.db —— 只在这台机器上，不会上传")
        print("=" * 60)
    return pin
