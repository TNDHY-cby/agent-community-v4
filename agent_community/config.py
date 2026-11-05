"""外端Agent生产合作社（External Agent Community） 配置管理模块

配置文件路径：
  · 默认（老用户照旧）：`~/.agent_community/config.json`
  · **显式设了 `AC_DATA_DIR`**：`<AC_DATA_DIR>/config/config.json`
    —— 为什么（2026-10-20，V61 公司间合作/双实例）：`AC_DATA_DIR` 的语义是"这个实例的全部自有数据"，
      而配置文件里有 **AI 接口/密钥/模式** 这类会互相影响的设置。原先它写死在用户目录 ⇒
      **同一台机器上跑两个实例（公司 A / 公司 B）会共用同一份配置**：改 B 的模型就把 A 也改了，
      隔离形同虚设。现在"数据目录在哪儿，配置就在哪儿"，不设 `AC_DATA_DIR`（老用户）行为完全不变。
所有 AI Provider 配置统一从此文件读写，不再依赖命令行传参。
"""

from __future__ import annotations
from .platform_address import DEFAULT_PORT as _DEFAULT_PORT
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

def _resolve_config_dir() -> Path:
    """配置目录：显式设了 `AC_DATA_DIR` ⇒ 跟着它走（每实例一套配置）；否则用户级（老用户照旧）。"""
    root = (os.environ.get("AC_DATA_DIR") or "").strip()
    if root:
        return Path(root) / "config"
    return Path.home() / ".agent_community"


CONFIG_DIR = _resolve_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    # ★2026-11-05（老板拍"好"）：**开箱不假装配了 AI**。
    #   原来是 `openai` + 示例域名 + 占位模型 ⇒ 界面显示「已配置」，实际解析不到任何服务
    #   （违反产品化红线②"开箱不许是看起来配好、其实必然失败的假数据"）。
    #   现在默认空 ⇒ `/api/status` 走 "not-configured" 并说清怎么配；
    #   老配置文件里若仍是占位值，`_ai_health_snapshot()` 会判成 `placeholder` 并说明"等于没配"。
    "ai_provider": "",
    "ai_base_url": "",
    "ai_api_key": "",
    "ai_model": "",
    # 内部 AI 接管模式：remote（真实 AI，默认）/ manual（外部接管）/ off（纯规则降级）
    "ai_mode": "remote",
    # manual 模式下等待外部回写的超时秒数，超时降级为纯规则
    "ai_manual_timeout": 120,
    "ai_temperature": 1.0,
    # 思考强度（类 示例Harness-E reasoningEfforts）：off / low / medium / high / max
    # off = 不思考（thinking disabled，省钱优先，默认）；medium 按官方映射走 high
    "ai_reasoning_effort": "off",
    # ── V-16 §四 连通性自检（2026-10-05 负责人拍板实施）──
    # ⚠️ 两个开关**默认 False**：负责人要求"避免默认产生计费流量"。
    # 自检会真发一次极小探针（max_tokens=1）验证"保存成功 ≠ 能用"。
    "ai_verify_on_save": False,      # 保存配置时顺带自检（会出网）
    "ai_verify_on_startup": False,   # 服务启动时自检（会出网；默认关，避免每次重启都计费）
    "ai_verify_cache_ttl": 600,      # 自检结论缓存 TTL（秒），默认 10 分钟
    # 探针超时（秒）。⚠️ 别照 /api/ai/models 的 8s 抄：那是**列清单**（实测 0.37s），
    # 自检是**一次真实 chat 调用** —— 实测真实服务 max_tokens=1 也要 37.8s。
    # 默认 45s（留余量）：宁可等久一点，也不把"慢但能用"误判成"连不上"。
    "ai_verify_timeout": 45,
    "wakeup_enabled": False,
    # ★2026-10-14：这里是**第三套**端口默认值（另两套在 cli.py 与 server.py），
    #   统一引用唯一真相 platform_address（老板：「本地端口因人而异」）。
    "port": _DEFAULT_PORT,
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
    # ── P1 身份凭证 Agent-Token（HMAC-SHA256 签名密钥）──
    # 空则表示尚未生成；platform/identity.py 首次使用时会生成 secrets.token_hex(32)
    # 并写回本文件持久化（幂等，已存在则沿用）。
    "agent_token_secret": "",
    # ── V-17 会话关闭兜底 ──
    # close_requested 超过该秒数仍未等到 harness 自述 → 平台兜底置 closed（惰性收敛，不轮询）。
    # 默认 600s = 10 × harness 心跳超时窗（60s）。设计稿：design-docs/V17_会话关闭兜底设计.md
    "session_close_grace_seconds": 600,
}


#: 环境变量 → 配置键（**只覆盖"有这个配置键"的项**；空值不覆盖）
_ENV_OVERRIDES = {
    "port": "AC_PORT",
    "data_dir": "AC_DATA_DIR",
    "ai_provider": "AC_AI_PROVIDER",
    "ai_base_url": "AC_AI_BASE_URL",
    "ai_api_key": "AC_AI_API_KEY",
    "ai_model": "AC_AI_MODEL",
    "ai_reasoning_effort": "AC_AI_REASONING_EFFORT",
    "ai_cache_enabled": "AC_AI_CACHE_ENABLED",
    "ai_cache_ttl": "AC_AI_CACHE_TTL",
    "ai_cache_max": "AC_AI_CACHE_MAX",
    "ai_usage_enabled": "AC_AI_USAGE_ENABLED",
    "budget_daily": "AC_BUDGET_DAILY",
    "budget_monthly": "AC_BUDGET_MONTHLY",
    "ai_price_per_1k": "AC_AI_PRICE_PER_1K",
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
    # ★2026-10-14（可移植性·工程层）：**环境变量优先于配置文件** ——
    #   登记的键在此统一覆盖，别让"环境差异"散落在各处各读一次。
    try:
        from .platform import env_spec as _es
        for _cfg_key, _env_name in _ENV_OVERRIDES.items():
            _v = _es.get(_env_name)
            if _v != "":
                merged[_cfg_key] = _v
    except Exception:  # noqa: BLE001
        pass
    # 解密 API Key：dpapi: 前缀为 DPAPI 密文，否则视为历史明文直接透传
    if merged.get("ai_api_key"):
        merged["ai_api_key"] = _decrypt_secret(merged["ai_api_key"])
    return merged


def peek_config() -> dict[str, Any]:
    """**只读**读配置：文件不存在/损坏时返回默认值，**绝不创建或改写任何文件**。

    ★2026-10-20（V60 踩到的真问题）：`load_config()` 在首次运行时会**自动创建配置文件**
    （`save_config(DEFAULT_CONFIG)`，见上面那行注释）。而 `/api/status` 是**高频只读端点**，
    界面/看门狗/CLI 都在轮询它 —— 给只读端点接上"建文件"副作用有两个坏后果：
      ① 在只读挂载 / 容器里打一次状态就报错或留下不该有的文件；
      ② 测试里打一次 `/api/status` 就会在仓库 `data/` 造出配置文件，
         被 `agent_community/tests/conftest.py` 的"防污染守卫"抓成「测试污染了真实数据」
         （实测：Linux 侧双平台判据就是这么红的）。
    只读场景一律用这个函数；要"顺手初始化配置"的地方才用 `load_config()`。
    """
    merged: dict[str, Any] = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged.update(data)
    except Exception:  # noqa: BLE001 — 损坏/不可读都退回默认值，**不修盘**
        merged = dict(DEFAULT_CONFIG)
    # 与 load_config 同一套"环境变量优先"（只读，不落盘）
    try:
        from .platform import env_spec as _es
        for _cfg_key, _env_name in _ENV_OVERRIDES.items():
            _v = _es.get(_env_name)
            if _v != "":
                merged[_cfg_key] = _v
    except Exception:  # noqa: BLE001
        pass
    try:
        if merged.get("ai_api_key"):
            merged["ai_api_key"] = _decrypt_secret(merged["ai_api_key"])
    except Exception:  # noqa: BLE001
        pass
    return merged


def save_config(data: dict[str, Any]) -> None:
    """写入配置文件。"""
    _ensure_dir()
    # 只保存已知字段
    clean: dict[str, Any] = {}
    for key in DEFAULT_CONFIG:
        clean[key] = data.get(key, DEFAULT_CONFIG[key])
    # V-9 修复：ai_api_key 落盘前加密（Windows DPAPI，当前用户绑定；非 Windows **明文回退**）
    # ★2026-10-15（可移植性缺陷**条 D**）：**消息必须与磁盘上的事实一致**。
    #   原来这里**无条件**打印「AI API Key 已加密保存（DPAPI 当前用户绑定）」——
    #   而 `_encrypt_secret` 在非 Windows 上是**原样返回**（明文回退）。
    #   真 Linux（WSL）实测复现：
    #       屏幕 → 「[config] 提示: AI API Key 已加密保存到 …（DPAPI 当前用户绑定）」
    #       磁盘 → `"ai_api_key": "sk-repro-0123456789abcdef"`（**原文**）
    #   ⇒ 一句话同时违反两条承诺：README「本地配置里会加密保存」、
    #     `design-docs/安全披露_CDP镜像与密钥存储.md`「非 Windows 降级为明文保存**并在保存时打印告警**」。
    #     对**安全属性**撒谎是最坏的一类"失败不说话"。
    #   现在按**实际落盘形态**分档说，并把"那该怎么办"一并说清。
    if clean.get("ai_api_key"):
        clean["ai_api_key"] = _encrypt_secret(clean["ai_api_key"])
    CONFIG_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    if clean.get("ai_api_key"):
        if str(clean["ai_api_key"]).startswith(_SECRET_PREFIX):
            print(
                "[config] 提示: AI API Key 已用 DPAPI 加密保存到 %s"
                "（当前用户绑定：换用户或换机器都解不开）" % CONFIG_FILE,
                flush=True,
            )
        else:
            print(
                "[config] 警告: 当前系统没有 DPAPI（非 Windows），AI API Key 以**明文**保存在 %s —— "
                "请限制该文件权限（如 chmod 600），或改用环境变量 AC_AI_API_KEY（不落盘）"
                % CONFIG_FILE,
                flush=True,
            )


# ── 密钥落盘保护（DPAPI）──
# V-9 单体拆分：加解密/掩码/危险命令检测统一下沉 platform/core/security.py，此处仅引用
from .platform.core.security import (
    _encrypt_secret,
    _decrypt_secret,
    _is_windows,
    _dpapi,
    _SECRET_PREFIX,
    mask_api_key,
)


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
