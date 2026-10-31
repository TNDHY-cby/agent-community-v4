# -*- coding: utf-8 -*-
"""`platform/v5_evidence_view`：**验收「证据四步表」与证据档校验的唯一一处真相**（`V121`，已拍板）。

## 它解决什么

第 14-15 步是「员工交产物 → 组长验收」。派活（第 13 步）早就会告诉用户
**卡在哪一步 / 为什么 / 该点哪儿**；验收以前只回一句 `{"code","error","next"}`，
用户看完仍不知道"我手上现在有什么证据 / 该谁补 / 补完点哪儿"。本模块把这件事摊平成四步表。

## 两条 2026-10-31 老板拍板的硬规则（写进 `V116` §15.1 / §0 第 12 条）

1. **`L1` 必须带凭据**（`evidence_ref`）：没凭据的 `L1` **按 `L4` 算**，并拒掉这次调用（`L1_NEEDS_PROOF`）；
2. **产物目录真扫到 0 个文件时只许 `L4`**：`L0`/`L1`/`L2` 一律拒（`ARTIFACTS_EMPTY`）。

## 为什么四步里只有第 1/4 步是"硬"的

老板的规则是「证据有一档够格就行」：产物真有 ⇒ `L0`；有留痕 ⇒ `L2`；都没有但人看过 ⇒ `L4`。
所以第 2/3 步是**可用证据档**，不是"必须都打勾的进度条"——画成进度条就是撒谎。

## 纪律

- **纯函数**：不写盘、不碰 `server`、不 import 任何会改状态的东西（判据要能脱机喂合成数据）。
- 这里的 `why` / `next` **必须带真实数字或具体动作**，不许写"请检查配置"这类空话。
"""
from __future__ import annotations

# 证据档（`V116` §15）：L3 = 大模型自报，永不采信
LEVELS = ("L0", "L1", "L2", "L4")
LEVEL_MEANING = {
    "L0": "平台实做（真扫盘/真请求）",
    "L1": "它库已有（**须带凭据**，平台未核对）",
    "L2": "平台留痕（回执流水）",
    "L4": "人眼确认",
}
# 这些档位依赖"盘上真有产物"；产物空时不能用
NEEDS_ARTIFACTS = ("L0", "L1", "L2")

STEP_NAMES = ("派活记录", "产物在盘上", "回执留痕", "人拍板")
WHO = {"派活记录": "组长", "产物在盘上": "员工", "回执留痕": "员工/桥", "人拍板": "组长"}


# ── ① 档位校验（`POST …/review` 与只读视图共用） ──────────────────────
def check_level(level: str, evidence_ref: str = "", *, artifacts_empty=None,
                artifacts_total=None) -> dict:
    """判这一档能不能用来「通过」。返回 `{"ok": bool, ...}`，**永远不抛**。

    - `artifacts_empty`：`True`/`False` = 这次**真扫过**盘；`None` = 没扫（不知道，不算"空"）。
    """
    lvl = str(level or "").strip().upper()
    ref = str(evidence_ref or "").strip()

    if lvl and lvl not in LEVELS:
        if lvl == "L3":
            return {"ok": False, "code": "EVIDENCE_L3_REJECTED", "normalized": "",
                    "error": "证据等级 'L3' 不能用（L3＝大模型自报，永不采信）",
                    "next": "用 L0（平台实做）/ L1（带凭据的它库核对）/ L2（留痕）/ L4（人眼确认）"}
        return {"ok": False, "code": "BAD_EVIDENCE", "normalized": "",
                "error": "证据等级不认识：%r" % level,
                "next": "只能是 L0 / L1 / L2 / L4（L3 永不采信）"}

    # 规则一：L1 必须带凭据；没凭据 ⇒ 按 L4 算并拒
    if lvl == "L1" and not ref:
        return {"ok": False, "code": "L1_NEEDS_PROOF", "normalized": "L4",
                "error": "L1 是「它库已有」，但没给 evidence_ref —— 平台不会去替你查那个库；"
                         "**没有凭据的 L1 按 L4（人眼确认）算**",
                "next": "要么补 evidence_ref（哪个库/哪个会话/谁核的/用什么工具核的），"
                        "要么直接改成 L4 并按人眼确认留 who/when"}

    # 规则二：产物真空时只许 L4
    if artifacts_empty is True and lvl in NEEDS_ARTIFACTS:
        if lvl == "L0":
            err = ("你选了 L0（产物已在盘上），但平台这次真扫是 **0 个文件** —— 扫描结果与你的说法冲突；"
                   "多半是目录名对不上，或产物写在别处")
            nxt = ("先 `POST …/verify-artifacts` 看清单里的 `slots`：确认该工位目录名与产物目录一致；"
                   "确实没有就让它写出来，或改用 L4")
        else:
            err = ("产物目录真扫是 **0 个文件**，%s 不足以判通过 —— **只能按 L4（人眼确认）**，"
                   "或让它真写出产物" % lvl)
            nxt = "改 evidence_level=L4 并留 who/when/which；或让它真往 `产物/` 里写文件后再扫"
        return {"ok": False, "code": "ARTIFACTS_EMPTY", "normalized": "L4",
                "error": err, "next": nxt, "artifacts_total": artifacts_total}

    if not lvl:
        return {"ok": False, "code": "EVIDENCE_REQUIRED", "normalized": "",
                "error": "「通过」必须给 evidence_level",
                "next": "产物目录 diff 过 ⇒ L0；人眼看过 ⇒ L4；有凭据的它库核对 ⇒ L1；只有留痕 ⇒ L2"}

    out = {"ok": True, "code": "", "normalized": lvl, "error": "", "next": ""}
    if lvl == "L1":
        # 有凭据的 L1：放行，但**必须**标明平台没核对
        out.update({"evidence_ref": ref, "evidence_ref_is_manual": True,
                    "label": "L1（人工凭据，平台未核对）",
                    "warn": "L1 的凭据是人给的，平台没有去查那个库 —— 界面不许显示成「平台已验证」"})
    if artifacts_empty is None and lvl in NEEDS_ARTIFACTS:
        out["warn"] = (out.get("warn", "") + "；" if out.get("warn") else "") + \
            "这次没有扫产物，平台无法确认盘上有没有文件"
    return out


# ── ② 四步表（`GET …/evidence-view` 与 `POST …/review` 成败都用它） ──
def build_view(*, task_seq: int, rec: dict | None, slot_dir: str = "",
               snapshot: dict | None = None, receipts: list | None = None,
               given_level: str = "") -> dict:
    """拼「证据四步表」。**只读地**看现状：不扫盘、不写盘。

    - `rec`：账本里这条任务的记录（`None` = 没派过活）
    - `snapshot`：`_meta/artifacts.json` 的上次快照（`None` = 还没扫过）
    - `receipts`：该工作间回执流水（`receipt_store.recent()` 的结果）
    - `given_level`：用户这次想用的档（用来算 `blocking`；只读视图里一般传空）
    """
    lvl_given = str(given_level or "").strip().upper()
    snap = snapshot or {}
    slots = snap.get("slots") or {}
    slot_info = slots.get(slot_dir) or {}
    files = slot_info.get("files") or {}
    total = int(slot_info.get("count") or sum(1 for _ in files) or 0)
    scanned = bool(snap.get("at"))
    exists = bool(slot_info.get("exists"))

    # 步 1 · 派活记录（硬前置）
    if rec:
        s1 = {"ok": True, "name": STEP_NAMES[0],
              "what": "T-%03d 派出于 %s（触达：%s）" % (int(task_seq), rec.get("created_at") or "?",
                                                     rec.get("wakeup_result") or "未记录")}
    else:
        s1 = {"ok": False, "name": STEP_NAMES[0],
              "what": "账本里没有 T-%03d（平台只记真派出过的任务）" % int(task_seq)}

    # 步 2 · 产物在盘上（可用证据档 L0）
    if not scanned:
        s2 = {"ok": False, "name": STEP_NAMES[1], "level": "L0",
              "what": "还没扫过产物（点「扫描产物」；直接点「通过」时平台也会自动扫一次）"
                      if rec else "没有派活记录，谈不上产物"}
    elif not exists:
        s2 = {"ok": False, "name": STEP_NAMES[1], "level": "L0",
              "what": "扫过了，但工位目录不存在（`%s` 下没有这个目录 —— 多半是名单里的岗位名与目录名不一致）"
                      % (slot_dir or "?")}
    elif total == 0:
        s2 = {"ok": False, "name": STEP_NAMES[1], "level": "L0",
              "what": "扫过了，`%s/产物/` 里 **0 个文件**" % (slot_dir or "?")}
    else:
        names = list(files.keys())[:3]
        more = "" if total <= 3 else " 等 %d 个" % total
        s2 = {"ok": True, "name": STEP_NAMES[1], "level": "L0",
              "what": "%d 个文件：%s%s" % (total, "、".join(names) + more, "")}

    # 步 3 · 回执留痕（可用证据档 L2）
    rs = list(receipts or [])
    if rs:
        r0 = rs[0]
        s3 = {"ok": True, "name": STEP_NAMES[2], "level": "L2",
              "what": "该工位最近一条回执：%s（%s，%s）" % (r0.get("type") or r0.get("kind") or "?",
                                                     r0.get("at") or "?", r0.get("member") or "?")}
    else:
        s3 = {"ok": False, "name": STEP_NAMES[2], "level": "L2", "what": "该工位还没有任何回执"}

    # 步 4 · 人拍板（硬收尾）
    state = str((rec or {}).get("state") or "")
    if state in ("accepted", "rework", "rejected"):
        s4 = {"ok": True, "name": STEP_NAMES[3], "level": "L4",
              "what": "已落闸：%s（%s，%s）" % (state, (rec or {}).get("reviewed_by") or "?",
                                            (rec or {}).get("reviewed_at") or "?")}
    else:
        s4 = {"ok": False, "name": STEP_NAMES[3], "level": "L4", "what": "还没人给结论（通过/返工/拒收）"}

    steps = [s1, s2, s3, s4]

    have = []
    if s2["ok"]:
        have.append("L0")
    if s3["ok"]:
        have.append("L2")
    if state == "accepted" and (rec or {}).get("evidence_level"):
        have.append(str((rec or {}).get("evidence_level")))
    if (rec or {}).get("evidence_ref") and (rec or {}).get("evidence_level") == "L1":
        have = [x for x in have if x != "L1"] + ["L1"]

    # 卡在哪一步：先算"事实层面"的硬卡点，再算"你想要的那一档在不在"
    blocking, why, nxt = "", "", ""
    if not s1["ok"]:
        blocking = STEP_NAMES[0]
        why = s1["what"]
        nxt = "先派活：`POST /api/workshop/{id}/dispatch`；或确认 task_seq 是不是这条"
    elif state in ("accepted", "rework", "rejected"):
        blocking = ""      # 已落闸，没什么可拦的
        why = s4["what"]
    elif lvl_given in NEEDS_ARTIFACTS and not s2["ok"]:
        blocking = STEP_NAMES[1]
        why = "%s 需要盘上真有产物，但现在：%s" % (lvl_given, s2["what"])
        nxt = "让它往 `%s/产物/` 写文件，再 `POST …/verify-artifacts`；或直接按 L4 人眼确认" % (slot_dir or "工位")
    else:
        blocking = STEP_NAMES[3]
        why = "证据：%s；就差一个人给结论" % ("、".join(have) if have else "还没有可用档")
        nxt = ("看过产物后点「通过」（evidence_level 用 %s）；没看过就点「返工」"
               % (have[0] if have else "L4"))

    return {"task_seq": int(task_seq), "slot_dir": slot_dir,
            "steps": steps, "evidence_have": have,
            "blocking": blocking, "who_should_fix": WHO.get(blocking, ""),
            "why": why, "next": nxt,
            "level_meaning": {k: LEVEL_MEANING[k] for k in have},
            "artifacts_snapshot": {"scanned_at": snap.get("at"),
                                   "total_files": total,
                                   "slot_exists": exists,
                                   "files_preview": list(files.keys())[:3]},
            "manual_required": False,
            "manual_hint": ""}


def merge_into_failure(payload: dict, view: dict) -> dict:
    """把四步表**追加**到 `POST …/review` 的失败响应里（老的 `code/error/next` 一个都不删）。"""
    out = dict(payload or {})
    for k in ("steps", "evidence_have", "blocking", "who_should_fix"):
        out[k] = view.get(k)
    # 老字段 next 保留原意，但若视图有更具体的动作，附在后面（不覆盖）
    if view.get("next") and view.get("next") != out.get("next"):
        out["next_detail"] = view["next"]
    return out
