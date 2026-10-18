# -*- coding: utf-8 -*-
"""守卫：`GET /api/version`（一键更新插件的唯一版本真相）与 V57 契约不许漂移。

判据（都能机器判）：
  ① 端点存在且 200，字段齐全；`current` == `agent_community.__version__`；
  ② `latest` / `update_available` 允许为 null（平台不猜远端），**但不许出现编造值**；
  ③ `channel` 指向 Gitee（GitHub 已停更）且给得出 release 压缩包地址形状；
  ④ `update_safety` 必须声明"只替换安装目录、保留数据目录、需要重启"；
  ⑤ 契约文档 `design-docs/V57_一键更新插件契约.md` 在发布面，且写着"待老板拍板"。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app
    yield TestClient(app, client=("127.0.0.1", 54321))


def test_version_endpoint_contract(client):
    import agent_community
    r = client.get("/api/version")
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    for k in ("current", "latest", "update_available", "channel", "install_dir",
              "data_dir", "update_safety", "notes"):
        assert k in body, "缺字段 %s：%s" % (k, sorted(body))
    assert body["current"] == agent_community.__version__, \
        "current 与 __version__ 不一致（版本真相必须只有一处）"
    assert body["latest"] is None and body["update_available"] is None, \
        "平台不联网查远端 ⇒ 这两项必须是 null，不许编造"


def test_version_channel_points_to_gitee(client):
    body = client.get("/api/version").json()
    chan = body["channel"]
    assert "gitee.com" in chan["repo"], "更新渠道必须是 Gitee（GitHub 2026-10-16 已停更）"
    assert "{tag}" in chan["release_tarball_tpl"], "没有 release 压缩包地址模板，插件下不了包"
    assert chan["tags_ref"].startswith("refs/tags/")


def test_version_update_safety_declares_keep_data(client):
    safety = client.get("/api/version").json()["update_safety"]
    assert safety["replace_install_dir_only"] is True
    assert safety["keep_data_dir"] is True, "更新不许动数据目录"
    assert safety["restart_required"] is True
    assert safety["restart_endpoint"] is None, \
        "受控重启端点尚未拍板；若已实现，请同步更新 V57 与这条守卫"


def test_contract_doc_exists_and_is_pending_decision():
    doc = _ROOT / "design-docs" / "V57_一键更新插件契约.md"
    assert doc.exists(), "缺一键更新插件契约文档（宋要照它做）"
    text = doc.read_text(encoding="utf-8", errors="replace")
    assert "待老板拍板" in text, "契约必须标明「待拍板」（设计先行铁律）"
    assert "/api/version" in text and "install_dir" in text and "data_dir" in text
