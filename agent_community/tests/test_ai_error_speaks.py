# -*- coding: utf-8 -*-
"""`[Error: ]` 事件（2026-10-15 朋友 v4.8.0 回执）：**错误文本永远不许空原因**。

**实测现象**：朋友第一次跑，`task-result` 的结果正文只有一个 **`[Error: ]`** —— 九个字符、
**不含任何原因**，用户完全不知道该改什么。

**真因**（两层）：
  ① 全新安装**没配任何 AI**，而 `OpenAICompatibleProvider` 的兜底是**占位配置**
     （`base_url=https://api.example.com`、`model=example-model-flash`）
     ⇒ 平台"看起来配了 AI"，任务走 AI 路径并必然失败；
  ② 失败时 `str(e)` 恰好为空（连接类异常常见），而原实现直接 `f"[Error: {e}]"` ⇒ **空原因**。

**判据（本文件按真结果判，不读源码文本）**：
  ① 任何异常形态下，错误文本都**含原因**（异常信息或异常类型名），且**绝不等于** `[Error: ]`；
  ② HTTP 非 200 但服务端没给正文 ⇒ 明说「服务端没有给说明」；
  ③ 还是占位配置时附一句「**你其实还没配 AI** + 怎么改」；**真配好了就不许再唠叨**；
  ④ **真走一次 `chat()`**（喂一个"抛空信息异常"的假 client）⇒ 返回的错误文本必须能读。
"""
from __future__ import annotations

import asyncio

from agent_community.platform.ai_provider import (
    PLACEHOLDER_BASE_URL,
    PLACEHOLDER_MODEL,
    OpenAICompatibleProvider,
    ai_error_from,
    ai_is_error,
)


class _EmptyStrError(Exception):
    """像连接类异常那样：`str(e)` 是空串（这正是 `[Error: ]` 的来源）。"""

    def __str__(self) -> str:
        return ""


def _placeholder_provider() -> OpenAICompatibleProvider:
    """**全新安装**的默认形态（占位域名 + 占位模型 + 没有 key）。"""
    return OpenAICompatibleProvider(base_url="https://" + PLACEHOLDER_BASE_URL,
                          model=PLACEHOLDER_MODEL, api_key="")


def _configured_provider() -> OpenAICompatibleProvider:
    """真配好的形态（用户自己的地址与模型）。"""
    return OpenAICompatibleProvider(base_url="https://api.my-own-endpoint.test/v1",
                          model="my-real-model", api_key="sk-your-key")


def test_error_text_never_has_an_empty_reason():
    """① 各种异常形态都要**说得清原因**。"""
    for exc in (Exception(""), _EmptyStrError(), RuntimeError("连接被拒绝"), OSError()):
        txt = ai_error_from(exc=exc)
        assert txt.startswith("[Error:"), txt
        assert txt.strip() != "[Error: ]", (
            "★又出现空原因的错误文本了 —— 用户会看到 `[Error: ]` 而不知道发生了什么")
        # 空信息的异常必须退到**类型名**（这就是"说得清"的最低标准）
        if not str(exc).strip():
            assert type(exc).__name__ in txt, "空信息时应退到异常类型名：%r" % txt
    # 连异常都没有
    assert ai_error_from().strip() != "[Error: ]"
    assert "未知错误" in ai_error_from()


def test_http_error_without_body_says_so():
    """② 服务端没给正文时说清楚，而不是留一段空白。"""
    txt = ai_error_from(status=402, body="")
    assert "402" in txt and "没有给说明" in txt, txt
    txt2 = ai_error_from(status=403, body="  ")
    assert "403" in txt2 and "没有给说明" in txt2, txt2
    # 有正文时照原样带上（排障要看的就是它）
    txt3 = ai_error_from(status=400, body='{"error":"bad model"}')
    assert "bad model" in txt3, txt3


def test_placeholder_config_gets_a_how_to_fix_hint():
    """③ 还停在占位配置 ⇒ 必须告诉用户"你其实还没配 AI"以及怎么改。"""
    txt = ai_error_from(exc=OSError(), provider=_placeholder_provider())
    assert "还没配 AI" in txt, txt
    assert "AC_AI_BASE_URL" in txt and "ai_mode" in txt, "要给出**具体怎么改**：%s" % txt
    # 两个占位值都要被点名（用户才知道自己看到的是哪一个）
    assert PLACEHOLDER_BASE_URL in txt and PLACEHOLDER_MODEL in txt, txt


def test_configured_provider_gets_no_nag():
    """③反 真配好了就不许再唠叨占位配置（否则是误导）。"""
    txt = ai_error_from(exc=RuntimeError("余额不足"), provider=_configured_provider())
    assert "还没配 AI" not in txt, "用户明明配了 AI，却被说成没配：%s" % txt
    assert "余额不足" in txt, txt


def test_real_chat_with_empty_exception_still_explains():
    """④ 真走一次 `chat()`：假 client 抛**空信息**异常 ⇒ 返回的错误文本必须能读。"""

    class _BoomClient:
        async def post(self, *a, **k):
            raise _EmptyStrError()

    async def _run():
        p = _placeholder_provider()
        p._client = _BoomClient()          # 已有 client 时 _get_client() 直接用它
        return await p.chat("sys", "user")

    reply = asyncio.run(_run())
    assert ai_is_error(reply), "应被识别为错误文本：%r" % reply
    assert reply.strip() != "[Error: ]", "★真调用路径又吐出了空原因的错误"
    assert "_EmptyStrError" in reply, reply
    assert "还没配 AI" in reply, "占位配置下要顺带说清「没配 AI」：%s" % reply
