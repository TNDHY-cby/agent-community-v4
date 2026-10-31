# -*- coding: utf-8 -*-
"""`platform/manifest`：**工作区清单（根B 的大脑）**的唯一契约（设计 `V117` §2，P2.5 批）。

## 为什么有它

根B（工作区）是**一次性**的：干活时用、验收完可整根归档。但 agent 每次挂载它都得知道
"从哪读、读什么、别碰哪"——这份 `manifest.json` 就是它唯一的入口地图。

**字段名只在这里出现一次**（对方给的权威 Schema，`coop.workshop.manifest/1`）；
以后版本演进改这里一处即可（`V117` §7 第 1 条：字段名集中管理）。

## 权威 Schema（来自《数字心智共同体总框架》2026-10-27 回函）

```jsonc
{
  "manifest_version": "coop.workshop.manifest/1",
  "workshop_id": "WS-20261027-01",
  "mind_path": "/absolute/path/to/AC_MIND_ROOT/A-01",
  "entry_points": ["_lead/建模.md", "前端组/分工.md"],
  "active_tasks": ["前端组/员工01/任务/T-001.md"],
  "ignore_zones": ["_meta/evidence/", "前端组/员工01/产物/", "_lead/历史归档/"],
  "task_focus": "当前阶段：接口联调与自测"
}
```

## G0 闸门四条（对方定稿）

1. `manifest_version` 必须匹配；
2. `mind_path` **绝不能**包含在 `<workshop_path>` 内（防嵌套：否则工作区一归档，记忆就跟着没了）；
3. `entry_points` 非空，且每个路径**真实存在**（`stat`，L0）；
4. `ignore_zones` 是给 Boot Prompt 看的黑名单（大模型禁入），路径**允许还不存在**（那是"以后可能有"）。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

#: 版本常量（对方口径：大版本变更阻断运行，小版本向下兼容）
MANIFEST_VERSION = "coop.workshop.manifest/1"
MANIFEST_FILE = "manifest.json"

#: 小版本能力：本实现认得的字段（未知字段忽略并记录，不拒收）
REQUIRED = ("manifest_version", "workshop_id", "mind_path", "entry_points")
OPTIONAL = ("active_tasks", "ignore_zones", "task_focus")


def manifest_path(workshop_path: str | Path) -> Path:
    return Path(workshop_path) / MANIFEST_FILE


def canonical(body: dict) -> str:
    """规范化 JSON（**算 hash 用**；键排序、无多余空白）—— 改一个字符 hash 就变。"""
    keep = {k: body.get(k) for k in (REQUIRED + OPTIONAL) if k in body}
    return json.dumps(keep, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(body: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def build(*, workshop_id: str, workshop_path: str, mind_path: str,
          entry_points: list, active_tasks: list | None = None,
          ignore_zones: list | None = None, task_focus: str = "") -> dict:
    """生成一份合法 manifest（**写入前不用它算 hash**：hash 由平台记账，见 `write`）。"""
    return {
        "manifest_version": MANIFEST_VERSION,
        "workshop_id": str(workshop_id),
        "mind_path": str(mind_path),
        "entry_points": [str(x) for x in (entry_points or [])],
        "active_tasks": [str(x) for x in (active_tasks or [])],
        "ignore_zones": [str(x) for x in (ignore_zones or [])],
        "task_focus": str(task_focus or ""),
    }


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def validate(body, *, workshop_path: str | Path, require_entry_exists: bool = True) -> tuple:
    """G0 校验。返回 `(ok, code, errors, four)` —— 失败时 `four` 是 409 四要素。"""
    errs = []
    wp = Path(workshop_path)

    def four(who, what, where, how):
        return {"who": who, "what": what, "where": where, "how": how}

    if not isinstance(body, dict):
        return False, "GATE_G0", [{"field": "manifest", "code": "M001",
                                   "message": "manifest 不是 JSON 对象"}], \
            four("组长", "manifest.json 读不出对象", str(manifest_path(wp)),
                 "按模板重写一份（字段：%s）" % "、".join(REQUIRED))

    missing = [k for k in REQUIRED if not str(body.get(k) if body.get(k) is not None else "").strip()
               and not isinstance(body.get(k), list)]
    if missing:
        return False, "GATE_G0", [{"field": k, "code": "M002", "message": "必填字段缺失或为空"}
                                  for k in missing], \
            four("组长", "manifest 缺必填字段：%s" % "、".join(missing), str(manifest_path(wp)),
                 "补齐这 %d 个字段后点「重新校验」" % len(missing))

    if str(body.get("manifest_version")) != MANIFEST_VERSION:
        return False, "GATE_G0", [{"field": "manifest_version", "code": "M003",
                                   "message": "版本不匹配（本机只认 %s，收到 %r）"
                                              % (MANIFEST_VERSION, body.get("manifest_version"))}], \
            four("组长", "manifest 版本太新/太旧", str(manifest_path(wp)),
                 "把 manifest_version 改成 %s（大版本不兼容需人工迁移）" % MANIFEST_VERSION)

    mind = Path(str(body.get("mind_path") or ""))
    if not str(mind).strip():
        errs.append({"field": "mind_path", "code": "M004", "message": "mind_path 为空"})
    elif _is_within(mind, wp):
        errs.append({"field": "mind_path", "code": "M005",
                     "message": "心智根被放进了工作区（%s 在 %s 之内）⇒ 工作区一归档记忆就丢了"
                                % (mind, wp)})
    if errs:
        return False, "GATE_G0", errs, four(
            "组长", errs[0]["message"], str(manifest_path(wp)),
            "把 mind_path 指到工作区之外（形如 <AC_MIND_ROOT>/<agent_key>）")

    eps = body.get("entry_points")
    if not isinstance(eps, list) or not eps:
        return False, "GATE_G0", [{"field": "entry_points", "code": "M006",
                                   "message": "entry_points 为空（agent 不知道该读什么）"}], \
            four("组长", "entry_points 是空的", str(manifest_path(wp)),
                 "至少写一条真实存在的入口（例：<岗位>/分工.md）")

    missing_files = []
    if require_entry_exists:
        for rel in eps:
            p = wp / str(rel)
            if not p.exists():
                missing_files.append(str(rel))
    if missing_files:
        return False, "GATE_G0", [{"field": "entry_points", "code": "M007",
                                   "message": "这些入口在工作区里不存在：%s" % "、".join(missing_files[:5])}], \
            four("组长", "入口文件不存在（%s）" % "、".join(missing_files[:3]), str(wp),
                 "先真建出这些文件（平台不代写），再点「重新校验」")

    return True, "", [], {}


def read(workshop_path: str | Path) -> tuple:
    """读 manifest。返回 `(ok, body_or_None, errors)`；**读不出就说清是哪个文件**。"""
    p = manifest_path(workshop_path)
    if not p.is_file():
        return False, None, [{"field": MANIFEST_FILE, "code": "M000",
                              "message": "工作区里没有 %s（%s）" % (MANIFEST_FILE, p)}]
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                     # noqa: BLE001 — 坏了要说哪个文件
        return False, None, [{"field": MANIFEST_FILE, "code": "M001",
                              "message": "%s 解析失败（%s: %s）" % (p, type(e).__name__, e)}]
    return True, body, []


def write(workshop_path: str | Path, body: dict) -> dict:
    """写 manifest + 返回平台记账用的 hash（**hash 不写进文件**，避免自指）。"""
    p = manifest_path(workshop_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return {"ok": True, "path": str(p), "manifest_hash": content_hash(body),
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
