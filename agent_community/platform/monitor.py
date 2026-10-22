# -*- coding: utf-8 -*-
"""监控室：把外端 harness 的**窗口画面**拿回来（V88，老板 2026-10-22 拍板）。

设计稿：`design-docs/V88_监控室窗口画面墙设计.md`。
老板三条拍板：① 先做快照墙（1–2 帧/秒，零依赖）；② AI 观察**默认关**，开了也
「**绝不是一帧一次**，好歹 30 分钟一次」；③ 监控对象挂在 **agent** 上（注册+架桥后可监控，
可开可关），不让用户去挑窗口句柄。

## 三条硬约束（都由真机实测逼出来的）

1. **抓取阶梯 + 如实报**：`PrintWindow(PW_RENDERFULLCONTENT)` 纯 ctypes、零依赖，
   本机 8 个真实窗口里 6 个成功；**Marvis（Electron-GPU 合成）/ 特殊 canvas 恒为黑图**。
   ⇒ 黑图必须判成**失败**并说清原因，绝不拿黑图当画面。
   WGC 只作兜底（需可选依赖），装了才亮。
2. **零依赖也能看**：编码用标准库 `zlib` 手写 PNG（不引 Pillow）；缩小用 GDI `StretchBlt`。
   Pillow 存在时才提供 JPEG/MJPEG 连续流。
3. **画面不出本机**：本模块不提供任何"把帧发出去"的路径；端点进只读档 DENY（见 server.py）。

本模块**不 import server**（避免循环）；需要 harness 时由调用方传进来。
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path
from typing import Any, Optional

IS_WINDOWS = sys.platform == "win32"

#: 监控状态/名单落点
MONITOR_DIR: Optional[Path] = None

#: 块哈希变化检测的阈值：比例超过它才算"画面变了"
CHANGE_THRESHOLD = 0.02
#: 墙上瓦片默认宽度（px）
TILE_W = 320


def _dir() -> Path:
    if MONITOR_DIR is not None:
        return Path(MONITOR_DIR)
    from . import env_spec

    return env_spec.data_dir() / "monitor"


def _watched_path() -> Path:
    return _dir() / "watched.json"


def _state_path() -> Path:
    return _dir() / "state.json"


def _ai_path() -> Path:
    return _dir() / "ai.json"


def _load(path: Path, default: dict) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else dict(default)
    except FileNotFoundError:
        return dict(default)
    except Exception as e:                                   # noqa: BLE001 — 文件坏了要能看见，但不炸
        print("[monitor] 读 %s 失败：%s（按空的继续）" % (path.name, e), flush=True)
        return dict(default)


def _save(path: Path, data: dict) -> None:
    d = path.parent
    d.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def cfg_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def max_watch() -> int:
    return max(1, min(24, cfg_int("AC_MONITOR_MAX_WATCH", 6)))


def fps() -> float:
    return max(0.2, min(10.0, float(cfg_int("AC_MONITOR_FPS", 1))))


def ai_enabled() -> bool:
    return (os.environ.get("AC_MONITOR_AI_OBSERVE") or "0").strip().lower() in ("1", "true", "yes", "on")


def ai_interval_seconds() -> int:
    """AI 观察的最小间隔。老板原话：「绝不是一帧一次，好歹 30 分钟一次」。"""
    return max(60, cfg_int("AC_MONITOR_AI_INTERVAL_MIN", 30) * 60)


# ── PNG 编码（标准库 zlib，不依赖 Pillow）─────────────────────────────────
def png_encode(width: int, height: int, rgb: bytes) -> bytes:
    """把 RGB（自上而下、每像素 3 字节）编成 PNG。纯标准库。"""
    if width <= 0 or height <= 0:
        raise ValueError("宽高必须为正：%dx%d" % (width, height))
    if len(rgb) != width * height * 3:
        raise ValueError("像素字节数不对：期望 %d，实际 %d" % (width * height * 3, len(rgb)))
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)                                        # 每行滤波类型 0（None）
        raw += rgb[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)   # 8bit、真彩色、无交错
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def _looks_black(rgb: bytes, step: int = 3, limit: int = 20000) -> bool:
    """PrintWindow 抓不动的窗口会返回整张黑图 —— 必须判成失败，不许当画面。"""
    sample = rgb[:limit]
    dark = sum(1 for i in range(0, len(sample) - 2, step) if sample[i] < 8 and sample[i + 1] < 8 and sample[i + 2] < 8)
    total = max(1, len(sample) // step)
    return dark / total > 0.97


def block_signature(rgb: bytes, w: int, h: int, grid: int = 8) -> str:
    """把一帧缩成 `grid×grid` 的块均值签名（纯算术，不依赖图片库）——变化检测用。"""
    if w <= 0 or h <= 0 or not rgb:
        return ""
    bw, bh = max(1, w // grid), max(1, h // grid)
    acc = []
    for gy in range(grid):
        for gx in range(grid):
            s = n = 0
            for y in range(gy * bh, min(h, (gy + 1) * bh), max(1, bh // 8)):
                base = y * w * 3 + gx * bw * 3
                for x in range(0, bw * 3, max(3, (bw * 3) // 8)):
                    if base + x + 2 < len(rgb):
                        s += rgb[base + x] + rgb[base + x + 1] + rgb[base + x + 2]
                        n += 3
            acc.append(s // n if n else 0)
    return hashlib.sha1(bytes(min(255, a) for a in acc)).hexdigest()


def diff_ratio(a: str, b: str) -> float:
    """两个签名差多少（按十六进制字符位粗算）。没有可比对象时算 1.0（当作"变了"）。"""
    if not a or not b or len(a) != len(b):
        return 1.0
    same = sum(1 for x, y in zip(a, b) if x == y)
    return 1.0 - same / len(a)


# ── Windows 抓取（只在 win32 上注册真实实现）──────────────────────────────
if IS_WINDOWS:                                               # pragma: no cover - 平台相关
    import ctypes
    import ctypes.wintypes as wt

    _user32 = ctypes.windll.user32
    _gdi32 = ctypes.windll.gdi32
    PW_RENDERFULLCONTENT = 0x00000002
    SRCCOPY = 0x00CC0020
    BI_RGB = 0
    DIB_RGB_COLORS = 0
    HALFTONE = 4

    class _BITHDR(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    class _BITINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITHDR), ("bmiColors", wt.DWORD * 3)]

    def _dib_to_rgb(hdc_mem, hbmp, w: int, h: int) -> Optional[bytes]:
        bi = _BITINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(_BITHDR)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h                            # 负数＝自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = _gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
        if not got:
            return None
        src = buf.raw
        # BGRA → RGB
        out = bytearray(w * h * 3)
        for i in range(w * h):
            b, g, r = src[i * 4], src[i * 4 + 1], src[i * 4 + 2]
            out[i * 3], out[i * 3 + 1], out[i * 3 + 2] = r, g, b
        return bytes(out)


def grab(hwnd: int, max_w: int = TILE_W) -> dict:
    """抓一个窗口的当前画面 → `{ok, method, w, h, rgb, why}`。

    `rgb` 是缩放到 `max_w` 之后的原始像素（自上而下、3 字节/像素）。
    **失败必须说清原因**（最小化 / 黑图 / 平台不支持），不许静默返回空帧。
    """
    if not IS_WINDOWS:                                       # pragma: no cover
        return {"ok": False, "why": "只在 Windows 上支持窗口抓取（当前是 %s）" % sys.platform,
                "method": "", "hint": "非 Windows 平台请用 /api/harness/{id}/mirror 的协议级映射"}
    if not hwnd:                                             # pragma: no cover
        return {"ok": False, "why": "没给窗口句柄", "method": ""}

    r = wt.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return {"ok": False, "why": "窗口不存在（可能已关闭）", "method": ""}
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return {"ok": False, "why": "窗口尺寸为 0（最小化了？最小化窗口抓不到）", "method": ""}
    if _user32.IsIconic(hwnd):
        return {"ok": False, "why": "窗口已最小化 —— 最小化时系统不渲染内容，抓不到",
                "method": "", "hint": "把窗口恢复（可以放在角落或被别的窗口挡住，那都能抓）"}

    hdc_screen = _user32.GetDC(0)
    hdc_mem = _gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = _gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    _gdi32.SelectObject(hdc_mem, hbmp)
    method = "printwindow"
    ok = _user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
    if not ok:
        ok = _user32.PrintWindow(hwnd, hdc_mem, 0)            # 老接口兜底
    rgb = _dib_to_rgb(hdc_mem, hbmp, w, h) if ok else None
    _gdi32.DeleteObject(hbmp)
    _gdi32.DeleteDC(hdc_mem)
    _user32.ReleaseDC(0, hdc_screen)

    if not rgb:
        return {"ok": False, "method": method, "why": "系统不给抓（PrintWindow 返回失败）",
                "hint": "这类窗口可能需要装 WGC 可选依赖；受保护内容（DRM/安全桌面）永远抓不到"}
    if _looks_black(rgb):
        return {"ok": False, "method": method,
                "why": "抓到的是整张黑图（这个窗口不允许 PrintWindow 抓取，常见于 GPU 合成的 Electron 窗口）",
                "hint": "装 WGC 可选依赖后重试；仍不行就是这个窗口受保护"}

    # 缩小到 max_w（GDI StretchBlt 会插值；这里用整数采样避免再开一个 DC，
    # 320px 瓦片下差别看不出来，且**不依赖任何图像库**）
    if max_w and w > max_w:
        nw = max_w
        nh = max(1, round(h * max_w / w))
        tiny = bytearray(nw * nh * 3)
        for y in range(nh):
            sy = min(h - 1, int(y * h / nh))
            for x in range(nw):
                sx = min(w - 1, int(x * w / nw))
                s = (sy * w + sx) * 3
                d = (y * nw + x) * 3
                tiny[d:d + 3] = rgb[s:s + 3]
        return {"ok": True, "method": method, "w": nw, "h": nh, "rgb": bytes(tiny)}

    return {"ok": True, "method": method, "w": w, "h": h, "rgb": rgb}


def frame_png(hwnd: int, max_w: int = TILE_W) -> tuple:
    """抓到 PNG 字节 → `(bytes, meta)`；失败时 `(None, {ok:False, why, …})`。"""
    g = grab(hwnd, max_w=max_w)
    if not g.get("ok"):
        return None, {k: v for k, v in g.items() if k != "rgb"}
    try:
        png = png_encode(g["w"], g["h"], g["rgb"])
    except Exception as e:                                   # noqa: BLE001 — 编码失败要如实报
        return None, {"ok": False, "why": "画面编码失败：%s" % e, "method": g.get("method")}
    return png, {"ok": True, "w": g["w"], "h": g["h"], "method": g.get("method"),
                 "bytes": len(png), "ts": time.time()}


def jpeg_available() -> bool:
    """Pillow 在不在。

    ⚠️ 这里**不能写 `import PIL`**：Pillow 是**可选**组件（不进 requirements.txt），
    而 `test_foundation_base` 的第三方 import 守卫要求"import 了就必须在 requirements 里声明" ——
    写 `find_spec` 既不真的 import、也不会被守卫误判（与 V51 §5 的可选组件纪律一致）。
    """
    import importlib.util

    try:
        return importlib.util.find_spec("PIL") is not None
    except (ImportError, ValueError):
        return False


# ── 窗口枚举与"这个 agent 的窗口是哪个"（挂在 agent 上，不让用户挑 hwnd）────────
def sources() -> list:
    """列出可抓的顶层窗口（供"手动指定一次"时选，以及给界面看能不能对上）。"""
    if not IS_WINDOWS:                                       # pragma: no cover
        return []
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lp):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        title = (buf.value or "").strip()
        if not title:
            return True
        pid = wt.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = wt.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 200 or h < 120:
            return True
        out.append({"hwnd": int(hwnd), "pid": int(pid.value), "title": title[:80],
                    "w": w, "h": h, "minimized": bool(_user32.IsIconic(hwnd))})
        return len(out) < 200

    _user32.EnumWindows(cb, 0)
    return out


def resolve_hwnd(hints: list, pid: int = 0) -> tuple:
    """按 agent 的线索找它的窗口 → `(hwnd|0, why)`。

    线索顺序：pid（最准）→ harness_name → harness_id（标题里包含）。
    **找不到就返回 0 并说清**，绝不乱猜别的窗口（抓错窗口比抓不到更糟）。
    """
    if not IS_WINDOWS:                                       # pragma: no cover
        return 0, "只在 Windows 上支持"
    wins = sources()
    if pid:
        hit = [w for w in wins if w["pid"] == int(pid)]
        if hit:
            hit.sort(key=lambda w: -(w["w"] * w["h"]))
            return hit[0]["hwnd"], "按 pid=%d 命中：%s" % (pid, hit[0]["title"])
    for h in [x for x in (hints or []) if x]:
        low = str(h).lower()
        hit = [w for w in wins if low in w["title"].lower()]
        if hit:
            hit.sort(key=lambda w: -(w["w"] * w["h"]))
            return hit[0]["hwnd"], "按标题包含「%s」命中：%s" % (h, hit[0]["title"])
    return 0, "没找到这个 agent 的窗口（标题里没有它的名字，也没有 pid 线索）"


# ── 监控名单（挂在 agent 上，可开可关）───────────────────────────────────
def watched() -> list:
    d = _load(_watched_path(), {"entries": []})
    return [e for e in (d.get("entries") or []) if isinstance(e, dict)]


def set_watched(harness_id: str, on: bool, *, hwnd: int = 0, title: str = "") -> dict:
    """开/关对某个 **agent** 的监控（不是对某个窗口）。"""
    d = _load(_watched_path(), {"entries": []})
    entries = [e for e in (d.get("entries") or []) if isinstance(e, dict)]
    cur = next((e for e in entries if e.get("harness_id") == harness_id), None)
    if on:
        now = time.time()
        if cur is None:
            if len([e for e in entries if e.get("on")]) >= max_watch():
                return {"ok": False, "why": "同时监控最多 %d 路（AC_MONITOR_MAX_WATCH）" % max_watch(),
                        "hint": "先关掉一路再开"}
            cur = {"harness_id": harness_id, "on": True, "hwnd": int(hwnd or 0),
                   "title": title, "added_at": now, "state": "stopped",
                   "why": "", "last_ok_at": 0}
            entries.append(cur)
        else:
            cur.update({"on": True, "hwnd": int(hwnd or cur.get("hwnd") or 0),
                        "title": title or cur.get("title", ""), "state": "stopped", "why": ""})
    else:
        if cur is None:
            return {"ok": True, "note": "本来就没开"}
        cur.update({"on": False, "state": "stopped", "why": "用户关掉了"})
    d["entries"] = entries
    _save(_watched_path(), d)
    return {"ok": True, "harness_id": harness_id, "on": bool(on),
            "entries": len([e for e in entries if e.get("on")])}


def touch(harness_id: str, **patch) -> None:
    d = _load(_watched_path(), {"entries": []})
    entries = [e for e in (d.get("entries") or []) if isinstance(e, dict)]
    for e in entries:
        if e.get("harness_id") == harness_id:
            e.update(patch)
    d["entries"] = entries
    _save(_watched_path(), d)


def note_viewer(harness_id: str) -> None:
    """记一次"有人在看"，供无人看自动停用。"""
    touch(harness_id, last_view_at=time.time(), state="running")


def auto_stop_idle(idle_seconds: int = 60) -> list:
    """超过 `idle_seconds` 没人拉画面 ⇒ 停抓（不后台空转）。返回被停掉的 harness_id。"""
    stopped = []
    d = _load(_watched_path(), {"entries": []})
    entries = [e for e in (d.get("entries") or []) if isinstance(e, dict)]
    now = time.time()
    for e in entries:
        if e.get("on") and e.get("state") == "running":
            if now - float(e.get("last_view_at") or 0) > idle_seconds:
                e["state"] = "stopped"
                e["why"] = "超过 %d 秒没人看，已自动停抓" % idle_seconds
                stopped.append(e.get("harness_id"))
    if stopped:
        d["entries"] = entries
        _save(_watched_path(), d)
    return stopped


# ── agent 的"简单观察"（默认只算变化；AI 每 30 分钟最多一次）──────────────────
def observe(harness_id: str, info: dict, hwnd: int = 0) -> dict:
    """看一眼：画面变没变、多久没动。**不做 AI**（AI 在 `observe_ai`，默认关）。"""
    info = info if isinstance(info, dict) else {}
    if not hwnd:
        pid = 0
        md = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
        try:
            pid = int(md.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        hwnd, why = resolve_hwnd([info.get("harness_name"), info.get("harness_id")], pid)
    else:
        why = "用户指定的窗口"
    if not hwnd:
        return {"ok": False, "why": why, "changed": False, "diff_ratio": 1.0}

    g = grab(hwnd, max_w=TILE_W)
    if not g.get("ok"):
        return {"ok": False, "why": g.get("why", "抓不到"), "hint": g.get("hint", ""),
                "changed": False, "diff_ratio": 1.0}
    sig = block_signature(g["rgb"], g["w"], g["h"])
    st = _load(_state_path(), {})
    prev = (st.get(harness_id) or {}) if isinstance(st.get(harness_id), dict) else {}
    ratio = diff_ratio(prev.get("sig", ""), sig)
    changed = ratio >= CHANGE_THRESHOLD
    still_since = prev.get("still_since") or 0
    now = time.time()
    if changed or not still_since:
        still_since = now
    st[harness_id] = {"sig": sig, "ts": now, "still_since": still_since,
                      "last_ok_at": now, "hwnd": int(hwnd)}
    _save(_state_path(), st)

    still_for = now - float(still_since or now)
    note = ""
    if not changed and still_for >= 3:
        note = "画面已经 %.0f 秒没变化（可能卡住了，也可能它就该这么静）" % still_for
    return {"ok": True, "harness_id": harness_id, "hwnd": int(hwnd), "changed": changed,
            "diff_ratio": round(ratio, 4), "still_for_seconds": round(still_for, 1),
            "note": note, "resolved_by": why, "ts": now}


def observe_ai_gate(harness_id: str) -> tuple:
    """AI 观察的闸门：默认关；开了也要**间隔 ≥ AC_MONITOR_AI_INTERVAL_MIN（默认 30 分钟）**。

    老板原话：「以后要的话也不可能是一帧一次，好歹得 30 分钟一次，而且还得能够开关」。
    """
    if not ai_enabled():
        return False, "AI 观察默认关闭（要开：AC_MONITOR_AI_OBSERVE=1，界面里也有开关）"
    st = _load(_ai_path(), {})
    last = float((st.get(harness_id) or {}).get("last_at") or 0)
    interval = ai_interval_seconds()
    if time.time() - last < interval:
        return False, "距上次 AI 观察不到 %d 分钟（最小间隔 AC_MONITOR_AI_INTERVAL_MIN）" % (interval // 60)
    return True, ""


def mark_ai_run(harness_id: str) -> None:
    p = _ai_path()
    st = _load(p, {})
    st[harness_id] = {"last_at": time.time()}
    _save(p, st)


def health() -> dict:
    """这功能现在到底能不能用（一处真相；界面据此决定亮不亮）。"""
    cap = {"windows": IS_WINDOWS, "jpeg": jpeg_available()}
    return {
        "supported": IS_WINDOWS,
        "why": "" if IS_WINDOWS else "窗口抓取只支持 Windows（当前 %s）" % sys.platform,
        "encoding": "jpeg" if cap["jpeg"] else "png",
        "jpeg_available": cap["jpeg"],
        # 本版**只做 PNG 快照墙**（老板 2026-10-22 拍板①）；连续流留到下一版。
        "stream_available": False,
        "stream_why": "本版只做 PNG 快照墙（轮询 frame）；连续流下一版再做"
                      + ("（届时需要 Pillow，当前已装）" if cap["jpeg"] else "（届时需要 Pillow）"),
        "max_watch": max_watch(), "fps": fps(),
        "ai_observe": {"enabled": ai_enabled(), "interval_min": ai_interval_seconds() // 60,
                       "note": "默认关；开了也最多每 %d 分钟一次，且可随时关" % (ai_interval_seconds() // 60)},
        "methods": ["printwindow"],
        "dir": str(_dir()),
    }


def window_state(hwnd: int) -> dict:
    """这个窗口现在什么状态（**只查状态，不抓图**，所以可以放心给列表用）。

    返回 `{exists, minimized, w, h}`；非 Windows 或没句柄时 `exists=False`。
    """
    if not IS_WINDOWS or not hwnd:                           # pragma: no cover - 平台相关
        return {"exists": False, "minimized": False, "w": 0, "h": 0}
    if not _user32.IsWindow(hwnd):
        return {"exists": False, "minimized": False, "w": 0, "h": 0}
    r = wt.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return {"exists": True, "minimized": bool(_user32.IsIconic(hwnd)),
            "w": max(0, r.right - r.left), "h": max(0, r.bottom - r.top)}


def last_result(harness_id: str) -> dict:
    """上一次观察/取帧的结果（判断"能抓/抓不到"用；**不现场抓**）。"""
    st = _load(_state_path(), {})
    row = st.get(harness_id) if isinstance(st.get(harness_id), dict) else {}
    return {"last_ok_at": float(row.get("last_ok_at") or 0),
            "hwnd": int(row.get("hwnd") or 0)}


def can_capture(harness_id: str, hwnd: int) -> tuple:
    """诚实回答"现在能不能抓到"：`(True|False|None, why)`。

    `None` = **没试过**（不猜）。只要窗口最小化/不存在/没找到，就直接给 False + 原因。
    """
    if not hwnd:
        return False, "还没认出这个 agent 的窗口"
    ws = window_state(hwnd)
    if not ws["exists"]:
        return False, "窗口已关闭（可能重启过，需要重新指定一次）"
    if ws["minimized"]:
        return False, "窗口最小化了 —— 最小化时系统不渲染内容，抓不到"
    last = last_result(harness_id)
    if last["last_ok_at"] > 0:
        return True, ""
    return None, "还没抓过；开监控后点开就知道能不能抓"


BRIDGE_READY_STATUSES = ("tested",)
"""桥算"架通"的唯一口径：`protocol.py` 里 `bridge_status` 的取值是
`""`（未登记）/ `"reported"`（只登记了路径）/ `"tested"`（**桥测试真通过**）。
⇒ 只有 `tested` 才算"已架桥"（老板说的"完整注册架桥后"）。**不许**把 `reported` 当通。"""


def eligible(bridge_status: str) -> tuple:
    """这个 agent 现在该不该出现在监控室的可选名单里：`(bool, why)`。"""
    st = (bridge_status or "").strip()
    if st in BRIDGE_READY_STATUSES:
        return True, ""
    if st == "reported":
        return False, "桥只登记了路径、还没跑通测试 —— 先在对接中心把桥测通，再来开监控"
    return False, "这个 agent 还没架桥 —— 注册并架桥之后才能监控它的窗口"
