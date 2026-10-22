# -*- coding: utf-8 -*-
"""V86 凭据体检（老板 2026-10-22 拍板：**只做体检**）。

判据一律"真文件 + 真调用"：哨兵明文塞进真实位置，再断言**响应与账本里都不许出现它**。
"""
from __future__ import annotations

import json
import os

import pytest

from agent_community.platform import credentials as cred
from agent_community.tests.conftest import resolve_handler  # noqa: F401  （保留统一取 handler 的口径）


#: 哨兵明文：一旦它出现在任何输出/账本里，就是泄漏。
#: ⚠️ **故意不用 `sk-` 前缀**：`tests/scan_sensitive.py` 的「疑似真实密钥」规则是 `sk-[A-Za-z0-9]{16,}`，
#:    用了它就得往扫描器白名单里加条目 —— 那是**削弱闸门**，不如让哨兵长得就不像真 key。
SENTINEL = "TEST-SENTINEL-9931-doNotLeak"


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把账本目录、数据目录、仓库根都指到临时目录（绝不碰真实数据）。"""
    data = tmp_path / "data"
    (data / "keys").mkdir(parents=True)
    (data / "bridges" / "示例Harness-A").mkdir(parents=True)
    monkeypatch.setattr(cred, "LEDGER_DIR", data / "keys", raising=True)
    monkeypatch.setattr(cred.env_spec, "data_dir", lambda: data, raising=True)
    monkeypatch.setattr(cred, "_root", lambda: tmp_path, raising=True)
    return {"root": tmp_path, "data": data}


def test_fingerprint_is_stable_and_sensitive():
    a = cred.fingerprint(SENTINEL)
    assert a == cred.fingerprint(SENTINEL), "同一明文必须算出同一指纹"
    assert len(a) == 12 and all(c in "0123456789abcdef" for c in a), "指纹口径＝sha256 前 12 位十六进制"
    assert cred.fingerprint(SENTINEL + "x") != a, "改一个字节指纹必须变"


def test_strength_flags_placeholder_and_short_values():
    assert cred.strength("sk-your-key")[0] == "weak"
    assert "占位" in cred.strength("sk-your-key")[1], "弱值必须说清为什么"
    assert cred.strength("abc123")[0] == "weak", "太短也算弱"
    assert cred.strength("x" * 24)[0] == "weak", "全同字符算占位"
    assert cred.strength(SENTINEL)[0] == "ok"


def test_scan_finds_dotenv_secret_but_never_returns_plaintext(sandbox):
    (sandbox["root"] / ".env").write_text("AC_AI_API_KEY=%s\nAC_PORT=18920\n" % SENTINEL,
                                          encoding="utf-8")
    out = cred.scan()
    blob = json.dumps(out, ensure_ascii=False)
    assert SENTINEL not in blob, "体检结果里出现了明文（不可接受）"
    hit = [f for f in out["found"] if f["purpose"] == "AC_AI_API_KEY"]
    assert hit, "`.env` 里的 key 没被扫到：%s" % out["found"]
    assert hit[0]["fingerprint"] == cred.fingerprint(SENTINEL), "要报的是同一枚指纹"
    assert ".env" in hit[0]["source"] and "AC_AI_API_KEY" in hit[0]["source"], "要能指出在哪一行"
    assert not [f for f in out["found"] if f["purpose"] == "AC_PORT"], "端口不是凭据，不该混进来"


def test_ledger_never_stores_plaintext(sandbox):
    (sandbox["root"] / ".env").write_text("AC_HUB_TOKEN=%s\n" % SENTINEL, encoding="utf-8")
    out = cred.scan_and_record()
    raw = open(out["ledger"], "rb").read().decode("utf-8", "replace")
    assert SENTINEL not in raw, "账本里出现了明文（不可接受）"
    assert cred.fingerprint(SENTINEL) in raw, "账本里应该有指纹"
    again = cred.overview()
    assert again["summary"]["total"] >= 1
    assert any(e["purpose"] == "AC_HUB_TOKEN" for e in again["entries"])


def test_bridge_plaintext_is_found_and_variable_references_are_not(sandbox):
    p = sandbox["data"] / "bridges" / "示例Harness-A" / "start.ps1"
    # 夹具**故意**写一个未渲染占位（`{{…}}`）：用来证明"变量引用/模板占位不算明文"这条判据不误报。
    literal = "$token = '%s'" % SENTINEL
    tmpl = "$t = '{{AC_TOKEN}}'"   # productization: allow(测试夹具故意含未渲染占位，正是被测场景，不是产品里的假数据)
    p.write_text("\n".join([literal, "$env:AC_TOKEN", tmpl, ""]), encoding="utf-8")
    out = cred.scan()
    blob = json.dumps(out, ensure_ascii=False)
    assert SENTINEL not in blob, "体检结果里出现了明文（不可接受）"
    bridge = [f for f in out["found"] if f["kind"] == "bridge"]
    assert bridge, "桥产物里的明文没被找到：%s" % out["found"]
    assert bridge[0]["fingerprint"] == cred.fingerprint(SENTINEL)
    assert "start.ps1:1" in bridge[0]["source"], "要指出文件与行号：%s" % bridge[0]["source"]
    assert len(bridge) == 1, "变量引用/模板占位不许算成明文：%s" % [b["source"] for b in bridge]


def test_unreadable_source_lands_in_skipped_with_a_reason(sandbox):
    # `config.local.json` 写坏了 ⇒ 必须如实说"解析不了"，不许静默当没问题
    (sandbox["root"] / "config.local.json").write_text("{ 这不是 JSON", encoding="utf-8")
    out = cred.scan()
    why = [s for s in out["skipped"] if "config.local.json" in s["source"]]
    assert why, "扫不了的东西必须进 skipped（不许静默当没问题）：%s" % out["skipped"]
    assert why[0]["why"], "skipped 必须给原因"


def test_endpoints_return_health_without_plaintext(sandbox, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AC_HUB_TOKEN", SENTINEL)
    from agent_community.platform.server import app

    c = TestClient(app, client=("127.0.0.1", 54321))
    r = c.post("/api/credentials/scan")
    assert r.status_code == 200, r.text
    body = r.text
    assert SENTINEL not in body, "体检响应里出现了明文（不可接受）"
    assert r.json()["summary"]["found"] >= 1

    r2 = c.get("/api/credentials")
    assert r2.status_code == 200, r2.text
    assert SENTINEL not in r2.text
    data = r2.json()
    assert "entries" in data and "summary" in data
    assert any(e["purpose"] == "AC_HUB_TOKEN" for e in data["entries"]), data["entries"]
    assert all("why" in e and "fingerprint" in e for e in data["entries"])


def test_credentials_is_never_exposed_to_external_readonly():
    """凭据体检会暴露"密钥在哪、多弱" ⇒ 外部只读档必须挡住（有断言，不靠注释）。"""
    from agent_community.platform import server as srv

    denied = any("/api/credentials".startswith(x) for x in srv.EXTERNAL_READONLY_DENY)
    assert denied, "/api/credentials 必须进 EXTERNAL_READONLY_DENY"
    allowed = any("/api/credentials".startswith(x) for x in srv.EXTERNAL_READONLY_PREFIX)
    assert not allowed, "/api/credentials 不许进只读档白名单"
