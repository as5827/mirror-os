# -*- coding: utf-8 -*-
"""个人画像：把「你是谁」从代码里挪出来，让别人也能用自己的。

原来提示词里硬编码了「高 Ti、厌恶模糊与悬置、要因果闭环与对等互惠」——
那是原作者的性格画像，别人部署后会被强行套上。所以改成读本机的 persona.txt：
  - 文件不存在 → 用通用默认值（不假设任何性格）
  - 文件存在   → 用你写的
persona.txt 已在 .gitignore 里，不会随仓库分发。
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSONA_PATH = os.path.join(BASE_DIR, "persona.txt")

DEFAULT = (
    "一个务实、重视因果闭环与对等互惠的人。他讨厌被敷衍，也讨厌模糊的结论；"
    "宁可听「证据不足」，也不要听安慰性的空话。"
)


def load():
    """返回画像描述（单行，用于拼进提示词）。"""
    try:
        with open(PERSONA_PATH, "r", encoding="utf-8") as f:
            txt = " ".join(f.read().split())
        if len(txt) >= 8:
            return txt
    except OSError:
        pass
    return DEFAULT


def is_custom():
    try:
        return os.path.exists(PERSONA_PATH) and len(load()) >= 8 and load() != DEFAULT
    except OSError:
        return False
