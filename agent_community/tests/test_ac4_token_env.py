# -*- coding: utf-8 -*-
"""`AC4_TOKEN` **必须真能用** —— 文档承诺的开关不许是死的（2026-10-15）。

## 缺陷（真机容器走查撞出来的，不是读代码发现的）

`AC4_TOKEN` 在**三处文档**里承诺过：
  · README 环境变量表：「平台访问令牌（跨机/对外访问时必设；空=仅本机可用）」；
  · `env_spec`：缺它就报"没配令牌"，并告诉用户「设 `AC4_TOKEN=<你的令牌>`，或用 `AC_HOST=127.0.0.1` 只在本机跑」；
  · `docker-compose.yml` 头部：「① 只想用命令行 / API：在 `.env` 里设 `AC4_TOKEN=<你的令牌>`，
    请求带 `Authorization: Bearer <令牌>`」。
而实现**只读 `--token` 命令行** ⇒ 容器里（`Dockerfile` 的 `CMD` 没有 `--token`）
与 `cli start`（`Popen` 也没传）里**设了令牌完全无效**。
真机实测 `temp/verify_container.py` 打出来的是：

    带令牌应 200，实得 403 {"error":"服务未配置访问 Token，拒绝非本地访问"}

**看起来像"你没配"，其实配了** —— 失败不说话，正是本项目最忌讳的那种死状态。
同族前例：2026-10-14 的 `AC_HOST`（登记进 env_spec 却被 `uvicorn.run` 写死）。

## 本文件按什么判

全部走**真结果**：真读环境变量、真调 `_auth_check` / `security_middleware`（用真的
`starlette.Request`，`client.host` 给成非本机地址）、**真起一个服务进程**
（只设 `AC4_TOKEN`、**不带** `--token`）。没有一条是查源码文本。

真·非本机网络来源的端到端证明不在这里（那需要端口映射），它在容器走查
`temp/verify_container.py` 里（重跑过，见 `PORTABILITY.md` §5.1 条 C）。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
# ★从 **fastapi** 取 `Request`/`HTTPException`（与 `server.py` 同一个类、同一个来源）：
#   直接 `import starlette...` 会让 `test_every_third_party_import_is_declared` 报红
#   —— `requirements.txt` 只声明了 `fastapi`，starlette 是它的传递依赖，不该当独立依赖引。
from fastapi import HTTPException, Request

from agent_community.platform import server as srv

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def _request(host: str = "172.17.0.1", headers: dict | None = None) -> Request:
    """一个**真的** `starlette.Request`，来源是非本机地址（容器网关就是这种）。

    只喂 TestClient 会给出 `client.host = "testclient"`（也算非本机），
    这里显式给 IP，避免"测试替身碰巧走对分支"。
    """
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http", "method": "GET", "path": "/api/status",
        "raw_path": b"/api/status", "query_string": b"", "headers": hdrs,
        "client": (host, 54321), "server": ("172.17.0.2", 18920),
        "scheme": "http", "root_path": "",
    }
    return Request(scope)


async def _call_next(_req):
    class _OK:
        status_code = 200
    return _OK()


# ── ① 真函数：把 env 与命令行合成一个集合 ──────────────────────────────────
def test_env_token_is_collected(monkeypatch):
    monkeypatch.setenv("AC4_TOKEN", "  tok-from-env  ")
    assert srv.collect_allowed_tokens([]) == ["tok-from-env"], \
        "环境变量里的令牌没被收进来 —— 文档承诺的那一半仍是死的"


def test_cli_and_env_merge(monkeypatch):
    monkeypatch.setenv("AC4_TOKEN", "tok-env")
    got = set(srv.collect_allowed_tokens(["tok-cli", "", "tok-cli"]))
    assert got == {"tok-cli", "tok-env"}, got


def test_blank_env_is_ignored(monkeypatch):
    monkeypatch.setenv("AC4_TOKEN", "   ")
    assert srv.collect_allowed_tokens([]) == [], "空白令牌不许被当成'配好了'"


def test_missing_env_keeps_cli(monkeypatch):
    monkeypatch.delenv("AC4_TOKEN", raising=False)
    assert srv.collect_allowed_tokens(["tok-cli"]) == ["tok-cli"]


# ── ② 真鉴权：_auth_check 的三个分支 ───────────────────────────────────────
def test_nonlocal_is_rejected_when_no_token_is_configured(monkeypatch):
    monkeypatch.delenv("AC4_TOKEN", raising=False)
    monkeypatch.setattr(srv, "ALLOWED_TOKENS", set())
    with pytest.raises(HTTPException) as e:
        srv._auth_check(_request())
    assert e.value.status_code == 403
    assert "未配置访问 Token" in e.value.detail, "拒绝要说明为什么（失败要说话）"


def test_env_token_grants_and_wrong_token_is_401(monkeypatch):
    monkeypatch.setenv("AC4_TOKEN", "tok-env")
    monkeypatch.setattr(srv, "ALLOWED_TOKENS", set())
    srv.ALLOWED_TOKENS.update(srv.collect_allowed_tokens([]))
    assert srv.ALLOWED_TOKENS == {"tok-env"}, "env 令牌没进 ALLOWED_TOKENS"

    # ① 带对的令牌 → 放行（不抛）
    srv._auth_check(_request(headers={"Authorization": "Bearer tok-env"}))
    srv._auth_check(_request(headers={"X-API-Key": "tok-env"}))

    # ② 带错的令牌 → 401（且说明要令牌）
    with pytest.raises(HTTPException) as e:
        srv._auth_check(_request(headers={"Authorization": "Bearer wrong"}))
    assert e.value.status_code == 401

    # ③ 本机来源仍放行（口径不变：本机免 token）
    srv._auth_check(_request(host="127.0.0.1"))


# ── ③ 真中间件：容器里那次 403 就是它返回的 ─────────────────────────────────
@pytest.mark.parametrize("with_token", [True, False])
def test_real_middleware_status_with_env_token(monkeypatch, with_token):
    monkeypatch.setenv("AC4_TOKEN", "tok-env")
    monkeypatch.setattr(srv, "ALLOWED_TOKENS", set())
    srv.ALLOWED_TOKENS.update(srv.collect_allowed_tokens([]))
    headers = {"Authorization": "Bearer tok-env"} if with_token else {}
    resp = asyncio.run(srv.security_middleware(_request(headers=headers), _call_next))
    if with_token:
        assert resp.status_code == 200, \
            "带 env 令牌仍被中间件拦下 —— 文档路径 ① 依然是死的"
    else:
        assert resp.status_code in (401, 403)
        body = json.loads(bytes(resp.body).decode("utf-8"))
        assert body.get("error"), "拒绝时必须给出原因（不许空原因）"


# ── ④ 真起一个进程（只设 env、**不带** --token，就是容器/CLI 的启动方式）────
def test_real_process_startup_reads_env_token(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "server.log"
    env = {**os.environ, "AC_DATA_DIR": str(data_dir), "AC_PORT": str(port),
           "AC4_TOKEN": "tok-from-env", "AC_HOST": "127.0.0.1",
           "PYTHONIOENCODING": "utf-8"}
    logf = open(log_path, "wb")
    p = None
    try:
        p = subprocess.Popen(
            [sys.executable, "-m", "agent_community.platform.server", "--port", str(port)],
            cwd=_ROOT, env=env, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        up = False
        for _ in range(60):
            time.sleep(1.0)
            if p.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/api/status" % port, timeout=3) as r:
                    if r.status == 200:
                        up = True
                        break
            except Exception:  # noqa: BLE001
                continue
        assert up, "进程没起来（说明启动阶段就挂了）：" + \
            log_path.read_text(encoding="utf-8", errors="replace")[-1200:]
        # 观测点：启动时如实说了令牌状态（**进程级**唯一可观测的那句话；
        # 真·非本机来源的端到端在容器走查里）
        tail = log_path.read_text(encoding="utf-8", errors="replace")
        assert "外部访问令牌已配置 1 个" in tail, \
            "启动日志没说令牌已配置（env 令牌没被读到）：\n" + tail[-800:]
    finally:
        try:
            logf.close()
        except Exception:  # noqa: BLE001
            pass
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=20)
            except Exception:  # noqa: BLE001
                p.kill()
