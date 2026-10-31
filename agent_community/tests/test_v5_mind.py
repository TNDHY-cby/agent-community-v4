# -*- coding: utf-8 -*-
"""守卫：**双根（根A 心智根 / 根B 工作区）+ manifest G0 + Boot Prompt + 日结**（P2.5）。

对应 `V117` §8 的判据草案：双根 8 格（D-R1~D-R8）+ Boot Prompt 真实性。
全部真跑：真目录、真文件、真 HTTP。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform import boot_prompt as boot  # noqa: E402
from agent_community.platform import manifest as mf  # noqa: E402
from agent_community.platform import mind  # noqa: E402


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """两套根：根A（minds）与根B（workspace），**故意放在不同父目录下**。"""
    minds = tmp_path / "minds"
    wsr = tmp_path / "ws"
    wsr.mkdir()
    monkeypatch.setenv("AC_MIND_ROOT", str(minds))
    monkeypatch.setattr(mind, "mind_root", lambda: minds, raising=True)
    return {"minds": minds, "ws": wsr}


# ── 根A：建骨架、幂等、只增不改 ────────────────────────────────────
def test_dr5_mind_scaffold_and_idempotent(roots):
    out = mind.ensure("A-01")
    p = Path(out["mind_path"])
    for name in ("00_宪章.md", "00_核心基模.md", "01_记忆索引表.md"):
        assert (p / name).is_file(), name
    for d in ("memories", "02_任务日志", "06_死胡同", "_meta"):
        assert (p / d).is_dir(), d
    assert out["created"], "第一次要报建了什么"
    again = mind.ensure("A-01")
    assert again["created"] == [], "第二次不许再建、不许覆盖已有文件"


def test_mind_memories_append_only(roots):
    mind.ensure("A-01")
    a = mind.add_memory("A-01", "打包后要验体积", tags=["打包"])
    b = mind.add_memory("A-01", "别在迁移中间改索引", tags=["迁移"])
    assert a["file"] != b["file"], "同一秒两条也不能覆盖"
    files = sorted(x.name for x in (Path(a["mind_path"]) / "memories").glob("*.md"))
    assert len(files) == 2
    assert "打包后要验体积" in (Path(a["mind_path"]) / "memories" / a["file"]).read_text(encoding="utf-8")


# ── manifest：G0 四条 ─────────────────────────────────────────────
def _good(roots, **kw):
    (roots["ws"] / "_lead").mkdir(parents=True, exist_ok=True)
    (roots["ws"] / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    body = mf.build(workshop_id="WS-T", workshop_path=str(roots["ws"]),
                    mind_path=str(roots["minds"] / "A-01"),
                    entry_points=["_lead/建模.md"], ignore_zones=["_meta/evidence/"],
                    task_focus="接口联调")
    body.update(kw)
    return body


def test_dr1_manifest_missing_blocks(roots):
    ok, body, errs = mf.read(roots["ws"])
    assert not ok and errs and errs[0]["code"] == "M000", "缺文件要说清是哪个文件"
    ok2, code2, errs2, four = mf.validate(None, workshop_path=str(roots["ws"]))
    assert not ok2 and code2 == "GATE_G0" and set(four) == {"who", "what", "where", "how"}


def test_dr2_mind_path_inside_workshop_rejected(roots):
    nested = roots["ws"] / "_meta" / "minds"
    nested.mkdir(parents=True)
    body = _good(roots, mind_path=str(nested))
    ok, code, errs, four = mf.validate(body, workshop_path=str(roots["ws"]))
    assert not ok and errs[0]["code"] == "M005", "心智根放进工作区必须拦"
    assert "记忆就丢了" in errs[0]["message"] or "归档" in errs[0]["message"]


def test_dr3_entry_points_must_really_exist(roots):
    body = _good(roots)
    body["entry_points"] = ["_lead/建模.md", "前端组/分工.md"]     # 第二个不存在
    ok, code, errs, four = mf.validate(body, workshop_path=str(roots["ws"]))
    assert not ok and errs[0]["code"] == "M007" and "分工.md" in errs[0]["message"]
    (roots["ws"] / "前端组").mkdir()
    (roots["ws"] / "前端组" / "分工.md").write_text("# 分工\n", encoding="utf-8")
    ok2, _, errs2, _ = mf.validate(body, workshop_path=str(roots["ws"]))
    assert ok2, errs2


def test_dr4_version_mismatch_blocked(roots):
    body = _good(roots, manifest_version="coop.workshop.manifest/9")
    ok, code, errs, four = mf.validate(body, workshop_path=str(roots["ws"]))
    assert not ok and errs[0]["code"] == "M003"
    assert mf.MANIFEST_VERSION in four["how"]


def test_dr4b_entry_points_empty_blocked(roots):
    body = _good(roots, entry_points=[])
    ok, code, errs, _ = mf.validate(body, workshop_path=str(roots["ws"]))
    assert not ok and errs[0]["code"] == "M006"


def test_manifest_hash_is_stable_and_sensitive(roots):
    body = _good(roots)
    h1 = mf.content_hash(body)
    assert h1 == mf.content_hash(dict(reversed(list(body.items())))), "键序不该影响 hash"
    body2 = dict(body, task_focus="换了焦点")
    assert mf.content_hash(body2) != h1, "改一个字段 hash 必须变"


def test_manifest_write_then_read(roots):
    body = _good(roots)
    out = mf.write(roots["ws"], body)
    assert Path(out["path"]).is_file()
    ok, got, errs = mf.read(roots["ws"])
    assert ok and got["workshop_id"] == "WS-T" and "manifest_hash" not in got, \
        "hash 不写进文件（避免自指）"


# ── Boot Prompt ───────────────────────────────────────────────────
def test_dr8_boot_prompt_writes_real_paths(roots):
    mind.ensure("A-01")
    text = boot.render(agent_key="A-01", workshop_path=str(roots["ws"]),
                       mind_path=str(roots["minds"] / "A-01"), task_focus="接口联调",
                       ignore_zones=["_meta/evidence/"])
    assert "【系统启动协议（Boot Protocol）】" in text
    assert str(roots["ws"]) in text and str(roots["minds"] / "A-01") in text, "两个根要写死"
    assert "禁止" in text and "_meta/evidence/" in text, "红线与黑名单要在里面"
    ok, bad = boot.paths_are_real(text)
    assert ok, "声明路径必须真实存在：%s" % bad


def test_boot_prompt_flags_placeholders(roots):
    text = boot.render(agent_key="A-01", workshop_path=str(roots["ws"]),
                       mind_path=str(roots["minds"] / "not-created"))
    ok, bad = boot.paths_are_real(text)
    assert not ok and bad, "不存在的根要被判出来（不许假装真）"


# ── 日结：写回根A（同一天追加，不覆盖）──────────────────────────
def test_dr6_daily_close_writes_task_log_and_dead_end(roots):
    r1 = mind.append_daily("A-01", date="2026-10-27", body="## 做完了\n- 接口联调",
                           kind="task_log")
    r2 = mind.append_daily("A-01", date="2026-10-27", body="## 做完了\n- 自测通过",
                           kind="task_log")
    f = Path(r1["mind_path"]) / "02_任务日志" / "2026-10-27.md"
    body = f.read_text(encoding="utf-8")
    assert "接口联调" in body and "自测通过" in body, "同一天要追加，不许覆盖"
    assert r1["file"] == r2["file"]
    d = mind.append_daily("A-01", date="2026-10-27", body="## 卡住\n- 桥没起", kind="dead_end")
    assert "06_死胡同" in d["file"]
    assert (Path(d["mind_path"]) / "06_死胡同" / "2026-10-27.md").is_file()


# ── HTTP 端到端 ───────────────────────────────────────────────────
@pytest.fixture()
def client(roots, monkeypatch):
    monkeypatch.setenv("AC_DATA_DIR", str(roots["minds"].parent / "data"))
    monkeypatch.setattr(mind, "mind_root", lambda: roots["minds"], raising=True)
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    ws = srv.Workshop(workshop_id="WS-T", name="示例间", workspace_dir=str(roots["ws"]),
                      hall_content="方向")
    srv.workshops["WS-T"] = ws
    return TestClient(srv.app, client=("127.0.0.1", 54321))


def test_http_manifest_g0_and_boot_prompt(client, roots):
    (roots["ws"] / "_lead").mkdir(parents=True, exist_ok=True)
    (roots["ws"] / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    # 1) 没 manifest ⇒ 409 + 四要素
    r = client.get("/api/workshop/WS-T/manifest")
    assert r.status_code == 409 and set(r.json()["four"]) == {"who", "what", "where", "how"}
    # 2) 心智根放进工作区 ⇒ 422 拒收
    bad = client.post("/api/workshop/WS-T/manifest",
                      json={"mind_path": str(roots["ws"] / "minds"),
                            "entry_points": ["_lead/建模.md"]})
    assert bad.status_code == 422 and bad.json()["errors"][0]["code"] == "M005"
    # 3) 正常写
    good = client.post("/api/workshop/WS-T/manifest",
                       json={"mind_path": str(roots["minds"] / "A-01"),
                             "entry_points": ["_lead/建模.md"],
                             "ignore_zones": ["_meta/evidence/"],
                             "task_focus": "接口联调"})
    assert good.status_code == 200 and good.json()["ok"], good.text
    # 4) 读回 + G0 通过
    got = client.get("/api/workshop/WS-T/manifest")
    assert got.status_code == 200 and got.json()["ok"] is True
    # 5) Boot Prompt：两个根写死且真实
    bp = client.post("/api/workshop/WS-T/boot-prompt", json={"agent_key": "A-01"})
    assert bp.status_code == 200
    b = bp.json()
    assert b["paths_are_real"] is True and b["manifest_ok"] is True
    assert str(roots["minds"] / "A-01") in b["boot_prompt"]


def test_http_daily_close_and_memory(client, roots):
    assert client.post("/api/mind/A-01", json={"display_name": "示例"}).json()["ok"]
    m = client.post("/api/mind/A-01/memories", json={"text": "联调先看 manifest", "tags": ["联调"]})
    assert m.status_code == 200 and m.json()["ok"]
    d = client.post("/api/workshop/WS-T/daily-close",
                    json={"agent_key": "A-01", "date": "2026-10-27",
                          "done": ["接口联调完成"], "blocked": ["桥没起"]})
    assert d.status_code == 200 and len(d.json()["written"]) == 2, d.text
    info = client.get("/api/mind/A-01").json()
    assert info["counts"]["memories"] == 1 and info["counts"]["task_logs"] == 1


def test_http_daily_close_rejects_empty(client):
    r = client.post("/api/workshop/WS-T/daily-close", json={"agent_key": "A-01"})
    assert r.status_code == 400 and r.json()["code"] == "EMPTY"

# ── 待审队列（`_pending/*.diff`）：**只读**两条端点（W前端-52 §5）───────
def _seed_pending(roots, key="A-01", *, approved=False):
    mind.ensure(key)
    d = Path(mind.mind_path(key)) / "_pending"
    d.mkdir(parents=True, exist_ok=True)
    diff = ("--- a/00_核心基模.md\n+++ b/00_核心基模.md\n@@ -1 +1 @@\n"
            "-我擅长：<待填>\n+我擅长：写判据\n")
    (d / "core-20261029T0001.diff").write_text(diff, encoding="utf-8")
    if approved:
        (d / "core-20261029T0001.approved.json").write_text(
            json.dumps({"approved_by": "boss", "at": "2026-10-29T00:10:00"}), encoding="utf-8")
    return d, diff


def test_pending_list_readonly(roots):
    d, diff = _seed_pending(roots, approved=True)
    before = {p.name: p.stat().st_size for p in d.iterdir()}
    out = mind.pending_list("A-01")
    after = {p.name: p.stat().st_size for p in d.iterdir()}
    assert before == after, "列待审项不许写盘（一个字节都不许变）"
    assert out["count"] == 1
    it = out["items"][0]
    assert it["name"] == "core-20261029T0001.diff"
    assert it["status"] == "approved" and it["approved_by"] == "boss"
    assert it["target"] == "00_核心基模.md", "目标文件要从 diff 头里读出来"
    assert it["diff_lines"] == diff.count("\n")
    assert "平台不代批" in it["note"]


def test_pending_read_returns_raw_text(roots):
    _d, diff = _seed_pending(roots)
    out = mind.pending_read("A-01", "core-20261029T0001.diff")
    assert out["ok"] is True and out["text"] == diff
    assert out["target"] == "00_核心基模.md"


def test_pending_read_rejects_traversal(roots):
    _seed_pending(roots)
    for bad in ("../x.diff", "a/b.diff", "..\\x.diff", "x.txt", "", ".."):
        with pytest.raises(ValueError):
            mind.pending_read("A-01", bad)


def test_pending_missing_is_honest_404(roots):
    _seed_pending(roots)
    out = mind.pending_read("A-01", "nope.diff")
    assert out["ok"] is False and out["code"] == "NO_SUCH_PENDING" and out["next"]


def test_http_pending_endpoints(roots):
    """真 HTTP：列表 200 / 单条 200 / 坏名 400 / 不存在 404。"""
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    d, diff = _seed_pending(roots)
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    listed = c.get("/api/mind/A-01/pending")
    assert listed.status_code == 200 and listed.json()["count"] == 1
    one = c.get("/api/mind/A-01/pending/core-20261029T0001.diff")
    assert one.status_code == 200 and one.json()["text"] == diff
    bad = c.get("/api/mind/A-01/pending/x.txt")
    assert bad.status_code == 400 and bad.json()["code"] == "BAD_PENDING_NAME"
    gone = c.get("/api/mind/A-01/pending/nope.diff")
    assert gone.status_code == 404 and gone.json()["code"] == "NO_SUCH_PENDING"

# ── 待审项「批准」：**只记审批、不动基模**（W前端-53 在等的按钮）──────
def test_approve_requires_approver(roots):
    _seed_pending(roots)
    with pytest.raises(ValueError):
        mind.approve_pending("A-01", "core-20261029T0001.diff", approver="   ")


def test_approve_records_audit_and_keeps_core_untouched(roots):
    import hashlib

    _seed_pending(roots)
    core = Path(mind.mind_path("A-01")) / "00_核心基模.md"
    before = hashlib.sha256(core.read_bytes()).hexdigest()
    out = mind.approve_pending("A-01", "core-20261029T0001.diff", approver="boss")
    after = hashlib.sha256(core.read_bytes()).hexdigest()
    assert out["ok"] is True and out["already"] is False and out["approved_by"] == "boss"
    assert before == after, "批准**不许**改核心基模（平台不代改认知）"
    assert "核心基模仍未改" in out["note"]
    rec = json.loads(Path(out["approved_record"]).read_text(encoding="utf-8"))
    for k in ("approved_by", "at", "diff", "diff_sha256", "target", "core_before_sha256"):
        assert k in rec, "审计字段缺 %s" % k
    assert rec["target"] == "00_核心基模.md"


def test_approve_is_not_overwritten_by_second_approver(roots):
    _seed_pending(roots)
    mind.approve_pending("A-01", "core-20261029T0001.diff", approver="boss")
    again = mind.approve_pending("A-01", "core-20261029T0001.diff", approver="别人")
    assert again["already"] is True and again["approved_by"] == "boss", "不许悄悄改批准人"
    assert "没有覆盖" in again["note"]


def test_approve_missing_is_404_shape(roots):
    _seed_pending(roots)
    out = mind.approve_pending("A-01", "nope.diff", approver="boss")
    assert out["ok"] is False and out["code"] == "NO_SUCH_PENDING" and out["next"]


def test_http_approve_endpoint(roots):
    """真 HTTP：200 批准 / 重复批准 already / 无批准人 400 / 坏名 400 / 不存在 404。"""
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    _seed_pending(roots)
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    ok = c.post("/api/mind/A-01/pending/core-20261029T0001.diff/approve",
                json={"approver": "boss"})
    assert ok.status_code == 200 and ok.json()["approved_by"] == "boss"
    again = c.post("/api/mind/A-01/pending/core-20261029T0001.diff/approve",
                   json={"approver": "别人"})
    assert again.status_code == 200 and again.json()["already"] is True
    no_who = c.post("/api/mind/A-01/pending/core-20261029T0001.diff/approve", json={})
    assert no_who.status_code == 400 and no_who.json()["code"] == "APPROVER_REQUIRED"
    bad = c.post("/api/mind/A-01/pending/x.txt/approve", json={"approver": "boss"})
    assert bad.status_code == 400 and bad.json()["code"] == "BAD_PENDING_NAME"
    gone = c.post("/api/mind/A-01/pending/nope.diff/approve", json={"approver": "boss"})
    assert gone.status_code == 404 and gone.json()["code"] == "NO_SUCH_PENDING"

# ── 批准之后**真正落地**：apply（导师信「灵魂火种」，2026-10-29）──────
def _seed_real_patch(roots, key="A-01"):
    """造一条**真 unified diff**（把基模里「我擅长」那行换掉）＋一条**建议稿**。"""
    mind.ensure(key)
    core = Path(mind.mind_path(key)) / "00_核心基模.md"
    lines = core.read_text(encoding="utf-8").splitlines()
    idx = next(i for i, l in enumerate(lines) if "我擅长" in l)
    d = Path(mind.mind_path(key)) / "_pending"
    d.mkdir(parents=True, exist_ok=True)
    patch = ("--- a/00_核心基模.md\n+++ b/00_核心基模.md\n@@ -%d,1 +%d,1 @@\n-%s\n+%s\n"
             % (idx + 1, idx + 1, lines[idx], lines[idx].replace("<待填>", "写判据、不粉饰")))
    (d / "core-real.diff").write_text(patch, encoding="utf-8")
    (d / "core-advice.diff").write_text(
        "--- 待审：00_核心基模.md\n+++ 建议：00_核心基模.md\n\n# 审忆员说明\n建议补一条\n\n# 改前 hash\nabc\n",
        encoding="utf-8")
    return core, patch


def test_apply_requires_approval(roots):
    """没批准就落地 ⇒ 拒 `NOT_APPROVED`（并告诉它先去 approve）。"""
    _core, _patch = _seed_real_patch(roots)
    out = mind.apply_pending("A-01", "core-real.diff", approver="boss")
    assert out["ok"] is False and out["code"] == "NOT_APPROVED"
    assert "approve" in out["next"]


def test_apply_writes_core_and_archives_original(roots):
    """批准后落地：基模**真变了**、原文归档可回溯、返回 before/after 双 hash。"""
    import hashlib

    core, _patch = _seed_real_patch(roots)
    original_bytes = core.read_bytes()          # ★用字节：文本模式会做 CRLF 归一，hash 会差
    original = original_bytes.decode("utf-8")
    mind.approve_pending("A-01", "core-real.diff", approver="boss")
    out = mind.apply_pending("A-01", "core-real.diff", approver="boss")
    assert out["ok"] is True, out
    assert "写判据、不粉饰" in core.read_text(encoding="utf-8"), "批了就必须真写进去"
    assert out["before_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert out["after_sha256"] == hashlib.sha256(core.read_bytes()).hexdigest()
    arc = Path(out["archive"])
    assert arc.is_file() and arc.read_bytes() == original_bytes, "原文必须先归档（可回溯，按字节比）"
    rec = json.loads((Path(mind.mind_path("A-01")) / "_pending" / "core-real.applied.json")
                     .read_text(encoding="utf-8"))
    for k in ("applied_by", "at", "before_sha256", "after_sha256", "archive"):
        assert k in rec, "落地审计字段缺 %s" % k


def test_apply_refuses_when_core_changed(roots):
    """批准之后基模又被改过 ⇒ 拒 `CORE_CHANGED_SINCE_PROPOSAL`（不许盲套）。"""
    core, _patch = _seed_real_patch(roots)
    original = core.read_text(encoding="utf-8")
    mind.approve_pending("A-01", "core-real.diff", approver="boss")
    core.write_text(original + "\n- 手改了一行\n", encoding="utf-8")
    out = mind.apply_pending("A-01", "core-real.diff", approver="boss")
    assert out["ok"] is False and out["code"] == "CORE_CHANGED_SINCE_PROPOSAL"
    assert "重新" in out["next"]


def test_apply_refuses_advice_not_patch(roots):
    """建议稿（没有 hunk）不是补丁 ⇒ `NOT_A_UNIFIED_DIFF`，并说清让人手改。"""
    _seed_real_patch(roots)
    mind.approve_pending("A-01", "core-advice.diff", approver="boss")
    out = mind.apply_pending("A-01", "core-advice.diff", approver="boss")
    assert out["ok"] is False and out["code"] == "NOT_A_UNIFIED_DIFF"
    assert "手改" in out["next"]


def test_http_apply_endpoint(roots):
    """真 HTTP：未批 409 / 批准后 200 且基模真改 / 再套 409。"""
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    core, _patch = _seed_real_patch(roots)
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    bad = c.post("/api/mind/A-01/pending/core-real.diff/apply", json={"approver": "boss"})
    assert bad.status_code == 409 and bad.json()["code"] == "NOT_APPROVED"
    c.post("/api/mind/A-01/pending/core-real.diff/approve", json={"approver": "boss"})
    ok = c.post("/api/mind/A-01/pending/core-real.diff/apply", json={"approver": "boss"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert "写判据、不粉饰" in core.read_text(encoding="utf-8")
    again = c.post("/api/mind/A-01/pending/core-real.diff/apply", json={"approver": "boss"})
    assert again.status_code == 409 and again.json()["code"] == "CORE_CHANGED_SINCE_PROPOSAL"

def test_pending_list_marks_applied(roots):
    """落地之后列表必须报 `applied`（读真文件），并带 before/after/归档，不可再批。"""
    _core, _patch = _seed_real_patch(roots)
    mind.approve_pending("A-01", "core-real.diff", approver="boss")
    out = mind.apply_pending("A-01", "core-real.diff", approver="boss")
    assert out["ok"] is True
    items = {x["name"]: x for x in mind.pending_list("A-01")["items"]}
    it = items["core-real.diff"]
    assert it["status"] == "applied", it
    assert it["applied"] is True
    assert it["applied_by"] == "boss"
    assert it["before_sha256"] == out["before_sha256"]
    assert it["after_sha256"] == out["after_sha256"]
    assert it["archive"] == out["archive"]
    # 已落地 ⇒ 再落地必须被拒（基模已变）
    again = mind.apply_pending("A-01", "core-real.diff", approver="boss")
    assert again["ok"] is False and again["code"] == "CORE_CHANGED_SINCE_PROPOSAL"


def test_pending_list_reports_unreadable_applied_record(roots):
    """落地记录坏了 ⇒ `applied_record_unreadable`（如实说，不猜成 applied）。"""
    _core, _patch = _seed_real_patch(roots)
    mind.approve_pending("A-01", "core-real.diff", approver="boss")
    assert mind.apply_pending("A-01", "core-real.diff", approver="boss")["ok"] is True
    bad = Path(mind.mind_path("A-01")) / "_pending" / "core-real.applied.json"
    bad.write_text("{ 坏掉的 json", encoding="utf-8")
    items = {x["name"]: x for x in mind.pending_list("A-01")["items"]}
    it = items["core-real.diff"]
    assert it["status"] == "applied_record_unreadable", it
    assert it["applied"] is False


def test_pending_list_http_carries_applied(roots):
    """HTTP 列表也要带这几个字段（界面直接读，别自己拼）。"""
    from fastapi.testclient import TestClient

    from agent_community.platform import server as srv

    _core, _patch = _seed_real_patch(roots)
    c = TestClient(srv.app, client=("127.0.0.1", 54321))
    c.post("/api/mind/A-01/pending/core-real.diff/approve", json={"approver": "boss"})
    assert c.post("/api/mind/A-01/pending/core-real.diff/apply",
                  json={"approver": "boss"}).status_code == 200
    d = c.get("/api/mind/A-01/pending").json()
    it = [x for x in d["items"] if x["name"] == "core-real.diff"][0]
    for k in ("status", "applied", "applied_by", "applied_at", "before_sha256",
              "after_sha256", "archive"):
        assert k in it, "列表缺字段 %s" % k
    assert it["status"] == "applied"

# ── 审忆员产出真补丁 + 批准后能落地（2026-10-31）────────────────────
def test_reviewer_emits_real_patch_and_lands(tmp_path):
    """审忆员必须产出**带 hunk 的真补丁**（否则 apply 只会报 NOT_A_UNIFIED_DIFF＝"审完了没下文"）。"""
    import os
    import subprocess
    import sys

    root = tmp_path / "minds"
    os.environ["AC_MIND_ROOT"] = str(root)
    mind.ensure("A-09")
    md = Path(mind.mind_path("A-09"))
    (md / "02_任务日志").mkdir(parents=True, exist_ok=True)
    (md / "02_任务日志" / "2026-10-31.md").write_text(
        "# 日结\n\n- 做完了 X\n- 学到 Y\n", encoding="utf-8")
    repo = Path(__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, str(repo / "tools" / "mind_reviewer.py"),
                        "--date", "2026-10-31", "--mind-root", str(root), "--apply", "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(repo))
    assert r.returncode == 0, (r.stdout, r.stderr)
    pend = sorted((md / "_pending").glob("*.diff"))
    assert pend, "审忆员没产出待审项"
    body = pend[0].read_text(encoding="utf-8")
    assert "@@" in body, "待审项不是可套用的补丁（没有 hunk）—— 批准之后也落不了地"
    assert (md / "_pending" / (pend[0].stem + ".note.md")).is_file(), "缺「人批前必看」的说明文件"

    name = pend[0].name
    mind.approve_pending("A-09", name, approver="boss")
    out = mind.apply_pending("A-09", name, approver="boss")
    assert out.get("ok") is True, out
    core = (md / "00_核心基模.md").read_text(encoding="utf-8")
    assert "2026-10-31 夜间审忆" in core, "批了之后基模没真变（等于审完没下文）"
    assert Path(out["archive"]).is_file(), "原文没归档（不可回滚）"
