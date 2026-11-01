# -*- coding: utf-8 -*-
"""`platform/dispatch`：**派活 = 写文件 + 追加 notice + 刷新 manifest + 三步激活 + 记账**
（设计 `V113` 第 10 步 / `V115` P3 批）。

## 一定要记住的四条

1. **任务正文只在文件里**（`<slot_dir>/任务/T-<seq>.md`），DB 只存索引与状态；
2. **`_notice.md` 只追加**（每次激活前一行，人也能看到"什么时候告诉过他"）；
3. **`manifest.active_tasks` 由平台 append**（架构师 2026-10-27 排雷①）：
   不让组长改 manifest —— 否则它改坏了 G0 就整间停工；平台 append 后重新校验，**保住 G0 持续绿通**；
4. **激活三步缺一不算激活**：写文件 → 追加 notice → 触达（A 真发请求 / B 写唤醒标记 / C 只标待唤醒）。
   `wakeup_result` 只有 `ok` / `queued` 算闭环；`manual` 必须人确认；**没配唤醒地址就说 manual + 原因，不许假装 ok**。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

TASK_TEMPLATE = """# {task_id} {title}

- 派给: {slot_dir}
- 派活人: {issued_by}
- 派活时间: {issued_at}
- 期限: {due}
- 依赖: {deps}

## 目标
{goal}

## 验收要点
{accept}

## 边界
不许改别人的目录；产物只写 `{slot_dir}/产物/`；变更先看 `{slot_dir}/_notice.md`。
"""

#: B 档"已排队"多久没被取件就降级为人工（秒）。可被环境变量覆盖。
DEFAULT_QUEUE_TIMEOUT = 600


def queue_timeout() -> int:
    import os

    try:
        # 地板 1 秒：判据要能把阈值调到几秒（默认仍是 600）
        return max(1, int(os.environ.get("AC_WAKEUP_QUEUE_TIMEOUT_SEC") or DEFAULT_QUEUE_TIMEOUT))
    except ValueError:                                         # 配错了就用默认（别让派活挂掉）
        return DEFAULT_QUEUE_TIMEOUT


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def task_relpath(slot_dir: str, task_seq: int) -> str:
    return "%s/任务/T-%03d.md" % (str(slot_dir).strip("/"), int(task_seq))


def next_task_seq(workshop_id: str) -> int:
    """按工作间记的任务号（`AC_DATA_DIR/dispatch_seq.json`），**不靠数文件**（归档会乱）。"""
    from .env_spec import data_dir as _data_dir

    p = Path(_data_dir()) / "dispatch_seq.json"
    data = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8") or "{}")
        except Exception as e:                                 # noqa: BLE001 — 读坏要说，不能从 1 重来（会撞号）
            print("[dispatch] 任务号账本读不出来（%s: %s）；本次按 1 起算，可能出现重号，请查 %s"
                  % (type(e).__name__, e, p), flush=True)
    n = int(data.get(workshop_id, 0)) + 1
    data[workshop_id] = n
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return n


def append_manifest_task(workshop_path: str, rel: str) -> dict:
    """把任务路径 append 进 `manifest.active_tasks`（**平台做**），并重算 hash。

    返回 `{ok, manifest_hash, already, error?}`；manifest 缺/坏时不硬写 —— **返回错误让调用方拒单**。
    """
    from . import manifest as _mf

    ok, body, errs = _mf.read(workshop_path)
    if not ok:
        return {"ok": False, "error": "manifest 读不出来，派活被拒（G0）", "errors": errs}
    tasks = list(body.get("active_tasks") or [])
    if rel in tasks:
        return {"ok": True, "already": True, "manifest_hash": _mf.content_hash(body)}
    tasks.append(rel)
    body["active_tasks"] = tasks
    w = _mf.write(workshop_path, body)
    return {"ok": True, "already": False, "manifest_hash": w["manifest_hash"],
            "active_tasks": tasks}


def append_notice(workshop_path: str, slot_dir: str, line: str) -> dict:
    """`_notice.md` **只追加**（不改历史）。返回写到的文件路径。"""
    f = Path(workshop_path) / str(slot_dir) / "_notice.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    head = "" if f.is_file() else "# 变更通知（只追加，最新在最上面）\n\n"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(head + "- %s | %s\n" % (_now(), line))
    return {"ok": True, "file": f.as_posix()}


# ── 激活三档 ───────────────────────────────────────────────────────
def _wakeup_endpoint(harness_id: str) -> str:
    """找唤醒地址：`wakeup_url` > `callback_url` > `api_base_url`（**都没有就返回空，不猜**）。

    真实结构（2026-10-27 实测）：注册后的 harness 在 `server.harness_manager.sessions[harness_id]`，
    该对象上有 `wakeup_url` / `callback_url`，其 `.info` 上有 `api_base_url`。
    """
    try:
        from .server import harness_manager  # type: ignore[attr-defined]
    except Exception:                                          # noqa: BLE001 — 结构变了就当真没有
        return ""
    try:
        sess = (getattr(harness_manager, "sessions", {}) or {}).get(harness_id)
        if sess is None:
            return ""
        for attr in ("wakeup_url", "callback_url"):
            v = str(getattr(sess, attr, "") or "").strip()
            if v.startswith("http"):
                return v
        info = getattr(sess, "info", None)
        for attr in ("api_base_url", "wakeup_url", "callback_url"):
            v = str(getattr(info, attr, "") or "").strip() if info is not None else ""
            if v.startswith("http"):
                return v
        cfg = getattr(sess, "wakeup_config", None) or {}
        if isinstance(cfg, dict):
            for key in ("wakeup_url", "api_base_url", "callback_url"):
                v = str(cfg.get(key) or "").strip()
                if v.startswith("http"):
                    return v
    except Exception as e:                                     # noqa: BLE001 — 读失败要说一句，不静默假睡
        print("[dispatch] 查唤醒地址失败（%s: %s）；按'没配地址'处理" % (type(e).__name__, e),
              flush=True)
    return ""


def _harness_is_a2a(harness_id: str) -> bool:
    """这个 harness 是不是按 **A2A** 接入（`wakeup_method=a2a`，或 `transports` 里声明了 a2a）。

    ★2026-11-01（老板：「加入注册功能中啊，明明是最适配的你不加」）：
    为什么要单列一条：A2A 的政策是**任务语义**——派活要发 JSON-RPC `tasks/send`（带 `message.parts`），
    对端还会回一个 task id 供 `tasks/get` 查。**混在普通 HTTP POST 里发，对端不认，
    而我们这边却会记成"已触达"** —— 那是最坏的那种假成功。
    """
    try:
        from .server import harness_manager  # type: ignore[attr-defined]
        sess = (getattr(harness_manager, "sessions", {}) or {}).get(harness_id)
        if sess is None:
            return False
        info = getattr(sess, "info", None)
        wm = getattr(info, "wakeup_method", "") if info is not None else ""
        wm = str(getattr(wm, "value", wm) or "").lower()
        if wm == "a2a":
            return True
        declared = [str(getattr(t, "value", t) or "").lower()
                    for t in (getattr(info, "transports", None) or [])]
        return "a2a" in declared
    except Exception:                                          # noqa: BLE001 — 结构变了就当不是 A2A
        return False


def _activate_via_a2a(url: str, task_id: str, task_rel: str, at: str,
                      timeout_sec: int = 30, *, report_hint: str = "",
                      context_id: str = "") -> dict:
    """按 A2A 的 `tasks/send` 派活（**不是**普通 HTTP POST）。返回与 `activate()` 同形状。

    ★2026-11-01（清单 B2）：正文里带**回报契约**（`report_hint`），让对端干完能主动回执；
    同时返回 `a2a_task_id`，交给 `a2a_result_poll` 轮询回写 —— 两条路谁先到算谁。

    ★2026-11-01（老板问「能不能自由控制同一会话 / 新开会话」）：
    A2A 用的就是 `contextId`。实测三种行为：
      · 不传 ⇒ 对端自己生成（**每次新上下文**，也就是现在的默认行为）；
      · 传同一个 ⇒ 对端回显同一个（协议上算"同一上下文"）；
      · 传新的 ⇒ 新上下文。
    ⚠️ 它控的是"A2A 上下文标识"；是否映射到客户端**同一个会话窗口**取决于对端实现。
    """
    try:
        from ..a2a_client import send_task  # 顶层包，零依赖
    except Exception as e:                                     # noqa: BLE001
        return {"result": "failed", "method": "a2a", "url": url, "at": at,
                "detail": "A2A 客户端不可用（%s: %s）" % (type(e).__name__, e)}
    title = "派活 %s：请按文件 %s 执行" % (task_id, task_rel)
    if report_hint:
        title += "\n\n" + report_hint
    try:
        res = send_task(url, title, None, timeout=timeout_sec, context_id=context_id)
        tid = str((res or {}).get("id") or (res or {}).get("taskId") or "").strip()
        task_obj = res.get("task") if isinstance(res.get("task"), dict) else (res or {})
        used_ctx = str(task_obj.get("contextId") or context_id or "").strip()
        return {"result": "ok", "method": "a2a", "url": url, "at": at,
                "a2a_task_id": tid, "a2a_context_id": used_ctx,
                "detail": "已按 A2A `tasks/send` 派出%s（平台会按 `tasks/get` 回查并把结果写回账本）"
                          % (("，对端任务 %s" % tid) if tid else "")}
    except Exception as e:                                     # noqa: BLE001 — 联网失败要如实说
        return {"result": "failed", "method": "a2a", "url": url, "at": at,
                "detail": "A2A `tasks/send` 失败：%s" % str(e)[:240]}


def activate(workshop_path: str, slot_dir: str, *, harness_id: str, tier: str,
             task_id: str, task_rel: str, timeout_sec: int = 8,
             report_hint: str = "", a2a_context_id: str = "") -> dict:
    """触达（第三步）。返回 `{result, detail, method, at, needs_human?}`。

    - A 档：`wakeup_url` 存在 ⇒ **真 POST**（超时/连不上 ⇒ `failed`，绝不记 ok）；
            没有地址 ⇒ `manual` + 原因（**不假装唤醒成功**）。
    - B 档：写 `<slot_dir>/_wakeup.flag` ⇒ `queued`（等 `poll` 结算：取走=ok / 超时=manual）。
    - C 档：`manual` + 给一句可复制的话。
    """
    at = _now()
    if tier == "b_bridge":
        flag = Path(workshop_path) / str(slot_dir) / "_wakeup.flag"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text(json.dumps({"task": task_id, "file": task_rel, "at": at},
                                   ensure_ascii=False), encoding="utf-8")
        return {"result": "queued", "method": "flag", "flag": flag.as_posix(), "at": at,
                "detail": "已写唤醒标记，等桥取件（取走后才算 ok；%d 秒没取会自动降为人工）"
                          % queue_timeout()}

    if tier == "a_protocol":
        url = _wakeup_endpoint(harness_id)
        if not url:
            return {"result": "manual", "method": "none", "at": at, "needs_human": True,
                    "detail": "这个 harness 没配唤醒地址（wakeup_url/api_base_url），"
                              "平台**不会假装叫醒它** —— 请人工去它的会话里说一句"}
        # ★2026-11-01：A2A 走自己的信封（tasks/send），不能用下面那套普通 POST
        if _harness_is_a2a(harness_id):
            return _activate_via_a2a(url, task_id, task_rel, at,
                                     timeout_sec=max(timeout_sec, 30), report_hint=report_hint,
                                     context_id=a2a_context_id)
        try:
            payload = json.dumps({"event": "task.assigned", "task_id": task_id,
                                  "file": task_rel}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=timeout_sec) as r:
                code = int(r.status)
            if 200 <= code < 300:
                return {"result": "ok", "method": "http", "url": url, "at": at,
                        "detail": "已真发唤醒请求（HTTP %d）" % code}
            return {"result": "failed", "method": "http", "url": url, "at": at,
                    "detail": "唤醒请求返回 HTTP %d（不是 2xx，不算叫醒）" % code}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"result": "failed", "method": "http", "url": url, "at": at,
                    "detail": "唤醒请求失败（%s: %s）" % (type(e).__name__, e)}

    return {"result": "manual", "method": "none", "at": at, "needs_human": True,
            "detail": "视觉型只能人工：去它的窗口说一句「看下 %s」" % task_rel}


def poll_flags(workshop_path: str, slot_dir: str, *, now: float | None = None) -> dict:
    """B 档结算：标记文件**不在了** ⇒ 桥真取走 ⇒ `ok`；还在且超时 ⇒ `manual`。"""
    flag = Path(workshop_path) / str(slot_dir) / "_wakeup.flag"
    if not flag.is_file():
        return {"state": "taken", "note": "标记已不在 ⇒ 桥取走了"}
    try:
        body = json.loads(flag.read_text(encoding="utf-8") or "{}")
    except Exception:                                          # noqa: BLE001 — 坏文件也要能结算
        body = {}
    at = str(body.get("at") or "")
    try:
        ts = time.mktime(time.strptime(at, "%Y-%m-%dT%H:%M:%S")) if at else 0.0
    except ValueError:
        ts = 0.0
    age = (now if now is not None else time.time()) - ts if ts else 0.0
    if ts and age > queue_timeout():
        return {"state": "timeout", "age_sec": int(age),
                "note": "标记放了 %d 秒还没被取走 ⇒ 降为人工（桥可能没在跑）" % int(age)}
    return {"state": "queued", "age_sec": int(age) if ts else 0,
            "note": "还在等桥取件"}
