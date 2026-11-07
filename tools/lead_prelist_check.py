#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**组长预列表**（`V137`）—— 按方向给「单 / 团」两套配置。

老板 2026-11-07：「为了做出组长预列表，各个组长（单/团）适合不同方向/任务」。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真注册（带 `fit_directions`）。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 空实例 | `directions=[]` + `sort_note`（不崩、不编） |
| ③ | 有方向的进方向组 | `前端` 组里有它 |
| ④ | 没方向的进 `no_direction` | 且不在任何方向组（不替它编） |
| ⑤ | 该方向 solo 可行 | `solo.feasible=true` + lead 对 |
| ⑥ | 该方向 solo 不行 ⇒ team | `solo.feasible=false` + `team.feasible=true` |
| ⑦ | 助手跨方向标注 | `team.helpers[].from_direction` 非空 |
| ⑧ | `by_mode` 三键齐 | solo/team/blocked 都存在，solo 段有前端 |
| ⑨ | `?task=` 命中 | `前端页面开发` → `matched_directions` 含 `前端` |
| ⑩ | `?task=` 未命中 | 如实说 + 列出实际方向（不退化） |
| ⑪ | 只读 | 调用前后状态文件 sha 不变 |
| ⑫ | 稳定 | 同输入两次结果逐字相同 |

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
    for name in ("state.json", "harnesses.json", "workshops.json"):
        p = d / name
        try:
            parts.append("%s:%s" % (name, hashlib.sha256(p.read_bytes()).hexdigest()[:12]))
        except FileNotFoundError:
            parts.append("%s:-" % name)
    return "|".join(parts)


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_prelist_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：组长预列表（V137 · 真跑：临时实例 + 真注册带方向）")
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

        st0, b0 = _http(base, "/api/lead-preliminary")
        check("② 空实例：不崩、不编（directions 空 + 有 sort_note）",
              st0 == 200 and (b0 or {}).get("directions") == [] and (b0 or {}).get("sort_note"),
              "HTTP %s %s" % (st0, json.dumps(b0, ensure_ascii=False)[:160]))

        ddir = Path(tmpdir)
        (ddir / "bridge_inbox").mkdir(parents=True, exist_ok=True)
        # 前端全能（能一个人闭环）
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-FE-All", "harness_name": "示例Harness-FE-All",
            "harness_type": "custom", "wakeup_method": "a2a",
            "fit_directions": ["前端"],
            "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "read_file"}, {"name": "write_file"}],
            "ai": {"model_name": "示例模型"}})
        # 后端：少协议（读不了文件）⇒ 需要助手
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-BE-Few", "harness_name": "示例Harness-BE-Few",
            "harness_type": "custom", "wakeup_method": "file_poll",
            "fit_directions": ["后端"],
            "wakeup_dir": str(ddir / "bridge_inbox"),
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        # 能读、不能当组长（跨方向助手）
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-Reader", "harness_name": "示例Harness-Reader",
            "harness_type": "custom", "wakeup_method": "clipboard",
            "fit_directions": ["测试"],
            "tools": [{"name": "read_file"}], "ai": {"model_name": "示例模型"}})
        # 没报方向
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-NoDir", "harness_name": "示例Harness-NoDir",
            "harness_type": "custom", "wakeup_method": "clipboard",
            "tools": [{"name": "read_file"}], "ai": {"model_name": "示例模型"}})

        # 前端再加一个全能（测"梯队 alternatives"）
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-FE-Alt", "harness_name": "示例Harness-FE-Alt",
            "harness_type": "custom", "wakeup_method": "a2a",
            "fit_directions": ["前端"],
            "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "read_file"}, {"name": "write_file"}],
            "ai": {"model_name": "示例模型"}})

        before = _sha_dir(ddir)
        st, b = _http(base, "/api/lead-preliminary")
        dirs = {g.get("direction"): g for g in ((b or {}).get("directions") or [])}
        check("③ 有方向的进方向组（前端/后端）",
              st == 200 and "前端" in dirs and "后端" in dirs
              and "示例Harness-FE-All" in [c["harness_id"] for c in dirs["前端"]["candidates"]],
              "dirs=%r" % list(dirs))
        nodir = [x["harness_id"] for x in ((b or {}).get("no_direction") or [])]
        in_any = any("示例Harness-NoDir" in [c["harness_id"] for c in g["candidates"]]
                     for g in dirs.values())
        check("④ 没报方向的进 no_direction，且不在任何方向组（不替它编）",
              nodir == ["示例Harness-NoDir"] and not in_any
              and "不替它编" in ((b or {}).get("no_direction_note") or ""),
              "nodir=%r in_any=%r" % (nodir, in_any))
        check("⑤ 前端 solo 可行（一个人闭环）",
              dirs["前端"]["solo"].get("feasible") is True
              and dirs["前端"]["solo"]["lead"]["harness_id"] == "示例Harness-FE-All",
              json.dumps(dirs["前端"]["solo"], ensure_ascii=False)[:200])
        be = dirs["后端"]
        check("⑥ 后端 solo 不行 ⇒ team 可行",
              be["solo"].get("feasible") is False and bool(be["solo"].get("reason"))
              and be["team"].get("feasible") is True,
              json.dumps({"solo": be["solo"], "team": be["team"]}, ensure_ascii=False)[:240])
        hs = be["team"].get("helpers") or []
        check("⑦ 助手跨方向时标 from_direction",
              bool(hs) and all(h.get("from_direction") for h in hs),
              json.dumps(hs, ensure_ascii=False)[:200])
        bm = (b or {}).get("by_mode") or {}
        check("⑧ by_mode 三键齐，solo 段有前端",
              set(bm) == {"solo", "team", "blocked"}
              and any(x.get("direction") == "前端" for x in (bm.get("solo") or []))
              and any(x.get("direction") == "后端" for x in (bm.get("team") or [])),
              json.dumps({k: len(v or []) for k, v in bm.items()}, ensure_ascii=False))

        st9, b9 = _http(base, "/api/lead-preliminary?task=%s" % "前端页面开发")
        check("⑨ ?task= 命中方向（包含匹配）",
              st9 == 200 and "前端" in ((b9 or {}).get("matched_directions") or []),
              "matched=%r" % ((b9 or {}).get("matched_directions")))
        st10, b10 = _http(base, "/api/lead-preliminary?task=%s" % "运维")
        check("⑩ ?task= 未命中 ⇒ 说清 + 列出实际方向（不退化）",
              st10 == 200 and ((b10 or {}).get("matched_directions") == [])
              and "没匹配到" in ((b10 or {}).get("match_note") or "")
              and "前端" in ((b10 or {}).get("match_note") or ""),
              str((b10 or {}).get("match_note"))[:160])

        after = _sha_dir(ddir)
        check("⑪ 只读（调用前后状态文件 sha 不变）", before == after,
              "before=%s after=%s" % (before[:60], after[:60]))

        _s1, p1 = _http(base, "/api/lead-preliminary")
        _s2, p2 = _http(base, "/api/lead-preliminary")
        check("⑫ 稳定可复现（两次结果逐字相同）",
              json.dumps(p1, ensure_ascii=False, sort_keys=True)
              == json.dumps(p2, ensure_ascii=False, sort_keys=True))
        # ── ★V137 优化：一行结论 / 梯队 / 任务卡 ───────────────────────
        st_o, b_o = _http(base, "/api/lead-preliminary")
        gs = {g.get("direction"): g for g in ((b_o or {}).get("directions") or [])}
        h_fe = str((gs.get("前端") or {}).get("headline") or "")
        h_be = str((gs.get("后端") or {}).get("headline") or "")
        h_te = str((gs.get("测试") or {}).get("headline") or "")
        check("⑬ 每个方向给一句话结论（一个人就够 / 得搭团 / 没人能接）",
              st_o == 200 and "一个人就够" in h_fe and "得搭团" in h_be
              and "没人能接" in h_te,
              "前端=%r 后端=%r 测试=%r" % (h_fe[:40], h_be[:40], h_te[:40]))

        fe = gs.get("前端") or {}
        alt = (fe.get("solo") or {}).get("alternatives") or []
        check("⑭ 梯队：同方向还有别的全能时列在 alternatives（不是推荐，是还有谁）",
              (fe.get("solo") or {}).get("feasible") is True
              and any(x.get("harness_id") == "示例Harness-FE-Alt" for x in alt),
              json.dumps(alt, ensure_ascii=False)[:200])

        st_h, b_h = _http(base, "/api/lead-preliminary?task=%s" % "前端页面开发")
        st_m, b_m = _http(base, "/api/lead-preliminary?task=%s" % "运维")
        card_h = (b_h or {}).get("task_card") or {}
        card_m = (b_m or {}).get("task_card") or {}
        check("⑮ 任务卡：命中给方向；未命中给换词建议 + 现有方向（不退化）",
              st_h == 200 and card_h.get("status") == "hit"
              and "前端" in (card_h.get("matched_directions") or [])
              and st_m == 200 and card_m.get("status") == "miss"
              and "前端" in (card_m.get("available_directions") or [])
              and bool(card_m.get("next")),
              "hit=%r miss=%r" % (card_h.get("status"), card_m.get("status")))

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
