#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**根系治理四件**（`V141` §3.1/§3.2 / 队列 49-1~49-4）。

真跑：临时实例（换端口 + 换数据目录 + 换心智根）+ 真写记忆 + 真申诉 + 真淘汰工具。

| # | 格 | 断言 |
|---|---|---|
| ① | 四层齐全、每层带治理参数 | `charter/review/log/experience` 都在，字段 `write/need_evidence/need_crosscheck/appealable/periodic` 齐 |
| ② | 「谁能写」按门槛真生效 | 宪章只 `human`（reviewer 不行）；审忆允许 `reviewer`（`auto` 不行） |
| ③ | **宪章不可申诉** | `appealable=False` + 真打 `target_tier=charter` ⇒ 400 `TIER_NOT_APPEALABLE` |
| ④ | R1 无档被拒 | `POST …/memories` 不带 `grade` ⇒ 400 `GRADE_REQUIRED`（**不落盘**） |
| ⑤ | R1 两档都能入库 | 写 `reusable` + `record` 各一条 ⇒ 文件头有 `- 档:`，两档查得出来 |
| ⑥ | R3 申诉带证据才收、**不自动通过** | 空证据 400 `APPEAL_NEEDS_PROOF`；带证据 ⇒ `status=pending` 且审计有 `mind.appeal` |
| ⑦ | R2 淘汰只动 record、归档不物理删 | `--dry-run` 列出 record、`reusable` 一条不动；`--apply` 移进 `archive/` 且原文件消失、归档里有 |
| ⑧ | 一处真相 | `GET /api/root-tiers` 返回的四层与 `root_tiers.TIERS` 逐字相同（不允许两份口径） |

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
KEY = "mem-judge-01"


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
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_tiers_"))
    minds = tmpdir / "minds"
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(minds), "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：根系治理四件（49-1~49-4 · 真跑）")
    print("=" * 78)
    try:
        up = False
        for _ in range(80):
            try:
                up = _http(base, "/api/status", timeout=3)[0] == 200
            except (urllib.error.URLError, OSError):           # noqa: BLE001 — 起服期间连不上是预期
                up = False
            if up:
                break
            time.sleep(0.5)
        if not up:
            print("[FAIL] 平台没起来 —— 这次不算跑成")
            return 2

        st1, b1 = _http(base, "/api/root-tiers")
        tiers = (b1 or {}).get("tiers") or {}
        need = ("write", "need_evidence", "need_crosscheck", "appealable", "periodic")
        check("① 四层齐全、每层带治理参数",
              st1 == 200 and {"charter", "review", "log", "experience"} <= set(tiers)
              and all(all(k in (tiers.get(n) or {}) for k in need) for n in tiers),
              "HTTP %s tiers=%r" % (st1, list(tiers)))

        from agent_community.platform import root_tiers as _rt

        check("② 「谁能写」按门槛真生效（宪章只人 / 审忆允许审忆员、不许自动）",
              _rt.can_write("charter", "human") and not _rt.can_write("charter", "reviewer")
              and _rt.can_write("review", "reviewer") and not _rt.can_write("review", "auto")
              and _rt.can_write("log", "auto"),
              "charter(human)=%r charter(reviewer)=%r review(auto)=%r"
              % (_rt.can_write("charter", "human"), _rt.can_write("charter", "reviewer"),
                 _rt.can_write("review", "auto")))

        st3, b3 = _http(base, "/api/mind/appeal",
                        {"target_tier": "charter", "reason": "我不服",
                         "evidence_ref": "session:abc/谁核的:判据工具"})
        check("③ **宪章不可申诉**（appealable=False + 端点拒）",
              _rt.appealable("charter") is False and st3 == 400
              and (b3 or {}).get("code") == "TIER_NOT_APPEALABLE",
              "HTTP %s %s" % (st3, (b3 or {}).get("code")))

        before = len(list((minds / KEY / "memories").glob("*.md"))) if (minds / KEY / "memories").is_dir() else 0
        st4, b4 = _http(base, "/api/mind/%s/memories" % KEY, {"text": "没带档的一条"})
        after = len(list((minds / KEY / "memories").glob("*.md"))) if (minds / KEY / "memories").is_dir() else 0
        check("④ R1 不给 grade ⇒ 400 GRADE_REQUIRED（且**不落盘**）",
              st4 == 400 and (b4 or {}).get("code") == "GRADE_REQUIRED" and before == after,
              "HTTP %s %s 文件 %d→%d" % (st4, (b4 or {}).get("code"), before, after))

        st5a, b5a = _http(base, "/api/mind/%s/memories" % KEY,
                          {"text": "能复用的经验：打包前先验体积", "grade": "reusable", "tags": ["打包"]})
        st5b, b5b = _http(base, "/api/mind/%s/memories" % KEY,
                          {"text": "只是一条记录：今天改了个文案", "grade": "record", "tags": ["杂记"]})
        files = list((minds / KEY / "memories").glob("*.md")) if (minds / KEY / "memories").is_dir() else []
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in files]
        check("⑤ R1 两档都能入库（文件头有 `- 档:`，两档分得开）",
              st5a == 200 and st5b == 200 and len(files) == 2
              and sum(1 for t in texts if "- 档: reusable" in t) == 1
              and sum(1 for t in texts if "- 档: record" in t) == 1,
              "files=%d reusable=%d record=%d"
              % (len(files), sum(1 for t in texts if "- 档: reusable" in t),
                 sum(1 for t in texts if "- 档: record" in t)))

        st6a, b6a = _http(base, "/api/mind/appeal",
                          {"target_tier": "review", "reason": "我的记忆被误改", "evidence_ref": ""})
        st6, b6 = _http(base, "/api/mind/appeal",
                        {"target_tier": "review", "reason": "我的记忆被误改",
                         "evidence_ref": "库:demo.db/会话:2026-11-09-01/核的人:示例组长"})
        ap = (b6 or {}).get("appeal") or {}
        aud = ((_http(base, "/api/audit?limit=20")[1] or {}).get("events") or [])
        check("⑥ R3 申诉带证据才收、**不自动通过**（pending + 写审计）",
              st6a == 400 and (b6a or {}).get("code") == "APPEAL_NEEDS_PROOF"
              and st6 == 200 and ap.get("status") == "pending"
              and any(str((e or {}).get("event")) == "mind.appeal" for e in aud),
              "空证据=%s %s | 带证据=%s status=%r 审计有=%r"
              % (st6a, (b6a or {}).get("code"), st6, ap.get("status"),
                 any(str((e or {}).get("event")) == "mind.appeal" for e in aud)))

        # ⑦ 淘汰：把两条记忆的时间回拨 40 天，再跑真工具
        old = time.time() - 40 * 86400
        for p in files:
            os.utime(p, (old, old))
        env2 = dict(os.environ)
        env2.update({"AC_MIND_ROOT": str(minds), "PYTHONIOENCODING": "utf-8", "AC_DATA_DIR": str(tmpdir / "data")})
        tool = str(ROOT / "tools" / "memory_evict.py")
        # ⚠️ 淘汰看的是**文件头里的 `- 时间:`**（工具优先信它，mtime 只作兜底）
        #   ⇒ 判据要把那行改写成 40 天前，而不是回拨 mtime；阈值用正常的 30 天。
        import re as _re

        _old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(old))
        for p in files:
            t = p.read_text(encoding="utf-8", errors="replace")
            t2 = _re.sub(r"^-\s*时间[:：].*$", "- 时间: %s" % _old, t, count=1, flags=_re.M)
            p.write_text(t2, encoding="utf-8")
        dry = subprocess.run([PY, "-X", "utf8", tool, "--dry-run", "--json"],
                             capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=180, env=env2)
        try:
            dj = json.loads(dry.stdout or "{}")
        except ValueError:
            dj = {}
        cand_grades = [c.get("grade") for c in (dj.get("candidates") or [])]
        app = subprocess.run([PY, "-X", "utf8", tool, "--apply"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=180, env=env2)
        left = list((minds / KEY / "memories").glob("*.md")) if (minds / KEY / "memories").is_dir() else []
        arch = list((minds / KEY / "archive" / "memories").glob("*.md")) \
            if (minds / KEY / "archive" / "memories").is_dir() else []
        check("⑦ R2 淘汰只动 record（reusable 不动）、归档**不物理删**",
              "record" in cand_grades and "reusable" not in cand_grades
              and len(arch) == 1 and len(left) == 1
              and "reusable" in left[0].read_text(encoding="utf-8", errors="replace"),
              "候选档=%r 剩=%d 归档=%d exit=%s" % (cand_grades, len(left), len(arch), app.returncode))

        st8, b8 = _http(base, "/api/root-tiers")
        same = json.dumps((b8 or {}).get("tiers"), sort_keys=True, ensure_ascii=False) == \
            json.dumps(_rt.TIERS, sort_keys=True, ensure_ascii=False)
        check("⑧ 一处真相：端点表与 root_tiers.TIERS 逐字相同", st8 == 200 and same,
              "same=%r" % same)
    finally:
        try:
            srv.terminate()
            srv.wait(timeout=10)
        except Exception:                                      # noqa: BLE001 — 收尾
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
