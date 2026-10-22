# -*- coding: utf-8 -*-
"""接入自检（V87）：把已有的判断**聚合**成「一句结论 + 有序步骤 + 一个下一步」。

设计稿：`design-docs/V87_接入自检一键闭环设计.md`（老板 2026-10-22 拍板「好」）。

三条铁律（都写成判据，见 `tests/test_harness_selfcheck.py`）：
1. **不新造探测**：每一步的数据都来自既有实现（`card_conformance` / `harness_capability`
   的四段 / `registry_health` / `credentials`），本模块**只做聚合 + 排序 + 说人话**；
2. **缺数据不许假绿**：一律 `unverified`，绝不当 `ok`；
3. **本模块是纯函数**：只读入参，不发请求、不写状态 —— 真要真发消息时由调用方（端点）在
   `probe=True` 时用既有回话探针，然后把结果传进来。
"""
from __future__ import annotations

from typing import Any, Optional

#: 步骤状态（对外契约，别加第四种）
STATE_OK = "ok"
STATE_BLOCKED = "blocked"
STATE_STALE = "stale"
STATE_UNVERIFIED = "unverified"

#: 结论（对外契约）
VERDICT_OK = "ok"
VERDICT_BLOCKED = "blocked"
VERDICT_STALE = "stale"
VERDICT_UNVERIFIED = "unverified"

#: 把「四段能力视图」里的状态词映射成本模块的词。
#: ⚠️ 段里的 `passed` 是卡片一致性的词，`unknown` 是"没测过" —— 都必须归到 `unverified`，
#: 不许因为"看起来像成功"就归 `ok`（那就成了缺数据假绿）。
_STATE_MAP = {
    "ok": STATE_OK,
    "passed": STATE_OK,
    "failed": STATE_BLOCKED,
    "stale": STATE_STALE,
    "unknown": STATE_UNVERIFIED,
    "unverified": STATE_UNVERIFIED,
}


def _map_state(raw: str) -> str:
    return _STATE_MAP.get(str(raw or "").strip().lower(), STATE_UNVERIFIED)


def _step(sid: str, title: str, state: str, why: str = "", next_: str = "") -> dict:
    return {"id": sid, "title": title, "state": state,
            "why": why or "", "next": next_ or ""}


def derive_verdict(steps: list) -> tuple:
    """由步骤**派生**结论与下一步（一处真相：结论不许另外算）。

    - 有 `blocked` ⇒ blocked（取**最靠前**那个的 next）
    - 否则有 `stale` ⇒ stale
    - 否则全 `ok` ⇒ ok
    - 否则 ⇒ unverified（全都没验过）
    """
    steps = steps if isinstance(steps, list) else []
    for st in steps:
        if st.get("state") == STATE_BLOCKED:
            return VERDICT_BLOCKED, st.get("next") or st.get("why") or ""
    for st in steps:
        if st.get("state") == STATE_STALE:
            return VERDICT_STALE, st.get("next") or st.get("why") or ""
    if steps and all(st.get("state") == STATE_OK for st in steps):
        return VERDICT_OK, ""
    for st in steps:
        if st.get("state") == STATE_UNVERIFIED:
            return VERDICT_UNVERIFIED, st.get("next") or st.get("why") or ""
    return VERDICT_UNVERIFIED, ""


def _credential_step(cred: Optional[dict]) -> dict:
    """凭据：弱值 / 桥产物明文 ⇒ 直接 blocked（改 key 比查桥更快见效，排最前）。"""
    cred = cred if isinstance(cred, dict) else {}
    if not cred.get("scanned"):
        return _step("credential", "凭据", STATE_UNVERIFIED, "",
                     "先体检一次：`python tools/credential_check.py`（或界面点「体检」）")
    weak = int(cred.get("weak") or 0)
    plain = int(cred.get("plaintext_in_bridge") or 0)
    if weak or plain:
        bits = []
        if weak:
            bits.append("%d 处密钥像占位值/太短" % weak)
        if plain:
            bits.append("%d 处明文落在桥脚本里" % plain)
        return _step("credential", "凭据", STATE_BLOCKED, "；".join(bits),
                     "按体检结果逐条改；改完再点「体检」确认")
    return _step("credential", "凭据", STATE_OK, "")


def _capability_step(seg_id: str, title: str, seg: Optional[dict], hint: str) -> dict:
    seg = seg if isinstance(seg, dict) else {}
    raw_state = seg.get("state")
    state = _map_state(raw_state)
    why = str(seg.get("why") or "")
    # 非 ok 时一律给"**怎么修/怎么重验**"那句话（hint）；hint 为空才退回通用话术 ——
    # 用户要的是下一步动作，不是"按上面的原因修"这种废话。
    next_ = "" if state == STATE_OK else (hint or "按上面的原因修，然后回来再自检一次")
    return _step(seg_id, title, state, why, next_)


def collect(info: dict, *, cred: Optional[dict] = None, health: Optional[dict] = None,
            probe: Optional[dict] = None) -> dict:
    """聚合（**纯函数**：不发请求、不写状态）。

    - `info`：`HarnessInfo.model_dump()`（同 `/api/harness/{id}/capability` 的入参）
    - `cred`：`credentials.ledger_summary_light()`
    - `health`：`registry_health.status(hid)`
    - `probe`：本次真的跑了回话探针时，把探针结论传进来覆盖 `agent_can_reply` 那一段
      （`{"state": "ok|failed|stale", "why": "..."}`）；不传就沿用段里的既有结论。
    """
    from . import harness_capability as hc

    info = info if isinstance(info, dict) else {}
    prov = hc.build_provenance(info)
    segments = (prov.get("segments") or {}) if isinstance(prov, dict) else {}

    reply_seg = segments.get("agent_can_reply")
    if isinstance(probe, dict) and probe.get("state"):
        reply_seg = {"state": probe.get("state"), "why": probe.get("why") or ""}

    steps = [
        _credential_step(cred),
        _capability_step("card", "声明合规", segments.get("card_conformance"),
                         "先在对接中心把注册卡片补齐（字段名与取值范围见登记页提示）"),
        _capability_step("config", "配置能连", segments.get("bridge_config"),
                         "点「架桥」或「验一次桥配置」把配置验通"),
        _capability_step("running", "桥在跑", segments.get("harness_running_bridge"),
                         "在 harness 那侧真的把桥跑起来（平台看不到就不算跑）"),
        _capability_step("reply", "真回话", reply_seg,
                         "点「验一次回话」真发一条极小消息（会打扰它，所以默认不跑）"),
    ]

    verdict, next_step = derive_verdict(steps)

    # 一句话结论：给用户看的，不是给日志看的
    one_line = _one_line(verdict, steps, health)
    return {
        "verdict": verdict,
        "one_line": one_line,
        "steps": steps,
        "next_step": next_step,
        "counts": prov.get("counts") if isinstance(prov, dict) else {},
        "probe_used": bool(isinstance(probe, dict) and probe.get("state")),
        "health": _health_view(health),
    }


def _health_view(health: Optional[dict]) -> dict:
    health = health if isinstance(health, dict) else {}
    return {"samples": int(health.get("samples") or 0),
            "days_all_failing": int(health.get("days_all_failing") or 0),
            "hidden": bool(health.get("hidden")),
            "candidate": bool(health.get("candidate"))}


def _one_line(verdict: str, steps: list, health: Optional[dict]) -> str:
    bad = [st for st in steps if st.get("state") != STATE_OK]
    past = ""
    hv = _health_view(health)
    if hv["candidate"]:
        past = "（注意：这个号最近连续 %d 天全失败过，平台已建议隐藏）" % hv["days_all_failing"]
    elif hv["samples"]:
        past = "（健康样本 %d 条，最近连续失败 %d 天）" % (hv["samples"], hv["days_all_failing"])

    if verdict == VERDICT_OK:
        return "这条已经通了：声明、配置、桥、回话四项都有据可查" + past
    if verdict == VERDICT_BLOCKED:
        first = bad[0]
        return "卡在「%s」：%s" % (first.get("title"), first.get("why") or "原因见步骤") + past
    if verdict == VERDICT_STALE:
        stale = next((st for st in steps if st.get("state") == STATE_STALE), {})
        return "以前是通的，但证据过期了（%s）" % (stale.get("why") or "需要重新验一次") + past
    return "还不能下结论：%d 项没验过 — 先做「%s」" % (
        len([st for st in steps if st.get("state") == STATE_UNVERIFIED]),
        (bad[0].get("title") if bad else "体检")) + past
