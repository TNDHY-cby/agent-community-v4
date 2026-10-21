# -*- coding: utf-8 -*-
"""守卫：启动器（V72）——**双击到大厅**的那条链路。

判据（真跑，不查源码文本为主）：
  ① 端口口径**只有一处**：`port_resolve.cli_port()` 与 `cli._cli_port()` 给出同一个答案；
     过期 PID 文件里的端口**不许**被采用（这是 2026-10-20 真事故）；
  ② 启动器**不写死端口**（`AC_PORT` 改了就跟），也不把过场页端口写死；
  ③ 过场页存在且真的轮询 `/state`（就绪跳大厅、失败给人话），且**不许**自己编端口；
  ④ 日志落点认 `AC_DATA_DIR`（与平台同一处真相）；
  ⑤ 桌面快捷方式**必须指向本仓库**（指到别的副本 = 起错数据），由 `make_shortcut --check` 判。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

TOOLS = _ROOT / "tools"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


# ── ① 端口口径一处真相 ────────────────────────────────────────────────
def test_cli_and_launcher_share_one_port_resolver(monkeypatch, tmp_path):
    from agent_community import cli, port_resolve

    monkeypatch.setenv("AC_PORT", "19123")
    assert port_resolve.cli_port() == 19123
    assert cli._cli_port() == 19123


def test_stale_pid_port_is_ignored_and_file_removed(monkeypatch, tmp_path):
    """PID 文件里记着一个**没人监听**的端口 ⇒ 不采用，且把过期文件删掉（不留死状态）。"""
    from agent_community import port_resolve

    monkeypatch.delenv("AC_PORT", raising=False)
    pid_file = tmp_path / "server.pid"
    dead_port = 19937
    pid_file.write_text(json.dumps({"pid": 999999, "port": dead_port}), encoding="utf-8")
    got = port_resolve.cli_port(pid_file)
    assert got == port_resolve.resolve_port(), "过期 PID 端口不该被采用"
    assert got != dead_port
    assert not pid_file.exists(), "过期 PID 文件应被顺手删掉"


def test_live_pid_port_is_used(monkeypatch, tmp_path):
    """PID 记录里的端口**真的活着**（这里用一个真监听 socketserver）⇒ 采用它。"""
    import socket
    import threading

    from agent_community import port_resolve

    monkeypatch.delenv("AC_PORT", raising=False)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def _serve():
        # 只要能应答 /api/status 200 就够（判据只认 200）
        body = b'{"version":"x"}'
        while not stop.is_set():
            try:
                srv.settimeout(0.2)
                conn, _ = srv.accept()
            except Exception:                                # noqa: BLE001
                continue
            try:
                conn.recv(4096)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                             b"Content-Length: %d\r\nConnection: close\r\n\r\n%s"
                             % (len(body), body))
            except Exception:                                # noqa: BLE001
                pass
            finally:
                conn.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    try:
        pid_file = tmp_path / "server.pid"
        pid_file.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
        assert port_resolve.cli_port(pid_file) == port
    finally:
        stop.set()
        srv.close()


# ── ② 启动器不写死端口（**按行为判**：给什么 AC_PORT 就报什么平台地址）────
def test_launcher_targets_the_port_from_env(monkeypatch, tmp_path):
    """真跑一次启动器（`--no-start`：只起过场页），断言它报的平台地址就是 `AC_PORT` 给的那个。

    ⚠️ 这条**不查源码文本**：文本里出现端口数字可能只是注释/举例，
    真正要守的是"用户设了端口，启动器就跟着走"。
    """
    import json as _json
    import subprocess
    import threading
    import urllib.request

    port = 19117
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmp_path / "d")})
    env.pop("AC_LAUNCHER_PYTHON", None)
    proc = subprocess.Popen(
        [sys.executable, str(TOOLS / "launcher.py"), "--no-open", "--print-url",
         "--no-start", "--keep", "3"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        line = proc.stdout.readline().strip()
        assert line.startswith("http://"), "启动器没打印过场页地址：%r" % line
        with urllib.request.urlopen(line.rstrip("/") + "/state", timeout=5) as r:
            st = _json.loads(r.read().decode("utf-8"))
        assert st["platform_url"] == "http://127.0.0.1:%d/" % port, st["platform_url"]
        assert st["launcher_url"] == line, "过场页地址应与打印的一致"
        # 过场页端口是内核临时给的，**不是**平台端口
        assert int(line.split(":")[2].strip("/")) != port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:                                    # noqa: BLE001
            proc.kill()


def test_launcher_interpreter_override(monkeypatch):
    sys.path.insert(0, str(TOOLS))
    import importlib

    mod = importlib.import_module("launcher") if "launcher" in sys.modules else None
    if mod is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("launcher", TOOLS / "launcher.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    monkeypatch.setenv("AC_LAUNCHER_PYTHON", "/nope/python")
    assert mod.interpreter_candidates() == [["/nope/python"]]


# ── ③ 过场页 ─────────────────────────────────────────────────────────
def test_over_page_polls_state_and_redirects_to_hall():
    html = _read(TOOLS / "launcher" / "over.html")
    assert "fetch('/state'" in html or 'fetch("/state"' in html, "过场页必须轮询 /state"
    assert "location.replace(s.platform_url)" in html, "就绪后要自动跳大厅（不是让人点）"
    assert "启动失败" in html, "失败要说话"
    assert "重试" in html, "失败要给重试"
    assert "18920" not in html, "过场页不许写死平台端口"
    # 视觉只读复用前端样式（不复制一份 CSS）
    assert 'href="/boot.css"' in html


# ── ③b 发布面：双击入口必须一起发出去 ─────────────────────────────────
def test_publish_ships_the_launcher_entries():
    """别人 clone 下来也得有双击入口 —— 否则"双击进大厅"只存在于开发机。

    ⚠️ 2026-10-21（CI 真跑才暴露）：`publish_acv4.ps1` 是**开发副本独有**的发布工具、**不在发布面**
    ⇒ 在发布副本/CI 里只检查"模板与生成物都在"，不读那个脚本；在开发副本里再要求它同步入口。
    """
    assert (TOOLS / "launcher" / "entry.vbs.tmpl").exists(), "缺 vbs 模板"
    assert (TOOLS / "launcher" / "entry-console.cmd.tmpl").exists(), "缺 cmd 模板"
    assert (_ROOT / "启动合作社.vbs").exists() and (_ROOT / "启动合作社.cmd").exists(), \
        "仓库根缺双击入口（别人 clone 下来就没有入口）"
    publish = _ROOT / "publish_acv4.ps1"
    if not publish.exists():
        pytest.skip("本副本没有 publish_acv4.ps1（发布副本/CI）；同步面检查在开发副本里生效")
    src = _read(publish)
    assert "启动合作社.vbs" in src and "启动合作社.cmd" in src, "发布脚本的 $rootDocs 少了双击入口"


def test_entry_files_match_templates():
    """仓库根的两个入口必须是**模板生成的**（GBK+CRLF），不许手改跑偏。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_entry", TOOLS / "gen_entry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for out_name, tmpl_name in mod.PAIRS:
        out = _ROOT / out_name
        assert out.exists(), "缺生成物：%s（跑 python tools/gen_entry.py）" % out_name
        assert out.read_bytes() == mod.render(TOOLS / "launcher" / tmpl_name), \
            "%s 与模板不一致（跑 python tools/gen_entry.py）" % out_name
        raw = out.read_bytes()
        assert b"\r\n" in raw, "%s 必须是 CRLF" % out_name
        raw.decode("gbk")                                     # 必须是 GBK 能解的字节


def test_over_page_reuses_launcher_static_whitelist():
    src = _read(TOOLS / "launcher.py")
    for name in ("boot.css", "boot_emblem.png", "boot_gear.png"):
        assert name in src, "启动器应只读转发前端静态资源：%s" % name


# ── ④ 日志落点 ───────────────────────────────────────────────────────
def test_launcher_log_dir_follows_data_dir(monkeypatch, tmp_path):
    import importlib.util

    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "d"))
    spec = importlib.util.spec_from_file_location("launcher_ld", TOOLS / "launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    got = mod.log_dir()
    assert Path(got) == tmp_path / "d" / "logs"
    assert Path(got).is_dir()


# ── ⑤ 快捷方式必须指向本仓库 ──────────────────────────────────────────
@pytest.mark.skipif(os.name != "nt", reason=".lnk 是 Windows 的；其它平台如实跳过")
def test_shortcut_check_detects_wrong_copy(monkeypatch, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_shortcut", TOOLS / "make_shortcut.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # 桌面上没有这个快捷方式 ⇒ 必须报"不合规"，不能装作没事
    monkeypatch.setattr(mod, "desktop_dir", lambda: tmp_path)
    assert mod.check(quiet=True) == 1
