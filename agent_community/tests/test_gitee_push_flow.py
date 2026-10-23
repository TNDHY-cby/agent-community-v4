# -*- coding: utf-8 -*-
"""守卫：**推前先问远端**这条纪律的端到端行为（用本地裸库当"远端"，绝不碰真 Gitee）。

场景（都是真实发生过/极易发生的）：
- 有人（比如前端）**直接往远端 master 推过东西** ⇒ 本地落后 ⇒ 这时推上去
  要么 non-fast-forward 失败，要么（若有人手滑 force）抹掉对方工作。
  ⇒ 工具必须**在推之前**判断出来，给"远端多了哪些提交 + 三条处置办法"，并且**一个字节都不推**。

判据：
1. 远端领先（本地落后）⇒ 退出码 **2**（＝没推），输出里点名远端多出来的提交，且**远端 sha 不变**；
2. 远端落后（本地领先）⇒ 正常推送、退出码 0、远端 sha == 本地 HEAD；
3. 远端与本地一致 ⇒ 推送（快进无需改动）后校验通过、退出码 0。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
TOOL = _ROOT / "tools" / "gitee_push.py"


def _git(cwd: Path, *args) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, "%s 失败：%s%s" % (args, r.stdout, r.stderr)
    return (r.stdout or "").strip()


def _mk_commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit %s" % name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def world(tmp_path):
    """本地裸库（当"远端"）+ 一个工作仓库（当"发布副本"）。"""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/master")

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-q", "-b", "master")
    _mk_commit(work, "a.txt", "1")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "-u", "origin", "master")
    token = tmp_path / "tok"
    token.write_text("dummy-token", encoding="utf-8")
    return {"remote": remote, "work": work, "token": token}


def _run(world, *extra):
    env = {**os.environ, "AC_GITEE_URL": str(world["remote"]), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(TOOL), "--repo", str(world["work"]),
                           "--token-file", str(world["token"]), *extra],
                          cwd=str(_ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300, env=env)


def _remote_head(world) -> str:
    return _git(world["remote"], "rev-parse", "master")


def test_remote_ahead_is_refused_without_pushing(world):
    """★核心：远端有本地没有的提交 ⇒ 退出码 2、点名那些提交、远端 sha 不变。"""
    work, remote = world["work"], world["remote"]

    # 模拟"别人直接往远端推了一笔"：从远端 clone 一份、提交、推回去
    other = work.parent / "other"
    _git(work.parent, "clone", "-q", str(remote), str(other))
    _git(other, "config", "user.email", "other@example.com")
    _git(other, "config", "user.name", "other")
    pushed = _mk_commit(other, "b.txt", "someone-else")
    _git(other, "push", "-q", "origin", "master")
    assert _remote_head(world) == pushed

    # 本地（"发布副本"）还在旧提交上
    r = _run(world)
    assert r.returncode == 2, "远端领先时必须**不推**并报「没推」（退出码 2）：%s%s" % (r.stdout, r.stderr)
    out = r.stdout + r.stderr
    assert "远端有你没有的提交" in out, "要说清是「远端领先」，别只甩一句 non-fast-forward：%s" % out[-600:]
    assert "commit b.txt" in out, "要点名远端多出来的是哪几笔：%s" % out[-600:]
    assert "不要 force push" in out or "不要force" in out, out[-600:]
    assert _remote_head(world) == pushed, "被拒的时候**一个字节都不该推上去**"


def test_remote_behind_pushes_and_verifies(world):
    """本地领先 ⇒ 正常快进推，且判据是"问远端"（远端 sha == 本地 HEAD）。"""
    work = world["work"]
    local = _mk_commit(work, "c.txt", "local-new")

    r = _run(world)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _remote_head(world) == local
    assert "OK：远端与本地一致" in (r.stdout + r.stderr)


def test_same_commit_is_idempotent(world):
    """已经一致时再推一次不报错（幂等）；远端仍是本地 HEAD。"""
    local = _root_head = _git(world["work"], "rev-parse", "HEAD")
    r = _run(world)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _remote_head(world) == local
