#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**P6 结构化讨论区（AILP 2.0）+ tooling 实测** 十二格（`V117` §7-5）。

真跑：临时实例；真 manifest 对账；真写一次盘定 `fs_write`。
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
REF = "前端组/员工01/任务/T-001.md"


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
            return int(r.status), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8", "replace")


def load(raw: str):
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {"_raw": raw[:300]}


def env_msg(**kw):
    base = {"ailp_version": "2.0", "msg_id": "msg-e2e-1", "msg_type": "PROPOSE",
            "sender": "员工01", "target": "组长", "manifest_ref": REF,
            "payload": {"proposal": "先做接口", "confidence": 0.8, "basis_ids": ["T-001"]}}
    base.update(kw)
    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    port = _free_port()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_p6_"))
    base = "http://127.0.0.1:%d" % port
    wsroot = tmpdir / "wsroot"
    minds = tmpdir / "minds"
    (wsroot / REF).parent.mkdir(parents=True, exist_ok=True)
    (wsroot / REF).write_text("# T-001\n", encoding="utf-8")
    (wsroot / "_lead").mkdir(parents=True, exist_ok=True)
    (wsroot / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
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

        st, raw = _http(base, "/api/workshop", {"name": "示例间", "hall_content": "方向",
                                                "workshop_path": str(wsroot)})
        wid = str(load(raw).get("workshop_id") or "")
        check("夹具：建工作间", bool(wid), raw[:120])
        st, raw = _http(base, "/api/mind/A-01", {})
        mp = str(load(raw).get("mind_path") or "")
        st, raw = _http(base, "/api/workshop/%s/manifest" % wid,
                        {"mind_path": mp, "entry_points": ["_lead/建模.md"],
                         "active_tasks": [REF]})
        check("夹具：写合法 manifest（含 active_tasks 供对账）", st == 200, raw[:150])

        # ★2026-11-09：**先派一条活建 T-001** —— `env_msg()` 的 `basis_ids: ["T-001"]`
        #   要去账本里查依据，不派活就没这条 ⇒ A014 ⇒ ②⑦⑧⑨ 连环红（不是代码问题，是夹具缺这步）。
        (tmpdir / "lead_inbox").mkdir(parents=True, exist_ok=True)
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-Lead", "harness_name": "示例Harness-Lead",
            "harness_type": "custom", "wakeup_method": "file_poll",
            "wakeup_dir": str(tmpdir / "lead_inbox"),
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        _http(base, "/api/harness/register", {
            "harness_id": "示例Harness-M1", "harness_name": "示例Harness-M1",
            "harness_type": "custom", "wakeup_method": "a2a",
            "api_base_url": "http://127.0.0.1:9/a2a/x",
            "tools": [{"name": "write_file"}], "ai": {"model_name": "示例模型"}})
        for d in (wsroot / "前端组", wsroot / "前端组" / "员工01" / "任务",
                  wsroot / "前端组" / "员工01" / "产物"):
            d.mkdir(parents=True, exist_ok=True)
        (wsroot / "前端组" / "分工.md").write_text("- 版本: 1\n", encoding="utf-8")
        roster_txt = ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 31\n---\n" % wid
                      + "岗位: 前端组/员工01 | harness: 示例Harness-M1 | model: 示例模型 | 人数: 1\n"
                      + "岗位: _lead | harness: 示例Harness-Lead | model: 示例模型 | 人数: 1\n"
                      + "---\n行数: 2")
        _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid, {"text": roster_txt})
        _st_r, raw_r = _http(base, "/api/workshop/%s/roster" % wid)
        _seq = {str((m or {}).get("slot_dir") or ""): int((m or {}).get("member_seq") or 0)
                for m in (load(raw_r).get("members") or [])}.get("前端组/员工01", 0)
        if _seq:
            _http(base, "/api/workshop/%s/members/%s/evidence" % (wid, _seq),
                  {"protocol_ok": True, "verified_scope": "判据", "tier": "a_protocol"})
            _st_d, raw_d = _http(base, "/api/workshop/%s/dispatch" % wid,
                                 {"member_seq": _seq, "title": "P6 夹具任务（T-001）",
                                  "goal": "做接口", "accept": "能调通"})
        check("夹具：先派活建 T-001（basis_ids 才有依据可查）",
              _seq > 0 and _st_d == 200, "seq=%r dispatch=%s %s" % (_seq, _st_d, str(raw_d)[:120]))

        # ① 字段表是唯一真源
        st, raw = _http(base, "/api/ailp/types")
        cat = load(raw)
        _tnames = {str(t.get("msg_type") or "") for t in (cat.get("types") or [])}
        _must = {"PROPOSE", "CHALLENGE", "REFINE", "CONTEXT_ECHO"}
        check("① `/api/ailp/types` 给字段表 + 规则（四类必须在；新增类型不算错）",
              st == 200 and _must <= _tnames and cat.get("rules"),
              "have=%r n=%d" % (sorted(_tnames), len(_tnames)))

        # ② 结构化消息收下
        st, raw = _http(base, "/api/workshop/%s/ailp" % wid, env_msg())
        ok1 = load(raw)
        check("② 结构化 PROPOSE 收下（回 msg_id）",
              st == 200 and ok1.get("msg_id") == "msg-e2e-1", raw[:160])

        # ③ 自由聊天一律拒（422 + 说明）
        st, raw = _http(base, "/api/workshop/%s/ailp" % wid, {"text": "大家好，随便聊聊"})
        free = load(raw)
        check("③ 自由聊天 ⇒ 422（不许把讨论区变群聊）",
              st == 422 and free.get("code") == "AILP_INVALID"
              and any(e.get("code") in ("A001", "A002", "A003") for e in free.get("errors") or []),
              raw[:180])

        # ④ manifest_ref 不在 manifest 里 ⇒ 拒（并告诉能用哪些）
        st, raw = _http(base, "/api/workshop/%s/ailp" % wid, env_msg(manifest_ref="乱写/x.md"))
        badref = load(raw)
        errs = badref.get("errors") or []
        check("④ `manifest_ref` 不在 manifest ⇒ 422 A006（并列出可用项）",
              st == 422 and any(e.get("code") == "A006" for e in errs)
              and any(REF in (e.get("fix") or "") for e in errs),
              raw[:200])

        # ⑤ 依据为空 / 置信度越界 ⇒ 拒
        st, raw = _http(base, "/api/workshop/%s/ailp" % wid,
                        env_msg(payload={"proposal": "x", "confidence": 0.5, "basis_ids": []}))
        check("⑤ PROPOSE 的 `basis_ids` 为空 ⇒ 拒（A010：说话得有依据）",
              st == 422 and any(e.get("code") == "A010" for e in load(raw).get("errors") or []),
              raw[:160])
        st, raw = _http(base, "/api/workshop/%s/ailp" % wid,
                        env_msg(payload={"proposal": "x", "confidence": 9,
                                         "basis_ids": ["a"]}))
        check("⑥ 置信度不在 0~1 ⇒ 拒（A011）",
              st == 422 and any(e.get("code") == "A011" for e in load(raw).get("errors") or []),
              raw[:160])

        # ⑦ 四类都能过
        payloads = {
            # ★2026-11-09：依据要指向**本间真实存在**的 id（原来写 "mem_005" 是空的 ⇒ 被依据校验拒）
            "CHALLENGE": {"contradiction": "与基模冲突", "evidence_ids": ["msg-e2e-1"],
                          "counter_proposal": "改用另一种"},
            # ★2026-11-09：REFINE 现在必须有 `reason`（A008）—— 判据跟不上接口，不是接口坏了
            "REFINE": {"target_id": "msg-e2e-1", "modifications": "限定条件",
                       "new_confidence": 0.9, "reason": "边界条件需要收紧"},
            "CONTEXT_ECHO": {"echo_target": "蚁群算法", "shared_context": "呼应去中心化"},
        }
        all_ok = True
        why7 = []          # ★"失败要说话"：哪一类没收、什么错误码，必须打出来
        for i, (t, payload) in enumerate(payloads.items(), 1):
            st, raw = _http(base, "/api/workshop/%s/ailp" % wid,
                            env_msg(msg_id="msg-%s" % t, msg_type=t, payload=payload))
            if st != 200:
                why7.append("%s: HTTP %s %s" % (t, st, str(raw)[:140]))
            all_ok = all_ok and st == 200
        check("⑦ CHALLENGE / REFINE / CONTEXT_ECHO 三类也都收", all_ok, "; ".join(why7))

        # ⑧ 读回来是结构化的，不是聊天
        st, raw = _http(base, "/api/workshop/%s/ailp?limit=10" % wid)
        listed = load(raw)
        kinds = {i.get("msg_type") for i in listed.get("items") or []}
        check("⑧ 读回来按类型分组（四类都在，且是结构化条目）",
              st == 200 and {"PROPOSE", "CHALLENGE", "REFINE", "CONTEXT_ECHO"} <= kinds
              and listed.get("counts"),
              json.dumps(listed.get("counts"), ensure_ascii=False))

        # ⑨ 重放幂等
        st, raw = _http(base, "/api/workshop/%s/ailp/replay" % wid, {"msg_id": "msg-e2e-1"})
        check("⑨ 同一条重放 ⇒ duplicate（不重复入账）",
              st == 200 and load(raw).get("duplicate") is True, raw[:120])

        # ⑩ 没有 manifest ⇒ 409 G0（讨论区不能对账就不开）
        st, raw = _http(base, "/api/workshop", {"name": "无清单间", "hall_content": "x",
                                                "workshop_path": str(tmpdir / "ws2")})
        wid2 = str(load(raw).get("workshop_id") or "")
        st, raw = _http(base, "/api/workshop/%s/ailp" % wid2, env_msg())
        check("⑩ 没有合法 manifest ⇒ 409 G0（不开无法对账的讨论区）",
              st == 409 and load(raw).get("code") == "GATE_G0", raw[:140])

        # ⑪⑫ tooling 实测：真写一次盘 ⇒ lead_eligible 变真
        st, raw = _http(base, "/api/harness/h_probe/probe-tooling", {})
        probed = load(raw)
        check("⑪ `probe-tooling` 真写一次盘并记账（并写明这是平台侧落点）",
              st == 200 and probed.get("ok") is True
              and probed["measured"]["fs_write"] is True
              and "不等于" in (probed["measured"].get("scope") or ""),
              raw[:180])
        _http(base, "/api/harness/register",
              {"harness_id": "h_probe", "harness_name": "示例Harness-P",
               "harness_type": "custom", "wakeup_method": "mcp",
               "ai": {"model_name": "示例模型"}})
        st, raw = _http(base, "/api/lead-roster")
        ros = load(raw)
        mine = [c for c in (ros.get("candidates") or []) if c["harness_id"] == "h_probe"]
        check("⑫ 实测为真 ⇒ 这条 harness 进组长候选（终结「候选=0」）",
              bool(mine) and mine[0]["tooling"].get("fs_write_source") == "measured",
              json.dumps([c["harness_id"] for c in (ros.get("candidates") or [])], ensure_ascii=False))
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
    print("判据：P6 结构化讨论区（AILP 2.0）+ tooling 实测 —— 临时实例 %s" % base)
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
