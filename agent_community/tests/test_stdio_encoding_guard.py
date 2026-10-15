# -*- coding: utf-8 -*-
"""`stdio_utf8` 守卫：**平台自己的入口在非 UTF-8 输出下不许崩**。

## 来历（外部用户真跑抓到 → 本机按同样条件复现）

外部朋友按 README 第 2 步 `python -m agent_community.cli start --no-gui`：
CLI 起了后台进程、几秒后报「服务启动超时」，日志里只有一句 ——

```text
UnicodeEncodeError: 'gbk' codec can't encode character '\u21d2' in position 13
  File ".../agent_community/platform/server.py", line 6277, in <module>
```

根因：`cli start` 把 server 的 stdout/stderr **重定向到日志文件** ⇒ Python 用**区域编码**
（中文 Windows = cp936、英文 Windows = cp1252）⇒ 启动横幅里的 `⇒` 编不出去 ⇒ **服务第一秒就死**。
**有真控制台时不发作**（PEP 528 走 WriteConsoleW）⇒ 开发者本机常常看不见，专挑陌生人的第一次。

判据（本文件）：
  ① **真起 server 进程**、stdout/stderr 重定向到文件、把输出编码逼成 **cp1252**（英文 Windows 同款）
     ⇒ 必须**活着**（`/api/status` 答 running）且日志里**没有 `UnicodeEncodeError`**；
  ② 不设 `PYTHONIOENCODING`（走**本机区域编码**；中文 Windows = cp936，正是外部用户的环境）
     ⇒ 同样必须活着；
  ③ ★**证明这条守卫不是摆设**：同一段"中文 + `⇒`"的输出在**没有**自保代码时**真的会崩**
     （对照组实跑，不靠推理）；
  ④ 自保动作本身**不许反过来炸**：流不支持 `reconfigure` 时安静跳过（返回没改到的名单）。
"""
from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from agent_community.stdio_utf8 import STDIO_ENCODING, STDIO_ERRORS, force_utf8_stdio  # noqa: E402


# ── ④ 自保动作本身 ──────────────────────────────────────────────────────
class _Stream:
    def __init__(self, boom=False):
        self.calls = []
        self.boom = boom

    def reconfigure(self, **kw):
        if self.boom:
            raise ValueError("这个流不支持 reconfigure")
        self.calls.append(kw)


def test_helper_pins_encoding_and_error_policy():
    s1, s2 = _Stream(), _Stream()
    done = force_utf8_stdio(s1, s2)
    assert s1.calls == [{"encoding": STDIO_ENCODING, "errors": STDIO_ERRORS}], \
        "必须把编码与错误策略一起钉住（只钉编码的话，编不出去的字符仍会抛异常）"
    assert s2.calls and "extra0" in done and "extra1" in done


def test_helper_never_raises_when_stream_refuses():
    """**自保动作不许变成新的崩溃点**：流不支持就安静跳过。"""
    bad = _Stream(boom=True)
    done = force_utf8_stdio(bad, None)
    assert "extra0" not in done, "改不到的流不该出现在成功名单里"
    assert isinstance(done, list)


def test_replace_policy_protects_against_unencodable_surrogates():
    """★**"只钉编码"是不够的**：孤立代理项（Windows 上路径/文件名解不出来时就会有）
    连 UTF-8 都编不出去 ⇒ 必须是 `errors="replace"`，否则打印一句话就能让服务崩。

    这条同时是"错误策略确实生效"的**可观察**判据（不是拿常量跟自己比）。
    """
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="")
    done = force_utf8_stdio(stream)
    assert "extra0" in done
    stream.write("坏名字 \udcff 结尾\n")      # 孤立代理项：strict 下连 utf-8 都会抛
    stream.flush()
    assert "坏名字" in raw.getvalue().decode("utf-8", "replace")


# ── ③ 对照组：证明"非 UTF-8 输出 + 这些字符"真的会崩 ────────────────────────
_BANNER = "print('[访问] 未配置访问令牌 \u21d2 **只有本机能访问**（要开外部访问：设 AC4_TOKEN=…）')"


def test_the_crash_is_real_without_the_guard():
    """★**这条守卫不是纸老虎**：同一个"中文 + `⇒`"在 cp1252 输出下，**没有**自保就真的会崩。"""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    r = subprocess.run([sys.executable, "-c", _BANNER], capture_output=True, env=env, timeout=60)
    assert r.returncode != 0, "对照组没崩 —— 那本守卫可能测不出真问题（先查预测条件）"
    assert b"UnicodeEncodeError" in (r.stderr or b""), \
        "对照组不是因为编码崩的：%r" % (r.stderr or b"")[:200]

    code = ("from agent_community.stdio_utf8 import force_utf8_stdio as f\n"
            "f()\n" + _BANNER)
    r2 = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env,
                        cwd=_ROOT, timeout=60)
    assert r2.returncode == 0, "加了自保还崩：%r" % (r2.stderr or b"")[:300]
    assert "\u21d2" in r2.stdout.decode("utf-8"), "自保之后输出仍是 UTF-8（内容不能丢）"


# ── ①② 真起 server 进程 ────────────────────────────────────────────────
def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _wait_running(port: int, proc, deadline_s: float = 60.0):
    end = time.time() + deadline_s
    while time.time() < end:
        if proc.poll() is not None:
            return None
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=3) as r:
                import json
                data = json.loads(r.read().decode("utf-8", "replace"))
            if data.get("status") == "running":
                return data
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)
    return None


def _run_server_case(tmp_path, extra_env: dict) -> tuple:
    """起一个真 server：stdout/stderr **重定向到文件**（`cli start` 就是这么起的）。"""
    port = _free_port()
    data_dir = tmp_path / "acdata"
    data_dir.mkdir(exist_ok=True)
    log = data_dir / "server_out.log"
    env = {**os.environ, "AC_DATA_DIR": str(data_dir), "AC_PORT": str(port),
           "AC_HOST": "127.0.0.1"}
    env.pop("PYTHONIOENCODING", None)          # 先清掉，再按用例显式给
    env.update(extra_env)
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server",
                                 "--port", str(port)],
                                cwd=_ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
        try:
            status = _wait_running(port, proc)
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=20)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
    text = log.read_text(encoding="utf-8", errors="replace")
    return status, text, port


@pytest.mark.parametrize("label,extra_env", [
    ("cp1252（英文 Windows / 任何非 UTF-8 管道）", {"PYTHONIOENCODING": "cp1252"}),
    ("本机区域编码（中文 Windows = cp936，正是外部用户的环境）", {}),
])
def test_server_stays_alive_with_redirected_non_utf8_stdout(tmp_path, label, extra_env):
    status, text, port = _run_server_case(tmp_path, extra_env)
    tail = text[-800:]
    assert "UnicodeEncodeError" not in text, \
        "[%s] 服务崩在编码上（这就是外部用户那次崩溃）：\n%s" % (label, tail)
    assert status is not None, "[%s] 服务没起来（端口 %s）：\n%s" % (label, port, tail)
    assert status.get("status") == "running"
    assert "\u21d2" in text or "只有本机能访问" in text, \
        "[%s] 启动横幅没打出来 —— 检查它是不是被静默吞掉了：\n%s" % (label, tail)


def test_cli_itself_survives_non_utf8_stdout():
    """CLI **自己**那一路也要自保（它的输出同样会被重定向；`cli status` 是最安全的一条命令）。"""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "AC_PORT": str(_free_port())}
    env.pop("AC_DATA_DIR", None)
    r = subprocess.run([sys.executable, "-m", "agent_community.cli", "status"],
                       cwd=_ROOT, env=env, capture_output=True, timeout=120)
    err = (r.stderr or b"").decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in err, "CLI 崩在编码上：\n%s" % err[-500:]
    assert r.returncode == 0, "cli status 不该因为编码失败（exit=%s）：\n%s" % (r.returncode, err[-500:])
    assert (r.stdout or b"").strip(), "CLI 什么都没输出（被静默吞掉了？）"
