# -*- coding: utf-8 -*-
"""`/api/version`：**给"一键更新"这类外部工具**的版本事实（只读）。

## 为什么要有它（2026-10-18 老板交办）

老板：「**我让宋去做一个一键更新的插件出来，到时候别人就可以一键更新了，不用跑到仓库来部署**」。
插件要做的第一件事是**问清楚"我现在是哪版、最新是哪版"** —— 这件事只能由平台自己回答，
不能靠插件去猜文件、也不能让用户去翻 CHANGELOG。

## 设计口径（唯一真相都从既有模块取）

- `current`：`agent_community.__version__`（与 CHANGELOG 最新条目由守卫保持一致的**唯一来源**）；
- `latest`：**离线不猜**。本端点**不主动联网**（平台是本地自部署，联网与否要看用户环境）；
  需要"远端最新版"的由插件自己去拉 Gitee Releases（契约里给了 URL 形状），
  这里只如实给出 `channel` 与 `release_url_tpl`；
- `data_dir`：本次运行的数据目录（更新只该动**安装目录**，不该动数据目录 —— 这点插件必须知道）；
- 失败要说话：任何拿不到的字段给 `null` 并附 `notes`，**不许编一个看起来成功的结果**。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["version"])

#: 更新渠道（**主仓库＝GitHub**，2026-11-04 起；Gitee 保留为备用）
CHANNEL = {
    "repo": "https://github.com/TNDHY-cby/agent-community-v4",
    "releases_api": "https://api.github.com/repos/TNDHY-cby/agent-community-v4/releases/latest",
    "release_tarball_tpl": "https://github.com/TNDHY-cby/agent-community-v4/archive/refs/tags/{tag}.tar.gz",
    "tags_ref": "refs/tags/vX.Y.Z",
}


def _platform_version() -> str:
    try:
        import agent_community
        return str(getattr(agent_community, "__version__", "") or "")
    except Exception:                                        # noqa: BLE001
        return ""


def _data_dir() -> str:
    try:
        from ..env_spec import data_dir
        return str(data_dir())
    except Exception:                                        # noqa: BLE001
        import os
        return os.environ.get("AC_DATA_DIR", "")


def _install_dir() -> str:
    """安装目录（＝本包所在的项目根）。更新**只该动这里**，不该动数据目录。"""
    return str(Path(__file__).resolve().parents[3])


def data_dir_risk(install: str, data: str) -> str:
    """数据目录与安装目录的关系 —— 返回""（安全）/ 一句警告。

    ★2026-10-18（实测本机就是这个坑）：默认 `AC_DATA_DIR` 落在**安装目录之内**
    （`<安装目录>/agent_community/data`）⇒ 插件"替换安装目录"会把用户的
    harness 注册、工作间、桥脚本、日志**一起删掉**。这必须明说，不能等出事。
    """
    if not install or not data:
        return "读不到目录，无法判断更新是否安全（插件应提示用户手工确认）"
    try:
        i = Path(install).resolve()
        d = Path(data).resolve()
    except OSError:
        return "目录路径解析失败，无法判断更新是否安全"
    if i == d:
        return "数据目录与安装目录**相同** ⇒ 更新安装目录会直接毁掉数据，必须先分离（设 AC_DATA_DIR 到别处）"
    try:
        d.relative_to(i)
    except ValueError:
        return ""                                     # 数据在安装目录之外 —— 安全形态
    return ("数据目录在**安装目录之内**（%s）⇒ 整体替换安装目录会连带删掉数据；"
            "插件必须先备份或提示用户把 AC_DATA_DIR 指到安装目录之外" % d)


@router.get("/api/version")
async def api_version():
    """版本事实（给外部更新工具；只读、不联网、缺什么就如实说 null）。"""
    notes = []
    current = _platform_version()
    if not current:
        notes.append("读不到 `agent_community.__version__`（安装可能不完整）")
    install = _install_dir()
    data = _data_dir()
    risk = data_dir_risk(install, data)
    if risk:
        notes.append(risk)
    if not data:
        notes.append("读不到数据目录（AC_DATA_DIR 未设置且默认路径不可用）")
    return {
        "current": current or None,
        "latest": None,                     # ★不猜：远端最新版由调用方按 channel 自行查询
        "update_available": None,           # ★没有 latest 就不可能有判断，如实给 null
        "channel": CHANNEL,
        "install_dir": install,
        "data_dir": data or None,
        "update_safety": {
            "replace_install_dir_only": True,
            "keep_data_dir": True,
            "restart_required": True,
            # ★2026-10-19（老板「最优解就行」拍板，V58）：受控重启端点已实现。
            #   四条闸门：本机来源 / 口令（若配）/ **必须有看门狗** / body 带 confirm=true。
            "restart_endpoint": "/api/admin/restart",
            "restart_howto": "POST /api/admin/restart  body: {\"confirm\": true}"
                             "（需本机来源；配了 AC4_SHUTDOWN_PIN 就要带 X-Shutdown-Pin；"
                             "**没有看门狗会被拒 409**——那样重启会永久宕机）",
            "apply_howto": "python tools/update.py --apply",
            "rollback_howto": "python tools/update.py --rollback",
        },
        "notes": notes,
    }


# ── 给前端的"查最新版"（只读、带超时与缓存、失败如实）────────────────
_LATEST_CACHE = {"at": 0.0, "value": None}


@router.get("/api/version/latest")
async def api_version_latest():
    """**只读**地查一次远端最新版，供「检查更新」按钮使用。

    为什么由平台代查而不是前端自己去请求 Gitee：
      ① 前端直连会撞 CORS / 代理 / 网络策略（自部署环境五花八门）；
      ② 平台可以用**超时 + 60 秒缓存**兜住，失败时如实返回 `error`，不把界面卡死；
      ③ 这条端点**只读**（不下载、不替换、不重启）—— 真正的更新动作仍是命令行
         `tools/update.py`；重启另有受控端点 `POST /api/admin/restart`（V58：本机 + 看门狗 + confirm）。
    """
    import time as _time
    import urllib.error
    import urllib.request

    current = _platform_version()
    now = _time.time()
    if _LATEST_CACHE["value"] is not None and now - _LATEST_CACHE["at"] < 60:
        body = dict(_LATEST_CACHE["value"])
        body["cached"] = True
        return body

    api = CHANNEL["releases_api"]
    latest, error, source = None, "", "GitHub Releases API"
    try:
        with urllib.request.urlopen(api, timeout=10) as resp:      # noqa: S310 - 固定 https 域名
            data = json.loads(resp.read().decode("utf-8", "replace"))
        tag = str(data.get("tag_name") or "").lstrip("v")
        latest = tag or None
        if not tag:
            error = "远端返回里没有 tag_name（如实说，不猜）"
    except urllib.error.HTTPError as e:
        error = "HTTP %s（可能是限流或未发布版本）" % e.code
    except Exception as e:                                          # noqa: BLE001
        error = "%s: %s" % (type(e).__name__, str(e)[:120])

    def _tup(v: str) -> tuple:
        return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)

    body = {
        "current": current or None,
        "latest": latest,
        "update_available": (None if not latest or not current
                             else _tup(latest) > _tup(current)),
        "source": source,
        "error": error or None,
        "checked_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cached": False,
        "howto": {
            "apply": "python tools/update.py --apply",
            "apply_and_restart": "python tools/update.py --apply --restart",
            "rollback": "python tools/update.py --rollback",
        },
    }
    if latest:
        _LATEST_CACHE.update({"at": now, "value": body})
    return body
