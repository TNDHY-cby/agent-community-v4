# -*- coding: utf-8 -*-
"""`platform/receipt`：**外端回执的唯一解析器**（设计 `V110`，P1 批）。

## 为什么只有这一份

三种投递（A 协议 / B 桥文件 / C 人工粘贴）送进来的东西**必须长得一样**，
否则每个适配器各写一套解析，就会出现"桥认、粘贴不认"这种两套口径（历史上真发生过）。

## 报文文法（`V110` §1）

```
#回执 v1
类型: 名单|派活|验收|状态
工作间: WS-…
成员: 前端组/员工01
凭据: <token 或 空>
序号: 12
---
<body 1..n，一行一条，字段用 | 分隔，字段名固定>
---
行数: n
```

## 硬规则

1. 头 5 行必填，缺一行**整单拒**；
2. **整单原子**：任一行解析失败 ⇒ 整单拒，报"第 X 行第 Y 字段"，不做部分写入；
3. 未知字段忽略并记日志；未知类型直接拒；
4. **任务正文不进球**：派活只传索引。

错误码与 `fix`（怎么改）见 `V110` §2。
"""
from __future__ import annotations

import hashlib
import re

VERSION_LINE = "#回执 v1"
HEAD_FIELDS = ("类型", "工作间", "成员", "凭据", "序号")
TYPE_MAP = {"名单": "roster", "派活": "dispatch", "验收": "accept", "状态": "state"}
BODY_FIELDS = {
    "roster": ("岗位", "harness", "model", "人数"),
    "dispatch": ("任务", "派给", "标题", "期限"),
    "accept": ("验收", "结果", "产物", "备注"),
    "state": ("状态", "新", "原因"),
}
#: 验收体**可选**第 5 列「证据」（认知证据等级，`V116` §15 / 对方 2026-10-27 答案 3）
ACCEPT_OPTIONAL_TAIL = ("证据",)
EVIDENCE_LEVELS = ("L0", "L1", "L2", "L4")   # ★L3（大模型自报）**永不采信**
ACCEPT_RESULTS = ("通过", "返工", "拒收")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _err(line: int, field: str, code: str, message: str, fix: str) -> dict:
    return {"line": line, "field": field, "code": code, "message": message, "fix": fix}


def parse(text: str) -> dict:
    """解析一份回执。

    返回 `{ok, receipt, errors, raw_hash}`；`ok=False` 时 `receipt=None`（整单拒）。
    """
    raw = (text or "").replace("\ufeff", "")
    raw_hash = sha256_text(raw)
    if not raw.strip():
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(1, "首行", "R001", "报文是空的",
                                "从 `#回执 v1` 开始抄一份（对接单里有一份模板）")]}
    lines = [ln.rstrip("\r") for ln in raw.split("\n")]
    # 去掉尾部空行（人粘贴时常带）
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or lines[0].strip() != VERSION_LINE:
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(1, "首行", "R001",
                                "首行不是 `#回执 v1`（实际是：%r）" % (lines[0][:40] if lines else ""),
                                "第一行必须逐字 `#回执 v1`，前面不能有空格、不能有 BOM")]}

    # ── 头（第二个 `---` 之前）──
    try:
        head_end = next(i for i, ln in enumerate(lines) if ln.strip() == "---")
    except StopIteration:
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(1, "分隔线", "R002", "找不到第一个 `---`（头没结束）",
                                "头 5 行之后写一行 `---`，再写体，再写一行 `---` 和 `行数: n`")]}
    head_lines = lines[1:head_end]
    head = {}
    for idx, ln in enumerate(head_lines, start=2):
        if not ln.strip():
            continue
        if ":" not in ln:
            return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                    "errors": [_err(idx, "头", "R002", "头里这一行没有 `:`：%r" % ln[:40],
                                    "头每行都是 `字段: 值` 的形状")]}
        k, v = ln.split(":", 1)
        head[k.strip()] = v.strip()
    missing = [f for f in HEAD_FIELDS if f not in head]
    if missing:
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(1, missing[0], "R002", "头缺这 %d 行：%s" % (len(missing), "、".join(missing)),
                                "补齐这几行：%s（`凭据` 没有就写空值，行不能少）"
                                % "、".join(missing))]}
    type_cn = head["类型"]
    if type_cn not in TYPE_MAP:
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(1, "类型", "R003", "类型不认识：%r" % type_cn,
                                "只能是：名单 / 派活 / 验收 / 状态")]}
    try:
        seq = int(head["序号"])
    except ValueError:
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(1, "序号", "R002", "序号不是整数：%r" % head["序号"],
                                "序号写一个整数，例如 `序号: 13`（每次比上一个大）")]}

    # ── 尾（第二个 `---` 与 `行数:`）──
    try:
        body_end = next(i for i in range(head_end + 1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(head_end + 1, "分隔线", "R002", "找不到第二个 `---`（体没结束）",
                                "体写完之后另起一行写 `---`，下一行写 `行数: n`")]}
    tail = [ln for ln in lines[body_end + 1:] if ln.strip()]
    if not tail:
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(body_end + 2, "行数", "R002", "尾缺 `行数:` 这一行",
                                "最后一行写 `行数: <体行数>`")]}
    m = re.match(r"^行数\s*:\s*(\d+)\s*$", tail[0])
    if not m:
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(body_end + 2, "行数", "R002", "尾那行不是 `行数: <int>`：%r" % tail[0][:40],
                                "写成 `行数: 2` 这种")]}
    declared = int(m.group(1))

    # ── 体（整单原子）──
    body_lines = [(i + 1, lines[i]) for i in range(head_end + 1, body_end) if lines[i].strip()]
    if declared != len(body_lines):
        return {"ok": False, "receipt": None, "raw_hash": raw_hash,
                "errors": [_err(body_end + 2, "行数", "R005",
                                "`行数:` 写 %d，实际有 %d 条体" % (declared, len(body_lines)),
                                "把 `行数:` 改成 %d（或补/删体行）" % len(body_lines))]}
    rtype = TYPE_MAP[type_cn]
    want = BODY_FIELDS[rtype]
    allow_tail = ACCEPT_OPTIONAL_TAIL if rtype == "accept" else ()
    rows, errors = [], []
    for lineno, ln in body_lines:
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) == len(want) + len(allow_tail) and allow_tail:
            if parts[-1].split(":", 1)[0].strip() != allow_tail[0]:
                errors.append(_err(lineno, allow_tail[0], "R004",
                                   "第 %d 段应为 `证据: L0|L1|L2|L4`，收到 %r"
                                   % (len(parts), parts[-1][:30]),
                                   "可选第 5 段写成 `证据: L2`（L3 不许用）"))
                continue
        elif len(parts) != len(want):
            # 多半是文本里写了半角竖线
            code = "R009" if len(parts) > len(want) else "R004"
            errors.append(_err(lineno, want[min(len(parts), len(want) - 1)], code,
                               "这一行被 `|` 切成了 %d 段，应该是 %d 段" % (len(parts), len(want)),
                               "文本里如果要写竖线，用全角 `｜`；字段仍是 `%s`"
                               % " | ".join(want)))
            continue
        row, bad = {}, None
        for name, seg in zip(want, parts):
            if ":" not in seg:
                bad = _err(lineno, name, "R004", "这一段没有 `字段: 值`：%r" % seg[:40],
                           "每段都要 `字段: 值`，字段名逐字用：%s" % "、".join(want))
                break
            k, v = seg.split(":", 1)
            if k.strip() != name:
                bad = _err(lineno, name, "R004",
                           "第 %d 段的字段名是 %r，应为 %r" % (want.index(name) + 1, k.strip(), name),
                           "字段名与顺序固定：%s" % " | ".join(want))
                break
            row[name] = v.strip()
        if bad:
            errors.append(bad)
            continue
        if rtype == "roster":
            try:
                row["人数"] = int(row["人数"])
            except ValueError:
                errors.append(_err(lineno, "人数", "R004", "人数不是整数：%r" % row["人数"],
                                   "写一个整数（默认 1）"))
                continue
            if row["人数"] < 1:
                errors.append(_err(lineno, "人数", "R004", "人数必须 ≥ 1",
                                   "至少写 1；这里不是\"有没有人\"的地方"))
                continue
        if rtype == "accept" and allow_tail and len(parts) == len(want) + 1:
            tail_k, tail_v = parts[-1].split(":", 1)
            lvl = tail_v.strip().upper()
            if lvl not in EVIDENCE_LEVELS:
                if lvl in ("L3", "L3自报"):
                    errors.append(_err(lineno, "证据", "R011",
                                       "证据等级 L3（大模型自报）**永不采信**",
                                       "换成 L0（平台实做）/ L1（它库已有）/ L2（留痕）/ L4（人眼确认）"))
                else:
                    errors.append(_err(lineno, "证据", "R004",
                                       "证据等级不认识：%r" % tail_v,
                                       "只能是 L0 / L1 / L2 / L4（L3 不许用）"))
                continue
            row["证据"] = lvl
        if rtype == "accept" and row["结果"] not in ACCEPT_RESULTS:
            errors.append(_err(lineno, "结果", "R004", "结果只能是 通过/返工/拒收（实际 %r）" % row["结果"],
                               "写 `结果: 通过` 或 `结果: 返工` 或 `结果: 拒收`"))
            continue
        if rtype == "accept":
            try:
                row["产物"] = int(row["产物"])
            except ValueError:
                errors.append(_err(lineno, "产物", "R004", "产物个数不是整数：%r" % row["产物"],
                                   "写一个整数（没有产物就写 0）"))
                continue
        rows.append(row)

    if errors:
        return {"ok": False, "receipt": None, "raw_hash": raw_hash, "errors": errors}

    receipt = {"type": rtype, "type_cn": type_cn, "workshop_id": head["工作间"],
               "member": head["成员"], "seq": seq, "credential": head["凭据"],
               "rows": rows, "raw_hash": raw_hash}
    return {"ok": True, "receipt": receipt, "errors": [], "raw_hash": raw_hash}
