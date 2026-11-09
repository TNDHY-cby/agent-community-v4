#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**组织关系指针化**（借鉴竞品 A1 / 设计稿 V139）—— 把"谁向谁汇报"变成数据。

真跑：临时实例 + 真工作间 + 真名单 + 真分队。

| # | 格 | 断言 |
|---|---|---|
| ① | `GET …/org-chart` 给树 | 200；有 `nodes`/`edges`/`unassigned`/`counts`/`note` |
| ② | 组长 `level=0` 且 `reports_to=null` | 不给自己编一个上级 |
| ③ | 每个成员 `level=1` 且 `reports_to="_lead"` | 汇报关系**显式化**（不再靠 `_lead` 字面量约定） |
| ④ | `edges` 数 = 成员数，且每条都是成员 → `_lead` | 边与实际一致 |
| ⑤ | 分队是**分组不是上级**：成员带 `squad_id`，但 `reports_to` 仍是 `_lead` | 不发明分队长这一层 |
| ⑥ | `roster` 里每个成员也有 `reports_to`/`level` | 前端从名单就能拿到，不必再打第二个接口 |
| ⑦ | **不排分**：响应里没有 score/rank/推荐 | 全项目口径 |
| ⑧ | `AC_ORG_CHART=0` ⇒ 端点 404 `ORG_CHART_DISABLED` | 回滚开关**真生效**（不是假开关） |

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
S1 = "前端组/员工01"
S2 = "前端组/员工02"


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


def _boot(env_extra: dict) -> tuple:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_org_")
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(Path(tmpdir) / "data"),
                "PYTHONIOENCODING": "utf-8", "AC_ARTIFACT_WATCH_SEC": "3600"})
    env.update(env_extra)
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        try:
            if _http(base, "/api/status", timeout=3)[0] == 200:
                return srv, base, Path(tmpdir)
        except (urllib.error.URLError, OSError):                 # noqa: BLE001 — 有意静默：起服探测期间连不上是
            pass                                                  #   预期（循环会重试，最后超时才报"起不来"）
        time.sleep(0.5)
    return srv, "", Path(tmpdir)


def _fixture(base: str, ddir: Path) -> str:
    """建一个真工作间 + 两人名单 + 一个分队。"""
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
    ws_root = ddir / "ws_org"
    for d in (ws_root / "_lead", ws_root / "前端组",
              ws_root / S1 / "任务", ws_root / S1 / "产物",
              ws_root / S2 / "任务", ws_root / S2 / "产物"):
        d.mkdir(parents=True, exist_ok=True)
    (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
    (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
    _st, made = _http(base, "/api/workshop", {"name": "组织关系判据间（示例）",
                                             "hall_content": "x", "workshop_path": str(ws_root)})
    wid = str((made or {}).get("workshop_id") or "")
    mk = _http(base, "/api/mind/A-01", {})[1] or {}
    _http(base, "/api/workshop/%s/manifest" % wid,
          {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
    txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 35\n---\n" % wid
           + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
           + "岗位: %s | harness: 示例Harness-M2 | model: 示例模型 | 人数: 1\n" % S2
           + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
           + "---\n行数: 3")
    _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
    # 建一个分队，把员工01 编进去（员工02 留作 unassigned）
    roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
    seqs = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
            for m in (roster.get("members") or [])}
    for slot in (S1, S2):                       # 建分队要求成员已接上 ⇒ 先补证据
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seqs.get(slot)),
              {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
    st_sq, b_sq = _http(base, "/api/workshop/%s/squads" % wid,
                        {"name": "前端分队", "direction": "前端",
                         "member_seqs": [seqs.get(S1)]})
    if st_sq != 200:
        print("  [夹具提示] 建分队失败：HTTP %s %s"
              % (st_sq, json.dumps(b_sq, ensure_ascii=False)[:160]))
    return wid


def main() -> int:
    print("判据：组织关系指针化（A1 · V139 · 真跑）")
    print("=" * 78)
    srv, base, ddir = _boot({})
    try:
        if not base:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2
        wid = _fixture(base, ddir)

        st1, b1 = _http(base, "/api/workshop/%s/org-chart" % wid)
        nodes = (b1 or {}).get("nodes") or []
        check("① GET …/org-chart 给树（nodes/edges/unassigned/counts/note）",
              st1 == 200 and bool(nodes) and "edges" in (b1 or {})
              and "unassigned" in (b1 or {}) and bool((b1 or {}).get("counts"))
              and bool((b1 or {}).get("note")),
              "HTTP %s keys=%r" % (st1, list(b1 or {})[:8]))

        lead = next((n for n in nodes if n.get("kind") == "lead"), None)
        check("② 组长 level=0 且 reports_to=null（不给自己编上级）",
              bool(lead) and lead.get("level") == 0 and lead.get("reports_to") is None,
              "lead=%r" % (lead,))

        mem = [n for n in nodes if n.get("kind") == "member"]
        check("③ 每个成员 level=1 且 reports_to='_lead'",
              len(mem) == 2 and all(m.get("level") == 1 and m.get("reports_to") == "_lead"
                                    for m in mem),
              "members=%r" % [(m.get("slot_dir"), m.get("level"), m.get("reports_to"))
                              for m in mem])

        edges = (b1 or {}).get("edges") or []
        check("④ edges 数 = 成员数，且每条都是 成员 → _lead",
              len(edges) == len(mem) and all(e.get("to") == "_lead" for e in edges),
              "edges=%r" % edges)

        sq_mem = next((m for m in mem if m.get("slot_dir") == S1), {})
        # ⚠️ 不写死分队 id：平台会自己生成（`squad-2839` 这种），传进去的 `squad_id` 不一定被采用。
        #   这一格要的只是"**有分组**、但**组不改变汇报关系**"。
        check("⑤ 分队是**分组不是上级**（带 squad_id，但 reports_to 仍是 _lead）",
              bool(sq_mem.get("squad_id")) and sq_mem.get("reports_to") == "_lead",
              "s1=%r" % sq_mem)

        rr = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {}).get("members") or []
        check("⑥ roster 里每个人也带 reports_to/level（前端不必再打第二个接口）",
              len(rr) >= 3 and all(("reports_to" in (m or {}) and "level" in (m or {})) for m in rr),
              "rows=%r" % [(m.get("slot_dir"), m.get("reports_to"), m.get("level")) for m in rr])

        bad = [n.get("slot_dir") for n in nodes
               if any(k in str(k).lower() for k in (n or {})) and
               any(("score" in str(k).lower() or "rank" in str(k).lower()) for k in (n or {}))]
        check("⑦ 不排分（节点里没有 score/rank 这类字段）", not bad, "违规节点=%r" % bad)

        st8, b8 = _http(base, "/api/workshop/%s/org-chart" % wid)   # 占位：开关在另一个实例上验
        del st8, b8
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾：进程没起来就杀掉
            srv.kill()
        shutil.rmtree(ddir, ignore_errors=True)

    # ⑧ 回滚开关：另起一个 AC_ORG_CHART=0 的实例
    srv2, base2, ddir2 = _boot({"AC_ORG_CHART": "0"})
    try:
        if not base2:
            check("⑧ AC_ORG_CHART=0 ⇒ 端点 404（回滚开关真生效）", False, "第二个实例起不来")
        else:
            st9, b9 = _http(base2, "/api/workshop/whatever/org-chart")
            check("⑧ AC_ORG_CHART=0 ⇒ 端点 404（回滚开关真生效）",
                  st9 == 404 and (b9 or {}).get("code") == "ORG_CHART_DISABLED",
                  "HTTP %s %s" % (st9, json.dumps(b9, ensure_ascii=False)[:160]))
    finally:
        try:
            srv2.terminate()
            srv2.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾
            srv2.kill()
        shutil.rmtree(ddir2, ignore_errors=True)

    print("-" * 78)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:240])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
