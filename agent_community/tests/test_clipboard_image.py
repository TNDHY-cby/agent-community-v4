# -*- coding: utf-8 -*-
"""守卫：**用户自己截的那张图**（剪贴板里的 CF_DIB）能被正确解出来。

起因（老板 2026-10-24）：「应该让用户自己去截图然后在平台输入框上传，或者直接点击按钮后
检测用户刚才截屏后保留在剪贴板里面的照片」—— 这条路必须不依赖宿主机的剪贴板也能测，
所以这里只钉**纯函数** `dib_to_rows`（真剪贴板由 `tools/` 里的真机判据覆盖）。

| # | 判据 |
|---|---|
| ① | 24 位、自下而上的 DIB ⇒ 行序被翻正（第 0 行是图的最上面一行） |
| ② | 32 位也认（截图工具常见） |
| ③ | 压缩格式 / 位数不对 / 数据不完整 ⇒ **如实说原因**，不猜 |
| ④ | 空剪贴板 ⇒ 提示"先截一张图再来" |
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _dib(w: int, h: int, bpp: int, rows_bgr: list, *, compression: int = 0) -> bytes:
    """按 DIB 规矩造一段字节（自下而上；`rows_bgr` 按"图从上到下"给，这里倒着写）。"""
    stride = ((w * bpp + 31) // 32) * 4
    px = bytearray()
    for row in reversed(rows_bgr):
        line = bytearray()
        for (b, g, r) in row:
            line += bytes((b, g, r))
            if bpp == 32:
                line += b"\x00"
        line += b"\x00" * (stride - len(line))
        px += line
    return struct.pack("<IiiHHIIiiII", 40, w, h, 1, bpp, compression,
                       len(px), 0, 0, 0, 0) + bytes(px)


def test_24bit_bottom_up_is_flipped_to_top_down():
    from agent_community.platform import clipboard_image as ci

    # 图上：第 1 行蓝、第 2 行红（BGR 里红 = (0,0,255)）
    blue, red = (255, 0, 0), (0, 0, 255)
    raw = _dib(2, 2, 24, [[blue, blue], [red, red]])
    r = ci.dib_to_rows(raw)
    assert r["ok"] and (r["w"], r["h"]) == (2, 2), r
    assert tuple(r["rows"][0][:3]) == (0, 0, 255), "第 0 行应该是最上面那行（蓝）：%s" % (r["rows"][0][:3],)
    assert tuple(r["rows"][1][:3]) == (255, 0, 0), "第 1 行应该是红：%s" % (r["rows"][1][:3],)


def test_32bit_is_supported():
    from agent_community.platform import clipboard_image as ci

    raw = _dib(1, 1, 32, [[(10, 20, 30)]])
    r = ci.dib_to_rows(raw)
    assert r["ok"] and (r["w"], r["h"]) == (1, 1), r
    assert tuple(r["rows"][0][:3]) == (30, 20, 10), r["rows"][0][:3]


def test_unsupported_shapes_say_why():
    from agent_community.platform import clipboard_image as ci

    assert ci.dib_to_rows(b"")["ok"] is False
    assert "先截一张图" in ci.dib_to_rows(b"")["why"]

    rle = _dib(1, 1, 24, [[(0, 0, 0)]], compression=1)          # BI_RLE8 这类是真不支持
    r = ci.dib_to_rows(rle)
    assert r["ok"] is False and "压缩" in r["why"], r

    sixbit = _dib(1, 1, 24, [[(0, 0, 0)]])
    # biSize(4)+biWidth(4)+biHeight(4)+biPlanes(2) = 14 ⇒ biBitCount 在偏移 14
    sixbit = sixbit[:14] + struct.pack("<H", 8) + sixbit[16:]
    r2 = ci.dib_to_rows(sixbit)
    assert r2["ok"] is False and "24/32" in r2["why"], r2

    ok = _dib(4, 4, 24, [[(1, 2, 3)] * 4] * 4)
    r3 = ci.dib_to_rows(ok[:-20])                                   # 截掉一截
    assert r3["ok"] is False and "不完整" in r3["why"], r3


def test_bitfields_32bit_is_supported():
    """`.NET Clipboard.SetImage` / 不少截图工具给的是 **32 位 + BI_BITFIELDS**（compression=3）。

    只认 BI_RGB 会一律报"压缩格式不支持" —— 2026-10-24 真机踩到，这里钉住。
    """
    from agent_community.platform import clipboard_image as ci

    w = h = 1
    rmask, gmask, bmask = 0x00FF0000, 0x0000FF00, 0x000000FF
    # 一个像素：R=200 G=30 B=30 ⇒ BGRA 字节序 B,G,R,A
    pixel = bytes((30, 30, 200, 0))
    head = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 3, len(pixel), 0, 0, 0, 0)
    masks = struct.pack("<III", rmask, gmask, bmask)
    r = ci.dib_to_rows(head + masks + pixel)
    assert r["ok"] and (r["w"], r["h"]) == (1, 1), r
    assert tuple(r["rows"][0][:3]) == (200, 30, 30), "掩码解出来的 RGB 不对：%s" % (r["rows"][0][:3],)
