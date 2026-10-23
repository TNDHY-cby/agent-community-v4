# -*- coding: utf-8 -*-
"""状态快照（**自动备份 + 可恢复**）。2026-10-23 数据被清空事故后的正面补强。

## 为什么要有它

那次事故的教训不是"追到凶手就完了"：**数据被清时，没有任何一份旧快照可以退回去**。
`data/*.json` 是平台的真相，但一直是"就地覆盖"——一次错误的写盘就等于永久损失。

这里做的事很小：按间隔把**状态文件**复制到 `data/backups/state_<时间>_<原因>/`，
只保留最近 N 份。恢复用 `tools/state_restore.py`（它会先给当前状态再拍一张，别二次伤害）。

## 三条纪律

1. **只抄不解释**：快照是文件的字节复制，不做任何"清洗/合并"（抄错了也一眼能看出来）；
2. **空状态也照抄，但打标记**：真被我遇上"清空"时，快照本身就是证据（`empty: true`）；
3. **不许挡住主流程**：快照失败只打一行日志，绝不抛异常、绝不阻塞落盘。

## 环境变量（都登记在 `env_spec.py`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AC_STATE_BACKUP_MINUTES` | `30` | 多久快照一次；`0`＝关掉自动快照 |
| `AC_STATE_BACKUP_KEEP` | `20` | 保留最近多少份（超了删最旧的） |
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

#: 要快照的状态文件（与 `core_check` 的"真实数据落点"同一张表：那是真相，这是副本）
STATE_FILES = ("tasks.json", "rooms.json", "task_to_room.json", "harnesses.json",
               "workshops.json", "state_machine.json", "interjects.json")

#: 自动快照的最小间隔（秒）——防止同一分钟里被反复触发
MIN_INTERVAL_SECONDS = 60

_LOCK = threading.Lock()
_LAST_AT = 0.0
_THREAD: "threading.Thread|None" = None


def _data_dir() -> Path:
    from .env_spec import data_dir

    return Path(data_dir())


def backup_root() -> Path:
    return _data_dir() / "backups"


def interval_minutes() -> int:
    from .env_spec import get

    try:
        return max(0, int(get("AC_STATE_BACKUP_MINUTES", "30") or "30"))
    except Exception:                                        # noqa: BLE001 — 配错了按默认走，别让备份把平台搞挂
        return 30


def keep_count() -> int:
    from .env_spec import get

    try:
        return max(1, int(get("AC_STATE_BACKUP_KEEP", "20") or "20"))
    except Exception:                                        # noqa: BLE001 — 同上
        return 20


def enabled() -> bool:
    return interval_minutes() > 0


def _state_is_empty(files: list) -> bool:
    """快照里的状态是不是"空的"（用来给恢复工具/日志留标记）。"""
    for p in files:
        if p.name not in ("workshops.json", "harnesses.json", "tasks.json"):
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                    # noqa: BLE001 — 读不了就不算空，交给上层报
            return False
        if isinstance(j, dict) and j:
            return False
        if isinstance(j, list) and j:
            return False
    return True


def snapshot(reason: str = "auto") -> dict:
    """立刻拍一张快照。返回 {ok, dir, files, empty, pruned, why}（**不抛异常**）。"""
    data = _data_dir()
    src = [data / n for n in STATE_FILES]
    exists = [p for p in src if p.is_file()]
    if not exists:
        return {"ok": False, "why": "状态文件一个都不存在（还没跑过？）", "files": [], "pruned": []}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = "".join(ch for ch in (reason or "auto") if ch.isalnum() or ch in "-_")[:24] or "auto"
    dest = backup_root() / ("state_%s_%s" % (stamp, tag))
    n = 1
    while dest.exists():                                     # 同一秒两次也不许互相覆盖
        n += 1
        dest = backup_root() / ("state_%s_%s_%d" % (stamp, tag, n))
    copied, pruned = [], []
    try:
        dest.mkdir(parents=True, exist_ok=True)
        digests = {}
        for p in exists:
            dst = dest / p.name
            shutil.copy2(p, dst)
            # ★「备份」的定义是**能还原**，不是「拷过了」：立刻回读校验字节数与 sha256。
            #   实测里磁盘满/权限/杀毒软件都能让 copy2 "看起来成功"。
            digests[p.name] = _digest(p)
            if _digest(dst) != digests[p.name]:
                raise RuntimeError("快照校验失败：%s 与源文件不一致" % p.name)
            copied.append(p.name)
        meta = {"reason": tag, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "files": copied, "digests": digests,
                "empty": _state_is_empty([data / n for n in copied])}
        (dest / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        pruned = prune()
        return {"ok": True, "dir": str(dest), "files": copied, "empty": meta["empty"], "pruned": pruned}
    except Exception as e:                                   # noqa: BLE001 — 备份失败绝不影响主流程，但要说
        print("[backup] ★快照失败：%s: %s（这不影响平台运行，但这次就没有退路了）"
              % (type(e).__name__, e), flush=True)
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e), "files": copied, "pruned": pruned}


def _digest(path: Path) -> dict:
    """(字节数, sha256)——备份/还原的**真判据**。"""
    import hashlib

    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
    return {"bytes": n, "sha256": h.hexdigest()}


def verify(snap: dict) -> dict:
    """校验一份快照**是否可用**（返回 {ok, checked, bad[]}）。

    判据：`_meta.json` 里的 sha256/字节数 与快照文件当前内容逐个比对。
    为什么值得单独做：备份文件也会被"半截写入""磁盘清理""人为改"弄坏，
    真出事的那天才发现备份是坏的＝没有备份。
    """
    src = Path(snap["dir"])
    meta_path = src / "_meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001 — 没有/坏了 meta 就判不可校验，不假绿
        return {"ok": False, "verifiable": False, "checked": 0, "bad": [],
                "why": "缺 _meta.json 或读不了（%s）⇒ 无法校验完整性" % e}
    digests = meta.get("digests") or {}
    if not digests:
        return {"ok": False, "verifiable": False, "checked": 0, "bad": [],
                "why": "_meta.json 里没有 digests（老版本快照，无法校验；不代表它是坏的）"}
    bad = []
    for name, want in digests.items():
        f = src / name
        if not f.is_file():
            bad.append("%s（快照里没有这个文件）" % name)
            continue
        got = _digest(f)
        if got["bytes"] != want.get("bytes") or got["sha256"] != want.get("sha256"):
            bad.append("%s（%s 字节/%s 现在 %s 字节/%s）"
                       % (name, want.get("bytes"), str(want.get("sha256"))[:12],
                          got["bytes"], got["sha256"][:12]))
    return {"ok": not bad, "verifiable": True, "checked": len(digests), "bad": bad,
            "why": "" if not bad else "内容与记录不一致"}


def copy_into_place(snap: dict, data: Path) -> dict:
    """把快照里的文件**还原到数据目录**（返回 {ok, restored[], bad[]}）。

    调用前请自行确认三件事：服务已停、快照已 `verify` 通过、空快照是否允许。
    这里只做"复制 + 回读校验"，不做判断、不删任何东西（多余文件一概不动）。
    """
    src = Path(snap["dir"])
    restored, bad = [], []
    for name in snap["files"]:
        s = src / name
        d = data / name
        try:
            data.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            if _digest(d) != _digest(s):
                bad.append("%s（还原后校验不一致）" % name)
            else:
                restored.append(name)
        except Exception as e:                               # noqa: BLE001 — 逐个报，别半途静默
            bad.append("%s（%s: %s）" % (name, type(e).__name__, e))
    return {"ok": not bad, "restored": restored, "bad": bad}


def list_snapshots() -> list:
    """列出已有快照（新的在前）。每项：dir/name/at/files/bytes/empty。"""
    root = backup_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.glob("state_*"), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        files = [p for p in d.iterdir() if p.is_file() and p.name != "_meta.json"]
        meta = {}
        mf = d / "_meta.json"
        if mf.is_file():
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:                                # noqa: BLE001 — meta 坏了不影响恢复（文件才是真相）
                meta = {}
        out.append({"name": d.name, "dir": str(d), "at": meta.get("at") or "",
                    "reason": meta.get("reason") or "", "files": sorted(p.name for p in files),
                    "bytes": sum(p.stat().st_size for p in files if p.exists()),
                    "empty": bool(meta.get("empty"))})
    return out


def prune(keep: int | None = None) -> list:
    """只保留最近 N 份，返回被删掉的目录名（新的在前，超出的从最旧开始删）。"""
    keep = keep_count() if keep is None else max(1, int(keep))
    snaps = list_snapshots()
    doomed = snaps[keep:]
    removed = []
    for s in doomed:
        try:
            shutil.rmtree(s["dir"])
            removed.append(s["name"])
        except Exception as e:                               # noqa: BLE001 — 删不掉只说，不影响别的
            print("[backup] 旧快照删不掉 %s（%s）" % (s["name"], e), flush=True)
    return removed


def maybe_snapshot(reason: str = "auto") -> dict:
    """距上次超过 `AC_STATE_BACKUP_MINUTES` 才拍（节流；线程安全）。"""
    global _LAST_AT
    if not enabled():
        return {"ok": False, "why": "自动快照已关闭（AC_STATE_BACKUP_MINUTES=0）"}
    with _LOCK:
        now = time.time()
        if _LAST_AT and (now - _LAST_AT) < MIN_INTERVAL_SECONDS:
            return {"ok": False, "why": "刚拍过（节流窗口内）"}
        if _LAST_AT and (now - _LAST_AT) < interval_minutes() * 60:
            return {"ok": False, "why": "还没到间隔（%d 分钟）" % interval_minutes()}
        res = snapshot(reason)
        if res.get("ok"):
            _LAST_AT = now
        return res


def start_periodic(check_seconds: int = 60) -> bool:
    """起一个后台线程：每 `check_seconds` 看一眼该不该拍快照。已起过就返回 False。"""
    global _THREAD, _LAST_AT
    if not enabled():
        print("[backup] 自动快照关闭（AC_STATE_BACKUP_MINUTES=0）", flush=True)
        return False
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        # 启动时外面通常已拍过一张 "startup" 快照 ⇒ 把节流起点对齐，
        # 否则循环第一圈会立刻再拍一张内容相同的（实测出现过同一秒两份）。
        if not _LAST_AT:
            _LAST_AT = time.time()

        def _loop():
            while True:
                try:
                    maybe_snapshot("auto")
                except Exception as e:                       # noqa: BLE001 — 循环里不许死
                    print("[backup] 自动快照循环出错（继续跑）：%s: %s" % (type(e).__name__, e), flush=True)
                time.sleep(max(30, int(check_seconds)))

        t = threading.Thread(target=_loop, name="ac-state-backup", daemon=True)
        t.start()
        _THREAD = t
    print("[backup] 自动快照已开：每 %d 分钟一份，保留最近 %d 份（%s）"
          % (interval_minutes(), keep_count(), backup_root()), flush=True)
    return True
