# -*- coding: utf-8 -*-
"""守卫：**发布推送前的"远端领先"判断**（2026-10-23 补，起因是"有人直接推 master"这类事故）。

发布是 `dev → oss → Gitee` **单向**推。若这期间有人直接往 Gitee `master` 推过东西，本地 oss 就落后：
硬推会抹掉对方工作，不硬推则 `git push` 只回一句 `non-fast-forward`，发布脚本只会说"推送失败"。

⇒ 现在推之前先 `ls-remote` + `merge-base --is-ancestor` 判断。这里把**判断逻辑**钉住（不碰网络）：

| # | 判据 | 为什么 |
|---|---|---|
| ① | 远端是 HEAD 祖先 ⇒ 可推（True） | 正常情况（远端落后或相同） |
| ② | 远端与本地分叉（远端有本地没有的提交）⇒ 不可推（False） | 这正是"别人推过 master" |
| ③ | 令牌文件位置可配置，**不写死本机路径** | 可移植性；换机器只改环境变量 |
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

sys.path.insert(0, str(_ROOT / "tools"))

# ⚠️ 不能写 `import gitee_push`：`tools/` 不是包，裸模块名会被
#    `test_foundation_base` 的「第三方 import 必须声明」守卫当成外部依赖（实测抓到）。
#    用 spec 从文件加载 —— 它是本仓库脚本，不是第三方。
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location("gitee_push_under_test", _ROOT / "tools" / "gitee_push.py")
gp = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(gp)  # noqa: E402


def _git(repo: Path, *args) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, "%s 失败：%s%s" % (args, r.stdout, r.stderr)
    return (r.stdout or "").strip()


def _repo_with_history(tmp: Path) -> tuple:
    repo = tmp / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "A")
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("2", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "B")
    second = _git(repo, "rev-parse", "HEAD")
    return repo, first, second


def test_remote_behind_is_pushable(tmp_path):
    repo, first, _second = _repo_with_history(tmp_path)
    # 远端在 A（落后于本地 HEAD=B）⇒ 可以快进推
    assert gp._is_ancestor(repo, first) is True


def test_remote_ahead_or_diverged_is_not_pushable(tmp_path):
    repo, _first, second = _repo_with_history(tmp_path)
    # 造一个"远端有、本地没有"的提交（另一个分支），它显然不是 HEAD 的祖先
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "c.txt").write_text("3", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "C-remote")
    remote_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-")

    assert gp._is_ancestor(repo, second) is True, "本地 HEAD 自己应当是祖先"
    assert gp._is_ancestor(repo, remote_commit) is False, \
        "远端有本地没有的提交时，必须判成'不能直接推'（否则就会去 force 抹掉别人）"


def test_token_file_is_configurable_not_hardcoded(tmp_path, monkeypatch):
    """令牌文件位置：环境变量优先；默认在"发布副本同级的 temp/"，**不写死本机绝对路径**。"""
    src = (_ROOT / "tools" / "gitee_push.py").read_text(encoding="utf-8")
    assert "D:\\\\DSH" not in src and "D:/DSH" not in src, "tools/gitee_push.py 里写死了本机路径"

    monkeypatch.setenv("AC_GITEE_TOKEN_FILE", str(tmp_path / "tok"))
    assert gp.token_file(tmp_path / "repo") == tmp_path / "tok"
    monkeypatch.delenv("AC_GITEE_TOKEN_FILE", raising=False)
    assert gp.token_file(tmp_path / "repo") == (tmp_path / "repo").resolve().parent / "temp" / ".giteetoken"
