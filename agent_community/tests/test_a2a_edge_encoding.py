# -*- coding: utf-8 -*-
"""9104 A2A 边缘**入站请求体编码**测试（2026-10-07 DSH）。

背景（实测取证）：47 号 A2A 送来的中文任务在平台侧落成字面 `?`
（`data/audit.json` 的 workshop.create 明细 = `47????????,?????????`，而工作间名取自
`title[:20]`）⇒ 文本**到达平台时已被污染**。而本层原实现有两处帮凶：

  ① `decode("utf-8", "replace")` **静默替换**：解不出来也不报错，继续拿污染文本建工作间；
  ② **完全忽略** `Content-Type` 里声明的 charset：对端如实声明 `charset=gbk` 也按 utf-8 解。

覆盖：
  1. `decode_body` 纯函数：默认 utf-8 / 声明 gbk 正确解 / 声明与实际不符 → 报错 / 未知 charset → 报错
  2. HTTP 层：声明 gbk + GBK 字节 → 上游 `handle_rpc` 拿到的是**正确中文**（不再静默污染）
  3. HTTP 层：非法 UTF-8 且未声明 charset → **400 明确拒绝**，且 `handle_rpc` **不被调用**
  4. HTTP 层：未知 charset → **415**
  5. **顺序**：编码坏 + 无 Bearer → 仍是 **401**（未认证方不得借错误码探测服务行为）
  6. 声明 utf-8 + 合法 body + 合法 token → 正常放行（回归：别把老路堵死）

⚠️ 不碰真实平台：`handle_rpc` 换成哨兵。
"""
from __future__ import annotations

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

SECRET = "unit-test-secret-a2a-enc-20261007"
HARNESS_ID = "h-a2a-enc"
CN = "合作社中文任务：请把接口文档整理成 Markdown"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(_ident, "_ensure_secret", lambda: SECRET)
    monkeypatch.setattr(_ident, "known_harness_ids", lambda: [HARNESS_ID])
    return _ident.issue_agent_token(HARNESS_ID)


@pytest.fixture
def edge(monkeypatch):
    """同进程起 A2A server（端口 0）；`handle_rpc` 换成能记录入参的哨兵。"""
    seen: list = []
    monkeypatch.setattr(a2a, "handle_rpc",
                        lambda msg, host, port: (seen.append(msg) or {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), a2a.A2AHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post_raw(url, raw: bytes, content_type: str, token=None):
    headers = {"Content-Type": content_type}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _rpc_bytes(encoding: str) -> bytes:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tasks/send",
            "params": {"message": {"parts": [{"text": CN}]}}}
    return json.dumps(body, ensure_ascii=False).encode(encoding)


# ══════════════════════════════════════════════════════════════
# 一、纯函数：decode_body
# ══════════════════════════════════════════════════════════════

class TestDecodeBody:
    def test_default_is_utf8_when_charset_absent(self):
        txt, err = a2a.decode_body(CN.encode("utf-8"), "application/json")
        assert err == "", f"未声明 charset 应按 JSON 默认 utf-8，实得 error={err!r}"
        assert CN in txt

    def test_declared_gbk_is_honoured(self):
        """对端如实声明 gbk → 必须按 gbk 解，且拿到正确中文（核心修复点）。"""
        txt, err = a2a.decode_body(CN.encode("gbk"), "application/json; charset=gbk")
        assert err == "", f"声明 gbk 应能正确解码，实得 error={err!r}"
        assert CN in txt

    def test_declared_charset_must_match_actual_bytes(self):
        """声明 gbk 却发 UTF-8 字节 → 必须报错，不许静默替换。"""
        txt, err = a2a.decode_body(CN.encode("utf-8"), "application/json; charset=gbk")
        assert err != "", "编码不符必须报错"
        assert "gbk" in err

    def test_invalid_utf8_without_charset_reports_error_not_replacement(self):
        """原缺陷复现点：非法 UTF-8 + 未声明 → 以前静默变成 U+FFFD，现在必须报错。"""
        txt, err = a2a.decode_body(CN.encode("gbk"), "application/json")
        assert err != ""
        assert "\ufffd" not in txt, "绝不能返回替换字符（那是静默降级）"

    def test_unknown_charset_is_rejected_as_unsupported(self):
        txt, err = a2a.decode_body(b"{}", "application/json; charset=no-such-codec-xyz")
        assert err.startswith("不支持"), f"未知 charset 应归为 415 一类，实得 {err!r}"

    def test_charset_detection_handles_quotes_and_case(self):
        assert a2a._client_charset('application/json; charset="GBK"') == "gbk"
        assert a2a._client_charset("application/json;charset=utf-8") == "utf-8"
        assert a2a._client_charset("") == "utf-8"


# ══════════════════════════════════════════════════════════════
# 二、HTTP 层：编码语义
# ══════════════════════════════════════════════════════════════

class TestEdgeEncoding:
    def test_gbk_declared_body_reaches_rpc_with_intact_chinese(self, edge, token):
        """声明 gbk + GBK 字节 → 上游拿到的是**正确中文**（而不是被替换的乱码）。"""
        url, seen = edge
        st, body = _post_raw(url, _rpc_bytes("gbk"), "application/json; charset=gbk", token)
        assert st == 200, f"应放行，实得 {st} {body}"
        assert seen, "handle_rpc 应被调用"
        text = seen[0]["params"]["message"]["parts"][0]["text"]
        assert text == CN, f"中文必须原样到达，实得 {text!r}"
        assert "\ufffd" not in text

    def test_bad_encoding_is_rejected_and_rpc_not_called(self, edge, token):
        url, seen = edge
        st, body = _post_raw(url, _rpc_bytes("gbk"), "application/json", token)
        assert st == 400, f"非法 UTF-8 应 400，实得 {st} {body}"
        assert not seen, "编码不合法时绝不能进入 JSON-RPC 处理（不许拿污染文本建工作间）"
        assert "编码" in body

    def test_unknown_charset_returns_415(self, edge, token):
        url, seen = edge
        st, body = _post_raw(url, b"{}", "application/json; charset=no-such-codec-xyz", token)
        assert st == 415, f"未知 charset 应 415，实得 {st} {body}"
        assert not seen

    def test_auth_precedes_encoding_error(self, edge):
        """**顺序契约**：编码坏且无凭据 → 401（未认证方不得借错误码探测服务行为）。"""
        url, seen = edge
        st, body = _post_raw(url, _rpc_bytes("gbk"), "application/json", token=None)
        assert st == 401, f"应先撞鉴权，实得 {st} {body}"
        assert not seen

    def test_utf8_happy_path_still_works(self, edge, token):
        """回归：正常 UTF-8 请求不能被我这次改动堵死。"""
        url, seen = edge
        st, body = _post_raw(url, _rpc_bytes("utf-8"), "application/json; charset=utf-8", token)
        assert st == 200, f"实得 {st} {body}"
        assert seen and seen[0]["method"] == "tasks/send"
