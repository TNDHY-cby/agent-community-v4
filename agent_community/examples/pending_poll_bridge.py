# -*- coding: utf-8 -*-
"""
通用 pending 队列桥（示例）：让「没有真实 ACP server、也不能被平台直接拉起」的 harness
（如 acp_command 指向 GUI 程序的桌面 Agent、或 http/clipboard 类）真正接入员工工作间。

原理：
  平台对非 file_poll 类 harness，把激活/任务放进 pending 队列（内存中等待领取）。
  本桥进程代替 harness 轮询领取：
    1) GET  /api/harness/pending-activations?harness_id=X   → 领激活任务
       → 把任务 JSON 落盘到 <work-dir>/inbox/activate_*.json（供 harness 侧读取/人工查看）
       → 可选执行 --trigger 唤醒命令（如打开你的 GUI 程序）
       → 回报 POST /api/harness/activation-result（默认 entered，可 --no-auto-enter 改为等回报）
    2) GET  /api/harness/pending-tasks?harness_id=X         → 领工作任务
       → 把任务 JSON 落盘到 <work-dir>/inbox/task_*.json
       → 可选执行 --trigger 唤醒命令
       → 等待 harness 干完活，把回报 JSON 写进 <work-dir>/replies/reply_*.json，
         桥自动转发到平台 /api/harness/task-result，并把文件移入 sent/
    3) 心跳线程：POST /api/harness/heartbeat，保持在线

回报文件格式（写 replies/reply_<时间戳>.json，内容与平台接口 body 一致）：
  激活回报：{"type":"activation","workshop_id":"...","member_id":"...","status":"entered"}
  任务回报：{"type":"task","workshop_id":"...","member_id":"...","ok":true,"result":"我完成了..."}
  （type 缺省时按有没有 result/ok 自动判断端点）

用法（在 harness 所在机器上跑，Windows 直接 python 运行；下面的 harness_id 是虚拟示例，请换成你自己的）：
  python pending_poll_bridge.py --harness-id 示例Harness-B ^
      --url http://127.0.0.1:18920 ^
      --work-dir "D:\\你的目录\\bridge" ^
      --trigger "start \"\" \"C:\\path\\to\\your\\app.exe\""

说明：
  - 激活默认自动回报 entered（harness 侧收到通知即视为进入工作状态）；
    若想严格等 harness 确认，加 --no-auto-enter，则激活也等 replies/ 里的回报。
  - 任务必须等 replies/ 回报才转发；人工干完活后写回报文件即可，桥全自动转发。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ── 输出编码自保（2026-10-11 实测缺陷修复）──────────────────────────────
# 同 filepoll_harness_bridge.py：本脚本也打印 ✓ / ✗，在中文 Windows（GBK 码页）上
# 会抛 `UnicodeEncodeError: 'gbk' codec can't encode`，**把桥进程弄死**。
# ⇒ 脚本自己把 stdout/stderr 改成 UTF-8 + errors="replace"，不要求用户记环境变量。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — 老解释器没有 reconfigure：退回原样，不因此崩
    pass


# ── HTTP 工具 ──────────────────────────────────────────────────

def post_json(url: str, payload: dict, timeout: float = 30.0) -> tuple[bool, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status < 300, body[:300]
    except Exception as e:
        return False, str(e)


def get_json(url: str, timeout: float = 30.0) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"[桥] 请求失败 {url}: {e}", flush=True)
        return {}


def self_check(base: str, harness_id: str, work_dir: Path) -> bool:
    """启动自检：平台是否在线、harness 是否已注册、work-dir 是否可写。"""
    ok = True
    # 1) 平台在线
    try:
        with urllib.request.urlopen(f"{base}/api/status", timeout=5) as r:
            print(f"[自检] 平台在线: {base} -> HTTP {r.status}", flush=True)
    except Exception as e:
        print(f"[自检][✗] 平台不可达 {base}: {e}", flush=True)
        ok = False
    # 2) harness 已注册
    try:
        lst = get_json(f"{base}/api/harness/list")
        ids = [h.get("harness_id") for h in lst.get("harnesses", [])]
        if harness_id in ids:
            print(f"[自检] harness「{harness_id}」已注册 ✓", flush=True)
        else:
            print(f"[自检][!] harness「{harness_id}」未注册！先注册再跑桥", flush=True)
            ok = False
    except Exception as e:
        print(f"[自检][!] 查询 harness 列表失败: {e}", flush=True)
    # 3) work-dir 可写
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        probe = work_dir / ".bridge_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print(f"[自检] work-dir 可写: {work_dir} ✓", flush=True)
    except Exception as e:
        print(f"[自检][✗] work-dir 不可写 {work_dir}: {e}（检查权限/沙箱）", flush=True)
        ok = False
    return ok


# ── 任务落盘 / 回报转发 ────────────────────────────────────────

def write_task_file(work_dir: Path, kind: str, payload: dict) -> Path:
    inbox = work_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    name = f"{kind}_{int(time.time() * 1000)}.json"
    path = inbox / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_trigger(trigger: str):
    if not trigger:
        return
    try:
        subprocess.Popen(trigger, shell=True)
        print(f"[唤醒] 已执行 trigger: {trigger}", flush=True)
    except Exception as e:
        print(f"[唤醒] trigger 失败: {e}", flush=True)


def pick_endpoint(payload: dict, base: str) -> str:
    t = str(payload.get("type", ""))
    if t in ("task", "task_assign") or "ok" in payload or "result" in payload:
        return f"{base}/api/harness/task-result"
    return f"{base}/api/harness/activation-result"


def scan_replies(work_dir: Path, base: str, auto_enter: bool, interval: float):
    """轮询 replies/：harness 干完活写的回报文件 → 转发平台 → 移入 sent/（失败移 failed/）。"""
    replies = work_dir / "replies"
    if not replies.exists():
        return
    (replies / "sent").mkdir(parents=True, exist_ok=True)
    (replies / "failed").mkdir(parents=True, exist_ok=True)
    for f in sorted(replies.glob("reply_*.json")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[回报] 解析失败 {f.name}: {e}", flush=True)
            payload = None
        ok = False
        if payload:
            url = pick_endpoint(payload, base)
            ok, resp = post_json(url, payload)
            print(f"[回报] POST {url} -> ok={ok} {resp}", flush=True)
        target = (replies / "sent" if ok else replies / "failed") / f"{int(time.time() * 1000)}_{f.name}"
        try:
            f.rename(target)
        except Exception:
            try:
                f.replace(target)
            except Exception as e2:
                print(f"[回报] 移动失败 {f.name}: {e2}", flush=True)


# ── 轮询主循环 ─────────────────────────────────────────────────

def poll_loop(base: str, harness_id: str, work_dir: Path, trigger: str,
              auto_enter: bool, interval: float):
    q = urllib.parse.quote(harness_id)
    while True:
        try:
            # 1) 领激活任务
            ad = get_json(f"{base}/api/harness/pending-activations?harness_id={q}")
            for act in ad.get("activations", []):
                path = write_task_file(work_dir, "activate", act)
                print("=" * 60, flush=True)
                print(f"[激活] 已落盘: {path}", flush=True)
                print(json.dumps(act, ensure_ascii=False, indent=2), flush=True)
                print("=" * 60, flush=True)
                run_trigger(trigger)
                if auto_enter:
                    ok, resp = post_json(f"{base}/api/harness/activation-result", {
                        "workshop_id": act.get("workshop_id"),
                        "member_id": act.get("member_id"),
                        "status": "entered",
                    })
                    print(f"[激活] 自动回报 entered -> ok={ok} {resp}", flush=True)
                else:
                    print("[激活] --no-auto-enter：等待 replies/ 回报", flush=True)

            # 2) 领工作任务
            td = get_json(f"{base}/api/harness/pending-tasks?harness_id={q}")
            for task in td.get("tasks", []):
                path = write_task_file(work_dir, "task", task)
                print("=" * 60, flush=True)
                print(f"[任务] 已落盘: {path}", flush=True)
                print(json.dumps(task, ensure_ascii=False, indent=2), flush=True)
                print("=" * 60, flush=True)
                run_trigger(trigger)
                print("[任务] 等待 harness 干完活：把回报写到 replies/reply_*.json，桥会自动转发", flush=True)

            # 2.5) 领桥测试任务并自动回报（平台测试桥通道）
            bt = get_json(f"{base}/api/harness/pending-bridge-tests?harness_id={q}")
            for t in bt.get("tests", []):
                test_id = t.get("test_id", "")
                print(f"[桥测试] 收到平台桥测试 test_id={test_id}，自动回报 ok", flush=True)
                ok, resp = post_json(f"{base}/api/harness/bridge-test-result", {
                    "harness_id": harness_id,
                    "test_id": test_id,
                    "ok": True,
                    "echo": f"pending-poll-bridge alive, work-dir={work_dir}",
                })
                print(f"[桥测试] 回报结果 -> ok={ok} {resp}", flush=True)

            # 3) 转发回报
            scan_replies(work_dir, base, auto_enter, interval)
        except KeyboardInterrupt:
            print("[桥] 退出", flush=True)
            sys.exit(0)
        except Exception as e:
            print(f"[桥] 轮询异常: {e}", flush=True)
        time.sleep(interval)


def heartbeat_loop(base: str, harness_id: str, interval: float):
    q = urllib.parse.quote(harness_id)
    while True:
        try:
            ok, resp = post_json(f"{base}/api/harness/heartbeat?harness_id={q}", {})
            if not ok:
                print(f"[心跳] 失败: {resp}", flush=True)
        except Exception as e:
            print(f"[心跳] 异常: {e}", flush=True)
        time.sleep(interval)


_LOCK_HANDLE = None          # 必须持有引用，否则文件对象被回收 ⇒ 锁被释放


def acquire_single_instance(lock_path):
    """★单实例锁（2026-10-12 新增）：同一 harness 只允许一只桥在跑。

    **为什么必须有**：同一 harness 起两只桥 ⇒ **各领一部分件 / 重复投递 / 心跳互相覆盖**，
    用户只会看到"有时重复、有时没人取"的玄学现象（真机实测：一次启动就出现 2 个活进程，
    而桥脚本**毫无保护**）。
    **为什么用"持锁"而不是"pid 文件"**：pid 文件在进程**崩溃**时会留下僵尸锁，
    且 pid 会被系统复用 ⇒ 判错就会误判"已在运行"或放行第二只。
    **持锁则由操作系统在进程结束时自动释放**（正常退出或崩溃都一样）—— 永不产生僵尸锁。

    成功返回 (handle, "")；已有实例在跑返回 (None, 原因)。
    """
    global _LOCK_HANDLE
    try:
        f = open(lock_path, "a+")
    except Exception as e:  # noqa: BLE001
        # 拿不到锁文件不算致命（例如目录只读）—— 如实说一句，但不阻止运行
        return None, "无法创建锁文件（跳过单实例保护）：%s" % e
    try:
        # ⚠️★**必须先 seek(0)**：`open(..., "a+")` 是**追加模式**，初始文件指针在 **EOF**；
        #   而 `msvcrt.locking` 锁的是"**从当前指针开始的** N 字节"。
        #   ⇒ 不 seek 的话：第一只锁第 0 字节、第二只锁 EOF 处那 1 字节 ——
        #     **两只锁的根本不是同一个区域，于是毫无冲突**（实测：第二只照样跑起来了）。
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None, "另一只桥正在使用同一个 work-dir（拒绝重复启动）"
    try:
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
    except Exception:  # noqa: BLE001
        pass
    _LOCK_HANDLE = f
    return f, ""


def main():
    ap = argparse.ArgumentParser(description="通用 pending 队列桥：轮询领取激活/任务，落盘 + 转发回报")
    ap.add_argument("--harness-id", required=True, help="harness 的 id（必须与注册时完全一致）")
    ap.add_argument("--url", default="http://127.0.0.1:18920", help="平台地址")
    ap.add_argument("--work-dir", default="", help="工作目录（默认 %%TEMP%%/pending_poll_bridge/<harness-id>）")
    ap.add_argument("--trigger", default="", help="领到激活/任务时额外执行的唤醒命令（shell）")
    ap.add_argument("--no-auto-enter", action="store_true", help="激活不自动回报 entered，等 replies/ 回报")
    ap.add_argument("--interval", type=float, default=2.0, help="轮询间隔秒")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    work_dir = Path(args.work_dir) if args.work_dir else (
        Path(__import__("tempfile").gettempdir()) / "pending_poll_bridge" / args.harness_id
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    # ★单实例保护（在**建目录之后**、**开始轮询之前**）：
    #   否则第二只桥会与第一只**抢着领同一批件** ⇒ 重复投递 / 丢件 / 心跳互相覆盖。
    _lk, _why = acquire_single_instance(work_dir / ".bridge.lock")
    if _lk is None and _why and "拒绝重复启动" in _why:
        print("[桥] ⛔ %s" % _why, flush=True)
        print("[桥] 同一 harness 只应有一只桥在跑；要重启请先停掉原来那只。"
              "（work-dir: %s）" % work_dir, flush=True)
        sys.exit(2)
    if _why:
        print("[桥] 提示: %s" % _why, flush=True)

    print(f"[桥] 通用 pending 桥启动", flush=True)
    print(f"[桥] harness: {args.harness_id} | 平台: {base} | work-dir: {work_dir}", flush=True)
    print(f"[桥] trigger: {args.trigger or '(无)'} | auto-enter: {not args.no_auto_enter}", flush=True)

    # 启动自检：平台在线 / 已注册 / 目录可写
    try:
        self_check(base, args.harness_id, work_dir)
    except Exception as e:
        print(f"[自检] 异常: {e}", flush=True)

    threading.Thread(target=heartbeat_loop, args=(base, args.harness_id, max(args.interval, 15)), daemon=True).start()
    poll_loop(base, args.harness_id, work_dir, args.trigger, not args.no_auto_enter, args.interval)


if __name__ == "__main__":
    main()
