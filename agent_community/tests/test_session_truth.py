# -*- coding: utf-8 -*-
"""守卫（`V102`）：**真相核对** —— 去「对方应用自己的库」里查这句话在不在。

## 为什么要有这一份

2026-10-25（`V101`）：外部 agent 每次回 `ok:true / in_session:true / message_id`（每次还都不一样），
平台用它自己的"读回"工具**也找到了口令**，可用户界面上什么都没有 —— 它自己的库里 12:58 之后一条都没落。
⇒ 判据只能是「**对方的存储里到底有没有这句话**」。本文件盯这条链的真身：

判据（全部真库 / 真 WAL / 真档案，不看源码文本）：
  ① **WAL**：写入留在 `库-wal` 里（主库没变）时仍要找得到 —— 只拷主库会假阴性（真教训）；
  ② 三档结论不许混：`0`=有 / `1`=没有 / `2`=核对不了；**2 绝不能被说成"库里没有"或"通过"**；
  ③ 找不到时把库里**最近的口令**捞出来（界面把口令截断过，这是防误判的那条）；
  ④ 配置端点：真打开一次才存得下；错表名要**列出实际表**；关掉要真关掉；
  ⑤ `test-now` 自动核对：真档案 + 真库 ⇒ 写进去=绿勾；把那行删掉 ⇒ 假成功被抓住。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

import pytest  # noqa: E402

from agent_community.platform import session_truth as st  # noqa: E402
from agent_community.platform.routers import docking  # noqa: E402

NONCE = "收到-ABC123"


# ── 夹具：真 SQLite（WAL）────────────────────────────────────────────────
def _make_db(path: Path, texts, *, wal_open: bool = True, table: str = "agent_session_message"):
    """造一个真库并写几条消息；`wal_open=True` 时**保持连接不关** ⇒ 数据还在 `-wal` 里。"""
    con = sqlite3.connect(str(path))
    con.execute("pragma journal_mode=WAL")
    con.execute("create table %s (id text, session_id text, data text, created_at integer)" % table)
    con.commit()
    keep = sqlite3.connect(str(path))
    keep.execute("pragma journal_mode=WAL")
    base = 1750000000000
    for i, txt in enumerate(texts):
        keep.execute("insert into %s values(?,?,?,?)" % table,
                     ("m%d" % i, "s-1", txt, base + i * 60000))
    keep.commit()
    if wal_open:
        return keep                      # 调用方负责关（关之前数据一直在 -wal 里）
    keep.close()
    con.close()
    return None


def _close(con):
    if con is not None:
        try:
            con.close()
        except sqlite3.Error:                                # noqa: BLE001 — 测试收尾
            pass


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _as_dict(res) -> dict:
    """handler 可能直接回 dict，也可能回 Response —— 两种都还原成 dict。"""
    if isinstance(res, dict):
        return res
    raw = getattr(res, "body", b"")
    import json as _json

    return _json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))


def _status(res) -> int:
    return int(getattr(res, "status_code", 200) or 200)


# ── ① WAL：只拷主库会假阴性 ────────────────────────────────────────────
def test_wal_new_write_is_visible(tmp_path):
    db = tmp_path / "app.sqlite"
    keep = _make_db(db, ["旧消息", "请回复：收到（口令：%s，请原样带回）" % NONCE])
    try:
        # 主库文件确实还没吸收这条写入（数据在 -wal 里）——否则这条用例证明不了 WAL 那条教训
        assert os.path.exists(str(db) + "-wal"), "夹具没造出 WAL 文件，用例无效"
        res = st.check(str(db), NONCE)
        assert res["code"] == 0, "WAL 里的新写入必须找得到（%s）" % res.get("why")
        assert res["found"] is True
        assert res["matches"], "找到了就必须给出命中的那条"
    finally:
        _close(keep)


# ── ② 三档结论 ─────────────────────────────────────────────────────────
def test_missing_needle_is_code_1_and_lists_recent_codes(tmp_path):
    db = tmp_path / "app.sqlite"
    keep = _make_db(db, ["最近的回复（口令：收到-ZZZ999）", "再早一条（口令：收到-YYY888）"])
    try:
        res = st.check(str(db), "收到-NOPE00")
        assert res["code"] == 1, "库里没有 ⇒ code=1（不是 2，也不是通过）"
        assert res["found"] is False and res["ok"] is False
        codes = [c["code"] for c in res.get("recent_codes") or []]
        assert "收到-ZZZ999" in codes, "找不到时要给出库里最近的口令（防口令被界面截断误判）"
        assert "NOPE" in res["verdict"], "结论要说清'它只是说发出去了'，不能只给 code"
    finally:
        _close(keep)


def test_unreadable_db_is_code_2_and_never_says_missing(tmp_path):
    # 文件不存在
    res = st.check(str(tmp_path / "nope.sqlite"), NONCE)
    assert res["code"] == 2 and res["found"] is False
    assert "找不到" in res["why"], "要说清是'找不到文件'（核对不了），不是'库里没有'"

    # 不是 SQLite
    fake = tmp_path / "fake.db"
    fake.write_text("not a database", encoding="utf-8")
    res2 = st.check(str(fake), NONCE)
    assert res2["code"] == 2, "不是 SQLite ⇒ 核对不了（不许判成 0/1）"
    assert "SQLite" in res2["why"], "要说清平台只支持 SQLite"


def test_wrong_table_and_columns_speak_the_truth(tmp_path):
    db = tmp_path / "app.sqlite"
    keep = _make_db(db, ["x"], table="messages_real")
    try:
        res = st.check(str(db), NONCE, table="agent_session_message")
        assert res["code"] == 2
        assert "messages_real" in res["why"], "表名不对时要把库里**实际**的表列出来"

        probe = st.probe(str(db), table="messages_real", text_col="no_such_col")
        assert probe["ok"] is False
        assert "no_such_col" in probe["why"] and "data" in probe["why"], \
            "列名不对时要列出这张表**实际**有哪些列（%s）" % probe["why"]
    finally:
        _close(keep)


def test_describe_for_user_three_states():
    ok = st.describe_for_user({"code": 0})
    miss = st.describe_for_user({"code": 1})
    unk = st.describe_for_user({"code": 2, "why": "库打不开"})
    assert ok["state"] == "ok" and ok["ok"] is True and ok["note"].startswith("✓")
    assert miss["state"] == "missing" and miss["ok"] is False and miss["note"].startswith("✗")
    assert unk["state"] == "unknown" and unk["ok"] is False and "库打不开" in unk["note"]


# ── ④ 配置端点 ─────────────────────────────────────────────────────────
def _register(hid="h-truth", metadata=None):
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.protocol import HarnessInfo

    info = HarnessInfo(harness_id=hid, harness_name="示例Harness-A", harness_type="custom",
                       ai={"model_name": "m", "provider": "p"}, wakeup_method="clipboard")
    info.metadata = dict(metadata or {})

    class _S:
        def __init__(self, i):
            self.info = i

    harness_manager.sessions[hid] = _S(info)
    return hid, info


def _cleanup(hid):
    from agent_community.platform.harness_adapter import harness_manager

    harness_manager.sessions.pop(hid, None)


def test_config_endpoint_unconfigured_then_saved(tmp_path):
    hid, info = _register()
    db = tmp_path / "app.sqlite"
    keep = _make_db(db, ["hello"])
    try:
        got = _as_dict(asyncio.run(docking.docking_harness_truth_config(hid)))
        assert got["configured"] is False and got["enabled"] is False, "没配过就说没配过，不许编默认路径"
        assert got["defaults"]["table"] == st.DEFAULTS["table"]

        res = asyncio.run(docking.docking_harness_truth_config_set(hid, _Req({"db_path": str(db)})))
        body = _as_dict(res)
        assert _status(res) == 200 and body["ok"] is True and body["enabled"] is True
        assert body["db_path"] == str(db)
        assert body["probe"]["rows"] == 1, "保存前要真读到内容（%r）" % body.get("probe")

        again = _as_dict(asyncio.run(docking.docking_harness_truth_config(hid)))
        assert again["configured"] is True and again["db_path"] == str(db)
        assert info.metadata["truth"]["enabled"] is True, "要真写进档案（落盘走 save_state）"
    finally:
        _close(keep)
        _cleanup(hid)


def test_config_endpoint_rejects_bad_db_with_reason(tmp_path):
    hid, _info = _register()
    try:
        res = asyncio.run(docking.docking_harness_truth_config_set(
            hid, _Req({"db_path": str(tmp_path / "missing.sqlite")})))
        body = _as_dict(res)
        assert _status(res) == 400, "路径不存在 ⇒ 400，不许'看起来保存成功'"
        assert "找不到这个文件" in (body.get("why") or body.get("error") or "")
        assert body.get("next"), "失败必须给下一步"

        db = tmp_path / "app.sqlite"
        keep = _make_db(db, ["x"])
        try:
            res2 = asyncio.run(docking.docking_harness_truth_config_set(
                hid, _Req({"db_path": str(db), "table": "wrong_table"})))
            body2 = _as_dict(res2)
            assert _status(res2) == 400
            assert "agent_session_message" in (body2.get("why") or ""), "要把实际表名列出来"
        finally:
            _close(keep)
    finally:
        _cleanup(hid)


def test_config_endpoint_disable_really_disables(tmp_path):
    hid, info = _register()
    db = tmp_path / "app.sqlite"
    keep = _make_db(db, ["x"])
    try:
        asyncio.run(docking.docking_harness_truth_config_set(hid, _Req({"db_path": str(db)})))
        res = asyncio.run(docking.docking_harness_truth_config_set(hid, _Req({"db_path": "", "enabled": False})))
        body = _as_dict(res)
        assert body["ok"] is True and body["enabled"] is False
        assert info.metadata["truth"]["enabled"] is False, "关掉要真的关掉（否则又偷偷跑）"
        after = _as_dict(asyncio.run(docking.docking_harness_truth_config(hid)))
        assert after["enabled"] is False
        assert after["db_path"] == str(db), "路径留着，下次开不用重填"
    finally:
        _close(keep)
        _cleanup(hid)


# ── ⑤ truth-check 端点 + test-now 自动核对 ─────────────────────────────
def test_truth_check_uses_last_test_nonce(tmp_path):
    hid, _info = _register(metadata={"last_test": {"nonce": NONCE, "ok": True}})
    db = tmp_path / "app.sqlite"
    keep = _make_db(db, ["请回复：收到（口令：%s，请原样带回）" % NONCE])
    try:
        asyncio.run(docking.docking_harness_truth_config_set(hid, _Req({"db_path": str(db)})))
        res = _as_dict(asyncio.run(docking.docking_harness_truth_check(hid, _Req({}))))
        assert res["code"] == 0 and res["ok"] is True, "口令取自上次测一次 ⇒ 库里找得到"
        assert res["needle"] == NONCE
        assert res["ui"]["state"] == "ok" and res["note"].startswith("✓")
    finally:
        _close(keep)
        _cleanup(hid)


def test_truth_check_without_needle_or_config_speaks(tmp_path):
    hid, _info = _register()
    try:
        res = _as_dict(asyncio.run(docking.docking_harness_truth_check(hid, _Req({}))))
        assert "还没登记" in (res.get("error") or ""), "没配库就要说清，不许默默返回空"
        assert res.get("next")

        db = tmp_path / "app.sqlite"
        keep = _make_db(db, ["x"])
        try:
            asyncio.run(docking.docking_harness_truth_config_set(hid, _Req({"db_path": str(db)})))
            res2 = _as_dict(asyncio.run(docking.docking_harness_truth_check(hid, _Req({}))))
            assert "没有可核对的口令" in (res2.get("error") or "")
            assert "测一次" in (res2.get("next") or ""), "要告诉用户去哪儿拿口令"
        finally:
            _close(keep)
    finally:
        _cleanup(hid)


def test_test_now_auto_runs_truth_and_catches_false_success(tmp_path):
    """★核心那条：它说发出去了、库里也真有 ⇒ 绿勾；把库里那行删掉 ⇒ 立刻抓住假成功。"""
    hid, info = _register(metadata={"truth": {"db_path": "", "enabled": True}})
    db = tmp_path / "app.sqlite"
    keep = _make_db(db, ["请回复：收到（口令：%s，请原样带回）" % NONCE])
    try:
        info.metadata["truth"] = {
            "db_path": str(db), "enabled": True,
            "table": st.DEFAULTS["table"], "text_col": st.DEFAULTS["text_col"],
            "time_col": st.DEFAULTS["time_col"], "session_col": st.DEFAULTS["session_col"],
        }
        # 纯探活（不带 text）⇒ 没有口令 ⇒ 如实说核不了
        no_text = asyncio.run(docking.docking_harness_test_now(hid, _Req({})))
        no_text = _as_dict(no_text)
        assert no_text["truth"]["code"] == 2 and no_text["truth_verified"] is False
        assert "没有口令" in no_text["truth"]["why"]

        # 带 text ⇒ 平台造口令；库里已有同口令的那条 ⇒ 绿勾
        with_text = _as_dict(asyncio.run(docking.docking_harness_test_now(
            hid, _Req({"text": "请回复：收到", "nonce": NONCE}))))
        assert with_text["truth_verified"] is True, "库里真有 ⇒ 绿勾（%r）" % with_text.get("truth_note")
        assert with_text["truth_note"].startswith("✓")
        assert with_text["in_session_verified_scope"], "必须带上'读回不代表界面'的限定"
        assert "不代表它的界面" in with_text["in_session_verified_scope"]

        # 把库里那条删掉 ⇒ 假成功被抓住
        keep.execute("delete from agent_session_message where data like ?", ("%" + NONCE + "%",))
        keep.commit()
        caught = _as_dict(asyncio.run(docking.docking_harness_test_now(
            hid, _Req({"text": "请回复：收到", "nonce": NONCE + "X"}))))
        assert caught["truth_verified"] is False, "库里没有就不能算通过"
        assert caught["truth"]["code"] == 1
        assert caught["truth_note"].startswith("✗")
    finally:
        _close(keep)
        _cleanup(hid)


def test_test_now_without_truth_config_has_no_truth_field(tmp_path):
    hid, _info = _register()
    try:
        res = _as_dict(asyncio.run(docking.docking_harness_test_now(hid, _Req({"text": "请回复：收到"}))))
        assert "truth" not in res, "没配库就不该出现 truth 字段（默认关，老用法零影响）"
        assert "truth_verified" not in res
        assert "不代表它的界面" in (res.get("in_session_verified_scope") or ""), \
            "限定额度那句话**无论配没配都要有**（V101 §4.1）"
    finally:
        _cleanup(hid)


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
