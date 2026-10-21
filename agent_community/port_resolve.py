# -*- coding: utf-8 -*-
"""port_resolve：**"平台在哪个端口"的唯一解析点**（CLI 与启动器共用）。

## 为什么单独一层

端口这件事此前只有 `platform_address.resolve_port()`（`AC_PORT` > 默认值），
但 **CLI** 还要多判一层："上一次运行留下的 PID 记录里的端口，**只有真的探得通才认**"
（2026-10-20 修过的真缺陷：过期 PID 文件让 CLI 永远报"服务未运行"）。

启动器（`tools/launcher.py`）需要**完全一样**的判断，否则会出现
"CLI 认为服务在 A 端口、启动器以为在 B 端口"——同一个问题两份口径。

⇒ 本模块是那**一份口径**，且**全程标准库**（启动器要在"环境坏掉"时也能跑）。

## 顺序（唯一）

`AC_PORT`（用户明确指定）> **活着的** PID 记录端口 > `platform_address.resolve_port()` 默认值

⚠️ 探不到（超时/非 200/连不上）就**不采用** PID 里的端口，并顺手把过期 PID 文件删掉——
不留"看起来成功"的死状态。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

#: PID 记录文件（与 `agent_community/cli.py` 同一处；那边从这里取，不再自己拼）
PID_DIR = Path.home() / ".agent_community"
PID_FILE = PID_DIR / "server.pid"


def resolve_port(explicit=None) -> int:
    """端口默认值解析（转调 `platform_address.resolve_port`，一处真相）。"""
    from .platform_address import resolve_port as _resolve

    return int(_resolve(explicit))


def read_pid(pid_file=None) -> dict | None:
    """读 PID 记录（坏文件当没有，不抛）。"""
    path = Path(pid_file) if pid_file else PID_FILE
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:                                        # noqa: BLE001
        return None


def probe(port: int, timeout: float = 2.0) -> bool:
    """这个端口上有没有**在跑的平台**（真打 `/api/status`，200 才算）。只探活，不改状态。"""
    try:
        url = "http://127.0.0.1:%d/api/status" % int(port)
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return int(getattr(r, "status", 0) or r.getcode() or 0) == 200
    except Exception:                                        # noqa: BLE001
        return False


def port_listening(port: int, timeout: float = 0.6) -> bool:
    """端口上**有没有人在听**（不管是不是平台）。用于把"被别的程序占着"和"平台没起来"分开。"""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:                                        # noqa: BLE001
        return False
    finally:
        try:
            s.close()
        except Exception:                                    # noqa: BLE001
            pass


def cli_port(pid_file=None) -> int:
    """CLI / 启动器该用哪个端口（**唯一口径**）。

    `AC_PORT` 设了就听它的；否则看 PID 记录里的端口**探得通才采用**；
    否则回到 `resolve_port()` 的默认值。过期 PID 文件顺便清掉。
    """
    explicit = str(os.environ.get("AC_PORT") or "").strip()
    if explicit:
        return resolve_port()
    default_port = resolve_port()
    data = read_pid(pid_file) or {}
    try:
        recorded = int(data.get("port") or 0)
    except (TypeError, ValueError):
        recorded = 0
    if recorded and recorded != default_port:
        if probe(recorded):
            return recorded
        _drop_stale_pid(pid_file)
    return default_port


def _drop_stale_pid(pid_file=None) -> None:
    """删掉过期 PID 文件（探不到 ⇒ 那条记录已经没意义）。失败不抛。"""
    path = Path(pid_file) if pid_file else PID_FILE
    try:
        if path.exists():
            path.unlink()
    except Exception:                                        # noqa: BLE001
        pass
