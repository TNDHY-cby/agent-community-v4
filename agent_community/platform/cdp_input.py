# -*- coding: utf-8 -*-
"""**浏览器级投递**（CDP）：把文字送进 Electron / Chromium 应用的输入框 —— **不用真鼠标**。

老板 2026-10-23 定的方向：「不能是真鼠标」，改成往 contenteditable 聊天框里塞文本。

## 为什么这条路对（对比"模拟鼠标键盘"）

| | 模拟鼠标键盘（SendInput） | **CDP 浏览器级** |
|---|---|---|
| 要占用你的鼠标/键盘 | **要**（会抢焦点、会点到别处） | **不要**（进程内派发事件） |
| 窗口没在前台 | 落空或打到别的程序 | **照样能投** |
| 能否核实 | 只有原生控件能读回 | **能**：`innerText`/`value` 直接读回 |
| 多行/长文本 | Enter 会触发发送 | 一次性 insertText / paste 事件，**不触发发送** |

## 三档（由稳到"最像人"，自动降级）

1. `insert`：JS `el.focus()` + `Input.insertText`（分块）——不碰剪贴板；
2. `paste-event`：JS 构造 `ClipboardEvent('paste')` + `DataTransfer`——**保留换行**，仍不碰剪贴板；
   （对 ProseMirror / CodeMirror / Slate 这类监听 `paste` 的编辑器有效；`isTrusted=false` 是唯一风险）
3. `clipboard`：写系统剪贴板（复用 `wakeup_bridge`，写完**还原**）+ `Input.dispatchKeyEvent` Ctrl+V
   ——最像真人，会短暂占用剪贴板（约 0.5 秒，用完还原）。

**每档之后都读回核对**（`innerText || value`）——不核对就等于"我以为送到了"。

## 依赖与可移植性

- 只用 `websockets`（已在 `requirements.txt`）＋标准库；**不引 Playwright**；
- 调试端口从环境变量 `AC_CDP_PORT` 读（默认 9222），**不写死**；
- 目标应用必须**自己**开了调试端口（Electron 参数 `--remote-debugging-port`）——这是用户侧的一次性设置，
  没开就**如实说"没开调试端口"**，不假装能投。
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request

DEFAULT_PORTS = (9222, 9223, 9224, 9225)

#: 找"能输入的地方"的 JS：可见的 textarea / contenteditable / 文本 input
SCAN_JS = r"""
(() => {
  const sel = 'textarea,[contenteditable="true"],[contenteditable=""],input[type="text"],input:not([type])';
  const list = [...document.querySelectorAll(sel)].filter(e => {
    const r = e.getBoundingClientRect();
    const st = window.getComputedStyle(e);
    return (r.width > 40 && r.height > 12) && st.visibility !== 'hidden' && st.display !== 'none';
  });
  return JSON.stringify(list.map((e, i) => {
    const r = e.getBoundingClientRect();
    return {
      idx: i, tag: e.tagName, editable: !!e.isContentEditable,
      placeholder: e.getAttribute('placeholder') || e.getAttribute('data-placeholder')
                   || e.getAttribute('aria-label') || e.getAttribute('aria-valuetext') || '',
      maxlength: e.getAttribute('maxlength') || null,
      cls: String(e.className || '').slice(0, 80),
      w: Math.round(r.width), h: Math.round(r.height)
    };
  }), null, 1);
})()
"""

READBACK_JS = r"""
(() => {
  const sel = 'textarea,[contenteditable="true"],[contenteditable=""],input[type="text"],input:not([type])';
  const list = [...document.querySelectorAll(sel)].filter(e => {
    const r = e.getBoundingClientRect();
    const st = window.getComputedStyle(e);
    return (r.width > 40 && r.height > 12) && st.visibility !== 'hidden' && st.display !== 'none';
  });
  const el = list[IDX] || document.activeElement;
  if (!el) return JSON.stringify({ok: false, text: '', why: 'no-editor'});
  const text = (el.isContentEditable ? el.innerText : el.value) || '';
  return JSON.stringify({ok: true, text: text, len: text.length,
                         tag: el.tagName, editable: !!el.isContentEditable});
})()
"""

FOCUS_JS = r"""
(() => {
  const sel = 'textarea,[contenteditable="true"],[contenteditable=""],input[type="text"],input:not([type])';
  const list = [...document.querySelectorAll(sel)].filter(e => {
    const r = e.getBoundingClientRect();
    const st = window.getComputedStyle(e);
    return (r.width > 40 && r.height > 12) && st.visibility !== 'hidden' && st.display !== 'none';
  });
  const el = list[IDX];
  if (!el) return 'no-editor';
  el.focus();
  if (el.isContentEditable) {
    const rng = document.createRange(); rng.selectNodeContents(el); rng.collapse(false);
    const s = window.getSelection(); s.removeAllRanges(); s.addRange(rng);
  }
  return 'ok';
})()
"""

PASTE_EVENT_JS = r"""
(() => {
  const sel = 'textarea,[contenteditable="true"],[contenteditable=""],input[type="text"],input:not([type])';
  const list = [...document.querySelectorAll(sel)].filter(e => {
    const r = e.getBoundingClientRect();
    const st = window.getComputedStyle(e);
    return (r.width > 40 && r.height > 12) && st.visibility !== 'hidden' && st.display !== 'none';
  });
  const el = list[IDX] || document.activeElement;
  if (!el) return JSON.stringify({ok: false, why: 'no-editor'});
  el.focus();
  const dt = new DataTransfer();
  dt.setData('text/plain', TEXT);
  const ev = new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true, composed: true});
  const notCancelled = el.dispatchEvent(ev);
  return JSON.stringify({ok: true, dispatched: true, notCancelled: notCancelled});
})()
"""


def debug_ports() -> tuple:
    """要探测的调试端口：`AC_CDP_PORT`（可逗号分隔）优先，否则 9222~9225。"""
    raw = (os.environ.get("AC_CDP_PORT") or "").strip()
    if not raw:
        return DEFAULT_PORTS
    out = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return tuple(out) or DEFAULT_PORTS


def _http_targets(port: int, timeout: float = 1.2) -> list:
    """`GET http://127.0.0.1:<port>/json/list`（拿不到就返回空表，不抛）。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % int(port), timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        return data if isinstance(data, list) else []
    except Exception:                                        # noqa: BLE001 — 没开端口是常态，调用方会如实说
        return []


def probe(ports=None) -> dict:
    """探测本机有哪些开了调试端口的 Chromium/Electron，以及它们的页面 target。

    返回 `{ok, ports:[{port, targets:[…]}], why}`；一个都没有时 `why` **说清怎么开**。
    """
    ports = tuple(ports or debug_ports())
    found = []
    for p in ports:
        ts = _http_targets(p)
        if not ts:
            continue
        pages = [{"id": t.get("id"), "title": (t.get("title") or "")[:120],
                  "url": (t.get("url") or "")[:300], "type": t.get("type"),
                  "ws": t.get("webSocketDebuggerUrl") or ""}
                 for t in ts if (t.get("type") or "") == "page" and t.get("webSocketDebuggerUrl")]
        if pages:
            found.append({"port": p, "targets": pages})
    if not found:
        return {"ok": False, "ports": [], "why": (
            "没发现开了调试端口的 Chromium/Electron（试过 %s）。目标应用需要自己加参数启动："
            "`--remote-debugging-port=9222`；平台这边可以设 AC_CDP_PORT 指定端口。" % (list(ports),))}
    return {"ok": True, "ports": found, "why": ""}


class CDP:
    """极简 CDP 连接（只有本项目要用的三件事：evaluate / insertText / dispatchKeyEvent）。"""

    def __init__(self, ws_url: str, timeout: float = 8.0):
        self.ws_url = ws_url
        self.timeout = timeout
        self._ws = None

    async def __aenter__(self):
        import websockets

        self._ws = await websockets.connect(self.ws_url, open_timeout=self.timeout,
                                            close_timeout=2, max_size=16 * 1024 * 1024)
        return self

    async def __aexit__(self, *exc):                          # noqa: BLE001
        try:
            await self._ws.close()
        except Exception:                                     # noqa: BLE001 — 关不掉不算错
            pass
        return False

    async def call(self, method: str, params: dict | None = None) -> dict:
        """发一条 CDP 命令并等它的应答（忽略事件消息）。"""
        import itertools

        if not hasattr(self, "_ids"):
            self._ids = itertools.count(1)
        mid = next(self._ids)
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)
            try:
                msg = json.loads(raw)
            except Exception:                                 # noqa: BLE001 — 非 JSON 事件，跳过
                continue
            if msg.get("id") != mid:
                continue
            if msg.get("error"):
                return {"ok": False, "error": msg["error"], "raw": msg}
            return {"ok": True, "result": msg.get("result") or {}}

    async def evaluate(self, expr: str) -> dict:
        """跑一段 JS，返回 `{ok, value, error}`（`returnByValue`）。"""
        r = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                 "awaitPromise": True})
        if not r.get("ok"):
            return {"ok": False, "value": None, "error": r.get("error")}
        res = r["result"].get("result") or {}
        if res.get("subtype") == "error" or r["result"].get("exceptionDetails"):
            return {"ok": False, "value": None, "error": r["result"].get("exceptionDetails")}
        return {"ok": True, "value": res.get("value"), "error": None}

    async def insert_text(self, text: str) -> dict:
        """`Input.insertText`：不碰剪贴板，等同"输入法上屏一次"。"""
        return await self.call("Input.insertText", {"text": text})

    async def press_ctrl_v(self) -> dict:
        """真按一次 Ctrl+V（`Input.dispatchKeyEvent`，带 keyCode/scanCode）。"""
        base = {"modifiers": 2, "windowsVirtualKeyCode": 86, "nativeVirtualKeyCode": 86,
                "code": "KeyV", "key": "v"}
        r1 = await self.call("Input.dispatchKeyEvent", {**base, "type": "keyDown"})
        r2 = await self.call("Input.dispatchKeyEvent", {**base, "type": "keyUp"})
        return {"ok": r1.get("ok") and r2.get("ok"), "down": r1.get("ok"), "up": r2.get("ok")}

    async def press_enter(self) -> dict:
        base = {"windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "code": "Enter",
                "key": "Enter", "text": "\r"}
        r1 = await self.call("Input.dispatchKeyEvent", {**base, "type": "keyDown"})
        r2 = await self.call("Input.dispatchKeyEvent", {**base, "type": "keyUp"})
        return {"ok": r1.get("ok") and r2.get("ok")}


def _js_str(s: str) -> str:
    """把 Python 字符串安全地嵌进 JS（JSON 转义，避免引号/换行把脚本拼坏）。"""
    return json.dumps(s, ensure_ascii=False)


async def scan_editors(cdp: CDP) -> dict:
    """扫页面里"能输入的地方"（可见的 textarea / contenteditable / 文本 input）。"""
    r = await cdp.evaluate(SCAN_JS)
    if not r.get("ok"):
        return {"ok": False, "editors": [], "why": "页面里跑 JS 失败：%s" % (r.get("error"),)}
    try:
        editors = json.loads(r.get("value") or "[]")
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "editors": [], "why": "扫描结果解析失败：%s" % e}
    return {"ok": True, "editors": editors, "why": "" if editors else "页面里没找到可见的输入框"}


async def read_back(cdp: CDP, idx: int) -> dict:
    r = await cdp.evaluate(READBACK_JS.replace("IDX", str(int(idx))))
    if not r.get("ok"):
        return {"ok": False, "text": "", "why": "读回失败：%s" % (r.get("error"),)}
    try:
        d = json.loads(r.get("value") or "{}")
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "text": "", "why": "读回结果解析失败：%s" % e}
    return d


async def _tier_insert(cdp: CDP, idx: int, text: str, chunk: int = 2000) -> dict:
    f = await cdp.evaluate(FOCUS_JS.replace("IDX", str(int(idx))))
    if (f.get("value") or "") != "ok":
        return {"ok": False, "why": "聚焦不到第 %s 个输入框（%s）" % (idx, f.get("value"))}
    sent = 0
    for i in range(0, len(text), max(1, chunk)):
        part = text[i:i + chunk]
        r = await cdp.insert_text(part)
        if not r.get("ok"):
            return {"ok": False, "why": "Input.insertText 失败：%s" % (r.get("error"),), "sent": sent}
        sent += len(part)
        await asyncio.sleep(0.04)
    return {"ok": True, "sent": sent, "why": ""}


async def _tier_paste_event(cdp: CDP, idx: int, text: str) -> dict:
    expr = PASTE_EVENT_JS.replace("IDX", str(int(idx))).replace("TEXT", _js_str(text))
    r = await cdp.evaluate(expr)
    if not r.get("ok"):
        return {"ok": False, "why": "派发 paste 事件失败：%s" % (r.get("error"),)}
    try:
        d = json.loads(r.get("value") or "{}")
    except Exception:                                         # noqa: BLE001
        d = {}
    if not d.get("ok"):
        return {"ok": False, "why": "页面上没有可粘贴的输入框"}
    return {"ok": True, "why": "", "notCancelled": d.get("notCancelled")}


async def _tier_clipboard(cdp: CDP, idx: int, text: str) -> dict:
    """写系统剪贴板（写完还原）+ CDP 派发 Ctrl+V。"""
    from .wakeup_bridge import wakeup_bridge

    old = ""
    try:
        old = _read_clipboard()
    except Exception:                                         # noqa: BLE001 — 读不回旧剪贴板不影响投递
        old = ""
    try:
        wakeup_bridge._set_clipboard(text)
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "why": "写剪贴板失败：%s" % e}
    f = await cdp.evaluate(FOCUS_JS.replace("IDX", str(int(idx))))
    if (f.get("value") or "") != "ok":
        return {"ok": False, "why": "聚焦不到输入框，没敢按 Ctrl+V"}
    await asyncio.sleep(0.08)
    r = await cdp.press_ctrl_v()
    await asyncio.sleep(0.35)
    if old:
        try:
            wakeup_bridge._set_clipboard(old)                 # 还原用户的剪贴板
        except Exception:                                     # noqa: BLE001 — 还原失败只影响体验
            pass
    return {"ok": bool(r.get("ok")), "why": "" if r.get("ok") else "Ctrl+V 没派发成功"}


def _read_clipboard() -> str:
    """读当前剪贴板（Windows 走 PowerShell `Get-Clipboard -Raw`；其它平台返回空）。"""
    if os.name != "nt":
        return ""
    import subprocess

    from .spawn import spawn_kwargs

    r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=20, **spawn_kwargs(detach=False, no_window=True))
    return (r.stdout or "").rstrip("\r\n")


#: 三档顺序（由稳到"最像人"）
TIERS = ("insert", "paste-event", "clipboard")


async def deliver(ws_url: str, text: str, *, idx: int = 0, mode: str = "auto",
                  expect_at_least: int | None = None) -> dict:
    """往页面的第 `idx` 个输入框投递文本，**每档后都读回核对**，不通就换下一档。

    `mode="auto"` 走三档；也可以指定某一档（`insert` / `paste-event` / `clipboard`）。
    返回 `{ok, mode, verified, tiers:[…], readback, detail, next_step}`。
    """
    text = str(text or "")
    if not text:
        return {"ok": False, "mode": mode, "verified": None, "tiers": [],
                "detail": "要投的文本是空的", "next_step": "传入 text"}
    want = expect_at_least if expect_at_least is not None else max(1, len(text) // 2)
    plan = TIERS if mode in ("auto", "", None) else (mode,)
    tiers = []
    async with CDP(ws_url) as cdp:
        # 先确认这个 idx 真的存在：越界时**不许**让 read_back 退到 activeElement 去冒充（会谎报）
        sc = await scan_editors(cdp)
        n_ed = len(sc.get("editors") or [])
        if not sc.get("ok") or n_ed == 0:
            return {"ok": False, "mode": mode, "verified": False, "tiers": [],
                    "detail": "页面里没找到可投递的输入框（%s）" % (sc.get("why") or ""),
                    "next_step": "先扫一遍页面（/web-probe），确认哪个才是聊天框"}
        if not (0 <= int(idx) < n_ed):
            return {"ok": False, "mode": mode, "verified": False, "tiers": [],
                    "detail": "第 %s 个输入框不存在（页面里只有 %d 个）" % (idx, n_ed),
                    "next_step": "按扫描结果换一个 idx（0 到 %d）" % (n_ed - 1)}
        for tier in plan:
            if tier == "insert":
                res = await _tier_insert(cdp, idx, text)
            elif tier == "paste-event":
                res = await _tier_paste_event(cdp, idx, text)
            elif tier == "clipboard":
                res = await _tier_clipboard(cdp, idx, text)
            else:
                return {"ok": False, "mode": mode, "verified": None, "tiers": tiers,
                        "detail": "不认识的档位 %r（可选：%s）" % (tier, " / ".join(TIERS)),
                        "next_step": "改用 auto / insert / paste-event / clipboard"}
            await asyncio.sleep(0.12)
            back = await read_back(cdp, idx)
            got = back.get("text") or ""
            tiers.append({"tier": tier, "sent_ok": bool(res.get("ok")), "why": res.get("why", ""),
                          "read_len": len(got), "contains": text.strip()[:20] in got if got else False})
            if res.get("ok") and len(got) >= want:
                return {"ok": True, "mode": tier, "verified": True, "tiers": tiers,
                        "readback": got[:300],
                        "detail": ("用「%s」投进去了；读回 %d 字（核对通过）" % (tier, len(got))),
                        "next_step": ""}
        last = await read_back(cdp, idx)
        return {"ok": False, "mode": plan[-1], "verified": False, "tiers": tiers,
                "readback": (last.get("text") or "")[:300],
                "detail": "三档都试过了，输入框里读回只有 %d 字（不够 %d）"
                          % (len(last.get("text") or ""), want),
                "next_step": ("确认那个编辑器是聊天框（不是搜索框）；"
                              "必要时在探测结果里换一个 idx 再试")}


def run(coro):
    """同步调用入口（工具/测试里用；服务里用 `asyncio.to_thread` 或直接 await）。"""
    return asyncio.run(coro)

#: 取第 idx 个输入框在**视口里的相对位置**（0~1）—— 用来「自动校准」教过的位置。
#: 为什么需要（2026-10-23 老板问「UI 变了脚本还能找到输入框吗」）：
#:   人教的相对位置能扛住窗口缩放/移动，但**扛不住界面改版**（侧栏变宽、聊天面板挪位）。
#:   只要 CDP 可用，就可以直接问页面「那个输入框现在在哪」，把位置更新掉 —— 这才是自愈。
BOX_JS = r"""
(() => {
  const sel = 'textarea,[contenteditable="true"],[contenteditable=""],input[type="text"],input:not([type])';
  const list = [...document.querySelectorAll(sel)].filter(e => {
    const r = e.getBoundingClientRect();
    const st = window.getComputedStyle(e);
    return (r.width > 40 && r.height > 12) && st.visibility !== 'hidden' && st.display !== 'none';
  });
  const e = list[IDX];
  if (!e) { return JSON.stringify({ok: false, why: 'no such editor'}); }
  const r = e.getBoundingClientRect();
  return JSON.stringify({ok: true,
    x: (r.x + r.width / 2) / innerWidth,
    y: (r.y + r.height / 2) / innerHeight,
    tag: e.tagName, w: Math.round(r.width), h: Math.round(r.height)});
})()
"""


async def element_box(cdp: CDP, idx: int = 0) -> dict:
    """第 `idx` 个输入框在视口里的相对中心（0~1）；拿不到就如实说（不猜）。"""
    r = await cdp.evaluate(BOX_JS.replace("IDX", str(int(idx))))
    if not r.get("ok"):
        return {"ok": False, "why": "取位置失败：%s" % (r.get("error"),)}
    try:
        d = json.loads(r.get("value") or "{}")
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "why": "位置结果解析失败：%s" % e}
    return d
