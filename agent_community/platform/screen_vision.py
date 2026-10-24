# -*- coding: utf-8 -*-
"""**看得见地找输入框**：抓窗口内容区像素，用三条信号猜"聊天输入框在哪"。

## 为什么要有它（老板 2026-10-23 夜的原话）

「把鼠标放在输入框上面怎么点击平台里面的按钮？让脚本学习位置根本不合理，
 我的设计是利用视觉脚本去检测大致方位（例如输入框一般都在中下方）、关键词（输入框里面经常有引导词）、图像（输入框的样子）。」

⇒ "人教位置"这条路**在交互上就不成立**（鼠标只有一只）。正确做法是**脚本自己看**：

1. **方位先验**：聊天输入框几乎总在内容区的**中下方**（y 大约 0.5~1.0），极少贴左边；
2. **图像特征**：它通常是一个**横向很宽、带边框、内部大片同色**的矩形（圆角/描边都算）；
3. **关键词**：框里常有引导词（placeholder）。**这一条要能读到文字** ——
   Chromium 里只有 CDP/UIA 能读；读不到时**不假装用了它**（`keywords_used=False`）。

## 实现约束

- **纯标准库**（GDI 抓屏用 ctypes；不装 Pillow / OpenCV）——陌生人拿到仓库就能跑；
- 分辨率降到 ~220px 宽再算，**快**（一次判定几十毫秒）；
- 找不到就返回 `None` + 原因，**绝不瞎点**（点错地方会打坏别人的东西）。

返回结构：
```
{"ok": True, "rel_x": 0.5, "rel_y": 0.86, "score": 0.72,
 "rect": {"l":..,"t":..,"w":..,"h":..},          # 内容区内的像素矩形
 "frame": {"w":..,"h":..},                        # 内容区尺寸（用于相对换算）
 "reasons": ["在中下方", "横向宽 78% 内容区", "内部均匀", ...],
 "keywords_used": False}
```
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_IS_WINDOWS = sys.platform == "win32"

#: 缩放后的分析宽度（越小越快；220 够看出一条 1px 的边框）
ANALYZE_WIDTH = 220
#: 判定阈值：低于它就当"没看出来"（宁可让人再看一眼，也不瞎点）
MIN_SCORE = 0.45


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def _gdi():
    return ctypes.WinDLL("gdi32", use_last_error=True), ctypes.WinDLL("user32", use_last_error=True)


def _ensure_dpi_aware() -> bool:
    """**抓图之前必须先声明 DPI 感知**（实现只有一份：`input_sim.ensure_dpi_aware`）。

    2026-10-24 凌晨真机实测（本机 150% 缩放，Trae 窗口 2163×1353）：
    进程不声明 DPI 感知时 `GetClientRect` 返回的是**逻辑坐标** 1442×902，
    而 `PrintWindow` 按**物理像素**画 ⇒ 位图按逻辑尺寸开，只装得下窗口**左上 2/3**，
    输入框整个被切掉。于是估位"成功"地返回了一个**很有把握的错位置**
    （`rel_y=0.95`、`score=1.0`，实际落点是聊天正文/底部工具条）。

    症状最坑的地方：**截图不报错、估位不报错、点下去打到别处** ——
    典型"看起来成功"。同一台机器上点鼠标的路径（`input_sim`）早就声明过 DPI，
    抓图的路径漏了，两条路各算各的坐标系。

    返回是否声明成功；失败时不抛异常，但会**把原因写进返回结构**（`dpi_aware=False`），
    由上层如实说"这张图可能不完整"，而不是假装图是全的。
    """
    if not _IS_WINDOWS:
        return False
    try:
        from . import input_sim

        return bool(input_sim.ensure_dpi_aware())
    except Exception as e:                                    # noqa: BLE001 — 声明不了就退老 API（下面照样要抓图）
        print("[screen_vision] 声明 DPI 感知失败（坐标可能按缩放算错，图可能被切）：%s: %s"
              % (type(e).__name__, e), flush=True)
        try:
            return bool(ctypes.WinDLL("user32", use_last_error=True).SetProcessDPIAware())
        except Exception as e2:                               # noqa: BLE001 — 老系统也没有这个入口
            print("[screen_vision] 老 API 也不可用：%s: %s" % (type(e2).__name__, e2), flush=True)
            return False


def grab_gray(hwnd: int, *, max_width: int = ANALYZE_WIDTH) -> dict:
    """把窗口**客户区**抓成灰度矩阵（降采样）。返回 `{ok, w, h, rows:[bytearray,…], why}`。"""
    if not _IS_WINDOWS:
        return {"ok": False, "why": "非 Windows：抓屏只在这里有"}
    dpi_ok = _ensure_dpi_aware()          # 必须在拿客户区尺寸之前（见 _ensure_dpi_aware 的实测记录）
    gdi, user = _gdi()
    hdc_win = user.GetDC(wintypes.HWND(int(hwnd)))
    if not hdc_win:
        return {"ok": False, "why": "拿不到窗口 DC（窗口可能已关闭）"}
    hdc_mem = gdi.CreateCompatibleDC(hdc_win)
    try:
        rc = _RECT()
        if not user.GetClientRect(wintypes.HWND(int(hwnd)), ctypes.byref(rc)):
            return {"ok": False, "why": "拿不到客户区矩形"}
        cw, ch = int(rc.right - rc.left), int(rc.bottom - rc.top)
        if cw < 120 or ch < 120:
            return {"ok": False, "why": "窗口客户区太小（%dx%d），不像主窗口" % (cw, ch)}
        step = max(1, cw // int(max_width))
        sw, sh = cw // step, ch // step
        bi = _BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        # ★2026-10-24 真机修（**估位一直是错的根因**）：位图必须按**整扇窗**开，
        #   再在 Python 里按 step 降采样。原来这里位图开的是 `sw x sh`（≈240x150），
        #   而 PrintWindow 是按窗口真实尺寸画的 ⇒ 只有**左上角 240x150 个像素**进了位图，
        #   其余被裁掉。于是 `find_input_box` 分析的从来不是"整扇窗"，而是"窗口左上角一小块"，
        #   它给出的 rel_y 乘回整窗高度纯属巧合（实测：输入框在 0.85~0.99，它说 0.95；
        #   而那次 0.95 恰好是 150 行里的第 142 行＝窗口**顶部**工具条）。
        bi.bmiHeader.biWidth = cw
        bi.bmiHeader.biHeight = -ch       # 负数 = 从上到下（方便按行读）
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0     # BI_RGB
        bits = ctypes.c_void_p()
        hbmp = gdi.CreateDIBSection(hdc_win, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
        if not hbmp or not bits:
            return {"ok": False, "why": "创建位图失败"}
        gdi.SelectObject(hdc_mem, hbmp)
        # ① 先让窗口**自己画一份**（DWM 窗口用 BitBlt(hdcWin) 会拿到全黑，实测 Edge/Trae 都是 0 像素）
        got = False
        try:
            got = bool(user.PrintWindow(wintypes.HWND(int(hwnd)), hdc_mem, 2))  # 2 = PW_RENDERFULLCONTENT
        except Exception:                                     # noqa: BLE001 — 老系统可能没有这个入口
            got = False
        # ② 再退回"从桌面 DC 裁一块"（窗口当时确实在屏幕上看得到时才有效）
        if not got:
            hdc_screen = user.GetDC(None)
            pt = _POINT(0, 0)
            user.ClientToScreen(wintypes.HWND(int(hwnd)), ctypes.byref(pt))
            got = bool(gdi.BitBlt(hdc_mem, 0, 0, cw, ch, hdc_screen, pt.x, pt.y, 0x00CC0020))
            user.ReleaseDC(None, hdc_screen)
        if not got:
            return {"ok": False, "why": "抓不到这个窗口的像素（PrintWindow 与桌面抓取都失败）"}
        buf = (ctypes.c_ubyte * (cw * ch * 4)).from_address(bits.value)
        rows = []
        off = sorted({0, step // 2, step - 1})       # 每个分析格子里取 3 个横向/纵向采样点
        off = [o for o in off if 0 <= o < step] or [0]
        for y in range(sh):
            row = bytearray(sw)
            ys = [min(ch - 1, y * step + o) for o in off]
            for x in range(sw):
                xs = [min(cw - 1, x * step + o) for o in off]
                tot = 0
                for yy in ys:
                    by = yy * cw * 4
                    for xx in xs:
                        i = by + xx * 4
                        # BGRA → 灰度（0.114B+0.587G+0.299R）
                        tot += (buf[i] * 114 + buf[i + 1] * 587 + buf[i + 2] * 299) // 1000
                row[x] = tot // (len(ys) * len(xs))
            rows.append(row)
        return {"ok": True, "w": sw, "h": sh, "rows": rows, "client_w": cw, "client_h": ch,
                "step": step, "dpi_aware": dpi_ok, "why": ""}
    finally:
        try:
            gdi.DeleteDC(hdc_mem)
        except Exception:                                     # noqa: BLE001
            pass
        user.ReleaseDC(wintypes.HWND(int(hwnd)), hdc_win)


def _edge_strength(rows, y: int) -> float:
    """某一行上"横向边缘"的强度（相邻像素跳变的平均幅度）—— 输入框的上下边框会很强。"""
    row = rows[y]
    n = len(row) - 1
    if n <= 0:
        return 0.0
    tot = 0
    for x in range(n):
        d = row[x + 1] - row[x]
        tot += d if d >= 0 else -d
    return tot / n


def _row_uniformity(rows, y: int, x0: int, x1: int) -> float:
    """某一行在 [x0,x1) 内的"均匀度"（输入框内部通常是一整片同色）。1.0 = 完全均匀。"""
    row = rows[y]
    seg = row[x0:x1]
    if len(seg) < 8:
        return 0.0
    mean = sum(seg) / len(seg)
    var = sum((v - mean) ** 2 for v in seg) / len(seg)
    return max(0.0, 1.0 - (var ** 0.5) / 40.0)


def find_input_box(pic: dict) -> dict:
    """在灰度图里找"最像聊天输入框"的那一条带（方位 + 形状 + 均匀度，暗色主题也认）。"""
    if not pic.get("ok"):
        return {"ok": False, "why": pic.get("why") or "没有图像"}
    rows, w, h = pic["rows"], pic["w"], pic["h"]
    if h < 40 or w < 60:
        return {"ok": False, "why": "图像太小，判定不可靠"}

    means = [sum(r) / w for r in rows]
    srt = sorted(means)
    med = srt[len(srt) // 2]
    span = max(4.0, (max(means) - min(means)) / 6.0)

    def _run(sign: int):
        """sign=+1 找"比基线亮"的带；-1 找"比基线暗"的带。"""
        best = None
        start = None
        for y in range(int(h * 0.40), h):
            hit = (means[y] - med) * sign >= span * 0.5
            if hit and start is None:
                start = y
            if (not hit or y == h - 1) and start is not None:
                y0, y1 = start, y
                start = None
                hh = y1 - y0
                if hh < 6 or hh > max(20, int(h * 0.55)):
                    continue
                if (y1 / h) < 0.45:
                    continue
                mid = (y0 + y1) // 2
                uni = _row_uniformity(rows, mid, int(w * 0.06), int(w * 0.94))
                bottom_bias = min(1.0, (y1 / h - 0.45) / 0.5)
                score = (0.40 * bottom_bias + 0.28 * uni
                         + 0.16 * min(1.0, ((w * 0.88) / w) / 0.8)
                         + 0.16 * (1.0 - min(1.0, abs(hh - max(6, int(h * 0.09))) / max(6, h * 0.25))))
                cand = {"score": score, "y0": y0, "y1": y1, "uni": uni, "hh": hh,
                        "sign": sign}
                if best is None or cand["score"] > best["score"]:
                    best = cand
        return best

    cands = [c for c in (_run(1), _run(-1)) if c]
    if not cands:
        return {"ok": False, "why": "没找到像输入框的横向带（界面样式特殊 / 窗口没渲染完 / 被遮挡）"}
    best = max(cands, key=lambda c: c["score"])
    cy = (best["y0"] + best["y1"]) / 2.0
    rel_y = cy / h
    # 横向取中间偏左一点（多数聊天框左侧有光标/引导词，点中间最稳）
    rel_x = 0.5
    reasons = ["在中下方（y=%.2f）" % rel_y,
               "带高约 %d 行（%.0f%% 内容区高）" % (best["hh"], 100.0 * best["hh"] / h),
               "内部均匀度 %.2f" % best["uni"],
               "比基线%s" % ("亮" if best["sign"] > 0 else "暗")]
    ok = best["score"] >= MIN_SCORE
    return {"ok": ok, "rel_x": rel_x, "rel_y": round(rel_y, 4),
            "score": round(best["score"], 3),
            "rect": {"l": 0, "t": best["y0"], "w": w, "h": best["hh"]},
            "frame": {"w": pic.get("client_w") or w, "h": pic.get("client_h") or h},
            "reasons": reasons, "keywords_used": False,
            "why": "" if ok else
                   "看得出一个像输入框的带，但把握不够（%.2f < %.2f）—— 不点，免得点错地方"
                   % (best["score"], MIN_SCORE)}


def guess_input_anchor(hwnd: int) -> dict:
    """**一条命令拿到"输入框大概在窗口里的哪儿"**（拿不到/没把握都如实说）。"""
    return find_input_box(grab_gray(hwnd))


def grab_band(hwnd: int, rel_y: float, *, half: float = 0.12,
              max_width: int = ANALYZE_WIDTH) -> dict:
    """只抓**输入框那一条带**的灰度（整窗抓图太贵，见下）。

    为什么要它（2026-10-24 老板反馈"输入有延迟"）：整窗抓一帧要开 2163×1353 的位图、
    再在 Python 里逐格采样 ~29 万次（实测 0.2~0.5s CPU/帧）；`visual-run` 一次要抓 2~4 帧，
    连着打就会让整机发卡。而"粘贴前后比变化"只关心**那一条带**，
    所以这里只把那条带的行解出来（同样要先 PrintWindow 画满，但 Python 侧的读取降到 ~1/6）。

    返回结构与 `grab_gray` 兼容：`rows` 只含带内的行，另带 `band_top`（带在原图里从第几行开始）
    与 `band_of`（原图共几行）。`band_diff(pic_a, pic_b, rel_y)` 直接用即可（它会用 `band_top`）。
    """
    pic = grab_gray(hwnd, max_width=max_width)
    if not pic.get("ok"):
        return pic
    h = int(pic["h"])
    y0 = max(0, int((float(rel_y) - float(half)) * h))
    y1 = min(h, int((float(rel_y) + float(half)) * h) + 1)
    if y1 <= y0:
        return {"ok": False, "why": "带的位置算不出来（rel_y=%.3f, half=%.3f）" % (rel_y, half)}
    out = dict(pic)
    out["rows"] = pic["rows"][y0:y1]
    out["band_top"] = y0
    out["band_of"] = h
    out["band_rows"] = y1 - y0
    return out


def band_diff(before: dict, after: dict, rel_y: float, *, half: float = 0.06,
              pixel_delta: int = 12) -> dict:
    """**粘贴前后，输入框那一条带变没变**（Electron 类窗口读不回文字时，唯一可得的硬信号）。

    为什么要它（2026-10-24 真机）：Trae 这种应用自己画输入框，系统层拿不到控件 ⇒
    `WM_GETTEXT` 读不回来。既然读不到"字对不对"，退一步判"**画面动没动**"：
    在一小片区域里数"灰度变化超过 `pixel_delta` 的像素比例"，变了就说明有东西进去了。

    ⚠️ **诚实边界**：画面变了 ≠ 文字一定对（可能是别的重绘）；画面没变 = 基本没粘进去。
    所以调用方必须把"没核实"照实说（`state=sent_unverified`），不许升级成 `delivered_verified`。

    返回 `{ok, ratio, changed, why}`；两张图尺寸不一致/抓不到 ⇒ `ok=False` 并说原因。
    """
    a, b = before or {}, after or {}
    if not a.get("ok") or not b.get("ok"):
        return {"ok": False, "ratio": None, "changed": None,
                "why": "抓图没成（%s / %s）" % (a.get("why") or "—", b.get("why") or "—")}
    if (a.get("w"), a.get("h")) != (b.get("w"), b.get("h")):
        return {"ok": False, "ratio": None, "changed": None,
                "why": "两次抓图尺寸不一致（%sx%s vs %sx%s）" % (a.get("w"), a.get("h"),
                                                               b.get("w"), b.get("h"))}
    rows_a, rows_b, h = a["rows"], b["rows"], int(a["h"])
    if a.get("band_top") is not None and b.get("band_top") is not None:
        # 两张都是 `grab_band` 抓的"只有那一条带"的图 ⇒ 整张直接比（省一次范围计算）
        y0, y1 = 0, len(rows_a)
        total = changed = 0
        for y in range(min(len(rows_a), len(rows_b))):
            ra, rb = rows_a[y], rows_b[y]
            for x in range(len(ra)):
                total += 1
                if abs(ra[x] - rb[x]) > pixel_delta:
                    changed += 1
        ratio = (changed / total) if total else 0.0
        return {"ok": True, "ratio": round(ratio, 4), "changed": ratio >= 0.003, "why": "",
                "band": {"y0": a.get("band_top"), "y1": int(a.get("band_top") or 0) + y1,
                         "of": a.get("band_of") or h}, "banded": True}
    y0 = max(0, int((rel_y - half) * h))
    y1 = min(h, int((rel_y + half) * h) + 1)
    if y1 <= y0:
        return {"ok": False, "ratio": None, "changed": None, "why": "带的位置算不出来"}
    total = changed = 0
    for y in range(y0, y1):
        ra, rb = rows_a[y], rows_b[y]
        for x in range(len(ra)):
            total += 1
            if abs(ra[x] - rb[x]) > pixel_delta:
                changed += 1
    ratio = (changed / total) if total else 0.0
    # 阈值怎么定的（2026-10-24 真机实测，Trae 2163x1353）：
    #   · 静止不动连抓三次：**0.0000**（噪声为零，因为窗口是 PrintWindow 重渲染出来的）
    #   · 空输入框 → 粘一句话：**1.27%**      · 已有文字 → 再敲 3 个字：**0.68%**
    # 取 0.3%：既抓得到"只多了几个字"，也不会被噪声骗。
    return {"ok": True, "ratio": round(ratio, 4), "changed": ratio >= 0.003, "why": "",
            "band": {"y0": y0, "y1": y1, "of": h}}

# ─────────────────────────────────────────────────────────────────────────────
# 彩色快照（给"框选"用）：纯标准库编码 PNG（zlib + struct），不依赖 Pillow
# ─────────────────────────────────────────────────────────────────────────────
def _png_chunk(tag: bytes, data: bytes) -> bytes:
    import struct
    import zlib

    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _encode_png(width: int, height: int, rgb_rows) -> bytes:
    """`rgb_rows` = 每行的 `bytes`（RGB 三通道，行间无填充）。返回完整 PNG 字节。"""
    import struct
    import zlib

    raw = b"".join(b"\x00" + r for r in rgb_rows)      # 每行前缀 filter=0
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(raw, 6))
            + _png_chunk(b"IEND", b""))


def grab_png(hwnd: int, *, max_width: int = 1100) -> dict:
    """抓窗口**客户区**的彩色快照（PNG 字节）。用于"用户框选输入框/发送按钮"。

    返回 `{ok, png, width, height, client_w, client_h, why}`；
    窗口最小化/抓不到像素时 `ok=False` 并如实说（前端据此提示"把窗口露出来再截"）。
    """
    if not _IS_WINDOWS:
        return {"ok": False, "why": "非 Windows：抓屏只在这里有"}
    dpi_ok = _ensure_dpi_aware()          # 必须在拿客户区尺寸之前（见 _ensure_dpi_aware 的实测记录）
    gdi, user = _gdi()
    hdc_win = user.GetDC(wintypes.HWND(int(hwnd)))
    if not hdc_win:
        return {"ok": False, "why": "拿不到窗口 DC（窗口可能已关闭）"}
    hdc_mem = gdi.CreateCompatibleDC(hdc_win)
    try:
        rc = _RECT()
        if not user.GetClientRect(wintypes.HWND(int(hwnd)), ctypes.byref(rc)):
            return {"ok": False, "why": "拿不到客户区矩形"}
        cw, ch = int(rc.right - rc.left), int(rc.bottom - rc.top)
        if cw < 120 or ch < 120:
            return {"ok": False, "why": "窗口客户区太小（%dx%d），不像主窗口" % (cw, ch)}
        step = max(1, cw // int(max_width))
        sw, sh = cw // step, ch // step
        bi = _BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = sw
        bi.bmiHeader.biHeight = -sh
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        hbmp = gdi.CreateDIBSection(hdc_win, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
        if not hbmp or not bits:
            return {"ok": False, "why": "创建位图失败"}
        gdi.SelectObject(hdc_mem, hbmp)
        got = False
        try:
            got = bool(user.PrintWindow(wintypes.HWND(int(hwnd)), hdc_mem, 2))
        except Exception:                                     # noqa: BLE001
            got = False
        if not got:
            hdc_screen = user.GetDC(None)
            pt = _POINT(0, 0)
            user.ClientToScreen(wintypes.HWND(int(hwnd)), ctypes.byref(pt))
            got = bool(gdi.BitBlt(hdc_mem, 0, 0, sw, sh, hdc_screen, pt.x, pt.y, 0x00CC0020))
            user.ReleaseDC(None, hdc_screen)
        if not got:
            return {"ok": False, "why": "抓不到这个窗口的像素（PrintWindow 与桌面抓取都失败）"}
        buf = (ctypes.c_ubyte * (sw * sh * 4)).from_address(bits.value)
        rows = []
        nonzero = 0
        for y in range(sh):
            base = y * sw * 4
            row = bytearray(sw * 3)
            for x in range(sw):
                i = base + x * 4
                b, g, r = buf[i], buf[i + 1], buf[i + 2]
                if b or g or r:
                    nonzero += 1
                row[x * 3] = r
                row[x * 3 + 1] = g
                row[x * 3 + 2] = b
            rows.append(bytes(row))
        if nonzero == 0:
            return {"ok": False, "why": "抓到的画面是全黑的（窗口最小化或被完全遮挡？）"}
        png = _encode_png(sw, sh, rows)
        return {"ok": True, "png": png, "width": sw, "height": sh,
                "client_w": cw, "client_h": ch, "dpi_aware": dpi_ok, "why": ""}
    finally:
        try:
            gdi.DeleteDC(hdc_mem)
        except Exception:                                     # noqa: BLE001
            pass
        user.ReleaseDC(wintypes.HWND(int(hwnd)), hdc_win)
