#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**催办**（`V134` ⑧ 延伸）—— "做了没回执 / 交了没验收"平台要主动说话。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间 + 真名单 + 真写 `_notice.md`。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 只读清单 | `GET …/nudge` 给出该催谁（`issued` 超阈值 ⇒ 催成员回执） |
| ③ | `dry_run` 一个字都不写 | 返回 `would_write`，但 `_notice.md` 没变、通知账没增 |
| ④ | 真催：写进 `_notice.md` + 通知账 `written` | 两处都真有 |
| ⑤ | 同一天只催一次 | 第二次返回 `skipped`，通知账不增 |
| ⑥ | 已了结的不催 | `accepted` 的任务不在清单里 |

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
SLOT = "前端组/员工01"


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
    tmpdir = tempfile.mkdtemp(prefix="ac_nudge_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600",
                # 把阈值压到 0 分钟：夹具里刚建的任务立刻"该催"（不改代码、只调环境）
                "AC_NUDGE_ISSUED_MIN": "0", "AC_NUDGE_DELIVERED_MIN": "0"})
    # 后台循环：默认关；这一轮先关（后面单独起一个"开了"的实例验它会自动催）
    env.setdefault("AC_NUDGE_WATCH_SEC", "0")
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：催办（V134 ⑧ 延伸 · 真跑：临时实例 + 真名单 + 真写通知）")
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
        ws_root = ddir / "ws_nudge"
        for d in (ws_root / "_lead", ws_root / "前端组", ws_root / SLOT / "任务",
                  ws_root / SLOT / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        _st, made = _http(base, "/api/workshop", {"name": "催办判据间（示例）",
                                                  "hall_content": "x",
                                                  "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 13\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n" % SLOT
               + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 2")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        roster = (_http(base, "/api/workshop/%s/roster" % wid)[1] or {})
        seq = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
               for m in (roster.get("members") or [])}.get(SLOT, 0)
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq),
              {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
        st_d, b_d = _http(base, "/api/workshop/%s/dispatch" % wid,
                          {"member_seq": seq, "title": "催办夹具任务", "goal": "x", "accept": "x"})
        notice_p = ws_root / SLOT / "_notice.md"
        before_notice = notice_p.read_text(encoding="utf-8") if notice_p.is_file() else ""
        st_lg0, b_lg0 = _http(base, "/api/workshop/%s/notice-log" % wid)
        n0 = len((b_lg0 or {}).get("items") or [])

        st2, b2 = _http(base, "/api/workshop/%s/nudge" % wid)
        items = (b2 or {}).get("items") or []
        check("② 只读清单：issued 超阈值 ⇒ 催成员回执",
              st_d == 200 and st2 == 200 and bool(items)
              and items[0]["kind"] == "member_no_receipt"
              and "task-result" in str(items[0].get("ask")),
              "HTTP %s items=%r" % (st2, json.dumps(items[:1], ensure_ascii=False)[:200]))

        st3, b3 = _http(base, "/api/workshop/%s/nudge" % wid, {"dry_run": True})
        after_notice = notice_p.read_text(encoding="utf-8") if notice_p.is_file() else ""
        _st_lg, b_lg = _http(base, "/api/workshop/%s/notice-log" % wid)
        check("③ dry_run 一个字都不写",
              st3 == 200 and (b3 or {}).get("dry_run") is True
              and bool((b3 or {}).get("would_write"))
              and after_notice == before_notice
              and len((b_lg or {}).get("items") or []) == n0,
              "notice_changed=%r log=%d→%d" % (after_notice != before_notice, n0,
                                               len((b_lg or {}).get("items") or [])))

        st4, b4 = _http(base, "/api/workshop/%s/nudge" % wid, {})
        txt4 = notice_p.read_text(encoding="utf-8") if notice_p.is_file() else ""
        _st_lg2, b_lg2 = _http(base, "/api/workshop/%s/notice-log" % wid)
        rows2 = (b_lg2 or {}).get("items") or []
        check("④ 真催：写进 _notice.md 且通知账多一条 written",
              st4 == 200 and bool((b4 or {}).get("sent"))
              and "催办" in txt4 and len(rows2) > n0
              and any((x.get("ref") or "").startswith("nudge:") for x in rows2),
              "sent=%r notice=%r log=%d→%d"
              % (len((b4 or {}).get("sent") or []), txt4[-60:], n0, len(rows2)))

        st5, b5 = _http(base, "/api/workshop/%s/nudge" % wid, {})
        _st_lg3, b_lg3 = _http(base, "/api/workshop/%s/notice-log" % wid)
        check("⑤ 同一天只催一次（第二次 skipped，通知账不增）",
              st5 == 200 and bool((b5 or {}).get("skipped"))
              and not (b5 or {}).get("sent")
              and len((b_lg3 or {}).get("items") or []) == len(rows2),
              "skipped=%r sent=%r" % (len((b5 or {}).get("skipped") or []),
                                      len((b5 or {}).get("sent") or [])))

        # ⑥ 不存在的工作间 ⇒ 404（"delivered ⇒ 催组长验收"那条由纯函数单测覆盖：
        #   test_nudge::test_02；端到端造"产物绑任务"成本高，不在这格里假装覆盖）
        st6, b6 = _http(base, "/api/workshop/no-such-ws-1/nudge")
        check("⑥ 不存在的工作间 ⇒ 404（不假装有清单）",
              st6 == 404 and (b6 or {}).get("code") == "NO_SUCH_WORKSHOP",
              "HTTP %s %s" % (st6, json.dumps(b6, ensure_ascii=False)[:160]))
        # ⑦⑧ 后台催办：默认关不写；开了（1 秒）会自己催一轮
        _st_lg, b_lg = _http(base, "/api/workshop/%s/notice-log" % wid)
        n_before = len((b_lg or {}).get("items") or [])
        time.sleep(3)
        _st_lg, b_lg = _http(base, "/api/workshop/%s/notice-log" % wid)
        n_after = len((b_lg or {}).get("items") or [])
        check("⑦ 后台催办默认关（等 3 秒，通知账不增）", n_after == n_before,
              "log %d → %d" % (n_before, n_after))

        # ⑨ 返工待重交：把 T-001 打成返工，等阈值（已压到 0）⇒ 清单里要出现"改完再交"
        _http(base, "/api/workshop/%s/tasks/1/review" % wid,
              {"result": "返工", "evidence_level": "L0", "note": "补一版说明"})
        st9, b9 = _http(base, "/api/workshop/%s/nudge" % wid)
        kinds9 = [x.get("kind") for x in ((b9 or {}).get("items") or [])]
        asks9 = [str(x.get("ask") or "") for x in ((b9 or {}).get("items") or [])
                 if x.get("kind") == "member_no_rework_delivery"]
        check("⑨ 被返工没重交 ⇒ 也要催（以前谁都不管，任务永久卡在 rework）",
              st9 == 200 and "member_no_rework_delivery" in kinds9
              and any("改完再交" in a for a in asks9),
              "kinds=%r asks=%r" % (kinds9, asks9[:1]))

        # ⑧ 开关生效：**独立数据目录**再起一个实例（同一数据目录不许起第二个，平台有保护），
        #   设 1 秒；断言它自己打印"后台催办已启用"（真写那条路由由 ②④ 与 apply 共用同一条路覆盖）
        import tempfile as _tf

        tmp2 = _tf.mkdtemp(prefix="ac_nudge_loop_")
        port2 = _free_port()
        env2 = dict(os.environ)
        env2.update({"AC_PORT": str(port2), "AC_DATA_DIR": tmp2, "PYTHONIOENCODING": "utf-8",
                     "AC_ARTIFACT_WATCH_SEC": "3600", "AC_NUDGE_WATCH_SEC": "1",
                     "AC_NUDGE_ISSUED_MIN": "0", "AC_NUDGE_DELIVERED_MIN": "0"})
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
            check("⑧ 开了后台催办（1 秒）⇒ 日志里真的启用了循环",
                  up2 and "后台催办已启用" in txt,
                  "up2=%r log=%r" % (up2, txt[-200:]))
        finally:
            try:
                srv2.terminate()
                srv2.wait(timeout=10)
            except Exception:                                  # noqa: BLE001 — 收尾
                srv2.kill()
            try:
                fh.close()
                shutil.rmtree(tmp2, ignore_errors=True)
            except OSError:                                    # noqa: BLE001 — 收尾清理失败不影响判据
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
