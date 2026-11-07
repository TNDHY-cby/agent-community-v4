#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**文件投递通道**（`V133`）—— 成员把文件放进收件目录，平台发现并转给组长。

老板 2026-11-07：「视觉脚本成员把文件传到一个特定文件夹……用小型脚本检测一个文件夹，
当有新增文件的时候就上报到平台，平台转给组长」。

真跑：临时实例（换端口 + 换 `AC_DATA_DIR`）+ 真工作间目录 + 真文件。

| # | 格 | 断言 |
|---|---|---|
| ① | 平台活着 | `GET /api/status` 200 |
| ② | 夹具 | 工作间 + 1 名成员 + 组长（`_lead`）真建出来 |
| ③ | 基线 | 首次扫一个**已有文件**的收件目录 ⇒ `baseline>0` 且 `new` 为空（不刷历史） |
| ④ | 新增 | 放 1 个文件再扫 ⇒ `new` 1 条，带真 `sha256`/`bytes` |
| ⑤ | 去重 | 再扫一次 ⇒ `new` 为空 |
| ⑥ | 内容变 | 改文件内容 ⇒ `changed` 1 条 |
| ⑦ | 转组长 | 组长的 `_lead/_notice.md` 真有这一行（含文件名与工位） |
| ⑧ | 目录不存在 | 档案指向不存在的目录 ⇒ `errors` 有人话，**不是"没有新文件"** |
| ⑨ | 事件流 | `GET …/inbox-events` 倒序、带 `source=platform_observed` / `judge=verified` |
| ⑩ | 状态端点 | `GET …/inbox-status` 列出每个工位的收件目录与 `exists` |
| ⑪ | 只读 | 扫描前后成员文件的 sha256 与 mtime 不变 |
| ⑫ | 可关 | `AC_ARTIFACT_WATCH_SEC=0` 起实例 ⇒ `enabled=false`（不偷偷跑） |

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
    # ⚠️ 路径里可能有中文（如 harness_id「示例Harness-A」）：urllib **不会**自动做 URL 编码，
    #    不编码就 UnicodeEncodeError（实测踩过）。浏览器/前端会自动编码，所以这只是工具的写法问题。
    import urllib.parse

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


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_inbox_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                "AC_ARTIFACT_WATCH_SEC": "3600"})   # 后台扫描关到很远：本判据只手动扫
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：文件投递通道（V133 · 真跑：临时实例 + 真目录 + 真文件）")
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

        # ── 夹具：工作间 + 成员 + 组长 ─────────────────────────────────────
        ws_root = Path(tmpdir) / "ws_inbox"
        slot = "前端组/员工01"
        for d in (ws_root / "_lead", ws_root / "前端组", ws_root / slot / "任务", ws_root / slot / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (ws_root / "_lead" / "建模.md").write_text("# 建模（示例）\n", encoding="utf-8")
        (ws_root / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        (ws_root / "前端组" / "_notice.md").write_text("", encoding="utf-8")
        st, made = _http(base, "/api/workshop", {"name": "投递通道判据间（示例）",
                                                 "hall_content": "文件投递",
                                                 "workshop_path": str(ws_root)})
        wid = str((made or {}).get("workshop_id") or "")
        mk = _http(base, "/api/mind/A-01", {})[1] or {}
        _http(base, "/api/workshop/%s/manifest" % wid,
              {"mind_path": str(mk.get("mind_path") or ""), "entry_points": ["_lead/建模.md"]})
        txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 5\n---\n" % wid
               + "岗位: %s | harness: 示例Harness-A | model: 示例模型 | 人数: 1\n" % slot
               + "岗位: _lead | harness: 示例Harness-A | model: 示例模型 | 人数: 1\n"
               + "---\n行数: 2")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": txt})
        mem = _http(base, "/api/workshop/%s/roster" % wid)[1] or {}
        check("② 夹具：工作间 + 成员 + 组长",
              bool(wid) and len(mem.get("members") or []) >= 2,
              "wid=%r members=%d" % (wid, len(mem.get("members") or [])))

        products = ws_root / slot / "产物"

        # ③ 基线：先放一个老文件，首次扫不许当新增
        (products / "老文件.md").write_text("历史内容", encoding="utf-8")
        st3, b3 = _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        check("③ 基线：首次扫不把历史文件当新增",
              st3 == 200 and int((b3 or {}).get("baseline") or 0) >= 1
              and not (b3 or {}).get("new"),
              "HTTP %s baseline=%r new=%d" % (st3, (b3 or {}).get("baseline"),
                                              len((b3 or {}).get("new") or [])))

        # ④ 新增
        nf = products / "报告.md"
        nf.write_text("这是真的产物正文", encoding="utf-8")
        st4, b4 = _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        news = (b4 or {}).get("new") or []
        ev = news[0] if news else {}
        check("④ 新增文件 ⇒ 一条事件（带真 sha256/bytes）",
              st4 == 200 and len(news) == 1 and ev.get("sha256") == _sha(nf)
              and int(ev.get("bytes") or 0) == nf.stat().st_size,
              "HTTP %s new=%r" % (st4, json.dumps(news[:1], ensure_ascii=False)[:200]))

        # ⑤ 去重
        st5, b5 = _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        check("⑤ 再扫一次不重复", st5 == 200 and not (b5 or {}).get("new"),
              "new=%r" % ((b5 or {}).get("new") or [])[:2])

        # ⑥ 内容变
        nf.write_text("这是改过的正文（v2）", encoding="utf-8")
        st6, b6 = _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        chg = (b6 or {}).get("changed") or []
        check("⑥ 改内容 ⇒ changed（sha 变了）",
              st6 == 200 and len(chg) == 1 and chg[0].get("sha256") == _sha(nf),
              "changed=%r" % json.dumps(chg[:1], ensure_ascii=False)[:160])

        # ⑦ 转组长
        notice = ws_root / "_lead" / "_notice.md"
        ntext = notice.read_text(encoding="utf-8") if notice.is_file() else ""
        check("⑦ 组长收到通知（_lead/_notice.md 真有这一行）",
              "报告.md" in ntext and slot in ntext, ntext[-200:])

        # ⑧ 目录不存在（把成员档案的收件目录指到不存在的地方）
        # ⚠️ 真实端点就是 `POST /api/docking/harness/{id}`（**没有 `/edit` 后缀**）。
        #    首版写成 `/edit` ⇒ 405，而 errors 恰好来自别的工位 ⇒ 假绿（实测抓到，已修）。
        hid = str((mem.get("members") or [{}])[0].get("harness") or "示例Harness-A")
        _missing = str(Path(tmpdir) / "不存在的收件目录")
        st_e, b_e = _http(base, "/api/docking/harness/%s" % hid,
                          {"artifact_inbox_dir": _missing})
        _applied = str(((b_e or {}).get("archive") or {}).get("artifact_inbox_dir") or "")
        st8, b8 = _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        errs = (b8 or {}).get("errors") or []
        check("⑧ 目录不存在 ⇒ errors 说清（且改档案真生效）",
              st_e == 200 and _applied == _missing
              and st8 == 200 and bool(errs) and "不存在" in json.dumps(errs, ensure_ascii=False),
              "edit=%s applied=%r errs=%s"
              % (st_e, _applied[:60], json.dumps(errs[:1], ensure_ascii=False)[:160]))
        # 恢复默认目录，后面的格不受影响
        _http(base, "/api/docking/harness/%s" % hid, {"artifact_inbox_dir": ""})

        # ⑨ 事件流端点
        st9, b9 = _http(base, "/api/workshop/%s/inbox-events?limit=10" % wid)
        evs = (b9 or {}).get("events") or []
        check("⑨ 事件流倒序 + source/judge 齐全",
              st9 == 200 and bool(evs) and evs[0].get("file") == "报告.md"
              and evs[0].get("source") == "platform_observed"
              and evs[0].get("judge") == "verified",
              json.dumps(evs[:1], ensure_ascii=False)[:200])

        # ⑩ 状态端点列出收件目录
        st10, b10 = _http(base, "/api/workshop/%s/inbox-status" % wid)
        dirs = (b10 or {}).get("inbox_dirs") or []
        check("⑩ 状态端点列出各工位收件目录 + exists",
              st10 == 200 and bool(dirs) and any(d.get("exists") for d in dirs),
              json.dumps(dirs[:2], ensure_ascii=False)[:220])

        # ⑪ 只读
        before = (_sha(nf), nf.stat().st_mtime_ns)
        _http(base, "/api/workshop/%s/scan-inbox" % wid, {})
        after = (_sha(nf), nf.stat().st_mtime_ns)
        check("⑪ 扫描只读（文件 sha256 与 mtime 不变）", before == after,
              "before=%s after=%s" % (before[0][:8], after[0][:8]))

        # ⑫ 可关：另起一个实例，AC_ARTIFACT_WATCH_SEC=0
        port2 = _free_port()
        tmp2 = tempfile.mkdtemp(prefix="ac_inbox_off_")
        env2 = dict(os.environ)
        env2.update({"AC_PORT": str(port2), "AC_DATA_DIR": tmp2, "PYTHONIOENCODING": "utf-8",
                     "AC_ARTIFACT_WATCH_SEC": "0"})
        srv2 = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env2,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base2 = "http://127.0.0.1:%d" % port2
        try:
            up2 = False
            for _ in range(80):
                try:
                    st2, _ = _http(base2, "/api/artifact-inbox/status", timeout=3)
                    up2 = st2 == 200
                except (urllib.error.URLError, OSError):
                    up2 = False
                if up2:
                    break
                time.sleep(0.5)
            st12, b12 = _http(base2, "/api/artifact-inbox/status") if up2 else (0, {})
            check("⑫ AC_ARTIFACT_WATCH_SEC=0 ⇒ enabled=false（不偷偷跑）",
                  st12 == 200 and (b12 or {}).get("enabled") is False
                  # ⚠️ 别写 `interval or -1`：interval=0 是**合法值**，`or` 会把它变成 -1（实测踩过）
                  and int((b12 or {}).get("interval", -1)) == 0,
                  "HTTP %s %s" % (st12, json.dumps(_prune(b12), ensure_ascii=False)[:200]))
        finally:
            try:
                srv2.terminate()
                srv2.wait(timeout=10)
            except Exception:                                  # noqa: BLE001 — 收尾：第二个实例没起来就杀掉
                srv2.kill()
            shutil.rmtree(tmp2, ignore_errors=True)
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
            print("  FAIL:", lb, "|", str(d)[:220])
    return 0 if passed == len(ROWS) else 1


def _prune(d: dict) -> dict:
    """状态里路径很长，报错时只留关键几项。"""
    keys = ("enabled", "interval", "running", "last_run", "last_error")
    return {k: (d or {}).get(k) for k in keys if k in (d or {})}


if __name__ == "__main__":
    raise SystemExit(main())
