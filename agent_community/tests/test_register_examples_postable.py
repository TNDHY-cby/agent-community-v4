# -*- coding: utf-8 -*-
"""给 V54 话术补一条**更硬**的守卫：示例 JSON **照原样发**必须能注册（不再做替换）。

起因（2026-10-18 核心矩阵体检抓到）：话术里 acp/http 的 `acp_command` 原来是
`<你自测出来的那条…命令>` —— 而 `acp_command` 校验**禁 shell 元字符**（含 `<` `>`）
⇒ agent 把示例原样发出去**直接 400**，而话术还写着"照这份做"。
原守卫之所以绿，是因为它在发之前**替换了占位符**（把真缺陷盖住了）。

现在的判据：**除 file_poll 的 `wakeup_dir`（必须绝对路径，天生没有通用值）以外**，
六种协议的示例 JSON **原样 POST 必须 200**。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """独立数据目录 + 独立端口上的 TestClient（不起真服务）。

    ⚠️ **必须把客户端地址设成 127.0.0.1**：注册端点对"非本地请求"要求 Token（403 拒绝），
    而 TestClient 默认 host 是 `testserver` ⇒ 会被判成"非本地访问"。这是产品既有安全口径，不是缺陷。
    """
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_ENV", "development")
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app
    yield TestClient(app, client=("127.0.0.1", 54321))


def _examples():
    from agent_community.platform import register_prompt as rp
    return rp


def test_examples_contain_no_shell_metachars_when_posted_verbatim():
    """示例里除 `wakeup_dir` 外**不许出现会被校验拒掉的占位符**（`<` `>` 就是典型）。"""
    rp = _examples()
    for w in rp.SUPPORTED:
        card = rp._minimal_json(w, "mx-" + w.replace("_", "-"))
        for k, v in card.items():
            if k == "wakeup_dir":
                continue                       # 唯一允许保留占位符的字段（必须绝对路径）
            assert "<" not in str(v) and ">" not in str(v), \
                "%s 的示例字段 %s 含尖括号占位符，原样提交会被 400：%r" % (w, k, v)


@pytest.mark.parametrize("exclude", ["file_poll"])
def test_verbatim_examples_register_successfully(exclude):
    """**把示例原样打进去**必须 200（这是"话术真能用"的硬判据）。"""
    rp = _examples()
    from fastapi.testclient import TestClient
    import os
    d = tempfile.mkdtemp(prefix="ac_verbatim_")
    os.environ["AC_DATA_DIR"] = d
    from agent_community.platform.server import app
    c = TestClient(app, client=("127.0.0.1", 54321))
    fails = []
    for w in rp.SUPPORTED:
        if w == exclude:
            continue
        card = rp._minimal_json(w, "vb-" + w.replace("_", "-"))
        r = c.post("/api/harness/register", json=card)
        if r.status_code != 200:
            fails.append("%s → HTTP %s %s" % (w, r.status_code, r.text[:160]))
    assert not fails, "示例原样提交竟被拒（话术不可照做）：\n" + "\n".join(fails)


def test_file_poll_example_needs_only_absolute_dir():
    """`file_poll` 的示例只该差"一条绝对路径"这一件事（换了它就能注册）。"""
    rp = _examples()
    card = rp._minimal_json("file_poll", "vb-fp")
    d = tempfile.mkdtemp(prefix="ac_fp_")
    card["wakeup_dir"] = d
    from fastapi.testclient import TestClient
    os.environ["AC_DATA_DIR"] = tempfile.mkdtemp(prefix="ac_verbatim_fp_")
    from agent_community.platform.server import app
    c = TestClient(app, client=("127.0.0.1", 54321))
    r = c.post("/api/harness/register", json=card)
    assert r.status_code == 200, r.text[:200]
