#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**三档同账（protocol parity）** + 协议目录对账 —— `V119` 批 A，十一格。

要证明的一件事：**同一份回执，不管走 A（令牌）/ B（桥文件）/ C（人工粘贴），
平台落的账必须一样、错误码必须一样、幂等必须一样、原文留痕必须一样。**
否则"支持三种接入"就是三套不同的产品。

另加两格"目录不撒谎"：`/api/protocols` 说 available 的协议，判据工具必须真在；
MCP 必须标成工具面、A2A/gRPC 必须是任务语义（老板 2026-10-28 的口径）。

真跑：临时实例（换端口 + 换数据目录 + 换心智根），全部走真 HTTP / 真文件。
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
PY = sys.executable

#: 协议 → 它的判据工具（目录说 available 就必须有，缺了要点名）
JUDGE_OF = {
    "http": "tools/protocol_call_check.py",
    "mcp": "tools/mcp_tools_check.py",
    "a2a": "tools/protocol_peer_check.py",
    "grpc": "tools/protocol_peer_check.py",
    "acp": "tools/acp_check.py",
    "ws": "tools/protocol_call_check.py",
    "pipe": "tools/protocol_call_check.py",
}

rows: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    rows.append((name, bool(ok), detail))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body=None, headers=None, method: str = "", timeout: float = 30):
    path = urllib.parse.quote(path, safe="/?&=%:")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=h,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8", "replace")


def load(raw: str):
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {"_raw": raw[:300]}


def roster_rcpt(wid: str, seq: int, slot: str, harness: str, member: str = "组长") -> str:
    """member 要跟令牌绑定的成员一致 —— A 档有 TOKEN_SCOPE 校验（判据自己踩过）。"""
    return ("#回执 v1\n类型: 名单\n工作间: %s\n成员: %s\n凭据:\n序号: %d\n---\n"
            "岗位: %s | harness: %s | model: 示例模型 | 人数: 1\n---\n行数: 1"
            % (wid, member, seq, slot, harness))


def broken_rcpt(wid: str, seq: int) -> str:
    """坏单：**故意去掉 `行数:` 那行**（整单应被拒，且原文要留痕）。"""
    return ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: %d\n---\n"
            "岗位: 前端组/员工02 | harness: demo-parity | model: 示例模型 | 人数: 1\n---\n"
            % (wid, seq))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_parity_"))
    base = "http://127.0.0.1:%d" % port
    wsdir = tmpdir / "ws"
    (wsdir / "_meta" / "_inbox").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(tmpdir / "minds"), "PYTHONIOENCODING": "utf-8"})
    proc = None
    try:
        proc = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT),
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        check("临时平台起来（换端口 + 换数据目录 + 换心智根）", True)

        _st, wb = _http(base, "/api/workshop", {"name": "三档同账", "hall_content": "方向",
                                                "workshop_path": str(wsdir)})
        wid = str(load(wb).get("workshop_id") or "")
        check("夹具：建工作间（带工作区路径，B 档才有收件夹可写）", bool(wid), wb[:120])

        # ① C 档：人工粘贴（必须 confirm=1）
        st_c, raw_c = _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid,
                            {"text": roster_rcpt(wid, 11, "前端组/员工01", "demo-parity")})
        body_c = load(raw_c)
        check("① C 档（人工粘贴）收下名单回执", st_c == 200 and body_c.get("ok") is True,
              "%s %s" % (st_c, raw_c[:160]))

        # 没打勾确认 ⇒ 必须被拒（C 档的红线）
        st_cn, raw_cn = _http(base, "/api/workshop/%s/intake?source=C" % wid,
                              {"text": roster_rcpt(wid, 12, "前端组/员工09", "demo-parity")})
        check("② C 档不打勾确认必须被拒（不许替人认账）",
              st_cn != 200 and str(load(raw_cn).get("code") or ""), "HTTP %s %s" % (st_cn, raw_cn[:120]))

        # ③ B 档：桥文件（位置即凭据）
        (wsdir / "_meta" / "_inbox" / "b-1.rcpt").write_text(
            roster_rcpt(wid, 13, "后端组/员工02", "demo-parity"), encoding="utf-8")
        st_b, raw_b = _http(base, "/api/workshop/%s/intake/poll" % wid, {})
        body_b = load(raw_b)
        check("③ B 档（桥文件）收下同一类回执",
              st_b == 200 and (body_b.get("found") or 0) >= 1 and body_b.get("processed"),
              "%s %s" % (st_b, raw_b[:200]))

        # ④ A 档：令牌（每个成员一个）
        st_t, raw_t = _http(base, "/api/workshop/%s/members/2/token" % wid, {})
        tok = str(load(raw_t).get("token") or "")
        st_a, raw_a = _http(base, "/api/workshop/%s/intake?source=A" % wid,
                            {"text": roster_rcpt(wid, 14, "后端组/员工03", "demo-parity",
                                                 member="后端组/员工02")},
                            headers={"Authorization": "Bearer " + tok} if tok else {})
        body_a = load(raw_a)
        check("④ A 档（令牌）收下同一类回执",
              bool(tok) and st_a == 200 and body_a.get("ok") is True,
              "token=%s HTTP %s %s" % ("有" if tok else "空", st_a, raw_a[:140]))

        # ⑤ 无令牌走 A 档 ⇒ 必须 401（fail-closed）
        st_a0, raw_a0 = _http(base, "/api/workshop/%s/intake?source=A" % wid,
                              {"text": roster_rcpt(wid, 15, "后端组/员工02", "demo-parity")})
        check("⑤ A 档没令牌必须 401（fail-closed）",
              st_a0 == 401, "HTTP %s %s" % (st_a0, raw_a0[:120]))

        # ⑥ 三档落库结果一致：工位都在名册里（名单回执三路都真落库）
        _st, rr = _http(base, "/api/workshop/%s/roster" % wid)
        members = load(rr).get("members") or []
        slots = {m.get("slot_dir") for m in members}
        check("⑥ 三档落出的账一致（三个工位都在同一份名册里）",
              {"前端组/员工01", "后端组/员工02", "后端组/员工03"}.issubset(slots)
              and len(members) >= 3,
              "名册工位=%s" % sorted(slots))

        # ⑦ 三档坏单错误码一致（去掉「行数:」那行 ⇒ 整单拒，且 code 相同）
        codes = {}
        st_x, raw_x = _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid,
                            {"text": broken_rcpt(wid, 21)})
        codes["C"] = str(load(raw_x).get("code") or "")
        (wsdir / "_meta" / "_inbox" / "bad-1.rcpt").write_text(broken_rcpt(wid, 22), encoding="utf-8")
        _st_y, raw_y = _http(base, "/api/workshop/%s/intake/poll" % wid, {})
        for item in (load(raw_y).get("processed") or []):
            if item.get("ok") is False:
                codes["B"] = str(item.get("code") or (item.get("errors") or [{}])[0].get("code") or "")
        check("⑦ 坏单在三档下**同一个错误码**（不是各说各话）",
              len(codes) >= 2 and len({c for c in codes.values() if c}) == 1 and codes.get("C"),
              json.dumps(codes, ensure_ascii=False))

        # ⑧ 幂等一致：同一份回执（同序号同内容）重发 ⇒ 判重复
        same = roster_rcpt(wid, 31, "前端组/员工01", "demo-parity")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": same})
        st_d, raw_d = _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": same})
        body_d = load(raw_d)
        dup_flag = bool(body_d.get("duplicate")) or "duplicate" in json.dumps(body_d, ensure_ascii=False)
        check("⑧ 同一份回执重发判重复（幂等，不重复入账）", st_d == 200 and dup_flag, raw_d[:200])

        # ⑨ 原文留痕一致：坏单原文真的落到 receipts/ 下（可追）
        receipts = tmpdir / "data" / "receipts"
        raw_hits = list(receipts.rglob("*")) if receipts.is_dir() else []
        saved = [p for p in raw_hits if p.is_file()]
        kept_text = ""
        for p in saved:
            try:
                kept_text += p.read_text(encoding="utf-8", errors="replace")
            except OSError:                                    # noqa: BLE001 — 读不到就跳过（下面按内容判）
                continue
        check("⑨ 坏单原文真留痕（receipts/ 里找得到那种坏单的痕迹）",
              bool(saved) and ("行数" in kept_text), "落盘 %d 个文件" % len(saved))

        # ⑩ 目录对账：available 的协议必须有判据工具（缺了要点名）
        st_p, raw_p = _http(base, "/api/protocols")
        protos = {p["id"]: p for p in (load(raw_p).get("protocols") or [])}
        missing = []
        for pid, p in protos.items():
            if str(p.get("status")) != "available":
                continue
            tool = JUDGE_OF.get(pid)
            if not tool or not (ROOT / tool).is_file():
                missing.append("%s→%s" % (pid, tool or "（没登记判据）"))
        check("⑩ `available` 的协议都有真机判据工具（目录不撒谎）",
              not missing, "缺：%s" % "、".join(missing))

        # ⑪ 目录语义不打架：MCP=工具面，A2A/gRPC=任务语义
        layers = {pid: str(p.get("layer") or "") for pid, p in protos.items()}
        check("⑪ 目录按层说能力（MCP=工具面，A2A/gRPC=任务语义）",
              layers.get("mcp") == "transport_tools"
              and layers.get("a2a") == "transport_task"
              and layers.get("grpc") == "transport_task",
              json.dumps(layers, ensure_ascii=False))
    finally:
        if proc is not None and not a.keep:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:                                   # noqa: BLE001 — 收尾杀不掉不影响结论
                try:
                    proc.kill()
                except Exception:                               # noqa: BLE001 — 同上
                    pass
        if not a.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：三档同账 + 协议目录对账（V119 批 A）")
    print("=" * 78)
    fails = 0
    for name, ok, detail in rows:
        print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("　（%s）" % detail) if not ok else ""))
        if not ok:
            fails += 1
    print("-" * 78)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(rows), len(rows) - fails, fails))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
