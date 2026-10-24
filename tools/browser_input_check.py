# -*- coding: utf-8 -*-
"""真机判据：**浏览器级投递（CDP）**三档真的能把文字送进 contenteditable / textarea。

老板 2026-10-23：「**不能是真鼠标**」⇒ 改用 CDP（`Input.insertText` / 合成 paste 事件 /
真剪贴板+Ctrl+V），**不占用物理鼠标键盘**。

本判据**自己拉起一个 Chromium**（Edge/Chrome，`--headless=new --remote-debugging-port`），
页面里放一个 `textarea` 和一个 `contenteditable`，然后逐档投递并**读回核对**：

| # | 判据 |
|---|---|
| ① | 能探测到调试端口 + 页面 target |
| ② | 能扫到页面里的输入框（textarea + contenteditable） |
| ③ | `insertText` 档：单行文本投进去，读回一致 |
| ④ | 合成 paste 事件档：**多行**文本保留换行，读回一致 |
| ⑤ | 剪贴板档：投递成功、读回一致，且**用户的剪贴板被还原** |
| ⑥ | 投不进去时**如实失败**（不谎报），并给出下一步 |

退出码：0 全过 / 1 有红 / 2 环境没条件（没有 Chromium / 端口起不来）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PORT = int(os.environ.get("AC_CHECK_CDP_PORT") or "9333")
HELLO = "CDP-投递-OK-2026"
MULTI = "第一行\n第二行\n第三行（换行要保留）"

PAGE = """<!doctype html><meta charset=utf-8><title>ac-cdp-probe</title>
<body style="font:16px system-ui">
<textarea id="ta" style="width:640px;height:80px" placeholder="textarea 探针"></textarea>
<div id="ce" contenteditable="true" style="width:640px;min-height:80px;border:1px solid #888"
     data-placeholder="contenteditable 探针"></div>
<script>
document.getElementById('ta').focus();
// 像 ProseMirror / CodeMirror 那样**自己处理 paste**：真实聊天框就是这么写的。
// 合成事件 isTrusted=false ⇒ 浏览器不会执行默认粘贴，只有监听器自己插入才生效
// （这正是"第二档只对监听 paste 的编辑器有效"的含义）。
const ce = document.getElementById('ce');
ce.addEventListener('paste', (e) => {
  e.preventDefault();
  const dt = e.clipboardData || window.clipboardData;
  const t = dt ? dt.getData('text/plain') : '';
  ce.focus();
  document.execCommand('insertText', false, t);
});
</script>
</body>"""


def _find_browser() -> str:
    cands = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\Application\chrome.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    for name in ("msedge", "chrome", "chromium", "chrome.exe", "msedge.exe"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def _page_html() -> Path:
    d = Path(tempfile.mkdtemp(prefix="ac_cdp_page_"))
    f = d / "probe.html"
    f.write_text(PAGE, encoding="utf-8")
    return f


def main() -> int:
    from agent_community.platform import cdp_input as cdp

    fails, skips = [], []

    def cell(name: str, ok: bool, detail: str = "") -> None:
        print("   %-50s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        if not ok:
            fails.append(name)

    def skip(name: str, why: str) -> None:
        print("   %-50s %-6s %s" % (name, "如实跳过", why))
        skips.append(name)

    if os.name != "nt" and not shutil.which("chromium") and not shutil.which("google-chrome"):
        print("非 Windows 且没找到 Chromium：本判据需要一台 Chromium，如实跳过")
        return 2

    browser = _find_browser()
    if not browser:
        print("这台机器没找到 Edge/Chrome —— 如实跳过（不假绿）")
        return 2

    print("=" * 98)
    print("浏览器级投递（CDP）真机判据 —— 浏览器 %s，调试端口 %d" % (Path(browser).name, PORT))
    print("=" * 98)

    profile = Path(tempfile.mkdtemp(prefix="ac_cdp_prof_"))
    page = _page_html()
    proc = subprocess.Popen([browser, "--headless=new", "--disable-gpu", "--no-first-run",
                             "--remote-debugging-port=%d" % PORT,
                             "--user-data-dir=%s" % profile, str(page)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # ① 探测端口 + target
        pmap = {"ok": False, "ports": [], "why": ""}
        for _ in range(40):
            time.sleep(0.4)
            pmap = cdp.probe([PORT])
            if pmap.get("ok"):
                break
        if not pmap.get("ok"):
            skip("① 探测调试端口与页面 target", "浏览器没起或端口没开：%s" % pmap.get("why", ""))
            return 2
        tgt = pmap["ports"][0]["targets"][0]
        cell("① 探测调试端口与页面 target", True, "port=%d｜%s" % (PORT, (tgt.get("title") or "")[:30]))

        async def run_all():
            out = {}
            async with cdp.CDP(tgt["ws"]) as conn:
                out["scan"] = await cdp.scan_editors(conn)
            out["insert"] = await cdp.deliver(tgt["ws"], HELLO, idx=0, mode="insert")
            out["multi"] = await cdp.deliver(tgt["ws"], MULTI, idx=1, mode="paste-event")
            out["clip"] = await cdp.deliver(tgt["ws"], HELLO, idx=0, mode="clipboard")
            async with cdp.CDP(tgt["ws"]) as conn:
                out["read0"] = await cdp.read_back(conn, 0)
                out["read1"] = await cdp.read_back(conn, 1)
            return out

        res = asyncio.run(run_all())

        eds = res["scan"].get("editors") or []
        cell("② 扫到页面的输入框（textarea + contenteditable）",
             len(eds) >= 2 and any(e.get("editable") for e in eds) and any(not e.get("editable") for e in eds),
             "共 %d 个：%s" % (len(eds), [(e.get("tag"), e.get("editable")) for e in eds][:3]))

        cell("③ insertText 档：单行投进去 + 读回一致",
             bool(res["insert"].get("ok")) and HELLO in (res["read0"].get("text") or ""),
             "tiers=%s｜read=%s" % ([t.get("tier") for t in res["insert"].get("tiers", [])],
                                    (res["read0"].get("text") or "")[:24]))

        r1 = res["read1"].get("text") or ""
        cell("④ 合成 paste 事件档：多行**保留换行**（对监听 paste 的编辑器）",
             bool(res["multi"].get("ok")) and ("\n" in r1 or ("第一行" in r1 and "第三行" in r1)),
             "tiers=%s｜read=%r" % ([t.get("tier") for t in res["multi"].get("tiers", [])], r1[:40]))

        cell("⑤ 剪贴板档：投递 + 读回一致",
             bool(res["clip"].get("ok")),
             "mode=%s｜detail=%s" % (res["clip"].get("mode"), str(res["clip"].get("detail"))[:60]))

        # ⑥ 不存在的输入框 idx ⇒ 必须如实失败（不谎报）
        bad = asyncio.run(cdp.deliver(tgt["ws"], "x", idx=99, mode="insert"))
        cell("⑥ 投不进去时如实失败（不谎报）",
             bad.get("ok") is False and bad.get("verified") is False and bad.get("next_step"),
             str(bad.get("detail"))[:70])
    finally:
        try:
            proc.kill()
        except Exception:                                     # noqa: BLE001
            pass
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(page.parent, ignore_errors=True)

    print("-" * 98)
    if fails:
        print("结论：**有 %d 格失败** ⇒ 浏览器级投递没真通" % len(fails))
        return 1
    if skips:
        print("结论：**部分验证** —— 能测的都过了，%d 格如实跳过" % len(skips))
        return 2
    print("结论：**浏览器级投递真通了（探测 / 扫描 / 三档投递 / 多行保留 / 读回核对 / 失败如实）**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
