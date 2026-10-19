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


def _free_port() -> int:
    """拿一个**空闲端口**（避免固定端口在整套回归里和别的用例撞车——矩阵在套件里假红过一次）。"""
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


PORT = int(os.environ.get("AC_MATRIX_PORT") or 0) or _free_port()
#: 本次运行的数据目录（main() 里赋值；`minimal_for` / 闭环都从这里派生 inbox）
DATA_DIR = Path(tempfile.gettempdir()) / "ac_matrix_data"


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
        out = DATA_DIR / "file_poll_inbox"      # ★放数据目录下（别和别的用例共用 /tmp 固定目录）
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
    # ★2026-10-19（核心体检偶发假红抓到）：verify 的 timeout 必须放宽。
    #   该端点内部会**同步扫进程表**（`_find_bridge_processes`）再等一次真实回报（默认 10s），
    #   在负载高时合计会超过 20s ⇒ 矩阵侧 urllib 超时（HTTP 0）⇒ 同一份代码"跑一次红、跑一次绿"。
    #   判据不稳定比没有判据更糟，所以这里给足时间（120s）。
    code2, body2 = req("/api/harness/bridge-verify", {"harness_id": hid}, timeout=120.0)
    if needs_bridge:
        ver_ok = code2 in (200, 202)
    else:
        # 不需要桥的协议：桥本来就不存在，verify 只是**附带自检**；
        # 允许 200/202/400，也允许"连不上"（0），但必须把这件事记进 notes（不掩盖）。
        ver_ok = code2 in (200, 202, 400)
        if code2 == 0:
            row["notes"].append("verify 未应答（本协议不需要桥，此步仅附带自检）：%s"
                                % str((body2 or {}).get("error") or "")[:80])
    row["cells"]["④生成桥+自检"] = (bool(gen_ok and ver_ok),
                                 "generate HTTP %s（%s）｜ verify HTTP %s"
                                 % (code, bridge_file[-34:] if bridge_file else str((body or {}).get("error") or "")[:34], code2))

    # ⑤ 投递
    code, body = req("/api/harness/bridge-test", {"harness_id": hid})
    row["cells"]["⑤投递"] = (code == 200, "HTTP %s %s" % (code, str((body or {}).get("delivered") if isinstance(body, dict) else body)[:80]))
    return row


def closure_file_poll(data_dir: Path, hid: str = "mx-file-poll", inbox: Path | None = None) -> dict:
    """**第 ⑥ 步：闭环消费**（只对 file_poll 做——它是唯一能在本机"真跑起来"的桥）。

    这一段验的是"**送到 ≠ 通了**"之后的那半段：
      ⑥a 平台派活 ⇒ inbox 里出现 `task_*.json`（真落盘）
      ⑥b **真起桥**（子进程）⇒ 任务被搬进 `delivered/`（桥真的消费了，不是"我发出去了"）
      ⑥c agent 侧写一份 `replies/reply_*.json` ⇒ 桥转发到 `/api/harness/task-result` ⇒ 平台真收到（回 200）
      ⑥d 再起第二只桥 ⇒ **被单实例锁拒绝**（exit 2 + 明确说明，防"两只桥抢件"）

    ⚠️ 唯一被"模拟"的是 **agent 自己写回执文件**那一下 —— 那是外部 agent 的活，平台替代不了；
    其余四段（派活/落盘/搬件/转发/入库）都是平台与桥真跑。
    """
    res = {"cells": {}, "notes": []}
    # ★必须用**该 harness 注册时那个 inbox**（平台把任务写到 `wakeup_dir`）——
    #   我第一版另起了一个 closure_inbox，于是"派活写左边、桥盯右边"⇒ ⑥a/⑥b 全红（自己的锅）。
    root = data_dir / "file_poll_inbox"
    inbox = inbox or root
    # 清干净再跑：否则上一轮的任务文件会混进来（我第一次跑就看到 31 个 task_*.json）
    if inbox.exists():
        for f in list(inbox.glob("task_*.json")):
            try:
                f.unlink()
            except OSError:
                pass
    bridge = data_dir / "bridges" / hid / "bridge.py"
    replies = inbox / "replies"
    delivered = inbox / "delivered"
    replies.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "bridge_closure.log"

    # ⑥a 派活
    code, body = req("/api/harness/bridge-test", {"harness_id": hid})
    tasks = sorted(inbox.glob("task_*.json"))
    res["cells"]["⑥a 派活落盘"] = (code == 200 and bool(tasks),
                                "HTTP %s ｜ inbox 里 task_*.json = %d" % (code, len(tasks)))

    # ⑥b 起桥真消费
    env = child_env()                 # 白名单（桥也要连同一个实例，别被外头的 AC_* 带偏）
    logf = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([sys.executable, "-u", str(bridge), "--url", _base(),
                             "--inbox", str(inbox), "--interval", "0.3"],
                            cwd=str(ROOT), env=env, stdout=logf,
                            stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 40
        consumed = False
        while time.time() < deadline:
            if delivered.exists() and list(delivered.glob("*task_*.json")):
                consumed = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.4)
        out_so_far = ""
        res["cells"]["⑥b 桥真消费"] = (consumed,
                                   "delivered/ 里收到搬走的任务 = %s" % consumed)

        # ⑥c agent 写回执 ⇒ 桥转发 ⇒ 平台收到
        reply = replies / "reply_matrix_1.json"
        reply.write_text(json.dumps({
            "type": "task", "harness_id": hid, "ok": True,
            "result": "矩阵闭环测试：agent 已处理", "from": "matrix",
        }, ensure_ascii=False), encoding="utf-8")
        forwarded = False
        deadline = time.time() + 40
        while time.time() < deadline:
            sent_dir = replies / "sent"
            failed_dir = replies / "failed"
            if ((sent_dir.exists() and list(sent_dir.glob("*reply_matrix_1.json")))
                    or (failed_dir.exists() and list(failed_dir.glob("*reply_matrix_1.json")))):
                forwarded = bool(sent_dir.exists() and list(sent_dir.glob("*reply_matrix_1.json")))
                break
            if proc.poll() is not None:
                break
            time.sleep(0.4)
        res["cells"]["⑥c 回报转发"] = (forwarded, "replies/sent/ 里出现已转发回执 = %s" % forwarded)
        if not forwarded:
            res["notes"].append("桥日志：%s" % log_path)

        # ⑥d 单实例锁
        second = subprocess.run([sys.executable, "-u", str(bridge), "--url", _base(),
                                 "--inbox", str(inbox), "--interval", "0.3"],
                                cwd=str(ROOT), env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=40)
        refused = second.returncode == 2 and "拒绝重复启动" in (second.stdout + second.stderr)
        res["cells"]["⑥d 单实例锁"] = (refused, "第二只桥 exit=%s（应 2）并说明" % second.returncode)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:                                   # noqa: BLE001
            proc.kill()
        try:
            logf.close()
        except Exception:                                   # noqa: BLE001
            pass
    if not res["cells"].get("⑥c 回报转发", (False,))[0]:
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
            res["notes"].append("桥日志尾：\n      " + "\n      ".join(tail))
        except OSError:
            pass
    return res


def child_env(**over) -> dict:
    """给子进程构造环境：**白名单**，不继承外头的 `AC_*`。

    ★2026-10-18 修（在整套回归里假红过一次）：矩阵单跑全绿、进了 pytest 就"★实例没起来"。
    病灶：Pytest 里有些老用例直接改 `os.environ`（`AC_ENV=production` / `AC_TOKEN*` 之类）没有还原，
    我这个工具是**子进程**，全盘继承 ⇒ 平台服务按"生产模式缺 Token"直接启动失败。
    ⇒ 现在只带系统必需项 + 本工具自己那三个，其余 `AC_*` 一律丢掉。
    """
    keep_prefix = ("PATH", "PYTHON", "SYSTEM", "WINDIR", "TEMP", "TMP", "HOME", "USER",
                   "APPDATA", "LOCALAPPDATA", "COMSPEC", "OS", "PATHEXT", "LANG", "LC_")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AC_") and (k.upper().startswith(keep_prefix) or k in ("LD_LIBRARY_PATH",))}
    env.update({"AC_PORT": str(PORT), "AC_DATA_DIR": str(DATA_DIR), "PYTHONPATH": str(ROOT)})
    env.update({k: str(v) for k, v in over.items()})
    return env


def main() -> int:
    global DATA_DIR
    # 每次跑一个**独立数据目录**（别复用上一轮：残留的桥锁/inbox 会让闭环假红）
    data_dir = Path(tempfile.mkdtemp(prefix="ac_matrix_data_"))
    DATA_DIR = data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    env = child_env()                 # 白名单（见 child_env 注释：不继承外头的 AC_*）
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

        # ⑥ 闭环消费（只对 file_poll：本机唯一能"真跑起来"的桥）
        print("-" * 96)
        print("⑥ 闭环消费（file_poll 真起桥）：派活 → 搬件 → agent 回执 → 桥转发 → 单实例锁")
        clo = closure_file_poll(data_dir)
        clo_fails = 0
        for name, (ok, detail) in clo["cells"].items():
            print("   %-14s %-6s %s" % (name, "OK" if ok else "★FAIL", detail))
            clo_fails += 0 if ok else 1
        fails += clo_fails

        print("=" * 96)
        if fails:
            print("结论：**有 %d 格失败**（见上）" % fails)
        else:
            print("结论：**六种协议五步全绿 + 闭环消费四段全绿**")
        return 0 if not fails else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:                                   # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
