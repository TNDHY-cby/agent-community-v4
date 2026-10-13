# -*- coding: utf-8 -*-
"""
filepoll 桥（示例）：让「文件轮询（file_poll）」类 harness（走文件邮箱的桌面 Agent）能被员工工作间真正调度。

原理：
  平台把任务 JSON 写进 harness 的 inbox（wakeup_dir），本桥进程轮询 inbox：
    1) 发现新 task_*.json → 打印任务内容；可选执行 --trigger 命令"唤醒"harness（如打开目录/启动程序）
       再把文件移动到 delivered/ 避免重复处理；
    2) 轮询 inbox/replies/ 子目录：harness 干完活把回报 JSON 写进来，
       桥自动 POST 到平台的 /api/harness/activation-result 或 /api/harness/task-result。

用法（在 harness 所在机器上跑，Windows 直接 python 运行；下面的 harness_id 是虚拟示例，请换成你自己的）：
  python filepoll_harness_bridge.py --harness-id 示例Harness-A ^
      --inbox "D:\\你的目录\\inbox" ^
      --platform <平台地址>

可选：
  --trigger "start \"\" \"C:\\path\\app.exe\""   发现新任务时额外执行的唤醒命令
  --interval 3                                    轮询间隔（秒）

harness 回报两种方式任选：
  A. 直接 HTTP POST 平台（harness 有联网能力时）：
      激活：POST {platform}/api/harness/activation-result  body: {"workshop_id":"...","member_id":"...","status":"entered"}
      任务：POST {platform}/api/harness/task-result        body: {"workshop_id":"...","member_id":"...","ok":true,"result":"..."}
  B. 把回报 JSON 写到 inbox/replies/reply_<时间戳>.json（内容同 A 的 body，可加 "type":"activate"/"task_assign" 让桥自动选端点），桥负责转发。
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ★2026-10-13：平台地址**跟着 AC_PORT 走**（端口因人而异，别写死本机地址）。
def _platform_url_default():
    import os as _os
    return "http://127.0.0.1:" + str(_os.environ.get("AC_PORT") or "18920")


# ── 输出编码自保（2026-10-11 实测缺陷修复）──────────────────────────────
# 本脚本会打印 ✓ / ✗。在**中文 Windows** 上 stdout 编码随控制台码页（多为 GBK）
# ⇒ 打印这两个字符会抛 `UnicodeEncodeError: 'gbk' codec can't encode character '\u2717'`，
# **把桥进程弄死**（实测：桥起来后一自检就异常）。
# ⇒ 与其要求用户记着设 PYTHONIOENCODING，不如脚本自己把 stdout/stderr 改成
#    UTF-8 + 出错不炸（errors="replace"）。这样谁跑都不会因为这个挂掉。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — 老解释器没有 reconfigure：退回原样，不因此崩
    pass


def post_json(url: str, payload: dict) -> bool:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            print(f"[转发] POST {url} -> {resp.status} {body[:200]}", flush=True)
            return resp.status < 300
    except Exception as e:
        print(f"[转发] 失败 {url}: {e}", flush=True)
        return False


def heartbeat_loop(platform: str, harness_id: str, interval: float):
    """心跳保活：注册后让平台知道桥真实在线（而非注册即在线）。"""
    import threading
    q = urllib.parse.quote(harness_id)
    while True:
        try:
            post_json(f"{platform}/api/harness/heartbeat?harness_id={q}", {})
        except Exception:
            pass
        time.sleep(max(interval, 15))


def pick_endpoint(payload: dict, platform: str) -> str:
    t = str(payload.get("type", ""))
    if t == "task_assign" or "task" in t:
        return f"{platform}/api/harness/task-result"
    return f"{platform}/api/harness/activation-result"


def self_check(platform: str, harness_id: str, inbox: Path) -> bool:
    """启动自检：平台在线 / inbox 可写（排查 WinError 5 沙箱拦截）。"""
    ok = True
    try:
        with urllib.request.urlopen(f"{platform}/api/status", timeout=5) as r:
            print(f"[自检] 平台在线: {platform} -> HTTP {r.status}", flush=True)
    except Exception as e:
        print(f"[自检][✗] 平台不可达 {platform}: {e}", flush=True)
        ok = False
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "delivered").mkdir(parents=True, exist_ok=True)
        probe = inbox / ".bridge_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print(f"[自检] inbox 可写: {inbox} ✓（delivered/ 已就绪）", flush=True)
    except Exception as e:
        print(f"[自检][✗] inbox 不可写 {inbox}: {e}（检查 NTFS 权限/沙箱）", flush=True)
        ok = False
    return ok


def scan_tasks(inbox: Path, trigger: str, interval: float, platform: str = "", harness_id: str = ""):
    delivered = inbox / "delivered"
    delivered.mkdir(parents=True, exist_ok=True)
    for f in sorted(inbox.glob("task_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            print("=" * 60, flush=True)
            print(f"[任务] {f.name}", flush=True)
            print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)
            print("=" * 60, flush=True)
        except Exception as e:
            print(f"[任务] 解析失败 {f.name}: {e}", flush=True)
            data = None
        # 桥测试：平台测试桥通道，自动回报 ok
        if data and data.get("type") == "bridge_test" and platform and harness_id:
            test_id = data.get("test_id", "")
            ok = post_json(f"{platform}/api/harness/bridge-test-result", {
                "harness_id": harness_id,
                "test_id": test_id,
                "ok": True,
                "echo": f"filepoll-bridge alive, inbox={inbox}",
            })
            print(f"[桥测试] 已自动回报 test_id={test_id} ok={ok}", flush=True)
        target = delivered / f"{int(time.time() * 1000)}_{f.name}"
        try:
            f.rename(target)
        except Exception:
            try:
                f.replace(target)
            except Exception as e2:
                print(f"[任务] 移动失败 {f.name}: {e2}", flush=True)
        if trigger and data is not None:
            try:
                subprocess.Popen(trigger, shell=True)
                print(f"[唤醒] 已执行 trigger: {trigger}", flush=True)
            except Exception as e:
                print(f"[唤醒] trigger 失败: {e}", flush=True)


def scan_replies(inbox: Path, platform: str, interval: float):
    replies = inbox / "replies"
    sent = replies / "sent"
    if not replies.exists():
        return
    sent.mkdir(parents=True, exist_ok=True)
    for f in sorted(replies.glob("reply_*.json")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[回报] 解析失败 {f.name}: {e}", flush=True)
            payload = None
        if payload:
            url = pick_endpoint(payload, platform)
            ok = post_json(url, payload)
        else:
            ok = False
        target = (sent if ok else replies / "failed") / f"{int(time.time() * 1000)}_{f.name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            f.rename(target)
        except Exception:
            try:
                f.replace(target)
            except Exception as e2:
                print(f"[回报] 移动失败 {f.name}: {e2}", flush=True)


_LOCK_HANDLE = None          # 必须持有引用，否则文件对象被回收 ⇒ 锁被释放


def acquire_single_instance(lock_path):
    """★单实例锁（2026-10-12 新增）：同一个 inbox 只允许一只桥在看着。

    同一 inbox 起两只桥 ⇒ **重复投递 / 两个进程抢搬同一个任务文件 / 回报被转发两次**。
    （平台侧注释早就写着"同一邮箱多只桥 ⇒ 重复投递"，但**桥脚本自己没有拦**。）
    用**持锁**而非 pid 文件：崩溃留下的僵尸锁 + pid 复用会误判；
    持锁由操作系统在进程结束时自动释放，永不产生僵尸锁。
    """
    global _LOCK_HANDLE
    try:
        f = open(lock_path, "a+")
    except Exception as e:  # noqa: BLE001
        return None, "无法创建锁文件（跳过单实例保护）：%s" % e
    try:
        # ⚠️★**必须先 seek(0)**：`open(..., "a+")` 是**追加模式**，初始指针在 **EOF**；
        #   而 `msvcrt.locking` 锁的是"从当前指针开始的 N 字节"⇒ 不 seek 的话
        #   两只桥会锁在**不同字节**上、**毫无冲突**（实测踩到）。
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None, "另一只桥正在看着同一个 inbox（拒绝重复启动）"
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
    ap = argparse.ArgumentParser(description="filepoll 桥：轮询 inbox 唤醒 harness 并转发回报")
    ap.add_argument("--harness-id", required=True, help="harness 的 id（与注册一致，仅用于日志）")
    ap.add_argument("--inbox", required=True, help="wakeup_dir，平台写任务文件的目录")
    ap.add_argument("--platform", default=_platform_url_default(), help="平台地址")
    ap.add_argument("--trigger", default="", help="发现新任务时额外执行的唤醒命令（shell）")
    ap.add_argument("--interval", type=float, default=3.0, help="轮询间隔秒")
    args = ap.parse_args()

    inbox = Path(args.inbox)
    inbox.mkdir(parents=True, exist_ok=True)

    # ★单实例保护（建目录之后、开始轮询之前）：同一 inbox 两只桥会**抢搬同一个任务文件**。
    _lk, _why = acquire_single_instance(inbox / ".bridge.lock")
    if _lk is None and _why and "拒绝重复启动" in _why:
        print("[filepoll桥] ⛔ %s" % _why, flush=True)
        print("[filepoll桥] 同一个 inbox 只应有一只桥在看着；要重启请先停掉原来那只。"
              "（inbox: %s）" % inbox, flush=True)
        sys.exit(2)
    if _why:
        print("[filepoll桥] 提示: %s" % _why, flush=True)

    print(f"[filepoll桥] 开始轮询 harness「{args.harness_id}」的 inbox: {inbox}", flush=True)
    print(f"[filepoll桥] 平台: {args.platform} | trigger: {args.trigger or '(无)'}", flush=True)
    # 启动自检：平台在线 / inbox 可写
    try:
        self_check(args.platform, args.harness_id, inbox)
    except Exception as e:
        print(f"[自检] 异常: {e}", flush=True)
    import threading
    threading.Thread(target=heartbeat_loop, args=(args.platform, args.harness_id, args.interval), daemon=True).start()
    while True:
        try:
            scan_tasks(inbox, args.trigger, args.interval, args.platform, args.harness_id)
            scan_replies(inbox, args.platform, args.interval)
        except KeyboardInterrupt:
            print("[filepoll桥] 退出", flush=True)
            sys.exit(0)
        except Exception as e:
            print(f"[filepoll桥] 循环异常: {e}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
