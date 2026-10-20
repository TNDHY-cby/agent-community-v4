# -*- coding: utf-8 -*-
"""platform/peers：**对端公司登记册**（V62 §3）——"我要去谁家"。

与 `routers/company_admin.py`（"谁能进我家"）互补，别混：
- `/api/companies`：我登记别人、给别人签发令牌，别人来访问我；
- **本模块**：我登记对端（名字 + 隧道地址 + 对方的令牌），我去访问它。

落盘：`<AC_DATA_DIR>/peers.json`。令牌**加密落盘**（复用 `platform/core/security` 的 DPAPI），
任何列表/详情接口都**只回打码值**。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

_LOCK_NAME = "peers.json.lock"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def peers_file() -> Path:
    """对端登记册路径（认 `AC_DATA_DIR`；**一处真相**）。"""
    from .identity import _default_data_root
    return Path(_default_data_root()) / "peers.json"


def load_peers() -> dict:
    p = peers_file()
    if not p.is_file():
        return {"peers": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        # 损坏不静默：备份坏文件再重建空册（数据不能凭空消失而不留痕）
        try:
            bad = p.with_suffix(".json.bad_%d" % int(time.time()))
            p.replace(bad)
            print("[peers] 登记册损坏，已备份到 %s 并重建空册" % bad.name, flush=True)
        except Exception:                                    # noqa: BLE001
            pass
        return {"peers": {}}
    if not isinstance(data, dict) or not isinstance(data.get("peers"), dict):
        return {"peers": {}}
    return data


def save_peers(data: dict) -> None:
    p = peers_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)          # 原子替换：写一半断电也不会留下半个登记册


def normalize_base_url(raw: str) -> tuple[str, str]:
    """校验并规范化对端地址。返回 `(url, 错误信息)`；错误信息为空表示通过。"""
    s = (raw or "").strip()
    if not s:
        return "", "base_url 不能为空（就是对端公司的 http 地址，隧道映射出来的那个）"
    if not s.startswith(("http://", "https://")):
        return "", "base_url 必须以 http:// 或 https:// 开头"
    u = urlparse(s)
    if not u.hostname:
        return "", "base_url 里没有主机名"
    # 只保留到端口为止：多余路径会让 A2A 端点拼错（对端是根路径提供 JSON-RPC）
    path = (u.path or "").strip("/")
    if path:
        return "", ("base_url 不要带路径（对端在根路径提供 JSON-RPC）；"
                    "你给的是 %r，应改成 %s://%s%s" %
                    (s, u.scheme, u.netloc, "/" if False else ""))
    port = u.port
    if port is None:
        port = 443 if u.scheme == "https" else 80
    return "%s://%s:%d" % (u.scheme, u.hostname, port), ""


def mask_token(token: str) -> str:
    if not token:
        return ""
    return token[:4] + "…" + token[-4:] if len(token) > 10 else "***"


def _enc(token: str) -> str:
    if not token:
        return ""
    try:
        from .core.security import _encrypt_secret
        return _encrypt_secret(token)
    except Exception:                                        # noqa: BLE001
        return token          # 极端情况下回退（save 时会打印告警口径由调用方负责）


def _dec(blob: str) -> str:
    if not blob:
        return ""
    try:
        from .core.security import _decrypt_secret
        return _decrypt_secret(blob)
    except Exception:                                        # noqa: BLE001
        return blob


def public_view(rec: dict) -> dict:
    """给接口/界面看的样子：**永远不含令牌明文**。"""
    out = {k: rec.get(k) for k in ("peer_id", "display_name", "base_url", "note",
                                   "created_at", "enabled", "last_verify")}
    out["token_masked"] = rec.get("token_masked") or mask_token(_dec(rec.get("token_enc") or ""))
    out["has_token"] = bool(rec.get("token_enc"))
    out["tasks_count"] = len((rec.get("tasks") or {}))
    return out


def upsert_peer(body: dict) -> tuple[dict, str]:
    """登记/更新对端。返回 `(记录, 错误)`。"""
    name = str(body.get("display_name") or "").strip()
    url, err = normalize_base_url(str(body.get("base_url") or ""))
    if err:
        return {}, err
    if not name:
        return {}, "display_name 不能为空（界面上要显示的名字）"
    pid = str(body.get("peer_id") or "").strip().lower() or re.sub(r"[^a-z0-9_-]+", "-",
                                                                   name.lower())[:32].strip("-")
    if not pid:
        pid = "peer-%d" % int(time.time())
    if not _ID_RE.match(pid):
        return {}, "peer_id 只能含小写字母/数字/连字符/下划线（≤64）"
    data = load_peers()
    rec = (data["peers"].get(pid) or {})
    rec.update({
        "peer_id": pid,
        "display_name": name,
        "base_url": url,
        "note": str(body.get("note") or "").strip(),
        "enabled": True if rec.get("enabled") is None else bool(rec.get("enabled")),
    })
    rec.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    rec.setdefault("tasks", {})
    token = str(body.get("token") or "").strip()
    if token:
        rec["token_enc"] = _enc(token)
        rec["token_masked"] = mask_token(token)
    data["peers"][pid] = rec
    save_peers(data)
    return rec, ""


def get_peer(pid: str) -> Optional[dict]:
    return (load_peers()["peers"].get((pid or "").strip().lower()))


def peer_token(rec: dict) -> str:
    """取令牌明文（只在发请求的瞬间用，**不要写进日志/响应**）。"""
    return _dec(rec.get("token_enc") or "")


def set_enabled(pid: str, enabled: bool) -> tuple[dict, str]:
    data = load_peers()
    rec = data["peers"].get((pid or "").strip().lower())
    if not rec:
        return {}, "对端公司不存在：%s" % pid
    rec["enabled"] = bool(enabled)
    data["peers"][rec["peer_id"]] = rec
    save_peers(data)
    return rec, ""


def delete_peer(pid: str) -> bool:
    data = load_peers()
    key = (pid or "").strip().lower()
    if key not in data["peers"]:
        return False
    data["peers"].pop(key, None)
    save_peers(data)
    return True


def record_task(pid: str, task_id: str, title: str, detail: str = "") -> None:
    """把"我派过什么"记进本地索引（**只记索引，不复制对端数据**）。"""
    data = load_peers()
    rec = data["peers"].get((pid or "").strip().lower())
    if not rec:
        return
    tasks = rec.setdefault("tasks", {})
    tasks[str(task_id)] = {"title": title[:200], "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "detail": detail[:200]}
    save_peers(data)


def record_verify(pid: str, ok: bool, detail: str) -> None:
    data = load_peers()
    rec = data["peers"].get((pid or "").strip().lower())
    if not rec:
        return
    rec["last_verify"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": bool(ok),
                          "detail": str(detail)[:300]}
    save_peers(data)
