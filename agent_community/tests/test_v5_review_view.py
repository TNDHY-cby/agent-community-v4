# -*- coding: utf-8 -*-
"""守卫：**验收「证据四步表」与失败态摊开**（`V121`，2026-10-31 老板拍板）。

对应设计稿 `design-docs/V121_验收证据四步表与失败态摊开_2026-10-31.md` §6 判据 1~7。
全部真跑：真 HTTP（TestClient）、真文件、真扫盘；`check_level` 另有纯函数单测（脱机喂合成数据）。

两条被守的老板规则（`V116` §15.1 / §0 第 12 条）：
  ① `L1` 必须带 `evidence_ref` 凭据，没有就按 `L4` 算；
  ② 产物目录真扫到 0 个文件时只许 `L4`。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import artifacts as arts  # noqa: E402
from agent_community.platform import task_ledger as ledger  # noqa: E402
from agent_community.platform import v5_evidence_view as ev  # noqa: E402
from agent_community.platform import v5_roster as roster  # noqa: E402

SLOT = "前端组/员工01"
STEP_NAMES = ["派活记录", "产物在盘上", "回执留痕", "人拍板"]


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


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws = srv.Workshop(workshop_id="WS-T", name="示例间", workspace_dir=str(env["ws"]),
                      hall_content="方向", status="running")
    srv.workshops["WS-T"] = ws
    roster.add_slots("WS-T", [{"岗位": SLOT, "harness": "h_x", "model": "m", "人数": 1}], source="C")
    return TestClient(srv.app, client=("127.0.0.1", 54321))


def _view(client, seq: int) -> dict:
    r = client.get("/api/workshop/WS-T/tasks/%d/evidence-view" % seq)
    assert r.status_code == 200, r.text
    return r.json()


# ── 纯函数：档位校验（脱机、喂合成数据）─────────────────────────────
def test_check_level_L1_needs_proof_and_falls_back_to_L4():
    out = ev.check_level("L1")
    assert out["code"] == "L1_NEEDS_PROOF" and out["normalized"] == "L4"
    assert ev.check_level("L1", "示例库 sqlite 命中（人工核对）")["ok"] is True


def test_check_level_empty_artifacts_only_L4():
    for lvl in ("L0", "L1", "L2"):
        got = ev.check_level(lvl, "凭据" if lvl == "L1" else "", artifacts_empty=True)
        assert got["code"] == "ARTIFACTS_EMPTY", "%s 在产物空时不许过：%s" % (lvl, got)
    assert ev.check_level("L4", "", artifacts_empty=True)["ok"] is True


def test_check_level_unknown_artifacts_state_does_not_pretend_empty():
    """没扫过盘 ≠ 盘上是空的：不许把"不知道"当成"空"来拒人。"""
    got = ev.check_level("L0", "", artifacts_empty=None)
    assert got["ok"] is True and "无法确认" in (got.get("warn") or ""), got


# ── 判据 1：没派活就验收 ─────────────────────────────────────────
def test_no_dispatch_yet_says_so_in_both_entries(client):
    view = _view(client, 9999)
    assert view["blocking"] == "派活记录" and view["who_should_fix"] == "组长"
    assert "T-9999" in view["steps"][0]["what"]
    r = client.post("/api/workshop/WS-T/tasks/9999/review",
                    json={"result": "通过", "evidence_level": "L4"})
    assert r.status_code == 409 and r.json()["code"] == "NO_SUCH_TASK"


# ── 判据 2：通过但没给等级 ───────────────────────────────────────
def test_review_without_level_reports_what_we_have(client, env):
    ledger.issue("WS-T", 1, slot_dir=SLOT, file="%s/任务/T-001.md" % SLOT, wake="manual")
    r = client.post("/api/workshop/WS-T/tasks/1/review", json={"result": "通过"})
    assert r.status_code == 400
    b = r.json()
    assert b["code"] == "EVIDENCE_REQUIRED"
    assert [s["name"] for s in b["steps"]] == STEP_NAMES
    assert b["steps"][0]["ok"] is True, b["steps"][0]
    assert b["steps"][1]["ok"] is False and "0 个文件" in b["steps"][1]["what"], b["steps"][1]
    assert b["blocking"] == "人拍板", b


# ── 判据 3：产物真在盘上 ⇒ L0 成立 ───────────────────────────────
def test_real_artifact_on_disk_gives_L0(client, env):
    ledger.issue("WS-T", 2, slot_dir=SLOT, file="x")
    (env["ws"] / SLOT / "产物" / "api.md").write_text("hello", encoding="utf-8")
    arts.scan(env["ws"], [SLOT])          # 先扫一次，让快照里有它
    view = _view(client, 2)
    assert view["steps"][1]["ok"] is True, view["steps"][1]
    assert "L0" in view["evidence_have"]
    assert view["artifacts_snapshot"]["total_files"] == 1
    assert "api.md" in view["steps"][1]["what"]


# ── 判据 4：先扫后验收，files_now 仍在（修「看反」的坑）───────────
def test_files_now_survives_a_previous_scan(client, env):
    ledger.issue("WS-T", 3, slot_dir=SLOT, file="x")
    f = env["ws"] / SLOT / "产物" / "api.md"
    f.write_text("v1", encoding="utf-8")
    first = arts.scan(env["ws"], [SLOT])
    assert "%s/api.md" % SLOT in first["diff"]["new"], "第一次扫应该算 new"
    # 先点过「扫描产物」再验收 ⇒ 这一轮 diff 里 new 是空的
    again = arts.scan(env["ws"], [SLOT])
    summ = arts.summary_for_task(again, SLOT, "T-003")
    assert summ["new"] == [], "这一轮确实没有新文件"
    assert summ["files_now"] == ["api.md"], "但盘上明明有 —— 判有没有交东西只看 files_now"
    assert summ["total_files"] == 1 and summ["empty"] is False
    assert "不代表" in summ["new_note"]


# ── 判据 5：L1 无凭据 ⇒ 拒，并按 L4 说清 ─────────────────────────
def test_http_L1_without_proof_is_rejected_with_next_step(client, env):
    ledger.issue("WS-T", 4, slot_dir=SLOT, file="x")
    (env["ws"] / SLOT / "产物" / "a.md").write_text("x", encoding="utf-8")
    r = client.post("/api/workshop/WS-T/tasks/4/review",
                    json={"result": "通过", "evidence_level": "L1"})
    assert r.status_code == 400
    b = r.json()
    assert b["code"] == "L1_NEEDS_PROOF"
    assert "L4" in b["error"], "要说清没凭据就按人眼确认算"
    assert b["next"], "必须教它怎么补凭据"


# ── 判据 6：POST 失败返回与 GET 视图**事实层一致**（规则 20 一处真相）──
def test_post_failure_and_get_view_agree_on_facts(client, env):
    """比的是**事实**（每步成不成立 / 档位 / 已有哪些证据），不是措辞、也不是"卡在哪"。

    两处必然有差异，且都是真事：
      · `POST …/review` 会真扫一次产物，只读视图按设计**不扫**（看的时候不许改盘）
        ⇒ 同一时刻一个说「扫过了，0 个文件」、一个说「还没扫过」；
      · `blocking` 取决于「你这次想用哪档」——`POST` 从 body 知道，只读视图要用 `?level=` 才等价。
    所以：事实层必须逐项相等；`blocking` 用 `?level=` 对齐后再比（见下一条测试）。
    """
    ledger.issue("WS-T", 5, slot_dir=SLOT, file="x", wake="manual")
    view = _view(client, 5)
    r = client.post("/api/workshop/WS-T/tasks/5/review",
                    json={"result": "通过", "evidence_level": "L1"})
    post = r.json()
    assert post["evidence_have"] == view["evidence_have"], \
        "同一件事两处口径不一致：\nPOST=%s\nGET =%s" % (post["evidence_have"], view["evidence_have"])
    post_facts = [(s["name"], s["ok"], s.get("level")) for s in post["steps"]]
    view_facts = [(s["name"], s["ok"], s.get("level")) for s in view["steps"]]
    assert post_facts == view_facts, "四步的事实层必须一致：\nPOST=%s\nGET =%s" % (post_facts, view_facts)
    assert all(s["what"] for s in post["steps"]) and all(s["what"] for s in view["steps"]), "每步都要说清"
    # `who_should_fix` 不许手写：必须与 blocking 一一对应（两处各自自洽）
    for name, body in (("POST", post), ("GET", view)):
        assert body["who_should_fix"] == ev.WHO.get(body["blocking"], ""), \
            "%s 的 who_should_fix 与 blocking 对不上：%s / %s" % (name, body["blocking"], body["who_should_fix"])
    assert post["code"] == "L1_NEEDS_PROOF", post


def test_get_view_with_level_predicts_the_same_blocking(client, env):
    """`?level=L1` 预演的卡点，必须与真打一次 `POST` 得到的卡点一致（前端靠它做"点之前就知道"）。"""
    ledger.issue("WS-T", 8, slot_dir=SLOT, file="x", wake="manual")
    pre = client.get("/api/workshop/WS-T/tasks/8/evidence-view?level=L1").json()
    assert pre["given_level"] == "L1"
    post = client.post("/api/workshop/WS-T/tasks/8/review",
                       json={"result": "通过", "evidence_level": "L1"}).json()
    assert pre["blocking"] == post["blocking"] == "产物在盘上", (pre["blocking"], post["blocking"])
    assert pre["who_should_fix"] == post["who_should_fix"] == "员工"


# ── 判据 7：只读视图**不写盘** ───────────────────────────────────
def test_evidence_view_does_not_touch_disk(client, env):
    ledger.issue("WS-T", 6, slot_dir=SLOT, file="x")
    (env["ws"] / SLOT / "产物" / "a.md").write_text("x", encoding="utf-8")
    arts.scan(env["ws"], [SLOT])
    snap = env["ws"] / "_meta" / "artifacts.json"
    before_sha = hashlib.sha256(snap.read_bytes()).hexdigest()
    before_mtime = snap.stat().st_mtime_ns
    ledger_before = (env["tmp"] / "data" / "task_ledger.json").read_bytes()

    for _ in range(3):
        _view(client, 6)

    assert hashlib.sha256(snap.read_bytes()).hexdigest() == before_sha, "只读视图不许改产物快照"
    assert snap.stat().st_mtime_ns == before_mtime, "mtime 也不许动"
    assert (env["tmp"] / "data" / "task_ledger.json").read_bytes() == ledger_before, "不许改账本"


# ── 附加：已落闸的任务视图不再喊"卡住" ───────────────────────────
def test_accepted_task_view_shows_closure_not_blocking(client, env):
    ledger.issue("WS-T", 7, slot_dir=SLOT, file="x")
    (env["ws"] / SLOT / "产物" / "a.md").write_text("x", encoding="utf-8")
    ok = client.post("/api/workshop/WS-T/tasks/7/review",
                     json={"result": "通过", "evidence_level": "L4", "note": "本人看过"}).json()
    assert ok["ok"] and ok["state"] == "accepted", ok
    view = _view(client, 7)
    assert view["blocking"] == "", "已经落闸了就不该再说卡在哪"
    assert view["steps"][3]["ok"] is True, view["steps"][3]
    assert "accepted" in view["steps"][3]["what"]
