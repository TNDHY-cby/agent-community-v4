# -*- coding: utf-8 -*-
"""中转消息的处理钩子（V67 M2）—— **收到就要办，不能只落收件箱**。

处理两类消息（其余只落收件箱给人看）：

| kind | 谁收到 | 要办什么 |
|---|---|---|
| `task.assign` | 执行方 | **建一个工作间**（等同"接到一趟外来的活"）+ 回一条 `task.ack` |
| `task.result` | 发起方 | 更新本侧那条外部任务（状态/时间线/产物清单） |

## 为什么必须"真办"

M1 的收件箱是"存下来给人看"。派活不一样：**发起方以为对方接活了**，
如果这边只存一条消息，就是**看起来成功**（准则⑤）。所以缺 title 这类坏消息必须
**让处理失败可见**（`handled.ok=false` + 审计 `relay.handle.failed`）。

## 与 a2a 的关系

两条路（中转发消息 / a2a 直投）最终都落到**同一张外部任务表**（`peer_tasks`），
只差 `via` 字段 —— 界面与看板不用写两套。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import peer_tasks
from .audit import audit_log as _audit_log


def _self_base() -> str:
    """本机端口的自调用地址（复用现有端点 = 一处真相，不另写一套建间逻辑）。

    ⚠️ 端口从**环境变量 `AC_BOUND_PORT`** 读（`server.py` 在 uvicorn 启动前写入）。
    为什么不用模块全局：`python -m agent_community.platform.server` 时
    `__main__` 与 `agent_community.platform.server` 是**两个模块对象**，
    在 `__main__` 里赋值全局，这边读到的永远是 0（实测踩过）。
    """
    import os as _os
    port = int((_os.environ.get("AC_BOUND_PORT") or "0").strip() or 0)
    if not port:
        # 兜底：只在测试/嵌入式场景下可能走到（那时自己知道端口）
        from . import server as _srv
        port = int(getattr(_srv, "BOUND_PORT", 0) or 0)
    if not port:
        return ""
    return "http://127.0.0.1:%d" % port


def _post_self(path: str, body: dict, timeout: float = 30.0) -> tuple[int, dict]:
    base = _self_base()
    if not base:
        return 0, {"error": "拿不到本机端口（BOUND_PORT 未设置），无法在本机建工作间"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(base + path, data=data, method="POST",
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                    # noqa: BLE001
            return e.code, {"error": raw[:200]}
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}


def _handle_assign(msg: dict) -> tuple[bool, str]:
    """收到派活 ⇒ 真建工作间 + 回 ack。"""
    from . import relay_client
    p = msg.get("payload") or {}
    title = str(p.get("title") or "").strip()
    if not title:
        # 坏消息：必须有痕迹（否则发起方以为派成了）
        return False, "task.assign 缺 title，没建工作间"
    frm = str(msg.get("from") or "")
    hall = str(p.get("hall") or "").strip() or (
        "【外来派活】来自终端：%s\n\n任务：%s\n" % (frm or "?", title))
    st, body = _post_self("/api/workshop", {"name": title[:40], "hall_content": hall})
    if st != 200:
        return False, "建工作间失败（HTTP %s）：%s" % (st, str((body or {}).get("error") or "")[:120])
    ws = str((body or {}).get("workshop_id") or "")
    # 回执 ack：告诉发起方"我接活了，我的工作间号是 X"
    if frm:
        relay_client.send(frm, {"pt_id": p.get("pt_id") or "", "accepted": True,
                                "workshop_id": ws, "title": title},
                          kind="task.ack")
    _audit_log.record("relay.handle.assign", actor="relay", target=ws or title,
                      detail="来自 %s 的派活已建工作间" % (frm or "?"))
    return True, "已建工作间 %s" % (ws or "?")


def _handle_result(msg: dict) -> tuple[bool, str]:
    """收到回执 ⇒ 更新本侧那条外部任务（**不用再点"刷新回执"**）。"""
    p = msg.get("payload") or {}
    pt_id = str(p.get("pt_id") or "").strip()
    if not pt_id:
        return False, "task.result 缺 pt_id，无法对上本地外部任务"
    rec = peer_tasks.get(pt_id)
    if not rec:
        return False, "本地没有这条外部任务（pt_id=%s）" % pt_id
    status = str(p.get("status") or "").strip() or "working"
    info = {"status": status,
            "artifacts": p.get("artifacts") or [],
            "metadata": {"note": "经中转回执"}}
    updated = peer_tasks.record_check(pt_id, True, info=info)
    _audit_log.record("relay.handle.result", actor="relay", target=pt_id,
                      detail="经中转回执：%s" % status)
    return True, "外部任务已更新为 %s" % (updated or {}).get("status", status)


def handle(msg: dict) -> tuple[bool, str]:
    """**中转消息的总入口**（由 `relay_client` 在轮询线程里串行调用）。"""
    kind = str((msg or {}).get("kind") or "")
    try:
        if kind == "task.assign":
            return _handle_assign(msg)
        if kind == "task.result":
            return _handle_result(msg)
        return True, "非需处理类型（只落收件箱）"
    except Exception as e:                                   # noqa: BLE001
        _audit_log.record("relay.handle.failed", actor="relay", target=kind,
                          detail="%s: %s" % (type(e).__name__, str(e)[:160]))
        return False, "%s: %s" % (type(e).__name__, str(e)[:160])
