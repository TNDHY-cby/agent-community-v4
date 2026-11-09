#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**拒收后重派**（`POST …/tasks/{seq}/redispatch`）。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真名单 + 真派活 + 真拒收 + 真重派。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 没被拒收的任务不许重派 | `issued` 状态下 ⇒ **409 `NOT_REJECTED`** + 说清为什么 |
| ③ | 拒收后能重派 | 新任务建出来、`state=issued`、号比原来大 |
| ④ | 两条任务**双向关联** | 原任务 `redispatched_to`=新号、新任务 `redispatched_from`=原号、`chain` 两侧一致 |
| ⑤ | 内容真的继承了 | 新任务的 title/goal 来自原任务（不是空壳） |
| ⑥ | 成员收到新任务通知（`_notice.md` 多了一条） | 文件里有新任务号 |
| ⑦ | 不存在任务 ⇒ 404 | `NO_SUCH_TASK` |

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
    tmpdir = tempfile.mkdtemp(prefix="ac_redis_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600",
                "AC_REDISPATCH_MAX": "1"})   # ★A6：上限压到 1，方便验"到顶"
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：拒收后重派（redispatch · 真跑）")
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
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-M1", "harness_name": "示例Harness-M1",
            "harness_type": "custom", "wakeup_method": "a2a",
            "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        ws_root = ddir / "ws_rd"
        for d in (ws_root / "_lead", ws_root / "前端组", ws_root / S1 / "任务",
                  ws_root / S1 / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "重派判据间（示例）",
                                                  "hall_content": "x", "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 29\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
               + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 2")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
        seq = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
               for m in (roster.get("members") or [])}.get(S1, 0)
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq),
              {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
        _st_d, b_d = _http(base, "/api/workshop/%s/dispatch" % wid,
                           {"member_seq": seq, "title": "重派夹具任务",
                            "goal": "做一个本地记账工具", "accept": "能记账即可"})
        old_seq = int((b_d or {}).get("task_seq") or 0)

        st2, b2 = _http(base, "/api/workshop/%s/tasks/%d/redispatch" % (wid, old_seq), {})
        check("② 没被拒收的任务不许重派（409 NOT_REJECTED + 说清为什么）",
              st2 == 409 and (b2 or {}).get("code") == "NOT_REJECTED"
              and bool((b2 or {}).get("next")),
              "HTTP %s %s" % (st2, json.dumps(b2, ensure_ascii=False)[:180]))

        notice = ws_root / S1 / "_notice.md"
        before = notice.read_text(encoding="utf-8") if notice.is_file() else ""
        _http(base, "/api/workshop/%s/tasks/%d/review" % (wid, old_seq),
              {"result": "拒收", "evidence_level": "L0", "note": "方向不对，重做"})
        st3, b3 = _http(base, "/api/workshop/%s/tasks/%d/redispatch" % (wid, old_seq), {})
        new_seq = int((b3 or {}).get("task_seq") or 0)
        check("③ 拒收后能重派（新任务 issued、号更大）",
              st3 == 200 and (b3 or {}).get("ok") and new_seq > old_seq,
              "old=%s new=%s resp=%r" % (old_seq, new_seq, json.dumps(b3, ensure_ascii=False)[:200]))

        tasks = {int(t.get("task_seq") or 0): t
                 for t in ((_http(base, "/api/workshop/%s/tasks" % wid)[1] or {}).get("tasks") or [])}
        t_old, t_new = tasks.get(old_seq) or {}, tasks.get(new_seq) or {}
        check("④ 两条任务双向关联（to/from/chain 都对）",
              int(t_old.get("redispatched_to") or 0) == new_seq
              and int(t_new.get("redispatched_from") or 0) == old_seq
              and (t_new.get("redispatch_chain") or []) == (t_old.get("redispatch_chain") or [])
              and new_seq in (t_old.get("redispatch_chain") or []),
              "old.to=%r new.from=%r chain=%r/%r" % (t_old.get("redispatched_to"),
                                                    t_new.get("redispatched_from"),
                                                    t_old.get("redispatch_chain"),
                                                    t_new.get("redispatch_chain")))

        check("⑤ 内容真的继承了（title/goal 来自原任务）",
              str(t_new.get("title") or "") == "重派夹具任务"
              and "本地记账工具" in str(t_new.get("goal") or ""),
              "title=%r goal=%r" % (t_new.get("title"), t_new.get("goal")))

        after = notice.read_text(encoding="utf-8") if notice.is_file() else ""
        added = after[len(before):] if after.startswith(before) else after
        check("⑥ 成员收到新任务通知（_notice.md 里有新号）",
              ("T-%03d" % new_seq) in added,
              "added=%r" % added[-160:])

        st7, b7 = _http(base, "/api/workshop/%s/tasks/999/redispatch" % wid, {})
        check("⑦ 不存在任务 ⇒ 404",
              st7 == 404 and (b7 or {}).get("code") == "NO_SUCH_TASK", "HTTP %s" % st7)
        # ⑧⑨ 借鉴 A6：重派有硬上限；到顶后**必须进组长待办**（否则终态只是死字段）
        #    上面 ③ 已经重派过一次（chain 长度=1），上限也压成了 1 ⇒ 再点一次就该到顶
        st8, b8 = _http(base, "/api/workshop/%s/tasks/%d/redispatch" % (wid, old_seq), {})
        check("⑧ 重派到顶 ⇒ 409 REDISPATCH_BUDGET_EXHAUSTED（带名字的终态）",
              st8 == 409 and (b8 or {}).get("code") == "REDISPATCH_BUDGET_EXHAUSTED"
              and bool((b8 or {}).get("next")),
              "HTTP %s %s" % (st8, json.dumps(b8, ensure_ascii=False)[:200]))

        st9, b9 = _http(base, "/api/workshop/%s/lead-todo" % wid)
        kinds9 = [x.get("kind") for x in ((b9 or {}).get("items") or [])]
        urg9 = [x.get("kind") for x in ((b9 or {}).get("urgent") or [])]
        check("⑨ 烂尾进组长待办（先处理），不是账本里的死字段",
              st9 == 200 and "redispatch_exhausted" in kinds9 and "redispatch_exhausted" in urg9,
              "kinds=%r urgent=%r" % (kinds9, urg9))

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
