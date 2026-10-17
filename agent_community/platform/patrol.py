# -*- coding: utf-8 -*-
"""V56 内核：**巡逻员**——派人到现场看、按三类动作修、修完必须复验。纯逻辑（好测），无副作用。

设计稿：`design-docs/V56_巡逻员现场查看与维修设计.md`（老板 2026-10-17「按你的来」拍板）。

## 它补的是哪一环

现有链条（`member_offline.handle_offline`）：①通知组长 → ②平台尝试调起 → ④到上限通知用户。
缺的是中间**"派人到现场看看到底怎么了"**：它为什么起不来（窗口没开／进程卡死／桥配置错／磁盘满／权限没了），
**只有能看本机事实的 agent 答得出**，平台自己只会"再试一次调起"。

## 三条铁律（写进本模块）

1. **只许三类动作**：`relaunch_window` / `restart_bridge` / `reactivate`；别的动议**拒收**（"改代码/删数据"一律不批）。
2. **三态如实**：`fixed` = `True`（有复验证据）/ `False`（修了但没好）/ `None`（**没自证**，必须写 `why_no_proof`）。
3. **"它说修好了"不算修好**：由 `verdict()` 拿**平台自己的掉线判据**复核；
   `fixed=True` 但掉线判据仍真 ⇒ 标 `claimed_but_not_verified` 并照旧升级到通知用户（与"调起成功不采信我发出去了"同一口径）。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

#: 允许它做的三类动作（别的动议一律拒收）
ALLOWED_ACTIONS = ("relaunch_window", "restart_bridge", "reactivate")

#: 现场检查单（只读优先；顺序＝先看最容易露馅的）
CHECKLIST = (
    "窗口在不在（标题里含该成员的会话名）",
    "进程在不在（pid / 命令行还活着吗）",
    "桥心跳多久没动（stale 多久）",
    "该成员的 compartment 日志尾巴有没有报错",
    "磁盘/权限有没有明显问题（写不进去、文件锁）",
)

#: 角色名的匹配口径（认「巡逻员」三个字；不写死具体 harness 名）
PATROL_ROLE_HINT = "巡逻"


def _s(v: Any) -> str:
    return str(v or "").strip()


def role_is_patrol(member: Any) -> bool:
    """这个成员是不是巡逻员（按**角色名**认，不认 harness 名/成员 id）。"""
    return PATROL_ROLE_HINT in _s(getattr(member, "role", ""))


def _has_harness(member: Any) -> bool:
    return any(_s(h) for h in (getattr(member, "harness_ids", None) or []))


def pick_patrol_member(ws: Any, *, exclude: Iterable[str] = ()) -> Optional[Any]:
    """挑一个可用巡逻员：角色含「巡逻」**且**绑了 harness（没绑就派不出去，不能假装派了）。"""
    skip = {_s(x) for x in (exclude or ()) if _s(x)}
    for m in (getattr(ws, "members", None) or []):
        mid = _s(getattr(m, "member_id", ""))
        if mid in skip:
            continue
        if role_is_patrol(m) and _has_harness(m):
            return m
    return None


def symptom_of(member: Any, *, reasons: Iterable[str] = (), revive: Optional[dict] = None) -> dict:
    """把"平台看到的症状"打包给巡逻员（它到现场先照着症状看）。"""
    rev = revive or {}
    return {
        "member_id": _s(getattr(member, "member_id", "")),
        "role": _s(getattr(member, "role", "")),
        "display_name": _s(getattr(member, "display_name", "")),
        "reasons": [_s(r) for r in (reasons or []) if _s(r)],
        "heartbeat": _s(getattr(member, "offline_last_reason", "")),
        "last_revive": {"ok": rev.get("ok"), "verified": rev.get("verified"),
                        "detail": _s(rev.get("detail"))[:200]},
        "attempts": int(getattr(member, "offline_attempts", 0) or 0),
    }


def checklist_for(symptom: Optional[dict] = None) -> list:
    """检查单（当前是固定五项；症状只用于提示"先看哪一项"）。"""
    return list(CHECKLIST)


def dispatch_payload(ws: Any, patrol_member: Any, target_member: Any, *,
                     symptom: Optional[dict] = None, base_url: str = "",
                     patrol_id: str = "") -> dict:
    """派给巡逻员的 payload（`kind="patrol"`，复用现有 `_dispatch_to_harness`）。"""
    sym = symptom or symptom_of(target_member)
    return {
        "type": "patrol",
        "kind": "patrol",
        "patrol_id": patrol_id,
        "workshop_id": _s(getattr(ws, "workshop_id", "")),
        "member_id": _s(getattr(patrol_member, "member_id", "")),
        "role": _s(getattr(patrol_member, "role", "")),
        "target_member_id": _s(getattr(target_member, "member_id", "")),
        "target_harness_id": next((_s(h) for h in (getattr(target_member, "harness_ids", None) or []) if _s(h)), ""),
        "symptom": sym,
        "allowed_actions": list(ALLOWED_ACTIONS),
        "checklist": checklist_for(sym),
        "report_endpoint": ("%s/api/workshop/%s/patrol/%s/result"
                            % (base_url.rstrip("/"), _s(getattr(ws, "workshop_id", "")), patrol_id))
        if base_url else "/api/workshop/%s/patrol/%s/result" % (_s(getattr(ws, "workshop_id", "")), patrol_id),
        "message": instruction(sym, patrol_id=patrol_id, workshop_id=_s(getattr(ws, "workshop_id", "")),
                               base_url=base_url),
    }


def instruction(symptom: dict, *, patrol_id: str = "", workshop_id: str = "", base_url: str = "") -> str:
    """写给人/agent 看的**纯文本**指令（无 markdown 星号；与 V54/V55 的话术同口径）。"""
    sym = symptom or {}
    reasons = "；".join(sym.get("reasons") or []) or "（平台没给具体症状）"
    lines = [
        "你被派去现场看一位掉线的员工。请按下面做，然后如实回报（修不好也照样回报）。",
        "",
        "【看谁】%s（%s·%s）" % (sym.get("display_name") or sym.get("member_id") or "该成员",
                               sym.get("role") or "员工", sym.get("member_id") or "?"),
        "【平台看到的症状】" + reasons,
        "【平台已经试过】调起 %s 次，最后一次：%s"
        % (sym.get("attempts") or 0, (sym.get("last_revive") or {}).get("detail") or "（无）"),
        "",
        "【现场检查单】（先只读地看，别急着动手）",
        *["  %d) %s" % (i, item) for i, item in enumerate(checklist_for(sym), 1)],
        "",
        "【允许你做的只有三类】",
        "  1) relaunch_window —— 重新把它的窗口拉起来（标题要含会话名）；",
        "  2) restart_bridge  —— 重启它的桥；",
        "  3) reactivate      —— 让平台重新派一次激活。",
        "  ✗ 不许改代码、不许删数据、不许动别的机器、不许改别人的配置。做不到就写「我看不了」。",
        "",
        "【修完必须复验】重新拉起之后要能确认「窗口真的在」、重启桥之后要能确认「心跳真的动了」；"
        "没有复验就写 fixed=null，别写 true。",
        "",
        "【回报到】POST %s" % ((base_url.rstrip("/") if base_url else "<平台地址>")
                              + "/api/workshop/%s/patrol/%s/result" % (workshop_id or "<ws>", patrol_id or "<id>")),
        "  字段：{checked:[{what,how,result}], found, tried:[{action,result,verified,why?}],"
        " fixed:true/false/null, why_no_proof?, still_broken_reason?, need_human?, evidence:{after:{…}}}",
        "",
        "【提醒】平台会拿它自己的掉线判据复核你的结论：你说修好了、但心跳还是死的，平台会记一笔"
        "「claimed_but_not_verified」并把情况升级给用户。",
    ]
    return "\n".join(lines).replace("**", "")


def validate_report(report: Any) -> tuple[bool, list, dict]:
    """校验巡逻回报。返回 `(ok, problems, normalized)`。

    硬约束（与"失败要说话"同源）：
      · `checked` 要有（看了什么、怎么看、结果），空着＝没说清；
      · `tried[].action` 必须在 `ALLOWED_ACTIONS` 里，否则**拒收**；
      · `fixed` 只能是 true/false/null；`fixed is None` ⇒ **必须**写 `why_no_proof`；
      · `fixed is False` ⇒ **必须**写 `still_broken_reason`。
    """
    probs: list = []
    r = report or {}
    checked = r.get("checked") or []
    if not isinstance(checked, list) or not checked:
        probs.append("checked 不能为空（你看了什么、怎么看、结果是什么）")
    tried, actions = [], []
    for i, t in enumerate(r.get("tried") or [], 1):
        if not isinstance(t, dict):
            probs.append("tried[%d] 必须是对象" % i)
            continue
        act = _s(t.get("action"))
        if act not in ALLOWED_ACTIONS:
            probs.append("tried[%d].action=%r 不在允许的三类里（%s）—— 改代码/删数据这类动议平台不批"
                         % (i, act, " / ".join(ALLOWED_ACTIONS)))
            continue
        actions.append(act)
        tried.append({"action": act, "result": _s(t.get("result")) or "unknown",
                      "verified": (None if t.get("verified") is None else bool(t.get("verified"))),
                      "why": _s(t.get("why"))})
    fixed = r.get("fixed")
    if fixed is not None and not isinstance(fixed, bool):
        probs.append("fixed 只能是 true / false / null（null＝没自证）")
    if fixed is None and not _s(r.get("why_no_proof")):
        probs.append("fixed=null 时必须写 why_no_proof（为什么没能自证）")
    if fixed is False and not _s(r.get("still_broken_reason")):
        probs.append("fixed=false 时必须写 still_broken_reason（还坏在哪）")
    need_human = bool(r.get("need_human"))
    if need_human and not _s(r.get("user_action")):
        probs.append("need_human=true 时要写 user_action（用户该做什么）")
    norm = {
        "checked": [{"what": _s(c.get("what")), "how": _s(c.get("how")), "result": _s(c.get("result"))}
                    for c in checked if isinstance(c, dict)],
        "found": _s(r.get("found")),
        "tried": tried,
        "actions": actions,
        "fixed": fixed,
        "why_no_proof": _s(r.get("why_no_proof")),
        "still_broken_reason": _s(r.get("still_broken_reason")),
        "need_human": need_human,
        "user_action": _s(r.get("user_action")),
        "evidence": r.get("evidence") or {},
    }
    return (not probs), probs, norm


def verdict(norm: dict, *, offline_still: bool) -> dict:
    """平台复核：**不采信"它说修好了"**，只看掉线判据有没有消失。"""
    claimed = norm.get("fixed")
    if claimed is True and offline_still:
        return {"status": "claimed_but_not_verified", "fixed": False,
                "detail": "巡逻员说修好了，但平台复核时掉线判据仍在 ⇒ 按未修好处理，继续升级给用户"}
    if claimed is True and not offline_still:
        return {"status": "fixed", "fixed": True, "detail": "巡逻员说修好了，平台复核：掉线判据已消失"}
    if claimed is False:
        return {"status": "not_fixed", "fixed": False,
                "detail": "巡逻员如实回报没修好：%s" % (norm.get("still_broken_reason") or "（没说原因）")}
    return {"status": "unverified", "fixed": False,
            "detail": "巡逻员没能自证（%s）—— 平台按「未确认修好」处理"
                      % (norm.get("why_no_proof") or "未说明")}


def human_line(norm: dict, v: dict) -> str:
    """给用户的一句人话（可展开细节在 still_broken_reason / user_action 里）。"""
    head = {"fixed": "巡逻员到现场看过了，已修好",
            "claimed_but_not_verified": "巡逻员说修好了，但平台复核**没通过**",
            "not_fixed": "巡逻员到现场看过了，**没修好**",
            "unverified": "巡逻员到现场看过了，但**没能确认修好**"}[v["status"]]
    bits = [head + "。"]
    if norm.get("found"):
        bits.append("现场情况：" + norm["found"].rstrip("。") + "。")
    if v["status"] in ("not_fixed", "claimed_but_not_verified", "unverified"):
        why = norm.get("still_broken_reason") or norm.get("why_no_proof")
        if why:
            bits.append("原因：" + why.rstrip("。") + "。")
    if norm.get("need_human") and norm.get("user_action"):
        bits.append("要你处理的是：" + norm["user_action"].rstrip("。") + "。")
    return "".join(bits).replace("**", "")
