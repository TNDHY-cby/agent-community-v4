# -*- coding: utf-8 -*-
"""凭据体检（V86 §3/§4，老板 2026-10-22 拍板：**只做体检**）。

一句话：**只看得出问题，绝不回显明文。**

设计要点（与 `design-docs/V86_凭据最小化与签名串联设计.md` 一一对应）：
- 指纹口径**一处真相**：`sha256(明文) → 前 12 位十六进制`，全项目只经 `fingerprint()`；
- **不搬明文**：明文留在原地（`.env` / 环境变量 / `data/keys/*` / 桥产物），
  我们只记**位置 + 指纹 + 强度**；搬一半比不搬更糟（会出现"两处都有一份、不知道哪份生效"）；
- 账本 `data/keys/credentials.json` **只存指纹与元信息**，删掉也能由 `scan()` 重建
  ⇒ 不是状态机，不要求用户维护；
- 扫不了的东西进 `skipped` 并给原因 —— **失败要说话**，不许静默当"没问题"。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from . import env_spec

#: 账本目录覆盖（测试/多实例用；不改 `AC_DATA_DIR` 也能隔离）
LEDGER_DIR: Optional[Path] = None

#: 「多久没换」的红线（天）。老板 2026-10-22 拍的 1 号方案里就是这个数。
STALE_DAYS = 90

#: 示例占位值（大小写不敏感）——这些"看起来填了、其实一定连不上"
PLACEHOLDERS = frozenset({
    "sk-your-key", "your-key", "yourkey", "your_key", "your-token", "your_token",
    "changeme", "change-me", "change_me", "placeholder", "example", "example-key",
    "test", "testkey", "dummy", "todo", "none", "null", "xxx", "1234", "123456",
    "password", "passwd", "secret", "token", "apikey", "api-key",
})

#: 名字像凭据才算（避免把 `AC_PORT` 这种当成密钥来体检）
_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE)", re.I)
#: 平台自己的变量前缀（不扫用户机器上其它无关的密钥：那是越界）
_OWN_PREFIX = ("AC_", "AC4_")

#: 桥产物里"像真的明文"的两种形状
_BRIDGE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"""(?i)\b(?:token|api[_-]?key|secret|password|passwd)\b\s*[=:]\s*['"]?([A-Za-z0-9_\-\.]{12,})['"]?"""),
)
_BRIDGE_EXTS = frozenset({".ps1", ".sh", ".cmd", ".bat", ".py", ".json", ".txt", ".env", ".yaml", ".yml"})
#: 这些不是"明文"（变量引用/模板占位），故意不报（判据 7 钉住它）
_NOT_LITERAL = ("$", "{{", "%(", os.sep)


def now() -> float:
    return time.time()


def _root() -> Path:
    """仓库根（`.env` / `config.local.json` 所在）。"""
    return Path(__file__).resolve().parents[2]


def ledger_dir() -> Path:
    if LEDGER_DIR is not None:
        return Path(LEDGER_DIR)
    return env_spec.data_dir() / "keys"


def ledger_path() -> Path:
    return ledger_dir() / "credentials.json"


def fingerprint(secret: str) -> str:
    """凭据指纹：**只此一处**（sha256 前 12 位十六进制）。"""
    return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()[:12]


def strength(value: str, purpose: str = "") -> tuple:
    """判"这个值像不像真能用的凭据"。返回 `(verdict, why)`；verdict ∈ ok / weak。"""
    v = value or ""
    if not v:
        return "weak", "值是空的（等于没填）"
    if v.strip().lower() in PLACEHOLDERS:
        return "weak", "这是**示例占位值**（`%s`），平台照着它一定连不上" % v.strip()
    if len(v) < 16:
        return "weak", "只有 %d 位，太短，多半是示例/临时值（一般真凭据 ≥ 16 位）" % len(v)
    if len(set(v)) == 1:
        return "weak", "所有字符都一样（像是占位）"
    if purpose and v.lower() == purpose.lower():
        return "weak", "值和变量名一样（像是占位）"
    return "ok", ""


def _secret_name(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name or ""))


# ── 扫描面 ────────────────────────────────────────────────────────────────
def _scan_env(findings: list, skipped: list) -> None:
    """① 进程环境变量里**平台自己的**凭据（不越界去翻用户机器上别的密钥）。"""
    registered = set(getattr(env_spec, "SPEC", {}) or {})
    for name, value in os.environ.items():
        if not _secret_name(name):
            continue
        if name not in registered and not name.startswith(_OWN_PREFIX):
            continue
        if not value:
            continue
        verdict, why = strength(value, name)
        findings.append({
            "purpose": name, "source": "env:%s" % name, "kind": "env",
            "fingerprint": fingerprint(value), "strength": verdict, "why": why,
        })


def _scan_dotenv(findings: list, skipped: list) -> None:
    """② 仓库根的 `.env`（用户真正会去填的那张表）。"""
    p = _root() / ".env"
    if not p.exists():
        return
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:                                   # noqa: BLE001 — 读不了就如实报
        skipped.append({"source": str(p), "why": "读不了：%s" % e})
        return
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        name, _, value = s.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if not _secret_name(name) or not value:
            continue
        verdict, why = strength(value, name)
        findings.append({
            "purpose": name, "source": ".env:%d（%s）" % (i, name), "kind": "dotenv",
            "fingerprint": fingerprint(value), "strength": verdict, "why": why,
        })


def _walk_json(node: Any, path: str = "") -> Iterable[tuple]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_json(v, "%s.%s" % (path, k) if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_json(v, "%s[%d]" % (path, i))
    else:
        yield path, node


def _scan_config(findings: list, skipped: list) -> None:
    """③ `config.local.json`（很多人把 key 填这儿）。"""
    p = _root() / "config.local.json"
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:                                   # noqa: BLE001 — 如实报，不静默
        skipped.append({"source": str(p), "why": "解析不了 JSON：%s" % e})
        return
    for path, value in _walk_json(data):
        leaf = path.split(".")[-1].split("[")[0]
        if not _secret_name(leaf) or not isinstance(value, str) or not value:
            continue
        verdict, why = strength(value, leaf)
        findings.append({
            "purpose": leaf, "source": "config.local.json:%s" % path, "kind": "config",
            "fingerprint": fingerprint(value), "strength": verdict, "why": why,
        })


def _scan_key_files(findings: list, skipped: list) -> None:
    """④ `data/keys/*`（平台自己生成的密钥文件，例如 `attest.key`）。"""
    d = ledger_dir()
    if not d.exists():
        return
    for p in sorted(d.iterdir()):
        if p.name == "credentials.json" or p.name.startswith("."):
            continue
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
        except Exception as e:                               # noqa: BLE001 — 如实报
            skipped.append({"source": str(p), "why": "读不了：%s" % e})
            continue
        if not raw.strip():
            continue
        value = raw.decode("utf-8", "replace").strip()
        verdict, why = strength(value, p.name)
        findings.append({
            "purpose": p.name, "source": "keys/%s" % p.name, "kind": "keyfile",
            "fingerprint": fingerprint(value), "strength": verdict, "why": why,
        })


def _scan_bridge_files(findings: list, skipped: list) -> None:
    """⑤ 桥产物：**明文 key 落在脚本里**是最常见的真实泄漏。"""
    d = env_spec.data_dir() / "bridges"
    if not d.exists():
        return
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _BRIDGE_EXTS:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:                               # noqa: BLE001 — 如实报
            skipped.append({"source": str(p), "why": "读不了：%s" % e})
            continue
        hits = 0
        for i, line in enumerate(text.splitlines(), 1):
            if hits >= 5:
                break
            for pat in _BRIDGE_PATTERNS:
                for m in pat.finditer(line):
                    value = m.group(1) if m.groups() else m.group(0)
                    if any(tok in value for tok in _NOT_LITERAL):
                        continue                       # 变量引用/模板占位，不是明文
                    verdict, why = strength(value, p.name)
                    findings.append({
                        "purpose": "bridge:%s" % p.name,
                        "source": "%s:%d" % (p.relative_to(env_spec.data_dir()).as_posix(), i),
                        "kind": "bridge", "fingerprint": fingerprint(value),
                        "strength": verdict, "why": why or "这里是**明文**，这份脚本外发就等于把凭据送出去",
                    })
                    hits += 1
                    break


# ── 账本 ──────────────────────────────────────────────────────────────────
def _load_ledger() -> dict:
    p = ledger_path()
    if not p.exists():
        return {"version": 1, "entries": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("entries"), list):
            return d
    except Exception:                                        # noqa: BLE001 — 有意静默：账本坏了就当空的重建
        pass
    return {"version": 1, "entries": []}


def _save_ledger(ledger: dict) -> str:
    d = ledger_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = ledger_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:                                        # noqa: BLE001 — 有意静默：Windows 上 chmod 语义有限，不影响正确性
        pass
    tmp.replace(p)
    return str(p)


def _merge(ledger: dict, findings: list) -> dict:
    """把本次体检结果并进账本：同 (source, fingerprint) 只更新时间，换值记为轮换。"""
    t = now()
    entries = ledger.setdefault("entries", [])
    by_key = {(e.get("source"), e.get("fingerprint")): e for e in entries}
    by_source: dict = {}
    for e in entries:
        by_source.setdefault(e.get("source"), e)
    for f in findings:
        key = (f["source"], f["fingerprint"])
        hit = by_key.get(key)
        if hit:
            hit["last_seen"] = t
            hit["strength"] = f["strength"]
            hit["why"] = f["why"]
            continue
        prev = by_source.get(f["source"])
        entry = {
            "purpose": f["purpose"], "source": f["source"], "kind": f["kind"],
            "fingerprint": f["fingerprint"], "strength": f["strength"], "why": f["why"],
            "first_seen": t, "last_seen": t,
            "rotated_from": prev.get("fingerprint") if prev else "",
        }
        entries.append(entry)
        by_key[key] = entry
        by_source[f["source"]] = entry
    ledger["scanned_at"] = t
    return ledger


def _entry_view(e: dict) -> dict:
    age = (now() - float(e.get("first_seen") or now())) / 86400.0
    return {
        "purpose": e.get("purpose", ""), "source": e.get("source", ""),
        "kind": e.get("kind", ""), "fingerprint": e.get("fingerprint", ""),
        "strength": e.get("strength", "ok"), "why": e.get("why", ""),
        "first_seen": e.get("first_seen"), "last_seen": e.get("last_seen"),
        "age_days": round(age, 1), "stale": age >= STALE_DAYS,
        "rotated_from": e.get("rotated_from", ""),
    }


def summary(entries: list) -> dict:
    views = [_entry_view(e) for e in entries]
    return {
        "total": len(views),
        "weak": len([v for v in views if v["strength"] == "weak"]),
        "stale": len([v for v in views if v["stale"]]),
        "plaintext_in_bridge": len([v for v in views if v["kind"] == "bridge"]),
        "stale_days": STALE_DAYS,
    }


def scan() -> dict:
    """体检一次（**不写明文、不写日志**）。返回 `{found, skipped, summary, scanned_at}`。"""
    findings: list = []
    skipped: list = []
    for step in (_scan_env, _scan_dotenv, _scan_config, _scan_key_files, _scan_bridge_files):
        try:
            step(findings, skipped)
        except Exception as e:                               # noqa: BLE001 — 一步失败不拖垮整轮，但要说话
            skipped.append({"source": step.__name__, "why": "这一步没跑完：%s" % e})
    # 去重（同一指纹同一来源只留一条）
    seen, uniq = set(), []
    for f in findings:
        key = (f["source"], f["fingerprint"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    return {"found": uniq, "skipped": skipped,
            "summary": {"found": len(uniq),
                        "weak": len([f for f in uniq if f["strength"] == "weak"]),
                        "plaintext_in_bridge": len([f for f in uniq if f["kind"] == "bridge"]),
                        "skipped": len(skipped)},
            "scanned_at": now()}


def scan_and_record() -> dict:
    """体检 + 并进账本（`POST /api/credentials/scan` 走这条）。"""
    out = scan()
    ledger = _merge(_load_ledger(), out["found"])
    out["ledger"] = _save_ledger(ledger)
    out["summary"].update({"total": len(ledger.get("entries") or []),
                           "stale": summary(ledger.get("entries") or [])["stale"]})
    return out


def overview() -> dict:
    """只读视图（`GET /api/credentials`）：账本 + 汇总 + 一句口径说明。"""
    ledger = _load_ledger()
    entries = ledger.get("entries") or []
    return {
        "summary": summary(entries),
        "entries": [_entry_view(e) for e in entries],
        "scanned_at": ledger.get("scanned_at"),
        "ledger": str(ledger_path()),
        "note": ("只存**指纹**与位置，**不存明文**；账本可删，删了 `POST /api/credentials/scan` 能重建。"
                 "明文仍在原来的地方（`.env` / 环境变量 / `data/keys/` / 桥产物），平台不搬它。"),
    }
