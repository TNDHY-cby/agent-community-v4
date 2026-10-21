# -*- coding: utf-8 -*-
"""tools/launcher.py —— **双击到大厅的启动器**（V72）。

老板 2026-10-21 原话：

    「双击后没有反应，我要双击后**先播放动画**，**同步加载进程**，然后**自动弹出平台大厅**」

## 它做四件事（顺序就是体验）

1. **先出画面**：在本机起一个**只服务过场页**的临时 HTTP 服务（端口由内核给，不写死），
   立刻打开浏览器 → 过场动画在**服务还没起来时**就已经在播；
2. **并行加载**：同时（不等动画）在后台拉起平台服务，隐藏窗口，输出落 `data/logs/launcher_*.log`；
3. **就绪即进大厅**：轮询 `GET /api/status`，200 且是平台 ⇒ `/state` 变 `ready` ⇒ 过场页自动跳大厅；
4. **失败要说话**：没解释器 / 端口被别人占 / 进程起来就退 / 超时 —— 每一类都给出
   「原因 + 怎么办 + 日志在哪」，**绝不留"双击后什么都没发生"**。

## 纪律

- **全程标准库**（与看门狗同一纪律：它要在"环境坏掉"时也能跑）；
- **不写死端口**：平台端口走 `agent_community/port_resolve.py` 的**唯一口径**
  （`AC_PORT` > 活着的 PID 记录 > `platform_address` 默认值）；过场页端口由系统临时分配；
- **数据落点认 `AC_DATA_DIR`**：日志走 `platform/env_spec.data_dir()`（同一处真相）；
- 过场页只**只读引用**前端的 `boot.css` 与两张图（美术归前端，我不复制一份样式）。

用法：

    python tools/launcher.py                # 正常：开过场页 → 等就绪 → 进大厅
    python tools/launcher.py --no-open      # 不开浏览器（判据/服务器环境用），打印地址
    python tools/launcher.py --no-start     # 只起过场页，不拉平台（调试过场用）
    python tools/launcher.py --timeout 90   # 健康等待上限（默认 60 秒）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None:                              # pythonw 下可能没有 stdout/stderr
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                        # noqa: BLE001
        pass

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_community.port_resolve import cli_port, port_listening, probe  # noqa: E402

FRONTEND = REPO / "agent_community" / "frontend"
OVER_HTML = REPO / "tools" / "launcher" / "over.html"

#: 过场页能取的静态资源（白名单；只读前端美术与样式）
STATIC = {
    "/boot.css": (FRONTEND / "boot.css", "text/css; charset=utf-8"),
    "/boot_emblem.png": (FRONTEND / "boot_emblem.png", "image/png"),
    "/boot_gear.png": (FRONTEND / "boot_gear.png", "image/png"),
}

#: 阶段口径（页面直接显示这里的中文；不要在页面里另编一套）
STEP_TEXT = {
    0: "读取启动配置",
    1: "选择解释器",
    2: "正在拉起平台服务",
    3: "等待端口就绪",
    4: "等待健康检查通过",
    5: "就绪，正在进入大厅",
    6: "启动失败",
}

T0 = time.time()
_LOCK = threading.Lock()
_STATE = {
    "phase": "starting",     # starting | ready | failed
    "step": 0,
    "text": STEP_TEXT[0],
    "hint": "",
    "log_path": "",
    "elapsed_ms": 0,
    "platform_url": "",
    "launcher_url": "",
}

#: 过场页最后一次被访问的时间（用来判断"页面已经跳走了，启动器可以退出"）
LAST_HIT = [time.time()]


def set_state(**kw) -> None:
    """更新对外状态（**唯一写入口**；页面读的就是它）。"""
    with _LOCK:
        _STATE.update(kw)
        _STATE["elapsed_ms"] = int((time.time() - T0) * 1000)


def snapshot() -> dict:
    with _LOCK:
        out = dict(_STATE)
    out["elapsed_ms"] = int((time.time() - T0) * 1000)
    return out


# ── 失败分类：每一类都必须能说清"为什么 + 怎么办" ───────────────────────────
def fail(reason: str, hint: str, log_path: str = "") -> None:
    set_state(phase="failed", step=6, text=reason, hint=hint,
              log_path=log_path or _STATE.get("log_path", ""))


def log_dir() -> Path:
    """日志落点：**跟 `AC_DATA_DIR` 走**（与平台/看门狗同一处真相）。"""
    try:
        from agent_community.platform.env_spec import data_dir

        base = Path(data_dir())
    except Exception:                                        # noqa: BLE001
        env = os.environ.get("AC_DATA_DIR")
        base = Path(env) if env else REPO / "agent_community" / "data"
    d = base / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:                                        # noqa: BLE001
        pass
    return d


def _hidden_kwargs() -> dict:
    """Windows 下不弹黑框（启动器自己是 pythonw 起的，但子进程也要隐藏）。"""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return {"startupinfo": si, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _dotenv_value(name: str):
    """从 `deploy/.env` 或仓库根 `.env` 读一个键（**只读**，不改文件、不回写）。

    为什么启动器要读它：老启动器（`启动合作社-控制台.cmd` 的前身）就是用
    `deploy/.env` 里的 `AC4_PYTHON` 指定解释器的 —— 这是项目**已有的**部署约定，
    启动器若不认它，就会出现"用户明明配好了，双击却说找不到 Python"。
    """
    for p in (REPO / "deploy" / ".env", REPO / ".env"):
        try:
            if not p.exists():
                continue
            for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
        except Exception:                                    # noqa: BLE001
            continue
    return None


def interpreter_candidates():
    """候选解释器（先"用户指定"，再"当前解释器"，最后 PATH 上的常见名）。

    `AC_LAUNCHER_PYTHON` 是**唯一**的"强制指定"入口（启动器自己的开关，
    与平台的 `AC4_PYTHON` 分开：后者是部署配置，前者是启动器排障/判据用）。
    """
    out = []
    forced = str(os.environ.get("AC_LAUNCHER_PYTHON") or "").strip()
    if forced:
        return [[forced]]
    env_py = str(os.environ.get("AC4_PYTHON") or "").strip() or (_dotenv_value("AC4_PYTHON") or "")
    if env_py:
        out.append([env_py])
    if sys.executable:
        out.append([sys.executable])
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p:
            out.append([p])
    if os.name == "nt":
        py = shutil.which("py")
        if py:
            out.append([py, "-3"])
    seen, uniq = set(), []
    for c in out:
        key = tuple(c)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def _has_deps(cmd) -> bool:
    """这个解释器能不能 import 平台要的两样东西（启动器只认能跑的）。"""
    try:
        r = subprocess.run(cmd + ["-c", "import fastapi, uvicorn"],
                           cwd=str(REPO), capture_output=True, timeout=30, **_hidden_kwargs())
        return r.returncode == 0
    except Exception:                                        # noqa: BLE001
        return False


def pick_interpreter():
    """挑一个**装好依赖**的解释器；一个都没有就返回 None（由调用方给出安装指引）。"""
    for cmd in interpreter_candidates():
        if _has_deps(cmd):
            return cmd
    return None


def tail_file(path: Path, n: int = 20) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:                                        # noqa: BLE001
        return ""


def run_platform(port: int, timeout: float) -> None:
    """③ 拉平台 + ④ 等健康（**整个生命周期都在后台线程里，页面不等它**）。"""
    from agent_community.port_resolve import probe as _probe

    platform_url = "http://127.0.0.1:%d/" % port
    set_state(platform_url=platform_url)

    # 已经在跑（比如看门狗刚救活、或用户重复双击）⇒ 不重复拉进程
    if _probe(port):
        set_state(phase="ready", step=5, text=STEP_TEXT[5])
        return

    if port_listening(port):
        fail("端口 %d 被别的程序占着（不是平台）" % port,
             "关掉占用该端口的程序，或设环境变量 AC_PORT 换个端口再双击")
        return

    set_state(step=1, text=STEP_TEXT[1])
    py = pick_interpreter()
    if py is None:
        fail("这台机器上没找到装好依赖的 Python",
             "装依赖：python -m pip install -r requirements.txt（或在 deploy/.env 里用 AC4_PYTHON 指定解释器绝对路径）")
        return

    log_path = log_dir() / ("launcher_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
    set_state(step=2, text="%s…（已 0 秒）" % STEP_TEXT[2], log_path=str(log_path))
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["AC_PORT"] = str(port)
    try:
        logf = open(log_path, "ab", buffering=0)
    except Exception as e:                                   # noqa: BLE001
        logf = None
        set_state(hint="日志写不了（%s），不影响启动" % e)

    proc = None
    try:
        proc = subprocess.Popen(
            list(py) + ["-m", "agent_community.platform.server", "--port", str(port)],
            cwd=str(REPO), env=env,
            stdout=logf or subprocess.DEVNULL, stderr=subprocess.STDOUT,
            **_hidden_kwargs())
    except Exception as e:                                   # noqa: BLE001
        fail("拉不起平台进程：%s: %s" % (type(e).__name__, e),
             "看日志 %s；或直接在本仓库跑 python -m agent_community.platform.server 看报错" % log_path,
             str(log_path))
        return
    finally:
        if logf is not None:
            logf.close()

    deadline = time.time() + float(timeout)
    waited_sock = False
    while time.time() < deadline:
        if _probe(port):
            set_state(phase="ready", step=5, text=STEP_TEXT[5])
            return
        code = proc.poll()
        if code is not None:
            tail = tail_file(log_path)
            fail("平台进程启动后立刻退出（退出码 %s）" % code,
                 ("按日志里第一条错误改；完整日志：%s" % log_path) +
                 (("\n\n最后几行：\n" + tail) if tail else ""),
                 str(log_path))
            return
        if not waited_sock and port_listening(port):
            waited_sock = True
            set_state(step=4, text=STEP_TEXT[4])
        else:
            elapsed = int(time.time() - (deadline - float(timeout)))
            set_state(step=3 if not waited_sock else 4,
                      text="%s…（已 %d 秒）" % (STEP_TEXT[3 if not waited_sock else 4], elapsed))
        time.sleep(0.4)

    tail = tail_file(log_path)
    fail("启动超时（%.0f 秒还没通过健康检查）" % float(timeout),
         ("服务可能很慢，也可能卡住了。看日志：%s"
          "；或在本仓库跑 python -m agent_community.platform.server 直接在控制台看报错" % log_path) +
         (("\n\n最后几行：\n" + tail) if tail else ""),
         str(log_path))


# ── 过场页（启动器自己 serve，同源轮询 /state，不需要给平台加 CORS）──────────
class Handler(BaseHTTPRequestHandler):
    server_version = "ACLauncher/1"

    def log_message(self, fmt, *args):                       # 静默（别刷屏）
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:                                    # noqa: BLE001
            pass

    def do_GET(self):                                        # noqa: N802
        LAST_HIT[0] = time.time()
        path = (self.path or "/").split("?")[0]
        if path in ("/", "/index.html"):
            try:
                body = OVER_HTML.read_bytes()
                return self._send(200, body, "text/html; charset=utf-8")
            except Exception as e:                           # noqa: BLE001
                return self._send(500, ("过场页读不到：%s" % e).encode("utf-8"),
                                  "text/plain; charset=utf-8")
        if path == "/state":
            body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
            return self._send(200, body, "application/json; charset=utf-8")
        if path in STATIC:
            f, ctype = STATIC[path]
            try:
                return self._send(200, f.read_bytes(), ctype)
            except Exception:                                # noqa: BLE001
                return self._send(404, b"not found", "text/plain; charset=utf-8")
        return self._send(404, b"not found", "text/plain; charset=utf-8")


def _find_browser_exe(name: str):
    """按名字找一个浏览器的可执行文件（PATH → 注册表 App Paths → 常见安装目录）。

    **不绑定任何一家**：`AC_LAUNCHER_BROWSER` 写什么就找什么（也接受绝对路径）；
    找不到就如实回落系统默认浏览器 —— 不假装成功。
    """
    p = str(name or "").strip()
    if not p:
        return None
    if os.path.isabs(p):
        return p if Path(p).exists() else None
    exe = p if p.lower().endswith(".exe") else (p + ".exe")
    hit = shutil.which(exe)
    if hit:
        return hit
    if os.name == "nt":
        try:
            import winreg

            for root, key in ((winreg.HKEY_CURRENT_USER, "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe),
                              (winreg.HKEY_LOCAL_MACHINE, "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe)):
                try:
                    with winreg.OpenKey(root, key) as k:
                        v = winreg.QueryValue(k, None)
                        v = str(v or "").strip('"')
                        if v and Path(v).exists():
                            return v
                except Exception:                            # noqa: BLE001
                    continue
        except Exception:                                    # noqa: BLE001
            pass
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            for sub in (p, os.path.join("Mozilla Firefox", "firefox.exe"),
                        os.path.join("Google", "Chrome", "Application", "chrome.exe"),
                        os.path.join("Microsoft", "Edge", "Application", "msedge.exe")):
                cand = Path(base) / sub
                if cand.suffix.lower() == ".exe" and cand.exists():
                    return str(cand)
    return None


def open_browser(url: str, prefer: str = "") -> bool:
    """打开浏览器：**先按用户指定**（`AC_LAUNCHER_BROWSER`），找不到再回落系统默认。

    老板 2026-10-21：「启动后使用火狐浏览器」⇒ 本机部署配置里写 `AC_LAUNCHER_BROWSER=firefox`，
    **代码里不绑定任何一家**（换机器/换浏览器只改配置）。
    """
    exe = _find_browser_exe(prefer) if prefer and prefer.lower() != "default" else None
    if exe:
        try:
            subprocess.Popen([exe, url], close_fds=True, **_hidden_kwargs())
            return True
        except Exception:                                    # noqa: BLE001
            pass
    for fn in (lambda: webbrowser.open(url),
               (lambda: os.startfile(url)) if os.name == "nt" else (lambda: False)):
        try:
            if fn() is not False:
                return True
        except Exception:                                    # noqa: BLE001
            continue
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, "平台已经在启动，但没能自动打开浏览器。\n\n手动打开这个地址：\n%s" % url,
                "生产合作社 · 启动器", 0x40)
        except Exception:                                    # noqa: BLE001
            pass
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生产合作社启动器（先播过场 · 并行加载 · 自动进大厅）")
    ap.add_argument("--no-open", action="store_true", help="不开浏览器（判据/无桌面环境）")
    ap.add_argument("--no-start", action="store_true", help="只起过场页，不拉平台（调试用）")
    ap.add_argument("--port", type=int, default=None, help="平台端口（默认按 port_resolve 口径）")
    ap.add_argument("--timeout", type=float, default=60.0, help="健康等待上限（秒）")
    ap.add_argument("--keep", type=float, default=120.0, help="就绪后再服务多久（秒，页面没在看就提前退）")
    ap.add_argument("--keep-failed", type=float, default=600.0, help="失败后再服务多久（秒）")
    ap.add_argument("--browser", default="",
                    help="用哪个浏览器打开（如 firefox；默认取 AC_LAUNCHER_BROWSER，空=系统默认）")
    ap.add_argument("--print-url", action="store_true", help="打印过场页地址后继续运行（判据用）")
    args = ap.parse_args(argv)

    # 部署配置也得算数：`deploy/.env` 里写了端口 / 数据目录时**补进环境**
    # （老启动器就是这么做的；不补的话会出现"配置里写着 18930、启动器却按 18920 起"的两套口径）。
    # ⚠️ 部署层沿用历史命名 `AC4_SERVER_PORT`，平台层认 `AC_PORT` —— 这里只做**别名收口**，
    #    不让第三个名字长出来。
    aliases = {"AC_PORT": ("AC_PORT", "AC4_SERVER_PORT"),
               "AC_DATA_DIR": ("AC_DATA_DIR", "AC4_DATA_DIR")}
    for key, names in aliases.items():
        if str(os.environ.get(key) or "").strip():
            continue
        for name in names:
            v = _dotenv_value(name)
            if v:
                os.environ[key] = v
                break

    port = int(args.port or cli_port())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    launcher_url = "http://127.0.0.1:%d/" % srv.server_address[1]
    set_state(launcher_url=launcher_url, platform_url="http://127.0.0.1:%d/" % port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    if args.print_url:
        print(launcher_url, flush=True)
    browser_pref = (args.browser or os.environ.get("AC_LAUNCHER_BROWSER")
                    or _dotenv_value("AC_LAUNCHER_BROWSER") or "").strip()
    if browser_pref:
        os.environ["AC_LAUNCHER_BROWSER"] = browser_pref
    opened = False
    if not args.no_open:
        opened = open_browser(launcher_url, browser_pref)

    if args.no_start:
        set_state(phase="ready", step=5, text=STEP_TEXT[5])
    else:
        threading.Thread(target=run_platform, args=(port, args.timeout), daemon=True).start()

    # 页面跳走之后启动器就没用了：**按"有没有人还在看"退出**，不常驻
    idle_ready = 20.0 if opened else 300.0
    idle_failed = 180.0 if opened else 900.0
    while True:
        st = snapshot()
        quiet = time.time() - LAST_HIT[0]
        if st["phase"] == "ready":
            if quiet > idle_ready or (time.time() - T0) > args.keep + idle_ready:
                break
        elif st["phase"] == "failed":
            if quiet > idle_failed or (time.time() - T0) > args.keep_failed:
                break
        time.sleep(0.5)
    try:
        srv.shutdown()
    except Exception:                                        # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:                                        # noqa: BLE001
        # pythonw 起的时候没有控制台：**把崩溃写进日志并用对话框说话**，别静默消失
        import traceback

        tb = traceback.format_exc()
        try:
            p = log_dir() / ("launcher_crash_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
            p.write_text(tb, encoding="utf-8")
        except Exception:                                    # noqa: BLE001
            p = None
        if os.name == "nt":
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(
                    None, "启动器自己出错了（已记录）：\n\n%s\n\n日志：%s" % (tb.strip().splitlines()[-1], p),
                    "生产合作社 · 启动器", 0x10)
            except Exception:                                # noqa: BLE001
                pass
        raise SystemExit(1)
