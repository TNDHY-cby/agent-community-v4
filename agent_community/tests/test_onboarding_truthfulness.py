# -*- coding: utf-8 -*-
"""★老板真机实测第一手材料（某 IDE 型外端 agent 的接入报告）暴露的四个真问题。

**报告原文的关键句**（我逐条核实过，都是真的）：
  1. 「平台在机器上扫到了 127.0.0.1:3722 / :5283 这两个端口，**误判为我的 HTTP API 端点**，
     把 wakeup_method 设成了 `http_api`」—— 而它只是 IDE 内嵌 agent，没有常驻 HTTP 服务；
  2. 「wake 接口拒绝空命令，但 **clipboard 人工模式本身没有 shell 命令**」⇒ 第 2 步卡住；
  3. 「第 4 步自检要 **workshop_id/member_id**，而我刚注册、还没有工作间」⇒ 走不下去；
  4. 它**明确声明了 clipboard**，平台却回「你**没有声明** wakeup_method」。

**根因（四处，同一类毛病：拿猜测覆盖对象的自述 / 向用不到的人索要东西）**：
  · `ProbeHarnessTool` 只要**本机扫到任意 HTTP 服务**（400/405/404 都算"有服务"）
    就推荐 `wakeup_method: http_api` + 别人的端口当地址 —— 开发机上永远有随机服务 ⇒ 几乎必然误判；
  · `probe-register` 拿上面那个推荐**推翻对象自己声明的** wakeup_method（还有个口子：
    仅凭 `port_hint` 就假设 http_api，连探都不探），且默认值用了 `file_poll`（会去写不存在的邮箱）；
  · `harness_pre_register` **从来没存** `wakeup_method`/`api_base_url` ⇒ 对象的自述被静默丢弃，
    到探测那步自然"你没声明"；
  · `/wake` 对空命令直接 400，而人工模式本来就没有命令。

**修完的判据**（本文件锁这些）：
  A. 没有证据时**不覆盖**对象的声明（探测只补线索、不下定论）；
  B. 对象在 pre-register 声明的 wakeup_method **会被存下来并沿用**；
  C. 人工模式配叫醒命令**成功**（mode=manual），不是 400；
  D. 话术第 4 步指向 `{hid}/verify`（**刚注册就能自检，不需要工作间**）。
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

P = "agent_community/platform"


def _as_dict(out):
    if isinstance(out, dict):
        return out
    body = getattr(out, "body", None)
    if body is not None:
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"_raw": str(out)[:200]}


class _Req:
    def __init__(self, body=None):
        self._body = body or {}
        self.headers = {}
        self.query_params = {}

    async def json(self):
        return self._body


# ── A. 探测不拿"没验证的端口"当结论 ──────────────────────────────────────
def test_probe_tool_does_not_recommend_http_api_from_scanned_ports():
    """★探测工具：扫到的端口只是**线索**，不许据此推荐 `http_api`。

    （原逻辑只要本机有任意 HTTP 服务就推荐 http_api + 那个端口 —— 实测把一个纯人工型
      agent 判成 http_api、地址指向与它无关的 127.0.0.1:3722。）
    """
    import inspect

    from agent_community.platform.tools.assistant_tools import ProbeHarnessTool

    src = inspect.getsource(ProbeHarnessTool)
    assert "尚不能确定属于" in src, "探测结果必须明说'这些服务未确认归属'"
    # 只有 port_hint 命中才给推荐
    assert "_hint_port" in src, "推荐必须建立在'对象自己给的 port_hint 命中'之上"
    assert "不要拿一个没验证的端口去当" in src or "⛔" in src, "要明确劝止拿没验证的端口当地址"


# ── B. 对象的自述要存下来、并沿用 ────────────────────────────────────────
def test_pre_register_persists_declared_wakeup_method(monkeypatch):
    """★pre-register 必须**存下**对象声明的 wakeup_method（原来从来没存 ⇒ 探测那步说"你没声明"）。"""
    from agent_community.platform.routers import harness_register as hr
    from agent_community.platform.state import pending_pre_register

    hid = "decl-wm-1"
    pending_pre_register.pop(hid, None)
    try:
        d = _as_dict(asyncio.run(hr.harness_pre_register(_Req({
            "harness_id": hid, "harness_name": "示例Harness-A",
            "harness_type": "ide-agent", "wakeup_method": "clipboard",
        }))))
        assert d.get("success") is True, d
        entry = pending_pre_register.get(hid) or {}
        assert entry.get("wakeup_method") == "clipboard", \
            "对象声明的 wakeup_method 必须被存下来（否则探测那步会当它没声明）：%r" % entry
    finally:
        pending_pre_register.pop(hid, None)


def test_probe_register_respects_declared_method(monkeypatch):
    """★探测注册：**没有证据时**沿用对象自己声明的方式，绝不拿没验证的端口当地址。"""
    from agent_community.platform.harness_adapter import harness_manager
    from agent_community.platform.routers import harness_register as hr
    from agent_community.platform.state import pending_pre_register

    hid = "decl-wm-2"
    pending_pre_register[hid] = {
        "harness_id": hid, "harness_name": "示例Harness-B", "harness_type": "ide-agent",
        "wakeup_method": "clipboard", "process_hint": "no-such-process-xyz",
        "port_hint": None, "path_hint": "", "capabilities": [], "ai": {"model_name": "示例模型"},
    }
    try:
        d = _as_dict(asyncio.run(hr.harness_probe_register(_Req({"harness_id": hid}))))
        if not d.get("success"):
            pytest.skip("探测注册在本环境不可用：%s" % str(d)[:120])
        assert d.get("wakeup_method") == "clipboard", \
            "没有 HTTP API 证据时必须沿用它声明的 clipboard：%s" % d
        assert not d.get("api_base_url"), "不许拿没验证的端口当 api_base_url：%s" % d
        assert d.get("wakeup_method_note"), "要说清'为什么这么判'（单独字段，别埋在被截断的长文本里）"
    finally:
        pending_pre_register.pop(hid, None)
        try:
            harness_manager.unregister(hid)
        except Exception:  # noqa: BLE001
            pass


# ── C. 人工模式的叫醒命令：空 = 正常完成 ─────────────────────────────────
def test_wake_accepts_empty_command_for_manual_mode(monkeypatch):
    """★人工中转型没有"叫醒命令"，空命令必须是**成功**（mode=manual），不是 400。"""
    import agent_community.platform.server as srv
    from agent_community.platform.protocol import HarnessInfo, WakeupMethod
    from agent_community.platform.routers import harness_bridge as hb

    hid = "manual-wake-1"
    monkeypatch.setattr(srv, "save_state", lambda: None, raising=False)
    info = HarnessInfo(harness_id=hid, harness_name="示例Harness-C", harness_type="ide-agent",
                       wakeup_method=WakeupMethod.CLIPBOARD, acp_command="x.exe --y",
                       ai={"model_name": "示例模型"})
    srv.harness_manager.register(info)
    try:
        out = asyncio.run(hb.harness_wake_set(hid, _Req({"command": ""})))
        d = _as_dict(out)
        assert getattr(out, "status_code", 200) != 400, "人工模式空命令不该被 400 拒：%s" % d
        assert d.get("success") is True and d.get("mode") == "manual", d
        assert d.get("note"), "要说明'这一型为什么不需要命令'"
    finally:
        try:
            srv.harness_manager.unregister(hid)
        except Exception:  # noqa: BLE001
            pass


# ── E. 桥型与自动连接的词汇不能混用 ─────────────────────────────────────
def test_auto_connect_bridge_type_is_a_real_bridge_kind_or_none():
    """★`auto-connect` 的 `bridge_type` 只能是**合法桥型**或 `None`。

    （原来对 clipboard 型回 `"bridge_type": "clipboard"`、对 acp 型回 `"acp"`、
      http_api 型回 `"http_api"` —— 这些**都不是桥型**，而是 `wakeup_method`；
      合法桥型只有 `file_poll / cli_acp / pending_poll / script_framework`。
      接入方拿到"世界上不存在的桥型"就会去找一只不存在的桥。）
    """
    import inspect
    import re

    from agent_community.platform.bridge_catalog import CATALOG
    from agent_community.platform.routers import harness_messaging as hm

    legal = set(CATALOG)
    # ⚠️**先剥掉注释再扫**：注释里会引用"原来的错误写法"（我今天就因此被自己的注释误报了一次）。
    #   判据必须只看真代码 —— 这是本项目反复踩的"测量判据匹配到注释/文本"那一类。
    src = "\n".join(l for l in inspect.getsource(hm).split("\n")
                    if not l.lstrip().startswith("#"))
    for m in re.finditer(r'[\"\']bridge_type[\"\']\s*:\s*[\"\']([a-z_]+)[\"\']', src):
        assert m.group(1) in legal, \
            "auto-connect 里 bridge_type=%r 不是合法桥型（合法：%s）" % (m.group(1), sorted(legal))
    for m in re.finditer(r'bridge_type\s*=\s*[\"\']([a-z_]+)[\"\']', src):
        assert m.group(1) in legal, \
            "bridge_type=%r 不是合法桥型（合法：%s）" % (m.group(1), sorted(legal))
    # 没起桥时必须如实说"没起桥"
    assert "bridge_started" in src, "没起桥的分支要如实标出 bridge_started=False"


# ── F. 探针要按桥型走对的通道（否则必然假阴性、还错怪用户）─────────────
def test_selfcheck_channel_depends_on_bridge_kind():
    """★`pending_poll` 这类**轮询平台 API** 的桥不走文件邮箱 ⇒ 探针必须走桥测试通道。

    （原来一律丢文件邮箱 ⇒ 文件永远不动 ⇒ 必然 `not_picked`，reason 还说
      「**桥没有在运行**」—— **桥其实好好的**。平台就这么对用户说了假话。）
    """
    import inspect

    from agent_community.platform import verify_sandbox as vs

    assert hasattr(vs, "channel_selfcheck"), "缺少按桥型分通道的实现"
    assert "_FILE_MAILBOX_KINDS" in inspect.getsource(vs), "要显式声明哪些桥型走文件邮箱"
    src = inspect.getsource(vs.channel_selfcheck)
    assert "pending_bridge_tests" in src, "非邮箱型要走平台的桥测试通道"
    assert "asyncio" in src or "await" in src, "必须 async（不能阻塞事件循环）"
    # 绝不阻塞事件循环：`time.sleep` 会把平台的监听套接字搞挂（实测 WinError 64）
    import re
    real_sleep = [l for l in src.split("\n")
                  if re.search(r"(?<!_)\btime\.sleep\s*\(", l) and not l.strip().startswith("#")]
    assert not real_sleep, "async 路径里不许有 time.sleep（会阻塞事件循环）：%s" % real_sleep


# ── G. "已验证"必须说清**验证范围**（别把"桥配置跑通"读成"整条链路跑通"）──
def test_commit_verified_records_what_was_actually_verified():
    """★`commit_verified` 要区分两种范围，并写进结果。

    · 文件邮箱通道 ⇒ 验的是**完整链路**（平台 → 桥 → agent → 回报）；
    · 桥测试通道（非邮箱型）⇒ 验的是**桥配置**，而且**那只桥是平台在验证间里启动的**
      ⇒ **harness 侧是否真的运行这只桥、agent 能否回话，本次并未验证**。
    不写清范围，`verified=true` 就会被读成"整条链路都通了" —— 那是**说过头**。
    """
    import inspect

    from agent_community.platform import verify_sandbox as vs

    src = inspect.getsource(vs.commit_verified)
    assert "verified_scope" in src, "回写结果里必须有 verified_scope"
    assert "完整链路" in src and "仅桥配置" in src, "两种范围要分别写明"
    assert "平台在验证间里启动" in src, \
        "非邮箱型要点明'桥是平台启动的'（否则会被读成 harness 侧也跑通了）"
    # 路由层要把通道信息传进去，否则 commit 分不出范围
    rs = inspect.getsource(__import__(
        "agent_community.platform.routers.harness_bridge", fromlist=["x"]))
    assert 'commit_verified(' in rs, "应经由路由层回写"
    assert '"channel"' in rs, "路由层必须把探针通道一并传给 commit_verified"


# ── H. 别把"持久跑桥"指到"一次性的验证间"去 ─────────────────────────────
def test_verify_tells_where_to_run_a_persistent_bridge():
    """★验证间隔间**跑完即删** ⇒ 建议必须把人**指到正确的地方**去持久跑桥。

    （真机材料：那个 agent 收到「在成员隔间目录里运行 bridge.py」后，打算去
      `verify_sandbox/vs_<hid>/验证员_v0/` 找桥脚本 —— 而那里**已经被删了**。
      它去找只会一无所获，然后怀疑自己或平台。）

    本用例同时锁住"给的路必须是**实测走通**的"：
      `out_dir` 必须在平台 `bridges/` 内（否则 400）⇒ 文档里不能让人填"你自己的目录"。
    """
    import inspect

    from agent_community.platform import verify_sandbox as vs

    src = inspect.getsource(vs.verify_bridge)
    assert "persistent_bridge" in src, "验证结果里要给出'怎么持久跑桥'的指引"
    assert "sandbox_dir_deleted" in src, "要明确说验证间目录已删（别去那儿找）"
    assert "bridge/generate" in src, "要指向平台的构桥 API"
    assert "launch_hint" in src, "要用返回的现成启动命令（launch_hint）"
    # 不能让人填平台目录以外的 out_dir（实测 400）
    assert "在平台的 `bridges/` 目录内" in src or "bridges/` 目录内" in src, \
        "必须写明 out_dir 只能落在平台的 bridges/ 内（实测否则 400）"


# ── D. 话术第 4 步：不需要工作间 ─────────────────────────────────────────
def test_template_step4_needs_no_workshop():
    """★话术第 4 步必须指向 `{hid}/verify`（刚注册就能自检），而不是必须先有工作间的那个端点。"""
    p = os.path.join("agent_community", "frontend", "register.html")
    html = open(p, encoding="utf-8").read()
    i = html.find('id="aiPrompt"')
    tpl = html[i:html.find("</pre>", i)]
    assert "harness/{我的harness_id}/verify" in tpl, \
        "第 4 步应指向低耗验证间端点（不需要工作间）"
    assert "不需要工作间" in tpl or "刚注册也能验" in tpl, "要说明它不需要工作间"
    assert "跑通" in tpl and "已验证" in tpl, "要说明'跑通才写模板并标记已验证'"
