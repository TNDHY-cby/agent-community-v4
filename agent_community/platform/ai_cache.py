"""AI 调用缓存层 — 相同输入复用历史结果，消除重复 API 调用。

- key = sha256(system_prompt + user_message + model)
- value = 回复文本 + 时间戳
- TTL 默认 24h（AC_AI_CACHE_TTL 秒），容量上限（AC_AI_CACHE_MAX 条，LRU 淘汰）
- 落盘 data/cache/ai_cache.json，重启不丢
- 只缓存纯函数类 chat（无工具调用）；chat_with_tools / classify 不缓存
"""

from __future__ import annotations
import hashlib

import logging

logger = logging.getLogger("ac.cache")

#: 同一类失败**每进程只喊一次**（不刷屏，但不许静默 —— 准则⑤）。
_WARNED: set = set()


def _warn_once(key: str, msg: str, exc: BaseException | None = None) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning("%s", msg, exc_info=exc is not None)


import json
import os
from pathlib import Path
import time
from typing import Optional


class AICache:
    def __init__(
        self,
        ttl: int = 0,
        max_entries: int = 0,
        cache_dir: str = "",
    ):
        self.ttl = int(ttl or os.environ.get("AC_AI_CACHE_TTL", "86400"))
        self.max_entries = int(max_entries or os.environ.get("AC_AI_CACHE_MAX", "512"))
        self._cache_dir = cache_dir or os.environ.get("AC_AI_CACHE_DIR", "")
        self._data: dict[str, dict] = {}
        self._path = ""
        if self._cache_dir:
            self._path = os.path.join(self._cache_dir, "ai_cache.json")
            self._load()

    @staticmethod
    def make_key(system_prompt: str, user_message: str, model: str = "") -> str:
        raw = f"{system_prompt}\x00{user_message}\x00{model}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load(self):
        try:
            if self._path and os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._data = data
        except Exception as e:
            print(f"[ai-cache] 加载缓存失败: {str(e)[:100]}", flush=True)

    def _save(self):
        try:
            if not self._path:
                return
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            print(f"[ai-cache] 落盘失败: {str(e)[:100]}", flush=True)

    def get(self, key: str) -> Optional[str]:
        item = self._data.get(key)
        if not item:
            return None
        if time.time() - float(item.get("ts", 0)) > self.ttl:
            self._data.pop(key, None)
            return None
        return item.get("reply")

    def set(self, key: str, reply: str):
        self._data[key] = {"reply": reply, "ts": time.time()}
        # LRU 简化：超容量时清最旧
        if len(self._data) > self.max_entries:
            oldest = min(self._data, key=lambda k: self._data[k].get("ts", 0))
            self._data.pop(oldest, None)
        self._save()

    def clear(self):
        self._data = {}
        if self._path and os.path.exists(self._path):
            try:
                os.remove(self._path)
            except OSError as _e:      # 删不掉 ⇒ 之后可能一直读到旧缓存值
                _warn_once("cache_remove_failed",
                           "删旧缓存文件失败（可能读到旧值）：%s: %s"
                           % (type(_e).__name__, str(_e)[:120]), _e)

    @property
    def size(self) -> int:
        return len(self._data)


_default_cache: Optional[AICache] = None


def get_default_cache() -> AICache:
    """全局默认缓存（惰性创建，data_dir/cache 优先，降级到用户目录）。"""
    global _default_cache
    if _default_cache is None:
        ttl = 0
        max_entries = 0
        cache_dir = ""
        try:
            from ..config import load_config
            cfg = load_config()
            ttl = int(cfg.get("ai_cache_ttl") or 0)
            max_entries = int(cfg.get("ai_cache_max") or 0)
            _root = cfg.get("data_dir") or ""
            if _root:
                cache_dir = os.path.join(_root, "cache")
        except Exception as _e:            # 配置读不到 ⇒ 只有缓存开关用默认，**目录仍按一处真相走**
            _warn_once("cache_cfg_read_failed",
                       "读 AI 缓存配置失败（ttl/上限按默认）：%s: %s"
                       % (type(_e).__name__, str(_e)[:120]), _e)
        # ★2026-10-21 反私人定制：缓存目录**只认一处真相**（AC_AI_CACHE_DIR > AC_DATA_DIR/cache），
        #   不再回落到 ~/.agent_community/... —— 那等于把数据写进某台机器的某个用户主目录。
        # 优先级：显式 AC_AI_CACHE_DIR > 配置里的 data_dir > 一处真相（AC_DATA_DIR/cache）
        if os.environ.get("AC_AI_CACHE_DIR"):
            cache_dir = os.environ["AC_AI_CACHE_DIR"]
        if not cache_dir:
            try:
                from .env_spec import data_dir as _data_dir
                cache_dir = str(Path(_data_dir()) / "cache")
            except Exception as _e:            # noqa: BLE001
                _warn_once("cache_dir_resolve_failed",
                           "解析 AI 缓存目录失败，本次不落盘缓存：%s: %s"
                           % (type(_e).__name__, str(_e)[:120]), _e)
                cache_dir = ""
        _default_cache = AICache(ttl=ttl, max_entries=max_entries, cache_dir=cache_dir)
    return _default_cache
