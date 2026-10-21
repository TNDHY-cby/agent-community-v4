# -*- coding: utf-8 -*-
"""守卫：**AI 缓存/用量的落点必须跟着 `AC_DATA_DIR` 走**（反私人定制）。

## 为什么有它

`ai_cache` / `ai_usage` 原来在"读配置失败"时**静默**回落到 `~/.agent_community/data/{cache,usage}`。
对用户的实际后果：换一台机器、换一个数据目录，**清缓存清不掉、用量对不上**，
而且因为异常被 `pass` 吞了，**没有人知道为什么**。

这正是老板 2026-10-21 反复强调的"不许私人定制"：
判据＝**换数据目录，不改一行代码，数据必须跟着走**。

本守卫按真结果判：把 `AC_DATA_DIR` 指到临时目录 → 取默认缓存/用量 →
两者的目录都必须在那个临时目录之下，且**不许出现 `~/.agent_community`**。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _reload_with_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("AC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AC_AI_CACHE_DIR", raising=False)
    monkeypatch.delenv("AC_AI_USAGE_DIR", raising=False)

    from agent_community.platform import ai_cache as ac
    from agent_community.platform import ai_usage as au

    ac._default_cache = None
    au._default_usage = None
    importlib.reload(ac)
    importlib.reload(au)
    return ac, au


def test_ai_cache_and_usage_follow_data_dir(monkeypatch, tmp_path):
    ac, au = _reload_with_data_dir(monkeypatch, tmp_path)
    cache_dir = str(getattr(ac.get_default_cache(), "_cache_dir", ""))
    usage_dir = str(getattr(au.get_default_usage(), "_usage_dir", ""))

    assert str(tmp_path) in cache_dir, "缓存目录没跟着 AC_DATA_DIR 走：%r" % cache_dir
    assert str(tmp_path) in usage_dir, "用量目录没跟着 AC_DATA_DIR 走：%r" % usage_dir
    assert ".agent_community" not in cache_dir.replace(str(tmp_path), ""), \
        "缓存目录又回落到了用户主目录（这正是「私人定制」）：%r" % cache_dir
    assert ".agent_community" not in usage_dir.replace(str(tmp_path), ""), \
        "用量目录又回落到了用户主目录：%r" % usage_dir


def test_env_overrides_win(monkeypatch, tmp_path):
    """`AC_AI_CACHE_DIR` / `AC_AI_USAGE_DIR` 必须能盖过 `AC_DATA_DIR`（用户显式指定优先）。"""
    ac, au = _reload_with_data_dir(monkeypatch, tmp_path)
    custom_cache = tmp_path / "mycache"
    custom_usage = tmp_path / "myusage"
    monkeypatch.setenv("AC_AI_CACHE_DIR", str(custom_cache))
    monkeypatch.setenv("AC_AI_USAGE_DIR", str(custom_usage))
    ac._default_cache = None
    au._default_usage = None

    cache_dir = str(getattr(ac.get_default_cache(), "_cache_dir", ""))
    usage_dir = str(getattr(au.get_default_usage(), "_usage_dir", ""))
    assert cache_dir == str(custom_cache), cache_dir
    assert usage_dir == str(custom_usage), usage_dir
