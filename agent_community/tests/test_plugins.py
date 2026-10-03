"""platform/routers/plugins.py 模块级单元测试。

重点：POST /api/plugins/{name}/invoke
- 未配置 Token -> 403；Token 无效 -> 401
- 插件不存在 -> 404
- cmd 插件危险 command（format/net stop/rm -rf/rd /s /q/reg delete）-> 400
- cmd 插件空 command -> 400
- cmd 插件安全 command -> 放行执行（subprocess.Popen 打桩，不真实起进程）
- http 插件 -> httpx.AsyncClient 打桩放行

隔离方式：monkeypatch server 模块的 ALLOWED_TOKENS / PLUGINS_FILE 到临时目录，
全程不触碰 dev/data 真实插件清单。
"""
import asyncio
import json

import pytest

from agent_community.platform import server as server_mod
from agent_community.platform.routers import plugins as plugins_mod


# ── 测试替身 ─────────────────────────────────────────────────────
class _Query:
    def __init__(self, d=None):
        self._d = d or {}

    def get(self, key, default=""):
        return self._d.get(key, default)


class FakeRequest:
    """最小 Request 替身：headers + query_params + async json()。"""

    def __init__(self, body=None, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = _Query(query)
        self._body = body

    async def json(self):
        return self._body if self._body is not None else {}


class FakeProc:
    """subprocess.Popen 替身：记录命令并返回可控输出。"""

    last_command = None

    def __init__(self, *args, **kwargs):
        type(self).last_command = args[0] if args else kwargs.get("args")
        self.pid = 4242
        self.returncode = 0

    def communicate(self, timeout=None):
        return "hello from fake", None

    def kill(self):
        pass


class FakeHttpResp:
    status_code = 200
    text = "mock http body"


class FakeAsyncClient:
    """httpx.AsyncClient 替身：async context manager，get 返回 200。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        return FakeHttpResp()


def unpack(resp):
    """把 Utf8JSONResponse 或纯 dict 统一拆成 (status_code, payload)。"""
    if hasattr(resp, "status_code") and hasattr(resp, "body"):
        return resp.status_code, json.loads(resp.body)
    return None, resp


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离测试环境：临时 PLUGINS_FILE + 固定 Token。"""
    plugins_file = tmp_path / "plugins.json"
    plugins_file.write_text(
        json.dumps(
            {
                "echo": {"type": "cmd", "target": "echo hi"},
                "web": {"type": "http", "target": "http://127.0.0.1:9/never"},
                "bad": {"type": "cmd", "target": "format c:"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_mod, "PLUGINS_FILE", plugins_file)
    monkeypatch.setattr(server_mod, "ALLOWED_TOKENS", {"test-tok"})
    return plugins_file


def auth_headers(token="test-tok"):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def permissive_policy(tmp_path, monkeypatch):
    """V-14：把策略引擎换成「全放行且无规则」，让本文件的测试继续验证**端点自身**逻辑。

    为什么需要：V-14 给 plugins 端点加了策略闸门，出厂策略下
    `shell.exec` / `plugin.install` 都是 ASK —— 会让本文件原有的
    「鉴权 / 黑名单 / 真执行」单元测试全部走到待审批分支（9 个用例失败）。
    本 fixture 让这些测试聚焦在端点自身行为上；
    **策略闸门本身的行为由 test_policy_wiring_tools.py 覆盖**
    （那里用出厂策略断言 ASK 不入队不执行、危险命令 403）。
    """
    from agent_community.platform import policy as policy_mod
    from agent_community.platform.policy import PolicyEngine

    eng = PolicyEngine(
        data_dir=tmp_path / "_policy",
        config={"version": 1, "default_by_op": {}, "actor_overrides": {}, "rules": []},
    )
    monkeypatch.setattr(policy_mod, "policy_engine", eng)
    return eng


def run(coro):
    return asyncio.run(coro)


# ── 鉴权 ─────────────────────────────────────────────────────────
def test_invoke_disabled_without_token(env, monkeypatch):
    monkeypatch.setattr(server_mod, "ALLOWED_TOKENS", set())
    status, payload = unpack(run(plugins_mod.api_plugins_invoke("echo", FakeRequest(headers=auth_headers()))))
    assert status == 403
    assert "未配置访问 Token" in payload["error"]


def test_invoke_rejects_bad_token(env):
    status, payload = unpack(run(plugins_mod.api_plugins_invoke("echo", FakeRequest(headers={}))))
    assert status == 401
    assert "Token" in payload["error"]
    # X-API-Key 也支持
    status2, _ = unpack(run(plugins_mod.api_plugins_invoke(
        "echo", FakeRequest(headers={"X-API-Key": "wrong"}))))
    assert status2 == 401


# ── 插件不存在 ───────────────────────────────────────────────────
def test_invoke_plugin_not_found(env):
    status, payload = unpack(run(plugins_mod.api_plugins_invoke("nope", FakeRequest(headers=auth_headers()))))
    assert status == 404
    assert "不存在" in payload["error"]


# ── cmd 插件：危险命令拦截（V-9c 修复回归）───────────────────────
@pytest.mark.parametrize(
    "dangerous",
    [
        "format c:",
        "net stop wuauserv",
        "rm -rf /tmp/x",
        "rd /s /q C:\\foo",
        "reg delete HKLM\\Software\\X /f",
        "del /f C:\\x",
        "shutdown /s /t 0",
    ],
)
def test_invoke_dangerous_command_400(env, dangerous):
    status, payload = unpack(run(plugins_mod.api_plugins_invoke(
        "echo", FakeRequest(body={"command": dangerous}, headers=auth_headers()))))
    assert status == 400
    assert "安全策略拦截" in payload["error"]


def test_invoke_empty_command_400(env):
    status, payload = unpack(run(plugins_mod.api_plugins_invoke(
        "echo", FakeRequest(body={"command": "   "}, headers=auth_headers()))))
    assert status == 400
    assert "command 不能为空" in payload["error"]


# ── cmd 插件：安全命令放行 ───────────────────────────────────────
def test_invoke_safe_command_ok(env, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", FakeProc)
    status, payload = unpack(run(plugins_mod.api_plugins_invoke(
        "echo", FakeRequest(body={"command": "echo hello"}, headers=auth_headers()))))
    assert status is None
    assert payload["success"] is True
    assert FakeProc.last_command == "echo hello"
    assert "[exit 0]" in payload["output"]
    assert "hello from fake" in payload["output"]


# ── http 插件：放行 ──────────────────────────────────────────────
def test_invoke_http_ok(env, monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)
    status, payload = unpack(run(plugins_mod.api_plugins_invoke(
        "web", FakeRequest(body={}, headers=auth_headers()))))
    assert status is None
    assert payload["success"] is True
    assert "HTTP 200" in payload["output"]


# ── 插件增删基础校验 ─────────────────────────────────────────────
def test_add_plugin_validation(env):
    status, payload = unpack(run(plugins_mod.api_plugins_add(FakeRequest(body={}, headers=auth_headers()))))
    assert status == 400
    status2, payload2 = unpack(run(plugins_mod.api_plugins_add(
        FakeRequest(body={"name": "x", "type": "badtype", "target": "y"}, headers=auth_headers()))))
    assert status2 == 400
    assert "type 仅支持" in payload2["error"]


def test_add_plugin_success_writes_file(env):
    body = {"name": "newplug", "type": "cmd", "target": "echo ok"}
    status, payload = unpack(run(plugins_mod.api_plugins_add(
        FakeRequest(body=body, headers=auth_headers()))))
    assert status is None
    assert payload["success"] is True
    saved = json.loads(env.read_text(encoding="utf-8"))
    assert "newplug" in saved
    assert saved["newplug"]["target"] == "echo ok"


def test_delete_plugin(env):
    status, payload = unpack(run(plugins_mod.api_plugins_del("echo")))
    assert status is None
    assert payload["success"] is True
    assert "echo" not in json.loads(env.read_text(encoding="utf-8"))
