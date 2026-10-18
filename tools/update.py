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


def _git(*args) -> tuple:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


# ── 主流程 ─────────────────────────────────────────────────────
def cmd_check() -> int:
    cur = current_version()
    tag, _ref, how = latest_tag()
    print("现在：%s" % (cur or "读不到版本号"))
    print("最新：%s（来源：%s）" % (tag or "查不到", how))
    if not tag:
        print("⇒ 查不到最新版：如实说，不做任何猜测（可稍后重试或检查网络）")
        return 2
    newer = _ver_tuple(tag) > _ver_tuple(cur)
    print("⇒ %s" % ("**有新版可更新**：%s → %s" % (cur, tag) if newer else "已是最新（或本地更新）"))
    return 0 if not newer else 0


def cmd_apply(dry_run: bool, restart: bool) -> int:
    cur = current_version()
    ok, problems = safety()
    if not ok:
        print("⛔ 不能更新：")
        for p in problems:
            print("   · %s" % p)
        return 1
    tag, tagref, how = latest_tag()
    if not tag:
        print("⛔ 查不到最新版 ⇒ 不更新（不做没有目标的动作）")
        return 1
    if _ver_tuple(tag) <= _ver_tuple(cur):
        print("已是最新：%s（远端 %s）" % (cur, tag))
        return 0

    print("准备：%s → %s（来源：%s）" % (cur, tag, how))
    print("步骤：① 记录备份提交号 ② git fetch --tags ③ 快进到 %s ④ 校验版本 ⑤ %s"
          % (tag, "重启服务" if restart else "（不重启，按提示自行重启）"))
    if dry_run:
        print("（--dry-run：什么都没做）")
        return 0

    rc, head = _git("rev-parse", "HEAD")
    if rc != 0:
        print("⛔ 拿不到当前提交号 ⇒ 不更新：%s" % head[:160])
        return 1
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"backup_head": head, "from": cur, "to": tag,
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("① 备份点已记录：%s（写在 %s）" % (head[:10], STATE))

    rc, out = _git("fetch", "--tags", "--force")
    print("② git fetch --tags：%s" % ("OK" if rc == 0 else "失败 " + out[:160]))
    if rc != 0:
        return 1

    rc, out = _git("merge", "--ff-only", tagref or tag)
    if rc != 0:
        print("③ 快进失败（本地有分叉？）：%s" % out[:200])
        print("   ⇒ 已保持原状；如需强更可手工 `git reset --hard %s`（会丢本地提交，先想清楚）" % tag)
        return 1
    print("③ 已快进到 %s" % tag)

    new_ver = current_version()
    if _ver_tuple(new_ver) != _ver_tuple(tag):
        print("④ 校验失败：更新后版本是 %s，不是 %s ⇒ **自动回滚**" % (new_ver, tag))
        rc, out = _git("reset", "--hard", head)
        print("   回滚：%s %s" % ("OK" if rc == 0 else "失败", out[:160]))
        return 1
    print("④ 校验通过：现在版本 = %s" % new_ver)

    if restart:
        print("⑤ 重启服务：杀掉监听进程，交给看门狗拉回来")
        try:
            sys.path.insert(0, str(ROOT))
            from agent_community.platform import procprobe
            from agent_community import platform_address
            port = int(re.search(r":(\d+)", platform_address.base_url()).group(1))
            pids = procprobe.listening_pids(port) or []
            for pid in pids:
                if pid != os.getpid():
                    procprobe.kill_pid(pid)
            print("   已停 %d 个进程；等看门狗拉起（约 30s 内）" % len(pids))
            print("   没装看门狗的话：python tools/watchdog.py --daemon  或  python -m agent_community.cli start")
        except Exception as e:                               # noqa: BLE001
            print("   重启失败（%s: %s）—— 请手工重启服务" % (type(e).__name__, e))
    else:
        print("⑤ 没重启：**现在跑的还是旧代码**，请手工重启（python -m agent_community.cli start）")
    return 0


def cmd_rollback() -> int:
    if not STATE.exists():
        print("没有更新记录（%s 不存在）⇒ 无可回滚" % STATE)
        return 1
    st = json.loads(STATE.read_text(encoding="utf-8"))
    head = st.get("backup_head") or ""
    if not head:
        print("记录里没有备份提交号 ⇒ 无法回滚")
        return 1
    rc, out = _git("reset", "--hard", head)
    print("回滚到 %s：%s %s" % (head[:10], "OK" if rc == 0 else "失败", out[:160]))
    return 0 if rc == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="一键更新（命令行后端）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restart", action="store_true")
    a = ap.parse_args()
    if a.check:
        return cmd_check()
    if a.apply:
        return cmd_apply(a.dry_run, a.restart)
    return cmd_rollback()


if __name__ == "__main__":
    raise SystemExit(main())
