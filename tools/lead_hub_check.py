#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**组长枢纽**（`V134`）—— 能力面 / 验收署名 / 产物绑任务 / 未读 / 对接单投递。

老板 2026-11-07：「组长最适配，功能最全，所以可以用来带动不适配者。从项目整体分析一下存在哪些设计漏洞」。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间目录 + 真注册 + 真派活 + 真扫盘。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 能力面端点 | 组长的 `can_write_files/can_receive_notice/lead_eligible/gaps` 都算出来（不是空壳） |
| ③ | 没有组长 | 404 `NO_LEAD` + 下一步 |
| ④ | 验收署名·默认 | 不填 who ⇒ 账本 `reviewed_by=user`（不再默认 lead） |
| ⑤ | 验收署名·声称 | `who=lead` 无凭据 ⇒ 403 `WHO_NOT_PROVEN`，且账本**没落账** |
| ⑥ | 验收署名·组长令牌 | `who_source=token` + 组长 A 档令牌 ⇒ `reviewed_by=_lead`、`reviewed_by_source=token` |
| ⑦ | 验收署名·成员令牌 | 成员令牌 ⇒ 403 `NOT_LEAD` |
| ⑧ | 产物绑任务 | 派活后放文件 ⇒ 事件带 `task_id`、`needs_review=true`；任务 `issued→delivered` |
| ⑨ | 两条未完成任务 | `task_id_ambiguous=true` 且**不推进**任何一条 |
| ⑩ | 未读 | `unread≥1`；`mark_read` 后归零；回退被拒（只前进） |
| ⑪ | 对接单投递 | `POST …/lead-kit/send` 有通道 ⇒ `sent=true`；同时返回 `kit` 与"投递≠收到并照做"的说明 |
| ⑫ | 只读/不假成功 | 能力面端点调用前后状态文件 sha 不变 |

退出码：0＝全过；1＝有格没过；2＝起不来（如实说没跑成）。
"""
from __future__ import annotations

import hashlib
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


def _http(base: str, path: str, body=None, timeout: float = 30, headers=None):
    path = urllib.parse.quote(path, safe="/?=&:,.~@!$'()*+;")   # 路径里可能有中文 id
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    h.update(headers or {})
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


def _sha_dir(d: Path) -> str:
    parts = []
    for name in ("state.json", "harnesses.json", "workshops.json", "v5_roster.json",
                 "task_ledger.json"):
        p = d / name
        try:
            parts.append("%s:%s" % (name, hashlib.sha256(p.read_bytes()).hexdigest()[:12]))
        except FileNotFoundError:
            parts.append("%s:-" % name)
    return "|".join(parts)


SLOT = "前端组/员工01"


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_leadhub_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：组长枢纽（V134 · 真跑：临时实例 + 真注册 + 真派活 + 真扫盘）")
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
        # ── 注册两个接入：组长（能写文件 + 桥通道 + 视觉脚本）与成员（A2A） ──────────
        # ⚠️ `wakeup_dir` 必须先存在：`validate_wakeup_dir` 会拒掉不存在的目录（注册直接 400，
        #    能力面就会全是 null —— 首版就栽在这，实测踩过）。
        (ddir / "lead_inbox").mkdir(parents=True, exist_ok=True)
        st_reg, b_reg = _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-Lead", "harness_name": "示例Harness-Lead",
            "harness_type": "custom", "wakeup_method": "file_poll",
            "wakeup_dir": str(ddir / "lead_inbox"),
            "tools": [{"name": "write_file", "description": "写文件"}],
            "activation_script": {"text": "点输入框粘一句", "title": "示例组长窗口-不存在-zzz"},
            "ai": {"model_name": "示例模型"}})
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-M1", "harness_name": "示例Harness-M1",
            "harness_type": "custom", "wakeup_method": "a2a",
            "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "write_file", "description": "写文件"}],
            "ai": {"model_name": "示例模型"}})

        # ── 工作间 + 名单（成员 + 组长） ─────────────────────────────────────
        ws_root = Path(tmpdir) / "ws_lead"
        for d in (ws_root / "_lead", ws_root / "前端组",
                  ws_root / SLOT / "任务", ws_root / SLOT / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "前端组" / "_notice.md").write_text("", encoding="utf-8")
        st, made = _http(base, "/api/workshop", {"name": "组长枢纽判据间（示例）",
                                                 "hall_content": "组长带动",
                                                 "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 5\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % SLOT
               + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 2")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = _http(base, "/api/workshop/%s/roster" % wid)[1] or {}
        mems = roster.get("members") or []
        seq_by_slot = {str(m.get("slot_dir") or ""): m.get("member_seq") for m in mems}

        before = _sha_dir(ddir)
        st2, b2 = _http(base, "/api/workshop/%s/lead-capabilities" % wid)
        caps2 = b2 or {}
        check("② 组长能力面算出来了（不是空壳）",
              st2 == 200 and caps2.get("can_write_files") is True
              and caps2.get("can_receive_notice") in ("file", "visual", "protocol")
              and caps2.get("lead_eligible") is True
              and isinstance(caps2.get("gaps"), list) and bool(caps2.get("gaps")),
              "HTTP %s %s ｜ 注册组长=%s %s"
              % (st2, json.dumps({k: caps2.get(k) for k in
                                  ("can_write_files", "can_receive_notice",
                                   "lead_eligible")}, ensure_ascii=False),
                 st_reg, json.dumps(b_reg, ensure_ascii=False)[:220]))
        after = _sha_dir(ddir)
        check("⑫ 能力面只读（调用前后状态文件 sha 不变）", before == after,
              "before=%s after=%s" % (before[:50], after[:50]))

        # ── 派活（真建任务账本） ─────────────────────────────────────────────
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq_by_slot.get(SLOT)),
              {"protocol_ok": True, "verified_scope": "判据夹具", "tier": "a_protocol"})
        st_d, b_d = _http(base, "/api/workshop/%s/dispatch" % wid,
                          {"member_seq": seq_by_slot.get(SLOT), "title": "判据任务",
                           "goal": "随便", "accept": "随便"})
        tseq = None
        for t in (_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []:
            if str(t.get("slot_dir") or "") == SLOT:
                tseq = int(t.get("task_seq") or 0)
        check("②b 夹具：真派活建出任务账本", st_d in (200, 400) and bool(tseq),
              "HTTP %s tseq=%r" % (st_d, tseq))

        # ── 验收署名 4 格 ───────────────────────────────────────────────────
        st4, b4 = _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, tseq),
                        {"result": "返工", "evidence_level": "L4", "note": "默认署名"})
        ledger_tasks = (_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []
        rec = next((t for t in ledger_tasks if int(t.get("task_seq") or 0) == tseq), {})
        check("④ 验收署名默认 user（不再默认 lead）",
              st4 == 200 and rec.get("reviewed_by") == "user"
              and rec.get("reviewed_by_source") == "user",
              "HTTP %s reviewed_by=%r" % (st4, rec.get("reviewed_by")))

        st5, b5 = _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, tseq),
                        {"result": "返工", "evidence_level": "L4", "who": "lead"})
        check("⑤ 声称 lead 无凭据 ⇒ 403 且不落账",
              st5 == 403 and b5.get("code") == "WHO_NOT_PROVEN",
              "HTTP %s %s" % (st5, json.dumps(b5, ensure_ascii=False)[:140]))

        tk_lead = ((_http(base, "/api/workshop/%s/members/%s/token" % (wid, seq_by_slot.get("_lead")),
                          {})[1] or {}).get("token") or "")
        st6, b6 = _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, tseq),
                        {"result": "返工", "evidence_level": "L4", "who_source": "token"},
                        headers={"Authorization": "Bearer %s" % tk_lead})
        ledger_tasks = (_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []
        rec6 = next((t for t in ledger_tasks if int(t.get("task_seq") or 0) == tseq), {})
        check("⑥ 组长令牌 ⇒ reviewed_by=_lead / source=token",
              st6 == 200 and rec6.get("reviewed_by") == "_lead"
              and rec6.get("reviewed_by_source") == "token",
              "HTTP %s %s" % (st6, json.dumps(b6, ensure_ascii=False)[:160]))

        tk_m = ((_http(base, "/api/workshop/%s/members/%s/token" % (wid, seq_by_slot.get(SLOT)),
                       {})[1] or {}).get("token") or "")
        st7, b7 = _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, tseq),
                        {"result": "返工", "evidence_level": "L4", "who_source": "token"},
                        headers={"Authorization": "Bearer %s" % tk_m})
        check("⑦ 成员令牌不能当组长验收（NOT_LEAD）",
              st7 == 403 and b7.get("code") == "NOT_LEAD",
              "HTTP %s %s" % (st7, json.dumps(b7, ensure_ascii=False)[:140]))

        # ── 产物绑任务 + delivered ──────────────────────────────────────────
        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})            # 基线
        f = ws_root / SLOT / "产物" / "交付物.md"
        f.write_text("真产物", encoding="utf-8")
        st8, b8 = _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        ev = ((b8 or {}).get("new") or [{}])[0]
        ledger_tasks = (_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []
        rec8 = next((t for t in ledger_tasks if int(t.get("task_seq") or 0) == tseq), {})
        check("⑧ 产物绑任务 + 推到 delivered（不等于验收）",
              st8 == 200 and ev.get("task_id") == "T-%03d" % tseq
              and ev.get("needs_review") is True and rec8.get("state") == "delivered",
              "event=%r state=%r" % (ev.get("task_id"), rec8.get("state")))

        # ── 未读 ────────────────────────────────────────────────────────────
        st10, b10 = _http(base, "/api/workshop/%s/inbox-events?limit=10" % wid)
        u10 = (b10 or {}).get("unread") or {}
        st10b, b10b = _http(base, "/api/workshop/%s/inbox-events/read" % wid, {})
        u10b = (b10b or {}).get("unread") or {}
        st10c, b10c = _http(base, "/api/workshop/%s/inbox-events/read" % wid, {"seq": 0})
        check("⑩ 未读可读、可推进、且只前进不后退",
              st10 == 200 and int(u10.get("unread") or 0) >= 1
              and int(u10b.get("unread") or 0) == 0
              and (b10c or {}).get("moved") is False,
              "unread=%r → %r backtrack_moved=%r"
              % (u10.get("unread"), u10b.get("unread"), (b10c or {}).get("moved")))

        # ── 对接单投递 ──────────────────────────────────────────────────────
        st11, b11 = _http(base, "/api/workshop/%s/lead-kit/send" % wid, {})
        check("⑪ 对接单投递（有通道 ⇒ sent=true，并给出 kit 与『投递≠照做』）",
              st11 == 200 and (b11 or {}).get("sent") is True
              and bool((b11 or {}).get("channels")) and bool((b11 or {}).get("kit"))
              and "投递成功" in str((b11 or {}).get("note")),
              "HTTP %s sent=%r channels=%r"
              % (st11, (b11 or {}).get("sent"), (b11 or {}).get("channels")))

        # ③ 没有组长的工作间 ⇒ 404 NO_LEAD（另建一个空间）
        st3, b3 = _http(base, "/api/workshop", {"name": "无组长间（示例）",
                                                "hall_content": "x",
                                                "workshop_path": str(Path(tmpdir) / "ws_nolead")})
        wid3 = str((b3 or {}).get("workshop_id") or "")
        st3b, b3b = _http(base, "/api/workshop/%s/lead-capabilities" % wid3)
        check("③ 没有组长 ⇒ 404 NO_LEAD + 下一步",
              st3b == 404 and (b3b or {}).get("code") == "NO_LEAD" and (b3b or {}).get("next"),
              "HTTP %s %s" % (st3b, json.dumps(b3b, ensure_ascii=False)[:140]))

        # ⑨ 两条未完成任务 ⇒ ambiguous（另派一条同工位任务再放文件）
        _http(base, "/api/workshop/%s/dispatch" % wid,
              {"member_seq": seq_by_slot.get(SLOT), "title": "第二条", "goal": "x", "accept": "x"})
        (ws_root / SLOT / "产物" / "交付物2.md").write_text("y", encoding="utf-8")
        st9, b9 = _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        ev9 = ((b9 or {}).get("new") or [{}])[0]
        check("⑨ 同工位两条未完成任务 ⇒ 标 ambiguous（不猜、不推进）",
              st9 == 200 and ev9.get("task_id_ambiguous") is True and not ev9.get("task_id"),
              "event=%r" % json.dumps(ev9, ensure_ascii=False)[:160])

        # ── V135：适配度三档 / 低适配不能当主组长 / 团队视图 ──────────────
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-High", "harness_name": "示例Harness-High",
            "harness_type": "custom", "wakeup_method": "a2a",
            "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "read_file"}, {"name": "write_file"}],
            "ai": {"model_name": "示例模型"}})
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-Low", "harness_name": "示例Harness-Low",
            "harness_type": "custom", "wakeup_method": "clipboard",
            "tools": [], "ai": {"model_name": "示例模型"}})
        st_hi, b_hi = _http(base, "/api/harness/%s/capabilities" % "示例Harness-High")
        st_lo, b_lo = _http(base, "/api/harness/%s/capabilities" % "示例Harness-Low")
        lv_hi = ((b_hi or {}).get("protocol_fit") or {}).get("level")
        lv_lo = ((b_lo or {}).get("protocol_fit") or {}).get("level")
        check("⑭ 适配度是真算的（high / low 两档都真出现）",
              st_hi == 200 and st_lo == 200 and lv_hi == "high" and lv_lo == "low"
              and bool(((b_lo or {}).get("protocol_fit") or {}).get("reasons")),
              "high=%r low=%r reasons=%r"
              % (lv_hi, lv_lo, ((b_lo or {}).get("protocol_fit") or {}).get("reasons")))

        st_ld, b_ld = _http(base, "/api/workshop/%s/lead" % wid, {"harness_id": "示例Harness-Low"})
        check("⑮ 低适配（视觉型）不能当主组长 ⇒ 409 且原因是适配度哪一项不够",
              st_ld == 409 and (b_ld or {}).get("code") in ("NOT_LEAD_ELIGIBLE", "NOT_LEAD_FIT")
              and bool((b_ld or {}).get("why"))
              and ((b_ld or {}).get("protocol_fit") or {}).get("level") == "low",
              "HTTP %s %s" % (st_ld, json.dumps(b_ld, ensure_ascii=False)[:220]))

        st_tm, b_tm = _http(base, "/api/workshop/%s/lead-team" % wid)
        cov = (b_tm or {}).get("coverage") or {}
        check("⑯ 团队视图：主组长 + 助手 + 四件事覆盖（缺的要说明平台代做）",
              st_tm == 200 and bool(((b_tm or {}).get("lead") or {}).get("protocol_fit"))
              and bool((b_tm or {}).get("helpers"))
              and set(cov) >= {"dispatch", "receive_result", "read_artifacts", "wake"}
              and all(isinstance(v.get("missing"), bool) for v in cov.values()),
              "HTTP %s lead_fit=%r helpers=%d cov=%s"
              % (st_tm, ((b_tm or {}).get("lead") or {}).get("protocol_fit", {}).get("level"),
                 len((b_tm or {}).get("helpers") or []),
                 json.dumps({k: v.get("missing") for k, v in cov.items()}, ensure_ascii=False)))
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
