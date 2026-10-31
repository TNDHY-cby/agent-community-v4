#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**P4 目录 / 产物 / 验收 / 归档 / 审忆员** 十四格（`V115` V1~V10 + M-R1~M-R5）。

真跑：临时实例（换端口/数据目录/心智根），真目录、真文件、真 HTTP；审忆员真跑 dry-run 与 apply 两次。
"""
from __future__ import annotations

import argparse
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

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
PY = sys.executable
SLOT = "前端组/员工01"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body=None, method: str = "", timeout: float = 30):
    path = urllib.parse.quote(path, safe="/?&=%:")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return int(r.status), (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), (json.loads(raw) if raw.strip() else {})
        except ValueError:
            return int(e.code), {"raw": raw[:300]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_p4_"))
    base = "http://127.0.0.1:%d" % port
    wsroot = tmpdir / "wsroot"
    minds = tmpdir / "minds"
    wsroot.mkdir(parents=True, exist_ok=True)
    minds.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(minds), "PYTHONIOENCODING": "utf-8"})
    rows, fails = [], []
    proc = None

    def check(name, ok, detail=""):
        rows.append((name, bool(ok), detail))
        if not ok:
            fails.append("%s（%s）" % (name, detail))

    try:
        proc = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=_ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        up = False
        for _ in range(60):
            try:
                up = _http(base, "/api/status", timeout=3)[0] == 200
            except OSError:
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 临时平台起不来（%s）" % base)
            return 2
        check("临时平台起来（换端口/数据目录/心智根）", True)

        st, made = _http(base, "/api/workshop", {"name": "示例间", "hall_content": "方向",
                                                 "workshop_path": str(wsroot)})
        wid = str((made or {}).get("workshop_id") or "")
        if not wid:
            check("夹具：建工作间", False, json.dumps(made, ensure_ascii=False)[:160])
            raise SystemExit(1)
        (wsroot / "_lead").mkdir(parents=True, exist_ok=True)
        (wsroot / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
        st, mk = _http(base, "/api/mind/A-01", {})
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str((mk or {}).get("mind_path") or ""),
               "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 5\n---\n"
               "岗位: %s | harness: h_x | model: m | 人数: 1\n---\n行数: 1" % (wid, SLOT))
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        r = _http(base, "/api/workshop/%s/roster" % wid)[1]
        seq = (r.get("members") or [{}])[0].get("member_seq")

        # ── V1~V3 verify-dirs ─────────────────────────────────────
        st, v1 = _http(base, "/api/workshop/%s/verify-dirs" % wid)
        missing = v1.get("missing") or []
        check("① 目录缺什么报什么（含分工.md 与成员任务/产物）",
              st == 200 and v1.get("ok") is False and missing
              and any("分工" in m.get("what", "") for m in missing)
              and all(set(m.get("four") or {}) == {"who", "what", "where", "how"} for m in missing),
              json.dumps([m.get("what") for m in missing][:5], ensure_ascii=False))
        (wsroot / "前端组").mkdir(parents=True, exist_ok=True)
        (wsroot / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (wsroot / "前端组" / "_notice.md").write_text("", encoding="utf-8")
        for sub in ("任务", "产物"):
            (wsroot / SLOT / sub).mkdir(parents=True, exist_ok=True)
        st, v2 = _http(base, "/api/workshop/%s/verify-dirs" % wid)
        check("② 补齐后校验通过（平台不代建，只 stat）", v2.get("ok") is True,
              json.dumps(v2.get("missing"), ensure_ascii=False)[:160])
        (wsroot / "前端组" / "分工.md").write_text("# 分工（没有版本行）\n", encoding="utf-8")
        st, v3 = _http(base, "/api/workshop/%s/verify-dirs" % wid)
        check("③ 分工.md 缺 `- 版本:` 行 ⇒ 明确指出",
              v3.get("ok") is False and any("版本" in m.get("what", "") for m in v3.get("missing") or []),
              json.dumps(v3.get("missing"), ensure_ascii=False)[:160])
        (wsroot / "前端组" / "分工.md").write_text("- 版本: 2\n", encoding="utf-8")

        # ── V4~V6 产物清单 ───────────────────────────────────────
        st, e1 = _http(base, "/api/workshop/%s/verify-artifacts" % wid, {})
        check("④ 产物目录空 ⇒ 清单就是空（不假装有产物）",
              st == 200 and e1.get("total") == 0, json.dumps(e1, ensure_ascii=False)[:160])
        f = wsroot / SLOT / "产物" / "api.md"
        f.write_text("hello", encoding="utf-8")
        st, e2 = _http(base, "/api/workshop/%s/verify-artifacts" % wid, {})
        info = ((e2.get("slots") or {}).get(SLOT) or {}).get("files", {}).get("api.md") or {}
        check("⑤ 清单带 sha256 与字节数，且落盘 _meta/artifacts.json",
              e2.get("total") == 1 and len(info.get("sha256") or "") == 64
              and info.get("bytes") == 5
              and (wsroot / "_meta" / "artifacts.json").is_file(),
              json.dumps(info, ensure_ascii=False)[:160])
        f.write_text("hello-v2-longer", encoding="utf-8")
        st, e3 = _http(base, "/api/workshop/%s/verify-artifacts" % wid, {})
        check("⑥ 改过的产物进 changed（不是又算一遍 new）",
              "%s/api.md" % SLOT in (e3.get("diff") or {}).get("changed", [])
              and not (e3.get("diff") or {}).get("new"),
              json.dumps(e3.get("diff"), ensure_ascii=False)[:160])

        # ── V7~V9 验收（先派活）──────────────────────────────────
        _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, seq),
              {"paste_seen": True, "press_ok": True, "rename_seen": True,
               "verified_scope": "夹具：三项齐"})
        st, d = _http(base, "/api/workshop/%s/dispatch" % wid,
                      {"member_seq": seq, "title": "P4 验收用", "goal": "验收"})
        tseq = d.get("task_seq")
        check("夹具：派活成功并进账本", bool(d.get("ok")) and (d.get("ledger") or {}).get("state") == "issued",
              json.dumps(d.get("ledger"), ensure_ascii=False))
        st, rv = _http(base, "/api/workshop/%s/tasks/%s/review" % (wid, tseq),
                       {"result": "通过"})
        check("⑦ 验收「通过」但没给证据等级 ⇒ 拒（EVIDENCE_REQUIRED）",
              st == 400 and rv.get("code") == "EVIDENCE_REQUIRED",
              json.dumps(rv, ensure_ascii=False)[:160])
        st, rv3 = _http(base, "/api/workshop/%s/tasks/%s/review" % (wid, tseq),
                        {"result": "通过", "evidence_level": "L3"})
        check("⑧ 证据等级 L3（大模型自报）⇒ 拒（EVIDENCE_L3_REJECTED）",
              st == 400 and rv3.get("code") == "EVIDENCE_L3_REJECTED",
              json.dumps(rv3, ensure_ascii=False)[:160])
        # ★2026-10-31（V121 / V116 §15.1 老板拍板）：L1＝「它库已有」，必须自带凭据
        st, rv_l1 = _http(base, "/api/workshop/%s/tasks/%s/review" % (wid, tseq),
                          {"result": "通过", "evidence_level": "L1"})
        check("⑨b L1 不给凭据 ⇒ 拒（L1_NEEDS_PROOF）；失败态带证据四步表 + 说清该谁补",
              st == 400 and rv_l1.get("code") == "L1_NEEDS_PROOF"
              and [s.get("name") for s in (rv_l1.get("steps") or [])]
              == ["派活记录", "产物在盘上", "回执留痕", "人拍板"]
              and bool(rv_l1.get("who_should_fix")),
              json.dumps(rv_l1, ensure_ascii=False)[:220])
        st, ok_rev = _http(base, "/api/workshop/%s/tasks/%s/review" % (wid, tseq),
                           {"result": "通过", "evidence_level": "L1",
                            "evidence_ref": "产物目录 diff 已人工核对（示例凭据）",
                            "note": "接口写清了"})
        check("⑨ 给 L1 + 凭据 ⇒ 任务 accepted（并记验收人/时间，且明标「平台未核对」）",
              ok_rev.get("ok") and ok_rev.get("state") == "accepted"
              and "未核对" in (ok_rev.get("evidence_label") or ""),
              json.dumps(ok_rev, ensure_ascii=False)[:200])

        # ── V8~V10 归档 ──────────────────────────────────────────
        sha_before = None
        import hashlib

        sha_before = hashlib.sha256(f.read_bytes()).hexdigest()
        st, integ = _http(base, "/api/workshop/%s/integrate-report" % wid, {})
        sha_after = hashlib.sha256(f.read_bytes()).hexdigest()
        check("⑩ integrate-report 写清单（accepted 计数对）且**不动产物文件**",
              integ.get("ok") and (integ.get("summary") or {}).get("accepted") == 1
              and sha_before == sha_after
              and (wsroot / "_meta" / "integrate.json").is_file(),
              json.dumps((integ.get("summary") or {}), ensure_ascii=False)[:160])
        st, closed = _http(base, "/api/workshop/%s/close" % wid, {})
        check("⑪ 归档：状态置 closed 且 token 全部吊销",
              closed.get("ok") and closed.get("status") == "closed" and closed.get("revoked"),
              json.dumps(closed, ensure_ascii=False)[:160])

        # ── 审忆员 M-R1~M-R5 ─────────────────────────────────────
        m = minds / "A-01"
        for d in ("memories", "02_任务日志", "06_死胡同", "_meta"):
            (m / d).mkdir(parents=True, exist_ok=True)
        (m / "00_宪章.md").write_text("# 宪章\n", encoding="utf-8")
        (m / "00_核心基模.md").write_text("# 基模\n- 初始\n", encoding="utf-8")
        (m / "01_记忆索引表.md").write_text("# 索引\n", encoding="utf-8")
        (m / "memories" / "20261027-打包踩坑.md").write_text("# 记忆\n- 打包装完要验体积\n", encoding="utf-8")
        (m / "02_任务日志" / "2026-10-27.md").write_text(
            "# 任务日志\n- 接口联调完成\n- 自测通过\n", encoding="utf-8")
        (m / "06_死胡同" / "2026-10-27.md").write_text("# 死胡同\n- 别在迁移中间改索引\n",
                                                       encoding="utf-8")
        before = {p.name: p.read_text(encoding="utf-8") for p in m.rglob("*") if p.is_file()}
        cmd = [PY, str(Path(_ROOT) / "tools" / "mind_reviewer.py"), "--mind-root", str(minds),
               "--date", "2026-10-27", "--json"]
        dry = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", cwd=_ROOT)
        after_dry = {p.name: p.read_text(encoding="utf-8") for p in m.rglob("*") if p.is_file()}
        check("⑫ 审忆员 dry-run 一个字节都不写", dry.returncode == 0 and before == after_dry,
              (dry.stderr or "")[:120])
        app = subprocess.run(cmd + ["--apply"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", cwd=_ROOT)
        archived = list((m / "_archive" / "2026-10-27").glob("*.bak"))
        comp = m / "02_压缩" / "2026-10-27.md"
        check("⑬ apply：先归档原文（两份不互相覆盖）+ 写压缩",
              app.returncode == 0 and len(archived) == 2 and comp.is_file()
              and "接口联调完成" in comp.read_text(encoding="utf-8"),
              "archived=%d" % len(archived))
        core_before = before["00_核心基模.md"]
        pend = list((m / "_pending").glob("*.diff"))
        notes = list((m / "_pending").glob("*.note.md"))
        # ★2026-10-31 修判据（既有问题，非本次引入）：881e63b 起审忆员产出**真补丁**，
        #   `改前 hash` 改落在 `<pid>.note.md`（说明与哈希分开），判据却还在 `.diff` 里找 ⇒ 长期假红（8dc159d 上同样红）。
        check("⑭ 核心基模**没被改**；待审里是**真补丁**（有 @@ hunk）+ 说明带改前 hash（等拍板）",
              (m / "00_核心基模.md").read_text(encoding="utf-8") == core_before
              and pend and notes
              and "@@" in pend[0].read_text(encoding="utf-8")
              and "改前 hash" in notes[0].read_text(encoding="utf-8"),
              "pending=%d notes=%d" % (len(pend), len(notes)))
    finally:
        if proc is not None and not a.keep:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:      # noqa: BLE001 — 有意静默：收尾杀不掉不影响结论
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001 — 有意静默：同上
                    pass
        if not a.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：P4 目录/产物/验收/归档/审忆员 —— 临时实例 %s" % base)
    print("=" * 78)
    for name, ok, detail in rows:
        tag = "PASS" if ok else "FAIL"
        print("  %s %s%s" % (tag, name, ("　（%s）" % detail) if not ok else ""))
    print("-" * 78)
    _pass = sum(1 for _n, _o, _d in rows if _o)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(rows), _pass, len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
