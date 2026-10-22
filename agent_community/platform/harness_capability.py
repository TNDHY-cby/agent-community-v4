# -*- coding: utf-8 -*-
"""platform/harness_capability：「这个 harness 有什么能力、凭什么这么说」（V73 §9 落地）。

## 为什么要有它（来自本机 8 条真实记录的实测）

平台存的能力是三处、且互相打架：
- `ai.capabilities`（声明的标签，同一件事有三种写法：`shell` / `bash` / `exec_command`）；
- `ai.can_code` / `ai.can_file_ops` / `ai.can_browse` / `ai.can_see`（布尔，和上面经常不一致）；
- `tools[]`（只有少数记录有，`parameters` 全空）。

而"验证"只到桥配置这一段——`metadata.bridge_template_verified` 里平台自己写明了范围：

> 仅桥配置（桥能连平台/领件/回报；且这只桥是平台在验证间里启动的）——
> harness 侧是否运行该桥、agent 能否回话，本次未验证

⇒ 本模块只做一件事：把能力与证据来源摊平成一份只读视图，
不新增存储、不改任何记录、不假装验证过。判定口径见 `design-docs/V73_…md` §4 / §9。

## 证据分级（唯一口径）

| 来源 | 含义 | 谁给的 |
|---|---|---|
| `declared` | 它/登记人声明的，未验证 | `ai.capabilities`、`ai.can_*`、`tools[]` |
| `probed` | 平台在宿主上探到的事实（进程/端口/路径线索），零侵入零 token | `metadata.probe_hints` |
| `verified` | 平台在验证间真跑通过——范围仅"桥配置" | `metadata.bridge_test.ok`、`bridge_template_verified[].evidence.state == "ok"` |
| `failed` | 探到过但失败（`bridge_test.ok == False`） | 同上 |

## 三段（V73 §4，别混）

```
bridge_config          桥能连平台/领件/回报        ← 唯一可能被 verified 的一段
harness_running_bridge harness 侧真的在跑那只桥     ← 本机 8/8 未验证
agent_can_reply        agent 真的能回话             ← 本机 8/8 未验证
```

只有第三段绿才叫"接上了"；前两段绿只叫"配置可行"（沿用 V43 §0' 的口径）。
"""
from __future__ import annotations

from datetime import datetime, timezone

#: 能力标签归一（同一件事的多种写法）。`raw` 原样保留，便于对照真实记录。
CAPABILITY_ALIASES = {
    "shell": "shell",
    "bash": "shell",
    "sh": "shell",
    "exec_command": "shell",
    "terminal": "shell",
    "coding": "coding",
    "code": "coding",
    "file_ops": "file_ops",
    "files": "file_ops",
    "web_search": "web_search",
    "browse": "web_search",
    "memory": "memory",
    "system_ops": "system_ops",
    "app_ops": "app_ops",
    "vision": "vision",
}

#: 归一后的中文显示名（界面直接用，别在界面里另编）
CAPABILITY_LABEL = {
    "shell": "执行命令",
    "coding": "写代码",
    "file_ops": "读写文件",
    "web_search": "联网检索",
    "memory": "长期记忆",
    "system_ops": "系统操作",
    "app_ops": "应用操作",
    "vision": "看图",
}

#: 验证范围原话 不再自带一份：唯一实现在 `platform/evidence_scope.py`
#: （写入侧 `verify_sandbox` 与读取侧本模块共用，避免两处各写一套）。
from . import evidence_scope as _scope
from . import reply_probe as _rp
SCOPE_BRIDGE_ONLY = _scope.SCOPE_BRIDGE_ONLY
SCOPE_UNVERIFIED = _scope.SCOPE_NONE


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_capability(name: str) -> tuple:
    """`(canonical, label_zh)`；认不出来就原样（不编造）。"""
    raw = str(name or "").strip()
    key = raw.lower()
    canonical = CAPABILITY_ALIASES.get(key, key)
    return canonical, CAPABILITY_LABEL.get(canonical, raw or "（未命名）")


def _ai(info: dict) -> dict:
    ai = info.get("ai")
    return ai if isinstance(ai, dict) else {}


def _meta(info: dict) -> dict:
    md = info.get("metadata")
    return md if isinstance(md, dict) else {}


def _bridge_evidence(info: dict) -> dict:
    """从真实记录里取"桥配置"这一段证据（原话与时间都带上）。"""
    md = _meta(info)
    bt = md.get("bridge_test") if isinstance(md.get("bridge_test"), dict) else {}
    vers = md.get("bridge_template_verified")
    last = None
    if isinstance(vers, list) and vers:
        cand = vers[-1] if isinstance(vers[-1], dict) else {}
        ev = cand.get("evidence") if isinstance(cand.get("evidence"), dict) else {}
        last = {"verified_at": cand.get("verified_at"), "state": ev.get("state"),
                "bridge_kind": ev.get("bridge_kind"), "channel": ev.get("channel"),
                "scope": ev.get("verified_scope")}
    return {"bridge_test": bt, "last_verified": last}


def _segment(state: str, why: str, extra: dict | None = None) -> dict:
    out = {"state": state, "why": why}
    if extra:
        out.update(extra)
    return out


def build_provenance(info: dict) -> dict:
    """把一条 harness 记录算成「三段状态 ＋ 能力清单（带来源）」。

    纯函数：只读入参、不发请求、不写任何状态；`info` 就是 `HarnessInfo` 的 dict。
    """
    info = info if isinstance(info, dict) else {}
    ai = _ai(info)
    md = _meta(info)
    bridge = _bridge_evidence(info)
    bt = bridge.get("bridge_test") or {}
    last = bridge.get("last_verified") or {}

    # ── ① 桥配置这一段：唯一可能有 verified 的地方 ─────────────────────
    if bt.get("ok") is True or (last.get("state") == "ok"):
        bridge_cfg = _segment("ok", SCOPE_BRIDGE_ONLY, {
            "evidence_scope": SCOPE_BRIDGE_ONLY,
            "bridge_test": {k: bt.get(k) for k in ("test_id", "ok", "at", "echo") if k in bt},
            "last_verified": last,
        })
    elif bt.get("ok") is False:
        bridge_cfg = _segment("failed", "桥脚本自测没通过（见 bridge_test.echo）",
                              {"bridge_test": {k: bt.get(k) for k in ("test_id", "ok", "at", "echo") if k in bt}})
    else:
        bridge_cfg = _segment("unknown", SCOPE_UNVERIFIED, {"evidence_scope": SCOPE_UNVERIFIED})

    # ── 第二/三段：只有真的验过才敢说 ok（`V74` 回话探针）────────────
    probe = md.get("reply_probe") if isinstance(md.get("reply_probe"), dict) else {}
    if _rp.is_stale(probe):
        # V81：验过但已过期 —— 既不能说 ok（旧的不能代表现在），也不能打回 unverified（那是没验过）
        _age = _rp.age_hours(probe)
        reply_seg = _segment("stale", (
            "验过，但那是 %.1f 小时前的结论（超过 %.0f 小时就不算数了）" % (_age, _rp.staleness_hours())
            if _age >= 0 else "验过，但不知道是多久前验的（记录缺时间），建议重验"),
            {"source": probe.get("source") or "auto", "at": probe.get("at"),
             "how_to_fix": "再点一次「验一次」（自动型）或「我确认它回了」（人工中转型）"})
    elif probe.get("state") == "ok":
        _src = probe.get("source") or "auto"
        reply_seg = _segment("ok", (
            "验过了：平台投递一条带 probe_id 的消息，它回了带回执的消息"
            if _src == "auto" else
            "人工确认（用户说它回了）——不是平台自动验的，来源已标注"), {
            "evidence_scope": ("自动回执：本次证明的是「这条桥投递后 agent 回了带回执的消息」"
                               if _src == "auto" else
                               "人工确认：本次只证明「用户判断它回了」，平台没有自动回执"),
            "source": _src, "at": probe.get("at"),
            "evidence": probe.get("evidence") or {},
        })
    elif probe.get("state") == "pending" and _rp.pending_expired(probe):
        # 超时 ⇒ 如实记失败（不许永远停在 pending）
        reply_seg = _segment("failed",
                             "探针超时：等了 %s 秒也没收到带回执的消息（发起于 %s）"
                             % (_rp.timeout_seconds(), probe.get("at") or "?"),
                             {"source": probe.get("source") or "auto", "at": probe.get("at"),
                              "expire_at": probe.get("expire_at")})
    elif probe.get("state") in ("failed", "pending"):
        reply_seg = _segment(
            "failed" if probe.get("state") == "failed" else "unverified",
            ("探针失败：" + str(probe.get("why") or "未知原因")) if probe.get("state") == "failed"
            else "探针已发出，还没收到回执（超时后会自动记为失败）",
            {"source": probe.get("source") or "auto", "at": probe.get("at")})
    else:
        reply_seg = _segment(
            "unverified",
            "没有任何记录证明这个 agent 收到消息后能回话"
            + ("；本条记录的验证范围原话是：" + str(last.get("scope")) if last.get("scope") else ""))

    # ★V82：**声明层**一致性（静态字段）—— 与"配置能跑""真能回话"分开，互不覆盖
    from . import card_conformance as _cc

    _card = _cc.check_card(info)
    card_seg = _segment(
        _card["conformance"],
        ("注册卡片字段齐、格式对" if _card["conformance"] == "passed"
         else "注册卡片有 %d 处不合规：%s" % (
             len(_card["conformance_errors"]),
             "；".join("%s %s" % (e["field"], e["why"]) for e in _card["conformance_errors"][:3]))),
        {"checked_at": _card.get("checked_at"), "errors": _card["conformance_errors"]})

    segments = {
        "card_conformance": card_seg,
        "bridge_config": bridge_cfg,
        "harness_running_bridge": _segment(
            "unverified",
            "平台没有观测到 harness 侧真的在跑这只桥（验证间里对面是平台自己）"),
        "agent_can_reply": reply_seg,
    }
    connected = segments["agent_can_reply"]["state"] == "ok"

    # ── ② 能力条目：来自 `ai.capabilities`（逐条标来源），工具单列 ─────────
    #    ⚠️ 按归一后的名字去重：真实记录里 `shell`/`bash`/`exec_command` 会同时出现，
    #    直接展开会让界面出现三条"执行命令"（本机 8 条记录实测）。
    caps: list = []
    index: dict = {}
    for raw in (ai.get("capabilities") or []):
        canonical, label = normalize_capability(str(raw))
        if canonical in index:
            index[canonical]["raw"].append(str(raw))
            continue
        item = {"name": canonical, "display": label, "raw": [str(raw)],
                "source": "declared", "evidence": "ai.capabilities", "also_in_tools": False}
        index[canonical] = item
        caps.append(item)

    tools = []
    for tool in (info.get("tools") or []):
        if not isinstance(tool, dict):
            continue
        tname = str(tool.get("name") or "").strip()
        if not tname:
            continue
        tag = str(tool.get("capability_tag") or "").strip()
        canonical, label = normalize_capability(tag or tname)
        tools.append({"name": tname, "capability_tag": tag, "canonical": canonical,
                      "display": label or tname, "params_known": bool(tool.get("parameters")),
                      "source": "declared", "evidence": "tools[]"})
        if canonical in index:
            index[canonical]["also_in_tools"] = True
        else:
            item = {"name": canonical, "display": label or tname, "raw": [tname],
                    "source": "declared", "evidence": "tools[]", "also_in_tools": True}
            index[canonical] = item
            caps.append(item)

    # ── ③ 布尔声明与标签的不一致要说出来（别替它们圆场）─────────────
    notes = []
    flags = {"can_code": "coding", "can_file_ops": "file_ops", "can_browse": "web_search",
             "can_see": "vision"}
    for flag, canonical in flags.items():
        if flag in ai:
            declared = canonical in index
            val = bool(ai.get(flag))
            if declared and not val:
                notes.append("自相矛盾：能力列表声明了 `%s`，但 `ai.%s=false`" % (canonical, flag))
            elif val and not declared:
                notes.append("自相矛盾：`ai.%s=true`，但能力列表里没有 `%s`" % (flag, canonical))

    probe = md.get("probe_hints") if isinstance(md.get("probe_hints"), dict) else {}
    probed = [{"kind": k, "value": probe[k], "source": "probed", "evidence": "metadata.probe_hints"}
              for k in ("process_hint", "port_hint", "path_hint") if probe.get(k)]

    return {
        "harness_id": str(info.get("harness_id") or ""),
        "harness_name": str(info.get("harness_name") or ""),
        "checked_at": _now_iso(),
        "connected": connected,
        "verdict_text": ("已接上（agent 能回话有证据）" if connected
                         else "配置可行，但没有证据证明 agent 能回话"),
        "segments": segments,
        "capabilities": caps,
        "tools": tools,
        "probed": probed,
        "counts": {
            "declared": len(caps),
            "probed": len(probed),
            "verified": 1 if bridge_cfg["state"] == "ok" else 0,
            "tools": len(tools),
        },
        "notes": notes,
        "source_hint": ("能力名称是 harness 声明的（未验证）；`probed` 是平台在宿主上探到的事实；"
                        "`verified` 仅指桥配置这一段"),
    }
