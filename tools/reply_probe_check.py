# -*- coding: utf-8 -*-
"""tools/reply_probe_check.py —— `V74` 回话探针的**判据**（真跑，六格）。

```
python tools/reply_probe_check.py
```

判据（每格都必须真跑出来，不许"看起来过"）：
1. **默认关**：`AC_REPLY_PROBE_ENABLED` 未设 ⇒ 发起探针必须 409 并**说清怎么开**；
2. **人工中转型不许硬撑自动验证**：`wakeup_method=clipboard` 发 auto ⇒ 400 + 人话原因；
3. **人工确认可落证据**：`mode=human_confirm` ⇒ state=ok，且 `source=human_confirm`（与自动区分开）；
4. **nonce 校验**：回报的 `probe_id` 不对 ⇒ 403；**对的 ⇒ ok**，落 `source=auto`；
5. **没证据不装**：没验过的 harness，`capability` 的 `agent_can_reply` 必须仍是 `unverified`；
6. **验过之后说得清**：验完 `capability.agent_can_reply.state == ok`，并带 evidence/scope。

用法注意：本工具会把 `AC_DATA_DIR` 指到临时目录（不污染仓库），进程内跑，不需要先起服务。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="ac_rp_")
os.environ["AC_DATA_DIR"] = TMP
os.environ.pop("AC_REPLY_PROBE_ENABLED", None)

results: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok))
    print("%s %-52s %s" % ("[OK  ]" if ok else "[FAIL]", label, detail[:90]))


def client():
    from fastapi.testclient import TestClient

    from agent_community.platform.server import app

    return TestClient(app, client=("127.0.0.1", 54321))


def make_session(hid: str, wakeup_method: str, wakeup_dir: str = ""):
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.protocol import HarnessInfo

    info = HarnessInfo(harness_id=hid, harness_name=hid, harness_type="custom",
                       wakeup_method=wakeup_method, wakeup_dir=wakeup_dir,
                       ai={"model_name": "m", "provider": "p"})

    class _Sess:
        def __init__(self, i):
            self.info = i
            self.metadata = {}

    sess = _Sess(info)
    harness_manager.sessions[hid] = sess
    return sess


def main() -> int:
    c = client()

    # ① 默认关
    make_session("rp-manual", "clipboard")
    r = c.post("/api/harness/rp-manual/probe-reply", json={"mode": "auto"})
    check("① 默认关：409 且告诉怎么开", r.status_code == 409 and "AC_REPLY_PROBE_ENABLED" in r.text,
          "%s %s" % (r.status_code, r.text[:60]))

    os.environ["AC_REPLY_PROBE_ENABLED"] = "1"

    # ② 人工中转型不许硬撑自动
    r = c.post("/api/harness/rp-manual/probe-reply", json={"mode": "auto"})
    check("② 人工中转型 auto ⇒ 400 + 人话", r.status_code == 400 and "人工中转" in r.text,
          "%s %s" % (r.status_code, r.text[:70]))

    # ③ 人工确认
    r = c.post("/api/harness/rp-manual/probe-reply",
               json={"mode": "human_confirm", "note": "我发了，它回了"})
    cap = c.get("/api/harness/rp-manual/capability").json()
    seg = cap["segments"]["agent_can_reply"]
    check("③ human_confirm 落证据且标 source", r.status_code == 200 and seg["state"] == "ok"
          and seg.get("source") == "human_confirm",
          "state=%s source=%s" % (seg["state"], seg.get("source")))

    # ④ nonce 校验（file_poll 自动型）
    inbox = Path(TMP) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    make_session("rp-auto", "file_poll", wakeup_dir=str(inbox))
    r = c.post("/api/harness/rp-auto/probe-reply", json={"mode": "auto"})
    ok_start = r.status_code == 200 and r.json().get("probe_id")
    pid = (r.json() or {}).get("probe_id", "")
    check("④a 自动型发起成功且拿到 probe_id", bool(ok_start), str(r.json())[:70])

    bad = c.post("/api/harness/reply-probe-result",
                 json={"harness_id": "rp-auto", "probe_id": "rp-wrong", "echo": "x"})
    good = c.post("/api/harness/reply-probe-result",
                  json={"harness_id": "rp-auto", "probe_id": pid, "echo": "回执"})
    cap2 = c.get("/api/harness/rp-auto/capability").json()["segments"]["agent_can_reply"]
    check("④b 错 nonce 拒 403 / 对 nonce 通过", bad.status_code == 403 and good.status_code == 200
          and cap2["state"] == "ok" and cap2.get("source") == "auto",
          "bad=%s good=%s state=%s" % (bad.status_code, good.status_code, cap2["state"]))

    # ⑤ 没证据不装
    make_session("rp-none", "file_poll", wakeup_dir=str(inbox))
    cap3 = c.get("/api/harness/rp-none/capability").json()["segments"]["agent_can_reply"]
    check("⑤ 没验过 ⇒ 仍 unverified", cap3["state"] == "unverified", cap3["state"])

    # ⑥ 验过之后说得清（带 evidence / 范围）
    ok6 = cap2["state"] == "ok" and "evidence" in cap2 and "evidence_scope" in cap2
    check("⑥ 验过之后带 evidence 与范围原话", ok6, str(cap2.get("evidence_scope"))[:70])

    # ⑦ 超时必须记失败（V74 §4：超时上限可配；超时不许永远 pending）
    import time as _t

    from agent_community.platform import reply_probe as _rp

    make_session("rp-timeout", "file_poll", wakeup_dir=str(inbox))
    r = c.post("/api/harness/rp-timeout/probe-reply", json={"mode": "auto"})
    ok7a = r.status_code == 200 and r.json().get("probe_id")
    if ok7a:                                     # 把"等回执"的过期时间拨到过去 = 已超时
        _rp._INFLIGHT["rp-timeout"]["expire_ts"] = _t.time() - 1
        sess_rec = c.get("/api/harness/rp-timeout/probe-reply").json()
        # 记录里的 expire_at 也要跟着过期，capability 才会判成失败
        from agent_community.platform.harness_adapter import harness_manager

        info = harness_manager.sessions["rp-timeout"].info
        info.metadata["reply_probe"] = dict(info.metadata.get("reply_probe") or {},
                                            expire_at="2000-01-01T00:00:00")
        cap7 = c.get("/api/harness/rp-timeout/capability").json()["segments"]["agent_can_reply"]
        ok7 = cap7["state"] == "failed" and "超时" in (cap7.get("why") or "")
        check("⑦ 超时 ⇒ failed 且写明超时", bool(ok7), "%s %s" % (cap7.get("state"), (cap7.get("why") or "")[:50]))
    else:
        check("⑦ 超时 ⇒ failed 且写明超时", False, "发起失败，无法验超时")

    print("\n结果：%d/%d 格通过" % (sum(1 for _, ok in results if ok), len(results)))
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
