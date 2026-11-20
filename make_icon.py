# -*- coding: utf-8 -*-
"""生成程序图标：字标「镜」，按尺寸分三档细节。

为什么分档而不是统一缩图：
  32px 下细环和发光会糊成一团噪点。所以 >=128 带细环+发光，64/48 只留发光，
  32/16 用纯色字并放大一点，保证任何尺寸都能一眼认出。

产物：
  mirror-app.ico   桌面快捷方式用（新文件名，绕开 Windows 图标缓存）
  mirror.ico       同一份的旧名副本，保持文档一致性
  icon-256.png     预览用
  static/icon-*.png 网页与手机主屏幕图标，和桌面保持一致
"""
import io
import os
import shutil

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.iconwriter import describe, write_ico

S = 1024
ROOT = os.path.dirname(os.path.abspath(__file__))
REFRESH_ICO = os.path.join(ROOT, "mirror-app.ico")
LEGACY_ICO = os.path.join(ROOT, "mirror.ico")
PNG_256 = os.path.join(ROOT, "icon-256.png")

TEAL = (63, 182, 168)
BG_TOP = (36, 41, 50)
BG_BOT = (13, 15, 19)

FONT_CANDIDATES = (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf",
                   r"C:\Windows\Fonts\msyh.ttc")


def vgrad(w, h, top, bot):
    g = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        g.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    return g.resize((w, h), Image.BILINEAR)


def badge():
    pad, r = 26, int(S * 0.235)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, S - pad, S - pad], radius=r, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(vgrad(S, S, BG_TOP, BG_BOT).convert("RGBA"), (0, 0), mask)
    edge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(edge)
    d.rounded_rectangle([pad + 3, pad + 3, S - pad - 3, S - pad - 3], radius=r - 2,
                        outline=(130, 210, 200, 40), width=3)
    d.arc([pad + 3, pad + 3, S - pad - 3, S - pad - 3], start=196, end=340,
          fill=(190, 245, 238, 70), width=4)
    img.alpha_composite(edge)
    return img


def load_font(px):
    for fp in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(fp, px)
        except Exception:
            continue
    raise SystemExit("找不到中文字体，无法生成字标图标")


def build(size_px, detail="full"):
    """detail: full(环+发光) / mid(仅发光) / flat(纯色字)。"""
    img = badge()
    cx = cy = S // 2

    if detail == "full":
        R = int(S * 0.345)
        ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse([cx - R, cy - R, cx + R, cy + R],
                                     outline=(TEAL[0], TEAL[1], TEAL[2], 78),
                                     width=int(R * 0.044))
        img.alpha_composite(ring)

    scale = {"full": 0.545, "mid": 0.575, "flat": 0.615}[detail]
    font = load_font(int(S * scale))
    txt = "镜"

    mask = Image.new("L", (S, S), 0)
    box = ImageDraw.Draw(mask).textbbox((0, 0), txt, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x, y = (S - w) // 2 - box[0], (S - h) // 2 - box[1]
    ImageDraw.Draw(mask).text((x, y), txt, font=font, fill=255)

    if detail == "flat":
        glyph = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        glyph.paste(Image.new("RGBA", (S, S), (222, 254, 249, 255)), (0, 0), mask)
        img.alpha_composite(glyph)
    else:
        grad = vgrad(S, S, (250, 255, 254), (118, 212, 198)).convert("RGBA")
        glyph = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        glyph.paste(grad, (0, 0), mask)
        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        glow.paste(Image.new("RGBA", (S, S), (63, 182, 168, 150)), (0, 0), mask)
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(int(S * 0.030))))
        img.alpha_composite(glyph)

    return img.resize((size_px, size_px), Image.LANCZOS)


PLAN = [(256, "full"), (128, "full"), (64, "mid"), (48, "mid"), (32, "flat"), (16, "flat")]


frames = [build(px, detail) for px, detail in PLAN]
write_ico(REFRESH_ICO, frames)
shutil.copyfile(REFRESH_ICO, LEGACY_ICO)
frames[0].save(PNG_256, format="PNG")

# 回读校验：帧数、尺寸、能否被解析
with Image.open(REFRESH_ICO) as chk:
    sizes = sorted(chk.info.get("sizes") or [])
print("ico :", REFRESH_ICO, os.path.getsize(REFRESH_ICO), "bytes")
print("帧  :", sizes)
assert sizes == sorted((px, px) for px, _ in PLAN), "ICO 帧不完整"

static = os.path.join(ROOT, "static")
for px, detail in ((192, "mid"), (512, "full")):
    build(px, detail).save(os.path.join(static, f"icon-{px}.png"), format="PNG")
# 关键：桌面快捷方式要引用 AppData 里的这一份。
# 项目目录下的文件若带沙箱会话 SID、ACL 里没有本机用户账户，
# shell 读不到，快捷方式会显示成「白色空白页」（实测）。
local_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MirrorOS")
local_ico = os.path.join(local_dir, "icon.ico")
os.makedirs(local_dir, exist_ok=True)
shutil.copyfile(REFRESH_ICO, local_ico)
print("shell 用:", local_ico)
print("web icons: static/icon-192.png, static/icon-512.png")

sheet = Image.new("RGB", (256 * 2 + 30, 256 * 3 + 34), (28, 30, 34))
d = ImageDraw.Draw(sheet)
for row, (px, art) in enumerate([(256, "full"), (48, "mid"), (32, "flat")]):
    im = build(px, art)
    tile = im if px == 256 else im.resize((256, 256), Image.NEAREST)
    bg = Image.new("RGB", (256, 256), (28, 30, 34))
    bg.paste(tile, (0, 0), tile.convert("RGBA"))
    sheet.paste(bg, (10, 24 + row * 266))
    d.text((276, 24 + row * 266 + 118), f"{px}px ({art})", fill=(210, 210, 210))
sheet.save(os.path.join(ROOT, "_shots", "icon_final.png"), quality=95)
print("预览:", os.path.join(ROOT, "_shots", "icon_final.png"))
