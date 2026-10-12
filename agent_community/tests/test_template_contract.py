# -*- coding: utf-8 -*-
"""★**模板契约**测试：话术（`register.html` 里那段给外端 agent 的文本）承诺的东西，
必须条条能对上平台 —— 否则就是"照着做必然失败"。

**为什么必须有这个文件**：2026-10-11 真机上，外端 agent 照话术填了
`wakeup_method=pending_poll`（那是**桥型名**，平台只认
`http/file_poll/clipboard/acp/http_api/mcp`）⇒ 注册被 **400 拒**，
agent 只能回"我什么接口都没有"，而用户完全看不出是自己被模板坑了。
**话术是契约文本**，它写错等于平台自己造坑 ⇒ 这个核对必须自动化、可复跑。

本文件锁三件事：
1. 话术里提到的 **`/api/...` 端点必须真实存在**（对着 `routers/` 的挂载列表核）；
2. 话术里教的 **`wakeup_method` 取值必须是合法枚举**（不是桥型名）；
3. 话术里出现的"像字段名"的词，**必须是真实字段**，或者被明确标注为
   "非字段、平台会存进 metadata 供探测用"（`*_hint` 属于这一类）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REGISTER_HTML = Path(__file__).resolve().parents[1] / "frontend" / "register.html"


def _template_text() -> str:
    """取话术本体（`<pre id="aiPrompt">` 里的内容）。"""
    html = REGISTER_HTML.read_text(encoding="utf-8")
    i = html.find('id="aiPrompt"')
    assert i > 0, "找不到话术本体 #aiPrompt（页面结构变了？请同步本测试）"
    j = html.find("</pre>", i)
    return html[i:j]


def _mounted_paths() -> set:
    """平台真实挂载的 HTTP 路径集合。

    ⚠️ 只遍历 `app.routes` 会**漏掉全部 include_router 端点**
    （本机 fastapi 把 `_IncludedRouter` 放进去、`.path is None`）。
    必须展开 `.original_router.routes` —— 这条是项目 AGENTS.md 里写明的坑。
    """
    from agent_community.platform.server import app
    paths = set()
    for r in app.routes:
        p = getattr(r, "path", None)
        if p:
            paths.add(p)
        orig = getattr(r, "original_router", None)
        if orig is not None:
            for rr in getattr(orig, "routes", []) or []:
                pp = getattr(rr, "path", None)
                if pp:
                    paths.add(pp)
    return paths


def test_template_api_endpoints_exist():
    """话术里写的 `/api/...` 必须**真实挂载**（照它做才不会 404）。"""
    tpl = _template_text()
    real = _mounted_paths()
    real_norm = {re.sub(r"\{[^}]+\}", "{x}", p) for p in real}

    bad = []
    for m in re.finditer(r"(/api/[A-Za-z0-9_\-/{}\.]+)", tpl):
        raw = m.group(1).rstrip(".,)")
        # 去掉行尾截断（话术里长路径会换行）：
        #   · 以 `/` 或 `/{` 结尾 ⇒ 明显是被换行截断的前缀，不是完整端点
        #   · 少于两段（如 `/api/harness/`）同此
        if raw.endswith("/") or raw.endswith("/{") or raw.count("/") < 3:
            continue
        norm = re.sub(r"\{[^}]+\}", "{x}", raw)
        # 允许"前缀匹配"：话术可能只写 /api/workshop/{id} 而真实是 /api/workshop/{ws_id}/members
        if norm in real_norm:
            continue
        if any(rp.startswith(norm + "/") or rp == norm for rp in real_norm):
            continue
        bad.append(raw)
    assert not bad, "话术写了平台没有的端点（照做会 404）：%s" % bad


def test_template_wakeup_method_values_are_legal():
    """★话术里教人填的 `wakeup_method` 取值必须是**合法枚举**，不能是桥型名。

    这正是 2026-10-11 那次注册被 400 拒的直接原因（模板写了 `pending_poll`）。
    """
    from agent_community.platform.protocol import WakeupMethod

    legal = {m.value for m in WakeupMethod}
    tpl = _template_text()

    # 话术里以反引号/代码形式出现的 wakeup_method 取值
    bad = []
    for m in re.finditer(r"`(http|file_poll|clipboard|acp|http_api|mcp|cli_acp|pending_poll|script_framework)`", tpl):
        v = m.group(1)
        if v in ("cli_acp", "pending_poll", "script_framework"):
            # 这三个只允许出现在"**不是** wakeup_method / 桥型名"的说明语境里
            ctx = tpl[max(0, m.start() - 120): m.start() + 120]
            if "桥型" not in ctx and "不是" not in ctx and "别把" not in ctx:
                bad.append(v)
    assert not bad, ("话术把桥型名当成 wakeup_method 教人填了（注册会被 400 拒）：%s" % bad)
    assert legal == {"http", "file_poll", "clipboard", "acp", "http_api", "mcp"}, legal


def test_template_field_names_are_real_or_annotated():
    """话术里"像字段名"的词必须是真实字段，或被标注为"非字段"。

    `*_hint` 三个是**有意存在但不在 HarnessInfo 上**的（平台存进 metadata 供探测用）——
    话术里必须写明这一点，否则用户会以为填在注册 body 里就直接生效。
    """
    from agent_community.platform.protocol import HarnessInfo

    fields = set(HarnessInfo.model_fields)
    tpl = _template_text()

    hints = {"process_hint", "port_hint", "path_hint"}
    present_hints = {h for h in hints if h in tpl}
    if present_hints:
        assert "metadata" in tpl or "非字段" in tpl or "不是 HarnessInfo" in tpl, (
            "话术教人填 %s，但没说清它们**不是**注册字段（平台会存进 metadata 供探测用）"
            "—— 用户会以为填在 body 里就生效（实测此前就是这样被静默丢弃的）" % sorted(present_hints))
    # 其它像字段名的词必须在真实字段里
    suspect = set()
    for m in re.finditer(r"\b([a-z_]{4,24})\b", tpl):
        w = m.group(1)
        if w.endswith(("_id", "_dir", "_cmd", "_url", "_path", "_method", "_name")) \
                and w not in fields and w not in hints:
            suspect.add(w)
    # 已知的**响应字段**与散文词（不是注册 body 的字段，但话术里出现是合理的）：
    #   probe_id    —— 自检探针 id（自检返回）
    #   request_id  —— AI 手工模式请求 id
    #   wrapper_path—— `GET /api/harness/{hid}/wake` **返回**的包装脚本路径（实测确认存在）
    suspect -= {"probe_id", "request_id", "wrapper_path"}
    assert not suspect, "话术里出现疑似写错的字段名：%s" % sorted(suspect)


def test_probe_hints_are_persisted_on_registration():
    """★`*_hint` 线索必须**真的被存下来**（此前填了被 pydantic 静默丢弃）。

    放在这个文件里，是因为它和上面那条同源：**"文档承诺了、平台没做"**。
    """
    import inspect

    from agent_community.platform.routers import harness_register as hr

    src = inspect.getsource(hr.harness_probe_register)
    assert "probe_hints" in src, (
        "probe-register 没有把 *_hint 存进 metadata['probe_hints'] ⇒ "
        "外端 agent 按文档填的线索会被静默丢弃（实测过）")
    assert "metadata" in src, "线索要落在 metadata 上"
