# -*- coding: utf-8 -*-
"""**用户自己截的那张图**：从系统剪贴板取出来（老板 2026-10-24 定）。

## 为什么要它（老板原话）

> 「应该让用户自己去截图然后在平台输入框上传，或者**直接点击按钮后检测用户刚才截屏后保留在剪贴板里面的照片**」

原来的路是"平台自己去找窗口截屏"，真机上连踩两坑：同名窗口里挑中了 93×150 的浮层；
用户真正想截的那扇窗当时是**最小化的**（`GetWindowRect` 给出 237×39 的占位值）。
⇒ 换成"用户截什么、平台就用什么"，这条路与"窗口现在长什么样"完全解耦。

## 边界（写在返回值里，不粉饰）

- **只有 Windows 有系统剪贴板图片**（`CF_DIB`）；其它平台如实返回 `supported=False`；
- 只认 24/32 位未压缩 DIB（截图工具给的基本都是这种）；认不出就**说清为什么**，不猜；
- 纯标准库（ctypes + zlib），不引 Pillow —— 陌生人拿到仓库就能跑。
"""
from __future__ import annotations

import ctypes
import struct
import sys

_IS_WINDOWS = sys.platform == "win32"

CF_DIB = 8
CF_DIBV5 = 17


def _png(rows: list, w: int, h: int) -> bytes:
    """RGB 行 → PNG（复用 `screen_vision` 的编码器，一处真相）。"""
    from . import screen_vision

    return screen_vision._encode_png(w, h, rows)


def _mask_extract(val: int, mask: int) -> int:
    """按位掩码把分量取出来并归一到 0~255。"""
    if not mask:
        return 0
    shift = (mask & -mask).bit_length() - 1
    width = bin(mask).count("1")
    v = (val & mask) >> shift
    if width < 8:
        v = v * 255 // ((1 << width) - 1)
    return min(255, max(0, v))


def dib_to_rows(dib: bytes) -> dict:
    """把一段 `CF_DIB` 字节解成 `{ok, w, h, rows, why}`（**纯函数**，好测）。

    支持：24/32 位；`BI_RGB`(0)、`BI_BITFIELDS`(3)、`BI_ALPHABITFIELDS`(6)。
    ★实测：`.NET Clipboard.SetImage` 与不少截图工具给的是 **32 位 + BI_BITFIELDS**，
    只认 BI_RGB 会一律报"压缩格式不支持"（2026-10-24 踩到）。
    高度为正 = 自下而上（DIB 常见形态），为负 = 自上而下。
    """
    if not dib or len(dib) < 40:
        return {"ok": False, "why": "剪贴板里的图是空的（先截一张图再来）"}
    try:
        (bi_size, w, h, _planes, bpp, compression,
         _size_img, _xppm, _yppm, _clr_used, _clr_imp) = struct.unpack("<IiiHHIIiiII", dib[:40])
    except struct.error as e:
        return {"ok": False, "why": "图头解析失败：%s" % e}
    if w <= 0 or h == 0:
        return {"ok": False, "why": "图尺寸不合法（%sx%s）" % (w, h)}
    if compression not in (0, 3, 6):
        return {"ok": False, "why": "这张图用了压缩格式（compression=%s），暂不支持 —— 换个截图工具再试"
                                    % compression}
    if bpp not in (24, 32):
        return {"ok": False, "why": "只认 24/32 位色（这张是 %s 位）" % bpp}
    # BI_BITFIELDS 的表头后面跟着 3 个（或 4 个）掩码
    extra = 12 if compression == 3 else (16 if compression == 6 else 0)
    masks = (0, 0, 0)
    if extra and len(dib) >= 40 + extra:
        rmask, gmask, bmask = struct.unpack("<III", dib[40:52])
        masks = (rmask, gmask, bmask)
    top_down = h < 0
    h = abs(h)
    stride = ((w * bpp + 31) // 32) * 4
    need = 40 + extra + stride * h
    if len(dib) < need:
        return {"ok": False, "why": "图数据不完整（要 %d 字节，只有 %d）" % (need, len(dib))}
    px = dib[40 + extra:]
    rows = []
    for y in range(h):
        sy = y if top_down else (h - 1 - y)          # DIB 默认自下而上，这里统一成自上而下
        base = sy * stride
        row = bytearray(w * 3)
        for x in range(w):
            i = base + x * (bpp // 8)
            if bpp == 32 and masks[0]:
                val = px[i] | (px[i + 1] << 8) | (px[i + 2] << 16) | (px[i + 3] << 24)
                r, g, b = (_mask_extract(val, masks[0]), _mask_extract(val, masks[1]),
                           _mask_extract(val, masks[2]))
            else:
                b, g, r = px[i], px[i + 1], px[i + 2]
            row[x * 3] = r
            row[x * 3 + 1] = g
            row[x * 3 + 2] = b
        rows.append(bytes(row))
    return {"ok": True, "w": w, "h": h, "rows": rows, "why": ""}


def grab_clipboard_png() -> dict:
    """从系统剪贴板拿图并编码成 PNG：`{ok, png, width, height, why, supported}`。"""
    if not _IS_WINDOWS:
        return {"ok": False, "supported": False, "png": b"", "width": 0, "height": 0,
                "why": "非 Windows：系统剪贴板图片只在这里有（可以改用「上传图片」）"}
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    # ★句柄是指针：不声明 argtypes/restype 时 ctypes 默认按 32 位整数传/收，**64 位下会被截断**
    #   （实测：`SetImage` 明明成功了，读出来却是空的）
    u.GetClipboardData.restype = ctypes.c_void_p
    u.GetClipboardData.argtypes = [ctypes.c_uint]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k.GlobalSize.restype = ctypes.c_size_t
    k.GlobalSize.argtypes = [ctypes.c_void_p]
    if not u.OpenClipboard(None):
        return {"ok": False, "supported": True, "png": b"", "width": 0, "height": 0,
                "why": "剪贴板被别的程序占着（稍等一下再点）"}
    try:
        last = "剪贴板里没有图（先截一张图再来）"
        for fmt in (CF_DIB, CF_DIBV5):
            handle = u.GetClipboardData(int(fmt))
            if not handle:
                continue
            ptr = k.GlobalLock(handle)
            if not ptr:
                continue
            try:
                size = int(k.GlobalSize(handle) or 0)
                raw = ctypes.string_at(ptr, size) if size else b""
            finally:
                k.GlobalUnlock(handle)
            parsed = dib_to_rows(raw)
            if parsed.get("ok"):
                png = _png(parsed["rows"], parsed["w"], parsed["h"])
                return {"ok": True, "supported": True, "png": png,
                        "width": parsed["w"], "height": parsed["h"], "why": ""}
            last = parsed.get("why") or ""
        return {"ok": False, "supported": True, "png": b"", "width": 0, "height": 0,
                "why": last}
    finally:
        u.CloseClipboard()
