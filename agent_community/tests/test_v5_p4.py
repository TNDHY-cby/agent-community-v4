# -*- coding: utf-8 -*-
"""守卫：**P4 目录校验 / 产物清单（L0）/ 验收落闸 / 归档 / 审忆员**（`V115` V1~V10 + M-R1~M-R5）。

全部真跑：真目录、真文件、真 HTTP；审忆员用 `--dry-run` / `--apply` 真跑两次比对。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import artifacts as arts  # noqa: E402
from agent_community.platform import task_ledger as ledger  # noqa: E402
from agent_community.platform import verify as vfy  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402

SLOT = "前端组/员工01"


def _mk_ws(tmp: Path) -> Path:
    wp = tmp / "ws"
    (wp / "_lead").mkdir(parents=True)
    (wp / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    (wp / "前端组").mkdir()
    (wp / "前端组" / "分工.md").write_text("- 版本: 2\n", encoding="utf-8")
    (wp / "前端组" / "_notice.md").write_text("", encoding="utf-8")
    (wp / SLOT / "任务").mkdir(parents=True)
    (wp / SLOT / "产物").mkdir(parents=True)
    return wp


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AC_MIND_ROOT", str(tmp_path / "minds"))
    monkeypatch.setattr(ledger, "_path", lambda: tmp_path / "data" / "task_ledger.json",
                        raising=True)
    monkeypatch.setattr(roster, "_path", lambda: tmp_path / "data" / "v5_roster.json",
                        raising=True)
    return {"tmp": tmp_path, "ws": _mk_ws(tmp_path)}


# ── V1/V2/V3：verify-dirs ─────────────────────────────────────────
def test_v1_missing_member_dir_is_reported_with_path(env):
    (env["ws"] / SLOT / "产物").rmdir()
    out = vfy.verify_dirs(env["ws"], [{"slot_dir": SLOT}])
    assert out["ok"] is False
    miss = [m for m in out["missing"] if "产物" in m["what"]]
    assert miss and miss[0]["path"].endswith("前端组/员工01/产物")
    assert set(miss[0]["four"]) == {"who", "what", "where", "how"}


def test_v2_after_creating_dir_it_passes(env):
    (env["ws"] / SLOT / "产物").rmdir()
    assert vfy.verify_dirs(env["ws"], [{"slot_dir": SLOT}])["ok"] is False
    (env["ws"] / SLOT / "产物").mkdir()
    assert vfy.verify_dirs(env["ws"], [{"slot_dir": SLOT}])["ok"] is True


def test_v3_division_without_version_line_is_flagged(env):
    (env["ws"] / "前端组" / "分工.md").write_text("# 分工\n没有版本行\n", encoding="utf-8")
    out = vfy.verify_dirs(env["ws"], [{"slot_dir": SLOT}])
    assert out["ok"] is False
    assert any("版本" in m["what"] for m in out["missing"])


# ── V4~V6：产物清单靠目录 diff ───────────────────────────────────
def test_v4_empty_artifacts_dir_is_honestly_empty(env):
    out = arts.scan(env["ws"], [SLOT])
    assert out["total"] == 0
    s = arts.summary_for_task(out, SLOT)
    assert s["empty"] is True and "L3" in s["note"], "自报不算要写在文案里"


def test_v5_snapshot_records_sha_and_bytes(env):
    f = env["ws"] / SLOT / "产物" / "api.md"
    f.write_text("hello", encoding="utf-8")
    out = arts.scan(env["ws"], [SLOT])
    info = out["slots"][SLOT]["files"]["api.md"]
    assert info["bytes"] == 5 and len(info["sha256"]) == 64
    assert (env["ws"] / "_meta" / "artifacts.json").is_file(), "清单要落盘"


def test_v6_changed_file_is_detected(env):
    f = env["ws"] / SLOT / "产物" / "api.md"
    f.write_text("v1", encoding="utf-8")
    arts.scan(env["ws"], [SLOT])
    f.write_text("v2-longer", encoding="utf-8")
    out = arts.scan(env["ws"], [SLOT])
    assert "%s/api.md" % SLOT in out["diff"]["changed"]
    assert out["diff"]["new"] == []


# ── V7~V10：验收与归档 ───────────────────────────────────────────
def test_v7_review_requires_evidence_and_rejects_L3(env):
    ledger.issue("WS-T", 1, slot_dir=SLOT, file="%s/任务/T-001.md" % SLOT)
    assert ledger.review("WS-T", 1, result="通过")["code"] == "EVIDENCE_REQUIRED"
    assert ledger.review("WS-T", 1, result="通过", evidence_level="L3")["code"] == "EVIDENCE_L3_REJECTED"
    # ★2026-10-31（`V116` §15.1 老板拍板）：L1＝「它库已有」，**必须带 evidence_ref 凭据**；
    #   没有凭据的 L1 按 L4 算并拒。以前这里直接 accepted —— 那是漏洞（实测抓到），不是行为基线。
    no_proof = ledger.review("WS-T", 1, result="通过", evidence_level="L1")
    assert no_proof["code"] == "L1_NEEDS_PROOF", no_proof
    assert "L4" in no_proof["error"], "要说清「没凭据按 L4 算」"
    ok = ledger.review("WS-T", 1, result="通过", evidence_level="L1",
                       evidence_ref="示例库 sqlite 主库 + 口令命中（人工核对）")
    assert ok["state"] == "accepted", ok
    assert ok.get("evidence_label") == "L1（人工凭据，平台未核对）", "不许显示成平台已验证"
    rec = ledger.get("WS-T", 1)
    assert rec["evidence_ref_is_manual"] is True and rec["evidence_ref"]


def test_v7b_L2_with_empty_artifacts_cannot_pass(env):
    ledger.issue("WS-T", 2, slot_dir=SLOT, file="x")
    out = ledger.review("WS-T", 2, result="通过", evidence_level="L2",
                        artifacts={"empty": True})
    assert out["code"] == "ARTIFACTS_EMPTY"


def test_v7d_empty_artifacts_only_L4_can_pass(env):
    """老板 2026-10-31 拍板第 ② 条：产物真扫到 0 个文件 ⇒ 只有 L4（人眼确认）能过。"""
    ledger.issue("WS-T", 4, slot_dir=SLOT, file="x")
    empty = {"empty": True, "total_files": 0, "files_now": []}
    for lvl in ("L0", "L2"):
        got = ledger.review("WS-T", 4, result="通过", evidence_level=lvl, artifacts=empty)
        assert got["code"] == "ARTIFACTS_EMPTY", "%s 在产物空时不许通过：%s" % (lvl, got)
    # L1 有凭据也不再是"盘上有产物"的替代品
    got1 = ledger.review("WS-T", 4, result="通过", evidence_level="L1",
                         evidence_ref="示例库命中（人工核对）", artifacts=empty)
    assert got1["code"] == "ARTIFACTS_EMPTY", got1
    # L4 不受限：人眼确认本来就不依赖盘上文件
    ok = ledger.review("WS-T", 4, result="通过", evidence_level="L4",
                       artifacts=empty, note="我本人看过它在别的窗口里干完了")
    assert ok["ok"] and ok["state"] == "accepted", ok


def test_v7c_rework_goes_back_to_rework_state(env):
    ledger.issue("WS-T", 3, slot_dir=SLOT, file="x")
    out = ledger.review("WS-T", 3, result="返工", note="接口少写错误码")
    assert out["state"] == "rework"
    rec = ledger.get("WS-T", 3)
    assert rec["review_note"] == "接口少写错误码"
    assert rec["log"][-1]["state"] == "rework", rec.get("log")


def test_v8_integrate_writes_manifest_of_results(env):
    ledger.issue("WS-T", 1, slot_dir=SLOT, file="x")
    ledger.review("WS-T", 1, result="通过", evidence_level="L1",
                  evidence_ref="示例库命中（人工核对）")
    ledger.issue("WS-T", 2, slot_dir=SLOT, file="y")
    out = ledger.integrate("WS-T", workshop_path=env["ws"])
    body = json.loads((env["ws"] / "_meta" / "integrate.json").read_text(encoding="utf-8"))
    assert out["ok"] and body["accepted"] == 1 and body["open"] == 1


def test_v9_integrate_does_not_touch_artifacts(env):
    f = env["ws"] / SLOT / "产物" / "keep.md"
    f.write_text("不动的文件", encoding="utf-8")
    before = f.read_text(encoding="utf-8")
    ledger.integrate("WS-T", workshop_path=env["ws"])
    assert f.read_text(encoding="utf-8") == before, "归档只写清单，不许动产物"


# ── HTTP 端到端（含 close 吊销）───────────────────────────────────
@pytest.fixture()
def client(env, monkeypatch):
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws = srv.Workshop(workshop_id="WS-T", name="示例间", workspace_dir=str(env["ws"]),
                      hall_content="方向", status="running")
    srv.workshops["WS-T"] = ws
    roster.add_slots("WS-T", [{"岗位": SLOT, "harness": "h_x", "model": "m", "人数": 1}], source="C")
    return TestClient(srv.app, client=("127.0.0.1", 54321))


def test_http_verify_dirs_and_artifacts(client, env):
    d = client.get("/api/workshop/WS-T/verify-dirs").json()
    assert d["ok"] is True, d
    (env["ws"] / SLOT / "产物" / "out.md").write_text("done", encoding="utf-8")
    a = client.post("/api/workshop/WS-T/verify-artifacts", json={}).json()
    assert a["ok"] and a["total"] == 1 and a["diff"]["new"]


def test_http_review_and_close(client, env):
    from agent_community.platform import intake_token as tok

    monkeypatch_ok = tok.issue("WS-T", SLOT)
    body = client.post("/api/workshop/WS-T/tasks/1/review",
                       json={"result": "通过", "evidence_level": "L1"}).json()
    assert body["code"] == "NO_SUCH_TASK", "没派出过的任务不许验收"
    ledger.issue("WS-T", 1, slot_dir=SLOT, file="x")

    # ★2026-10-31（`V121`）：失败态必须带「证据四步表」，且老的 code/error/next 一个都不删
    bad = client.post("/api/workshop/WS-T/tasks/1/review",
                      json={"result": "通过", "evidence_level": "L1"})
    assert bad.status_code == 400, bad.text
    bj = bad.json()
    assert bj["code"] == "L1_NEEDS_PROOF", bj
    assert bj["error"] and bj["next"], "老字段不许丢"
    assert [s["name"] for s in bj["steps"]] == ["派活记录", "产物在盘上", "回执留痕", "人拍板"], bj["steps"]
    assert bj["who_should_fix"], "要说清该谁补"
    assert bj["steps"][0]["ok"] is True, "派活记录这步应该是成立的"

    # 真写产物 + 给凭据 ⇒ 才能通过
    (env["ws"] / SLOT / "产物" / "out.md").write_text("done", encoding="utf-8")
    body2 = client.post("/api/workshop/WS-T/tasks/1/review",
                        json={"result": "通过", "evidence_level": "L1",
                              "evidence_ref": "示例库 sqlite 命中（人工核对）"}).json()
    assert body2["ok"] and body2["state"] == "accepted", body2
    assert body2["evidence_label"] == "L1（人工凭据，平台未核对）", "不许显示成平台已验证"

    closed = client.post("/api/workshop/WS-T/close", json={}).json()
    assert closed["ok"] and closed["status"] == "closed"
    assert tok.verify(monkeypatch_ok["token"])[0] is None, "关了必须吊销 token"


# ── 审忆员（M-R1~M-R5）─────────────────────────────────────────
def _mk_mind(tmp: Path) -> Path:
    m = tmp / "minds" / "A-01"
    for d in ("memories", "02_任务日志", "06_死胡同", "_meta"):
        (m / d).mkdir(parents=True, exist_ok=True)
    (m / "00_宪章.md").write_text("# 宪章\n", encoding="utf-8")
    (m / "00_核心基模.md").write_text("# 基模\n- 初始\n", encoding="utf-8")
    (m / "01_记忆索引表.md").write_text("# 索引\n", encoding="utf-8")
    (m / "memories" / "20261027-打包踩坑.md").write_text("# 记忆\n- 打完包要验体积\n", encoding="utf-8")
    (m / "02_任务日志" / "2026-10-27.md").write_text(
        "# 任务日志\n## 做完了\n- 接口联调完成\n- 自测通过\n- 顺手修了空指针\n", encoding="utf-8")
    (m / "06_死胡同" / "2026-10-27.md").write_text(
        "# 死胡同\n- 别在迁移中间改索引\n", encoding="utf-8")
    return m


def test_mr1_dry_run_writes_nothing(env):
    mind = _mk_mind(env["tmp"])
    before = {p.name: p.read_text(encoding="utf-8") for p in mind.rglob("*") if p.is_file()}
    out = subprocess.run([sys.executable, str(_ROOT / "tools" / "mind_reviewer.py"),
                          "--mind-root", str(env["tmp"] / "minds"), "--date", "2026-10-27",
                          "--dry-run", "--json"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", cwd=str(_ROOT))
    assert out.returncode == 0, out.stderr
    after = {p.name: p.read_text(encoding="utf-8") for p in mind.rglob("*") if p.is_file()}
    assert before == after, "dry-run 必须一个字节都不写"


def test_mr3_apply_archives_before_compressing(env):
    mind = _mk_mind(env["tmp"])
    original = (mind / "02_任务日志" / "2026-10-27.md").read_text(encoding="utf-8")
    out = subprocess.run([sys.executable, str(_ROOT / "tools" / "mind_reviewer.py"),
                          "--mind-root", str(env["tmp"] / "minds"), "--date", "2026-10-27",
                          "--apply", "--json"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", cwd=str(_ROOT))
    assert out.returncode == 0, out.stderr
    archived = list((mind / "_archive" / "2026-10-27").glob("*.bak"))
    assert archived, "改之前必须先归档（不可真删）"
    assert any(original.strip() in p.read_text(encoding="utf-8") for p in archived), \
        "归档要留原文"
    comp = mind / "02_压缩" / "2026-10-27.md"
    assert comp.is_file() and "接口联调完成" in comp.read_text(encoding="utf-8")


def test_mr4_core_model_never_auto_changes(env):
    mind = _mk_mind(env["tmp"])
    before = (mind / "00_核心基模.md").read_text(encoding="utf-8")
    subprocess.run([sys.executable, str(_ROOT / "tools" / "mind_reviewer.py"),
                    "--mind-root", str(env["tmp"] / "minds"), "--date", "2026-10-27",
                    "--apply"], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", cwd=str(_ROOT))
    assert (mind / "00_核心基模.md").read_text(encoding="utf-8") == before, "基模不许自动改"
    pend = list((mind / "_pending").glob("*.diff"))
    assert pend, "改动建议要进待审队列"
    # ★2026-10-31：待审项改成**真补丁**（带 @@ hunk，apply 才套得上）；说明与改前 hash 落在同名 .note.md
    assert "@@" in pend[0].read_text(encoding="utf-8"), "待审项必须是可套用的 unified diff"
    note = mind / "_pending" / (pend[0].stem + ".note.md")
    assert note.is_file() and "改前 hash" in note.read_text(encoding="utf-8")


def test_mr5_same_day_twice_is_idempotent(env):
    mind = _mk_mind(env["tmp"])
    for _ in range(2):
        subprocess.run([sys.executable, str(_ROOT / "tools" / "mind_reviewer.py"),
                        "--mind-root", str(env["tmp"] / "minds"), "--date", "2026-10-27",
                        "--apply"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(_ROOT))
    assert len(list((mind / "02_压缩").glob("2026-10-27.md"))) == 1, "同日不重复压缩"


def test_mr2_refuses_workshop_like_path(env):
    """给一个像工作区的目录：审忆员必须拒绝（只读根A）。"""
    fake = env["tmp"] / "minds" / "ws_abc" / "产物"
    fake.mkdir(parents=True)
    out = subprocess.run([sys.executable, str(_ROOT / "tools" / "mind_reviewer.py"),
                          "--mind-root", str(env["tmp"] / "minds"), "--date", "2026-10-27",
                          "--json"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", cwd=str(_ROOT))
    assert out.returncode == 0
    data = json.loads(out.stdout) if out.stdout.strip().startswith("{") else {}
    refused = (data.get("refused") or [])
    assert any("ws_abc" in (r.get("mind") or "") for r in refused), "像工作区的必须拒绝"
