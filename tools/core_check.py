# -*- coding: utf-8 -*-
"""**核心体检一条命令**：六协议矩阵 + 闭环消费 + acp 真拉起 + 桥产物落点。

老板 2026-10-18：「先把核心做好：各种协议、桥、唤醒脚本」→ 由此立了三项判据，
它们各自是独立工具；本文件把它们**合成一条命令**，再补一项"跑完仓库干净没被污染"。

```bash
python tools/core_check.py            # 全跑（约 2 分钟）
python tools/core_check.py --plan     # 只打印会跑什么
```

**退出码**：0＝全过；1＝有项红；2＝有项**没跑成**（容器/平台缺件，如实算"部分"）。

| # | 项 | 判据来源 |
|---|---|---|
| 1 | 六协议矩阵（登记→叫醒脚本→桥模板→生成桥+自检→投递） | `tools/protocol_matrix.py` |
| 2 | 闭环消费（派活→桥搬件→agent 回执→桥转发→单实例锁） | 同上，第 ⑥ 段 |
| 3 | acp 真拉起 + ACP 一轮对话 | `tools/acp_core_check.py` |
| 4 | **看门狗真能救活服务**（能起 + 崩了能再起 + 有日志） | `tools/check_watchdog.py` |
| 5 | **桥产物落点**：跑完这些检查后，仓库 `agent_community/bridges/` 不许多出目录 | 本文件快照对比 |
| 6 | **掉线恢复链**（不打扰空闲 / 掉线可见 / 派巡逻员 / 假修好不放过 / 真修好能确认） | `tools/offline_recover_check.py` |
| 7 | **多成员并发派活**（各自落盘 / 各自回报 / 不串件 / 不互扰 / 冒名被拦） | `tools/task_parallel_check.py` |
| 8 | **证据链 V55**（校验 / 压分 / 归并 / 冲突互链 / 限量分发 / 不泄漏摘录） | `tools/evidence_chain_check.py` |
| 9 | **辩论模式 V52**（默认关 / 拒绝说清 / 陈述入池 / 触发裁决 / 只追加 / 不重复烧钱） | `tools/debate_chain_check.py` |
| 10 | **隔间继承 V53**（只搬产物 / 邮箱桥不搬 / 逐项核对 / 不覆盖 / 不接管 / 幂等） | `tools/inherit_chain_check.py` |
| 11 | **通道生命周期 V28**（真 active / 停用不假 / 停用后不投递 / 换对话序号与留痕） | `tools/channel_chain_check.py` |
| 12 | **受控重启 V58**（没看门狗就拒 / 缺确认就拒 / 非本机就拒 / 有看门狗真重启并自己回来） | `tools/admin_restart_check.py` |
| 13 | **只读日志尾 V59**（尾部 / 上限 / 穿越被拒 / 去敏 / 只读 / 追尾 / 审计） | `tools/logs_tail_check.py` |
| 14 | **任务树 + 资源库**（真嵌套 / 双向关联 / 删登记不删文件 / 落盘留痕） | `tools/tree_resources_check.py` |
| 15 | **巡逻员人工点派**（拒得合理 / 三类动作 / 三态 / 平台复核 / 留痕） | `tools/patrol_chain_check.py` |
| 16 | **视觉拉起**（窗口列表不乱码 / 只读选项 / 置顶 / 方法兜底 / 不许假成功） | `tools/visual_launch_check.py` |
| 17 | **公司间合作 P1**（双实例真投 / 真收 / 回查 / 鉴权 / 隔离） | `tools/cross_company_check.py` |
| 18 | **外部访问通用能力**（只读档 / 三种令牌携带 / 401与403区分 / cookie） | `tools/external_access_check.py` |
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
BRIDGES = ROOT / "agent_community" / "bridges"


def _snapshot_bridges() -> dict:
    """给"生成的桥产物目录"拍快照（它们是运行期产物，不该由检查工具留在仓库里）。"""
    if not BRIDGES.exists():
        return {}
    out = {}
    for p in BRIDGES.rglob("*"):
        try:
            st = p.stat()
            out[str(p.relative_to(BRIDGES))] = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
    return out


def _run(name: str, argv: list, timeout: int = 900) -> tuple:
    t0 = time.time()
    try:
        r = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        out = (r.stdout or "") + (r.stderr or "")
        rc = r.returncode
    except subprocess.TimeoutExpired:
        return "fail", "超时 %ds" % timeout, ""
    except Exception as e:                                   # noqa: BLE001
        return "skip", "%s: %s" % (type(e).__name__, e), ""
    secs = time.time() - t0
    tail = [ln.strip() for ln in out.strip().splitlines() if ln.strip()][-2:]
    return ("pass" if rc == 0 else "fail"), "%.0fs ｜ %s" % (secs, " / ".join(tail)[-160:]), out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args()

    py = sys.executable
    items = [
        ("六协议矩阵（含闭环消费）", [py, str(ROOT / "tools" / "protocol_matrix.py")]),
        ("「建立会话」核心链路", [py, str(ROOT / "tools" / "session_core_check.py")]),
        ("多轮派活（含上一轮上下文）", [py, str(ROOT / "tools" / "task_rounds_check.py")]),
        ("任务全链路（复核/退回/完成）", [py, str(ROOT / "tools" / "task_flow_check.py")]),
        ("acp 真拉起 + ACP 一轮对话", [py, str(ROOT / "tools" / "acp_core_check.py")]),
        ("看门狗真能救活服务", [py, str(ROOT / "tools" / "check_watchdog.py")]),
        ("一键更新可用（更新/回滚/拒绝）", [py, str(ROOT / "tools" / "check_update.py")]),
        ("掉线恢复链（派巡逻员/复核/复验）", [py, str(ROOT / "tools" / "offline_recover_check.py")]),
        ("多成员并发派活（不串件/冒名被拦）", [py, str(ROOT / "tools" / "task_parallel_check.py")]),
        ("证据链 V55（校验/压分/归并/不泄漏）", [py, str(ROOT / "tools" / "evidence_chain_check.py")]),
        ("辩论模式 V52（只追加/不重复烧钱）", [py, str(ROOT / "tools" / "debate_chain_check.py")]),
        ("隔间继承 V53（只搬产物/不接管）", [py, str(ROOT / "tools" / "inherit_chain_check.py")]),
        ("通道生命周期 V28（停用后不投递）", [py, str(ROOT / "tools" / "channel_chain_check.py")]),
        ("受控重启 V58（没看门狗就拒）", [py, str(ROOT / "tools" / "admin_restart_check.py")]),
        ("只读日志尾 V59（白名单/去敏/只读）", [py, str(ROOT / "tools" / "logs_tail_check.py")]),
        ("任务树+资源库（双向关联/不删文件）", [py, str(ROOT / "tools" / "tree_resources_check.py")]),
        ("巡逻员人工点派（三态/平台复核）", [py, str(ROOT / "tools" / "patrol_chain_check.py")]),
        ("视觉拉起（置顶/兜底/不乱码）", [py, str(ROOT / "tools" / "visual_launch_check.py")]),
        ("公司间合作 P1（双实例真投/隔离）", [py, str(ROOT / "tools" / "cross_company_check.py")]),
        ("外部访问（只读档/令牌/cookie）", [py, str(ROOT / "tools" / "external_access_check.py")]),
    ]
    if a.plan:
        print("核心体检会跑 %d 项：" % (len(items) + 1))
        for i, (n, argv) in enumerate(items, 1):
            print("  %d. %s  →  %s" % (i, n, " ".join(Path(x).name for x in argv[1:])))
        print("  %d. 桥产物落点（跑完后仓库 `agent_community/bridges/` 不许新增）" % (len(items) + 1))
        print("退出码：0=全过；1=有红；2=有项没跑成（＝部分）。")
        return 0

    before = _snapshot_bridges()
    print("=" * 88)
    print("核心体检 —— %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 88)
    bad = skipped = 0
    for name, argv in items:
        st, msg, out = _run(name, argv)
        bad += st == "fail"
        skipped += st == "skip"
        print("  [%-4s] %-30s %s" % ({"pass": "OK", "fail": "FAIL", "skip": "跳过"}[st], name, msg))
        if st != "pass":
            for ln in (out or "").strip().splitlines()[-6:]:
                print("            %s" % ln[:150])
            # ★2026-10-19（本机踩到）：失败时只留 6 行 ⇒ **看不见是哪一格红**（六协议矩阵有 30+ 格），
            #   当时又恰逢与全量 pytest 并行、事后单独重跑全绿 ⇒ 无法定位到底是什么失败。
            #   判据必须可定位：把**完整输出**落盘并打印路径（失败要说话，也要可复查）。
            try:
                logp = ROOT / "temp" / ("core_check_fail_%s_%s.log"
                                        % (time.strftime("%Y%m%d-%H%M%S"),
                                           "".join(ch for ch in name if ch.isalnum() or ch in "-_")[:20]))
                logp.parent.mkdir(parents=True, exist_ok=True)
                logp.write_text((out or "") + "\n", encoding="utf-8")
                print("            完整输出：%s" % logp)
            except Exception as _e:                          # noqa: BLE001
                print("            （完整输出落盘失败：%s）" % _e)


    after = _snapshot_bridges()
    new = sorted(set(after) - set(before))
    changed = sorted(k for k in set(after) & set(before) if after[k] != before[k])
    polish_ok = not new and not changed
    print("  [%-4s] %-30s %s" % ("OK" if polish_ok else "FAIL", "桥产物落点（仓库不许新增）",
                                 "仓库 bridges/ 无新增" if polish_ok else
                                 "新增 %d 项 / 改动 %d 项" % (len(new), len(changed))))
    if not polish_ok:
        bad += 1
        for k in (new[:5] + changed[:5]):
            print("            %s" % k)

    print("-" * 88)
    if bad:
        print("结论：**%d 项红** ⇒ 核心不算通过" % bad)
        return 1
    if skipped:
        print("结论：**%d 项没跑成（＝部分）** ⇒ 如实说，不算全绿" % skipped)
        return 2
    print("结论：**核心全绿**（六协议 + 闭环 + acp + 任务链路 + 掉线恢复 + 并发派活 + 证据链 + 辩论 + 继承 + 通道 + 受控重启 + 日志尾 + 任务树 + 巡逻员 + 视觉拉起 + 公司间合作 + 外部访问 + 产物落点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
