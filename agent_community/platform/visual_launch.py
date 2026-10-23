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
from pathlib import Path

#: 与 `tools/watchdog.py` 同一套"按路径定位项目根"的写法（本模块总在包内，路径稳定）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INJECT_PS1 = PROJECT_ROOT / "agent_community" / "tools" / "inject_window.ps1"

_IS_WINDOWS = os.name == "nt"
_IS_POSIX = os.name == "posix"

#: 允许的档位（`auto` 由本模块挑）
METHODS = ("auto", "wmpaste", "clipboard")


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
    """
    if not _IS_WINDOWS:
        return 127, "非 Windows：消息式界面注入（WM_PASTE）只在 Windows 上存在"
    if not INJECT_PS1.exists():
        return 127, "找不到注入脚本 %s（它随仓库发布）" % INJECT_PS1.name
    argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INJECT_PS1)] + args
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
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


def list_windows(title_like: str = "", timeout: float = 30.0) -> dict:
    """列出候选窗口（只读，不改任何东西）。`-List` 的输出见 `_LIST_LINE_RE` 上方说明。"""
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


def launch(member, text: str, *, method: str = "auto", session_name: str | None = None,
           verify: bool = True) -> dict:
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

    if method == "clipboard":
        return _clipboard(member, text)

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
        reached = rc2 == 0 and "[inject]" in out2
        if not reached:
            # **第三层兜底**：全自动剪贴板也没送进去 ⇒ 至少把内容放进剪贴板，让人一键贴
            # （"失败要说话"不是"失败就交给人"，而是"能自动的自动，剩下的说清怎么手动"）。
            fb = _clipboard(member, text)
            fb["fallback_from"] = "wmpaste→clipboard"
            fb["detail"] = ("wmpaste 不行（%s）⇒ 自动剪贴板档也没送进去（%s）⇒ 已退回半自动：%s"
                            % (reason, (out2[-140:].strip() or "无输出"), fb.get("detail")))
            return fb
        evid2 = [ln for ln in out2.splitlines() if ln.startswith("[inject]")]
        vline2 = next((ln for ln in out2.splitlines() if ln.startswith("[verify]")), "")
        verified2 = True if (verify and "contains_expected=True" in vline2) else None
        return {
            "ok": True, "method": "clipboard", "fallback_from": "wmpaste",
            "need_human": False, "verified": verified2,
            "foreground": _focus_flag(out2),
            "detail": ("wmpaste 不行（%s）⇒ 已自动改用「剪贴板 + Ctrl+V」：%s"
                       % (reason, (evid2[-1] if evid2 else out2[-160:]).strip() or "（无输出）")),
            "next_step": "已自动兜底送到会话（含回车）；若目标没回应，看它的日志/桥状态",
            "warnings": [ln for ln in out2.splitlines() if ln.startswith("[WARN]")],
        }

    if method == "wmpaste":
        return {"ok": False, "method": "wmpaste", "need_human": False,
                "detail": "注入失败（exit=%s）：%s" % (rc, reason),
                "next_step": ("窗口标题里带上会话名「%s」，或改用 clipboard 兜底"
                              "（-List 可以看到现有窗口标题）" % name)}
    fb = _clipboard(member, text)
    fb["detail"] = "wmpaste 不行（%s）⇒ 已退到兜底：%s" % (reason, fb.get("detail"))
    return fb
