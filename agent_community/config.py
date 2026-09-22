"""外端Agent生产合作社（External Agent Community） 配置管理模块

配置文件路径：~/.agent_community/config.json
所有 AI Provider 配置统一从此文件读写，不再依赖命令行传参。
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".agent_community"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "ai_provider": "openai",
    "ai_base_url": "https://api.deepseek.com",
    "ai_api_key": "",
    "ai_model": "deepseek-v4-flash",
    # 内部 AI 接管模式：remote（真实 AI，默认）/ manual（外部接管）/ off（纯规则降级）
    "ai_mode": "remote",
    # manual 模式下等待外部回写的超时秒数，超时降级为纯规则
    "ai_manual_timeout": 120,
    "ai_temperature": 1.0,
    # 思考强度（类 DSH reasoningEfforts）：off / low / medium / high / max
    # off = 不思考（thinking disabled，省钱优先，默认）；medium 按官方映射走 high
    "ai_reasoning_effort": "off",
    "wakeup_enabled": False,
    "port": 9103,
    # ── 规则闸门 RuleGate（L1 纯规则短路，零 token）──
    # 开启后 chat/classify/chat_with_tools 三入口统一先走规则
    "rule_gate_enabled": True,
    # 测试触发词清单：命中仅简单回复，不走 LLM
    "trigger_keywords": [],
    # 配置扩展规则：{"正则": "回复文本"}，重启生效
    "rule_gate_patterns": {},
    # ── 缓存层 AICache（L2 相同输入复用历史回复，零 token）──
    # 只缓存纯函数类 chat（无工具调用）；classify / chat_with_tools 不缓存
    "ai_cache_enabled": True,
    "ai_cache_ttl": 1800,         # 缓存有效期（秒），默认 30 分钟（避免动态查询过期旧数据）
    "ai_cache_max": 512,          # 缓存容量上限（LRU 淘汰）
    # ── 预算熔断 AIUsage（额度保护）──
    "ai_usage_enabled": True,
    "budget_daily": 2.0,          # 日花费阈值（元），超限熔断 AI 调用
    "budget_monthly": 20.0,       # 月花费阈值（元）
    "ai_price_per_1k": 0.0,       # 单价（元/1K tokens），0 表示仅计数不估费
    "data_dir": "",               # 运行数据目录（缓存/用量落盘），空则用 ~/.agent_community/data
}


def load_config() -> dict[str, Any]:
    """读取配置文件，不存在或损坏时返回默认配置。"""
    if not CONFIG_FILE.exists():
        # 首次运行：自动创建默认配置
        _ensure_dir()
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        # 文件损坏：用默认值覆盖
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    # 合并默认值（补齐缺失字段）
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def save_config(data: dict[str, Any]) -> None:
    """写入配置文件。"""
    _ensure_dir()
    # 只保存已知字段
    clean: dict[str, Any] = {}
    for key in DEFAULT_CONFIG:
        clean[key] = data.get(key, DEFAULT_CONFIG[key])
    CONFIG_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    # V-8 修复：api_key 明文落盘时打印警示，提示改用环境变量注入
    if clean.get("ai_api_key"):
        print(
            "[config] 警告: AI API Key 已明文保存到 %s。建议改用环境变量 "
            "AC_AI_API_KEY 注入，避免 key 随配置文件同步/备份泄露。" % CONFIG_FILE,
            flush=True,
        )


def mask_api_key(key: str) -> str:
    """脱敏显示 API Key，只展示前4后4位。"""
    if not key or len(key) <= 8:
        return key or ""
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
