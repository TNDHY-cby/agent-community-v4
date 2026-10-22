# -*- coding: utf-8 -*-
"""Agent Manager 端点（V80 §A.3）：清单 / 检测 / 安装 / 升级。

安全边界（写死在代码里，不许放松）：
- 只执行**清单里登记过**的命令模板；`shell=False`（命令是列表，不经 shell）；
- 必须 `AC_AGENT_INSTALL_ALLOW=1` **且** body 带 `confirm: true`；
- `dry_run` 只回命令不执行；
- 失败的退出码与输出尾部**原样**返回（失败要说话）。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

from fastapi import APIRouter, Request

from .. import agent_catalog as cat
from ..audit import audit_log as _audit_log

router = APIRouter()


def _allow() -> bool:
    return (os.environ.get("AC_AGENT_INSTALL_ALLOW", "0") or "0").strip() in ("1", "true", "yes", "on")


def _timeout() -> int:
    try:
        return max(30, int(os.environ.get("AC_AGENT_INSTALL_TIMEOUT", "600")))
    except Exception:                                        # noqa: BLE001
        return 600


@router.get("/api/agents/catalog")
async def list_catalog():
    items, err = cat.load()
    out = []
    for row in items:
        out.append({"id": row.get("id"), "name": row.get("name"), "kind": row.get("kind") or "cli",
                    "detect": row.get("detect") or "", "docs_url": row.get("docs_url") or "",
                    "note": row.get("note") or "",
                    "install_supported": bool(row.get("install_cmd")),
                    "install_allowed_now": _allow() and bool(row.get("install_cmd"))})
    return {"count": len(out), "install_switch_on": _allow(), "agents": out,
            **({"catalog_error": err} if err else {})}


@router.post("/api/agents/{agent_id}/detect")
async def detect(agent_id: str):
    from ..server import Utf8JSONResponse

    row = cat.by_id(agent_id)
    if row is None:
        return Utf8JSONResponse({"error": "清单里没有这个 agent：%s" % agent_id,
                                 "how_to_fix": "GET /api/agents/catalog 看现有 id；或用 AC_AGENT_CATALOG_EXTRA 加自己的"},
                                status_code=404)
    bin_name = str(row.get("detect") or "")
    if not bin_name:
        return {"agent_id": agent_id, "installed": False, "why": "这条清单没声明检测用的可执行文件名"}
    path = shutil.which(bin_name)
    if not path:
        return {"agent_id": agent_id, "installed": False, "path": "",
                "why": "PATH 里找不到 %s（没装，或者装了但不在 PATH 里）" % bin_name}
    ver = ""
    try:
        # async 里不许直接跑阻塞进程（守卫 test_async_no_blocking 会抓）⇒ 挪到线程
        r = await asyncio.to_thread(
            subprocess.run, [path] + list(row.get("version_args") or ["--version"]),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        ver = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[:1]
        ver = ver[0][:80] if ver else ""
    except Exception as e:                                   # noqa: BLE001
        ver = "（版本没读出来：%s: %s）" % (type(e).__name__, str(e)[:60])
    return {"agent_id": agent_id, "installed": True, "path": path, "version": ver, "why": ""}


def _run_install(agent_id: str, request_body: dict):
    from ..server import Utf8JSONResponse

    row = cat.by_id(agent_id)
    if row is None:
        return Utf8JSONResponse({"error": "清单里没有这个 agent：%s" % agent_id}, status_code=404)
    tpl = row.get("install_cmd")
    if not tpl:
        return Utf8JSONResponse({"error": "这条清单没有安装命令（只能手工装）",
                                 "docs_url": row.get("docs_url") or ""}, status_code=400)
    if not _allow():
        return Utf8JSONResponse({"error": "安装开关没打开（默认关，防止误执行）",
                                 "how_to_fix": "设环境变量 AC_AGENT_INSTALL_ALLOW=1 再试；只想看命令就用 dry_run"}, status_code=403)
    if not bool(request_body.get("confirm")):
        return Utf8JSONResponse({"error": "要显式确认：body 加 confirm: true"}, status_code=400)

    pkg = str(request_body.get("package") or "")
    cmd = [str(x).replace("%PACKAGE%", pkg) for x in tpl]
    if "%PACKAGE%" in " ".join(map(str, tpl)) and not pkg:
        return Utf8JSONResponse({"error": "这条命令需要包名：body 加 package（例如某个 CLI 的 npm 包名）"},
                                status_code=400)

    if bool(request_body.get("dry_run")):
        return {"executed": False, "cmd": cmd, "agent_id": agent_id,
                "why": "dry_run：只回命令，没有执行"}

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=_timeout())
        tail = ((r.stdout or "") + (r.stderr or "")).strip()[-2000:]
        ok = r.returncode == 0
        _audit_log.record("agent.install", actor="user", target=agent_id,
                          detail="安装命令 %s → exit=%d" % (" ".join(cmd)[:120], r.returncode))
        return {"executed": True, "ok": ok, "cmd": cmd, "exit_code": r.returncode, "tail": tail,
                "agent_id": agent_id,
                "how_to_fix": "" if ok else "按上面的输出修；也可以按 docs_url 手工装：%s" % (row.get("docs_url") or "")}
    except subprocess.TimeoutExpired:
        return Utf8JSONResponse({"error": "安装超时（%d 秒）" % _timeout(),
                                 "how_to_fix": "命令可能卡住/在等输入；请手工执行：%s" % " ".join(cmd)},
                                status_code=504)
    except Exception as e:                                   # noqa: BLE001
        return Utf8JSONResponse({"error": "%s: %s" % (type(e).__name__, str(e)[:120]),
                                 "cmd": cmd}, status_code=500)


@router.post("/api/agents/{agent_id}/install")
async def install(agent_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    return await asyncio.to_thread(_run_install, agent_id, body if isinstance(body, dict) else {})


@router.post("/api/agents/{agent_id}/upgrade")
async def upgrade(agent_id: str, request: Request):
    """升级 = 同一套命令模板（pip 用 -U；npm 用同一条 install） + 记录。"""
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    # ★D6：升级同样是同步 subprocess（最长 600s），必须挪线程，否则整个 API 假死
    return await asyncio.to_thread(_run_install, agent_id, body if isinstance(body, dict) else {})
