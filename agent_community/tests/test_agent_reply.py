# -*- coding: utf-8 -*-
"""守卫：**「它回的话」不许把我们自己写进去的那句当成它的回复**（老板 2026-10-25「我要看回复」）。

## 为什么要有这一份

`session_post` 的返回体里有个 `reply` 字段 —— 2026-10-25 拿**它自己的库**核实过：
那个 `reply` 就是**我们发出去那句**的原样回显，会话里的模型**根本没开口**。
如果平台把 `reply` 当"它的回复"显示，用户会以为对方答过了（又一次"看起来成功"）。

判据（纯函数，喂真形状的数据）：
  ① 带这次口令的消息＝我们写的，**绝不能**算它的回复；
  ② 我们那条**之后**的新消息才可能是它的回复；
  ③ 时间早于我们那条的旧消息不算；
  ④ 明确标成 `user/human` 的不算（那是人打的字）；
  ⑤ 认三种形状：`messages[]` 的 `text` / 数据库那种 `parts[{type:text,text}]` / 纯文本；
  ⑥ 读回来什么都没有时，`note` 要**如实说"它没回话"**，不许沉默。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

from agent_community.platform.routers.docking import (  # noqa: E402
    _extract_agent_reply, _messages_from_blob, _text_of_message)

NONCE = "收到-AAA111"
OUR_TEXT = "请回复：收到（口令：%s，请原样带回）" % NONCE
POSTED_MS = 1750000000000


def _blob(messages) -> str:
    return json.dumps({"ok": True, "count": len(messages), "messages": messages}, ensure_ascii=False)


def test_our_own_echo_is_never_counted_as_reply():
    """插件把 `reply` 回显成我们那句时（2026-10-25 的真事），不许算"它回了"。"""
    blob = _blob([{"id": "m1", "role": "assistant", "text": OUR_TEXT, "created_at": POSTED_MS}])
    got = _extract_agent_reply(blob, nonce=NONCE, our_text=OUR_TEXT, since_ms=POSTED_MS)
    assert got["ok"] is False, "我们自己的回显不能当它的回复"
    assert got["items"] == []
    assert got["skipped_ours"] == 1
    assert "没回话" in got["note"], "要如实说它没回话（%s）" % got["note"]


def test_real_reply_after_our_message_is_returned():
    blob = _blob([
        {"id": "m1", "role": "assistant", "text": OUR_TEXT, "created_at": POSTED_MS},
        {"id": "m2", "role": "assistant", "text": "收到，已经照办。", "created_at": POSTED_MS + 4000},
    ])
    got = _extract_agent_reply(blob, nonce=NONCE, our_text=OUR_TEXT, since_ms=POSTED_MS)
    assert got["ok"] is True
    assert [i["text"] for i in got["items"]] == ["收到，已经照办。"]
    assert "它回了" in got["note"]


def test_older_messages_are_not_replies():
    blob = _blob([{"role": "assistant", "text": "上一条旧回复", "created_at": POSTED_MS - 60000}])
    got = _extract_agent_reply(blob, nonce=NONCE, our_text=OUR_TEXT, since_ms=POSTED_MS)
    assert got["items"] == [], "比我们那条还早的消息不是这次的回复"


def test_user_role_is_not_the_agent_reply():
    blob = _blob([
        {"role": "assistant", "text": OUR_TEXT, "created_at": POSTED_MS},
        {"role": "user", "text": "我自己打的字", "created_at": POSTED_MS + 1000},
        {"role": "assistant", "text": "它说的话", "created_at": POSTED_MS + 2000},
    ])
    got = _extract_agent_reply(blob, nonce=NONCE, our_text=OUR_TEXT, since_ms=POSTED_MS)
    assert [i["text"] for i in got["items"]] == ["它说的话"]


def test_parts_shape_and_plain_text_are_understood():
    """它库里的正文是 `parts:[{type:text,text:…}]`；读回也可能只给一段纯文本。"""
    parts_msg = {"id": "x", "role": "assistant", "created_at": POSTED_MS + 1000,
                 "parts": [{"type": "text", "text": "用 parts 存的回复"}]}
    assert _text_of_message(parts_msg) == "用 parts 存的回复"
    got = _extract_agent_reply(_blob([parts_msg]), nonce=NONCE, our_text=OUR_TEXT, since_ms=POSTED_MS)
    assert [i["text"] for i in got["items"]] == ["用 parts 存的回复"]

    plain = _extract_agent_reply("好的，收到", nonce=NONCE, our_text=OUR_TEXT)
    assert [i["text"] for i in plain["items"]] == ["好的，收到"]
    assert plain["items"][0]["at_ms"] == 0, "纯文本认不出时间就不要编时间"


def test_empty_readback_speaks():
    got = _extract_agent_reply("", nonce=NONCE, our_text=OUR_TEXT)
    assert got["ok"] is False and got["items"] == []
    assert "没找到任何消息" in got["note"], "读回空也要说清（%s）" % got["note"]
    assert _messages_from_blob("") == []


if __name__ == "__main__":                                   # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
