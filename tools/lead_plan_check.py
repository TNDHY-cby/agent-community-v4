#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**组长算法**（`V136`）—— 两条路：全能单组长 / 少协议组长 + 助手互补。

老板 2026-11-07：「做组长算法，两条路：单一多协议全能组长；少协议组长搭助手互补」。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真注册 + 真改档案 + 真选组长。

| # | 格 | 断言 |
|---|---|---|
| ① | 有 high ⇒ solo | `mode=solo`、`helpers=[]`、四件事全覆盖 |
| ② | 多协议优先 | 两个 high 时选协议数多的；`others_solo` 列出另一个 |
| ③ | 无 high 但能补 ⇒ team | `mode=team`、助手带 `adds`（补了哪一项） |
| ④ | 助手确实补缺 | 主组长读不了文件 ⇒ 助手里有能读文件的 |
| ⑤ | 补不齐 ⇒ blocked | `blocked.missing` 非空 + `next` 给三条路 + **不给假配置** |
| ⑥ | 需求可缩小 | `needs=dispatch` ⇒ 只要一件，结论相应放宽 |
| ⑦ | 稳定可复现 | 同输入两次调用 `lead/helpers` 逐字相同 |
| ⑧ | 坏参数 | `needs=nope` ⇒ 400 + 可用项 |
| ⑨ | 无候选 | 空实例 ⇒ `blocked` + 说明先去注册 |
| ⑩ | 算法选的人真能选上 | 按 `lead.harness_id` 去 `POST …/lead` ⇒ 200 |
| ⑪ | 只读 | 调用前后状态文件 sha 不变 |
| ⑫ | 不排分不推荐 | 响应里有 `sort_note`，且**没有** score/rank/best 这类字段 |

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


def _sha_dir(d: Path) -> str:
    parts = []
    for name in ("state.json", "harnesses.json", "workshops.json", "task_ledger.json"):
        p = d / name
        try:
            parts.append("%s:%s" % (name, hashlib.sha256(p.read_bytes()).hexdigest()[:12]))
        except FileNotFoundError:
            parts.append("%s:-" % name)
    return "|".join(parts)


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_leadplan_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：组长算法（V136 · 真跑：临时实例 + 真注册 + 真改档案 + 真选组长）")
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

        # ⑨ 空实例：先验"没有候选"
        st0, b0 = _http(base, "/api/lead-plan")
        check("⑨ 没有候选 ⇒ blocked + 说明先去注册",
              st0 == 200 and (b0 or {}).get("mode") == "blocked"
              and bool(((b0 or {}).get("blocked") or {}).get("next")),
              "HTTP %s mode=%r" % (st0, (b0 or {}).get("mode")))

        ddir = Path(tmpdir)
        (ddir / "bridge_inbox").mkdir(parents=True, exist_ok=True)
        # 全能（多协议 + 读写文件）
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-All", "harness_name": "示例Harness-All",
            "harness_type": "custom", "wakeup_method": "a2a",
            "protocols": ["a2a", "mcp", "http_api"],
            "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "read_file"}, {"name": "write_file"}],
            "ai": {"model_name": "示例模型"}})
        # 少协议（桥 + 写文件，读不了）
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-Few", "harness_name": "示例Harness-Few",
            "harness_type": "custom", "wakeup_method": "file_poll",
            "wakeup_dir": str(ddir / "bridge_inbox"),
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        # 助手（能读文件，但没协议通道，不能当组长）
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-Reader", "harness_name": "示例Harness-Reader",
            "harness_type": "custom", "wakeup_method": "clipboard",
            "tools": [{"name": "read_file"}], "ai": {"model_name": "示例模型"}})

        before = _sha_dir(ddir)
        st1, b1 = _http(base, "/api/lead-plan")
        check("① 有 high ⇒ solo（一个全能组长，不要助手）",
              st1 == 200 and (b1 or {}).get("mode") == "solo"
              and not (b1 or {}).get("helpers")
              and all(v.get("covered_by") for v in ((b1 or {}).get("coverage") or {}).values()),
              "HTTP %s mode=%r lead=%r" % (st1, (b1 or {}).get("mode"),
                                           ((b1 or {}).get("lead") or {}).get("harness_id")))
        check("② 多协议优先 + 其余 high 列在 others_solo",
              ((b1 or {}).get("lead") or {}).get("harness_id") == "示例Harness-All"
              and int(((b1 or {}).get("lead") or {}).get("protocol_count") or 0) >= 2
              and isinstance((b1 or {}).get("others_solo"), list),
              json.dumps({k: (b1 or {}).get(k) for k in ("lead", "others_solo")},
                         ensure_ascii=False)[:220])

        # ── ★V136 的目的：**组长列表**（老板：「这是为了做组长列表的」）───────────
        st_lr, b_lr = _http(base, "/api/lead-roster")
        rows = (b_lr or {}).get("candidates") or []
        need_fields = ("protocol_fit", "covers", "solo_feasible", "needs_helpers", "helper_options")
        has_fields = bool(rows) and all(set(need_fields) <= set(r) for r in rows)
        check("⑬ 组长列表：每个候选带适配度/覆盖/能否独立/缺什么/谁能补",
              st_lr == 200 and has_fields
              and bool((b_lr or {}).get("groups"))
              and bool((b_lr or {}).get("plans"))
              and bool((b_lr or {}).get("needs_def"))
              and bool((b_lr or {}).get("group_hint")),
              "HTTP %s rows=%d fields=%r" % (st_lr, len(rows), has_fields))

        g_lr = (b_lr or {}).get("groups") or {}
        p_lr = (b_lr or {}).get("plans") or {}
        check("⑭ 三组齐且与算法同源（有 solo_ready ⇒ plans.mode=solo）",
              bool(g_lr.get("solo_ready")) and bool(g_lr.get("helpers_only"))
              and str(p_lr.get("mode") or "") == "solo"
              and str((p_lr.get("lead") or {}).get("harness_id") or "")
              in (g_lr.get("solo_ready") or []),
              "groups=%r plans.mode=%r"
              % ({k: v for k, v in g_lr.items()}, p_lr.get("mode")))

        # 把它改成"少协议"：去掉读工具 + 只留桥
        # ⚠️ 真实端点是 `POST /api/docking/harness/{id}`（没有 `/edit` 后缀；首版写错 ⇒ 405、假绿）
        _st_edit, _b_edit = _http(base, "/api/docking/harness/%s" % "示例Harness-All",
                                  {"tools": [{"name": "write_file"}]})
        assert _st_edit == 200, "改档案失败：%s %s" % (_st_edit, str(_b_edit)[:200])
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-All2", "harness_name": "示例Harness-All2",
            "harness_type": "custom", "wakeup_method": "a2a",
            "protocols": ["a2a"], "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        st3, b3 = _http(base, "/api/lead-plan")
        helpers3 = (b3 or {}).get("helpers") or []
        adds3 = [a for h in helpers3 for a in (h.get("adds") or [])]
        check("③ 无 high 但能补 ⇒ team（助手带 adds）",
              st3 == 200 and (b3 or {}).get("mode") == "team" and bool(helpers3)
              and all(h.get("adds") for h in helpers3),
              "mode=%r helpers=%r" % ((b3 or {}).get("mode"),
                                      json.dumps(helpers3, ensure_ascii=False)[:200]))
        check("④ 助手确实补缺（主组长读不了 ⇒ 助手能读）",
              "read_artifacts" in adds3,
              "adds=%r" % adds3)

        # 需求缩小：只要 dispatch ⇒ 应该能 solo/team 且不要求 read
        st6, b6 = _http(base, "/api/lead-plan?needs=dispatch")
        check("⑥ needs 可缩小（只要 dispatch）",
              st6 == 200 and (b6 or {}).get("mode") in ("solo", "team")
              and list((b6 or {}).get("needs") or []) == ["dispatch"],
              "mode=%r needs=%r" % ((b6 or {}).get("mode"), (b6 or {}).get("needs")))

        # 把能读文件的助手也拿掉 ⇒ blocked
        _st_edit2, _b_edit2 = _http(base, "/api/docking/harness/%s" % "示例Harness-Reader",
                                    {"tools": []})
        assert _st_edit2 == 200, "改档案失败：%s %s" % (_st_edit2, str(_b_edit2)[:200])
        st5, b5 = _http(base, "/api/lead-plan")
        blk = (b5 or {}).get("blocked") or {}
        check("⑤ 补不齐 ⇒ blocked（不缺就是怪事）",
              st5 == 200 and (b5 or {}).get("mode") == "blocked"
              and bool(blk.get("missing")) and bool(blk.get("next"))
              and not (b5 or {}).get("lead") and not (b5 or {}).get("helpers"),
              "mode=%r blocked=%r" % ((b5 or {}).get("mode"),
                                      json.dumps(blk, ensure_ascii=False)[:200]))

        st8, b8 = _http(base, "/api/lead-plan?needs=nope")
        check("⑧ 坏需求项 ⇒ 400 + 可用项",
              st8 == 400 and (b8 or {}).get("code") == "BAD_NEEDS" and (b8 or {}).get("available"),
              "HTTP %s %s" % (st8, json.dumps(b8, ensure_ascii=False)[:140]))

        # ⑪ 的采样点放在**所有写操作之后**（注册/改档案都会动 harnesses.json，
        #    首版把 before 放在前面 ⇒ 把自己前面的写算成"plan 改的"，假红）
        _s_a, _ = _http(base, "/api/lead-plan")
        _mid = _sha_dir(ddir)
        _s_b, _ = _http(base, "/api/lead-plan")
        _aft = _sha_dir(ddir)
        check("⑪ 只读（调 plan 前后状态文件 sha 不变）", _mid == _aft,
              "mid=%s after=%s" % (_mid[:60], _aft[:60]))

        # ⑦ 稳定可复现：两次调用逐字相同（清掉 blocked 前先恢复一个 high）
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-All3", "harness_name": "示例Harness-All3",
            "harness_type": "custom", "wakeup_method": "a2a",
            "protocols": ["a2a", "mcp"], "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "read_file"}, {"name": "write_file"}],
            "ai": {"model_name": "示例模型"}})
        _s1, p1 = _http(base, "/api/lead-plan")
        _s2, p2 = _http(base, "/api/lead-plan")
        key = lambda d: (json.dumps((d or {}).get("lead"), ensure_ascii=False, sort_keys=True),
                         json.dumps((d or {}).get("helpers"), ensure_ascii=False, sort_keys=True))
        check("⑦ 稳定可复现（两次结果逐字相同）", key(p1) == key(p2),
              "%s != %s" % (str(key(p1))[:80], str(key(p2))[:80]))

        # ⑫ 不排分：响应里不许出现 score/rank/best 字段
        blob = json.dumps(p1, ensure_ascii=False).lower()
        bad_keys = [k for k in ("score", "rank", "best_candidate", "rating") if ('"%s"' % k) in blob]
        check("⑫ 不排分不推荐（有 sort_note，无 score/rank/best）",
              bool((p1 or {}).get("sort_note")) and not bad_keys,
              "bad_keys=%r sort_note=%r" % (bad_keys, str((p1 or {}).get("sort_note"))[:60]))

        # ⑩ 按算法选的人真能选上（建个工作间再选）
        ws_root = Path(tmpdir) / "ws_plan"
        (ws_root / "_lead").mkdir(parents=True, exist_ok=True)
        _st, made = _http(base, "/api/workshop", {"name": "算法判据间（示例）",
                                                  "hall_content": "x",
                                                  "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        lead_id = str(((p1 or {}).get("lead") or {}).get("harness_id") or "")
        st10, b10 = _http(base, "/api/workshop/%s/lead" % wid, {"harness_id": lead_id})
        check("⑩ 算法选的主组长真能选上",
              st10 in (200, 201) and bool((b10 or {}).get("ok")),
              "HTTP %s lead=%r %s" % (st10, lead_id, json.dumps(b10, ensure_ascii=False)[:160]))
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
