#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**多协议选中之后到底能干什么**（V132，老板 2026-11-06「做多协议选中适合的功能」）。

真跑：起临时实例（换端口 + 换 `AC_DATA_DIR`），把"选一组协议 → 算计划 → 真注册"这条链**真走一遍**。

| # | 格 | 断言 |
|---|---|---|
| ① | 计划端点活着 | `POST /api/docking/protocol-plan`（a2a+mcp）⇒ 200 + `ok` |
| ② | MCP 不派活 | 路线里永远不出现 mcp；且有一条「工具面」说明（措辞不写死） |
| ③ | 只有 MCP | `dispatch_via=none` + `why` 非空 + `can_dispatch=false`（不假装能派活） |
| ④ | 合并必填 | `a2a+acp` ⇒ 同时要 `api_base_url` 与 `acp_command` |
| ⑤ | 缺项逐项说清 | 不给 `acp_command` ⇒ 只有 acp 那条 `usable=false`、`missing` 非空；a2a 仍可用 |
| ⑥ | 同层重复 | `http+http_api` ⇒ `duplicates` 里有一条 `same_channel` |
| ⑦ | 首选不被偷改 | 首选 `http` + 含 `a2a` ⇒ 派活走 a2a、`honest_note` 非空、`register_payload.wakeup_method` 仍是 `http` |
| ⑧ | 未知 id | 400 + `code=UNKNOWN_PROTOCOL` + `available` 列出可用值 |
| ⑨ | 空 ids | 400 + `code=BAD_PROTOCOLS`（不是返回空计划假装成功） |
| ⑩ | ★多选真注册（旧行为 400） | 按 `register_payload` 注册 `a2a+http_api` ⇒ 200 |
| ⑪ | 档案读回一致 | `GET /api/harness` 里这条的 `protocols` / `transports` 与计划一致 |
| ⑫ | 只读 | 调计划端点前后状态文件 sha256 不变 |
| ⑬ | **clipboard × 视觉脚本** | `clipboard` 开箱 `can_dispatch=false`，但计划标 `can_wake_with_script=true`，且**写明「配了视觉脚本能自动投递、但结果核不到」**（老板 2026-11-06：「clipboard 是不是可以用视觉脚本，记得说明」） |
| ⑭ | **能给组长投一段话** | `POST /api/docking/harness/{id}/send-text` 真跑：不存在的接入 404；带脚本但窗口找不到时**如实失败**（`ok=false` + 原因 + next），且返回里**明说「读不到它的回复」** |

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
    """把数据目录里几个关键状态文件的名字+sha 拼起来（只读）。"""
    parts = []
    for name in ("state.json", "harnesses.json", "workshops.json", "task_ledger.json"):
        p = d / name
        try:
            parts.append("%s:%s" % (name, hashlib.sha256(p.read_bytes()).hexdigest()[:12]))
        except FileNotFoundError:
            parts.append("%s:-" % name)
    return "|".join(parts)


FIELDS_OK = {"harness_id": "示例Harness-A", "api_base_url": "http://127.0.0.1:9/a2a/x",
             "acp_command": "示例命令 --noop", "ai": {"model_name": "示例模型"}}


def main() -> int:
    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_protocol_plan_")
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8"})
    srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("判据：多协议选中与能力适配（V132 · 真跑：临时实例 + 真注册）")
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

        ddir = Path(tmpdir)
        before = _sha_dir(ddir)

        st1, b1 = _http(base, "/api/docking/protocol-plan",
                        {"ids": ["a2a", "mcp"], "fields": FIELDS_OK})
        check("① 计划端点活着（a2a+mcp）", st1 == 200 and b1.get("ok") is True,
              "HTTP %s %s" % (st1, str(b1)[:180]))

        caps = (b1 or {}).get("capabilities") or {}
        route = ((b1 or {}).get("route") or {})
        warns = (b1 or {}).get("warnings") or []
        check("② MCP 不派活（路线里没有 mcp，且有「工具面」说明）",
              "mcp" not in str(route.get("dispatch_via") or "").lower()
              and any("MCP" in w and "工具面" in w for w in warns)
              and route.get("dispatch_via") == "a2a",
              "route=%r warns=%r" % (route.get("dispatch_via"), warns[:2]))

        st3, b3 = _http(base, "/api/docking/protocol-plan",
                        {"ids": ["mcp"], "fields": FIELDS_OK})
        r3 = (b3 or {}).get("route") or {}
        c3 = (b3 or {}).get("capabilities") or {}
        check("③ 只有 MCP ⇒ 如实说不能派活",
              st3 == 200 and r3.get("dispatch_via") == "none" and bool(str(r3.get("why") or "").strip())
              and c3.get("can_dispatch") is False,
              "HTTP %s route=%r why=%r" % (st3, r3.get("dispatch_via"), str(r3.get("why"))[:70]))

        st4, b4 = _http(base, "/api/docking/protocol-plan",
                        {"ids": ["a2a", "acp"], "fields": FIELDS_OK})
        merged = (b4 or {}).get("merged_required") or []
        check("④ 合并必填（a2a+acp 同时要 api_base_url 与 acp_command）",
              st4 == 200 and "api_base_url" in merged and "acp_command" in merged,
              "merged=%r" % merged)

        fields_missing = dict(FIELDS_OK)
        fields_missing.pop("acp_command", None)
        st5, b5 = _http(base, "/api/docking/protocol-plan",
                        {"ids": ["a2a", "acp"], "fields": fields_missing})
        by5 = {x.get("id"): x for x in ((b5 or {}).get("picked") or [])}
        acp5 = by5.get("acp") or {}
        a2a5 = by5.get("a2a") or {}
        check("⑤ 缺项逐项说清（acp 不可用、a2a 仍可用）",
              st5 == 200 and acp5.get("usable") is False
              and "acp_command" in (acp5.get("missing") or [])
              and a2a5.get("usable") is True,
              "acp=%r a2a=%r" % (acp5.get("missing"), a2a5.get("usable")))

        st6, b6 = _http(base, "/api/docking/protocol-plan",
                        {"ids": ["http", "http_api"], "fields": FIELDS_OK})
        dups = (b6 or {}).get("duplicates") or []
        check("⑥ 同层重复提醒（http + http_api）",
              st6 == 200 and any(d.get("kind") == "same_channel" for d in dups),
              "dups=%r" % dups[:2])

        st7, b7 = _http(base, "/api/docking/protocol-plan",
                        {"ids": ["http", "a2a"], "fields": FIELDS_OK})
        r7 = (b7 or {}).get("route") or {}
        pay7 = (b7 or {}).get("register_payload") or {}
        check("⑦ 首选不被偷改（派活走 a2a、载荷里首选仍是 http、且给了诚实提示）",
              st7 == 200 and r7.get("dispatch_via") == "a2a"
              and bool(str(r7.get("honest_note") or "").strip())
              and pay7.get("wakeup_method") == "http",
              "route=%r honest=%r payload=%r"
              % (r7.get("dispatch_via"), str(r7.get("honest_note"))[:60],
                 pay7.get("wakeup_method")))

        st8, b8 = _http(base, "/api/docking/protocol-plan", {"ids": ["a2a", "nope"]})
        check("⑧ 未知协议 ⇒ 400 + 列出可用值",
              st8 == 400 and b8.get("code") == "UNKNOWN_PROTOCOL" and b8.get("available"),
              "HTTP %s %s" % (st8, str(b8)[:140]))

        st9, b9 = _http(base, "/api/docking/protocol-plan", {"ids": []})
        check("⑨ 空 ids ⇒ 400（不返回空计划假装成功）",
              st9 == 400 and b9.get("code") == "BAD_PROTOCOLS",
              "HTTP %s %s" % (st9, str(b9)[:120]))

        # ⑫ 只读：采样点必须在**任何写操作**（⑩ 的真注册）之前
        after_plan = _sha_dir(ddir)
        check("⑫ 计划端点只读（调用前后状态文件 sha256 不变）",
              before == after_plan,
              "before=%s after=%s" % (before[:70], after_plan[:70]))

        # ⑬ clipboard × 视觉脚本：能自动投递，但只到"送进去了"
        st13, b13 = _http(base, "/api/docking/protocol-plan",
                          {"ids": ["clipboard"], "fields": FIELDS_OK})
        c13 = (b13 or {}).get("capabilities") or {}
        blob13 = json.dumps(b13, ensure_ascii=False)
        check("⑬ clipboard：配视觉脚本能自动投递，且写明「结果核不到」",
              st13 == 200 and c13.get("can_wake_with_script") is True
              and c13.get("can_dispatch") is False
              and "视觉脚本" in blob13
              and ("核不到" in blob13 or "结果仍要人" in blob13),
              "HTTP %s caps=%r 文本片段=%r"
              % (st13, c13, blob13[:220]))

        # ⑭ send-text：把任意一段话（对接单/通知）送进窗口 —— 只到"送进去了"，且明说读不到回复
        #   ⚠️ 路径里的 harness_id 用 ASCII：urllib 不会替我们做 URL 编码（中文会 UnicodeEncodeError），
        #      浏览器/前端会自动编码，所以这只是判据工具的写法问题，不是接口问题（实测踩过）。
        st_nf, b_nf = _http(base, "/api/docking/harness/no-such-harness/send-text", {"text": "x"})
        _http(base, "/api/harness/register", {
            "harness_id": "visual-check-demo", "harness_name": "视觉判据-示例",
            "wakeup_method": "clipboard", "protocols": ["clipboard"],
            "activation_script": {"text": "点输入框粘一句",
                                  "title": "示例窗口-不存在的标题-zzz"},
            "ai": {"model_name": "示例模型"}})
        st_send, b_send = _http(base, "/api/docking/harness/visual-check-demo/send-text",
                                {"text": "对接单：岗位 前端组/员工01（判据用示例）"})
        _note = str((b_send or {}).get("note") or "")
        _honest = (st_send == 200 and (_note and "读不到" in _note)
                   and ((b_send or {}).get("ok") is not True
                        or (b_send or {}).get("delivered") is True))
        _fail_says_why = ((b_send or {}).get("ok") is True
                          or bool(str((b_send or {}).get("error") or
                                      (b_send or {}).get("detail") or "").strip()))
        check("⑭ 能给组长投一段话（send-text）：404 说清 / 找不到窗口如实失败 / 明说读不到回复",
              st_nf == 404 and (b_nf or {}).get("code") == "NO_SUCH_HARNESS"
              and _honest and _fail_says_why,
              "404=%s send=%s note=%r err=%r"
              % (st_nf, st_send, _note[:60], str((b_send or {}).get("error"))[:80]))

        # ⑩ 真注册：用计划给的载荷（旧行为：transports 含 http_api ⇒ 400）
        #   ⚠️ 用 a2a+http_api 这组（`wakeup_method` 是 a2a）：若拿"首选 http"那组去注册，
        #   会撞上既有语义「wakeup_method=http 必须有拉起命令」，那与本判据无关（实测踩过）。
        _stp, b_pay = _http(base, "/api/docking/protocol-plan",
                            {"ids": ["a2a", "http_api"], "fields": FIELDS_OK})
        payload = dict((b_pay or {}).get("register_payload") or {})
        st10, b10 = (0, {})
        if payload:
            reg = dict(payload)
            reg.update({"harness_id": "多选判据-示例", "harness_name": "多选判据-示例",
                        "api_base_url": "http://127.0.0.1:9/a2a/x",
                        "ai": {"model_name": "示例模型"}})
            st10, b10 = _http(base, "/api/harness/register", reg)
        check("⑩ ★多选真注册成功（a2a+http_api，旧行为是 400）",
              st10 == 200 and (b10.get("success") is True or b10.get("ok") is True),
              "HTTP %s %s" % (st10, str(b10)[:200]))

        # ⑪ 档案读回一致：直接读**平台自己落盘的** `harnesses.json`（真文件，比问接口更硬）
        st11, b11 = 200, {}
        mine = {}
        hp = Path(tmpdir) / "harnesses.json"
        try:
            all_h = json.loads(hp.read_text(encoding="utf-8") or "{}")
            mine = (all_h.get("多选判据-示例") or all_h.get("harnesses", {}).get("多选判据-示例")
                    or {}) if isinstance(all_h, dict) else {}
        except Exception as e:                                 # noqa: BLE001 — 读不到就如实报（判据要看真文件）
            st11, b11 = 0, {"error": "%s: %s" % (type(e).__name__, e)}
        got_protocols = [str(x) for x in (mine.get("protocols") or [])]
        got_t = [str(getattr(x, "value", x)) for x in (mine.get("transports") or [])]
        check("⑪ 档案读回与计划一致（读平台落盘的 harnesses.json）",
              st11 == 200 and got_protocols == ["a2a", "http_api"] and got_t == ["a2a", "http"],
              "protocols=%r transports=%r err=%r" % (got_protocols, got_t, str(b11)[:120]))
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


if __name__ == "__main__":
    raise SystemExit(main())
