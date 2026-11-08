#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**组长日报**（`platform/lead_digest`）—— 把"该管谁"主动送到组长面前。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真名单 + 真派活 + 真产物。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 有事时 GET 只算不写 | `GET …/lead-digest` 返回一段话，但组长 `_notice.md` 没变、通知账没增 |
| ③ | dry_run 也不写 | `POST {dry_run:true}` 只回文本 |
| ④ | 真发：写进 `_lead/_notice.md` + 通知账 `written` | 两处都真有，且 `ref=digest:<日期>` |
| ⑤ | 同一天只发一次 | 第二次 `skipped=true`，通知账不增 |
| ⑥ | 没事可管就不发 | 空工作间 `POST` ⇒ `sent=false` + 说明（不打扰） |
| ⑦ | 不存在的工作间 ⇒ 404 | `NO_SUCH_WORKSHOP` |

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
    tmpdir = tempfile.mkdtemp(prefix="ac_digest_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.setdefault("AC_DIGEST_WATCH_SEC", "0")   # 后台日报默认关；⑦⑧ 单独验开关
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600",
                "AC_NUDGE_ISSUED_MIN": "0", "AC_NUDGE_DELIVERED_MIN": "0",
                "AC_PRESENCE_SILENT_MIN": "0"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：组长日报（lead-digest · 真跑）")
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
        ws_root = ddir / "ws_dg"
        for d in (ws_root / "_lead", ws_root / "前端组", ws_root / S1 / "任务",
                  ws_root / S1 / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "日报判据间（示例）",
                                                  "hall_content": "x", "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 21\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % S1
               + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 2")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
        seq = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
               for m in (roster.get("members") or [])}.get(S1, 0)
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq),
              {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
        _http(base, "/api/workshop/%s/dispatch" % wid,
              {"member_seq": seq, "title": "日报夹具任务", "goal": "x", "accept": "x"})

        lead_notice = ws_root / "_lead" / "_notice.md"
        before = lead_notice.read_text(encoding="utf-8") if lead_notice.is_file() else ""
        _st_lg, b_lg = _http(base, "/api/workshop/%s/notice-log" % wid)
        n0 = len((b_lg or {}).get("items") or [])

        st2, b2 = _http(base, "/api/workshop/%s/lead-digest" % wid)
        after = lead_notice.read_text(encoding="utf-8") if lead_notice.is_file() else ""
        check("② GET 只算不写（返回一段话，日志与通知账都没动）",
              st2 == 200 and bool(b2.get("text")) and after == before
              and len(((_http(base, "/api/workshop/%s/notice-log" % wid)[1]) or {}).get("items") or []) == n0,
              "text=%r" % str(b2.get("text"))[:80])

        st3, b3 = _http(base, "/api/workshop/%s/lead-digest" % wid, {"dry_run": True})
        after3 = lead_notice.read_text(encoding="utf-8") if lead_notice.is_file() else ""
        check("③ dry_run 也不写", st3 == 200 and (b3 or {}).get("dry_run") is True
              and after3 == before, "dry=%r" % (b3 or {}).get("dry_run"))

        st4, b4 = _http(base, "/api/workshop/%s/lead-digest" % wid, {})
        txt4 = lead_notice.read_text(encoding="utf-8") if lead_notice.is_file() else ""
        _st_lg2, b_lg2 = _http(base, "/api/workshop/%s/notice-log" % wid)
        rows2 = (b_lg2 or {}).get("items") or []
        check("④ 真发：写进组长 _notice.md 且通知账多一条 written（ref=digest:…）",
              st4 == 200 and (b4 or {}).get("sent") is True
              and "组长日报" in txt4 and len(rows2) > n0
              and any(str(x.get("ref") or "").startswith("digest:") for x in rows2),
              "sent=%r notice=%r log=%d→%d" % ((b4 or {}).get("sent"), txt4[-70:], n0, len(rows2)))

        st5, b5 = _http(base, "/api/workshop/%s/lead-digest" % wid, {})
        _st_lg3, b_lg3 = _http(base, "/api/workshop/%s/notice-log" % wid)
        check("⑤ 同一天只发一次（第二次 skipped，通知账不增）",
              st5 == 200 and (b5 or {}).get("skipped") is True
              and len((b_lg3 or {}).get("items") or []) == len(rows2),
              "skipped=%r" % (b5 or {}).get("skipped"))

        # ⑥ 空工作间：没活可管 ⇒ 不发
        empty_root = ddir / "ws_empty2"
        (empty_root / "_lead").mkdir(parents=True, exist_ok=True)
        (empty_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _se, made_e = _http(base, "/api/workshop", {"name": "空日报间（示例）",
                                                    "hall_content": "x",
                                                    "workshop_path": str(empty_root)})
        wid_e = str((made_e or {}).get("workshop_id") or "")
        st6, b6 = _http(base, "/api/workshop/%s/lead-digest" % wid_e, {})
        check("⑥ 没事可管就不发（不打扰）",
              st6 == 200 and (b6 or {}).get("sent") is False
              and bool((b6 or {}).get("why") or (b6 or {}).get("note")),
              "resp=%r" % json.dumps({k: (b6 or {}).get(k) for k in ("sent", "why", "note")},
                                     ensure_ascii=False)[:160])

        st7, b7 = _http(base, "/api/workshop/no-such-ws-1/lead-digest")
        check("⑦ 不存在的工作间 ⇒ 404",
              st7 == 404 and (b7 or {}).get("code") == "NO_SUCH_WORKSHOP", "HTTP %s" % st7)
        # ⑧ 后台日报默认关：等 3 秒通知账不增
        _st_lg, b_lg = _http(base, "/api/workshop/%s/notice-log" % wid)
        n_before = len((b_lg or {}).get("items") or [])
        time.sleep(3)
        _st_lg, b_lg = _http(base, "/api/workshop/%s/notice-log" % wid)
        n_after = len((b_lg or {}).get("items") or [])
        check("⑧ 后台日报默认关（等 3 秒，通知账不增）", n_after == n_before,
              "log %d → %d" % (n_before, n_after))

        # ⑨ 开关生效：独立数据目录再起一个实例（设 1 秒），日志里要真的启用循环
        import tempfile as _tf

        tmp2 = _tf.mkdtemp(prefix="ac_digest_loop_")
        port2 = _free_port()
        env2 = dict(os.environ)
        env2.update({"AC_PORT": str(port2), "AC_DATA_DIR": tmp2, "PYTHONIOENCODING": "utf-8",
                     "AC_ARTIFACT_WATCH_SEC": "3600", "AC_NUDGE_WATCH_SEC": "0",
                     "AC_DIGEST_WATCH_SEC": "1"})
        log2 = Path(tmp2) / "server.log"
        fh = open(log2, "w", encoding="utf-8", errors="replace")
        srv2 = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT),
                                env=env2, stdout=fh, stderr=subprocess.STDOUT)
        try:
            up2 = False
            for _ in range(60):
                try:
                    st, _j = _http("http://127.0.0.1:%d" % port2, "/api/status", timeout=3)
                    up2 = st == 200
                except (urllib.error.URLError, OSError):
                    up2 = False
                if up2:
                    break
                time.sleep(0.5)
            time.sleep(3)
            fh.flush()
            txt = log2.read_text(encoding="utf-8", errors="replace")
            check("⑨ 开了后台日报（1 秒）⇒ 日志里真的启用了循环",
                  up2 and "后台日报已启用" in txt,
                  "up2=%r log=%r" % (up2, txt[-200:]))
        finally:
            try:
                srv2.terminate()
                srv2.wait(timeout=10)
            except Exception:                              # noqa: BLE001 — 收尾
                srv2.kill()
            try:
                fh.close()
                shutil.rmtree(tmp2, ignore_errors=True)
            except OSError:                                # noqa: BLE001 — 收尾清理失败不影响判据
                pass
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
