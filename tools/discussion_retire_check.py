#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**三级讨论区已换成结构化讨论**（`V116` §18 决策 + `V117` §7-5 AILP）—— 八格。

老板 2026-10-29：「不是要把三个级的讨论区换掉吗？」
⇒ 判据要证明三件事：
  ① 旧自由发言入口**默认退役**（410 + 代码 + 为什么 + 新路径），**不落库、不驱动 AI**；
  ② 新结构化入口**真能用**（四类消息、`manifest_ref` 强校验、自由聊天 422）；
  ③ 兼容开关打开时旧行为还在，但**响应标 legacy**（不是偷偷复活）。

真跑：临时实例（换端口 + 换数据目录 + 换心智根），全部真 HTTP。
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

rows: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    rows.append((name, bool(ok), detail))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _http(base: str, path: str, body=None, timeout: float = 30):
    path = urllib.parse.quote(path, safe="/?&=%:")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(base + path, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
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


def _boot(base: str, tmpdir: Path, env_extra: dict | None = None):
    port = _free_port()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.update({"AC_PORT": str(port), "AC_DATA_DIR": str(tmpdir / "data"),
                "AC_MIND_ROOT": str(tmpdir / "minds"), "PYTHONIOENCODING": "utf-8"})
    env.update(env_extra or {})
    proc = subprocess.Popen([PY, "-m", "agent_community.platform.server"], cwd=str(ROOT),
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            if _http(base, "/api/status", timeout=3)[0] == 200:
                return base, proc
        except OSError:                                        # noqa: BLE001 — 有意静默：服务还没起来，继续等
            pass
        time.sleep(0.5)
    proc.terminate()
    return "", proc


def _fixture(base: str, tmpdir: Path, wid_name: str = "退役判据"):
    """建一个真工作间（带工作区路径 + 名单 + manifest），返回 wid。"""
    wsdir = tmpdir / ("ws_" + wid_name)
    (wsdir / "_lead").mkdir(parents=True, exist_ok=True)
    (wsdir / "_lead" / "建模.md").write_text("# 建模\n", encoding="utf-8")
    _s, wb = _http(base, "/api/workshop", {"name": wid_name, "hall_content": "方向",
                                          "workshop_path": str(wsdir)})
    wid = str(load(wb).get("workshop_id") or "")
    _s2, mind_raw = _http(base, "/api/mind/A-01", {})
    mind = load(mind_raw)
    _http(base, "/api/workshop/%s/manifest" % wid,
          {"mind_path": str(mind.get("mind_path") or ""),
           "entry_points": ["_lead/建模.md"]})
    _http(base, "/api/workshop/%s/intake?source=C&confirm=1" % wid,
          {"text": ("#回执 v1\n类型: 名单\n工作间: %s\n成员: 组长\n凭据:\n序号: 1\n---\n"
                    "岗位: 前端组/员工01 | harness: demo-x | model: 示例模型 | 人数: 1\n"
                    "---\n行数: 1" % wid)})
    return wid


def _messages(base: str, wid: str) -> int:
    _s, raw = _http(base, "/api/workshop/%s" % wid)
    msgs = load(raw).get("messages") or []
    return len(msgs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    tmpdir = Path(tempfile.mkdtemp(prefix="ac_retire_"))
    procs = []
    try:
        # ── A. 默认（开关关）：旧入口退役 ───────────────────────────
        base, proc = _boot(base="", tmpdir=tmpdir)
        procs.append(proc)
        if not base:
            print("[FAIL] 临时平台起不来")
            return 2
        check("临时平台起来（默认开关：旧讨论区退役）", True)
        wid = _fixture(base, tmpdir)
        check("夹具：建间 + manifest + 名单", bool(wid), wid)

        before = _messages(base, wid)
        st, raw = _http(base, "/api/workshop/%s/discuss" % wid, {"message": "大家好，随便聊聊"})
        body = load(raw)
        check("① 旧自由发言入口默认 **410 Gone**（不是 200 假成功）",
              st == 410, "HTTP %s %s" % (st, raw[:140]))
        check("② 拒绝时给 `code` + `why` + `next`（说清为什么、该改用什么）",
              body.get("code") == "FREECHAT_RETIRED" and body.get("why") and
              "/ailp" in str(body.get("next")), json.dumps(body, ensure_ascii=False)[:220])
        check("③ 拒绝时**不落库**（讨论区消息数没变）", _messages(base, wid) == before,
              "before=%d after=%d" % (before, _messages(base, wid)))
        check("④ 拒绝时**不驱动 AI**（响应里没有 reply/stage 这类「已讨论」痕迹）",
              not body.get("reply") and not body.get("stage"),
              json.dumps(list(body.keys()), ensure_ascii=False))

        # ── B. 新入口：AILP 结构化 ───────────────────────────────────
        st2, raw2 = _http(base, "/api/workshop/%s/ailp" % wid,
                          {"ailp_version": "2.0", "msg_id": "m-1", "msg_type": "PROPOSE",
                           "sender": "员工01", "target": "组长",
                           "manifest_ref": "_lead/建模.md",
                           "payload": {"proposal": "先做接口", "confidence": 0.8,
                                       "basis_ids": ["T-001"]}})
        check("⑤ 新讨论入口（AILP）真能用", st2 == 200 and load(raw2).get("msg_id") == "m-1",
              "HTTP %s %s" % (st2, raw2[:160]))
        st3, raw3 = _http(base, "/api/workshop/%s/ailp" % wid, {"text": "自由聊天"})
        check("⑥ 新入口同样拒自由聊天（422 + 错误码）",
              st3 == 422 and load(raw3).get("code") == "AILP_INVALID", raw3[:150])
        st4, _raw4 = _http(base, "/api/workshop/%s/ailp" % wid,
                           {"ailp_version": "2.0", "msg_id": "m-2", "msg_type": "PROPOSE",
                            "sender": "员工01", "target": "组长",
                            "manifest_ref": "乱写的/x.md",
                            "payload": {"proposal": "x", "confidence": 0.5, "basis_ids": ["a"]}})
        check("⑦ `manifest_ref` 必须指向真实条目（422 A006）", st4 == 422, "HTTP %s" % st4)

        # ── C. 兼容开关：旧行为还在，但标 legacy ──────────────────────
        tmp2 = Path(tempfile.mkdtemp(prefix="ac_retire_legacy_"))
        base2, proc2 = _boot(base="", tmpdir=tmp2, env_extra={"AC_ALLOW_LEGACY_DISCUSS": "1"})
        procs.append(proc2)
        if base2:
            wid2 = _fixture(base2, tmp2, "兼容判据")
            st5, raw5 = _http(base2, "/api/workshop/%s/discuss" % wid2,
                              {"message": "老前端还在用这条"})
            b5 = load(raw5)
            # 旧行为被恢复＝不再是 410 FREECHAT_RETIRED；老闸门（没成员 ⇒ 409）也算旧行为
            not_retired = not (st5 == 410 and b5.get("code") == "FREECHAT_RETIRED")
            marked = b5.get("legacy") is True
            check("⑧ 开关 `AC_ALLOW_LEGACY_DISCUSS=1` 时旧行为仍在，且响应标 `legacy: true`",
                  not_retired and marked,
                  "HTTP %s %s" % (st5, raw5[:200]))
        else:
            check("⑧ 开关 `AC_ALLOW_LEGACY_DISCUSS=1` 时旧行为仍在，且响应标 `legacy: true`",
                  False, "兼容实例起不来")
    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:                                   # noqa: BLE001 — 收尾杀不掉不影响结论
                try:
                    p.kill()
                except Exception:                               # noqa: BLE001 — 有意静默：子进程可能已退出
                    pass
        if not a.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    print("=" * 78)
    print("判据：三级讨论区已换成结构化讨论（V116 §18 + V117 §7-5）")
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
