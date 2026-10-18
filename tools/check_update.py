# -*- coding: utf-8 -*-
"""判据：**一键更新真的能更新、能回滚、且该拒绝时会拒绝**（端到端，造两个真仓库）。

```bash
python tools/check_update.py      # 约 20 秒
```

## 为什么要造仓库

"更新"这动作只有**版本真的不一样**时才存在。所以本工具现场造一对真 git 仓库：

```
upstream/    ← 模拟"远端有新版"：版本 9.9.9，打 tag v9.9.9
install/     ← 模拟"用户装的那份"：是 upstream 的 clone，版本 9.9.8（旧）
```

然后对 `install/` 跑 `tools/update.py`，验四格：

| 格 | 判据 |
|---|---|
| ① 拒绝脏工作树 | 有未提交改动 ⇒ **退出码 1** 且说明"先提交或还原" |
| ② 拒绝数据落在安装目录内 | `AC_DATA_DIR` 在 install 里 ⇒ **退出码 1** 且说明"会把数据一起删掉" |
| ③ 能真的更新 | 数据目录分离后 `--apply` ⇒ install 的版本变成 **9.9.9**，且写了备份点 |
| ④ 能回滚 | `--rollback` ⇒ 版本回到 **9.9.8** |

⚠️ 全程只在临时目录里动，**不碰真实安装**。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
TOOL_SRC = ROOT / "tools" / "update.py"
PKG_SRC = ROOT / "agent_community"


def run(cmd, cwd, env=None, timeout=180):
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def make_tree(base: Path, version: str) -> None:
    """造一个"最小可更新"的安装树：agent_community 包（带版本号）+ tools/update.py。"""
    (base / "agent_community").mkdir(parents=True, exist_ok=True)
    (base / "tools").mkdir(parents=True, exist_ok=True)
    (base / "agent_community" / "__init__.py").write_text(
        '__version__ = "%s"\n' % version, encoding="utf-8")
    (base / "agent_community" / "platform_address.py").write_text(
        'def base_url(explicit_port=None):\n    return "http://127.0.0.1:1"\n', encoding="utf-8")
    shutil.copy(TOOL_SRC, base / "tools" / "update.py")
    # 让 `platform.env_spec.data_dir()` 能工作（update.py 会尝试导入）
    plat = base / "agent_community" / "platform"
    plat.mkdir(parents=True, exist_ok=True)
    (plat / "__init__.py").write_text("", encoding="utf-8")
    (plat / "env_spec.py").write_text(
        "import os\nfrom pathlib import Path\n\n\n"
        "def data_dir():\n"
        "    return Path(os.environ.get('AC_DATA_DIR') or (Path(__file__).resolve().parents[1] / 'data'))\n",
        encoding="utf-8")
    (base / "agent_community" / "platform" / "routers").mkdir(parents=True, exist_ok=True)
    (base / "agent_community" / "platform" / "routers" / "__init__.py").write_text("", encoding="utf-8")
    (base / "agent_community" / "platform" / "routers" / "version.py").write_text(
        'CHANNEL = {"releases_api": "", "repo": ""}\n', encoding="utf-8")


def git(cwd, *args):
    """返回 (rc, 输出) —— 调用处都按二元组解包。

    ⚠️ 临时仓库里没有 git 身份 ⇒ **显式带上**（`-c user.email/user.name`），
    否则在别人机器上跑会因为"Committer identity unknown"直接失败（本机就踩过）。
    """
    r = run(["git", "-c", "user.email=check@example.com",
             "-c", "user.name=update-judge", *args], cwd)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="ac_update_judge_"))
    upstream = work / "upstream"
    install = work / "install"
    print("=" * 84)
    print("一键更新端到端判据（临时目录 %s）" % work)
    print("=" * 84)
    fails = 0

    def cell(name, ok, detail=""):
        nonlocal fails
        print("   %-30s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
        fails += 0 if ok else 1

    try:
        # ── 造 upstream：两个**真实的先后提交**（9.9.8 → 9.9.9，后者打 tag）──
        #    ⚠️ 一开始我把 install 造成"在旧版上多一个本地提交"，结果 `merge --ff-only` 必然失败 ——
        #    那不是工具的问题，是**我的夹具不真实**（真实用户的安装是干净 clone，没有本地提交）。
        for a in (["init", "-q"],):
            upstream.mkdir(parents=True, exist_ok=True)     # git 需要 cwd 真实存在
            git(upstream, *a)
        for ver, msg in (("9.9.8", "release 9.9.8"), ("9.9.9", "release 9.9.9")):
            make_tree(upstream, ver)
            for a in (["add", "-A"], ["commit", "-qm", msg]):
                rc, out = git(upstream, *a)
                if rc != 0 and "nothing to commit" not in out:
                    print("★造 upstream 失败：%s %s" % (a, out[:200]))
                    return 2
        rc, out = git(upstream, "tag", "-a", "v9.9.9", "-m", "9.9.9")
        if rc != 0:
            print("★打 tag 失败：%s" % out[:200])
            return 2

        # ── 造 install：干净 clone，然后回到**上一个**提交（＝用户装的旧版）──
        rc, out = git(work, "clone", "-q", str(upstream), str(install))
        if rc != 0:
            print("★clone 失败：%s" % out[:200])
            return 2
        rc, out = git(install, "reset", "--hard", "HEAD~1")
        ver_old = re.search(r'__version__ = "([\d.]+)"',
                            (install / "agent_community" / "__init__.py").read_text(encoding="utf-8"))
        if not ver_old or ver_old.group(1) != "9.9.8":
            print("★夹具不对：install 起始版本应是 9.9.8，实际 %s" % (ver_old.group(1) if ver_old else "?"))
            return 2
        # 让 install 的远端指向 upstream（模拟"远端有新版"）
        git(install, "remote", "set-url", "origin", str(upstream))

        sep_data = work / "data"
        sep_data.mkdir()
        env_sep = {**os.environ, "AC_DATA_DIR": str(sep_data), "PYTHONPATH": str(install)}
        env_nested = {**os.environ, "AC_DATA_DIR": str(install / "agent_community" / "data"),
                      "PYTHONPATH": str(install)}
        py = sys.executable

        # ① 脏工作树必须拒绝
        (install / "local_edit.txt").write_text("用户自己的改动\n", encoding="utf-8")
        r = run([py, "tools/update.py", "--apply"], install, env_sep)
        cell("① 拒绝脏工作树", r.returncode == 1 and "未提交改动" in (r.stdout + r.stderr),
             (r.stdout + r.stderr).strip().splitlines()[1][:80] if r.returncode == 1 else "竟然没拒")
        (install / "local_edit.txt").unlink()

        # ② 数据目录在安装目录内必须拒绝
        r = run([py, "tools/update.py", "--apply"], install, env_nested)
        cell("② 拒绝数据在安装目录内", r.returncode == 1 and "之内" in (r.stdout + r.stderr),
             (r.stdout + r.stderr).strip().splitlines()[1][:90])

        # ③ 真更新（数据分离后）
        r = run([py, "tools/update.py", "--apply"], install, env_sep)
        out3 = r.stdout + r.stderr
        ver_after = re.search(r'__version__ = "([\d.]+)"',
                              (install / "agent_community" / "__init__.py").read_text(encoding="utf-8"))
        cell("③ 真的更新到 9.9.9", r.returncode == 0 and ver_after and ver_after.group(1) == "9.9.9",
             "rc=%s 版本=%s ｜ %s" % (r.returncode, ver_after.group(1) if ver_after else "?",
                                    " / ".join([ln for ln in out3.splitlines() if ln.strip()][-3:])[:220]))
        state = (install / "agent_community" / "data" / "update_state.json")
        cell("③b 写了备份点（可回滚）", state.exists(), str(state.name))

        # ④ 回滚
        r = run([py, "tools/update.py", "--rollback"], install, env_sep)
        ver_back = re.search(r'__version__ = "([\d.]+)"',
                             (install / "agent_community" / "__init__.py").read_text(encoding="utf-8"))
        cell("④ 回滚回 9.9.8", r.returncode == 0 and ver_back and ver_back.group(1) == "9.9.8",
             "rc=%s 版本=%s" % (r.returncode, ver_back.group(1) if ver_back else "?"))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("-" * 84)
    print("结论：%s" % ("**一键更新可用（能更新 / 能回滚 / 该拒绝时拒绝）**" if not fails
                      else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
