# -*- coding: utf-8 -*-
"""platform/bridge_templates：**多条**「具体桥模板」（V35/V36 的 ①→② 改造）。

设计稿：`design-docs/V35_注册与具体桥模型改版设计.md`（已拍板）
契约：`design-docs/V36_桥模型接口与数据契约.md`（§2.1）

**为什么新开一个模块而不是改 `bridge_template.py`**：

    bridge_template.py  = **L2 默认模板**（V-15 产物）：按 `wakeup_method` **自动派生一条**，
                          存在 `info.metadata["bridge_template"]`（**单数**）。它是"平台替你猜一条"。
    本模块               = **用户/接入方可维护的多条"具体桥模板"**，存在 `info.metadata["bridge_templates"]`
                          （**复数，列表**）。名字、落点路径、匹配条件都能手填。

    两者**并存不冲突**：自动派生那条继续作为"没有手填模板时的兜底"（V36 §4 选择顺序）。

**键名约定（★别写错）**：单数 `bridge_template` = 旧的自动派生；复数 `bridge_templates` = 本模块的列表。

**迁移（V36 §4.1）**：读到"有单数、没有复数"时，把单数**包成单元素列表**（`source="auto"`，
`name=DEFAULT_NAME`）。**原单数键保留不删** ⇒ 旧读取方（`bridge_template.get()` 等）不受影响，
既能向后兼容，又能被本模块统一看到。
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

# 复数列表（本模块的存储位置）
METADATA_KEY = "bridge_templates"
# 单数（V-15 自动派生那条；迁移时读它）
LEGACY_METADATA_KEY = "bridge_template"

#: 迁移过来的那条的显示名（也用作"没有名字"时的缺省名）
DEFAULT_NAME = "默认（自动派生）"

#: 形状参数允许值 —— 与 `agent_community/bridge_templates/` 下的目录名一致（V35 §2 的 4 种传输层）
KINDS = ("cli_acp", "file_poll", "pending_poll", "script_framework")
#: 来源标记
SOURCE_AUTO, SOURCE_USER, SOURCE_AI = "auto", "user", "ai"

_NAME_MAX = 64
#: 模板名会进目录名（`bridges/<harness>/<name>/`）⇒ 必须拦路径穿越与保留字符
_FORBIDDEN = set('\\/:*?"<>|')


class BridgeTemplateError(ValueError):
    """模板校验失败（端点层映射成 400 + 中文 error）。"""


def _now() -> float:
    return time.time()


def _safe_name(name: str) -> str:
    """校验模板名：非空、≤64、不含路径分隔符与 Windows 保留字符、非 `.`/`..`。"""
    n = (name or "").strip()
    if not n:
        raise BridgeTemplateError("模板名不能为空")
    if len(n) > _NAME_MAX:
        raise BridgeTemplateError(f"模板名过长（最多 {_NAME_MAX} 字）")
    if n in (".", ".."):
        raise BridgeTemplateError("模板名不能是 . 或 ..")
    bad = sorted({c for c in n if c in _FORBIDDEN})
    if bad:
        raise BridgeTemplateError(f"模板名不能含这些字符：{' '.join(bad)}")
    if any(ord(c) < 32 for c in n):
        raise BridgeTemplateError("模板名不能含控制字符")
    return n


def _normalize_match(match: Any) -> dict:
    """匹配条件规范化。

    ★老板裁定 2：**缺省 = 全局可用**（`workshop`/`role` 都是 None）——
    所以这里**只做规范化，不做"必须指定"的校验**（缺省合法）。
    """
    if not isinstance(match, dict):
        return {"workshop": None, "role": None}
    ws = match.get("workshop")
    role = match.get("role")
    return {
        "workshop": str(ws).strip() or None if ws is not None else None,
        "role": str(role).strip() or None if role is not None else None,
    }


def _normalize_params(params: Any) -> dict:
    if not isinstance(params, dict):
        return {}
    return {str(k): ("" if v is None else str(v)) for k, v in params.items()}


def _normalize_one(raw: Any, *, default_source: str = SOURCE_USER) -> dict:
    """把一条原始 dict 规范成契约形状；**不校验 name**（列表里可能存在历史脏名）。"""
    if not isinstance(raw, dict):
        raise BridgeTemplateError("模板必须是 JSON 对象")
    name = str(raw.get("name") or "").strip() or DEFAULT_NAME
    kind = str(raw.get("kind") or "").strip() or "pending_poll"
    if kind not in KINDS:
        raise BridgeTemplateError(f"kind 不支持：{kind}（可用：{' / '.join(KINDS)}）")
    params = _normalize_params(raw.get("params"))
    missing = raw.get("missing")
    if not isinstance(missing, list):
        # 没给就按"参数是否为空"算一个（失败要说话），不假装完整
        missing = [k for k, v in params.items() if not str(v).strip()] if params else []
    # ★2026-10-13：**"已验证"必须存在模板本体上**（V42 F3 定稿要求的
    #   `verified` / `verified_at` / `verified_by` / `evidence`）。
    #   此前这些字段只在"验证历史"列表里，模板本体上没有 ⇒ 表现为：
    #   · 模板跑通后**仍然是"草稿"**、没有验证时间可显示；
    #   · 界面无法按 V42 §130 的要求说"这条还没验证过"（字段根本不存在）；
    #   · 谁也不能靠模板本体判断"它到底验过没有"。
    #   ⇒ 落到契约里，缺省 `verified=False`（**没验过就说没验过**，不靠缺省值暗示）。
    _va = raw.get("verified_at")
    return {
        "name": name,
        "path": str(raw.get("path") or "").strip(),
        "kind": kind,
        "params": params,
        "match": _normalize_match(raw.get("match")),
        "source": str(raw.get("source") or default_source),
        "complete": bool(raw.get("complete", not missing)),
        "missing": [str(x) for x in missing],
        "verified": bool(raw.get("verified", False)),
        "verified_at": (float(_va) if _va else None),
        "verified_by": str(raw.get("verified_by") or ""),
        "evidence": dict(raw.get("evidence") or {}),
        "created_at": float(raw.get("created_at") or _now()),
        "updated_at": float(raw.get("updated_at") or raw.get("created_at") or _now()),
    }


def _legacy_to_one(legacy: Any) -> Optional[dict]:
    """把旧的单数 `bridge_template`（V-15 `build()` 的形状）转成列表里的一条。

    旧形状：`{template, wakeup_method, artifact_kind, params, required, missing, complete, ...}`
    —— 没有 `name`/`path`/`match`，这里补上（`source="auto"`，标记为自动派生）。
    """
    if not isinstance(legacy, dict) or not legacy:
        return None
    kind = str(legacy.get("template") or "").strip()
    if kind not in KINDS:
        # 无桥型（template=None，走"连接契约/提示词"那条）⇒ 没有可复制的桥模板，不迁进列表
        return None
    return _normalize_one(
        {
            "name": DEFAULT_NAME,
            "path": "",
            "kind": kind,
            "params": legacy.get("params") or {},
            "match": {"workshop": None, "role": None},
            "source": SOURCE_AUTO,
            "complete": bool(legacy.get("complete")),
            "missing": legacy.get("missing") or [],
            "created_at": legacy.get("created_at") or _now(),
        },
        default_source=SOURCE_AUTO,
    )


def _meta_of(info: Any) -> dict:
    meta = getattr(info, "metadata", None)
    if not isinstance(meta, dict):
        info.metadata = {}
        meta = info.metadata
    return meta


def list_all(info: Any, *, migrate: bool = True) -> list:
    """列出该 harness 的全部具体桥模板，**并把旧单数键迁移进来**。

    `migrate=False` 只读不迁移（给纯查询用；一般不用）。
    """
    meta = _meta_of(info)
    raw = meta.get(METADATA_KEY)
    out: list = []
    if isinstance(raw, list):
        for item in raw:
            try:
                out.append(_normalize_one(item, default_source=SOURCE_AUTO))
            except BridgeTemplateError:
                continue          # 历史脏条目跳过，不让它把整表读崩
    if migrate and not out:
        legacy = _legacy_to_one(meta.get(LEGACY_METADATA_KEY))
        if legacy is not None:
            meta[METADATA_KEY] = [legacy]
            # ★原单数键保留：旧读取方（bridge_template.get / status_of）不受影响
            return [legacy]
    # 列表非空时**不再**迁移旧单数键：它只是"自动派生那条"的历史残留，
    # 若再包进来会与用户手填的条目重复（迁移只在"还没有列表"时发生一次）。
    return out


def get(info: Any, name: str) -> Optional[dict]:
    """按名字取一条（名字比较去首尾空格；大小写敏感——是路径的一部分）。"""
    want = (name or "").strip()
    for t in list_all(info):
        if t.get("name") == want:
            return t
    return None


def default_one(info: Any) -> Optional[dict]:
    """没有显式指定模板、也没有工作间/职位匹配（`pick_for` 未命中）时的挑选。

    ⚠️★2026-10-13（V44 #11）：原实现是 `return items[0]` ——**只取列表第一条**，
    既没有"唯一"这个前提，也不分**草稿 / 已验证**。后果：一个 harness 名下同时有
    `source=draft, verified=False` 的草稿与 `verified=True` 的具体模板时，**只要草稿排在前面**，
    省略 `template` 的调用就会拿到**没验证过的那条**，而调用方只会看到 `template_source`，
    用户基本不会去核。
    ⇒ 选序改为：**① 已验证的（多条取 `verified_at` 最新）→ ② 唯一模板 → ③ 第一条**。
    另：草稿在返回值里保留 `verified=False`，调用方应据此提示"这条还没验证过"。
    """
    items = list_all(info)
    if not items:
        return None
    verified = [t for t in items if t.get("verified")]
    if verified:
        verified.sort(key=lambda t: float(t.get("verified_at") or 0), reverse=True)
        return verified[0]
    if len(items) == 1:
        return items[0]
    return items[0]


def pick_for(info: Any, *, ws: str = "", role: str = "") -> Optional[dict]:
    """按匹配条件挑一条（V36 §4 选择顺序的 ②）。

    匹配规则（老板裁定 2：**缺省 = 全局可用**）：
      · `match.workshop` 为 None ⇒ 任意工作间都算命中；为具体 ws_id 时**必须相等**；
      · `match.role` 同理（但与 workshop 是"与"关系）；
      · 两条都 None ⇒ 全局模板。
    命中多条时：**限定越具体越优先**（workshop+role 都限 > 只限一个 > 全局）。
    """
    items = list_all(info)
    if not items:
        return None
    scored = []
    for t in items:
        m = t.get("match") or {}
        mw, mr = m.get("workshop"), m.get("role")
        if mw is not None and mw != (ws or ""):
            continue
        if mr is not None and mr != (role or ""):
            continue
        score = (1 if mw is not None else 0) + (1 if mr is not None else 0)
        scored.append((score, t))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def put(info: Any, template: dict, *, source: str = SOURCE_USER) -> dict:
    """新增/覆盖一条（**同名即覆盖**）。返回该条。

    ⚠️ 覆盖**只动列表里的这一条**，不动磁盘上的桥产物 ——
    产物备份是 `bridges/<harness>/<name>/` 落盘时的责任（V36 §2.3）。
    """
    name = _safe_name(str((template or {}).get("name") or ""))
    one = _normalize_one({**(template or {}), "name": name}, default_source=source)
    meta = _meta_of(info)
    items = list_all(info)
    now = _now()
    for i, t in enumerate(items):
        if t.get("name") == name:
            one["created_at"] = t.get("created_at") or now   # 覆盖时保留创建时间
            one["updated_at"] = now
            items[i] = one
            meta[METADATA_KEY] = items
            return one
    one["created_at"] = now
    one["updated_at"] = now
    items.append(one)
    meta[METADATA_KEY] = items
    return one


def delete(info: Any, name: str) -> bool:
    """删一条模板。**不删已架起的桥产物**（V36 §3.1）。返回是否删到了。"""
    want = (name or "").strip()
    meta = _meta_of(info)
    items = list_all(info)
    kept = [t for t in items if t.get("name") != want]
    if len(kept) == len(items):
        return False
    meta[METADATA_KEY] = kept
    return True


def status_of(info: Any) -> dict:
    """给注册响应/界面用的摘要（对齐 `bridge_template.status_of` 的形状，便于前端复用）。"""
    items = list_all(info)
    legacy = getattr(info, "metadata", {}) or {}
    return {
        "count": len(items),
        "names": [t["name"] for t in items],
        "has_any": bool(items),
        "has_legacy_singular": bool(legacy.get(LEGACY_METADATA_KEY)),
        "incomplete": [t["name"] for t in items if not t.get("complete")],
    }


def looks_like_path(value: str) -> bool:
    """粗判一个字符串像不像"落点路径"（给草稿/校验用，不参与强校验）。"""
    v = (value or "").strip()
    return bool(re.match(r"^([A-Za-z]:[\\/]|\\\\|/|\.\.?[\\/])", v))
