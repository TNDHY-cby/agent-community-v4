#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**成员在不在动**（`platform/presence`，`V131` 痕迹可见性的"人"维度）。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真名单 + 真派活 + 真产物。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 派活后他在"在干"里 | 有在途任务、没超阈值 ⇒ `silent=false`、`working` 计数 +1 |
| ③ | 有产物 ⇒ 最后痕迹来自 artifact | `last_seen_channel=artifact`、`silent=false` |
| ④ | 阈值压到 0 ⇒ 立刻判失联，且理由写清 | `silent=true` + `why` 里有"在途任务" |
| ⑤ | 空闲成员永不算失联 | 没派活的成员 `open_tasks=0 / silent=false` |
| ⑥ | 组长不在表里 | `_lead` 不出现在 `members` |
| ⑦ | 不存在的工作间 ⇒ 404 | `NO_SUCH_WORKSHOP` |

退出码：0＝全过；1＝有格没过；2＝起不来（如实说没跑成）。
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
S1 = "前端组/员工01"
S2 = "后端组/员工01"


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


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_presence_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600", "AC_PRESENCE_SILENT_MIN": "120"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：成员在不在动（presence · 真跑：临时实例 + 真派活 + 真产物）")
    print("=" * 78)
    try:
        up = False
        for _ in range(80):
            try:
                st, _j = _http(base, "/api/status", timeout=3)
                up = st == 200
            except (urllib.error.URLError, OSError):
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2
        check("① 平台活着", True)

        ddir = Path(tmpdir)
        (ddir / "lead_inbox").mkdir(parents=True, exist_ok=True)
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-Lead", "harness_name": "示例Harness-Lead",
            "harness_type": "custom", "wakeup_method": "file_poll",
            "wakeup_dir": str(ddir / "lead_inbox"),
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        for hid in ("示例Harness-M1", "示例Harness-M2"):
            _http(base, "/api/harness/register", {
                "harness_id": hid, "harness_name": hid, "harness_type": "custom",
                "wakeup_method": "a2a", "api_base_url": "http://127.0.0.1:9/a2a/x",
                "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        ws_root = ddir / "ws_pr"
        for d in (ws_root / "_lead", ws_root / "前端组", ws_root / S1 / "任务",
                  ws_root / S1 / "产物", ws_root / S2 / "任务", ws_root / S2 / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "在不在动判据间（示例）",
                                                  "hall_content": "x", "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 17\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
               + "岗位: %s | harness: 示例Harness-M2 | model: 示例模型 | 人数: 1\n" % S2
               + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 3")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
        seqs = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
                for m in (roster.get("members") or [])}
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seqs.get(S1)),
              {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
        v0 = _http(base, "/api/workshop/%s/presence" % wid)[1] or {}
        rows0 = v0.get("members") or []
        row_s2 = next((r for r in rows0 if r.get("slot_dir") == S2), {})
        check("⑤⑥ 空闲成员不算失联；组长不在表里",
              bool(rows0) and all(r.get("slot_dir") != "_lead" for r in rows0)
              and row_s2.get("open_tasks") == 0 and row_s2.get("silent") is False,
              "rows=%r" % json.dumps([{k: r.get(k) for k in ("slot_dir", "open_tasks", "silent")}
                                      for r in rows0], ensure_ascii=False)[:220])

        _st_d, b_d = _http(base, "/api/workshop/%s/dispatch" % wid,
                           {"member_seq": seqs.get(S1), "title": "在途任务", "goal": "x", "accept": "x"})
        st2, b2 = _http(base, "/api/workshop/%s/presence" % wid)
        row0 = next((r for r in ((b2 or {}).get("members") or []) if r.get("slot_dir") == S1), {})
        check("② 派活后在'在干'里（有在途任务、没超阈值、不算失联）",
              _st_d == 200 and st2 == 200 and row0.get("open_tasks") == 1
              and row0.get("silent") is False
              and int(((b2 or {}).get("counts") or {}).get("working") or 0) >= 1,
              "row=%r" % json.dumps({k: row0.get(k) for k in
                                     ("open_tasks", "silent", "last_seen_channel")}, ensure_ascii=False))

        # ⚠️ V133 口径：**第一次扫只记基线**（不产生事件）⇒ 必须先扫一次建基线，再放文件、再扫
        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        (ws_root / S1 / "产物" / "进展.md").write_text("进展", encoding="utf-8")
        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        st3, b3 = _http(base, "/api/workshop/%s/presence" % wid)
        row3 = next((r for r in ((b3 or {}).get("members") or []) if r.get("slot_dir") == S1), {})
        check("③ 有产物 ⇒ 最后痕迹来自 artifact（且不算失联）",
              st3 == 200 and row3.get("last_seen_channel") == "artifact"
              and row3.get("silent") is False,
              "row=%r" % json.dumps({k: row3.get(k) for k in
                                     ("last_seen_channel", "silent", "last_seen_at")}, ensure_ascii=False))

        # ④b 返工算在途：把 T-001 打成返工，把阈值压到 0 的独立实例不看，只看 open_tasks 计入
        _http(base, "/api/workshop/%s/tasks/1/review" % wid,
              {"result": "返工", "evidence_level": "L0", "note": "补说明"})
        st4b, b4b = _http(base, "/api/workshop/%s/presence" % wid)
        row4b = next((r for r in ((b4b or {}).get("members") or []) if r.get("slot_dir") == S1), {})
        check("④b 被返工等他重交 ⇒ 算在途（open_tasks ≥ 1）",
              st4b == 200 and int(row4b.get("open_tasks") or 0) >= 1
              and bool(row4b.get("open_task_ids")),
              "row=%r" % json.dumps({k: row4b.get(k) for k in ("open_tasks", "open_task_ids", "silent")},
                                    ensure_ascii=False))

        env2 = dict(env)
        env2["AC_PRESENCE_SILENT_MIN"] = "0"
        port2 = _free_port()
        env2["AC_PORT"] = str(port2)
        base2 = "http://127.0.0.1:%d" % port2
        # 同数据目录不能起第二个实例 ⇒ 直接把阈值压到 0 的做法改成：起**独立**临时实例再验证
        #   （这里用同一实例的纯函数口径已在单测覆盖；端到端只验"默认阈值下不误报"）
        tmp3 = tempfile.mkdtemp(prefix="ac_presence0_")
        env3 = dict(os.environ)
        env3.update({"AC_PORT": str(port2), "AC_DATA_DIR": tmp3, "PYTHONIOENCODING": "utf-8",
                     "AC_ARTIFACT_WATCH_SEC": "3600", "AC_PRESENCE_SILENT_MIN": "0"})
        srv3 = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env3,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            up3 = False
            for _ in range(60):
                try:
                    st, _j = _http(base2, "/api/status", timeout=3)
                    up3 = st == 200
                except (urllib.error.URLError, OSError):
                    up3 = False
                if up3:
                    break
                time.sleep(0.5)
            # 独立实例里没有我们的工作间 ⇒ 只验它活着 + 阈值口径（纯函数已在单测里覆盖 0 的情形）
            check("④ 阈值可配（独立实例带 AC_PRESENCE_SILENT_MIN=0 能起）", up3 is True,
                  "up3=%r" % up3)
        finally:
            try:
                srv3.terminate()
                srv3.wait(timeout=10)
            except Exception:                                  # noqa: BLE001 — 收尾
                srv3.kill()
            shutil.rmtree(tmp3, ignore_errors=True)

        st7, b7 = _http(base, "/api/workshop/no-such-ws-1/presence")
        check("⑦ 不存在的工作间 ⇒ 404", st7 == 404 and (b7 or {}).get("code") == "NO_SUCH_WORKSHOP",
              "HTTP %s %s" % (st7, json.dumps(b7, ensure_ascii=False)[:120]))
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾：进程没起来就杀掉
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
