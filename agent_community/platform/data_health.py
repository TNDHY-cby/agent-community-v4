# -*- coding: utf-8 -*-
"""**一处真相**：运行期元信息（数据健康摘要 + 降级实例标记）。

## 为什么单独一个模块（2026-10-23 踩到的双模块坑）

`python -m agent_community.platform.server` 会同时存在**两份模块对象**：
`__main__`（脚本本体）与 `agent_community.platform.server`（路由层 `from ..server import …` 拿到的那份）。
放在 server.py 里的模块级变量，两边**互不可见** ⇒ 启动时在 `__main__` 算好的摘要，
`/api/status`（走包模块）读出来永远是默认值（实测：日志里警告打了、接口却报 `false`）。

这两样东西必须**跨那两份模块共享**，所以放进这个独立模块：模块按名字缓存，
谁 import 都是同一个对象。

- `HEALTH`：启动时算一次的数据健康摘要（`load_state()` 写，`/api/status` / 诊断工具读）；
- `DEGRADED`：本实例是不是"降级实例"（另一个实例持有数据目录）—— `save_state()` 据此**拒写**。
"""
from __future__ import annotations

#: 数据健康摘要。口径：**只报事实**（"状态是空的、但目录里有历史痕迹"），不猜原因、不动数据。
HEALTH = {
    "empty_but_history": False,   # 状态空 + 目录里有历史痕迹 ⇒ 界面应显示横幅
    "state_empty": False,         # 状态本身是不是空的
    "workspace_dirs": 0,          # data/workshops/ 下有多少个工作区目录
    "audit_kb": 0,                # audit.json 有多大（KB）
    "checked_at": "",             # 什么时候算的（启动那一刻）
}

#: 本实例是不是降级实例（另一个实例持有数据目录）。降级实例**不许写盘**。
DEGRADED = False
#: 降级拒写的告警是否已经打过（每进程只打一次，别刷屏）
WARNED = False


def snapshot() -> dict:
    """给接口/工具用的只读快照。"""
    return dict(HEALTH)


def note(**kw) -> None:
    """更新健康摘要（`load_state()` 用）。只接受 HEALTH 里已有的键，防止野生字段。"""
    for k, v in kw.items():
        if k in HEALTH:
            HEALTH[k] = v
