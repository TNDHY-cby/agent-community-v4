# -*- coding: utf-8 -*-
"""可验证附属记录（V83）：签名 / 扫描 / 审计，**只追加**。

⚠️ 签名是**平台自签**（HMAC-SHA256），只证明"这台平台签过、之后没被改"，
**不是** CA/公钥信任链；接口一律带 `trust="platform-self"`，对外别吹成"第三方认证"。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

KINDS = ("signature", "scan", "audit")
SUBJECTS = ("harness", "task")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def root_dir() -> Path:
    from .env_spec import data_dir

    d = Path(data_dir()) / "attestations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def key_path() -> Path:
    from .env_spec import data_dir

    d = Path(data_dir()) / "keys"
    d.mkdir(parents=True, exist_ok=True)
    return d / "attest.key"


def signing_key() -> bytes:
    """签名密钥：优先 `AC_ATTEST_KEY`；否则自动生成并落盘（首次）。"""
    env = (os.environ.get("AC_ATTEST_KEY") or "").strip()
    if env:
        return env.encode("utf-8")
    p = key_path()
    if p.exists():
        try:
            return p.read_bytes()
        except OSError:                                       # noqa: BLE001 — 有意静默：读不出旧密钥就重新生成一个（会在下面写回）
            pass
    k = uuid.uuid4().hex.encode("utf-8") * 2
    try:
        p.write_bytes(k)
        try:
            os.chmod(p, 0o600)
        except OSError:                                       # noqa: BLE001 — 有意静默：Windows 上 chmod 语义有限，收紧失败不影响签名可用
            pass
    except OSError:                                           # noqa: BLE001 — 有意静默：密钥文件写不下时退回"进程内临时密钥"，签名仍可用但重启后验签会失败（调用方会看到）
        pass
    return k


def canonical(payload: dict) -> bytes:
    """规范化字节串（键排序、无空格），签名与验签必须用同一份。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign(payload: dict) -> str:
    return hmac.new(signing_key(), canonical(payload), hashlib.sha256).hexdigest()


def verify(record: dict) -> dict:
    got = str(record.get("signature") or "")
    body = {k: v for k, v in record.items() if k != "signature"}
    want = sign(body)
    ok = bool(got) and hmac.compare_digest(got, want)
    return {"valid": ok, "trust": "platform-self",
            "why": "" if ok else "签名不匹配：记录内容被改过，或不是这台平台签的"}


def _subject_dir(subject_type: str, subject_id: str) -> Path:
    if subject_type not in SUBJECTS:
        raise ValueError("subject_type 只支持：%s" % "、".join(SUBJECTS))
    d = root_dir() / subject_type / str(subject_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def scan(subject_type: str, subject_id: str, info=None) -> dict:
    """内置确定性扫描（不装第三方扫描器）。`info` 给 harness 对象最好；没有就只报"判不了"。"""
    items = []
    if subject_type == "harness" and info is not None:
        from . import card_conformance as cc
        from . import reply_probe as rp

        card = cc.check_card(info)
        items.append({"item": "card_conformance", "passed": card["conformance"] == "passed",
                      "why": "" if card["conformance"] == "passed" else
                             "；".join("%s %s" % (e["field"], e["why"]) for e in card["conformance_errors"][:3])})

        md = getattr(info, "metadata", None) or {}
        probe = md.get("reply_probe") if isinstance(md, dict) else None
        if isinstance(probe, dict) and probe.get("state") == "ok":
            stale = rp.is_stale(probe)
            items.append({"item": "reply_probe", "passed": not stale,
                          "why": "验过但已过期" if stale else ""})
        else:
            items.append({"item": "reply_probe", "passed": False,
                          "why": "没有验过它能不能回话（unverified）"})

        bt = md.get("bridge_test") if isinstance(md, dict) else None
        items.append({"item": "bridge_test", "passed": bool(isinstance(bt, dict) and bt.get("ok")),
                      "why": "" if isinstance(bt, dict) and bt.get("ok") else "没有桥测试通过记录"})
    else:
        items.append({"item": "scan", "passed": False,
                      "why": "这类主体还没有内置扫描项（当前只对 harness 有）"})
    return {"items": items, "passed": all(i["passed"] for i in items)}


def append(subject_type: str, subject_id: str, kind: str, *, info=None,
           note: str = "", actor: str = "user", att_id: str = "") -> dict:
    """追加一条记录（**同 id 不许覆盖**）。返回记录本身。"""
    if kind not in KINDS:
        raise ValueError("kind 只支持：%s" % "、".join(KINDS))
    d = _subject_dir(subject_type, subject_id)
    aid = att_id or ("att-" + uuid.uuid4().hex[:12])
    target = d / ("%s.json" % aid)
    if target.exists():
        raise FileExistsError("记录 %s 已存在（只追加，不许覆盖；要改就新增一条）" % aid)

    body = {"att_id": aid, "kind": kind, "subject_type": subject_type,
            "subject_id": str(subject_id), "at": _now(), "actor": str(actor)[:60],
            "note": str(note)[:300], "trust": "platform-self"}
    if kind == "scan":
        body["result"] = scan(subject_type, subject_id, info)
    rec = dict(body)
    rec["signature"] = sign(body)
    target.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def list_of(subject_type: str, subject_id: str) -> list:
    d = root_dir() / subject_type / str(subject_id)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8", errors="replace")))
        except Exception:                                    # noqa: BLE001 — 有意静默：坏记录跳过（单条 GET 会如实 404）
            continue
    return out


def get_one(subject_type: str, subject_id: str, att_id: str):
    d = root_dir() / subject_type / str(subject_id)
    p = d / ("%s.json" % att_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:                                        # noqa: BLE001 — 有意静默：坏记录当作没有（调用方拿 404，不编内容）
        return None
