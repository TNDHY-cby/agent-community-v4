# -*- coding: utf-8 -*-
"""platform/visual_launch：**把文本真的送进某个成员的会话窗口** —— "视觉拉起"的唯一实现。

## 为什么有这个模块（2026-10-15 核实的缺口）

步骤 9「更新视觉脚本」一直**只做到"名字进数据"**：平台把会话名写进 `member.session_aliases`，
然后……没有然后了 —— **全仓没有任何地方调用 `tools/inject_window.ps1`**
（那把脚本早就写了、V25 §5 实测 `WM_PASTE` **两次两次完整送达**，只有脚本模板里提了它一句）。
本模块就是那一环：**按会话名把载荷送进去，并如实回报成功/失败。**

## 档位＝照抄 `V25_唤醒与寻址设计.md` §5，**不新增**

| 档 | 手段 | 本模块 |
|---|---|---|
| — | Windows 消息式注入（`WM_PASTE`） | ✅ `method="wmpaste"`（非 Windows **如实返回不支持**） |
| f | **半自动兜底**：只把载荷放剪贴板，**人点一次会话**再粘贴 | ✅ `method="clipboard"`（跨平台，**永远可用**） |

OCR / CV / UIAutomation 那些档**故意不在核心**（V25 §5 的依赖决定：与"装好就能用"冲突，
做成按 harness 逐个落地的可选组件）。

## 纪律

1. **不假装成功**：窗口不匹配 / 没有文本控件 / 非 Windows / 没有剪贴板工具 —— 一律如实说，
   并把"人该做什么"写进 `next_step`；
2. **不抢用户前台**：注入走消息式（`WM_PASTE` 直接投给子控件），脚本若报
   `[WARN] window did not come to the foreground` 我们**原样透传**（那是"可能被 UIPI 挡住"的信号，不是失败）；
3. **一处真相**：剪贴板直接复用 `wakeup_bridge` 的 `_set_clipboard()`（它已经处理了
   Windows `Set-Clipboard` 与 POSIX `xclip`/`xsel`/`pbcopy` 三档，以及"一个都没有时怎么告诉用户"）；
4. 本模块里的函数都是**同步阻塞**的（要起子进程）⇒ **调用方必须 `await asyncio.to_thread(...)`**
   （铁律：async 里绝不许同步阻塞）。

设计稿：`design-docs/V51_分工跑通与视觉拉起设计.md`。
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

#: 与 `tools/watchdog.py` 同一套"按路径定位项目根"的写法（本模块总在包内，路径稳定）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INJECT_PS1 = PROJECT_ROOT / "agent_community" / "tools" / "inject_window.ps1"

_IS_WINDOWS = os.name == "nt"
_IS_POSIX = os.name == "posix"

#: 允许的档位（`auto` 由本模块挑）
METHODS = ("auto", "wmpaste", "clipboard")

#: ★2026-10-23（老板真机报"点一次试投只弹几个黑框"）：起 `powershell.exe` 时**必须**不弹控制台，
#: 否则每调用一次就闪一个黑框（列窗口一次、注入一次、聚焦一次、写剪贴板一次 ⇒ 屏幕上连闪）。
#: ⚠️ 平台相关的子进程开关**只有一处真相**：`platform/spawn.py`。这里只做"要隐藏窗口"的薄包装，
#:    不自己再写一份 `0x08000000`（守卫 `test_subprocess_platform_flags_live_in_one_place` 会抓）。
def _hidden_kwargs() -> dict:
    """子进程参数：Windows 上**不弹控制台窗口**（其它平台返回空 dict）。"""
    from .spawn import spawn_kwargs

    return spawn_kwargs(detach=False, no_window=True)


def session_name_of(member) -> str:
    """成员的**会话名**（＝视觉脚本的匹配键）。

    取 `session_aliases[0]`（平台在步骤 9 生成的那个），空则退回 `session_ref.session_name`。
    **脚本不写死名字**：这个名字随时可能被用户改（V25 的要求），所以每次都从数据里读。
    """
    aliases = [str(a).strip() for a in (getattr(member, "session_aliases", None) or []) if str(a).strip()]
    if aliases:
        return aliases[0]
    ref = getattr(member, "session_ref", None) or {}
    return str((ref or {}).get("session_name") or "").strip()


def _run_inject(args: list[str], timeout: float = 45.0) -> tuple[int, str]:
    """跑 `inject_window.ps1`（Windows 专属；其它平台返回"不支持"）。

    返回 `(returncode, 输出)`；`returncode=127` 表示本平台不支持，`124` 表示超时。
    另外两个失败码（2026-10-23 补，都是"看着像成功其实是空投"的情形）：
    `5`＝剪贴板写不进去（被别的程序占着/这个会话没有剪贴板）；`6`＝窗口没置顶，拒绝键鼠注入。
    """
    if not _IS_WINDOWS:
        return 127, "非 Windows：消息式界面注入（WM_PASTE）只在 Windows 上存在"
    if not INJECT_PS1.exists():
        return 127, "找不到注入脚本 %s（它随仓库发布）" % INJECT_PS1.name
    argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INJECT_PS1)] + args
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, **_hidden_kwargs())
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "注入脚本超时（%ss）—— 目标窗口可能弹了模态框" % int(timeout)
    except Exception as e:  # noqa: BLE001
        return 127, "注入脚本没能跑起来：%s: %s" % (type(e).__name__, e)


#: `inject_window.ps1 -List` 的一行长这样：
#: `  hwnd=1969346      pid=12704   class=WindowsForms10.Window.8.app.0.8b7966_r8_ad1 title=ws-parse-项目-组员-m0`
#: ⚠️**必须用正则**（2026-10-15 实测踩过）：那几列是用 `{2,-28}` 这类**宽度对齐**打的，
#: 而 WinForms / Electron 的窗口类名**会超过 28 字符** ⇒ 列之间**只剩一个空格**。
#: 原来按"两个空格"切分 ⇒ `class=` 的值把 ` title=` 一起吃掉、**title 解析成 None**
#: ⇒ `options()` 永远报"0 个候选窗口"（而 `launch()` 因为把 `-TitleLike` 交给脚本、不解析文本，照样能用）
#: —— 界面因此显示"没有可注入的窗口"，实际有。**别解析人类可读的对齐格式**，要么正则要么让它吐 JSON。
_LIST_LINE_RE = re.compile(
    r"hwnd=(?P<hwnd>\S+)\s+pid=(?P<pid>\S+)\s+class=(?P<cls>.*?)\s+title=(?P<title>.*)$")


def _parse_list_line(line: str) -> dict:
    """把 `-List` 的一行解析成 `{hwnd,pid,class,title}`（解析不出返回 `{}`）。"""
    m = _LIST_LINE_RE.search(line.strip())
    if not m:
        return {}
    return {"hwnd": m.group("hwnd").strip(), "pid": m.group("pid").strip(),
            "class": m.group("cls").strip(), "title": m.group("title").strip()}


def list_windows(title_like: str = "", timeout: float = 30.0, *, fast: bool = False) -> dict:
    """列出候选窗口（只读，不改任何东西）。`-List` 的输出见 `_LIST_LINE_RE` 上方说明。

    **2026-10-24 加了快路**（`fast=True` 才走）：纯 ctypes 枚举（`input_sim.enum_windows`，实测 **3ms**、不起子进程），
    拿不到再退回原来的 PowerShell 注入（**1.1 秒/次 + 一个子进程** —— 老板说的"脚本进程太多"就是这类）。
    快路额外带回真实尺寸与最小化状态（`width/height/client_w/client_h/minimized`），
    调用方不用再逐个 `window_info()`。
    """
    try:
        from . import input_sim

        _fastwins = input_sim.enum_windows() if fast else []
        if _fastwins:
            wins = [{"hwnd": str(w["hwnd"]), "pid": str(w.get("pid") or ""),
                     "class": w.get("class") or "", "title": w.get("title") or "",
                     "width": w.get("width"), "height": w.get("height"),
                     "client_w": w.get("client_w"), "client_h": w.get("client_h"),
                     "minimized": w.get("minimized")} for w in _fastwins]
            if title_like:
                wins = [w for w in wins if title_like.lower() in (w.get("title") or "").lower()]
            return {"ok": True, "supported": True, "detail": "共 %d 个候选窗口（快路 ctypes）" % len(wins),
                    "windows": wins, "fast": True}
    except Exception as e:                                    # noqa: BLE001 — 快路挂了就退注入，别静默
        print("[visual_launch] 快路枚举失败，退回 PowerShell 注入：%s: %s" % (type(e).__name__, e),
              flush=True)
    rc, out = _run_inject(["-List"], timeout=timeout)
    if rc == 127:
        return {"ok": False, "supported": False, "detail": out, "windows": []}
    if rc != 0:
        return {"ok": False, "supported": True, "detail": out[-300:], "windows": []}
    wins = []
    for ln in out.splitlines():
        if "hwnd=" not in ln:
            continue
        item = _parse_list_line(ln)
        if item.get("hwnd"):
            wins.append(item)
    if title_like:
        wins = [w for w in wins if title_like.lower() in (w.get("title") or "").lower()]
    return {"ok": True, "supported": True, "detail": "共 %d 个候选窗口" % len(wins), "windows": wins}


def window_info(hwnd: int) -> dict:
    """窗口的一条**真实体检**：`{hwnd, width, height, client_w, client_h, minimized}`。

    ★2026-10-24 真机教训：`list_windows()` 每一项**只有 hwnd/pid/class/title，没有尺寸、也不说最小化**，
    而调用方原来都在按 `w.get("width")` 排序挑"最大的那个"—— **排序等于没排**，谁先出现就用谁。
    实测后果：同一个 Trae 有两个窗口（主窗 2163x1353 已最小化；另一个是 93x150 的浮层），
    截图端点挑中了浮层，用户看到「✗ 窗口客户区太小（93x150），不像主窗口」，而他真正想截的那个窗口
    当时是**最小化**的（`GetWindowRect` 给的是 -32000 占位坐标、尺寸 237x39）。
    """
    from . import input_sim

    h = int(hwnd or 0)
    out = {"hwnd": h, "width": 0, "height": 0, "client_w": 0, "client_h": 0, "minimized": None}
    if not h:
        return out
    try:
        input_sim.ensure_dpi_aware()             # 不声明 DPI 拿到的是缩放后的逻辑尺寸
        r = input_sim.window_rect(h) or {}
        out["width"], out["height"] = int(r.get("width") or 0), int(r.get("height") or 0)
        c = input_sim.window_client_rect(h) or {}
        out["client_w"], out["client_h"] = int(c.get("width") or 0), int(c.get("height") or 0)
        import ctypes
        from ctypes import wintypes

        u = ctypes.WinDLL("user32", use_last_error=True)
        out["minimized"] = bool(u.IsIconic(wintypes.HWND(h)))
    except Exception as e:                                    # noqa: BLE001 — 量不到就留 0，并说清
        out["why"] = "%s: %s" % (type(e).__name__, e)
    return out


def fuzzy_candidates(windows, query: str, *, limit: int = 20, min_sim: float = 0.3) -> list:
    """按**相似度**排出"最像这个模糊名"的窗口（老板 2026-10-24：「平台根据相似程度搜索窗口」）。

    为什么不能只做"标题包含"：用户输 `trea` 时 `TRAE CN` 一条都不会命中（真想输对就不用模糊了）。
    做法（纯标准库）：
    - 小写化、去掉多余空白后算 `difflib.SequenceMatcher.ratio()`；
    - 再补两条经验加成：**前缀相同**、**查询串被包含**（这两个场景最常见）；
    - `sim < min_sim` 直接丢掉（宁少不滥，不硬凑）；一条都没有就返回空。
    每条候选多回 `sim`（0~1，排序用，**界面不必显示百分比** —— 那是近似值）。
    """
    import difflib

    q = " ".join(str(query or "").lower().split())
    if not q:
        return []
    out = []
    for w in windows or []:
        t = " ".join(str(w.get("title") or "").lower().split())
        if not t:
            continue
        sim = difflib.SequenceMatcher(None, q, t).ratio()
        if t.startswith(q):
            sim = min(1.0, sim + 0.15)
        if q in t:
            sim = min(1.0, sim + 0.10)
        if sim < float(min_sim):
            continue
        item = dict(w)
        item["sim"] = round(float(sim), 4)
        out.append(item)
    out.sort(key=lambda x: -float(x.get("sim") or 0))
    return out[:max(1, int(limit))]


def window_size(hwnd: int) -> tuple:
    """窗口**真实**尺寸 `(w, h)`（拿不到就 `(0, 0)`）。"""
    i = window_info(hwnd)
    return (int(i.get("width") or 0), int(i.get("height") or 0))


def pick_biggest(windows, *, min_side: int = 200) -> dict | None:
    """在候选窗口里挑**真实尺寸最大、且没最小化**的那个（挑不到返回 `None`，不瞎猜）。

    先按真实尺寸量（`window_size`），量不到才退回列表里可能带的 width/height；
    `min_side` 排掉"影子窗口"（实测同进程里就有一个 93x150 的浮层）；
    最小化的窗口直接排除 —— 它本来也截不到、点不到。
    """
    best, best_area, best_size = None, -1, (0, 0)
    for w in windows or []:
        try:
            hwnd = int(w.get("hwnd") or 0)
        except (TypeError, ValueError):
            continue
        if not hwnd:
            continue
        info = window_info(hwnd)
        if info.get("minimized"):
            continue
        cw, ch = int(info.get("width") or 0), int(info.get("height") or 0)
        if not cw or not ch:
            cw, ch = int(w.get("width") or 0), int(w.get("height") or 0)
        if min_side and (cw < min_side or ch < min_side):
            continue
        area = cw * ch
        if area > best_area:
            best, best_area, best_size = dict(w), area, (cw, ch)
    if best is not None:
        best["width"], best["height"] = best_size
    return best


def options(member, *, session_name: str | None = None, probe: bool = True) -> dict:
    """**只读**：这个成员现在能怎么被"视觉拉起"。给界面按钮的可用性用，**没有任何副作用**。"""
    name = (session_name or session_name_of(member)).strip()
    out = {
        "session_name": name,
        "named": bool(name),
        "methods": [],
        "notes": [],
    }
    if not name:
        # 没命名 ⇒ 连"匹配键"都没有，注入毫无意义。如实说，不做任何尝试。
        out["notes"].append("这个成员**还没有会话名**（步骤 7~9 未完成）——"
                            "先去命名，视觉拉起才知道该找哪个窗口（脚本不写死名字）")
        return out

    if _IS_WINDOWS:
        win = list_windows(name) if probe else {"ok": True, "windows": [], "detail": "未探测"}
        matched = win.get("windows") or []
        out["methods"].append({
            "method": "wmpaste",
            "available": bool(matched),
            "detail": ("匹配到 %d 个窗口（标题含会话名）" % len(matched)) if matched
                      else (win.get("detail") or "没有标题匹配的窗口"),
            "windows": matched[:5],
            # ★2026-10-20：把"这种窗口常见送不进去"提前说清 —— 免得界面报了可用、点下去却失败。
            "note": ("Chrome / Electron 类窗口通常**没有文本框子窗口**，消息式粘贴用不了；"
                     "平台会自动改用「剪贴板 + Ctrl+V」兜底（不需要你手动贴）"),
        })
    else:
        out["methods"].append({
            "method": "wmpaste",
            "available": False,
            "detail": "非 Windows：消息式注入不存在（如实不支持，不假装）",
        })
    out["methods"].append({
        "method": "clipboard",
        "available": _IS_WINDOWS or _IS_POSIX,
        "detail": "半自动兜底：把内容放剪贴板，你点一下会话再 Ctrl+V（V25 §5 档 f，永远可用）",
        "need_human": True,
    })
    return out


def _clipboard(member, text: str) -> dict:
    """兜底档：把载荷写进系统剪贴板（复用 `wakeup_bridge` 的跨平台实现，不另造一份）。"""
    from .wakeup_bridge import wakeup_bridge
    try:
        wakeup_bridge._set_clipboard(text)          # 同步阻塞 ⇒ 调用方负责 to_thread
    except Exception as e:  # noqa: BLE001
        # ★2026-10-23（真机实测抓到）：文案原来是**跨平台一句通用话**（"装一个剪贴板工具：
        #   Linux xclip/xsel；macOS pbcopy"）——在 Windows 上**指错了方向**：
        #   Windows 侧失败几乎都是"剪贴板被别的程序占着"（`Set-Clipboard` 抛
        #   `ExternalException: Requested Clipboard operation did not succeed.`），
        #   而 Windows 从来不需要装剪贴板工具。**失败要说话，还要说对**。
        _msg = str(e)
        if _IS_WINDOWS:
            _hint = ("Windows 上这通常是**剪贴板被别的程序占着**（或当前会话没有可用剪贴板）："
                     "关掉/等那个程序放开后重试；也可以改用邮箱那条路（不需要剪贴板）")
        elif _IS_POSIX:
            _hint = "装一个剪贴板工具（xclip 或 xsel；macOS 自带 pbcopy）后重试，或改用邮箱那条路（不需要剪贴板）"
        else:
            _hint = "这个平台没有可用的剪贴板实现 —— 如实说不支持；请改用邮箱那条路（不需要剪贴板）"
        return {"ok": False, "method": "clipboard", "need_human": True,
                "detail": "剪贴板写入失败：%s" % _msg,
                "clipboard_unavailable": ("Clipboard operation did not succeed" in _msg
                                          or "ExternalException" in _msg
                                          or "剪贴板" in _msg),
                "next_step": _hint}
    name = session_name_of(member)
    return {"ok": True, "method": "clipboard", "need_human": True, "verified": None,
            # 这一档**只把内容放进剪贴板**、一个人都没按过 ⇒ 只能算等人贴，不能算送出去了
            "queued_only": True,
            "detail": "已把 %d 字放进剪贴板（半自动档：**内容到了剪贴板，但要你贴过去**）" % len(text),
            "next_step": ("点一下「%s」那个会话窗口，按 Ctrl+V%s"
                          % (name or "你的 agent", "，再按回车发送"))}


def _focus_flag(out: str):
    """从脚本输出里读**置顶到底成没成**（`[focus] ... is_foreground=True/False`）。

    ★2026-10-20 补：老板报"没调到最上层"时，界面只有一句「未核实」——因为平台**根本没把这件事带回来**。
    现在 `launch()` 的结果里带 `foreground`：`True`=真到前台了 / `False`=没抢到 / `None`=脚本没报。
    """
    for ln in out.splitlines():
        if ln.startswith("[focus]"):
            if "is_foreground=True" in ln:
                return True
            if "is_foreground=False" in ln:
                return False
    return None


def focus(name: str, timeout: float = 45.0) -> dict:
    """**只把窗口拉到最上层，不投递任何输入**（排障 / 界面上的"叫到前面"按钮）。

    为什么单独有它（2026-10-23）：老板真机报"试投没置顶也没输入"，而"能不能置顶"和
    "能不能输入"是**两件事**（前者被前台锁/管理员权限影响，后者还可能因为目标没有文本框）。
    分开验，才说得清是哪一步不行。

    返回 `{ok, foreground, detail, next_step}`；`ok=False` 时 `next_step` 给可执行的动作。
    """
    nm = str(name or "").strip()
    if not nm:
        return {"ok": False, "foreground": None, "detail": "要置顶哪个窗口？标题是空的",
                "next_step": "传入会话名或窗口标题的一部分"}
    rc, out = _run_inject(["-TitleLike", nm, "-FocusOnly"], timeout=timeout)
    if rc == 0 and "[focus-only] ok=True" in out:
        return {"ok": True, "foreground": True,
                "detail": "已把它拉到最上层（未投递任何输入）", "next_step": ""}
    if rc == 7:
        return {"ok": False, "foreground": False,
                "detail": "没能置顶（%s）" % next((l for l in out.splitlines()
                                                  if l.startswith("[focus-only]")), "")[:80],
                "next_step": ("手动点一下窗口标题含「%s」的那个窗口，再用管理员权限重跑一次"
                              "（目标以管理员运行时，普通权限的输入会被 UIPI 挡掉）" % nm)}
    why = next((l for l in out.splitlines() if l.startswith("[FAIL]")), out.strip()[-160:])
    return {"ok": False, "foreground": None, "detail": "置顶没做成：%s" % (why[:160] or "无输出"),
            "next_step": "换标题的一部分再试（-List 能看到现有窗口标题）"}


def surface_send(name: str, text: str, *, anchor: dict | None = None,
                 press_enter: bool = False, timeout: float = 60.0) -> dict:
    """**表层交互**：置顶 → 点到"教过的那个位置" → 逐字键入（真鼠标键盘），可选回车。

    为什么有它（老板 2026-10-23 定）：Electron 类目标没有文本框子窗口，
    系统层的"剪贴板 + Ctrl+V"落不进输入框；**像人一样先点一下、再打字**才可靠。

    `anchor` = `{"rel_x": 0~1, "rel_y": 0~1}`（窗口客户区**相对坐标**，换窗口大小仍对得上）。
    没教过位置 ⇒ 如实说"没教过"，**不乱点**（乱点会点坏人家东西）。

    返回 `{ok, method:"surface", verified, clicked, typed, foreground, detail, next_step}`；
    能读回的目标（原生文本框）会给出 `verified=True/False`，读不回的如实 `None`。
    """
    from . import input_sim

    nm = str(name or "").strip()
    txt = str(text or "")
    if not nm:
        return {"ok": False, "method": "surface", "need_human": False, "verified": None,
                "detail": "没给窗口名，点不到任何一个窗口", "next_step": "先选一个候选窗口"}
    if not txt:
        return {"ok": False, "method": "surface", "need_human": False, "verified": None,
                "detail": "要输入的内容是空的", "next_step": "传入 text"}
    # ★顺序有讲究（2026-10-23 双平台实测抓到）：**先判教过位置没有**，再判平台支持。
    #   否则在非 Windows 上，用户明明还没教位置，却先被告知这个平台不支持——答非所问。
    if not (anchor or {}).get("rel_x") and (anchor or {}).get("rel_x") != 0:
        return {"ok": False, "method": "surface", "need_human": True, "verified": None,
                "detail": "还没教过输入框位置 —— 表层交互不知道该点哪儿（也不会乱点）",
                "next_step": "在目标窗口里对着输入框点一次「教一次位置」，之后就不用再点了"}
    if not input_sim.supported():
        return {"ok": False, "method": "surface", "need_human": False, "verified": None,
                "detail": input_sim.platform_note(), "next_step": "这个平台不支持表层交互（如实说不支持）"}

    # ① 置顶（用注入脚本，它带前台锁绕过；失败就如实停）
    rc, out = _run_inject(["-TitleLike", nm, "-FocusOnly"], timeout=timeout)
    if rc != 0:
        return {"ok": False, "method": "surface", "need_human": True, "verified": None,
                "foreground": _focus_flag(out),
                "detail": "没能把「%s」拉到最上层，所以**没有点击也没有输入**（否则会点到别的窗口）" % nm,
                "next_step": "先点一下那个窗口让它到最前面，再试一次；或确认平台与目标权限一致"}
    hwnd = _hwnd_from_output(out)
    if not hwnd:
        return {"ok": False, "method": "surface", "need_human": False, "verified": None,
                "detail": "置顶成功，但没解析到窗口句柄（脚本输出里没有 [target] 行）",
                "next_step": "把这份输出发我（这是脚本/解析的问题，不是目标的问题）"}

    # ② 点"教过的位置"（相对坐标 ⇒ 窗口大小变了也对）
    # ★2026-10-23 实测教训：按标题匹配到的**不一定**是主窗口 —— 实测 Trae 的两个同名窗口
    #   分别只有 62×100 与 158×26（托盘/小弹层）。往这种窗口里按比例点等于往桌子上点，
    #   字还会打到当前真正有焦点的程序里。所以：窗口太小就**先停下来说清楚**，不乱点。
    wrect = input_sim.window_rect(hwnd) or {}
    if wrect and (wrect.get("width", 0) < 200 or wrect.get("height", 0) < 150):
        return {"ok": False, "method": "surface", "need_human": True, "verified": None,
                "foreground": True,
                "detail": ("按标题「%s」找到的窗口只有 %dx%d —— 像托盘/小弹层，**不是主窗口**；"
                           "没敢往里点（会点到别的地方）"
                           % (nm, wrect.get("width", 0), wrect.get("height", 0))),
                "next_step": "把目标窗口**打开/最大化**（别只留托盘图标），再教一次位置、再试一次"}
    click = input_sim.click_in_window(hwnd, float(anchor["rel_x"]), float(anchor["rel_y"]))
    if not click.get("ok"):
        return {"ok": False, "method": "surface", "need_human": False, "verified": None,
                "detail": "点击没成功：%s" % (click.get("why") or "未知原因"),
                "next_step": "重新教一次位置（窗口可能换了显示器/缩放），或把平台和目标用同样权限跑"}
    time.sleep(0.12)

    # ③ 逐字键入（unicode 事件，中文不需要输入法；不动剪贴板）
    typed = input_sim.send_text(txt)
    if not typed.get("ok"):
        return {"ok": False, "method": "surface", "need_human": False, "verified": None,
                "clicked": True, "foreground": True,
                "detail": "点了输入框，但键入没送完整：%s" % (typed.get("why") or ""),
                "next_step": "重试一次；若反复如此，目标的输入框可能有拦截（先手动点进去看看）"}
    if press_enter:
        input_sim.send_keys("enter")

    # ④ 能读回就判真假（原生文本框）；读不回如实 None
    verified = None
    verify_note = "目标没有可读的文本框（Electron/浏览器类常态）⇒ 只能如实说未核实"
    vrc, vout = _run_inject(["-TitleLike", nm, "-ExpectText", txt], timeout=timeout)
    if vrc == 0 and "contains_expected=True" in vout:
        verified = True
        verify_note = "读回核对通过"
    elif vrc == 4 and "contains_expected=False" in vout:
        verified = False
        verify_note = "读回核对**不一致**（内容没进去或进了别的地方）"

    res = {"ok": verified is not False, "method": "surface", "verified": verified,
           "clicked": True, "typed": True, "foreground": True,
           "detail": ("已点输入框（相对位置 %.2f,%.2f）并逐字键入 %d 字；%s"
                      % (float(anchor["rel_x"]), float(anchor["rel_y"]), len(txt), verify_note)),
           "next_step": ("已送达（读回核对通过）" if verified
                         else ("没送达：目标里读不到这段文字" if verified is False
                               else "已点击并输入（未核实）；若目标里没出现，看它是否把焦点移走了"))}
    if verified is None:
        res["need_human"] = True
    return res


def _hwnd_from_output(out: str) -> int:
    """从注入脚本输出里取 `[target] hwnd=…` 的句柄（取不到返回 0）。"""
    m = re.search(r"\[target\]\s+hwnd=(\d+)", out or "")
    return int(m.group(1)) if m else 0


def launch(member, text: str, *, method: str = "auto", session_name: str | None = None,
           verify: bool = True, anchor: dict | None = None) -> dict:
    """对外入口：结果**一定带 state**（delivered_verified / sent_unverified / queued_for_human / not_sent）。

    2026-10-23：老板真机看到「✗ 没有投递」下面却写着「已改用剪贴板 + Ctrl+V」——
    根因是返回里没有单一字段说清到底到哪一步。以后界面**只看 state**。
    """
    res = _launch_core(member, text, method=method, session_name=session_name,
                       verify=verify, anchor=anchor)
    try:
        from .delivery import state_of, text_control_of

        res["state"] = state_of(res)
        res["text_control"] = text_control_of(res.get("detail") or "")
    except Exception as e:                                    # noqa: BLE001 — 加不上 state 也要如实留话
        print("[delivery] 计算 state 失败（按 not_sent 渲染）：%s: %s" % (type(e).__name__, e), flush=True)
        res["state"] = "not_sent"
        res["text_control"] = {"found": None, "why": ""}
    return res


def _launch_core(member, text: str, *, method: str = "auto", session_name: str | None = None,
           verify: bool = True, anchor: dict | None = None) -> dict:
    """**真拉起**：把 `text` 送进这个成员的会话。

    返回 `{ok, method, detail, need_human, next_step, verified}` —— **失败必须说清为什么、人该做什么**。
    `method="auto"`：Windows 上先试 `wmpaste`（消息式、不抢前台），不行再退 `clipboard`。

    ## ★`verify` 默认 **True**（2026-10-15 实测教训）

    本机真注入一次：脚本打印了 `[inject] WM_PASTE -> class=… (15 chars)`（**看起来成功**），
    但**受控靶子窗口自报文本框 `len=0`** —— **文本根本没落进去**。
    也就是说"注入 API 返回了"**不等于**"内容到了"；脚本自己的收尾话术也写着
    "this only says *we sent input*"。
    ⇒ 所以默认**带着 `-Verify` 去读回核对**，并把结论如实放进 `verified`：

    | `verified` | 含义 |
    |---|---|
    | `True` | 读回目标控件，**文本真的在里面** |
    | `False` | 读回不匹配 ⇒ `ok=False`（**不许拿"我们送了"当"送到了"**）|
    | `None` | **没能自证**：目标没有标准文本框（Electron/Qt 常态），或调用方明确 `verify=False` |

    `verified=None` 时 `detail` 会**明说"这只是我们送出了输入，没有读回核对"**，不让人误以为已验证。
    """
    text = str(text or "")
    if not text:
        return {"ok": False, "method": method, "need_human": False, "verified": None,
                "detail": "要送的内容是空的", "next_step": "传入 text"}
    if method not in METHODS:
        return {"ok": False, "method": method, "need_human": False, "verified": None,
                "detail": "未知的拉起方式 %r（可选：%s）" % (method, " / ".join(METHODS)),
                "next_step": "改用 auto / wmpaste / clipboard"}

    name = (session_name or session_name_of(member)).strip()
    if not name:
        return {"ok": False, "method": method, "need_human": False, "verified": None,
                "detail": "这个成员还没有会话名，视觉拉起不知道该找哪个窗口",
                "next_step": "先走步骤 7~9 命名（平台会把名字写进 session_aliases），再拉起"}

    # ★2026-10-23（老板实测）：Electron/浏览器类窗口里"窗口在前台 ≠ 焦点在输入框"，
    #   光按 Ctrl+V 会落空。若这个员工**教过输入框位置**，就先点一下它再粘贴。
    click_note = ""
    if anchor and method in ("clipboard", "auto", "wmpaste"):
        try:
            from . import input_anchor, input_sim

            # 找窗口只用本模块**已有**的 list_windows（同名取面积最大，已经是它的口径）
            wins = [w for w in (list_windows().get("windows") or [])
                    if name.lower() in (w.get("title") or "").lower()]
            wins.sort(key=lambda w: -int(w.get("area") or 0))
            hwnd = int(wins[0]["hwnd"]) if wins else 0
            if hwnd:
                # 先恢复/抬到最前：最小化的窗口坐标是占位值，直接点会点空（实测）
                try:
                    focus(name)
                except Exception as _fe:                      # noqa: BLE001 — 抬不起来也照常往下走（点击前会自证）
                    print("[visual_launch] 置顶失败（继续，点击前会自证）：%s" % _fe, flush=True)
            if hwnd and (anchor or {}).get("rel_x") is not None:
                ck = input_sim.click_in_window(hwnd, float(anchor["rel_x"]), float(anchor["rel_y"]))
                if ck.get("ok"):
                    click_note = "已先点一下教过的输入框位置(%s,%s)再粘贴" % (
                        round(float(anchor["rel_x"]), 3), round(float(anchor["rel_y"]), 3))
                else:
                    # 点不到目标窗口 ⇒ **不粘贴**（否则等于把话打进别人程序里）
                    return {"ok": False, "method": method, "need_human": False, "verified": False,
                            "detail": "先点输入框失败：%s" % (ck.get("why") or "点不到那个窗口"),
                            "next_step": "把目标窗口切到最前（别被挡住）再试一次；"
                                         "或重新「教一次位置」"}
        except Exception as e:                                # noqa: BLE001 — 点击失败不该炸掉整条路
            print("[visual_launch] 点击教过的位置时出错（按未点击继续）：%s: %s"
                  % (type(e).__name__, e), flush=True)

    if method == "clipboard":
        fb = _clipboard(member, text)
        if click_note:
            fb["detail"] = fb.get("detail", "") + "；" + click_note
        return fb

    # auto / wmpaste：先试消息式注入
    args = ["-TitleLike", name, "-Text", text, "-Method", "wmpaste"]
    if verify:
        # 读回前多等一会儿：消息式粘贴是异步的，读太早会误判成失败
        args += ["-Enter", "-Verify", "-PasteDelayMs", "900"]
    else:
        args.append("-Enter")
    rc, out = _run_inject(args)
    if rc == 4:
        # 注入发出去了，但**读回不匹配** ⇒ 如实报失败（实测真发生过：`[inject] WM_PASTE (15 chars)`
        # 之后靶子自报文本框 `len=0`）
        vline = next((ln for ln in out.splitlines() if ln.startswith("[verify]")), out[-200:])
        return {"ok": False, "method": "wmpaste", "need_human": False, "verified": False,
                "detail": "注入后读回校验失败：%s" % vline[:200],
                "next_step": "那个控件可能不是标准文本框（Electron/Qt 常见），"
                             "或目标没接收这次粘贴；改用 `clipboard` 兜底，"
                             "或让 agent 回执来证明它醒了",
                "warnings": [ln for ln in out.splitlines() if ln.startswith("[WARN]")]}
    if rc == 0 and "[inject]" in out:
        # 证据要取**真正投递那一行**（`[inject] WM_PASTE …`），不是最后一行 ——
        # 脚本在投递之后还会打 `[inject] Enter`，取末行会把"怎么送进去的"丢掉。
        evid = [ln for ln in out.splitlines() if ln.startswith("[inject]")]
        pick = next((ln for ln in evid if ("WM_PASTE" in ln or "clipboard" in ln or "SendKeys" in ln)),
                    evid[-1] if evid else out.splitlines()[-1])
        res = {"ok": True, "method": "wmpaste", "need_human": False, "verified": None,
               "foreground": _focus_flag(out),
               "detail": pick[:200],
               "next_step": "已送达会话（含回车）；若目标没回应，看它的日志/桥状态",
               "warnings": [ln for ln in out.splitlines() if ln.startswith("[WARN]")]}
        vline = next((ln for ln in out.splitlines() if ln.startswith("[verify]")), "")
        if "contains_expected=True" in vline:
            res["verified"] = True
            res["detail"] = "%s；读回校验通过" % res["detail"]
        else:
            # 没读回核对（没要，或目标没有标准控件）—— **如实说"没自证"**，不标成已验证
            why = vline[:120] if vline else "调用方未要求读回核对"
            res["detail"] = "%s；⚠️这只是「我们送出了输入」，**没有读回核对**（%s）" % (res["detail"], why)
        return res
    # 失败：如实回报，并在 auto 模式下退到兜底档
    reason = next((ln for ln in out.splitlines() if ln.startswith("[FAIL]")), None) or out[-200:]

    # ★2026-10-20 修（老板真机报"无法激活/注入失败"）：`rc == 3` 的含义是
    #   **「wmPaste 这种方法对这个窗口不适用」**（目标没有文本框子窗口 —— Chrome / Electron 的常态），
    #   不是"目标坏了"。原来这里直接返回失败（界面上老板只看到"未核实"，还得自己点剪贴板），
    #   于是"送不进去"变成人的活。
    #   现在：**自动改用脚本的全自动 clipboard 档**（置顶 → 放进剪贴板 → 发 Ctrl+V），
    #   并在 detail 里说清"为什么换方法、换了之后发生了什么"（仍不谎报"已验证"）。
    # ★2026-10-20：`rc == 3` 有**两种完全不同的含义**，必须分开处理（否则会造出假成功）：
    #   (a) `[FAIL] -Method wmpaste needs a text-control child` ⇒ **窗口找到了**，只是它没有文本框子窗
    #       （Chrome/Electron 常态）⇒ 换方法（剪贴板 + Ctrl+V）是对的；
    #   (b) `[FAIL] no matching window` ⇒ **根本没找到那个窗口** ⇒ 绝不能兜底成"成功"，
    #       只能如实说"没找到窗口"（这是既有守卫 test_missing_window_never_reports_success 的承诺）。
    if rc == 3 and "text-control" in out:
        args2 = ["-TitleLike", name, "-Text", text, "-Method", "clipboard"]
        args2 += (["-Enter", "-Verify", "-PasteDelayMs", "900"] if verify else ["-Enter"])
        rc2, out2 = _run_inject(args2)
        # ★2026-10-23（老板真机报"点试投只弹黑框、目标没置顶也没输入，界面却说已兜底送到"）：
        #   这里原来只判 `rc2 == 0 and "[inject]" in out2` —— 而 `[inject] clipboard + Ctrl+V`
        #   只代表"我们按了 Ctrl+V"，**不代表内容进去了**：
        #   ① 剪贴板被别的程序占着时 `Set-Clipboard` 失败，贴的是旧内容（现在脚本会 exit 5）；
        #   ② 窗口没置顶时 Ctrl+V 贴到**前台那个别的窗口**（现在脚本会 exit 6，拒绝盲投）。
        #   所以：只认"剪贴板真写进去了"（`[clipboard] wrote=`）+ 有投递证据，才算"送上去了"，
        #   并且**仍然**只在读回核对成功时才敢说"已验证"。
        if rc2 == 5:
            evid5 = next((ln for ln in out2.splitlines() if ln.startswith("[FAIL]")), out2[-160:])
            fb = _clipboard(member, text)
            fb["fallback_from"] = "wmpaste→clipboard"
            fb["detail"] = ("wmpaste 不行（%s）⇒ 自动剪贴板档也没成：别的内容写不进剪贴板（%s）"
                            % (reason, evid5.strip()[:140]))
            if fb.get("ok"):
                fb["detail"] += " ⇒ 已退回半自动：剪贴板里现在有内容，你点一下目标窗口按 Ctrl+V"
                fb["next_step"] = ("点一下「%s」那个窗口，按 Ctrl+V 再回车（这是**手动**那一步；"
                                   "不是平台帮你按的）" % (name or "目标"))
            return fb
        if rc2 == 6:
            evid6 = next((ln for ln in out2.splitlines() if ln.startswith("[FAIL]")), out2[-160:])
            return {"ok": False, "method": "clipboard", "fallback_from": "wmpaste",
                    "need_human": True, "verified": None, "foreground": _focus_flag(out2),
                    "detail": ("窗口没能置顶 ⇒ **没有投递**（%s）—— 键鼠注入会把内容贴到"
                               "当前前台那个窗口里去，所以宁可不动。" % evid6.strip()[:140]),
                    "next_step": ("先点一下「%s」那个窗口让它到最上层，再点一次「试一次」；"
                                  "若目标是以管理员身份运行的，平台需要同样权限才能把输入送进去"
                                  % (name or "目标")),
                    "warnings": [ln for ln in out2.splitlines() if ln.startswith("[WARN]")]}
        reached = (rc2 == 0 and "[inject]" in out2
                   and ("[clipboard] wrote=" in out2 or "[verify] contains_expected=True" in out2))
        if not reached:
            # **第三层兜底**：全自动剪贴板也没送进去 ⇒ 至少把内容放进剪贴板，让人一键贴
            # （"失败要说话"不是"失败就交给人"，而是"能自动的自动，剩下的说清怎么手动"）。
            fb = _clipboard(member, text)
            fb["fallback_from"] = "wmpaste→clipboard"
            fb["detail"] = ("wmpaste 不行（%s）⇒ 自动剪贴板档也没送进去（%s）⇒ 已退回半自动：%s"
                            % (reason, (out2[-140:].strip() or "无输出"), fb.get("detail")))
            return fb
        evid2 = [ln for ln in out2.splitlines() if ln.startswith("[inject]")]
        # 证据要取**真正投递那一行**（`[inject] clipboard + Ctrl+V`），不是最后一行 ——
        # 脚本投递完还会打 `[inject] Enter`，取末行就把"怎么送进去的"丢掉了（实测踩到）。
        pick2 = next((ln for ln in evid2 if ("clipboard" in ln or "WM_PASTE" in ln or "SendKeys" in ln)),
                     evid2[-1] if evid2 else out2[-160:])
        vline2 = next((ln for ln in out2.splitlines() if ln.startswith("[verify]")), "")
        verified2 = True if (verify and "contains_expected=True" in vline2) else None
        res2 = {
            "ok": True, "method": "clipboard", "fallback_from": "wmpaste",
            "need_human": False, "verified": verified2,
            "foreground": _focus_flag(out2),
            "detail": ("wmpaste 不行（%s）⇒ 已自动改用「剪贴板 + Ctrl+V」：%s%s"
                       % (reason, pick2.strip() or "（无输出）",
                          ("；" + click_note) if click_note else "")),
            "warnings": [ln for ln in out2.splitlines() if ln.startswith("[WARN]")],
        }
        if verified2 is None:
            # **不许说"送到了"**：我们只知道"内容进了剪贴板、按了一次 Ctrl+V"。
            # ★2026-10-23 老板真机实测（Trae/Electron）：剪贴板内容确实写进去了、窗口确实置顶了、
            #   Ctrl+V 也确实按了，但**输入框里什么都没出现** —— 因为 Electron 应用的**内部焦点**
            #   不在输入框（我们的光标停在对话区），而平台**拿不到**它内部的输入元素，无法把光标放进去。
            #   ⇒ 定成 `need_human=True` 并给出**唯一有效的动作**（点一下输入框再按 Ctrl+V）。
            #   这不是"失败就交给人"，而是把"我们只能做到哪一步"如实标出来：内容已经在剪贴板里了。
            res2["need_human"] = True
            res2["detail"] += ("；⚠️这只是「送出了输入」，**没有读回核对**（目标没有可读的文本框）；"
                               "内容**已在剪贴板里**")
            res2["next_step"] = ("内容已经在剪贴板里。如果目标输入框里没出现：**先点一下它的输入框**"
                                 "（浏览器/Electron 类窗口内部焦点我们放不进去），再按 Ctrl+V；"
                                 "若它已经收到，就不用管")
        else:
            res2["next_step"] = "已送达会话（含回车），且读回核对通过"
        return res2

    if method == "wmpaste":
        return {"ok": False, "method": "wmpaste", "need_human": False,
                "detail": "注入失败（exit=%s）：%s" % (rc, reason),
                "next_step": ("窗口标题里带上会话名「%s」，或改用 clipboard 兜底"
                              "（-List 可以看到现有窗口标题）" % name)}
    fb = _clipboard(member, text)
    fb["detail"] = "wmpaste 不行（%s）⇒ 已退到兜底：%s" % (reason, fb.get("detail"))
    return fb
