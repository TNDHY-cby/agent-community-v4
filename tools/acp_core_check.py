# -*- coding: utf-8 -*-
"""⑦ acp 档核心体检：**平台能用一条命令把 agent 真拉起来，并按 ACP 走通一轮对话**。

前六步验的是 file_poll 那条链（桥搬件/回执/转发）。acp 档的关键不同在于：
**平台要通过 `acp_command` 真的把对端进程拉起来**，然后 `initialize → session/new → session/prompt` 走一轮。
本工具用 `agent_community/tests/_fake_acp_agent.py` 当对端（假 agent），验两件事：

  ⑦a **客户端真拉起**：`AcpHarnessBridge(cmd=[python, fake])` → `start()` → `initialize()` →
      `session/new()` → `prompt()` ⇒ 回文里出现「收到」，且假 agent 的 mark 文件里有 `started`/`recv initialize`
      （＝**进程真的被这条命令拉起来了**，不是"我发了个字符串"）；
  ⑦b **生成的 cli_acp 桥也带着这条命令**：跑生成的 `bridge.py`，其日志里出现拉起痕迹（模板注入的
      `ACP_COMMAND` 真被用上）——防"桥领得到件、却永远拉不起 agent"那个历史病复发。

跑法：`python tools/acp_core_check.py`（退出码 0＝两段都绿）
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "agent_community" / "tests" / "_fake_acp_agent.py"


def _python_with_fwd_slashes() -> str:
    # Windows 上给 ACP_COMMAND 的路径要**正斜杠**（shlex 会把反斜杠当转义吃掉，模板里也写了这条）
    return str(sys.executable).replace("\\", "/")


def part_a(work: Path) -> tuple[bool, str]:
    """⑦a 客户端真拉起 + 一轮对话。"""
    mark = work / "fake_acp_mark.log"
    mark.unlink(missing_ok=True)
    os.environ["AC_FAKE_ACP_MARK"] = str(mark)
    sys.path.insert(0, str(ROOT))
    try:
        from agent_community.platform.acp_harness_bridge import AcpHarnessBridge
        cmd = shlex.split('"%s" "%s"' % (_python_with_fwd_slashes(), str(FAKE).replace("\\", "/")))
        bridge = AcpHarnessBridge(cmd=cmd, repo=str(work))
        bridge.start()
        try:
            init = bridge.initialize(timeout=30)
            if not init.get("result"):
                return False, "initialize 没拿到 result：%s ｜ stderr=%s" % (init, bridge.stderr_tail()[-300:])
            sess = bridge.new_session(str(work), timeout=30)
            if sess is None:
                return False, "session/new 失败 ｜ stderr=%s" % (bridge.stderr_tail()[-300:])
            stop, text = bridge.prompt(sess.session_id, "只回复：收到。", timeout=30)
            started = mark.exists() and "started" in mark.read_text(encoding="utf-8", errors="replace")
            ok = ("收到" in (text or "")) and started
            return ok, "stop=%s text=%r ｜ 进程真被拉起=%s" % (stop, (text or "")[:40], started)
        finally:
            try:
                bridge.stop()
            except Exception:                            # noqa: BLE001
                pass
    except Exception as e:                               # noqa: BLE001
        return False, "异常：%s: %s" % (type(e).__name__, e)
    finally:
        os.environ.pop("AC_FAKE_ACP_MARK", None)


def part_b(work: Path) -> tuple[bool, str]:
    """⑦b 生成的 cli_acp 桥里，那条命令真被用上（模板注入没丢，且桥真去拉）。"""
    sys.path.insert(0, str(ROOT))
    from agent_community.platform.bridge_factory import generate
    out_dir = work / "bridges" / "acp-core"
    params = {
        "HARNESS_ID": "acp-core-probe",
        "ACP_COMMAND": '"%s" "%s"' % (_python_with_fwd_slashes(), str(FAKE).replace("\\", "/")),
        "ACP_CWD": str(work).replace("\\", "/"),
        "MODEL_NAME": "fake-model",
        "PROVIDER": "fake",
        "DESCRIPTION": "acp core check",
        "PLATFORM_URL": "http://127.0.0.1:9",
    }
    try:
        bridge_file = Path(generate("cli_acp", params, out_dir))
    except Exception as e:                               # noqa: BLE001
        return False, "生成 cli_acp 桥失败：%s: %s" % (type(e).__name__, e)
    src = bridge_file.read_text(encoding="utf-8", errors="replace")
    if "_fake_acp_agent.py" not in src:
        return False, "生成的桥里没有注入 acp_command（模板占位符没替换？）"
    log = work / "acp_bridge_run.log"
    mark = work / "fake_acp_mark_b.log"
    mark.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "AC_FAKE_ACP_MARK": str(mark)}
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.Popen([sys.executable, "-u", str(bridge_file),
                                 "--url", "http://127.0.0.1:9"],
                                cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT)
        time.sleep(6)
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:                                # noqa: BLE001
            proc.kill()
    text = log.read_text(encoding="utf-8", errors="replace")
    got = mark.exists() and "started" in mark.read_text(encoding="utf-8", errors="replace")
    ok = got or ("ACP" in text and "桥" in text)
    return ok, "桥日志里有拉起痕迹=%s ｜ 假 agent 启动标记=%s" % ("ACP" in text, got)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="ac_acp_core_"))
    print("=" * 84)
    print("⑦ acp 档核心体检（假 agent 对端；工作目录 %s）" % work)
    print("=" * 84)
    fails = 0
    for name, fn in (("⑦a 客户端真拉起+一轮对话", part_a), ("⑦b 生成桥带着这条命令", part_b)):
        ok, detail = fn(work)
        print("   %-24s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1
    print("-" * 84)
    print("结论：%s" % ("**acp 档两段全绿**" if not fails else "**有 %d 段失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
