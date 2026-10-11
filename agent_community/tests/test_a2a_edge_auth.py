# -*- coding: utf-8 -*-
"""38 号实施任务书 · 候选C（稿B）在 9104 A2A 侧的落地测试。

覆盖（对应稿B §8 的 M1 前件 1-4 与稿A C1/C4）：
  1. Agent Card `authentication.schemes` **非空**（读卡公开、调用须凭据）；
  2. POST 入站 Bearer：缺 → **401**、错 → **403**、对 → 放行到 JSON-RPC（语义与平台 P1 一致）；
  3. 启动守卫 fail-closed（`auth_startup_guard`）：鉴权未就绪 → **SystemExit(1)**、就绪 → 放行；
  4. 默认绑定 **回环**（稿A C1）。

⚠️ 不碰真实平台：`handle_rpc` 被替换成哨兵，只验"门过不过"。
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agent_community import a2a_server as a2a  # noqa: E402
from agent_community.platform import identity as _ident  # noqa: E402

SECRET = "unit-test-secret-a2a-20261006"
HARNESS_ID = "h-a2a-edge"


@pytest.fixture
def edge(monkeypatch):
    """把边缘鉴权钉成可控（不写用户 config、不依赖真实 data），并给出合法 token。"""
    monkeypatch.setattr(_ident, "_ensure_secret", lambda: SECRET)
    monkeypatch.setattr(_ident, "known_harness_ids", lambda: [HARNESS_ID])
    return _ident.issue_agent_token(HARNESS_ID)


@pytest.fixture
def a2a_url(monkeypatch):
    """同进程起 A2A server（端口 0），返回基址；`handle_rpc` 换成哨兵避免打平台。"""
    sentinel = {"jsonrpc": "2.0", "id": 1, "result": {"sentinel": True}}
    monkeypatch.setattr(a2a, "handle_rpc", lambda msg, host, port: sentinel)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), a2a.A2AHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _post(url, body, token=None):
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class TestAgentCard:
    def test_schemes_non_empty_and_declares_bearer(self):
        card = a2a._agent_card("127.0.0.1", 9104)
        schemes = (card.get("authentication") or {}).get("schemes") or []
        assert schemes, "候选C §4：卡上 schemes 不得为空（现状空数组属误导性暴露）"
        assert any(s.get("scheme") == "bearer" for s in schemes)
        assert card["supportedInterfaces"], "传输语义不得因鉴权而改变"

    def test_card_endpoint_stays_public(self, a2a_url, edge):
        """读卡无需凭据（A2A 设计：先读卡、再按卡指示鉴权）。"""
        req = urllib.request.Request(f"{a2a_url}/.well-known/agent-card.json")
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode("utf-8"))
        assert body["authentication"]["schemes"]


class TestPostBearerGate:
    """稿B §8-1：缺 401 / 错 403 / 对 放行（与平台 P1 同语义）。"""

    def test_missing_token_is_401(self, a2a_url, edge):
        status, body = _post(f"{a2a_url}/", {"jsonrpc": "2.0", "id": 1, "method": "x"})
        assert status == 401, body
        assert "Bearer" in body

    def test_wrong_token_is_403(self, a2a_url, edge):
        status, body = _post(f"{a2a_url}/", {"jsonrpc": "2.0", "id": 1, "method": "x"},
                             token="bogus")
        assert status == 403, body

    def test_valid_token_passes_gate(self, a2a_url, edge):
        status, body = _post(f"{a2a_url}/", {"jsonrpc": "2.0", "id": 1, "method": "x"},
                             token=edge)
        assert status == 200, (status, body)
        assert json.loads(body).get("result", {}).get("sentinel"), "过了门应进入 JSON-RPC 处理"

    def test_gate_rejects_before_json_parse(self, a2a_url, edge):
        """先鉴权后解析：坏 JSON 且无凭据 → 401（而不是 400 解析错误）。"""
        data = b"{not-json"
        req = urllib.request.Request(f"{a2a_url}/", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 401


class TestStartupGuard:
    """稿B §5 / 稿A C4：鉴权先于暴露（fail-closed）。"""

    def test_refuses_when_secret_missing(self, monkeypatch):
        monkeypatch.setattr(_ident, "auth_secret_ready", lambda: False)
        with pytest.raises(SystemExit) as ei:
            a2a.auth_startup_guard("127.0.0.1")
        assert ei.value.code == 1

    def test_allows_when_ready(self, monkeypatch):
        monkeypatch.setattr(_ident, "auth_secret_ready", lambda: True)
        assert a2a.auth_startup_guard("127.0.0.1") == "127.0.0.1"

    def test_default_host_is_loopback(self, monkeypatch):
        """稿A C1：默认不得是 0.0.0.0（外溢源）。"""
        import argparse

        src = io.open(a2a.__file__, encoding="utf-8").read()
        assert 'default="0.0.0.0"' not in src, "默认 host 必须回环"
        assert 'default="127.0.0.1"' in src


class TestCompanyLevelAuth:
    """M-1 候选 A 升公司级：company_id + Bearer（公司间 B2B 对等协作入口）。

    覆盖：
      1. 公司 token = HMAC(secret, "company:" + company_id) 可通过边缘闸；
      2. `known_company_ids` 正确读取登记册（dict / 数组两种结构）；
      3. Agent Card 声明公司级身份（provider.company_id + schemes 文案含 company_token）。
    """

    def test_company_token_passes_gate(self, monkeypatch):
        """公司 B 持公司 token 经 9104 入站 → 放行（与成员 token 同等语义）。"""
        monkeypatch.setattr(_ident, "_ensure_secret", lambda: SECRET)
        monkeypatch.setattr(_ident, "known_harness_ids", lambda: [])
        monkeypatch.setattr(_ident, "known_company_ids", lambda: ["co-remote-b"])
        token = _ident.issue_company_token("co-remote-b")
        assert _ident.check_edge_bearer(token) is None

    def test_company_token_wrong_company_is_403(self, monkeypatch):
        """登记册只有 co-remote-b，持未登记公司 token → 403。"""
        monkeypatch.setattr(_ident, "_ensure_secret", lambda: SECRET)
        monkeypatch.setattr(_ident, "known_harness_ids", lambda: [])
        monkeypatch.setattr(_ident, "known_company_ids", lambda: ["co-remote-b"])
        token = _ident.issue_company_token("co-intruder")
        code, _ = _ident.check_edge_bearer(token)
        assert code == 403

    def test_company_and_member_tokens_both_accepted(self, monkeypatch):
        """成员 token 与公司 token 双轨共存（向后兼容 harness 侧，不互斥）。"""
        monkeypatch.setattr(_ident, "_ensure_secret", lambda: SECRET)
        monkeypatch.setattr(_ident, "known_harness_ids", lambda: [HARNESS_ID])
        monkeypatch.setattr(_ident, "known_company_ids", lambda: ["co-remote-b"])
        member_tok = _ident.issue_agent_token(HARNESS_ID)
        company_tok = _ident.issue_company_token("co-remote-b")
        assert _ident.check_edge_bearer(member_tok) is None
        assert _ident.check_edge_bearer(company_tok) is None

    def test_known_company_ids_dict_registry(self, tmp_path, monkeypatch):
        """登记册 dict 结构（与 harnesses.json 一致）可正确读取。"""
        reg = tmp_path / "companies.json"
        reg.write_text(json.dumps({"co-a": {"company_id": "co-a", "status": "active"},
                                   "co-b": {"company_id": "co-b", "status": "active"}}),
                       encoding="utf-8")
        monkeypatch.setattr(_ident, "load_config",
                            lambda: {"data_dir": str(tmp_path)})
        assert sorted(_ident.known_company_ids()) == ["co-a", "co-b"]

    def test_known_company_ids_array_registry(self, tmp_path, monkeypatch):
        """登记册数组结构（[{...}]）也可正确读取。"""
        reg = tmp_path / "companies.json"
        reg.write_text(json.dumps([{"company_id": "co-x", "status": "active"},
                                   {"company_id": "co-y", "status": "active"}]),
                       encoding="utf-8")
        monkeypatch.setattr(_ident, "load_config",
                            lambda: {"data_dir": str(tmp_path)})
        assert sorted(_ident.known_company_ids()) == ["co-x", "co-y"]

    def test_known_company_ids_missing_registry_returns_empty(self, tmp_path, monkeypatch):
        """登记册缺失 → 空列表（fail-closed：无登记公司即拒绝公司 token）。"""
        monkeypatch.setattr(_ident, "load_config",
                            lambda: {"data_dir": str(tmp_path)})
        assert _ident.known_company_ids() == []

    def test_agent_card_declares_company_identity(self, monkeypatch):
        """M-1：Agent Card 声明公司级身份（provider.company_id 可配置）。"""
        monkeypatch.setenv("AC4_COMPANY_ID", "co-local-a")
        card = a2a._agent_card("127.0.0.1", 9104)
        assert (card.get("provider") or {}).get("company_id") == "co-local-a"
        schemes = (card.get("authentication") or {}).get("schemes") or []
        creds = "".join(str(s.get("credentials", "")) for s in schemes)
        assert "company_token" in creds, "卡片须对外声明公司级 token 维度"


class TestTailscaleBind:
    """M-2（45 号 §二.2）：9104 绑 **Tailscale 虚拟 IP**，且**绝不退化成 0.0.0.0**。

    无 Tailscale 的环境下也要能验证判据本身（用注入的 runner 模拟 `tailscale ip -4` 输出）。
    """

    def test_resolves_cgnat_ip(self):
        ip = a2a.resolve_tailscale_ipv4(runner=lambda cmd: "100.101.102.103\n")
        assert ip == "100.101.102.103"

    def test_takes_first_line_of_multi_line(self):
        ip = a2a.resolve_tailscale_ipv4(runner=lambda cmd: "100.64.0.9\nfd7a::1\n")
        assert ip == "100.64.0.9"

    @pytest.mark.parametrize("out", ["", "\n", "0.0.0.0\n", "192.168.1.5\n", "1.2.3.4\n"])
    def test_fail_closed_on_missing_or_non_cgnat(self, out):
        """未安装 / 未登录 / 非 100.x → 拒绝启动（**不退化成 0.0.0.0 暴露**）。"""
        with pytest.raises(SystemExit) as ei:
            a2a.resolve_tailscale_ipv4(runner=lambda cmd: out)
        assert ei.value.code == 1

    def test_runner_exception_is_fail_closed(self):
        """runner 自身抛错（如命令不存在）也要走 fail-closed，不把异常泄给上层。"""
        with pytest.raises(SystemExit) as ei:
            a2a.resolve_tailscale_ipv4(
                runner=lambda cmd: (_ for _ in ()).throw(FileNotFoundError("tailscale 未安装")))
        assert ei.value.code == 1

    def test_host_tailscale_binds_resolved_ip(self, monkeypatch):
        """`--host tailscale` → 真正绑定解析出的虚拟 IP（而不是回环 / 0.0.0.0）。"""
        captured = {}

        class _FakeHTTPD:
            def __init__(self, addr, handler):
                captured["addr"] = addr

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        monkeypatch.setattr(a2a, "resolve_tailscale_ipv4", lambda: "100.88.99.100")
        monkeypatch.setattr(a2a, "auth_startup_guard", lambda h: h)
        monkeypatch.setattr(a2a, "ThreadingHTTPServer", _FakeHTTPD)
        monkeypatch.setattr(sys, "argv", ["a2a_server", "--host", "tailscale", "--port", "9104"])
        a2a.main()
        assert captured["addr"] == ("100.88.99.100", 9104)
