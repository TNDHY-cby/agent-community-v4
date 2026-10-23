# -*- coding: utf-8 -*-
"""判据：**视觉拉起端到端**（V51/FE-26）—— 窗口列表 / 只读选项 / 置顶 / 方法兜底 / 不许假成功。

```bash
python tools/visual_launch_check.py     # 约 12 秒（Windows 真机；其它平台如实跳过）
```

## 为什么补这一条

老板 2026-10-20 在这条链上连着踩了两个坑（窗口名乱码、激活不了/没到最上层）。
而此前只有零散单测（`test_visual_launch*.py` 用 mock），**没有一条"对真窗口真拉一次"的端到端判据**。
这条判据用的靶子是**自己起的、标题可控的控制台窗口**（不碰任何人的工作窗口），跑完就关。

## 七格判据

| 格 | 判据 |
|---|---|
| ⓪ | 起靶子窗口（标题 `ACT_AC_TEST_*`），确认它出现在候选列表里 |
| ① | **编码**：`-List` 里若存在中文标题，**不许出现替换字符 U+FFFD**（老板报的"一片方块"） |
| ② | **只读**：`options()` 不建目录、不改文件、不改成员对象 |
| ③ | `options()` 的 wmpaste 带 `note`（提前说明"这类窗口常送不进去"） |
| ④ | **真拉一次**：`method="wmpaste"` 对无文本框窗口 ⇒ **自动改剪贴板档并成功**，`fallback_from="wmpaste"` |
| ⑤ | **真置顶**：返回里 `foreground=True`（老板要的"调到最上层"确实做到了） |
| ⑥ | **不许假成功**：标题完全不存在的会话名 ⇒ `ok=False`，且不产生 clipboard 兜底结论 |
| ⑦ | 收尾：靶子窗口被关掉（不留垃圾进程） |
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

IS_WIN = os.name == "nt"
TAG = "ACT_AC_TEST_%d" % (int(time.time()) % 100000)


class _Member:
    """最小成员替身：平台只用 `session_aliases` / `session_ref` 寻址。"""

    def __init__(self, name: str):
        self.session_aliases = [name]
        self.session_ref = {}


def main() -> int:
    from agent_community.platform import visual_launch as vl

    fails = 0
    skips = []

    def cell(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        print("   %-50s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    def skip(name: str, why: str) -> None:
        """如实跳过：**没验到**（不是通过，也不算红）。

        用来把「产品坏了」和「这台机器此刻测不了」分开 —— 后者判红会逼人去修不存在的 bug。
        """
        skips.append(name)
        print("   %-50s %-6s %s" % (name, "如实跳过", why))

    print("=" * 92)
    print("视觉拉起端到端判据（靶子窗口 %s）" % TAG)
    print("=" * 92)

    if not IS_WIN:
        # 如实说：这套机制只在 Windows 上存在（其它平台**不假装成功**）
        r = vl.list_windows()
        supported = bool(r.get("supported"))
        cell("非 Windows：如实返回「不支持」", (r.get("ok") is False) and (not supported),
             r.get("detail", "")[:60])
        print("-" * 92)
        print("结论：**本平台不支持消息式注入 —— 如实跳过真机部分**（不是失败）")
        return 0 if not fails else 1

    proc = None
    try:
        # ⓪ 起靶子：控制台窗口，标题可控、纯 ASCII（避免命令行传参编码干扰）
        # ⚠️ 必须 `CREATE_NEW_CONSOLE`：第一版用了 `CREATE_NO_WINDOW` ⇒ 进程**没有窗口**，
        #    于是 `-List` 永远扫不到它（判据自己把自己坑了，实测踩到）。
        proc = subprocess.Popen(
            ["cmd", "/c", "title %s & ping -n 90 127.0.0.1 > nul" % TAG],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        found = None
        for _ in range(40):
            time.sleep(0.5)
            r = vl.list_windows(TAG)
            hit = [w for w in (r.get("windows") or []) if TAG in (w.get("title") or "")]
            if hit:
                found = hit[0]
                break
        cell("⓪ 靶子窗口出现在候选列表", found is not None,
             "hwnd=%s class=%s" % ((found or {}).get("hwnd"), (found or {}).get("class")))

        # ① 编码：有中文标题时不许有替换字符
        allw = vl.list_windows()
        titles = [w.get("title") or "" for w in (allw.get("windows") or [])]
        cn = [t for t in titles if any("\u4e00" <= ch <= "\u9fff" for ch in t)]
        bad = [t for t in titles if "\ufffd" in t]
        if cn:
            cell("① 中文窗口标题不乱码", not bad,
                 "中文标题 %d 个；乱码 %d 个%s" % (len(cn), len(bad),
                                                   ("，例：%s" % cn[0][:24]) if cn else ""))
        else:
            print("   %-50s %-6s %s" % ("① 中文窗口标题不乱码", "SKIP",
                                        "这台机器上暂时没有中文标题窗口，无法实证（不假绿）"))

        # ② 只读：options 不许有副作用
        m = _Member(TAG)
        before = (list(m.session_aliases), dict(m.session_ref))
        opt = vl.options(m, probe=True)
        cell("② options() 只读（不改成员、无副作用）",
             (list(m.session_aliases), dict(m.session_ref)) == before
             and isinstance(opt.get("methods"), list) and len(opt["methods"]) >= 2,
             "methods=%s" % [x.get("method") for x in opt.get("methods", [])])

        # ③ wmpaste 提前说明"这类窗口常送不进去"
        wp = next((x for x in opt.get("methods", []) if x.get("method") == "wmpaste"), {})
        cell("③ wmpaste 选项带提醒（别让人以为一定能送）",
             bool(wp.get("note")), (wp.get("note") or "（缺 note）")[:40])

        # ④⑤ 真拉一次：无文本框窗口 ⇒ 自动改剪贴板 + 真置顶
        res = vl.launch(m, "echo VISUAL_LAUNCH_CHECK_OK", method="wmpaste", verify=True)
        # ★2026-10-23（真机实测）：剪贴板会被别的程序占着（Windows `Set-Clipboard` 抛
        #   `ExternalException: Requested Clipboard operation did not succeed.`）⇒ 平台**如实报失败**
        #   （这是对的）。这种情况下 ④⑤ 属于**环境没条件**、不是产品回归：
        #   如实跳过并写明原因；只有"不是剪贴板问题却失败"才判红。
        _clip_env = bool(res.get("clipboard_unavailable")) or (
            "Clipboard operation did not succeed" in str(res.get("detail") or ""))
        if _clip_env:
            skip("④ wmPaste 不适用 ⇒ 自动改剪贴板档且成功",
                 "本机剪贴板被占用/不可用（%s）⇒ 兜底路径没法实证；平台已如实报失败"
                 % str(res.get("detail"))[:80])
            skip("⑤ 真的把窗口调到最上层（foreground=True）", "随 ④ 一起无法实证（同一次拉起）")
        else:
            cell("④ wmPaste 不适用 ⇒ 自动改剪贴板档且成功",
                 bool(res.get("ok")) and res.get("method") == "clipboard"
                 and res.get("fallback_from") == "wmpaste",
                 "ok=%s method=%s from=%s" % (res.get("ok"), res.get("method"), res.get("fallback_from")))
            cell("⑤ 真的把窗口调到最上层（foreground=True）",
                 res.get("foreground") is True,
                 "foreground=%s ｜ %s" % (res.get("foreground"), str(res.get("detail"))[:60]))

        # ⑥ 不许假成功：根本不存在的标题
        ghost = _Member("NO_SUCH_WINDOW_%d" % int(time.time()))
        r2 = vl.launch(ghost, "x", method="wmpaste", verify=False)
        cell("⑥ 找不到窗口 ⇒ 如实失败（不许假成功）",
             r2.get("ok") is False and "no matching window" in str(r2.get("detail")),
             "ok=%s ｜ %s" % (r2.get("ok"), str(r2.get("detail"))[:50]))
    finally:
        if proc is not None:
            # 收尾走**项目统一的进程层**（`platform/sysprobe`，psutil 优先、没有 psutil 自动转纯标准库层）：
            # 别在这里写 `taskkill` 这类平台专属命令（第一版就写了，被反私人定制扫描当场抓到）。
            try:
                from agent_community.platform import sysprobe
                sysprobe.kill_pid(proc.pid, tree=True)
            except Exception:                                # noqa: BLE001
                try:
                    proc.kill()
                except Exception:                            # noqa: BLE001
                    pass
        left = [w for w in (vl.list_windows(TAG).get("windows") or []) if TAG in (w.get("title") or "")]
        print("   %-50s %-6s %s" % ("⑦ 靶子窗口已清理（不留垃圾）", "OK" if not left else "★FAIL",
                                    "残留 %d 个" % len(left)))
        if left:
            fails += 1

    print("-" * 92)
    if fails:
        print("结论：**有 %d 格失败**%s" % (fails, ("（另有 %d 格如实跳过）" % len(skips)) if skips else ""))
        return 1
    if skips:
        print("结论：**部分验证** —— 能测的都过了，%d 格**如实跳过**（环境没条件，不当通过）：%s"
              % (len(skips), "、".join(skips)))
        return 2
    print("结论：**视觉拉起端到端全绿（列表/编码/只读/兜底/置顶/不假成功/清理）**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
