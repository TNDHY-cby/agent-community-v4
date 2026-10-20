# -*- coding: utf-8 -*-
"""守卫：**对端回执里的产物清单**（V64）。

判据（按真行为）：
  ① 对端回执带 `artifacts` ⇒ 本侧记录里存**摘要**（名字/大小/归属），**不带文件内容**；
  ② 归属标注为 `remote`（文件在对端机器上），并带上"不自动下载"的说明 —— **不假装能取文件**；
  ③ 对端回执没有产物 ⇒ 清单为空且 `artifacts_note` 为空（不编造交付物）；
  ④ 平台只读端点 `GET /api/workshop/{ws}/outputs` 只给清单（`note` 里写明不给内容）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from agent_community.platform import peer_tasks
    from agent_community import config as cfgmod
    import importlib
    importlib.reload(cfgmod)
    return peer_tasks


def test_artifacts_are_summarized_without_content(env):
    tasks = env
    rec = tasks.create("co-b", "公司 B", "交一份文档", "remote-9")
    info = {
        "id": "remote-9", "status": "completed",
        "artifacts": [{"name": "m0/报告.md", "parts": [{"type": "text", "text": "很长很长的正文"}],
                       "metadata": {"bytes": 2048, "member_id": "m0", "role": "码农",
                                    "owner": "remote", "note": "在对端机器上"}}],
    }
    updated = tasks.record_check(rec["pt_id"], True, info=info)
    view = tasks.public_view(updated)
    assert view["status"] == "done"
    arts = view["artifacts"]
    assert len(arts) == 1
    a = arts[0]
    assert a["name"] == "m0/报告.md" and a["bytes"] == 2048
    assert a["owner"] == "remote", "必须标明文件在对端机器上"
    assert "不自动下载" in (view["artifacts_note"] or "") or "不自动下载" in a["note"]
    # 绝不许把正文塞进来（跨公司不传文件内容）
    assert "很长很长的正文" not in str(view.get("artifacts")), "产物内容不该被带过来"


def test_no_artifacts_is_honest(env):
    tasks = env
    rec = tasks.create("co-c", "公司 C", "还没交东西", "remote-10")
    updated = tasks.record_check(rec["pt_id"], True, info={"status": "working", "artifacts": []})
    view = tasks.public_view(updated)
    assert view["artifacts"] == []
    assert view["artifacts_note"] == "", "没产物就不要给说明（别暗示有交付）"


def test_outputs_endpoint_is_readonly_list(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from fastapi.testclient import TestClient
    from agent_community.platform.server import app
    c = TestClient(app, client=("127.0.0.1", 54321))
    r = c.post("/api/workshop", json={"name": "产物清单走查", "hall_content": "x",
                                      "members": [{"role": "码农", "harness_ids": []}]})
    assert r.status_code == 200, r.text[:200]
    ws = r.json()["workshop_id"]
    body = c.get("/api/workshop/%s/outputs" % ws).json()
    assert body["success"] is True and body["files"] == [] and body["total_files"] == 0
    assert "不自动下载" in body["note"], "要说明只给清单、不给内容"
    # 不存在的工作间：404（不假装空清单）
    assert c.get("/api/workshop/ws-nope/outputs").status_code == 404
