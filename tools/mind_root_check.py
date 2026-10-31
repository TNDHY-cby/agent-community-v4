#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**双根 + manifest G0 + Boot Prompt + 日结**真机十二格（`V117`，P2.5 批）。

跑在临时实例上（换端口 + 换数据目录 + 换心智根），全流程真 HTTP：

```
根A：建档案（宪章/基模/索引/memories/日志/死胡同）→ 追加记忆（append-only）
根B：没 manifest ⇒ 409 四要素；心智根被放进工作区 ⇒ 422；正常写 ⇒ 读回 G0 通过
Boot Prompt：两个根写死且**真实存在**（占位符会被判红）
日结：写 02_任务日志 + 06_死胡同（同一天追加不覆盖）
边界：审忆员不该在这批里出现（清单里没有就说没有）
```

退出码：0＝全过；1＝有格没过；2＝起不来。
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
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_mind_"))
    base = "http://127.0.0.1:%d" % port
    wsroot = tmpdir / "wsroot"
    minds = tmpdir / "minds"
    wsroot.mkdir(parents=True, exist_ok=True)
    minds.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(minds), "PYTHONIOENCODING": "utf-8"})
    rows, fails, skips = [], [], []
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

        # ── 根A ────────────────────────────────────────────────────
        st, mk = _http(base, "/api/mind/A-01", {"display_name": "示例"})
        mp = Path(str((mk or {}).get("mind_path") or ""))
        check("① 心智根建成（宪章/基模/索引/memories/日志/死胡同）",
              st == 200 and mp.is_dir()
              and all((mp / n).is_file() for n in ("00_宪章.md", "00_核心基模.md", "01_记忆索引表.md"))
              and all((mp / d).is_dir() for d in ("memories", "02_任务日志", "06_死胡同")),
              str(mp))
        st, mk2 = _http(base, "/api/mind/A-01", {})
        check("② 幂等：第二次不再建、不覆盖已有文件",
              st == 200 and mk2.get("created") == [], str(mk2.get("created")))
        st, mem1 = _http(base, "/api/mind/A-01/memories", {"text": "联调先看 manifest", "tags": ["联调"]})
        st, mem2 = _http(base, "/api/mind/A-01/memories", {"text": "别在迁移中间改索引", "tags": ["迁移"]})
        check("③ 记忆 append-only（两条两个文件，不覆盖）",
              mem1.get("file") and mem2.get("file") and mem1["file"] != mem2["file"],
              "%s / %s" % (mem1.get("file"), mem2.get("file")))
        got = _http(base, "/api/mind/A-01")[1]
        check("④ 只读实况能查（记忆 2 条，不含正文）",
              got.get("counts", {}).get("memories") == 2 and "memories" in got,
              json.dumps(got.get("counts"), ensure_ascii=False))

        # ── 根B：manifest G0 ───────────────────────────────────────
        st, made = _http(base, "/api/workshop", {"name": "示例间", "hall_content": "方向",
                                                 "workshop_path": str(wsroot)})
        wid = str((made or {}).get("workshop_id") or "")
        check("夹具：建工作间（根B）", bool(wid), "HTTP %s" % st)
        if not wid:
            raise SystemExit(1)
        st, nomf = _http(base, "/api/workshop/%s/manifest" % wid)
        four = (nomf or {}).get("four") or {}
        check("⑤ 没 manifest ⇒ 409 + 四要素齐全",
              st == 409 and set(four) == {"who", "what", "where", "how"} and all(four.values()),
              json.dumps(nomf, ensure_ascii=False)[:220])
        st, nested = _http(base, "/api/workshop/%s/manifest" % wid,
                           {"mind_path": str(wsroot / "minds"), "entry_points": ["_lead/建模.md"]})
        check("⑥ 心智根被放进工作区 ⇒ 拒收（422，M005：归档就丢记忆）",
              st == 422 and (nested.get("errors") or [{}])[0].get("code") == "M005",
              json.dumps(nested, ensure_ascii=False)[:220])
        (wsroot / "_lead").mkdir(parents=True, exist_ok=True)
        (wsroot / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
        st, noentry = _http(base, "/api/workshop/%s/manifest" % wid,
                            {"mind_path": str(minds / "A-01"),
                             "entry_points": ["_lead/建模.md", "前端组/分工.md"]})
        check("⑦ entry_points 里有不存在的文件 ⇒ 拒收（M007 指名道姓）",
              st == 422 and (noentry.get("errors") or [{}])[0].get("code") == "M007",
              json.dumps(noentry, ensure_ascii=False)[:220])
        st, good = _http(base, "/api/workshop/%s/manifest" % wid,
                         {"mind_path": str(minds / "A-01"), "entry_points": ["_lead/建模.md"],
                          "ignore_zones": ["_meta/evidence/"], "task_focus": "接口联调"})
        check("⑧ 正常写 manifest（平台算 hash，不写回文件）",
              st == 200 and str((good or {}).get("manifest_hash", "")).startswith("sha256:"),
              json.dumps(good, ensure_ascii=False)[:200])
        st, rd = _http(base, "/api/workshop/%s/manifest" % wid)
        check("⑨ 读回 G0 通过（ok=true，不回显 hash 到文件）",
              st == 200 and rd.get("ok") is True and "manifest_hash" not in (rd.get("manifest") or {}),
              json.dumps(rd, ensure_ascii=False)[:200])

        # ── Boot Prompt ────────────────────────────────────────────
        st, bp = _http(base, "/api/workshop/%s/boot-prompt" % wid, {"agent_key": "A-01"})
        text = str((bp or {}).get("boot_prompt") or "")
        check("⑩ Boot Prompt 两个根写死且真实存在（占位符会被判红）",
              st == 200 and bp.get("paths_are_real") is True
              and str(minds / "A-01") in text and str(wsroot) in text
              and "禁止" in text and "_meta/evidence/" in text,
              json.dumps((bp or {}).get("unreal_paths"), ensure_ascii=False))

        # ── 日结 ───────────────────────────────────────────────────
        st, dc = _http(base, "/api/workshop/%s/daily-close" % wid,
                       {"agent_key": "A-01", "date": "2026-10-27",
                        "done": ["接口联调完成"], "blocked": ["桥没起"]})
        log = minds / "A-01" / "02_任务日志" / "2026-10-27.md"
        dead = minds / "A-01" / "06_死胡同" / "2026-10-27.md"
        check("⑪ 日结真写 02_任务日志 + 06_死胡同",
              st == 200 and log.is_file() and dead.is_file()
              and "接口联调完成" in log.read_text(encoding="utf-8"),
              json.dumps(dc, ensure_ascii=False)[:200])
        _http(base, "/api/workshop/%s/daily-close" % wid,
              {"agent_key": "A-01", "date": "2026-10-27", "done": ["自测通过"]})
        body = log.read_text(encoding="utf-8")
        check("⑫ 同一天再日结 ⇒ 追加（两条都在，不覆盖）",
              "接口联调完成" in body and "自测通过" in body, body[:160])
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
    print("判据：双根 + manifest G0 + Boot Prompt + 日结（P2.5）—— 临时实例 %s" % base)
    print("=" * 78)
    for name, ok, detail in rows:
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print("  %s %s%s" % (tag, name, ("　（%s）" % detail) if ok is not True else ""))
    print("-" * 78)
    _pass = sum(1 for _n, _o, _d in rows if _o is True)
    print("合计 %d 格：PASS %d / FAIL %d / SKIP %d" % (len(rows), _pass, len(fails), len(skips)))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
