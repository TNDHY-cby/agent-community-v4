# -*- coding: utf-8 -*-
"""守卫：**对端公司登记册 + 隧道建议**（V62 公司间合作通用框架）。

判据（按真行为）：
  ① 地址规范化：`http://host:9104` 原样保留、补端口、拒绝带路径 / 非 http / 空；
  ② 令牌**只回打码值**（列表/详情里永不出现明文）；
  ③ 登记幂等（同一 peer_id 更新不重复建）；停用/启用生效；删除只删本地；
  ④ **隧道建议是"通用"的**：不出现任何具体服务商名；给出多类办法；明确"别暴露管理端点"；
     只监听回环 ⇒ 判断为 `unlikely`（不猜"已通"）；
  ⑤ 暴露前自查：没签发过令牌时 `check_inbound_ready` 必须报问题（fail-closed）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


@pytest.fixture()
def peers_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    from agent_community.platform import peers as pmod
    return pmod


def test_url_normalization(peers_env):
    ok = peers_env.normalize_base_url
    assert ok("http://127.0.0.1:9104")[1] == ""
    assert ok("http://example.com:8080")[0] == "http://example.com:8080"
    # 没写端口：按 scheme 补默认端口（A2A 端点要拼根路径，端口不能丢）
    assert ok("http://example.com")[0] == "http://example.com:80"
    assert ok("https://example.com")[0] == "https://example.com:443"
    # 带路径要拒（对端在根路径提供 JSON-RPC，带路径会拼错）
    url, err = ok("http://example.com:8080/a2a")
    assert not url and "不要带路径" in err
    assert ok("")[1] and ok("ftp://x")[1]


def test_token_never_returned_in_clear(peers_env):
    rec, err = peers_env.upsert_peer({"display_name": "公司 B", "base_url": "http://127.0.0.1:9104",
                                      "token": "company-token-abc"})
    assert not err and rec["peer_id"]
    view = peers_env.public_view(peers_env.get_peer(rec["peer_id"]))
    assert "company-token-abc" not in str(view), "明文令牌泄漏：%s" % view
    assert view["has_token"] is True and view["token_masked"]
    # 发请求时才解密出来
    assert peers_env.peer_token(peers_env.get_peer(rec["peer_id"])) == "company-token-abc"


def test_upsert_idempotent_and_enable_delete(peers_env):
    r1, _ = peers_env.upsert_peer({"display_name": "B", "base_url": "http://h:9104"})
    r2, _ = peers_env.upsert_peer({"display_name": "B 改名", "base_url": "http://h:9104"})
    assert r1["peer_id"] == r2["peer_id"]
    assert len(peers_env.load_peers()["peers"]) == 1, "同一对端不该建两条"
    peers_env.set_enabled(r1["peer_id"], False)
    assert peers_env.get_peer(r1["peer_id"])["enabled"] is False
    assert peers_env.delete_peer(r1["peer_id"]) is True
    assert peers_env.delete_peer(r1["peer_id"]) is False


def test_tunnel_advice_is_generic(monkeypatch):
    """关键：**不许出现任何具体服务商名**（否则就是把外部依赖写进产品）。"""
    monkeypatch.setenv("AC_HOST", "127.0.0.1")
    from agent_community.platform import tunnel
    out = tunnel.advise(local_port=9104)
    blob = (out["why"] + " ".join(out["suggest"])).lower()
    for vendor in ("chmlfrp", "tailscale", "zerotier", "ngrok-only", "花生壳"):
        assert vendor not in blob, "隧道建议里出现了具体服务商：%s" % vendor
    assert out["reachable_from_outside"] == "unlikely", "只监听回环必须判 unlikely（不猜已通）"
    assert len(out["suggest"]) >= 3
    assert out["must_not_expose"], "必须给出「别暴露什么」"
    assert out["local_port"] == 9104


def test_inbound_ready_is_fail_closed():
    from agent_community.platform import tunnel
    bad = tunnel.check_inbound_ready(token_ready=False, exposed=True)
    assert bad["ok"] is False and bad["problems"], "没鉴权却允许暴露＝裸奔"
    good = tunnel.check_inbound_ready(token_ready=True, exposed=True)
    assert good["ok"] is True
