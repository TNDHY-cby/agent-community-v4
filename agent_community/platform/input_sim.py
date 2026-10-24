# -*- coding: utf-8 -*-
"""**表层交互**：真鼠标、真键盘（Windows `SendInput`）—— 老板 2026-10-23 定的方向。

## 为什么需要它（而不是继续往应用内部想办法）

2026-10-23 真机实测（Trae / Electron）：
- 窗口能置顶（`is_foreground=True`）、剪贴板内容能核对、`Ctrl+V` 也确实按了；
- 但**文字没进输入框** —— Electron 没有原生文本框子窗口，内部焦点不在输入框时，
  系统层的粘贴没有落点；平台**拿不到**它的内部元素。

⇒ 换思路：**像人一样从表层操作** —— 先把鼠标点到输入框（位置由用户"教一次"），
再用 `SendInput` 逐字键入或粘贴。这样不依赖应用暴露任何内部结构。

## 与"应用专属脚本"的区别（反私人定制）

- 本模块**不认识任何具体应用**：不发 `Ctrl+K` 这类某家 IDE 的快捷键，也不猜坐标；
- 点击位置来自**用户教的那一次**（存成窗口内的**相对坐标 0~1**），换窗口大小仍然对得上；
- 没有教过位置时**如实说"没教过"**，不乱点（乱点会点坏人家的东西）。

## 诚实边界（写在返回值里，不粉饰）

- `send_text` 只保证"**把按键事件送进系统输入队列**"；目标是否收到，取决于它当前焦点。
  能读回的目标（原生文本框）用 `visual_launch` 的 `-Verify` 判；读不回的（Electron）**只能如实说未核实**。
- 非 Windows：`supported=False` + 说清"这套机制只在 Windows 上存在"（不假装成功）。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

_IS_WINDOWS = os.name == "nt"

#: `SendInput` 的常量（Windows 定义；非 Windows 下不会被用到）
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

#: 本进程是否已经声明 DPI 感知（声明过才会拿到**物理像素**坐标）
_DPI_DONE = False


def ensure_dpi_aware() -> bool:
    """声明 DPI 感知 —— **不做这一步，坐标会按 125%/150% 缩放算，点偏**。

    实测（老板这台机）：不声明时 `GetSystemMetrics(SM_CXSCREEN)` 返回 1707×1067，
    而屏幕其实是 3072×1920（175% 缩放）；拿"逻辑坐标"去 `SendInput` 绝对坐标必然点偏。
    依次尝试：Per-Monitor-V2 → Per-Monitor → System aware（哪一步成功用哪步）。
    """
    global _DPI_DONE
    if _DPI_DONE or not _IS_WINDOWS:
        return _DPI_DONE
    u = _user32()
    ok = False
    try:                                                     # Win10 1703+
        ctx = ctypes.c_void_p(-4)                            # PER_MONITOR_AWARE_V2
        ok = bool(u.SetProcessDpiAwarenessContext(ctx))
    except Exception:                                        # noqa: BLE001 — 老系统没有这个 API，往下退
        ok = False
    if not ok:
        try:                                                 # Win8.1+
            shcore = ctypes.WinDLL("shcore")
            ok = shcore.SetProcessDpiAwareness(2) == 0       # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:                                    # noqa: BLE001 — 再往老退
            ok = False
    if not ok:
        try:                                                 # Vista+
            ok = bool(u.SetProcessDPIAware())
        except Exception:                                    # noqa: BLE001
            ok = False
    _DPI_DONE = ok
    return ok


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


# ★2026-10-24 修（**非本次引入**的跨平台硬伤）：`ctypes.WINFUNCTYPE` **只存在于 Windows**，
#   原来在模块顶层无条件求值 ⇒ **Linux 上一 import 这个模块就 AttributeError**，
#   表现为「凡是用到表层交互的测试在 Linux 全红（本机 Windows 全绿）」。现在非 Windows 下留空。
if _IS_WINDOWS:
    _EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
else:                                                          # pragma: no cover — 仅非 Windows
    _EnumChildProc = None


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def supported() -> bool:
    return _IS_WINDOWS


def screen_size() -> tuple:
    """**整个虚拟桌面**的尺寸（多显示器时是所有屏幕合起来的范围）。"""
    if not _IS_WINDOWS:
        return (0, 0)
    ensure_dpi_aware()
    u = _user32()
    return (int(u.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
            int(u.GetSystemMetrics(SM_CYVIRTUALSCREEN)))


def virtual_origin() -> tuple:
    """虚拟桌面左上角在"屏幕坐标系"里的位置（多显示器可能有负值）。"""
    if not _IS_WINDOWS:
        return (0, 0)
    ensure_dpi_aware()
    u = _user32()
    return (int(u.GetSystemMetrics(SM_XVIRTUALSCREEN)),
            int(u.GetSystemMetrics(SM_YVIRTUALSCREEN)))


def cursor_pos() -> tuple:
    """当前鼠标位置（**屏幕坐标**，已 DPI 感知）；非 Windows 返回 `(0, 0)`。"""
    if not _IS_WINDOWS:
        return (0, 0)
    ensure_dpi_aware()
    pt = wintypes.POINT()
    _user32().GetCursorPos(ctypes.byref(pt))
    return (int(pt.x), int(pt.y))


def _send_inputs(items: list) -> int:
    """把一串 `_INPUT` 送进系统输入队列（返回实际发送条数）。"""
    if not items:
        return 0
    arr = (_INPUT * len(items))(*items)
    u = _user32()
    sent = u.SendInput(len(items), ctypes.byref(arr), ctypes.sizeof(_INPUT))
    return int(sent)


def _key_event(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)
    return _INPUT(type=INPUT_KEYBOARD, union=_INPUTUNION(ki=ki))


def _mouse_event(dx: int, dy: int, flags: int) -> _INPUT:
    mi = _MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=None)
    return _INPUT(type=INPUT_MOUSE, union=_INPUTUNION(mi=mi))


def click_screen(x: int, y: int, *, button: str = "left", settle: float = 0.05) -> dict:
    """在**屏幕坐标**上真点一下（移动 + 按下 + 抬起）。

    返回 `{ok, x, y, why}`；失败原因如实给（不是"静默不动"）。
    """
    if not _IS_WINDOWS:
        return {"ok": False, "x": int(x), "y": int(y), "why": "非 Windows：鼠标模拟只在 Windows 上存在"}
    ensure_dpi_aware()
    vx, vy = virtual_origin()
    vw, vh = screen_size()
    if vw <= 0 or vh <= 0:
        return {"ok": False, "x": int(x), "y": int(y), "why": "拿不到屏幕尺寸"}
    if not (vx <= int(x) < vx + vw and vy <= int(y) < vy + vh):
        return {"ok": False, "x": int(x), "y": int(y),
                "why": "坐标 (%s,%s) 不在虚拟桌面范围 (%s,%s)-(%s,%s) 内"
                       % (x, y, vx, vy, vx + vw - 1, vy + vh - 1)}
    # 绝对坐标是"相对虚拟桌面 0..65535"的归一化值（带 VIRTUALDESK 才按整个桌面算）
    nx = int(round((int(x) - vx) * 65535 / max(1, vw - 1)))
    ny = int(round((int(y) - vy) * 65535 / max(1, vh - 1)))
    base = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    down = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    up = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP
    n = _send_inputs([_mouse_event(nx, ny, base),
                      _mouse_event(nx, ny, base | down),
                      _mouse_event(nx, ny, base | up)])
    time.sleep(settle)
    return {"ok": n == 3, "x": int(x), "y": int(y),
            "why": "" if n == 3 else "SendInput 只送出了 %d/3 条事件" % n}


def send_text(text: str, *, per_char_delay: float = 0.004) -> dict:
    """用 `KEYEVENTF_UNICODE` **逐字**键入（中文/emoji 都行，不经过输入法）。

    为什么不用粘贴：粘贴要把内容放进剪贴板（会被别的程序占用、还会覆盖用户的剪贴板）；
    直接送 unicode 按键既不动剪贴板，也不依赖目标有没有文本框子窗口。
    """
    if not _IS_WINDOWS:
        return {"ok": False, "sent": 0, "why": "非 Windows：键盘模拟只在 Windows 上存在"}
    text = str(text or "")
    if not text:
        return {"ok": False, "sent": 0, "why": "要输入的内容是空的"}
    total = 0
    # 分批送：一次几百个字符的 SendInput 在某些应用里会被丢尾部
    for i in range(0, len(text), 200):
        chunk = text[i:i + 200]
        items = []
        for ch in chunk:
            code = ord(ch)
            # 代理对（emoji）要拆成两个 UTF-16 单元分别送
            if code > 0xFFFF:
                code -= 0x10000
                hi = 0xD800 + (code >> 10)
                lo = 0xDC00 + (code & 0x3FF)
                for unit in (hi, lo):
                    items.append(_key_event(scan=unit, flags=KEYEVENTF_UNICODE))
                    items.append(_key_event(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            else:
                items.append(_key_event(scan=code, flags=KEYEVENTF_UNICODE))
                items.append(_key_event(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        n = _send_inputs(items)
        total += n
        time.sleep(per_char_delay * len(chunk))
    want = sum(2 if ord(ch) > 0xFFFF else 1 for ch in text) * 2
    ok = total >= want - 2 * max(1, len(text) // 50)      # 允许极少量丢包，少了要如实说
    return {"ok": ok, "sent": total, "expected": want,
            "why": "" if ok else "只送出了 %d/%d 条按键事件（目标可能吞了尾部）" % (total, want)}


#: 常用按键 → (虚拟键码, 扫描码)。只列通用键，**不含任何应用的专用快捷键**。
_VK = {
    "enter": (0x0D, 0x1C), "tab": (0x09, 0x0F), "esc": (0x1B, 0x01),
    "space": (0x20, 0x39), "backspace": (0x08, 0x0E), "delete": (0x2E, 0x53),
    "up": (0x26, 0x48), "down": (0x28, 0x50), "left": (0x25, 0x4B), "right": (0x27, 0x4D),
    "home": (0x24, 0x47), "end": (0x23, 0x4F),
    "ctrl": (0x11, 0x1D), "shift": (0x10, 0x2A), "alt": (0x12, 0x38),
    "a": (0x41, 0x1E), "c": (0x43, 0x2E), "v": (0x56, 0x2F), "x": (0x58, 0x2D),
    "z": (0x5A, 0x2C), "f": (0x46, 0x21),
}


def send_keys(combo: str, *, settle: float = 0.05) -> dict:
    """按键组合，例如 `"ctrl+v"`、`"enter"`、`"ctrl+shift+v"`（全小写，用 `+` 连接）。

    实现用扫描码（`KEYEVENTF_SCANCODE`）——比虚拟键码更贴近"真实按键"，
    Chromium/Electron 这类对合成键有过滤的应用更容易接受。
    """
    if not _IS_WINDOWS:
        return {"ok": False, "why": "非 Windows：键盘模拟只在 Windows 上存在"}
    parts = [p.strip().lower() for p in str(combo or "").split("+") if p.strip()]
    if not parts:
        return {"ok": False, "why": "按键组合是空的"}
    unknown = [p for p in parts if p not in _VK]
    if unknown:
        return {"ok": False, "why": "不认识的键：%s（可用：%s）"
                                   % ("、".join(unknown), "、".join(sorted(_VK)))}
    downs = [_key_event(vk=_VK[p][0], scan=_VK[p][1], flags=KEYEVENTF_SCANCODE) for p in parts]
    ups = [_key_event(vk=_VK[p][0], scan=_VK[p][1], flags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
           for p in reversed(parts)]
    n = _send_inputs(downs + ups)
    time.sleep(settle)
    return {"ok": n == len(downs) + len(ups), "sent": n, "combo": "+".join(parts),
            "why": "" if n == len(downs) + len(ups) else "只送出了 %d/%d 条事件" % (n, len(downs) + len(ups))}


def window_client_rect(hwnd: int) -> dict:
    """窗口**客户区**在屏幕上的矩形（`left/top/width/height`）；拿不到就如实返回空。"""
    if not _IS_WINDOWS:
        return {}
    rect = wintypes.RECT()
    u = _user32()
    if not u.GetClientRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)):
        return {}
    pt = wintypes.POINT(0, 0)
    u.ClientToScreen(wintypes.HWND(int(hwnd)), ctypes.byref(pt))
    return {"left": int(pt.x), "top": int(pt.y),
            "width": int(rect.right - rect.left), "height": int(rect.bottom - rect.top)}


def window_rect(hwnd: int) -> dict:
    """窗口**整体**矩形（含标题栏边框，屏幕坐标）—— 比客户区更稳：客户区对 Chromium 子窗会给出怪值。"""
    if not _IS_WINDOWS:
        return {}
    rect = wintypes.RECT()
    if not _user32().GetWindowRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)):
        return {}
    return {"left": int(rect.left), "top": int(rect.top),
            "width": int(rect.right - rect.left), "height": int(rect.bottom - rect.top)}


def content_rect(hwnd: int) -> dict:
    """窗口里**内容区**（页面视口 / 控件区）在屏幕上的矩形 —— **会用 WindowFromPoint 自证**。

    为什么要"自证"（2026-10-23 实测抓到的坑，代价是一整轮排查）：
      EnumChildWindows 拿到的不一定是**此刻显示在你面前的那个控件** —— 我按"类名含
      Chrome_RenderWidgetHostHWND"取到过一个**别的窗口**的渲染控件（它的矩形覆盖了同样位置），
      于是点击落到了**别人家的窗口**上：光标位置对、窗口也是前台，可页面里 activeElement 永远是 BODY。
      ⇒ 判据必须是：候选矩形中心点用 WindowFromPoint 看，**顶层祖先就是本窗口**才算数；
        候选都不合格就退回窗口客户区，仍不合格才如实说"拿不到内容区"。
    """
    if not _IS_WINDOWS:
        return {}
    u = _user32()
    parent = wintypes.HWND(int(hwnd))
    pr = wintypes.RECT()
    if not u.GetWindowRect(parent, ctypes.byref(pr)):
        return window_client_rect(hwnd)
    cands = []

    def _cb(h, _l):
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, cls, 256)
        r = wintypes.RECT()
        if u.GetWindowRect(h, ctypes.byref(r)):
            w = int(r.right - r.left)
            hh = int(r.bottom - r.top)
            if w > 40 and hh > 40 and u.IsWindowVisible(h):
                cands.append({"hwnd": int(h), "cls": cls.value or "", "w": w, "hh": hh,
                              "left": int(r.left), "top": int(r.top),
                              "inside": (r.left >= pr.left - 2 and r.top >= pr.top - 2
                                          and r.right <= pr.right + 2 and r.bottom <= pr.bottom + 2)})
        return True

    proc = _EnumChildProc(_cb)
    u.EnumChildWindows(parent, proc, 0)

    def _hits_self(c) -> bool:
        """候选中心点落在谁的窗口上？顶层祖先是不是本窗口。"""
        cx = c["left"] + c["w"] // 2
        cy = c["top"] + c["hh"] // 2
        hit = u.WindowFromPoint(wintypes.POINT(cx, cy))
        if not hit:
            return False
        return int(u.GetAncestor(wintypes.HWND(hit), 2)) == int(hwnd)   # GA_ROOT = 2

    order = []
    for want in ("Chrome_RenderWidgetHostHWND", "Internet Explorer_Server", "MozillaWindowClass"):
        order += [c for c in cands if want.lower() in c["cls"].lower()]
    order += sorted([c for c in cands if c not in order], key=lambda c: -(c["w"] * c["hh"]))
    for c in order:
        if not c["inside"]:
            continue
        if _hits_self(c):
            return {"left": c["left"], "top": c["top"], "width": c["w"], "height": c["hh"],
                    "frame": "render-child" if "RenderWidgetHost" in c["cls"] else "largest-child"}
    cr = window_client_rect(hwnd)
    if cr:
        cr["frame"] = "client"
    return cr


def click_in_window(hwnd: int, rel_x: float, rel_y: float, *, button: str = "left") -> dict:
    """在窗口里按**相对坐标**（0~1）点一下 —— 换窗口大小仍然对得上。"""
    r = content_rect(hwnd) or window_rect(hwnd) or window_client_rect(hwnd)
    if not r or r["width"] <= 0 or r["height"] <= 0:
        return {"ok": False, "why": "拿不到窗口 %s 的客户区矩形" % hwnd}
    rx = min(max(float(rel_x), 0.0), 0.999)
    ry = min(max(float(rel_y), 0.0), 0.999)
    x = r["left"] + int(round(rx * r["width"]))
    y = r["top"] + int(round(ry * r["height"]))
    # ★2026-10-23（一整轮排查换来的）：**点之前先自证**这个点落在目标窗口上。
    #   实测过：目标窗口被别的窗口挡住时，坐标算得再准，点下去也是点在**别人家**窗口上 ——
    #   表现就是"点了、也按了 Ctrl+V，字却没进输入框"。这种情况必须**如实拒绝**，不许静默。
    if int(x) < -10000 or int(y) < -10000:
        # Windows 给"最小化"的窗口一个 -32000 左右的占位坐标（实测 Trae 最小化时就是 -21254,-21310）
        return {"ok": False, "x": int(x), "y": int(y), "rel_x": rx, "rel_y": ry, "rect": r,
                "why": "目标窗口**最小化了**（坐标 %s,%s 是 Windows 给最小化窗口的占位值）—— "
                       "先把它恢复出来再试；为了不误伤别人程序，**没有点击**" % (x, y)}
    u_ = _user32()
    _hit = u_.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not _hit or int(u_.GetAncestor(wintypes.HWND(_hit), 2)) != int(hwnd):
        return {"ok": False, "x": int(x), "y": int(y), "rel_x": rx, "rel_y": ry, "rect": r,
                "why": "这个点(%s,%s)现在落在**别的窗口**上（目标窗口可能被挡住/没真到前台）—— "
                       "为了不把话打进别人程序里，**没有点击**" % (x, y)}
    res = click_screen(x, y, button=button)
    res.update({"rel_x": rx, "rel_y": ry, "rect": r})
    return res


def foreground_window() -> int:
    """当前**前台窗口**的句柄（0 = 拿不到）。按键会送到它那儿，所以按之前要先问它。"""
    if not _IS_WINDOWS:
        return 0
    ensure_dpi_aware()
    try:
        return int(_user32().GetForegroundWindow() or 0)
    except Exception:                                        # noqa: BLE001 — 拿不到就说 0，别猜
        return 0


def ensure_foreground(hwnd: int, *, tries: int = 6, settle: float = 0.12) -> dict:
    """把窗口**真的**抬到前台并**自证**（返回 `{ok, foreground, why}`）。

    为什么单独有一个函数（2026-10-24 真机踩到）：
    投递流程里"先点一下输入框"发生在**很早**，之后还要走 native/uia 两档（各是一次 PowerShell 注入，
    几秒钟），等到剪贴板档真要按 Ctrl+V 时，**前台早就不是目标窗口了** ——
    实测结果：点了、也按了 Ctrl+V，字没进框（粘到别处或者没人接），而返回里看不出来。
    ⇒ 临按之前必须**重新自证前台**，不成立就不按（宁可如实失败）。

    做法：`ShowWindow(SW_RESTORE)` → `SetForegroundWindow` → 轮询确认；
    Windows 有"前台锁"，刚被别的程序占领时 SetForegroundWindow 可能不生效，所以**轮询几次**再判失败。
    """
    if not _IS_WINDOWS or not hwnd:
        return {"ok": False, "foreground": 0, "why": "非 Windows 或没给窗口句柄"}
    ensure_dpi_aware()
    u = _user32()
    h = wintypes.HWND(int(hwnd))
    try:
        u.ShowWindow(h, 9)                                   # SW_RESTORE
    except Exception:                                        # noqa: BLE001 — 恢复不了也照样试前台
        pass
    try:
        u.SetForegroundWindow(h)
    except Exception:                                        # noqa: BLE001 — 前台锁会让它失败，靠下面轮询判
        pass
    fg = 0
    for _ in range(max(1, int(tries))):
        time.sleep(float(settle))
        fg = foreground_window()
        if fg == int(hwnd):
            return {"ok": True, "foreground": fg, "why": ""}
    # ★2026-10-24 真机补：平台是**后台进程**时，`SetForegroundWindow` 常被 Windows 前台锁直接拒掉
    #   （前台被用户正开着的窗口占着）⇒ 表现就是"永远抬不到前台、于是永远不粘"。
    #   用官方承认的 `AttachThreadInput` 临时挂到前台线程的输入队列上再要前台。
    if _force_foreground(int(hwnd)):
        for _ in range(max(1, int(tries))):
            time.sleep(float(settle))
            fg = foreground_window()
            if fg == int(hwnd):
                return {"ok": True, "foreground": fg, "why": "", "forced": True}
    return {"ok": False, "foreground": fg,
            "why": "抬不到前台（现在的前台是别的窗口 %s）—— 为了不把内容粘进别人程序里，没有按 Ctrl+V" % fg}


def _force_foreground(hwnd: int) -> bool:
    """**硬把窗口抢到前台**（`SetForegroundWindow` 被前台锁挡住时用）。

    做法（Windows 官方文档承认的路子，仍是用户态 API）：
    `AttachThreadInput(本线程, 当前前台线程, True)` → `BringWindowToTop` + `SetForegroundWindow` → 解绑。
    2026-10-24 真机：平台作为后台进程调用 `SetForegroundWindow` 直接被拒，
    导致"抬不到前台 ⇒ 不粘"，整条视觉激活在服务里等于不可用。
    """
    u = _user32()
    try:
        fg = int(u.GetForegroundWindow() or 0)
        cur_tid = int(ctypes.windll.kernel32.GetCurrentThreadId())
        fg_tid = int(u.GetWindowThreadProcessId(wintypes.HWND(fg), None)) if fg else 0
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(u.AttachThreadInput(cur_tid, fg_tid, True))
        try:
            u.BringWindowToTop(wintypes.HWND(int(hwnd)))
            u.SetForegroundWindow(wintypes.HWND(int(hwnd)))
        finally:
            if attached:
                u.AttachThreadInput(cur_tid, fg_tid, False)
        return True
    except Exception as e:                                    # noqa: BLE001 — 抢不到就如实说，别硬撑
        print("[input_sim] 强制抬前台失败：%s: %s" % (type(e).__name__, e), flush=True)
        return False


def set_foreground(hwnd: int, *, settle: float = 0.08) -> dict:
    """把某个窗口换到前台（**不**做 `ShowWindow` 恢复，别把人家最大化的窗口还原成小窗）。

    用来"收尾"：视觉脚本跑完要把平台自己换回最前（老板 2026-10-24 规格 ③②：
    「最后将平台拉至最前，恢复合作社展示」）。拿不到就如实说，不动。
    """
    if not _IS_WINDOWS or not hwnd:
        return {"ok": False, "why": "非 Windows 或没给窗口句柄"}
    ensure_dpi_aware()
    try:
        _user32().SetForegroundWindow(wintypes.HWND(int(hwnd)))
    except Exception as e:                                    # noqa: BLE001 — 前台锁会让它失败
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}
    time.sleep(float(settle))
    fg = foreground_window()
    return {"ok": fg == int(hwnd), "foreground": fg,
            "why": "" if fg == int(hwnd) else "没能把窗口 %s 换到前台（现在前台是 %s）" % (hwnd, fg)}


def enum_windows() -> list:
    """**本机可见窗口一览**（纯 ctypes，~0.1 秒；不起 PowerShell 子进程）。

    为什么要它（2026-10-24 实测）：原来列窗口靠 `visual_launch._run_inject` 起一次 PowerShell，
    **单次约 1.3 秒**；界面上"输模糊名→确定"要连搜几次 ⇒ 用户等 4 秒，而且每次都多一个子进程
    （老板说的"脚本进程太多"就是这类）。ctypes 直接 `EnumWindows` 只要 ~0.1 秒。

    每项：`{hwnd, pid, class, title, width, height, client_w, client_h, minimized}`；
    只要**有标题**的可见顶层窗口（没标题的基本是内部/工具窗，用户也认不出来）。
    """
    if not _IS_WINDOWS:
        return []
    ensure_dpi_aware()
    u = _user32()
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lp):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            n = int(u.GetWindowTextLengthW(hwnd) or 0)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 2)
            u.GetWindowTextW(hwnd, buf, n + 2)
            title = (buf.value or "").strip()
            if not title:
                return True
            pid = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            r = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            c = wintypes.RECT()
            u.GetClientRect(hwnd, ctypes.byref(c))
            out.append({"hwnd": int(hwnd), "pid": int(pid.value),
                        "class": (cls.value or "").strip(), "title": title,
                        "width": int(r.right - r.left), "height": int(r.bottom - r.top),
                        "client_w": int(c.right - c.left), "client_h": int(c.bottom - c.top),
                        "minimized": bool(u.IsIconic(hwnd))})
        except Exception:                                    # noqa: BLE001 — 单个窗口读失败就跳过
            pass
        return True

    try:
        u.EnumWindows(_cb, 0)
    except Exception as e:                                   # noqa: BLE001 — 枚举不起来就如实空
        print("[input_sim] 枚举窗口失败：%s: %s" % (type(e).__name__, e), flush=True)
        return []
    _ = k
    return out


def screen_to_relative(hwnd: int, x: int, y: int) -> dict:
    """屏幕坐标 → 窗口客户区的相对坐标（"教一次位置"时用）。"""
    r = window_rect(hwnd) or window_client_rect(hwnd)
    if not r or r["width"] <= 0 or r["height"] <= 0:
        return {}
    rx = (int(x) - r["left"]) / float(r["width"])
    ry = (int(y) - r["top"]) / float(r["height"])
    return {"rel_x": round(rx, 4), "rel_y": round(ry, 4),
            "inside": 0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0, "rect": r}


def platform_note() -> str:
    """一句话说清本平台支持到什么程度（给界面/文档用，别让人猜）。"""
    if _IS_WINDOWS:
        return "Windows：鼠标点击与逐字键入都可用（SendInput；中文走 unicode 事件，不需要输入法）"
    return "非 Windows：本平台目前不提供鼠标/键盘模拟（如实不支持，不假装成功）"
