# -*- coding: utf-8 -*-
"""**一键更新（命令行后端）**：查新版 → 备份 → 拉取 → 校验 → 必要时回滚/重启。

```bash
python tools/update.py --check          # 只看：现在哪版、最新哪版（不联网猜、不写任何东西）
python tools/update.py --apply          # 真的更新（会自动备份；**数据目录一律不动**）
python tools/update.py --apply --dry-run    # 只打印会做什么
python tools/update.py --apply --restart    # 更新完顺手重启服务（靠看门狗把它拉回来）
python tools/update.py --rollback       # 回到本次更新前的提交（备份点写在数据目录里）
```

## 为什么平台不能"自己改自己"（这条写死在这里）

一键更新的**动作**放在这里、而不是塞进 HTTP 端点：本地自部署平台给自己打补丁是高风险动作，
必须由**用户在他自己的机器上主动执行**（可回滚、可查看）。平台负责**如实回答**版本事实
（`GET /api/version`），工具负责**动手**。

## 安全铁律（每条都有真事）

1. **数据目录一律不动**：默认 `AC_DATA_DIR` 可能就在安装目录**之内**（本机实测就是），
   整体替换会把 harness 注册/工作间/桥/日志一起删掉 ⇒ `--apply` 检测到这种布局**直接拒绝**，
   并告诉用户把 `AC_DATA_DIR` 指到安装目录之外；
2. **只走能回滚的路**：优先 `git`（记录备份提交号，失败 `git reset --hard` 回去）；
   不是 git 检出就**如实拒绝**，不搞"下载压缩包覆盖"这种没法回滚的操作；
3. **工作树必须干净**：有未提交改动就**拒绝更新**（否则用户自己的改动会被卷进冲突）；
4. **更新后必须验版本**：`agent_community.__version__` 要等于目标 tag，否则**自动回滚并报错**；
5. **失败要说话**：每一步都打印"做了什么 / 结果 / 下一步该干啥"。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "agent_community" / "data" / "update_state.json"


# ── 一处真相 ───────────────────────────────────────────────────
def data_dir() -> Path:
    sys.path.insert(0, str(ROOT))
    try:
        from agent_community.platform import env_spec
        return Path(env_spec.data_dir())
    except Exception:                                        # noqa: BLE001
        return Path(os.environ.get("AC_DATA_DIR") or (ROOT / "agent_community" / "data"))


def current_version() -> str:
    """**从磁盘读**版本号（`agent_community/__init__.py`），读不到才退回 import。

    ★2026-10-18 真事故：本工具在更新**之后**要校验版本，但进程里 `agent_community` 早已 import 过
    ⇒ `import` 拿到的是**旧值**，于是"刚更新成功却被判为失败并自动回滚"。
    更新工具必须读**文件**，不能信自己进程里的模块缓存。
    """
    try:
        m = re.search(r'__version__\s*=\s*"([^"]+)"',
                      (ROOT / "agent_community" / "__init__.py").read_text(encoding="utf-8",
                                                                          errors="replace"))
        if m:
            return m.group(1)
    except OSError:
        pass
    sys.path.insert(0, str(ROOT))
    try:
        import agent_community
        return str(agent_community.__version__)
    except Exception:                                        # noqa: BLE001
        return ""


def _ver_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def channel() -> dict:
    try:
        sys.path.insert(0, str(ROOT))
        from agent_community.platform.routers.version import CHANNEL
        return dict(CHANNEL)
    except Exception:                                        # noqa: BLE001
        return {}


def remote_url() -> str:
    r = subprocess.run(["git", "remote", "-v"], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines():
        if line.startswith("gitee"):
            return line.split()[1]
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            return parts[1]
    return ""


# ── 查最新版（两条路：git / Releases API；都能失败要说话）──
def latest_tag() -> tuple:
    """返回 (版本号不带 v, **git 里那条 tag 的真实名字**, 来源)。

    ⚠️ 两个都要：`9.9.9` 用来比大小，`v9.9.9` 才是 git 认的名字 ——
    本机实测踩过：只留"去掉 v"的版本号去 `git merge --ff-only 9.9.9` ⇒
    `merge: 9.9.9 - not something we can merge`（工具把自己的活干砸了）。
    """
    url = remote_url()
    if url:
        r = subprocess.run(["git", "ls-remote", "--tags", url], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        tags = [ln.split("refs/tags/")[-1].replace("^{}", "")
                for ln in (r.stdout or "").splitlines() if "refs/tags/" in ln]
        tags = sorted({t for t in tags if re.match(r"^v?\d+\.\d+\.\d+$", t)}, key=_ver_tuple)
        if tags:
            ref = tags[-1]
            return (ref.lstrip("v"),
                    ref if ref.startswith("v") else "v" + ref,
                    "git ls-remote")
        if r.returncode != 0:
            print("  · 从远端查 tag 失败：%s" % (r.stderr or "").strip()[:120])
    api = (channel() or {}).get("releases_api")
    if api:
        try:
            with urllib.request.urlopen(api, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            raw = str(data.get("tag_name") or "")
            if raw:
                ref = raw if raw.startswith("v") else "v" + raw
                return raw.lstrip("v"), ref, "Gitee Releases API"
        except Exception as e:                                   # noqa: BLE001
            print("  · 查 Releases API 失败：%s: %s" % (type(e).__name__, str(e)[:80]))
    return "", "", "两条路都没拿到（可能离线）"


# ── 安全检查 ───────────────────────────────────────────────────
def safety() -> tuple:
    """返回 (ok, [问题...])。**任何一条不过就不许动**。"""
    problems = []
    install, data = ROOT.resolve(), data_dir().resolve()
    try:
        data.relative_to(install)
        problems.append(
            "数据目录在**安装目录之内**（%s）⇒ 更新会连带删掉数据；"
            "请先把 AC_DATA_DIR 指到安装目录之外再更新" % data)
    except ValueError:
        pass                                     # 数据在安装目录之外 —— 安全形态
    if not (ROOT / ".git").exists():
        problems.append("安装目录不是 git 检出 ⇒ 无法回滚，本工具拒绝更新（请用 git clone 部署）")
        return False, problems
    r = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    IGNORABLE = ("__pycache__/", ".pyc", "agent_community/data/", "/data/logs/", ".pytest_cache/")
    dirty = [ln for ln in (r.stdout or "").splitlines()
             if ln.strip() and not any(tok in ln for tok in IGNORABLE)]
    if dirty:
        problems.append("工作树有未提交改动（%d 项）⇒ 请先提交或还原再更新：%s"
                        % (len(dirty), " ".join(x[3:].strip() for x in dirty[:3])))
    return (not problems), problems


def _clean_env(**over) -> dict:
    """给自检子进程构造环境：**白名单**，不继承外头的 `AC_*`。

    ★2026-10-18 真事故（Linux 侧判据红）：`post_check` 原样继承 `os.environ` ⇒ 在 pytest 里
    跑时（外层有 `AC_ENV` 之类的残留）子进程按"生产模式缺 Token"起不来 ⇒ **好版本被误判成坏版本并回滚**。
    与 `tools/protocol_matrix.py` 里同一个教训：工具起子进程要**白名单**。
    """
    keep = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
            "APPDATA", "LOCALAPPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_", "LD_LIBRARY")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and k.upper().startswith(keep)}
    env["PYTHONPATH"] = str(ROOT)
    env.update({k: str(v) for k, v in over.items()})
    return env


def post_check(expect_version: str, timeout: float = 0.0) -> tuple:
    """**更新后自检**：新版本能不能真的起来？返回 (ok, [(步骤, 结果, 细节)...])。

    做法：隔离实例（空闲端口 + 临时数据目录）起一次新代码，等 `/api/status` 回来，
    并核对 `/api/version` 的 `current` 等于目标版本。**全程不碰用户正在跑的实例。**

    ⚠️ 超时**可配**（`AC_POSTCHECK_TIMEOUT`，默认 45s）：在慢盘（网络盘 / `/mnt/d` 这种 9p 挂载）
    上启动会明显变慢，写死 30s 会把**好版本**误判成坏版本（Linux 侧真发生过）。
    """
    import socket
    import tempfile

    if timeout <= 0:
        try:
            timeout = float(os.environ.get("AC_POSTCHECK_TIMEOUT") or 45)
        except ValueError:
            timeout = 45.0

    steps: list = []

    def mark(name: str, ok: bool, detail: str = "") -> bool:
        steps.append((name, "OK" if ok else "失败", detail))
        return ok

    # ① 编译面：新代码至少得能编过
    rc, out = subprocess.run([sys.executable, "-m", "compileall", "-q",
                              str(ROOT / "agent_community"), str(ROOT / "tools")],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=180).returncode, ""
    if not mark("compileall", rc == 0, ""):
        return False, steps

    # ② 进程面：隔离实例真起一次
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    finally:
        s.close()
    # ★2026-10-19 真事故（WSL 侧）：ff 到新版本后，子进程报的**还是旧版本号** —— 因为
    #   `/mnt/d` 这类 9p 挂载的 **mtime 粒度是秒级**，`__pycache__` 里的旧 .pyc 判定"没过期"被复用。
    #   ⇒ 自检前**清掉 __pycache__**，并让子进程用 `-B`（不写字节码）。
    import shutil as _sh
    for cache in list(ROOT.rglob("__pycache__")):
        _sh.rmtree(cache, ignore_errors=True)

    data = Path(tempfile.mkdtemp(prefix="ac_postcheck_"))
    env = _clean_env(AC_PORT=port, AC_DATA_DIR=data)       # ★白名单：不继承外层 AC_*（见 _clean_env）
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    child_log = data / "child.log"
    logf = open(child_log, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([sys.executable, "-B", "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env, stdout=logf, stderr=subprocess.STDOUT)
    deadline = time.time() + timeout
    status = None
    try:
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port,
                                            timeout=3) as resp:
                    if resp.status == 200:
                        status = json.loads(resp.read().decode("utf-8", "replace"))
                        break
            except Exception:                                # noqa: BLE001
                pass
            # ★子进程已经退出就别傻等满超时（坏版本通常 1~2s 就炸）——否则判据会被拖到超时
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        if status is None:
            # ★失败要说话：把子进程自己说的话带出来（否则只会说"30s 内没起来"，没法查）
            try:
                logf.flush()
                tail = child_log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
            except OSError:
                tail = []
            mark("实例能起来", False,
                 "%.0fs 内 /api/status 未返回 200；子进程输出：" % timeout + " ⏎ ".join(tail)[-400:])
            return False, steps
        got = str((status or {}).get("version") or "")
        mark("/api/status 版本对得上", _ver_tuple(got) == _ver_tuple(expect_version),
             "报的是 %s，期望 %s" % (got or "?", expect_version))
        return _ver_tuple(got) == _ver_tuple(expect_version), steps
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        try:
            logf.close()
        except Exception:                                    # noqa: BLE001
            pass
        import shutil
        shutil.rmtree(data, ignore_errors=True)


def _git(*args) -> tuple:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


# ── 主流程 ─────────────────────────────────────────────────────
def _emit(result: dict, human: list, as_json: bool) -> None:
    """统一出口：`--json` 给机器（插件/前端解析），否则给人看。"""
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
        return
    for line in human:
        print(line)


def cmd_check(as_json: bool) -> int:
    cur = current_version()
    tag, _ref, how = latest_tag()
    newer = (_ver_tuple(tag) > _ver_tuple(cur)) if tag else None
    result = {
        "action": "check",
        "current": cur or None,
        "latest": tag or None,
        "source": how,
        "update_available": newer,
        "error": None if tag else "查不到最新版（网络/限流/未发布）",
        "safe": safety()[0],
        "problems": safety()[1],
    }
    human = ["现在：%s" % (cur or "读不到版本号"),
             "最新：%s（来源：%s）" % (tag or "查不到", how)]
    if not tag:
        human.append("⇒ 查不到最新版：如实说，不做任何猜测（可稍后重试或检查网络）")
        _emit(result, human, as_json)
        return 2
    human.append("⇒ %s" % ("**有新版可更新**：%s → %s" % (cur, tag) if newer else "已是最新（或本地更新）"))
    _emit(result, human, as_json)
    return 0


def cmd_apply(dry_run: bool, restart: bool, as_json: bool, verify: bool = True) -> int:
    cur = current_version()
    ok, problems = safety()
    result = {"action": "apply", "from": cur or None, "to": None, "safety_ok": ok,
              "post_check": None,
              "problems": problems, "applied": False, "rolled_back": False,
              "backup_head": None, "restarted": False, "dry_run": dry_run, "steps": []}
    human: list = []

    def say(line: str) -> None:
        human.append(line)
        if not as_json:
            print(line)

    if not ok:
        say("⛔ 不能更新：")
        for p in problems:
            say("   · %s" % p)
        result["error"] = "safety"
        _emit(result, [], as_json)
        return 1
    tag, tagref, how = latest_tag()
    result["to"] = tag or None
    result["source"] = how
    if not tag:
        say("⛔ 查不到最新版 ⇒ 不更新（不做没有目标的动作）")
        result["error"] = "no-latest"
        _emit(result, [], as_json)
        return 1
    if _ver_tuple(tag) <= _ver_tuple(cur):
        say("已是最新：%s（远端 %s）" % (cur, tag))
        result["error"] = None
        _emit(result, [], as_json)
        return 0

    say("准备：%s → %s（来源：%s）" % (cur, tag, how))
    say("步骤：① 记录备份提交号 ② git fetch --tags ③ 快进到 %s ④ 校验版本 ⑤ %s"
        % (tag, "重启服务" if restart else "（不重启，按提示自行重启）"))
    if dry_run:
        say("（--dry-run：什么都没做）")
        _emit(result, [], as_json)
        return 0

    rc, head = _git("rev-parse", "HEAD")
    if rc != 0:
        say("⛔ 拿不到当前提交号 ⇒ 不更新：%s" % head[:160])
        result["error"] = "no-head"
        _emit(result, [], as_json)
        return 1
    result["backup_head"] = head
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"backup_head": head, "from": cur, "to": tag,
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    say("① 备份点已记录：%s（写在 %s）" % (head[:10], STATE))
    result["steps"].append("backup")

    rc, out = _git("fetch", "--tags", "--force")
    say("② git fetch --tags：%s" % ("OK" if rc == 0 else "失败 " + out[:160]))
    if rc != 0:
        result["error"] = "fetch"
        _emit(result, [], as_json)
        return 1
    result["steps"].append("fetch")

    rc, out = _git("merge", "--ff-only", tagref or tag)
    if rc != 0:
        say("③ 快进失败（本地有分叉？）：%s" % out[:200])
        say("   ⇒ 已保持原状；如需强更可手工 `git reset --hard %s`（会丢本地提交，先想清楚）" % tag)
        result["error"] = "ff-only-failed"
        _emit(result, [], as_json)
        return 1
    say("③ 已快进到 %s" % tag)
    result["steps"].append("ff")

    new_ver = current_version()
    if _ver_tuple(new_ver) != _ver_tuple(tag):
        say("④ 校验失败：更新后版本是 %s，不是 %s ⇒ **自动回滚**" % (new_ver, tag))
        rc, out = _git("reset", "--hard", head)
        say("   回滚：%s %s" % ("OK" if rc == 0 else "失败", out[:160]))
        result.update({"error": "verify-failed", "rolled_back": rc == 0,
                       "new_version": new_ver})
        _emit(result, [], as_json)
        return 1
    say("④ 校验通过：现在版本 = %s" % new_ver)
    result.update({"applied": True, "new_version": new_ver, "error": None})
    result["steps"].append("verify")

    if verify:
        say("④b 自检：起一个隔离实例跑新代码（最多 30s）——**版本对得上不代表能用**")
        try:
            pc_ok, pc_steps = post_check(tag)
        except Exception as e:                               # noqa: BLE001
            pc_ok, pc_steps = False, [("自检异常", "失败", "%s: %s" % (type(e).__name__, e))]
        result["post_check"] = [{"step": a, "result": b, "detail": c} for a, b, c in pc_steps]
        for a, b, c in pc_steps:
            say("   · %-22s %s %s" % (a, b, c))
        if not pc_ok:
            say("④c 自检不过 ⇒ **新版本不可用，自动回滚**")
            rc, out = _git("reset", "--hard", head)
            say("   回滚：%s %s" % ("OK" if rc == 0 else "失败", out[:160]))
            result.update({"error": "post-check-failed", "rolled_back": rc == 0,
                           "applied": False})
            _emit(result, [], as_json)
            return 1
        say("④c 自检通过：新版本真能起来")
        result["steps"].append("post-check")
    else:
        say("④b 跳过自检（--no-verify）—— 你自己承担「新版本可能起不来」的风险")

    if restart:
        say("⑤ 重启服务：杀掉监听进程，交给看门狗拉回来")
        try:
            sys.path.insert(0, str(ROOT))
            from agent_community.platform import procprobe
            from agent_community import platform_address
            port = int(re.search(r":(\d+)", platform_address.base_url()).group(1))
            pids = procprobe.listening_pids(port) or []
            for pid in pids:
                if pid != os.getpid():
                    procprobe.kill_pid(pid)
            say("   已停 %d 个进程；等看门狗拉起（约 30s 内）" % len(pids))
            say("   没装看门狗的话：python tools/watchdog.py --daemon  或  python -m agent_community.cli start")
            result["restarted"] = bool(pids)
        except Exception as e:                               # noqa: BLE001
            say("   重启失败（%s: %s）—— 请手工重启服务" % (type(e).__name__, e))
            result["restart_error"] = "%s: %s" % (type(e).__name__, e)
    else:
        say("⑤ 没重启：**现在跑的还是旧代码**，请手工重启（python -m agent_community.cli start）")
        result["restart_required"] = True
    _emit(result, [], as_json)
    return 0


def cmd_rollback(as_json: bool) -> int:
    result = {"action": "rollback", "ok": False, "target": None, "error": None}
    human: list = []
    if not STATE.exists():
        human.append("没有更新记录（%s 不存在）⇒ 无可回滚" % STATE)
        result["error"] = "no-state"
        _emit(result, human, as_json)
        return 1
    st = json.loads(STATE.read_text(encoding="utf-8"))
    head = st.get("backup_head") or ""
    result["target"] = head or None
    if not head:
        human.append("记录里没有备份提交号 ⇒ 无法回滚")
        result["error"] = "no-backup-head"
        _emit(result, human, as_json)
        return 1
    rc, out = _git("reset", "--hard", head)
    human.append("回滚到 %s：%s %s" % (head[:10], "OK" if rc == 0 else "失败", out[:160]))
    result.update({"ok": rc == 0, "error": None if rc == 0 else out[:200]})
    _emit(result, human, as_json)
    return 0 if rc == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="一键更新（命令行后端；给插件/前端用 --json）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--no-verify", action="store_true", dest="no_verify",
                    help="跳过更新后的自检（默认会起一个隔离实例验新版本能不能用）")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="机器可读输出（插件/前端解析用；退出码语义不变）")
    a = ap.parse_args()
    if a.check:
        return cmd_check(a.as_json)
    if a.apply:
        return cmd_apply(a.dry_run, a.restart, a.as_json, verify=not a.no_verify)
    return cmd_rollback(a.as_json)


if __name__ == "__main__":
    raise SystemExit(main())
