# -*- coding: utf-8 -*-
"""状态落盘（V90 抽取）：**原子写 + 逐文件容错 + 两道硬闸**，纯函数、可单测。

## 为什么单独一层（而不是把 `save_state()` 原样搬走）

`python -m agent_community.platform.server` 时存在**两份模块对象**（`__main__` 与包模块），
模块级全局（`tasks` / `workshops` / `DATA_DIR` …）两边**互不可见**。
如果本模块去 `from . import server` 读全局，运行中的 `__main__` 副本写盘时会拿到包模块那份**空的**全局
⇒ 正好复现 2026-10-23 把数据写成 `{}` 的事故。

⇒ 本模块**不认识任何全局**：只接受"成品"（payload dict + 数据目录），判定与写盘都在这里，
组装 payload 与"所有权"状态留在 `server.py`（它必须和那些全局同一个模块对象）。

## 两道硬闸（事故换来的）

1. **降级实例不许写盘**：另一个实例持有数据目录时，写了必然互相覆盖；
2. **没加载过状态的进程不许用空内存覆盖磁盘上已有的数据**
   （`AC_STATE_ALLOW_EMPTY=1` 是故意清库的口子）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: 状态文件名（与 `state_backup.STATE_FILES` 同一张表；两边不一致就是"备份漏文件"）
STATE_NAMES = ("tasks.json", "rooms.json", "task_to_room.json", "harnesses.json",
               "workshops.json", "interjects.json", "state_machine.json")


def allow_empty_write() -> bool:
    """故意清库的口子（首次运行 / 手工重置）。设 `AC_STATE_ALLOW_EMPTY=1` 才放行。"""
    return (os.environ.get("AC_STATE_ALLOW_EMPTY") or "0").strip() == "1"


def atomic_write_text(path: Path, text: str) -> None:
    """临时文件 + `os.replace` 原子替换：写盘中断也不会留下半截 JSON。"""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> tuple:
    """容错读：返回 `(数据, 错误说明)`。文件不存在 → `(None, "")`（不是错误）。"""
    p = Path(path)
    if not p.is_file():
        return None, ""
    try:
        return json.loads(p.read_text(encoding="utf-8")), ""
    except Exception as e:                                   # noqa: BLE001 — 读不了要如实报，别静默当空
        return None, "%s: %s" % (type(e).__name__, e)


def disk_has_data(data_dir: Path, names=("workshops.json", "harnesses.json", "tasks.json")) -> bool:
    """磁盘上的状态文件里**有没有东西**（只看几个大的，够判断"是不是全空"）。"""
    for name in names:
        j, err = read_json(Path(data_dir) / name)
        if err:
            print("[persist] 判断磁盘状态时读不了 %s（%s）" % (name, err), flush=True)
            continue
        if isinstance(j, dict) and j:
            return True
        if isinstance(j, list) and j:
            return True
    return False


def block_reason(*, empty_now: bool, owned: bool, degraded: bool,
                 disk_has_data: bool, allow_empty: bool) -> str:
    """该不该**拒写**：返回 `""`＝放行，否则返回给用户看的原因（纯函数，好测）。"""
    if degraded:
        return ("[persist][危险] 本实例是**降级实例**（另一个实例正持有数据目录）⇒ 拒绝写盘："
                "写了会和它互相覆盖，可能把真实数据写成空的。要落盘请先停掉那个实例。")
    if empty_now and not owned and not allow_empty and disk_has_data:
        return ("[persist][危险] 本进程**没有加载过状态**（内存是空的），而磁盘上明明有数据 ⇒ "
                "拒绝用空状态覆盖（2026-10-23 就是这样把 dev 数据清空的）。"
                "真是要清库？设 AC_STATE_ALLOW_EMPTY=1 再跑。")
    return ""


def _payload_empty(data) -> bool:
    """这份状态算不算"空"（dict/list/str 长度为 0；None 也算空）。"""
    if data is None:
        return True
    if isinstance(data, (dict, list, str)):
        return len(data) == 0
    return False


def _disk_file_nonempty(path: Path) -> bool:
    """磁盘上这份文件**有内容**吗（不存在/读不出/坏 JSON 都算"没有"——不该因此拒写新内容）。"""
    try:
        if not path.is_file():
            return False
        return not _payload_empty(json.loads(path.read_text(encoding="utf-8")))
    except Exception:                                        # noqa: BLE001 — 坏文件当空处理，下面照写
        return False


def write_items(items, data_dir: Path, *, allow_empty: bool = False) -> tuple:
    """**逐项**序列化 + 原子写盘（`items` 是 `[(文件名, () -> 数据)]`）。

    ★为什么是"惰性可调用"而不是现成 dict（2026-10-23 抽取时踩到）：
    序列化**必须**和写盘一起被单独的 try 包住 —— 实测里有测试替身（假 session）的
    `info.model_dump()` 会抛 `AttributeError`。如果先在调用方把整个 dict 组装好，
    这一个坏对象就会**把全部状态的落盘都打断**（正好是历史事故里最怕的那种"全废"）。
    ⇒ 逐项：这一项坏，只记它失败，其它照写。

    🚨**第三道闸（逐文件，2026-10-24 事故后加）**：某个进程可能只认识**一部分**状态
    （例如外部 agent 的诊断脚本只登记了 harness，`workshops` 在它内存里是空的），
    无脑写盘就会把别人的 `workshops.json` 覆盖成 `{}` —— 实测就这么丢过数据。
    规则：**新内容是空的、而磁盘这份文件非空** ⇒ **跳过这份、并大声说清**；
    真要清库仍可显式传 `allow_empty=True`（`save_state` 侧认 `AC_STATE_ALLOW_EMPTY=1`）。

    返回 `(ok, failed_names)`；绝不抛异常。
    """
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    ok, failed = True, []
    for name, build in items:
        try:
            data = build()
            if not allow_empty and _payload_empty(data) and _disk_file_nonempty(d / name):
                print("[persist][危险] %s：本进程这份状态是空的，而磁盘上非空 ⇒ **跳过这份、不覆盖**"
                      "（跨进程/诊断脚本常见；真要清库请设 AC_STATE_ALLOW_EMPTY=1）" % name, flush=True)
                continue
            atomic_write_text(d / name, json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:                               # noqa: BLE001 — 逐项报，别让一个坏对象拖垮全部
            ok = False
            failed.append(name)
            print("[persist] 序列化/写 %s 失败: %s" % (name, e), flush=True)
            import traceback as _tb
            _tb.print_exc()
    return ok, failed


def write_payload(payload: dict, data_dir: Path) -> tuple:
    """把 `{文件名: 可 JSON 化的数据}` 逐文件原子写盘（`write_items` 的便利包装）。"""
    return write_items([(k, (lambda v=v: v)) for k, v in payload.items()], data_dir)
