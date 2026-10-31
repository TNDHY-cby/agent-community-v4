#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据（`V102`）：**真相核对**在真平台上真能走通 —— 绿勾 / 红字 / 灰字 三态都要真出现。

## 为什么要有它

`V101` 的教训是"对方说通了不算"。那么新加的真相核对**本身**也得有个骗不过的判据：
不是"代码看起来对"，而是**对一个真的注册 harness、喂一个真的 SQLite 库**，
看三个端点回的到底是 绿勾（库里有）、红字（库里没有）、灰字（核对不了）。

## 它自己会做的事（**全部落在临时目录，跑完删掉**）

1. 造一个**真 SQLite（WAL）** 库，里面有一条带口令的消息；
2. 注册一个**临时 harness**（虚拟名 `示例Harness-真核自检`）—— 跑完 `DELETE` 掉；
3. `POST …/truth-config` 登记这个库 ⇒ 要 `ok:true` 且 `probe.rows>=1`；
4. `POST …/truth-check` 拿库里那条口令核 ⇒ 要 `code==0`（绿勾）；
5. 把库里那行删掉再核 ⇒ 要 `code==1`（红字，**不许还是绿**）；
6. 传一个不存在的库 ⇒ 要 **400** ＋ 有 `next`（**不许"看起来保存成功"**）；
7. `POST …/test-now`（不带 text）⇒ 要没有 `truth` 或 `truth.code==2`，且**必须**带
   `in_session_verified_scope`（"读回不代表界面"的限定，`V101` §4.1）。

退出码：0＝十格全过；1＝有格没过；2＝连不上平台 / 注册不了（如实说"没跑成"，不假绿）。

⚠️ **它会在目标平台上注册/删除一个临时 harness**（写人家的数据目录、留审计）。
   ⇒ **别在跑全量回归的同时跑它**（会撞上 guard_real_data_dir 的"真实数据被改"判定，
   2026-10-25 我就这么撞了一次）；要打线上就用 --base 明确指过去，跑完它自己删。

用法：
    python tools/truth_check.py                    # 打本机平台（AC_REGRESSION_BASE 或 18920）
    python tools/truth_check.py --base http://127.0.0.1:18920 --keep   # 留着临时 harness 便于手看
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

DEFAULT_BASE = os.environ.get("AC_REGRESSION_BASE") or "http://127.0.0.1:18920"
HID = "demo-truth-selfcheck"          # 临时 harness 的 id（纯 ASCII，便于放进 URL；跑完删）
HNAME = "示例Harness-真核自检"          # 给人看的显示名（虚拟名，不是任何真机的东西）
NONCE = "收到-TRUTH1"


def _call(base: str, method: str, path: str, body: dict | None = None,
          timeout: float = 60) -> tuple:
    """发一次真请求；返回 (status, json)。4xx/5xx 也要把响应体读出来（这是判据的一部分）。"""
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), json.loads(raw or "{}")
        except ValueError:
            return int(e.code), {"raw": raw[:400]}


def _make_db(path: str, texts) -> sqlite3.Connection:
    """真 SQLite + WAL（数据先落 `-wal`，正是 `V101` 那条教训）；返回保持打开的连接。"""
    con = sqlite3.connect(path)
    con.execute("pragma journal_mode=WAL")
    con.execute("create table agent_session_message "
                "(id text, session_id text, data text, created_at integer)")
    con.commit()
    keep = sqlite3.connect(path)
    keep.execute("pragma journal_mode=WAL")
    for i, t in enumerate(texts):
        keep.execute("insert into agent_session_message values(?,?,?,?)",
                     ("m%d" % i, "s-1", t, 1750000000000 + i * 60000))
    keep.commit()
    return keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE, help="平台地址（默认本机，可用 AC_REGRESSION_BASE）")
    ap.add_argument("--keep", action="store_true", help="跑完不删临时 harness（默认删）")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    fails, rows = [], []

    def check(name: str, ok: bool, detail: str = "") -> None:
        rows.append((name, bool(ok), detail))
        if not ok:
            fails.append("%s（%s）" % (name, detail))

    tmpdir = tempfile.mkdtemp(prefix="ac_truth_live_")
    db = os.path.join(tmpdir, "app.sqlite")
    keep_con = _make_db(db, ["请回复：收到（口令：%s，请原样带回）" % NONCE])
    try:
        # 0) 平台在不在
        try:
            st, _ = _call(base, "GET", "/api/status", timeout=15)
        except (urllib.error.URLError, OSError) as e:
            print("[FAIL] 连不上平台 %s：%s: %s" % (base, type(e).__name__, e))
            return 2
        check("平台活着", st == 200, "HTTP %s" % st)

        # 1) 注册临时 harness（虚拟名；跑完删）
        st, reg = _call(base, "POST", "/api/harness/register", {
            "harness_id": HID, "harness_name": HNAME, "harness_type": "desktop-agent",
            "wakeup_method": "clipboard", "ai": {"model_name": "示例模型"}})
        if st >= 400:
            print("[FAIL] 注册不了临时 harness（HTTP %s）：%s" % (st, json.dumps(reg, ensure_ascii=False)[:300]))
            return 2

        # 2) 登记库 ⇒ 要真读到内容
        st, cfg = _call(base, "POST", "/api/docking/harness/%s/truth-config" % HID, {"db_path": db})
        check("登记库成功且真读到内容", st == 200 and bool(cfg.get("ok"))
              and int((cfg.get("probe") or {}).get("rows") or 0) >= 1,
              "HTTP %s rows=%s" % (st, (cfg.get("probe") or {}).get("rows")))

        # 3) 库里有 ⇒ 绿勾
        st, hit = _call(base, "POST", "/api/docking/harness/%s/truth-check" % HID, {"needle": NONCE})
        check("库里有 ⇒ code=0 绿勾", st == 200 and hit.get("code") == 0 and hit.get("found") is True,
              "HTTP %s code=%s why=%s" % (st, hit.get("code"), hit.get("why")))
        check("结论自带图标（界面照抄）", str(hit.get("note") or "").startswith("✓"),
              str(hit.get("note") or "")[:80])

        # 4) 库里没有 ⇒ 红字（不是灰字、更不是绿）
        keep_con.execute("delete from agent_session_message where data like ?", ("%" + NONCE + "%",))
        keep_con.commit()
        st, miss = _call(base, "POST", "/api/docking/harness/%s/truth-check" % HID, {"needle": NONCE})
        check("库里删掉 ⇒ code=1 红字", st == 200 and miss.get("code") == 1,
              "HTTP %s code=%s why=%s" % (st, miss.get("code"), miss.get("why")))
        check("红字自带 ✗ 且给出库里最近的口令",
              str(miss.get("note") or "").startswith("✗"),
              "note=%s recent=%s" % (str(miss.get("note") or "")[:40], miss.get("recent_codes")))

        # 5) 路径不存在 ⇒ 400 ＋ 下一步（不许假成功）
        st, bad = _call(base, "POST", "/api/docking/harness/%s/truth-config" % HID,
                        {"db_path": os.path.join(tmpdir, "nope.sqlite")})
        check("路径不存在 ⇒ 400 且有 next", st == 400 and bool(bad.get("next")),
              "HTTP %s error=%s" % (st, str(bad.get("error") or "")[:80]))
        check("坏路径不许把原配置改坏",
              _call(base, "GET", "/api/docking/harness/%s/truth-config" % HID)[1].get("db_path") == db,
              "GET truth-config 的 db_path 应仍是好路径")

        # 6) test-now 必须带"读回不代表界面"的限定
        st, tn = _call(base, "POST", "/api/docking/harness/%s/test-now" % HID, {}, timeout=120)
        check("test-now 带限定语 in_session_verified_scope",
              st == 200 and "不代表它的界面" in str(tn.get("in_session_verified_scope") or ""),
              "HTTP %s scope=%s" % (st, str(tn.get("in_session_verified_scope") or "")[:60]))
        check("test-now 没口令时不给假绿勾",
              tn.get("truth_verified") is not True,
              "truth_verified=%r truth.code=%r" % (tn.get("truth_verified"),
                                                   (tn.get("truth") or {}).get("code")))
    finally:
        try:
            keep_con.close()
        except sqlite3.Error:                                  # noqa: BLE001 — 收尾失败不影响结论
            pass
        if not a.keep:
            _call(base, "DELETE", "/api/harness/%s" % urllib.parse.quote(HID), None)
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：真相核对真机十格 —— %s" % base)
    print("=" * 78)
    for name, ok, detail in rows:
        print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("　（%s）" % detail) if not ok else ""))
    print("-" * 78)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(rows), len(rows) - len(fails), len(fails)))
    if a.keep:
        print("（--keep：临时 harness %s 没删，自己看着办）" % HID)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
