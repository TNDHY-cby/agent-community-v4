#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用脚本框架 · 一致性自检（V26 §6）——**给接入者的尺子**。

接入者拿到 L1 骨架、填好自己 harness 的命令后，跑这个工具就知道**合不合格**，
不用来回问平台（也就避免"看起来接上了其实没接"）。

用法：
    python conformance.py <动作脚本.py> [--action new_session] [--timeout 45] [--json]

检查五件事：
  1) `--self-test`：能返回合法 JSON、退出码 0、stdout 无杂质；
  2) **空输入**：不给任何输入时必须**合法 JSON + 非 0 退出码**（不许假装成功、不许崩栈）；
  3) **正常输入**：退出码语义与 evidence 一致（0 必须带 evidence；10 必须不带）；
  4) **幂等**（仅 new_session）：连跑两次——若两次都能自证却给出**不同的会话标识** ⇒ 不合格；
  5) **超时**：在标称超时内返回。

退出码：0 = 全部通过；1 = 有不合格项。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

EXIT_OK, EXIT_UNVERIFIED = 0, 10

EVIDENCE_HINTS = {
    "new_session": ("session",),
    "rename_session": ("applied_name",),
    "deliver": ("method",),
}


def _call(script: str, payload: dict | None, timeout: float, extra_args: list[str] | None = None):
    """调用脚本一次，返回 (退出码, stdout, stderr, 用时)。"""
    args = [sys.executable, script] + list(extra_args or [])
    tmp = None
    if payload is not None:
        fd, tmp = tempfile.mkstemp(prefix="conf_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        args += ["--input", tmp]
    t0 = time.time()
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or ""), time.time() - t0
    except subprocess.TimeoutExpired:
        return -9, "", "TIMEOUT", time.time() - t0
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _json_of(stdout: str):
    s = (stdout or "").strip()
    if not s:
        return None, "stdout 为空"
    try:
        obj = json.loads(s)
    except Exception as e:  # noqa: BLE001
        return None, f"stdout 不是合法 JSON（{e}）；契约要求**只输出一个 JSON**，日志请走 stderr"
    if not isinstance(obj, dict):
        return None, "stdout 的 JSON 必须是对象"
    return obj, ""


def check(script: str, action: str, timeout: float) -> list[dict]:
    rows: list[dict] = []

    def add(name, ok, detail=""):
        rows.append({"check": name, "ok": bool(ok), "detail": detail})

    # 1) self-test
    rc, out, err, _ = _call(script, None, min(timeout, 20), ["--self-test"])
    obj, why = _json_of(out)
    add("1 自检可调用（--self-test）", rc == EXIT_OK and obj is not None,
        why or f"退出码={rc}（期望 0）")

    # 2) 空输入：必须合法 JSON + 非 0 退出码
    rc, out, err, _ = _call(script, {}, timeout)
    obj2, why2 = _json_of(out)
    add("2 空输入不假装成功", obj2 is not None and rc not in (0,),
        why2 or f"退出码={rc}（期望非 0：空输入不得当成成功）")

    # 3) 正常输入：退出码与 evidence 一致
    payload = {"action": action, "name_hint": "conf-ws-conf-成员-conf1",
               "new_name": "conf-ws-conf-成员-conf1", "text": "conformance probe",
               "session": {"id": "conf-session", "name": "conf"}}
    rc, out, _, _ = _call(script, payload, timeout)
    obj3, why3 = _json_of(out)
    if obj3 is None:
        add("3 退出码与 evidence 一致", False, why3)
    else:
        ev = obj3.get("evidence") or {}
        if rc == EXIT_OK:
            ok = bool(ev) and all(k in ev for k in EVIDENCE_HINTS.get(action, ()) or ("",))
            add("3 退出码与 evidence 一致", ok,
                f"退出码 0 时 evidence 必须含 {EVIDENCE_HINTS.get(action)}，实得 {list(ev)}")
        elif rc == EXIT_UNVERIFIED:
            add("3 退出码与 evidence 一致", not ev,
                "退出码 10（未核实）时 evidence 应为空，否则请改用退出码 0")
        else:
            add("3 退出码与 evidence 一致", True,
                f"退出码={rc}（失败态，本项不适用；平台会走降级链）")

    # 4) 幂等（仅 new_session）
    if action == "new_session":
        r1 = _call(script, payload, timeout)
        r2 = _call(script, payload, timeout)
        o1, _ = _json_of(r1[1])
        o2, _ = _json_of(r2[1])
        id1 = ((o1 or {}).get("evidence") or {}).get("session", {}).get("id") if o1 else None
        id2 = ((o2 or {}).get("evidence") or {}).get("session", {}).get("id") if o2 else None
        if id1 and id2:
            add("4 幂等（重跑不得建出第二个会话）", id1 == id2,
                f"两次得到不同会话 id：{id1} vs {id2} —— 重跑会多建一个会话")
        else:
            add("4 幂等（重跑不得建出第二个会话）", True,
                "两次都没给出可自证的会话 id（人工/未核实模式），本项跳过")
    else:
        add("4 幂等", True, f"{action} 不需要幂等判定")

    # 5) 超时
    rc, _, _, used = _call(script, payload, timeout)
    add("5 在标称超时内返回", rc != -9, f"用时 {used:.2f}s / 上限 {timeout}s")

    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="通用脚本框架一致性自检")
    ap.add_argument("script")
    ap.add_argument("--action", default="new_session")
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = check(a.script, a.action, a.timeout)
    failed = [r for r in rows if not r["ok"]]
    if a.json:
        print(json.dumps({"script": a.script, "action": a.action, "rows": rows,
                          "passed": len(rows) - len(failed), "failed": len(failed)},
                         ensure_ascii=False, indent=2))
    else:
        print(f"一致性自检：{a.script}（动作 {a.action}）")
        for r in rows:
            print(f"  [{'PASS' if r['ok'] else 'FAIL'}] {r['check']}" + (f" —— {r['detail']}" if r['detail'] else ""))
        print(f"结论：{'全部通过' if not failed else str(len(failed)) + ' 项不合格'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
