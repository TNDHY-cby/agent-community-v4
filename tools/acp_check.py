#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判据：**ACP 落地**（`V119` 批 C）九格 —— 真拉起子进程、真多轮、真多会话。

判的是这五条"落地"（缺一条就不算）：
  ① 真拉起 → initialize 有回；
  ② session/new 拿到会话号；
  ③ **多轮上下文不丢**（第 3 轮回复里带得出第 1 轮的内容）；
  ④ **多会话不串**（两条会话各记各的）；
  ⑤ **旧会话释放要如实说**：进程重启后旧 sessionId 必须被拒（不假装续上）；
  ⑥ **拉起失败要如实报**（命令不存在 ⇒ 明确错误，不挂起）。

⚠️ 用 `tests/_fake_acp_agent.py` 当对端（它**有状态**：记会话历史）；真实 harness 换成真命令即可。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
PY = sys.executable
FAKE = ROOT / "agent_community" / "tests" / "_fake_acp_agent.py"

rows: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    rows.append((name, bool(ok), detail))


def _bridge():
    from agent_community.platform.acp_harness_bridge import AcpHarnessBridge

    return AcpHarnessBridge(cmd=[PY, str(FAKE).replace("\\", "/")])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="跑完不关（调试用）")
    a = ap.parse_args()

    mark = Path(os.environ.get("TEMP") or ".") / ("acp_mark_%d.log" % int(time.time()))
    os.environ["AC_FAKE_ACP_MARK"] = str(mark)
    if not FAKE.is_file():
        print("[SKIP] 没有假 agent：%s" % FAKE)
        return 0

    b1 = b2 = None
    sid1 = sid2 = ""
    try:
        # ① 真拉起 + initialize
        b1 = _bridge()
        try:
            b1.start()
            init = b1.initialize()
            check("ACP ① 真拉起子进程且 initialize 有回", bool(init),
                  "initialize=%s" % str(init)[:120])
        except Exception as e:                                  # noqa: BLE001
            check("ACP ① 真拉起子进程且 initialize 有回", False, "%s: %s" % (type(e).__name__, e))
            raise

        # ② session/new
        sess1 = b1.new_session(str(ROOT))
        sid1 = str(getattr(sess1, "session_id", "") or "")
        check("ACP ② session/new 回了一个会话号", bool(sid1), "sessionId=%r" % sid1)
        sess2 = b1.new_session(str(ROOT))
        sid2 = str(getattr(sess2, "session_id", "") or "")
        check("ACP ③ 同一连接能开第二条会话（1 harness → N 会话）",
              bool(sid2) and sid2 != sid1, "sid1=%s sid2=%s" % (sid1, sid2))

        # ④ 多轮上下文不丢：第 3 轮要能带出第 1 轮
        s1, t1 = b1.prompt(sid1, "第一轮：记住暗号 蓝鲸-77")
        s2, t2 = b1.prompt(sid1, "第二轮：再记一个 珊瑚-12")
        s3, t3 = b1.prompt(sid1, "第三轮：把我们记得的复述一遍")
        check("ACP ④ 多轮上下文不丢（第 3 轮带得出第 1 轮）",
              bool(t3) and "蓝鲸-77" in str(t3) and "珊瑚-12" in str(t3),
              "第3轮 stop=%r 回复=%r" % (s3, str(t3)[-160:].replace("\n", " ")))
        check("ACP ⑤ 每轮都真收到了回复（不是空回）",
              all(bool(x) for x in (t1, t2, t3)), "1=%s 2=%s 3=%s" % (
                  bool(t1), bool(t2), bool(t3)))

        # ⑥ 多会话不串：会话 2 里不应该看到会话 1 的暗号
        _su, u1 = b1.prompt(sid2, "会话二的唯一内容 白鹭-09")
        check("ACP ⑥ 多会话不串（会话二看不到会话一的暗号）",
              bool(u1) and "白鹭-09" in str(u1) and "蓝鲸-77" not in str(u1),
              "会话二回复=%r" % str(u1)[-160:].replace("\n", " "))

        # ⑦ 旧会话释放要如实说：重启进程后，旧 sid 必须被拒
        b1.close()
        b1 = None
        b2 = _bridge()
        b2.start()
        b2.initialize()
        _old_stop, _old_text = b2.prompt(sid1, "重启后还想续旧会话？")
        old_err = str(_old_stop or "")
        check("ACP ⑦ 重启后旧会话被明确拒（不假装续得上）",
              old_err.startswith("error") and "unknown session" in old_err.lower(),
              "旧会话错误=%r" % (str(old_err)[:140],))
        fresh = b2.new_session(str(ROOT))
        fsid = str(getattr(fresh, "session_id", "") or "")
        fstop, ftext = b2.prompt(fsid, "新会话第一轮")
        ferr = "" if str(fstop) == "end_turn" else str(fstop)
        check("ACP ⑧ 重启后新会话可用（能继续说）", bool(ftext) and not ferr,
              "新会话=%s stop=%r 回复=%r" % (fsid, fstop, str(ftext)[:80]))

        # ⑨ 拉起失败要如实报（命令不存在 ⇒ 明确错误，且不挂起）
        t0 = time.time()
        bad = None
        try:
            from agent_community.platform.acp_harness_bridge import AcpHarnessBridge

            bad = AcpHarnessBridge(cmd=[PY, "不存在的脚本_zzz.py"])
            bad.start()
            bad.initialize(timeout=5)
            failed_loudly = False
            detail = "没有抛错（可能会挂起）"
        except Exception as e:                                  # noqa: BLE001
            failed_loudly = True
            detail = "%s: %s" % (type(e).__name__, str(e)[:120])
        else:
            detail = ""
        cost = time.time() - t0
        check("ACP ⑨ 拉起失败如实报（不挂起、不假装成功）",
              failed_loudly and cost < 20, "%s（耗时 %.1fs）" % (detail, cost))
    except Exception as e:                                      # noqa: BLE001
        check("ACP 整段没跑完（如实记）", False, "%s: %s" % (type(e).__name__, e))
    finally:
        for b in (b1, b2):
            try:
                if b is not None:
                    b.close()
            except Exception:                                   # noqa: BLE001 — 收尾失败不影响结论
                pass

    print("=" * 78)
    print("判据：ACP 落地（V119 批 C）—— 真拉起子进程 + 真多轮 + 真多会话")
    print("=" * 78)
    fails = 0
    for name, ok, detail in rows:
        print("  %s %s%s" % ("PASS" if ok else "FAIL", name, ("　（%s）" % detail) if not ok else ""))
        if not ok:
            fails += 1
    print("-" * 78)
    print("合计 %d 格：PASS %d / FAIL %d" % (len(rows), len(rows) - fails, fails))
    try:
        mark.unlink()
    except OSError:                                             # noqa: BLE001 — 删不掉临时日志不重要
        pass
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
