# -*- coding: utf-8 -*-
"""ICO 写入器：小尺寸用 DIB(BMP) 条目，256 用 PNG。

为什么必须这样：
  桌面/快捷方式渲染图标走的是老式图标提取路径，只认 DIB 条目；全程 PNG 压缩的
  .ico 在那条路径上会失败，表现为「白色空白页」图标（实测踩过：.ico 作为文件能
  正常显示，但被 .lnk 引用就不显示——两条代码路径的格式支持不同）。
  传统应用图标都是 DIB 小图 + PNG 256，就是为兼容这个。

DIB 条目结构：
  BITMAPINFOHEADER(40B) + 像素数据（32bpp，BGRA，自下而上）+ AND 掩码(1bpp)
"""
import io
import struct

from PIL import Image


def _dib_payload(im):
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    # 32bpp BGRA，自下而上
    rows = []
    for y in range(h - 1, -1, -1):
        row = bytearray()
        for x in range(w):
            r, g, b, a = px[x, y]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    pixels = b"".join(rows)
    # AND 掩码：1bpp，每行补齐到 4 字节；全 0 表示不透明由 alpha 决定
    mask_row = ((w + 31) // 32) * 4
    mask = b"\x00" * (mask_row * h)
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                         len(pixels) + len(mask), 0, 0, 0, 0)
    return header + pixels + mask


def _png_payload(im):
    buf = io.BytesIO()
    im.convert("RGBA").save(buf, format="PNG")
    return buf.getvalue()


def write_ico(path, frames, png_from=256):
    """frames: [(PIL.Image, size)]。>=png_from 的尺寸用 PNG，其余用 DIB。"""
    entries = []
    for im in frames:
        w, h = im.size
        data = _png_payload(im) if w >= png_from else _dib_payload(im)
        entries.append((w, h, data))

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    dirs, blobs = b"", b""
    for w, h, data in entries:
        dirs += struct.pack("<BBBBHHII",
                            0 if w >= 256 else w, 0 if h >= 256 else h,
                            0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, "wb") as f:
        f.write(header + dirs + blobs)


def describe(path):
    """回读校验：每帧尺寸 + 载荷格式。"""
    out = []
    with open(path, "rb") as f:
        head = f.read(6)
        n = int.from_bytes(head[4:6], "little")
        for i in range(n):
            e = f.read(16)
            w = e[0] or 256
            h = e[1] or 256
            size = int.from_bytes(e[8:12], "little")
            off = int.from_bytes(e[12:16], "little")
            f.seek(off)
            sig = f.read(4)
            kind = "PNG" if sig == b"\x89PNG" else "DIB"
            f.seek(6 + 16 * (i + 1))
            out.append(f"{w}x{h}:{kind}")
    return "frames=%d  " % n + "  ".join(out)
