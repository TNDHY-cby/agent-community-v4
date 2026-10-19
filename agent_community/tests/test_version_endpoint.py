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


def test_data_dir_risk_flags_nested_and_equal_dirs(tmp_path):
    """★更新安全的核心判据（2026-10-18 本机实测就踩到）：数据目录在安装目录**之内**时必须警告。

    ⚠️ 我第一版只比了"相等"，而本机默认数据目录是 `<安装>/agent_community/data`（**包含**而非相等）
    ⇒ 插件替换安装目录会静默删掉用户数据，端点的 `notes` 却还是空的。
    """
    from agent_community.platform.routers.version import data_dir_risk
    install = tmp_path / "install"
    (install / "agent_community" / "data").mkdir(parents=True)
    outside = tmp_path / "acdata"
    outside.mkdir()

    assert data_dir_risk(str(install), str(install)) != "", "相等必须警告"
    nested = data_dir_risk(str(install), str(install / "agent_community" / "data"))
    assert nested and "之内" in nested, "数据目录在安装目录之内必须警告：%r" % nested
    assert data_dir_risk(str(install), str(outside)) == "", "数据目录在安装目录之外＝安全形态，不该警告"
    assert data_dir_risk("", str(outside)) != "", "读不到安装目录时必须如实说不确定"


def test_version_latest_endpoint_is_readonly_and_honest(client, monkeypatch):
    """"查最新版"端点：**只读**、有缓存、失败如实（不联网的机器上也不能编一个版本号）。"""
    import agent_community.platform.routers.version as v

    # ① 成功路径：伪造一次 Releases API 应答
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"tag_name": "v9.9.9"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
    v._LATEST_CACHE.update({"at": 0.0, "value": None})
    d = client.get("/api/version/latest").json()
    assert d["latest"] == "9.9.9", d
    assert d["update_available"] is True
    assert d["error"] is None
    assert d["howto"]["apply"].endswith("--apply")

    # ② 失败路径：如实给 error，latest 为 null（不许编）
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    v._LATEST_CACHE.update({"at": 0.0, "value": None})
    d2 = client.get("/api/version/latest").json()
    assert d2["latest"] is None and d2["error"], d2
    assert d2["update_available"] is None


def test_version_latest_endpoint_does_not_write_anything(client, monkeypatch):
    """**只读**：绝不能因为点了"检查更新"就动文件/下载/重启（这条端点只在内存里算）。"""
    src = (_ROOT / "agent_community" / "platform" / "routers" / "version.py").read_text(encoding="utf-8")
    for forbidden in ("Popen", "subprocess", "git ", "shutil", "unlink", "rmtree"):
        assert forbidden not in src, "查版本端点里出现了会动系统的动作：%r" % forbidden
    safety = client.get("/api/version").json()["update_safety"]
    assert safety["replace_install_dir_only"] is True
    assert safety["keep_data_dir"] is True, "更新不许动数据目录"
    assert safety["restart_required"] is True
    # 2026-10-19（老板「最优解就行」拍板，V58）：受控重启端点已实现，契约必须指向它，
    # 且必须把「没有看门狗会被拒」这条安全前提写在契约里（用户照着做不会把平台弄死）。
    assert safety["restart_endpoint"] == "/api/admin/restart", \
        "受控重启端点已实现（V58），契约必须指向它：%r" % safety["restart_endpoint"]
    assert "看门狗" in safety["restart_howto"] and "confirm" in safety["restart_howto"], \
        "restart_howto 必须说清看门狗前提与 confirm 字段：%r" % safety["restart_howto"]


def test_contract_doc_exists_and_is_pending_decision():
    """契约文档必须在发布面；受控重启已拍板（V58），文档要指向拍板稿与端点，别再写"待拍板"当现状。"""
    doc = _ROOT / "design-docs" / "V57_一键更新插件契约.md"
    assert doc.exists(), "缺一键更新插件契约文档（宋要照它做）"
    text = doc.read_text(encoding="utf-8", errors="replace")
    assert "已拍板" in text, "受控重启已拍板（2026-10-19），契约必须写明"
    assert "V58_受控重启端点设计.md" in text, "契约要指向拍板稿 V58"
    assert "/api/version" in text and "install_dir" in text and "data_dir" in text
