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


SERVER_TMPL = '''"""最小可用服务：让"更新后自检"能真起一次并回答 /api/status。"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

import agent_community


class H(BaseHTTPRequestHandler):
    def do_GET(self):                                          # noqa: N802
        if self.path != "/api/status":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"status": "running", "version": agent_community.__version__}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(os.environ.get("AC_PORT") or "8000")), H).serve_forever()
'''

BROKEN_SERVER = '''"""故意坏掉的服务：模拟"版本号对、但新版本起不来"的那种发布。"""
raise RuntimeError("这个版本起不来（用于验证更新后自检必须拦住它）")
'''


def make_tree(base: Path, version: str, broken: bool = False) -> None:
    """造一个"最小可更新"的安装树：agent_community 包（带版本号、能起服务）+ tools/update.py。"""
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
    (plat / "routers").mkdir(parents=True, exist_ok=True)
    (plat / "routers" / "__init__.py").write_text("", encoding="utf-8")
    (plat / "routers" / "version.py").write_text(
        'CHANNEL = {"releases_api": "", "repo": ""}\n', encoding="utf-8")
    # ★"更新后自检"要真起服务 ⇒ 夹具必须真的能起来（否则自检会把好版本也判坏，测试就失真）
    (plat / "server.py").write_text(BROKEN_SERVER if broken else SERVER_TMPL, encoding="utf-8")


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
        # 再造一个**坏版本** 9.9.10（版本号对、但服务起不来）——专验"更新后自检必须拦住它"
        make_tree(upstream, "9.9.10", broken=True)
        for a in (["add", "-A"], ["commit", "-qm", "release 9.9.10 (broken)"]):
            rc, out = git(upstream, *a)
            if rc != 0 and "nothing to commit" not in out:
                print("★造坏版本失败：%s %s" % (a, out[:200]))
                return 2
        rc, out = git(upstream, "tag", "-a", "v9.9.10", "-m", "9.9.10")
        if rc != 0:
            print("★打 9.9.10 tag 失败：%s" % out[:200])
            return 2
        # install 从 9.9.8 起（干净 clone 后回退到 v9.9.9 的前一个提交）

        # ── 造 install：干净 clone，然后回到"用户装的那版"（9.9.8）──
        rc, out = git(work, "clone", "-q", str(upstream), str(install))
        if rc != 0:
            print("★clone 失败：%s" % out[:200])
            return 2
        rc, out = git(install, "reset", "--hard", "v9.9.9~1")
        ver_old = re.search(r'__version__ = "([\d.]+)"',
                            (install / "agent_community" / "__init__.py").read_text(encoding="utf-8"))
        if not ver_old or ver_old.group(1) != "9.9.8":
            print("★夹具不对：install 起始版本应是 9.9.8，实际 %s" % (ver_old.group(1) if ver_old else "?"))
            return 2
        # 让 install 的远端指向 upstream（模拟"远端有新版"）
        git(install, "remote", "set-url", "origin", str(upstream))

        sep_data = work / "data"
        sep_data.mkdir()
        # ⚠️ 判据环境放宽自检超时：临时目录可能在慢盘（WSL 的 /mnt/d 是 9p），
        #    写死 30s 会把**好版本**误判成坏版本（Linux 侧真红过一次）。
        env_sep = {**os.environ, "AC_DATA_DIR": str(sep_data), "PYTHONPATH": str(install),
                   "AC_POSTCHECK_TIMEOUT": "90"}
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

        # ③ **坏版本必须被"更新后自检"拦住**（远端最新是 9.9.10，但那个版本起不来）
        r = run([py, "tools/update.py", "--apply", "--json"], install, env_sep)
        out3 = (r.stdout or "") + (r.stderr or "")
        ver_bad = re.search(r'__version__ = "([\d.]+)"',
                            (install / "agent_community" / "__init__.py").read_text(encoding="utf-8"))
        j3 = {}
        try:
            j3 = json.loads([ln for ln in out3.splitlines() if ln.strip().startswith("{")][-1])
        except Exception:                                    # noqa: BLE001
            pass
        cell("③ 坏版本被自检拦住并回滚",
             r.returncode == 1 and ver_bad and ver_bad.group(1) == "9.9.8"
             and j3.get("rolled_back") is True,
             "rc=%s 版本=%s rolled_back=%s" % (r.returncode,
                                              ver_bad.group(1) if ver_bad else "?",
                                              j3.get("rolled_back")))

        # ④ 好版本能真的更新（把坏版本 tag 从远端删掉，模拟"官方撤回了坏版本"）
        git(upstream, "tag", "-d", "v9.9.10")
        r = run([py, "tools/update.py", "--apply", "--json"], install, env_sep)
        out4 = (r.stdout or "") + (r.stderr or "")
        ver_after = re.search(r'__version__ = "([\d.]+)"',
                              (install / "agent_community" / "__init__.py").read_text(encoding="utf-8"))
        j4 = {}
        try:
            j4 = json.loads([ln for ln in out4.splitlines() if ln.strip().startswith("{")][-1])
        except Exception:                                    # noqa: BLE001
            pass
        cell("④ 好版本更新到 9.9.9（含自检通过）",
             r.returncode == 0 and ver_after and ver_after.group(1) == "9.9.9"
             and j4.get("applied") is True,
             "rc=%s 版本=%s error=%s steps=%s ｜ %s" % (
                 r.returncode, ver_after.group(1) if ver_after else "?", j4.get("error"),
                 j4.get("steps"),
                 " / ".join("%s=%s" % (x.get("step"), x.get("detail") or x.get("result"))
                            for x in (j4.get("post_check") or []))[:200]))
        state = (install / "agent_community" / "data" / "update_state.json")
        cell("④b 写了备份点（可回滚）", state.exists(), str(state.name))

        # ⑤ 回滚
        r = run([py, "tools/update.py", "--rollback"], install, env_sep)
        ver_back = re.search(r'__version__ = "([\d.]+)"',
                             (install / "agent_community" / "__init__.py").read_text(encoding="utf-8"))
        cell("⑤ 回滚回 9.9.8", r.returncode == 0 and ver_back and ver_back.group(1) == "9.9.8",
             "rc=%s 版本=%s" % (r.returncode, ver_back.group(1) if ver_back else "?"))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("-" * 84)
    print("结论：%s" % ("**一键更新可用（拒绝坏情形 / 坏版本自检拦住 / 好版本能更新 / 能回滚）**"
                      if not fails else "**有 %d 格失败**" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
