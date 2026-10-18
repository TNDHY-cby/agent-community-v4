# -*- coding: utf-8 -*-
"""内核体检：**六种协议 × 桥模板 × 唤醒脚本 × 投递** 的端到端矩阵（独立实例，真 HTTP）。

## 为什么有它

老板 2026-10-18：「先把核心做好：各种协议、桥、唤醒脚本」。
这三块模块很多（桥相关 20+ 模块 / 20+ 端点），但**没人一次把六种协议整条链跑通给你看**。
本工具就是那张表：每种协议跑同一套五步，逐格给 OK/失败与原因。

## 五步（每种协议都跑）

1. **登记**：`POST /api/harness/register`（最小 JSON 来自 `register_prompt._minimal_json`，一处真相）
2. **叫醒脚本**：`POST /api/harness/{hid}/wake {command}` ⇒ 再 `GET` 复核 `configured`；
   并检查落点在**数据目录**里（不许写进仓库）
3. **具体桥模板**：`POST /api/harness/{hid}/bridge-template`（kind 用 `interface_guidance` 该协议的 primary_bridge）
   —— 另跑一次**故意错的 kind**，确认它会 **400 且给 hint**（失败要说话）
4. **生成桥 + 自检**：`POST /api/harness/{hid}/bridge/generate`（内置模板）→ `POST /api/harness/bridge-verify`
5. **投递**：`POST /api/harness/bridge-test` ⇒ 看消息落到哪（file_poll 应为 inbox 文件；其余进 pending 队列）

跑法：`python tools/protocol_matrix.py`（起临时实例 → 跑 → 关；退出码 0＝全绿 / 1＝有格失败）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("AC_MATRIX_PORT") or 11153)


def _base() -> str:
    return "http://127.0.0.1:%d" % PORT


def req(path: str, body=None, timeout: float = 20.0, method: str | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(_base() + path, data=data,
                               method=method or ("POST" if body is not None else "GET"),
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                                  # noqa: BLE001
            return e.code, {"raw": raw[:300]}
    except Exception as e:                                 # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def protocols() -> list:
    sys.path.insert(0, str(ROOT))
    from agent_community.platform import register_prompt as rp
    from agent_community.platform import interface_guidance as ig
    out = []
    for w in rp.SUPPORTED:
        m = ig.match_for(w) or {}
        out.append({"method": w, "bridge": m.get("primary_bridge"), "label": (ig.for_wakeup(w) or {}).get("label")})
    return out


def minimal_for(method: str, hid: str) -> dict:
    """最小注册 JSON —— **直接取 `register_prompt._minimal_json`**（一处真相，别在这里另编一套）。

    ⚠️ 2026-10-18 修：我第一版自己往 `http_api` 里塞了 `wakeup_url`（端点根本没这个字段，
    而且 `wakeup_url` 走的是**严格 SSRF 校验**、连 127.0.0.1 都禁）⇒ 矩阵里 http_api 全红是**我的错**。
    正确字段是 `api_base_url`（走 `validate_harness_api_url`，本机回环合法 —— 本机桌面 Agent 的常态）。
    唯一需要"喂真值"的是 `file_poll.wakeup_dir`（平台真校验必须绝对路径）。
    """
    sys.path.insert(0, str(ROOT))
    from agent_community.platform import register_prompt as rp
    card = rp._minimal_json(method, hid)
    if method == "file_poll":
        out = Path(tempfile.gettempdir()) / "ac_matrix_inbox"
        out.mkdir(parents=True, exist_ok=True)
        card["wakeup_dir"] = str(out)
    return card


def _inside(path: str, root: Path) -> bool:
    """路径是否在 root 之下 —— **跨平台**判法（别自己做分隔符替换：Linux 上 `/` 会判错）。"""
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def run_one(p: dict, data_dir: Path) -> dict:
    method, hid = p["method"], "mx-" + p["method"].replace("_", "-")
    row = {"protocol": method, "cells": {}, "notes": []}
    needs_bridge = bool(p.get("bridge"))          # http_api / mcp 的平台口径：不需要桥（直推/直连）

    code, body = req("/api/harness/register", minimal_for(method, hid))
    row["cells"]["①登记"] = (code == 200, "HTTP %s%s" % (code, "" if code == 200 else " " + str(body.get("error") or body)[:90]))
    if code != 200:
        return row

    # ② 叫醒脚本
    cmd = "cmd /c echo wake" if os.name == "nt" else "echo wake"
    code, body = req("/api/harness/%s/wake" % hid, {"command": cmd})
    code2, body2 = req("/api/harness/%s/wake" % hid)
    wrapper = str((body2 or {}).get("wrapper_path") or "")
    in_data = _inside(wrapper, data_dir)
    ok2 = code == 200 and bool((body2 or {}).get("configured")) and in_data
    row["cells"]["②叫醒脚本"] = (ok2, "HTTP %s configured=%s 落点在数据目录=%s"
                              % (code, (body2 or {}).get("configured"), in_data))
    if not in_data and wrapper:
        row["notes"].append("叫醒脚本落点不在数据目录：%s" % wrapper)

    # ③ 具体桥模板（有桥的协议：正确 kind 通过 + 坏 kind 400 给 hint；没桥的协议：应如实说"不需要桥"）
    if needs_bridge:
        kind = p["bridge"]
        code, body = req("/api/harness/%s/bridge-template" % hid,
                         {"name": "矩阵模板", "kind": kind, "path": str(data_dir / "bridge.py")})
        code_bad, body_bad = req("/api/harness/%s/bridge-template" % hid,
                                 {"name": "矩阵坏模板", "kind": "no_such_kind", "path": "x"})
        ok3 = code == 200 and code_bad == 400 and bool((body_bad or {}).get("hint"))
        row["cells"]["③桥模板"] = (ok3, "kind=%s → HTTP %s ｜ 坏 kind → %s（hint=%s）"
                                  % (kind, code, code_bad, bool((body_bad or {}).get("hint"))))
    else:
        code, body = req("/api/harness/%s/bridge-template" % hid,
                         {"name": "矩阵模板", "kind": "file_poll", "path": "x"})
        ok3 = code in (200, 400)
        row["cells"]["③桥模板"] = (ok3, "不需要桥的协议：模板接口 HTTP %s（不适用；能创建也允许、拒绝也如实）" % code)

    # ④ 生成桥 + 自检（有桥：真生成到**临时数据目录**，别往仓库里写；没桥：平台应如实说不需要桥并给 hint）
    if needs_bridge:
        code, body = req("/api/harness/%s/bridge/generate" % hid,
                         {"template": p["bridge"], "out_dir": str(data_dir / "bridges" / hid)})
        bridge_file = str((body or {}).get("bridge_file") or (body or {}).get("path") or "")
        gen_ok = code == 200 and bool(bridge_file)
        if bridge_file and not _inside(bridge_file, data_dir):
            row["notes"].append("桥产物没落在数据目录（会污染仓库）：%s" % bridge_file)
            gen_ok = False
    else:
        code, body = req("/api/harness/%s/bridge/generate" % hid, {"template": "file_poll"})
        gen_ok = (code == 400 and "不需要架桥" in str((body or {}).get("error") or "")
                  and bool((body or {}).get("hint")))
        bridge_file = ""
    code2, body2 = req("/api/harness/bridge-verify", {"harness_id": hid})
    ver_ok = code2 in (200, 202)
    row["cells"]["④生成桥+自检"] = (bool(gen_ok and ver_ok),
                                 "generate HTTP %s（%s）｜ verify HTTP %s"
                                 % (code, bridge_file[-34:] if bridge_file else str((body or {}).get("error") or "")[:34], code2))

    # ⑤ 投递
    code, body = req("/api/harness/bridge-test", {"harness_id": hid})
    row["cells"]["⑤投递"] = (code == 200, "HTTP %s %s" % (code, str((body or {}).get("delivered") if isinstance(body, dict) else body)[:80]))
    return row


def main() -> int:
    data_dir = Path(tempfile.gettempdir()) / "ac_matrix_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "AC_PORT": str(PORT), "AC_DATA_DIR": str(data_dir),
           "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen([sys.executable, "-m", "agent_community.platform.server"],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            if req("/api/status", timeout=3)[0] == 200:
                break
            time.sleep(0.5)
        else:
            print("★实例没起来"); return 1

        print("=" * 96)
        print("核心矩阵：六种协议 × 桥 × 唤醒脚本 × 投递（实例 %s，数据目录 %s）" % (_base(), data_dir))
        print("=" * 96)
        rows = [run_one(p, data_dir) for p in protocols()]
        cols = ["①登记", "②叫醒脚本", "③桥模板", "④生成桥+自检", "⑤投递"]
        print("%-11s %-10s %s" % ("协议", "主桥", "  ".join("%-13s" % c for c in cols)))
        print("-" * 96)
        fails = 0
        for r in rows:
            cells = []
            for c in cols:
                ok, _ = r["cells"].get(c, (False, "未跑"))
                fails += 0 if ok else 1
                cells.append("%-13s" % ("OK" if ok else "★FAIL"))
            print("%-11s %-10s %s" % (r["protocol"], str(next((p["bridge"] for p in protocols() if p["method"] == r["protocol"]), "-")), "  ".join(cells)))
        print("-" * 96)
        for r in rows:
            for c in cols:
                ok, detail = r["cells"].get(c, (False, "未跑"))
                if not ok:
                    print("  ★ %-10s %-14s %s" % (r["protocol"], c, detail))
            for n in r["notes"]:
                print("  · %-10s %s" % (r["protocol"], n))
        print("=" * 96)
        print("结论：%s" % ("**六种协议五步全绿**" if not fails else "**有 %d 格失败**（见上）" % fails))
        return 0 if not fails else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                   # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
