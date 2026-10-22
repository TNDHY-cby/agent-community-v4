# -*- coding: utf-8 -*-
"""platform/reply_probe：「agent 能否回话」探针（`V74`，待拍板稿的落地实现）。

## 它解决的真事

平台此前只能证明桥配置这一段（`bridge_template_verified[].verified_scope` 自己写着
"harness 侧是否运行该桥、agent 能否回话，本次未验证"）。于是界面显示"接入成功"，
却没有任何证据说明"发消息给它，它会回话"——老板最恨的"看起来成功"就在这里。

## 三条硬约束（`V74` §3，写进代码注释防止被后人改掉）

1. 不烧钱：`AC_REPLY_PROBE_ENABLED` 默认 `0`（关）；一次只发 1 条；超时可配。
2. 不假装：投递走和真实派活同一条路（`file_poll` 写 inbox / 其它走 `pending_bridge_tests`
   队列，与 `/api/harness/bridge-test` 完全一致），不另开捷径。
3. 不绑定 harness 类型：人工中转型（`clipboard` / `manual`）没有自动回报通道 ⇒
   只接受人工确认（`source=human_confirm`），尝试自动验证必须被拒并说清原因。

## 证据长什么样（落 `info.metadata["reply_probe"]`）

```json
{"at": "...", "mode": "auto|human_confirm", "state": "ok|failed",
 "source": "auto|human_confirm", "probe_id": "…", "evidence": {"echo": "…", "channel": "…"},
 "why": "失败时的人话原因"}
```

没有这条记录 ⇒ `agent_can_reply` 仍然 `unverified`（`harness_capability` 里读的就是它）。
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime

#: 平台发出、尚未回报的探针：harness_id → {probe_id, at, expire_ts, reported, channel}
_INFLIGHT: dict = {}

#: 人工中转型（没有自动回报通道，只能人工确认）
MANUAL_METHODS = ("clipboard", "manual", "human", "")


def enabled() -> bool:
    return (os.environ.get("AC_REPLY_PROBE_ENABLED", "0") or "0").strip() in ("1", "true", "yes", "on")


def timeout_seconds() -> int:
    try:
        return max(10, int(os.environ.get("AC_REPLY_PROBE_TIMEOUT", "120")))
    except Exception:                                        # noqa: BLE001
        return 120


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_manual(wakeup_method: str) -> bool:
    """人工中转型：没有叫醒命令、也没有自动回执（本机 8 条记录里 6 条是这种）。"""
    return (wakeup_method or "").strip().lower() in MANUAL_METHODS


def status_of(info) -> dict:
    """读这条 harness 的回话证据（没有就如实说没有）。"""
    md = getattr(info, "metadata", None) or {}
    rec = md.get("reply_probe")
    return dict(rec) if isinstance(rec, dict) else {}


def _persist(sess, record: dict) -> None:
    """同时写 session.metadata 与 info.metadata（后者才落盘，照 `bridge_test` 的老做法）。"""
    try:
        sess.metadata = dict(getattr(sess, "metadata", None) or {})
        sess.metadata["reply_probe"] = record
    except Exception:                                        # noqa: BLE001 — 替身对象没有这个属性
        pass
    sess.info.metadata = dict(getattr(sess.info, "metadata", None) or {})
    sess.info.metadata["reply_probe"] = record


def start(sess) -> dict:
    """发起一次探针。返回 `{ok, probe_id, channel, how_to…}` 或 `{ok: False, error, how_to_fix}`。"""
    # 投递走和真实桥测试同一条路：这三个辅助函数在 `server.py`（`harness_bridge` 也是从那儿拿的）
    from .routers.harness_bridge import _wake_channel
    from .server import _file_poll_send, _harness_wakeup_method, _pending_push
    from .state import pending_bridge_tests

    hid = str(getattr(sess.info, "harness_id", "") or "")
    method = _harness_wakeup_method(hid)
    if is_manual(method):
        return {"ok": False,
                "error": "这个员工是人工中转型（没有自动回报通道），平台没法自动验证它回不回话",
                "how_to_fix": "用 mode=human_confirm 记一条人工确认：你发给它、它回了，就点确认"}

    probe_id = "rp-" + uuid.uuid4().hex[:10]
    payload = {
        "type": "reply_probe",
        "harness_id": hid,
        "probe_id": probe_id,
        "sent_at": _now(),
        "message": "平台回话探针：收到后请回一条带 probe_id 的回执",
        "report_endpoint": "/api/harness/reply-probe-result",
    }
    channel = _wake_channel(method)
    if method == "file_poll":
        if not _file_poll_send(hid, payload):
            return {"ok": False,
                    "error": "写 inbox 失败（wakeup_dir 不可写）",
                    "how_to_fix": "检查该员工的 wakeup_dir 是否存在且可写；改完再发一次"}
    else:
        _pending_push(pending_bridge_tests, hid, payload)

    expire_ts = time.time() + timeout_seconds()
    _INFLIGHT[hid] = {"probe_id": probe_id, "at": _now(),
                      "expire_ts": expire_ts,
                      "reported": False, "channel": channel}
    _persist(sess, {"at": _now(), "mode": "auto", "state": "pending", "source": "auto",
                    "probe_id": probe_id, "channel": channel,
                    "expire_at": datetime.fromtimestamp(expire_ts).isoformat(timespec="seconds")})
    return {"ok": True, "probe_id": probe_id, "channel": channel,
            "wait_seconds": timeout_seconds(),
            "report_endpoint": payload["report_endpoint"]}


def record_result(sess, probe_id: str, echo: str = "") -> dict:
    """harness 侧回报（必须带回本次 probe_id，否则不算数）。"""
    hid = str(getattr(sess.info, "harness_id", "") or "")
    inflight = _INFLIGHT.get(hid) or {}
    body_id = str(probe_id or "").strip()
    if not inflight or inflight.get("probe_id") != body_id:
        return {"ok": False, "status_code": 403,
                "error": "探针回报不匹配：probe_id 与平台发出的不一致（请先由平台发起探针）"}
    if inflight.get("reported"):
        return {"ok": False, "status_code": 403, "error": "该探针已回报过，拒绝重复提交"}
    if time.time() > float(inflight.get("expire_ts", 0)):
        _INFLIGHT.pop(hid, None)
        return {"ok": False, "status_code": 403, "error": "探针回报已过期，请重新发起"}

    inflight["reported"] = True
    record = {"at": _now(), "mode": "auto", "state": "ok", "source": "auto",
              "probe_id": body_id, "channel": inflight.get("channel", ""),
              "evidence": {"echo": str(echo or "")[:200]}}
    _persist(sess, record)
    return {"ok": True, "harness_id": hid, "state": "ok"}


def human_confirm(sess, note: str = "", by: str = "user") -> dict:
    """人工确认（只有人工中转型才该用；自动型也能用，但证据来源会标 human_confirm）。"""
    record = {"at": _now(), "mode": "human_confirm", "state": "ok", "source": "human_confirm",
              "confirmed_by": by, "evidence": {"note": str(note or "")[:200]}}
    _persist(sess, record)
    return {"ok": True, "state": "ok", "source": "human_confirm"}


def fail(sess, why: str) -> dict:
    """如实记失败（超时 / 桥无响应 …），不留"看起来成功"。"""
    record = {"at": _now(), "mode": "auto", "state": "failed", "source": "auto",
              "why": str(why or "未知原因")[:200]}
    _persist(sess, record)
    return {"ok": False, "state": "failed", "why": record["why"]}


def pending_expired(record: dict) -> bool:
    """这条 `pending` 记录是不是已经超时（纯函数，不写任何东西）。

    ★V74 §4 要求"超时上限可配"；超时就必须如实记失败，
    否则永远停在 `pending` — 那正是"看起来还在等、其实不会有了"的假状态。
    """
    if not isinstance(record, dict) or record.get("state") != "pending":
        return False
    exp = str(record.get("expire_at") or "")
    if not exp:
        return False
    try:
        return datetime.now() > datetime.fromisoformat(exp)
    except Exception:                                        # noqa: BLE001 — 解析不了就不判断（不当失败）
        return False


def clear(hid: str) -> None:
    _INFLIGHT.pop(str(hid or ""), None)


def staleness_hours() -> float:
    """探针结果多久算过期（小时；V81 §3 #1 借鉴的 24h 口径）。"""
    try:
        return max(1.0, float(os.environ.get("AC_PROBE_STALENESS_HOURS", "24")))
    except Exception:                                        # noqa: BLE001
        return 24.0


def age_hours(record: dict) -> float:
    """这条记录存了多久（小时）。时间解析不了返回 -1（表示"算不出"）。"""
    at = str((record or {}).get("at") or "")
    if not at:
        return -1.0
    try:
        return (datetime.now() - datetime.fromisoformat(at)).total_seconds() / 3600.0
    except Exception:                                        # noqa: BLE001
        return -1.0


def is_stale(record: dict) -> bool:
    """`ok` 记录是否已过期。算不出年龄时**不判过期**（宁可显示 ok，也不误报）。"""
    if not isinstance(record, dict) or record.get("state") != "ok":
        return False
    age = age_hours(record)
    return age >= 0 and age > staleness_hours()
