#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**大厅先选组长，再建工作间**（`V142` / 老板 2026-11-09 定的流程）。

真跑：临时实例 + 真注册两个 harness（一个能当组长、一个不能）+ 真建间。

| # | 格 | 断言 |
|---|---|---|
| ① | 带**合格**组长建间 ⇒ 建间成功**且**名单里已有 `_lead` | `lead.ok`、`status=lead_in`、roster 有 `_lead` 槽位 |
| ② | 带**不合格**组长 ⇒ **拒绝且不建间** | 409 `NOT_LEAD_ELIGIBLE`，`/api/workshops` 数量不变 |
| ③ | 不带 `lead_harness_id` ⇒ **老三步不变** | 建间成功、`status` 不是 `lead_in`、roster 里没有 `_lead` |
| ④ | 不存在的 harness ⇒ 404 且不建间 | `NO_SUCH_HARNESS`，数量不变 |
| ⑤ | `lead-roster` 给了 `how_to_use`（前端不必猜第二步） | 响应里有 `how_to_use` 且提到 `POST /api/workshop`，且**仍不推荐** |
| ⑥ | **一处真相**：两条路装出来的 `_lead` 槽位字段一致 | 建间绑定 vs 事后 `POST …/lead`，槽位字段集合逐字相同 |

退出码：0＝全过；1＝有格没过；2＝起不来。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
PY = sys.executable

ROWS: list = []
GOOD = "示例Harness-LeadGood"
BAD = "示例Harness-WebOnly"


def check(label: str, ok: bool, detail: str = "") -> None:
    ROWS.append((label, bool(ok), detail))
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label,
                        ("　（" + detail + "）") if detail and not ok else ""))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body=None, timeout: float = 30):
    path = urllib.parse.quote(path, safe="/?=&:,.~@!$'()*+;")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(base + path, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), json.loads(raw or "{}")
        except ValueError:
            return int(e.code), {"raw": raw[:300]}


def _count(base: str) -> int:
    d = _http(base, "/api/workshops")[1] or {}
    return len(d.get("workshops") or d.get("items") or [])


def _lead_slot(base: str, wid: str) -> dict | None:
    r = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
    for m in (r.get("members") or []):
        if str((m or {}).get("slot_dir") or "") == "_lead":
            return m
    return None


def main() -> int:
    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_picklead_"))
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(tmpdir / "minds"), "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：大厅先选组长再建间（V142 · 真跑）")
    print("=" * 78)
    try:
        up = False
        for _ in range(80):
            try:
                up = _http(base, "/api/status", timeout=3)[0] == 200
            except (urllib.error.URLError, OSError):           # noqa: BLE001 — 起服期间连不上是预期
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2

        (tmpdir / "lead_inbox").mkdir(parents=True, exist_ok=True)
        # 能当组长：能写文件 + 有 a2a（protocol_call）
        _http(base, "/api/harness/register", {
            "harness_id": GOOD, "harness_name": GOOD, "harness_type": "custom",
            "wakeup_method": "a2a", "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        # 不能当组长：没有 fs_write
        _http(base, "/api/harness/register", {
            "harness_id": BAD, "harness_name": BAD, "harness_type": "custom",
            "wakeup_method": "file_poll", "wakeup_dir": str(tmpdir / "lead_inbox"),
            "tools": [], "ai": {"model_name": "示例模型"}})

        def mk(**kw):
            d = tmpdir / ("ws_" + str(int(time.time() * 1000)))
            (d / "_lead").mkdir(parents=True, exist_ok=True)
            (d / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
            body = {"name": "选组长判据间（示例）", "hall_content": "做一个记账工具",
                    "workshop_path": str(d)}
            body.update(kw)
            return _http(base, "/api/workshop", body)

        st1, b1 = mk(lead_harness_id=GOOD)
        wid1 = str((b1 or {}).get("workshop_id") or "")
        slot1 = _lead_slot(base, wid1) if wid1 else None
        check("① 带合格组长建间 ⇒ 成功且名单里已有 `_lead`",
              st1 == 200 and bool(wid1) and bool(slot1)
              and str((b1 or {}).get("lead", {}).get("status")) == "lead_in",
              "HTTP %s wid=%r slot=%r lead=%r"
              % (st1, wid1, slot1, (b1 or {}).get("lead")))

        n0 = _count(base)
        st2, b2 = mk(lead_harness_id=BAD)
        check("② 带不合格组长 ⇒ 拒绝且**不建间**",
              st2 in (400, 409) and (b2 or {}).get("code") == "NOT_LEAD_ELIGIBLE"
              and _count(base) == n0,
              "HTTP %s %s 工作间 %d→%d" % (st2, (b2 or {}).get("code"), n0, _count(base)))

        st3, b3 = mk()
        wid3 = str((b3 or {}).get("workshop_id") or "")
        check("③ 不带 lead_harness_id ⇒ 老三步不变（没有 _lead、status 不是 lead_in）",
              st3 == 200 and bool(wid3) and _lead_slot(base, wid3) is None
              and str(((b3 or {}).get("workshop") or {}).get("status") or "") != "lead_in",
              "HTTP %s wid=%r slot=%r" % (st3, wid3, _lead_slot(base, wid3)))

        n1 = _count(base)
        st4, b4 = mk(lead_harness_id="no-such-harness-xyz")
        check("④ 不存在的 harness ⇒ 404 且不建间",
              st4 in (400, 404) and (b4 or {}).get("code") == "NO_SUCH_HARNESS"
              and _count(base) == n1,
              "HTTP %s %s 工作间 %d→%d" % (st4, (b4 or {}).get("code"), n1, _count(base)))

        st5, b5 = _http(base, "/api/lead-roster")
        how = str((b5 or {}).get("how_to_use") or "")
        blob = json.dumps(b5, ensure_ascii=False).lower()
        check("⑤ lead-roster 给了 how_to_use（且仍不推荐）",
              st5 == 200 and "POST /api/workshop" in how and "lead_harness_id" in how
              and "不排序不推荐" in str((b5 or {}).get("pick_policy") or "")
              and "score" not in blob,
              "how_to_use=%r" % how[:120])

        # ⑥ 一处真相：老路径（建完再选）与建间绑定，装出来的槽位字段一致
        st6, b6 = mk()
        wid6 = str((b6 or {}).get("workshop_id") or "")
        st6b, b6b = _http(base, "/api/workshop/%s/lead" % wid6, {"harness_id": GOOD})
        slot6 = _lead_slot(base, wid6)
        keys1 = set((slot1 or {}).keys())
        keys6 = set((slot6 or {}).keys())
        check("⑥ 一处真相：两条路装出来的 `_lead` 槽位字段集合一致",
              st6b == 200 and bool(slot1) and bool(slot6) and keys1 == keys6,
              "建间绑定=%r / 事后选=%r 差=%r" % (sorted(keys1), sorted(keys6),
                                                 sorted(keys1 ^ keys6)))
        # ⑦⑧ V142 补丁：选完组长要**把桥交到它手里 + 唤醒**（老板 2026-11-09 追加）
        h = ((b1 or {}).get("lead") or {}).get("handoff") or {}   # 建间响应里 handoff 在 lead 下面
        kit = h.get("kit") or {}
        kit_file = str(kit.get("file") or "")
        kit_text = ""
        if kit_file and Path(kit_file).is_file():
            kit_text = Path(kit_file).read_text(encoding="utf-8", errors="replace")
        check("⑦ 对接单**真落盘到 `_lead/组长对接单.md`**（含具体桥/地址，不是只给个 URL）",
              bool(kit.get("ok")) and bool(kit_text)
              and ("_wakeup" in kit_text or "wakeup" in kit_text.lower()
                   or "桥" in kit_text or "api" in kit_text.lower()),
              "kit=%r 字数=%d" % (kit, len(kit_text)))

        wake = h.get("wake") or {}
        check("⑧ **真唤醒被尝试过**（有 result，且失败时给 next 说清怎么办）",
              bool(wake.get("result"))
              and (bool(h.get("awake")) or bool(h.get("next"))),
              "wake=%r awake=%r next=%r" % (wake.get("result"), h.get("awake"),
                                            str(h.get("next"))[:80]))
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾
            srv.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("-" * 78)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:240])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
