# -*- coding: utf-8 -*-
"""platform_address：**平台地址与端口的唯一解析点**。

老板 2026-10-13 两句话定了这个模块的存在：

    「本地网址**不应该引入**」——别把本机地址（`127.0.0.1:某个数字`）写进对外的东西里；
    「本地端口**因人而异**」——端口是**每个用户自己**的事，产品不该把它焊死。

## 之前的状态（这就是病因）

同一个"平台地址/端口"在 **5 处各写一套**，而且**默认值还不一致**：

| 位置 | 原来怎么取 |
|---|---|
| `cli.py` | `DEFAULT_PORT = 9103` |
| `platform/server.py`（原） | 读 `AC_PORT`，缺省写死 9103 |
| `platform/server.py::_platform_base_url()`（原） | 读 `AC_PORT`，缺省写死 18920 ← **同一个文件里两套** |
| `platform/bridge_template.py::_platform_url()` | `127.0.0.1:18920` 硬编码兜底 |
| `platform/verify_sandbox.py::_platform_url()` | `127.0.0.1:18920` 硬编码兜底 |
| `tools/watchdog.py` | `DEFAULT_PORT = 18920` |

⇒ 症状：用户按文档开一个非 18920 的端口，平台自己生成的告警、桥模板、验证间提示里
仍写着 `127.0.0.1:18920` —— **同类口径写了六遍，必然是错的**（规则 20）。

## 现在的口径（一处）

- **优先级**：`AC_PORT`（环境变量，**用户自己设的**）> `DEFAULT_PORT`（只是一个**可用即可**的默认）。
- **谁能改**：`AC_PORT`、命令行 `--port`（命令行最优先，见 `cli.py`）。
- **对外的文档/界面**：**不许**写死端口 —— 用相对路径（界面）或 `<你实际的端口>`（文档）。
"""
from __future__ import annotations

import os

#: 默认端口。**它只是一个"开箱能起来"的默认值，不是产品事实** ——
#:  用户用 `AC_PORT` 或 `--port` 改掉它，全链路（桥模板/验证间/告警/说明书）都必须跟着变。
DEFAULT_PORT = 18920

#: 端口环境变量名（唯一入口；别在别处再读别的名字）
ENV_PORT = "AC_PORT"

#: 平台基址环境变量（外部服务用它回连平台时用；可选）
ENV_BASE_URL = "AC4_BASE_URL"
ENV_BASE_URL_ALT = "AC_BASE_URL"

#: 绑定的主机（环回；`0.0.0.0` 是平台自身的监听口径，见 server.py）
HOST = "127.0.0.1"

#: 监听地址环境变量名（**唯一入口**）。默认 `0.0.0.0`（容器/局域网可达）；
#: `0.0.0.0` 且未配 `AC4_TOKEN` 时平台**只允许本机来源**（靠 Origin/Referer 检查）。
ENV_HOST = "AC_HOST"
DEFAULT_HOST = "0.0.0.0"


def resolve_port(explicit: "int|str|None" = None) -> int:
    """**端口的唯一解析点**：命令行 > `AC_PORT` > `DEFAULT_PORT`。取不到就退回默认（不抛）。

    ⚠️ **不接受 `0`**：本函数解析的是"**别人要连的那个地址**"（桥模板/告警/自检都拿它拼 URL），
    而 `0` 只在"要内核临时挑端口"时对**绑定**有意义。允许 0 会让生成出来的桥脚本
    写着 `http://<host>:0` —— 看起来像配置成功，实际一个也连不上。
    需要临时端口的地方（测试 / 容器探测）请直接 `bind((host, 0))` 并回读端口。
    """
    for v in (explicit, os.environ.get(ENV_PORT)):
        if v in (None, ""):
            continue
        try:
            p = int(v)
            if 1 <= p <= 65535:
                return p
        except (TypeError, ValueError):
            continue
    return DEFAULT_PORT


def resolve_host(explicit: "str|None" = None) -> str:
    """**监听地址的唯一解析点**：命令行 `--host` > `AC_HOST` > `DEFAULT_HOST`。

    ⚠️ 2026-10-14 修：此前 `server.py` 直接 `uvicorn.run(host="0.0.0.0")`，
    而 `env_spec` 里 `AC_HOST` 登记为"部署核心变量"并写进了 `.env.example` 与 README ——
    **文档承诺了一个改不动的开关**（实测：`AC_HOST=127.0.0.1` 仍绑 0.0.0.0）。
    这正是本模块存在的理由（同类口径只能有一处）。
    """
    for v in (explicit, os.environ.get(ENV_HOST)):
        h = (v or "").strip()
        if h:
            return h
    return DEFAULT_HOST


def base_url(explicit_port: "int|str|None" = None) -> str:
    """平台基址（`http://host:port`）。

    ⚠️ **不要**把它写进对外文档 —— 那是"本机网址"；文档里请写 `<平台地址>` 占位。
    """
    return f"http://{HOST}:{resolve_port(explicit_port)}"


def base_url_from_env(default: str = "") -> str:
    """外部服务（A2A/gRPC/MCP 桥等）回连平台用的基址。

    优先级：`AC4_BASE_URL` > `AC_BASE_URL` > 按 `AC_PORT` 推 > `default`。
    """
    for k in (ENV_BASE_URL, ENV_BASE_URL_ALT):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v.rstrip("/")
    if os.environ.get(ENV_PORT):
        return base_url()
    return default or base_url()
