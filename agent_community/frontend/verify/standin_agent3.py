"""Stand-in agent, reading the task the way the platform's bridge actually delivers it.

KEY FACT (read from `filepoll_harness_bridge.py::scan_tasks`):
the bridge **moves the task file into `inbox/delivered/` BEFORE it runs the trigger**:
    target = delivered / f"{ts}_{f.name}"; f.rename(target)   # ① first
    subprocess.Popen(trigger, shell=True)                      # ② then wake the agent
So by the time the agent wakes up, the task is in `delivered/`, not in `inbox/`.
An agent therefore reads its task from `inbox/delivered/` (that is also why the
platform's self-check counts `delivered/` as "picked up").

Inbox path comes from a sibling `inbox.txt` (a real harness keeps its own wakeup_dir
in its config); the trigger itself is called with NO arguments.
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

here = os.path.dirname(os.path.abspath(__file__))
cfg = os.path.join(here, "inbox.txt")
if not os.path.isfile(cfg):
    print("no inbox.txt next to the agent:", cfg)
    raise SystemExit(2)
INBOX = open(cfg, encoding="utf-8-sig").read().strip()
delivered = os.path.join(INBOX, "delivered")
replies = os.path.join(INBOX, "replies")
os.makedirs(delivered, exist_ok=True)
os.makedirs(replies, exist_ok=True)

n = 0
# ① 平台/桥把任务放这儿就绪（含刚搬进来的）
for f in sorted(os.listdir(delivered)):
    if not f.endswith(".json"):
        continue
    p = os.path.join(delivered, f)
    try:
        body = json.load(open(p, encoding="utf-8-sig"))
        pid = str(body.get("probe_id") or "")
        if pid:
            with open(os.path.join(replies, "reply_%s.json" % pid), "w", encoding="utf-8") as fh:
                json.dump({"type": "self_check_ack", "probe_id": pid,
                           "from": "stand-in-agent", "ok": True, "ts": time.time()},
                          fh, ensure_ascii=False)
            n += 1
            # 处理过就移走，避免重复回话
            os.replace(p, os.path.join(delivered, "done_" + f))
    except Exception as e:  # noqa: BLE001
        print("handle failed:", f, e)

# ② 兜底：万一任务还在 inbox（不同桥的时序可能不同）
for f in sorted(os.listdir(INBOX)):
    p = os.path.join(INBOX, f)
    if not os.path.isfile(p) or not f.startswith("task_"):
        continue
    try:
        body = json.load(open(p, encoding="utf-8-sig"))
        pid = str(body.get("probe_id") or "")
        if pid:
            with open(os.path.join(replies, "reply_%s.json" % pid), "w", encoding="utf-8") as fh:
                json.dump({"type": "self_check_ack", "probe_id": pid,
                           "from": "stand-in-agent", "ok": True, "ts": time.time()},
                          fh, ensure_ascii=False)
            n += 1
    except Exception as e:  # noqa: BLE001
        print("handle failed:", f, e)

print("inbox=%s handled: %d" % (INBOX, n))
