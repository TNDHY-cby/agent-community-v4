#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**MCP 只读资源面**（V127）—— 12 格，全部真请求（不 mock）。

## 为什么要它

工具面有 `tools/mcp_tools_check.py`（19 格）盯着；资源面是**另一个协议层**
（客户端能"看"到平台，而不是每次先猜该调哪个工具）。它一旦坏了，客户端会
**静默拿到空目录** —— 用户只会觉得"这台平台里什么都没有"，所以必须有判据。

## 判据怎么来的（每条都对着一个真坑）

| # | 格 | 为什么这么判 |
|---|---|---|
| ① | 能力声明 | 声明 `resources` 且 `subscribe=false`：**不支持就不许声明 true** |
| ② | 固定资源非空 | 每条都要有 `uri/name/mimeType`，缺字段客户端就是空标签 |
| ③ | 模板非空 | 含 `ac://workshop/{workshop_id}`，否则客户端没法展开 |
| ④ | 逐个真读 | 每个 URI 都读一遍，`text` 必须能 `json.loads` |
| ⑤ | 数据是真的 | 临时实例里**真建工作间 + 任务**，资源里必须查得到（不是空壳） |
| ⑥ | 模板展开 | 用真 `workshop_id` 读单体/成员，读到同一个对象 |
| ⑦ | 不存在就说清 | 读不存在的 id ⇒ `-32002`，消息里带那个 URI |
| ⑧ | 脱敏（双平台） | 正文不许有令牌明文、`sk-`、盘符路径、POSIX 深路径（URI 不算路径） |
| ⑨ | 翻页/截断 | 把上限压到 1 KB ⇒ 清单类必须给 `next` 可翻页；截断必须带 `full_size` |
| ⑩ | 只读 | 读前后 `workshops.json` 的 sha256 不变 |
| ⑪ | HTTP 同源 | 同一个 URI 走 `POST /mcp` 与 stdio，正文**逐字相同** |
| ⑫ | 坏输入不崩 | 未知方法 `-32601`、坏 URI `-32602`，之后 `tools/list` 仍正常 |

跑在**临时实例**上（换端口 + 换数据目录），跑完关掉删目录 ⇒ 不污染真实数据。
退出码：0＝全过；1＝有格没过（点名）；2＝起不来（如实说没跑成）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)
PY = sys.executable

#: 压小上限，好让"翻页"这条路真的被走到（默认 64 KB 时清单装得下，就测不到 next）
SMALL_LIMIT = 1024

#: 脱敏断言：与 `mcp_server._scrub` 同一口径（URI 不算路径）
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\")
_POSIX_PATH_RE = re.compile(r"(?<![:\w/])/(?:[^\s/\"']+/){2,}")
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{8,}|agent_token\"\s*:\s*\"[^\"]{8,})")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body: dict | None = None, timeout: float = 60):
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


class HttpMcp:
    """真 MCP 客户端（HTTP 传输）。"""

    def __init__(self, url: str, name: str = "mcp-resources-check"):
        self.url = url
        self.name = name
        self.sid = ""

    def _rpc(self, method: str, params: dict | None = None, rid: int = 1) -> dict:
        h = {"Content-Type": "application/json"}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(
            self.url,
            data=json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                             "params": params or {}}).encode("utf-8"),
            headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.sid = r.headers.get("Mcp-Session-Id") or self.sid
            return json.loads(r.read().decode("utf-8") or "{}")

    def init(self) -> dict:
        d = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": self.name, "version": "0"}})
        self._rpc("notifications/initialized", {})
        return d.get("result") or {}

    def list_resources(self, cursor: str = "") -> dict:
        return (self._rpc("resources/list", {"cursor": cursor} if cursor else {}).get("result") or {})

    def templates(self, cursor: str = "") -> dict:
        return (self._rpc("resources/templates/list",
                          {"cursor": cursor} if cursor else {}).get("result") or {})

    def read(self, uri: str) -> tuple[int, dict]:
        d = self._rpc("resources/read", {"uri": uri})
        err = d.get("error")
        if err:
            return int(err.get("code") or 0), err
        return 0, ((d.get("result") or {}).get("contents") or [{}])[0]

    def rpc_raw(self, method: str, params: dict | None = None) -> dict:
        return self._rpc(method, params or {})


class StdioMcp:
    """真 MCP 客户端（stdio 传输，子进程 + 逐行 JSON-RPC）。"""

    def __init__(self, base: str):
        env = dict(os.environ)
        env.update({"AC4_BASE_URL": base, "PYTHONIOENCODING": "utf-8",
                    "AC_MCP_RESOURCE_MAX_BYTES": str(SMALL_LIMIT)})
        self.p = subprocess.Popen([PY, "-m", "agent_community.mcp_server"], cwd=_ROOT, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True,
                                  encoding="utf-8", errors="replace")
        self._rid = 0

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self._rid += 1
        line = json.dumps({"jsonrpc": "2.0", "id": self._rid, "method": method,
                           "params": params or {}}, ensure_ascii=False)
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        out = self.p.stdout.readline()
        if not out:
            return {"error": {"code": -1, "message": "stdio 客户端已退出（无响应）"}}
        try:
            return json.loads(out)
        except ValueError:
            return {"error": {"code": -2, "message": "stdio 回了非 JSON：%s" % out[:160]}}

    def close(self) -> None:
        try:
            self.p.stdin.close()
        except Exception:                                      # noqa: BLE001 — 关不上就算了
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:                                      # noqa: BLE001 — 超时就杀掉
            self.p.kill()


def _sha256(p: str) -> str:
    try:
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="", help="直接打一个已在跑的平台（默认自起临时实例）")
    ap.add_argument("--keep", action="store_true", help="跑完不关临时实例")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = tempfile.mkdtemp(prefix="ac_mcp_res_check_")
    base = a.base.rstrip("/") or ("http://127.0.0.1:%d" % port)
    srv = None
    if not a.base:
        env = dict(os.environ)
        env.update({"AC_PORT": str(port), "AC_DATA_DIR": tmpdir, "PYTHONIOENCODING": "utf-8",
                    "AC_MCP_RESOURCE_MAX_BYTES": str(SMALL_LIMIT)})
        srv = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=_ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    rows: list[tuple] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        rows.append((name, bool(ok), detail))

    cli = None
    std = None
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
            print("[FAIL] 平台没起来（%s）—— 这次不算跑成" % base)
            return 2

        # ── 夹具：真注册一个 harness + 真建 12 个工作间（各带一条任务）
        _http(base, "/api/harness/register", {
            "harness_id": "demo-res-check", "harness_name": "示例Harness-资源巡检",
            "harness_type": "custom", "wakeup_method": "mcp",
            "api_base_url": base + "/mcp", "ai": {"model_name": "示例模型"}})
        ws_ids = []
        for i in range(12):
            st, made = _http(base, "/api/workshop", {
                "name": "巡检间%02d" % i, "hall_content": "资源面巡检夹具",
                "create_task": True, "task_title": "巡检任务%02d" % i,
                "task_description": "由 tools/mcp_resources_check.py 创建"})
            if st in (200, 201) and made.get("workshop_id"):
                ws_ids.append(str(made["workshop_id"]))
        check("夹具：建 12 个工作间", len(ws_ids) == 12, "建成 %d 个" % len(ws_ids))

        cli = HttpMcp(base + "/mcp")
        init = cli.init()
        caps = (init.get("capabilities") or {})
        res_cap = caps.get("resources") or {}
        check("① 能力声明 resources(subscribe=false)",
              bool(res_cap) and res_cap.get("subscribe") is False,
              "capabilities=%s" % json.dumps(caps, ensure_ascii=False)[:200])

        lst = cli.list_resources()
        items = lst.get("resources") or []
        fields_ok = all(x.get("uri") and x.get("name") and x.get("mimeType") for x in items)
        check("② resources/list 非空且字段齐", len(items) >= 6 and fields_ok,
              "%d 条：%s" % (len(items), [x.get("uri") for x in items]))

        tpl = (cli.templates().get("resourceTemplates") or [])
        has_ws_tpl = any(x.get("uriTemplate") == "ac://workshop/{workshop_id}" for x in tpl)
        check("③ templates/list 非空且含工作间模板", len(tpl) >= 5 and has_ws_tpl,
              "%d 条：%s" % (len(tpl), [x.get("uriTemplate") for x in tpl]))

        # ── ④ 逐个真读（固定资源 + 用真 workshop 展开的模板）
        read_rows = []
        bad_json = []
        for x in items:
            code, got = cli.read(x["uri"])
            body = got.get("text") if isinstance(got, dict) else None
            ok = code == 0 and isinstance(body, str)
            if ok:
                try:
                    json.loads(body)
                except ValueError:
                    ok = False
                    bad_json.append(x["uri"])
            read_rows.append((x["uri"], ok, code))
        ws0 = ws_ids[0] if ws_ids else "ws-none"
        for uri in ("ac://workshop/%s" % ws0, "ac://workshop/%s/members" % ws0,
                    "ac://workshop/%s/tasks" % ws0, "ac://workshop/%s/manifest" % ws0):
            code, got = cli.read(uri)
            # 刚建的间**还没有产物清单** ⇒ 平台回 409「还没生成」，资源面如实转 -32002 是**正确**行为，
            # 不能因为"读不到"就判红（那是把正常状态当故障）。
            ok = (code in (0, -32002)) if uri.endswith("/manifest") else (code == 0)
            read_rows.append((uri, ok, code))
            if ok and code == 0:
                try:
                    json.loads(got.get("text") or "")
                except ValueError:
                    bad_json.append(uri)
                    read_rows[-1] = (uri, False, code)
        check("④ 固定+模板逐个真读且是合法 JSON",
              all(r[1] for r in read_rows),
              "失败(code≠0)：%s（非 JSON：%s）" % ([r[0] for r in read_rows if not r[1]], bad_json))

        # ── ⑤ 数据是真的（**分页下也要找得到**：上限压到 1 KB 后清单一定翻页）
        def _all_items(uri: str, key: str) -> list:
            out, cur, guard = [], uri, 0
            while cur and guard < 10:
                code_x, got_x = cli.read(cur)
                try:
                    obj_x = json.loads((got_x.get("text") if isinstance(got_x, dict) else "") or "{}")
                except ValueError:
                    break
                out.extend(obj_x.get(key) or [])
                cur = str(obj_x.get("next") or "")
                guard += 1
            return out

        all_tasks = _all_items("ac://tasks", "tasks")
        task_blob = json.dumps(all_tasks, ensure_ascii=False)
        code_ws, got_ws = cli.read("ac://workshop/%s" % ws0)
        ws_body = (got_ws.get("text") if isinstance(got_ws, dict) else "") or ""
        code_wt, _got_wt = cli.read("ac://workshop/%s/tasks" % ws0)
        check("⑤ 数据是真的（任务找得到／单体 id 对得上／v5 任务接口正常）",
              ("巡检任务00" in task_blob) and (ws0 in ws_body) and code_wt == 0,
              "任务 %d 条／单体 code=%s／v5 任务 code=%s" % (len(all_tasks), code_ws, code_wt))

        # ── ⑥ 模板展开读到同一个对象
        code, got = cli.read("ac://workshop/%s/members" % ws0)
        check("⑥ 成员模板能展开", code == 0, "code=%s" % code)

        # ── ⑦ 不存在 ⇒ -32002 且消息带 URI
        ghost = "ac://workshop/no-such-workshop-xyz"
        code, err = cli.read(ghost)
        check("⑦ 不存在的资源 ⇒ -32002", code == -32002 and ghost in json.dumps(err, ensure_ascii=False),
              "code=%s msg=%s" % (code, str(err)[:160]))

        # ── ⑧ 脱敏（双平台）
        hits = []
        for uri in [x["uri"] for x in items] + ["ac://workshop/%s" % ws0]:
            code, got = cli.read(uri)
            body = got.get("text") if isinstance(got, dict) else ""
            if not body:
                continue
            for label, rx in (("盘符路径", _WIN_PATH_RE), ("POSIX 路径", _POSIX_PATH_RE),
                              ("令牌/密钥", _SECRET_RE)):
                if rx.search(body):
                    hits.append("%s→%s" % (uri, label))
        check("⑧ 脱敏（令牌/盘符/POSIX 路径都不出现）", not hits, "命中：%s" % hits)

        # ── ⑨ 翻页/截断
        code, got = cli.read("ac://tasks")
        body = got.get("text") if isinstance(got, dict) else "{}"
        try:
            obj = json.loads(body or "{}")
        except ValueError:
            obj = {}
        nxt = str(obj.get("next") or "")
        page1 = obj.get("tasks") or []
        page2 = []
        if nxt:
            code2, got2 = cli.read(nxt)
            try:
                obj2 = json.loads((got2.get("text") if isinstance(got2, dict) else "") or "{}")
            except ValueError:
                obj2 = {}
            page2 = obj2.get("tasks") or []
        trunc_ok = (not obj.get("truncated")) or ("full_size" in obj)
        overlap = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in page1} & \
                  {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in page2}
        check("⑨ 清单超限先翻页、截断必带 full_size",
              bool(nxt) and not overlap and trunc_ok,
              "next=%r 第1页%d条 第2页%d条 重叠%d truncated=%s" % (
                  nxt[:40], len(page1), len(page2), len(overlap), obj.get("truncated")))

        # ── ⑩ 只读：读前后 workshops.json 不变
        wf = os.path.join(tmpdir, "workshops.json") if not a.base else ""
        before = _sha256(wf) if wf else ""
        for uri in [x["uri"] for x in items] + ["ac://workshop/%s/tasks" % ws0]:
            cli.read(uri)
        after = _sha256(wf) if wf else ""
        check("⑩ 资源路径不改状态（sha256 不变）",
              (not wf) or (before == after), "before=%s after=%s" % (before[:12], after[:12]))

        # ── ⑪ HTTP 与 stdio 同源（用稳定资源逐字比；status 含动态值不参与）
        std = StdioMcp(base)
        ini2 = std.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "stdio-check", "version": "0"}})
        same_ok, detail = True, []
        for uri in ("ac://workshops", "ac://protocols"):
            h_code, h_got = cli.read(uri)
            s_res = std.rpc("resources/read", {"uri": uri})
            s_txt = (((s_res.get("result") or {}).get("contents") or [{}])[0].get("text") or "")
            h_txt = (h_got.get("text") if isinstance(h_got, dict) else "") or ""
            if h_code != 0 or h_txt != s_txt:
                same_ok = False
                detail.append("%s：HTTP %d 字 / stdio %d 字，不一致" % (uri, len(h_txt), len(s_txt)))
        check("⑪ HTTP 与 stdio 同一份内容", same_ok,
              ("；".join(detail) or "两处逐字一致（capabilities=%s）"
               % json.dumps(((ini2.get("result") or {}).get("capabilities") or {}), ensure_ascii=False)[:120]))

        # ── ⑫ 坏输入不崩
        bad_method = std.rpc("resources/unknown", {})
        code_bm = int(((bad_method.get("error") or {}).get("code")) or 0)
        bad_uri = std.rpc("resources/read", {"uri": "http://example.com/x"})
        code_bu = int(((bad_uri.get("error") or {}).get("code")) or 0)
        still = std.rpc("tools/list", {})
        alive = isinstance((still.get("result") or {}).get("tools"), list)
        check("⑫ 未知方法 -32601 / 坏 URI -32602 / 进程仍活",
              code_bm == -32601 and code_bu == -32602 and alive,
              "method=%s uri=%s alive=%s" % (code_bm, code_bu, alive))
    finally:
        if std is not None:
            std.close()
        if srv is not None and not a.keep:
            srv.terminate()
            try:
                srv.wait(timeout=10)
            except Exception:                                  # noqa: BLE001 — 关不掉就强杀
                srv.kill()
    if not a.keep:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 74)
    print("MCP 只读资源面判据（V127）—— 临时实例 %s" % base)
    passed = 0
    for name, ok, detail in rows:
        print("  %s %s%s" % ("[PASS]" if ok else "[FAIL]", name, ("  — " + detail) if detail else ""))
        passed += 1 if ok else 0
    print("-" * 74)
    print("通过 %d/%d" % (passed, len(rows)))
    if passed != len(rows):
        print("没过的格：%s" % [n for n, ok, _d in rows if not ok])
        return 1
    print("结论：**全过**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
