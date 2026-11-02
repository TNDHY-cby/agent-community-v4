# -*- coding: utf-8 -*-
"""把平台**清回出厂空白**（盲测用）：注册的 agent / 工作间 / 心智根 / 会话 / 任务 / 桥 / 凭据一次清掉。

设计原则（与项目铁律对齐）：

1. **默认只看不删**（`--dry-run` 是默认行为）；真删必须 `--yes`，且**删前自动备份**，备份失败就中止；
2. **服务在跑时拒绝执行**（边跑边删 = 半清状态），提示先停服务；
3. **清单必须说清"删什么、留什么、为什么"**，不许静默跳项；
4. **可回滚**：备份落在 `<data>/backups/pre-reset-<时间戳>/`，恢复就是把备份拷回去；
5. **不假定路径**：数据目录认 `--data-dir` > `AC_DATA_DIR` > 仓库默认 `agent_community/data`。

用法：
```
python tools/reset_platform_data.py                 # 只看（dry-run）
python tools/reset_platform_data.py --yes           # 真清（先自动备份）
python tools/reset_platform_data.py --yes --keep-logs   # 日志也留着（默认就留）
python tools/reset_platform_data.py --data-dir D:\\acdata --dry-run
```
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 要清的（业务数据 + 凭据 + 生成物）。**宁可多列，也不许漏**：漏一个就会带着旧身份进盲测。
WIPE = [
    # 注册的 agent 与它们的凭据
    "harnesses.json", "harness_tooling.json", "harness_launch_log.json",
    "sessions.json", "session_notes.json", "keys",
    # 工作间与名单（两套都清，避免"旧有新无"）
    "workshops.json", "workshops", "workshop_modes.json", "v5_roster.json",
    "rooms.json", "interjects.json", "discuss",
    # 任务 / 派活 / 账本 / 验收
    "tasks.json", "task_ledger.json", "task_to_room.json", "dispatch_log.jsonl",
    "dispatch_seq.json", "progress.jsonl", "receipts", "handover_receipts.json",
    "state_machine.json", "ai_pending",
    # 心智根（agent 记忆；盲测必须是空白）
    "minds",
    # 协议侧
    "a2a_contexts.json", "a2a_pending.json", "peers.json", "registry_health.json",
    # 桥与验证沙箱（桥产物、验证间工作间都算生成物）
    "bridges", "verify_sandbox", "probe",
    # 其它运行时状态
    "monitor", "runtime", "usage", "cache", "attestations", "memory", "companies.json",
]

#: 一律保留（配置/日志/备份）：删了要么伤配置，要么没法回滚。
KEEP_ALWAYS = ["logs", "backups", "policy.json", "plugins.json", "local_ai.json",
               "policy_pending.json", "input_anchors.json", "input_regions.json",
               "audit.json", "run", "autostart"]

WIPE_PREFIX_BAD = (".bak", ".bak_", ".old")


def _data_dir(argv: list) -> Path:
    for i, a in enumerate(argv):
        if a == "--data-dir" and i + 1 < len(argv):
            return Path(argv[i + 1]).expanduser().resolve()
        if a.startswith("--data-dir="):
            return Path(a.split("=", 1)[1]).expanduser().resolve()
    env = os.environ.get("AC_DATA_DIR")
    return Path(env).expanduser().resolve() if env else ROOT / "agent_community" / "data"


def _service_running(data: Path) -> tuple:
    """(在跑?, 说明)。看 lease 文件里的 pid 是否还活着 —— 不联网、不依赖平台模块。"""
    lease = data / "run" / "platform.lease.json"
    if not lease.is_file():
        return False, "没有 lease 文件（服务应该没在跑）"
    try:
        info = json.loads(lease.read_text(encoding="utf-8") or "{}")
    except Exception as e:                                    # noqa: BLE001 — 读坏也要说清
        return True, "lease 文件读不出来（%s），保险起见当作服务在跑" % e
    pid = info.get("pid")
    if not isinstance(pid, int):
        return True, "lease 里没有 pid（当作服务在跑）"
    # ★★2026-11-02 事故修正（**必须 fail-closed**）★★
    #   我在 Windows 上用 `os.kill(pid, 0)` 探活 → 抛 SystemError（WinError 87，Windows 非 POSIX 语义），
    #   当时的代码把"探活失败"当成"服务没在跑" ⇒ `--yes` 通过 ⇒ **真把老板的 38 项业务数据删了**
    #   （幸好事前自动备份，已完整恢复）。教训：**探活不确定时一律当作"在跑"并拒绝真删**，
    #   宁可让人多确认一次，也不能在不确定时动数据。
    #   探活走项目自己的进程事实层 `platform/sysprobe.py`（psutil 优先 / 纯标准库兜底，一处真相）。
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from agent_community.platform import sysprobe as _sp

        alive = bool(_sp.is_pid_alive(pid))
    except Exception as e:                                    # noqa: BLE001 — 探活层不可用 ⇒ fail-closed
        return True, ("探活不可用（%s: %s）⇒ **保险起见当作服务在跑**；确认停干净再执行"
                      % (type(e).__name__, str(e)[:80]))
    if not alive:
        # 只有"明确不存在"才放行；这里再交叉核对一次 lease 端口是否还在听
        port = info.get("port")
        try:
            if isinstance(port, int) and _sp.port_listening(port):
                return True, "pid=%s 不在了但端口 %s 还在听（可能另一个实例）⇒ 当作在跑" % (pid, port)
        except Exception:                                     # noqa: BLE001 — 端口探不了 ⇒ 不放行
            return True, "pid=%s 不在了，但端口探测不可用 ⇒ 当作在跑" % pid
        return False, "lease 里的 pid=%s 已不在，端口也没在听（服务没跑）" % pid
    return True, "服务在跑（pid=%s，端口 %s）" % (pid, info.get("port", "?"))


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%d B" % n


def main(argv: list) -> int:
    dry = "--yes" not in argv
    data = _data_dir(argv)
    print("=" * 74)
    print("平台数据清理（盲测用）｜数据目录：%s" % data)
    print("模式：%s" % ("**只看不删**（加 --yes 才真清）" if dry else "**真清**（先自动备份）"))
    print("=" * 74)
    if not data.is_dir():
        print("[FAIL] 数据目录不存在：%s" % data)
        return 2

    # ★2026-11-02 事故后加的**第二道闸**：真清必须在交互终端里再敲一次 `yes`；
    #   非交互（脚本/CI）必须显式给 `--force-noninteractive`，否则一律不动数据。
    #   （不用 `stdin.isatty()` 判断：PowerShell/管道里它可能报 True 但读不到输入 ⇒ 改"直接试读"）
    if not dry and "--force-noninteractive" not in argv:
        print("\n即将**删除**上面这个数据目录里的业务数据（会先自动备份）。")
        try:
            ans = input("确认请输入 yes（其它任何输入都取消）：").strip()
        except EOFError:
            ans = ""
        if ans != "yes":
            print("已取消：**什么都没删**。"
                  "（非交互执行请加 `--force-noninteractive`，但请先确认目录没搞错）")
            return 6

    running, why = _service_running(data)
    print("服务状态：%s" % why)
    # 只看（dry-run）时服务在跑也无害，仅提示；**真清必须服务停**（否则半清状态）
    if running and dry:
        print("提示：服务在跑 —— dry-run 只读，没关系；但 **--yes 之前必须先停服务**。")
    elif running:
        print("\n[FAIL] 服务还在跑 —— 边跑边删会留下半清状态。先停：")
        print("  1) 停看门狗：结束命令行里带 `tools\\watchdog.py` 的 python 进程")
        print("  2) 停服务：  结束命令行里带 `platform.server` 的 python 进程")
        print("  3) 再跑本工具")
        return 3

    todo, keep = [], []
    for name in WIPE:
        p = data / name
        if not p.exists():
            continue
        todo.append(p)
    listed = {p.name for p in todo}
    for p in sorted(data.iterdir()):
        if p.name in listed or p.name in KEEP_ALWAYS:
            continue
        if any(tag in p.name for tag in WIPE_PREFIX_BAD):      # *.bak_* 备份不动
            keep.append((p, "旧备份（*.bak_*），不动"))
            continue
        keep.append((p, "未登记（保守起见不动；要清请加进 WIPE）"))

    total = sum(_size(p) for p in todo)
    print("\n--- 将被清掉（%d 项，共 %s）---" % (len(todo), _human(total)))
    for p in todo:
        print("  删  %-26s %8s  %s" % (p.name, _human(_size(p)),
                                       "目录" if p.is_dir() else "文件"))
    print("\n--- 一律保留 ---")
    for p in sorted(data.iterdir()):
        if p.name in KEEP_ALWAYS:
            print("  留  %-26s %8s" % (p.name, _human(_size(p))))
    if keep:
        print("\n--- 其它（未登记，不动）---")
        for p, note in keep[:12]:
            print("  ·   %-26s %s" % (p.name, note))
        if len(keep) > 12:
            print("  ·   …… 其余 %d 项同理" % (len(keep) - 12))

    if dry:
        print("\n结论：**什么都没删**（这是 dry-run）。确认无误后加 `--yes` 真清。")
        print("提示：盲测更安全的做法是**换数据目录**（新目录起服务），原数据一字不动：")
        print("  $env:AC_DATA_DIR='D:\\acdata_blank' ; python -m agent_community.platform.server")
        return 0

    # ── 真清：先备份（失败就中止）────────────────────────────────
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = data / "backups" / ("pre-reset-%s" % stamp)
    backup.mkdir(parents=True, exist_ok=False)
    print("\n备份到：%s" % backup)
    failed = []
    for p in todo:
        dst = backup / p.name
        try:
            if p.is_dir():
                shutil.copytree(p, dst)
            else:
                shutil.copy2(p, dst)
        except Exception as e:                                # noqa: BLE001 — 备份失败必须中止
            failed.append("%s（%s: %s）" % (p.name, type(e).__name__, e))
    if failed:
        print("[FAIL] 备份没成功，**一个字节都没删**：")
        for f in failed:
            print("  · " + f)
        print("备份目录留着：%s（可手工核对）" % backup)
        return 4
    print("备份完成（%d 项）" % len(todo))

    removed, errors = [], []
    for p in todo:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(p.name)
        except Exception as e:                                # noqa: BLE001 — 删不掉的要说
            errors.append("%s（%s: %s）" % (p.name, type(e).__name__, e))
    print("\n已删 %d 项" % len(removed))
    if errors:
        print("[WARN] 有 %d 项没删掉（**这是半清状态**，请手工处理）：" % len(errors))
        for e in errors:
            print("  · " + e)
        print("回滚：把 %s 里的内容拷回 %s" % (backup, data))
        return 5

    print("\n当前数据目录只剩：")
    for p in sorted(data.iterdir()):
        print("  · %s" % p.name)
    print("\n下一步（盲测）：")
    print("  1) 起服务：`python -m agent_community.platform.server`（或 tools\\watchdog.py --daemon）")
    print("  2) 自检：  `python tools/watchdog.py --check`（exit 0 = 活）")
    print("  3) 空白确认（真端点，2026-11-02 实测）：")
    print("       /api/harness-tooling  → 0 项（没有注册过的 agent）")
    print("       /api/sessions         → count = 0")
    print("       /api/workshops        → workshops = []")
    print("  4) 陌生人走查：`python tools/first_run_check.py`")
    print("\n回滚：把 `%s` 里的内容拷回 `%s`（然后重启服务）" % (backup, data))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
