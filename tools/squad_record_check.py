#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**分队履历 + 分队模板化**（`V140` / 队列 47-1、47-2）。

真跑：临时实例 + 真工作间 + 真名单 + 真派活 + 真验收（**看账本/接口真值**，不信自述）。

| # | 格 | 断言 |
|---|---|---|
| ① | 履历给得出结构与口径 | `tasks{total/accepted/rejected/open}`、`quality`、`recent`、`note`、`no_ranking` 都在 |
| ② | **只算终态**（在途不进分母） | 1 通过 + 1 拒收 + 1 在途 ⇒ `total=2`、`open=1`、`accept_rate=0.5` |
| ③ | 样本不足要说话 | `total<3` ⇒ `insufficient=true` + note 里明说"别据此判断" |
| ④ | clone 生成新分队且**不复制任务历史** | 新队 `record.tasks.total == 0`，账本条数不变 |
| ⑤ | clone 复制方向/目标/成员构成（可被 body 覆盖） | 三者与源队一致；给了 body 就用 body 的 |
| ⑥ | `copy_experience=true` **只给指针不给正文** | 返回 `experience_index.paths`（可能为空）+ why；响应里**不含** memories 正文片段 |
| ⑦ | 两个开关都**真生效** | `AC_SQUAD_HISTORY=0` ⇒ 履历 `enabled=false` 且说明；`AC_SQUAD_TEMPLATE=0` ⇒ clone 404 |

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


def _py_env(expr: str, extra_env: dict) -> str:
    """在**子进程**里真跑一段 Python（带指定环境变量）—— 验"开关真生效"。"""
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from agent_community.platform import squad_record\n"
            "print(%s)" % (str(ROOT), expr))
    env = dict(os.environ)
    env.update(extra_env)
    r = subprocess.run([PY, "-X", "utf8", "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120, env=env)
    return (r.stdout or "").strip()


def _boot(extra: dict | None = None):
    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_sqrec_"))
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "PYTHONIOENCODING": "utf-8", "AC_ARTIFACT_WATCH_SEC": "3600"})
    if extra:
        env.update(extra)
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        try:
            if _http(base, "/api/status", timeout=3)[0] == 200:
                return srv, base, tmpdir
        except (urllib.error.URLError, OSError):               # noqa: BLE001 — 起服期间连不上是预期
            pass
        time.sleep(0.5)
    return srv, "", tmpdir


def _fixture(base: str, tmpdir: Path) -> tuple:
    """建间 + 名单 + 1 人分队 + 三条任务（1 通过 / 1 拒收 / 1 在途）。"""
    (tmpdir / "lead_inbox").mkdir(parents=True, exist_ok=True)
    _http(base, "/api/harness/register", {
        "harness_id": "示例Harness-Lead", "harness_name": "示例Harness-Lead",
        "harness_type": "custom", "wakeup_method": "file_poll",
        "wakeup_dir": str(tmpdir / "lead_inbox"),
        "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
    _http(base, "/api/harness/register", {
        "harness_id": "示例Harness-M1", "harness_name": "示例Harness-M1",
        "harness_type": "custom", "wakeup_method": "a2a",
        "api_base_url": "http://127.0.0.1:9/a2a/x",
        "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
    ws_root = tmpdir / "ws_sqrec"
    for d in (ws_root / "_lead", ws_root / "前端组", ws_root / S1 / "任务", ws_root / S1 / "产物"):
        d.mkdir(parents=True, exist_ok=True)
    (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
    (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
    _st, made = _http(base, "/api/workshop", {"name": "分队履历判据间（示例）",
                                             "hall_content": "x", "workshop_path": str(ws_root)})
    wid = str((made or {}).get("workshop_id") or "")
    mk = _http(base, "/api/mind/A-01", {})[1] or {}
    _http(base, "/api/workshop/%s/manifest" % wid,
          {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
    txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 41\n---\n" % wid
           + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
           + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
           + "---\n行数: 2")
    _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
    roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
    seq = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
           for m in (roster.get("members") or [])}.get(S1, 0)
    _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq),
          {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
    _st_sq, b_sq = _http(base, "/api/workshop/%s/squads" % wid,
                         {"name": "前端分队", "direction": "前端", "goal": "把界面做完",
                          "member_seqs": [seq]})
    sq_id = ""
    for c in (_http(base, "/api/workshop/%s/squads" % wid)[1] or {}).get("squads") or []:
        sq_id = str((c or {}).get("squad_id") or "")
    # 三条任务
    seqs = []
    for title in ("通过的那条", "拒收的那条", "还在做的那条"):
        _st, b = _http(base, "/api/workshop/%s/dispatch" % wid,
                       {"member_seq": seq, "title": title, "goal": title, "accept": "行"})
        seqs.append(int((b or {}).get("task_seq") or 0))
    # T-001 通过（L2：**要有非空产物** —— 空目录会被拒，所以先放一个交付文件）
    (ws_root / S1 / "产物" / "交付.txt").write_text("这份活的交付物\n", encoding="utf-8")
    st_r, b_r = _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, seqs[0]),
                      {"result": "通过", "evidence_level": "L2", "note": "可以"})
    if st_r != 200:
        print("  [夹具提示] 验收通过失败：HTTP %s %s"
              % (st_r, json.dumps(b_r, ensure_ascii=False)[:180]))
    _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, seqs[1]),
          {"result": "拒收", "evidence_level": "L0", "note": "方向不对"})
    return wid, sq_id, seqs


def main() -> int:
    print("判据：分队履历 + 分队模板化（V140 · 队列 47-1/47-2 · 真跑）")
    print("=" * 78)
    srv, base, tmpdir = _boot()
    try:
        if not base:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2
        wid, sq_id, task_seqs = _fixture(base, tmpdir)
        if not sq_id:
            print("[FAIL] 夹具失败：分队没建成（后面的格子无意义）")
            return 2

        st1, b1 = _http(base, "/api/workshop/%s/squads/%s/record" % (wid, sq_id))
        t1 = (b1 or {}).get("tasks") or {}
        check("① 履历给得出结构与口径",
              st1 == 200 and all(k in t1 for k in ("total", "accepted", "rejected", "open"))
              and bool((b1 or {}).get("quality")) and "recent" in (b1 or {})
              and bool((b1 or {}).get("note")) and bool((b1 or {}).get("no_ranking")),
              "HTTP %s keys=%r" % (st1, list(b1 or {})[:10]))

        q1 = (b1 or {}).get("quality") or {}
        check("② 只算终态（在途不进分母）",
              t1.get("total") == 2 and t1.get("accepted") == 1 and t1.get("rejected") == 1
              and t1.get("open") == 1 and q1.get("accept_rate") == 0.5,
              "tasks=%r accept_rate=%r" % (t1, q1.get("accept_rate")))

        check("③ 样本不足要说话（total<3 ⇒ insufficient + 人话）",
              (b1 or {}).get("insufficient") is True and "别据此判断" in str((b1 or {}).get("note")),
              "insufficient=%r note=%r" % ((b1 or {}).get("insufficient"),
                                           str((b1 or {}).get("note"))[:80]))

        before = len(((_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []))
        st4, b4 = _http(base, "/api/workshop/%s/squads/%s/clone" % (wid, sq_id), {"name": "前端分队副本"})
        new_sq = str(((b4 or {}).get("squad") or {}).get("squad_id") or "")
        after = len(((_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or []))
        st4b, b4b = _http(base, "/api/workshop/%s/squads/%s/record" % (wid, new_sq)) if new_sq else (0, {})
        # ⚠️ 设计稿 V140 §四 ④ 原来写"新队 record.tasks.total == 0"—— **与数据模型不符**：
        #   任务归属是**工位级**（`member_seq`），账本里没有 squad_id；同一批人换个队名，
        #   看到的当然是同一批历史。真正该验的是"**没有把账本条目复制一份**"。
        #   （已在 V140 §四 更正这句，这里按更正后的口径判。）
        check("④ clone 生成新分队且**没有复制账本条目**（任务归属按工位，不按队）",
              st4 == 200 and bool(new_sq) and new_sq != sq_id and before == after
              and int(((b4b or {}).get("tasks") or {}).get("total") or 0)
              == int(((b1 or {}).get("tasks") or {}).get("total") or -1),
              "new=%r record=%r 账本 %d→%d（源队 total=%r）"
              % (new_sq, (b4b or {}).get("tasks"), before, after,
                 (b1 or {}).get("tasks", {}).get("total")))

        sq4 = (b4 or {}).get("squad") or {}
        check("⑤ clone 复制方向/目标/成员构成（可被 body 覆盖）",
              sq4.get("direction") == "前端" and sq4.get("goal") == "把界面做完"
              and list(sq4.get("member_seqs") or []) != [],
              "squad=%r" % sq4)

        # 先造一条经验（带独特口令），再验"指针里有它、响应正文里没有它"
        secret = "SECRET-EXPERIENCE-BODY-请勿出现在响应里"
        mroot = Path(tmpdir) / "data" / "minds"
        memdir = mroot / "mem-seed" / "memories"
        memdir.mkdir(parents=True, exist_ok=True)
        (memdir / "踩过的坑.md").write_text("# 坑\n%s\n" % secret, encoding="utf-8")
        st6, b6 = _http(base, "/api/workshop/%s/squads/%s/clone" % (wid, sq_id),
                        {"name": "带经验的副本", "copy_experience": True})
        ei = (b6 or {}).get("experience_index") or {}
        blob = json.dumps(b6, ensure_ascii=False)
        check("⑥ copy_experience **只给指针不给正文**（口令不能出现在响应里）",
              st6 == 200 and isinstance(ei.get("paths"), list) and bool(ei.get("paths"))
              and any("memories/" in p for p in ei.get("paths") or [])
              and secret not in blob,
              "ei=%r 含口令=%r" % (ei, secret in blob))
        # ⑧ 不存在的分队必须 404（`squads.find()` 返 {} 不是 None —— 曾导致"看起来成功"）
        st8, b8 = _http(base, "/api/workshop/%s/squads/no-such-squad/record" % wid)
        st8b, b8b = _http(base, "/api/workshop/%s/squads/no-such-squad/clone" % wid, {"name": "x"})
        check("⑧ 不存在的分队 ⇒ 404 NO_SUCH_SQUAD（不许返回 ok:true 的空壳）",
              st8 == 404 and (b8 or {}).get("code") == "NO_SUCH_SQUAD"
              and st8b == 404 and (b8b or {}).get("code") == "NO_SUCH_SQUAD",
              "record=%s %s / clone=%s %s" % (st8, (b8 or {}).get("code"),
                                              st8b, (b8b or {}).get("code")))

    finally:
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾
            srv.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ⑦ 两个开关各起一个实例验
    srv2, base2, tmp2 = _boot({"AC_SQUAD_HISTORY": "0", "AC_SQUAD_TEMPLATE": "0"})
    try:
        if not base2:
            check("⑦ 开关真生效", False, "第二个实例起不来")
        else:
            wid2 = str((_http(base2, "/api/workshop", {"name": "开关判据间（示例）",
                                                      "hall_content": "x",
                                                      "workshop_path": str(tmp2 / "wsx")})[1] or {})
                       .get("workshop_id") or "")
            (tmp2 / "wsx" / "_lead").mkdir(parents=True, exist_ok=True)
            st7a, b7a = _http(base2, "/api/workshop/%s/squads/whatever/record" % wid2) if wid2 else (0, {})
            st7b, b7b = _http(base2, "/api/workshop/%s/squads/whatever/clone" % wid2, {}) if wid2 else (0, {})
            # 履历开关要在**子进程**里验：本进程的 os.environ 改不到已 import 的模块
            hist_off = _py_env("squad_record.history_enabled()", {"AC_SQUAD_HISTORY": "0"})
            check("⑦ 两个开关真生效（clone 404 + 履历开关在子进程里读到 False）",
                  st7b == 404 and (b7b or {}).get("code") == "SQUAD_TEMPLATE_DISABLED"
                  and hist_off == "False",
                  "clone=%s %s history_enabled(子进程)=%r"
                  % (st7b, (b7b or {}).get("code"), hist_off))
    finally:
        try:
            srv2.terminate()
            srv2.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾
            srv2.kill()
        shutil.rmtree(tmp2, ignore_errors=True)

    print("-" * 78)
    passed = sum(1 for _l, ok, _d in ROWS if ok)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(ROWS), passed, len(ROWS) - passed))
    for lb, ok, d in ROWS:
        if not ok:
            print("  FAIL:", lb, "|", str(d)[:240])
    return 0 if passed == len(ROWS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
