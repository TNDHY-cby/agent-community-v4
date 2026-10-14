# -*- coding: utf-8 -*-
"""platform/env_spec：**环境变量的唯一登记表 + 校验**。

老板 2026-10-14 的「可移植性三层」要求（指令层 / 工程层 / 验收层）里，工程层写的是：

    「所有环境差异从环境变量读取，**集中到 config 模块并做校验**」
    「默认值仅用于开发且可被环境变量覆盖；**生产缺必需变量应启动失败并提示**」

## 为什么必须有这个文件（动手前的实测）

2026-10-14 摸底：全仓被 git 跟踪的 `.py` 里读了 **48 个环境变量**，
散在 30 多个文件里，**没有登记表、没有校验、没有文档**；同一件事还有别名
（`AC_PORT` 与 `AC4_SERVER_PORT`；`AC4_BASE_URL` 与 `AC_BASE_URL`）。
⇒ 后果：使用者不知道该设什么；漏设时表现为"某个功能静默不工作"，而不是**启动时说清**。

## 本文件的契约

- `SPEC` 是**唯一**的环境变量清单：README 的环境变量表、`.env.example`、启动校验
  **全部由它生成/驱动**（`tools/gen_env_docs.py` 负责生成，别手抄 —— 规则 20）。
- `assert_ready()`：缺**必需**变量时**抛 `EnvError` 并列出"怎么修"**（不静默降级）。
- **开箱即用**优先：默认一个变量都不设也能跑（与产品准则④"装好就能用"一致）；
  只有"你启用了某个需要凭据的功能"时，才把对应变量视为**必需**（条件必需）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

#: 一处真相：端口默认值来自 platform_address（别在这里再写一个字面量数字）
from ..platform_address import DEFAULT_PORT, ENV_PORT

#: 运行模式（production 会对"条件必需"更严：缺了直接不让起来）
ENV_MODE = "AC_ENV"
MODE_PRODUCTION = "production"


@dataclass(frozen=True)
class Var:
    """一个环境变量的登记项。"""
    name: str
    summary: str
    default: str = ""
    kind: str = "feature"          # core（部署核心）| feature（功能开关）| dev（开发/测试内部）
    required: bool = False         # 无条件必需
    required_when: str = ""        # 条件必需的**人话说明**（判定在 _conditional_issues）
    example: str = ""
    aliases: tuple = field(default_factory=tuple)


# ══════════════════════════════════════════════════════════════════════════
# 登记表（**唯一真相**）
# ══════════════════════════════════════════════════════════════════════════
SPEC: dict[str, Var] = {v.name: v for v in [
    # ── 部署核心 ──────────────────────────────────────────────────────────
    Var(ENV_MODE, "运行模式：留空=开发；`production` 时对条件必需项更严格（缺了不让启动）",
        default="", kind="core", example="production"),
    Var(ENV_PORT, "平台监听端口（**端口因人而异**，命令行 `--port` 优先级更高）",
        default=str(DEFAULT_PORT), kind="core",
        example="18920", aliases=("AC4_SERVER_PORT",)),
    Var("AC_HOST", "平台监听地址。默认 0.0.0.0（容器/局域网可达）；只想本机访问填 127.0.0.1",
        default="0.0.0.0", kind="core", example="127.0.0.1"),
    Var("AC_DATA_DIR", "运行数据目录（工作间/审计/记忆/桥产物都落这里）。**换目录不改代码**就靠它",
        default="<仓库>/agent_community/data", kind="core", example="/var/lib/agent-community"),
    Var("AC4_TOKEN", "平台访问令牌（跨机/对外访问时必设；空=仅本机可用）",
        default="", kind="core", example="sk-your-token",
        required_when="对外暴露平台（AC_HOST 非回环）时"),
    Var("AC4_SHUTDOWN_PIN", "关停口令（空=关停端点仅允许本机来源 + 二次确认）",
        default="", kind="core", example="your-pin"),

    # ── AI Provider（可选：不配也能跑，走规则兜底）────────────────────────
    Var("AC_AI_PROVIDER", "AI 后端：`openai`（OpenAI 兼容）/`ollama`/`http_callback`；空=不接 AI",
        default="", kind="core", example="openai"),
    Var("AC_AI_BASE_URL", "OpenAI 兼容基址（**按你的服务商填**，别照抄示例）",
        default="", kind="core", example="https://api.example.com/v1",
        required_when="AC_AI_PROVIDER=openai 时"),
    Var("AC_AI_API_KEY", "AI 服务密钥（**绝不入库**；本地配置里会加密保存）",
        default="", kind="core", example="sk-your-key",
        required_when="AC_AI_PROVIDER=openai（或需要鉴权的服务）时"),
    Var("AC_AI_MODEL", "模型名", default="", kind="core", example="example-model-flash"),
    Var("AC_AI_REASONING_EFFORT", "思考强度：off/low/medium/high/max（默认 off 省钱）",
        default="off", kind="feature"),
    Var("AC_AI_CACHE_ENABLED", "相同输入复用历史回复（L2 缓存，零 token）", default="1", kind="feature"),
    Var("AC_AI_CACHE_TTL", "缓存有效期（秒）", default="1800", kind="feature"),
    Var("AC_AI_CACHE_MAX", "缓存容量上限（LRU）", default="512", kind="feature"),
    Var("AC_AI_CACHE_DIR", "缓存落盘目录（默认随 AC_DATA_DIR）", default="", kind="feature"),
    Var("AC_AI_USAGE_ENABLED", "用量/预算统计开关", default="1", kind="feature"),
    Var("AC_AI_USAGE_DIR", "用量落盘目录（默认随 AC_DATA_DIR）", default="", kind="feature"),
    Var("AC_BUDGET_DAILY", "日预算（元）；超限熔断 AI 调用", default="2.0", kind="feature"),
    Var("AC_BUDGET_MONTHLY", "月预算（元）", default="20.0", kind="feature"),
    Var("AC_AI_PRICE_PER_1K", "单价（元/1K tokens）；0=只计数不估费", default="0.0", kind="feature"),
    Var("AC_HTTP_CALLBACK_URL", "http_callback 模式的回调地址", default="", kind="feature",
        required_when="AC_AI_PROVIDER=http_callback 时"),
    Var("AC_OLLAMA_HOST", "Ollama 地址", default="http://127.0.0.1:11434", kind="feature"),
    Var("AC_OLLAMA_MODEL", "Ollama 模型名", default="", kind="feature"),
    Var("AC_LOCAL_AI_AUTOSTART", "本地模型自动拉起（1/0）", default="1", kind="feature"),
    Var("AC_LOCAL_AI_QUICK_WAIT", "本地模型快等多长（秒）", default="12", kind="feature"),
    Var("AC_LOCAL_AI_BOOT_WAIT", "本地模型冷启等待（秒）", default="90", kind="feature"),

    # ── 运行行为开关 ──────────────────────────────────────────────────────
    Var("AC_OFFLINE_AUTO", "掉线自动扫描抢救（**默认关**）", default="0", kind="feature"),
    Var("AC_CDP_MIRROR_ENABLED", "CDP 镜像（调试用）", default="0", kind="feature"),
    Var("AC_VERIFY_NO_START_BRIDGE", "接入自检时**不**自动起桥（测试/排障用）", default="", kind="dev"),
    Var("AC_PYTHON", "平台用哪个 Python 解释器去跑脚本/桥（默认当前解释器）",
        default="", kind="core", example="/usr/bin/python3"),

    # ── 多实例 / A2A 侧（可选）──────────────────────────────────────────
    Var("AC4_BASE_URL", "本平台对外基址（A2A/gRPC/MCP 回连平台用）",
        default="http://127.0.0.1:<AC_PORT>", kind="feature",
        aliases=("AC_BASE_URL",)),
    Var("AC4_A2A_PORT", "A2A 服务端口", default="9104", kind="feature"),
    Var("AC4_GRPC_PORT", "gRPC 网关端口", default="9105", kind="feature"),
    Var("AC4_A2A_RELAY", "A2A 接力插件开关（默认关）", default="0", kind="feature"),
    Var("AC4_RELAY_INBOX", "接力信箱目录", default="", kind="feature"),
    Var("AC4_PEER_URL", "对端平台地址（公司间协作用）", default="", kind="feature",
        required_when="要用 A2A 客户端连对端时"),
    Var("AC4_COMPANY_ID", "本/对端公司标识", default="", kind="feature",
        required_when="设了 AC4_PEER_URL 时"),
    Var("AC4_COMPANY_TOKEN", "公司级令牌", default="", kind="feature",
        required_when="设了 AC4_PEER_URL 时"),

    # ── 桥/脚本侧（由平台注入给桥；手工跑桥时才需要自己设）──────────────
    Var("AC_ACP_COMMAND", "ACP 拉起命令（桥用它拉起 agent）", default="", kind="core",
        required_when="手工跑 ACP 类桥且平台未注入时"),
    Var("AC_ACP_CWD", "ACP 命令的工作目录", default="", kind="feature"),
    Var("AC_ACP_REPO", "ACP 仓库根（部分 harness 需要）", default="", kind="feature"),
    Var("AC_AGENT_TOKEN", "Agent-Token（桥回报鉴权；平台注册时签发，通常由平台注入）",
        default="", kind="core", required_when="平台为该 harness 启用了 Agent-Token 时"),

    # ── 开发 / 测试内部（发布面也会带上，但使用者一般不用管）─────────────
    Var("AC_FAKE_REPLY", "假回复（离线跑测试用；**生产别设**）", default="", kind="dev"),
    Var("AC_REGRESSION_BASE", "安全回归指向的地址（测试用）", default="", kind="dev"),
    Var("AC_ACTION", "动作脚本上下文标记（平台注入）", default="", kind="dev"),
    Var("AC_HARNESS_ID", "动作脚本上下文：harness 标识（平台注入）", default="", kind="dev"),
    Var("TEMP", "临时目录（系统自带；Windows 下平台会读它）", default="", kind="dev"),
    Var("USERNAME", "当前用户名（系统自带；仅在探测/显示里用到）", default="", kind="dev"),
    Var("DEEPSEEK_API_KEY", "**兼容用**：旧版示例桥读它；新配置请用 AC_AI_API_KEY",
        default="", kind="dev"),
]}


class EnvError(RuntimeError):
    """环境不满足启动条件（**带修复清单**，不是一句"配置错误"）。"""


def get(name: str, default: Optional[str] = None) -> str:
    """按登记表读一个变量（未登记也能读，但会走调用方给的默认值）。

    ⚠️ 新代码请优先用本函数而不是裸 `os.environ`：这样"表里有没有它"是可检查的。
    """
    v = os.environ.get(name)
    if v not in (None, ""):
        return v
    if default is not None:
        return default
    spec = SPEC.get(name)
    if spec and spec.default and not spec.default.startswith("<"):
        return spec.default
    for a in (spec.aliases if spec else ()):
        av = os.environ.get(a)
        if av not in (None, ""):
            return av
    return ""


def get_bool(name: str, default: bool = False) -> bool:
    v = get(name)
    if v == "":
        return default
    return str(v).strip().lower() not in ("0", "false", "off", "no", "")


def is_production() -> bool:
    return get(ENV_MODE).strip().lower() == MODE_PRODUCTION


def _conditional_issues() -> list[dict]:
    """**条件必需**判定：只在"用户启用了需要它的功能"时才报缺失。"""
    issues: list[dict] = []

    def miss(name: str, why: str, fix: str):
        issues.append({"name": name, "why": why, "fix": fix, "level": "error"})

    prod = is_production()

    # ① AI provider：选了 openai 却没给 key/基址 —— 调用必然失败，该在启动时就说
    prov = get("AC_AI_PROVIDER").strip().lower()
    if prov == "openai":
        if not get("AC_AI_BASE_URL"):
            miss("AC_AI_BASE_URL", "AC_AI_PROVIDER=openai 但没有基址",
                 "填你服务商的 OpenAI 兼容地址，例如 https://api.example.com/v1")
        if not get("AC_AI_API_KEY"):
            miss("AC_AI_API_KEY", "AC_AI_PROVIDER=openai 但没有密钥",
                 "填你的 AI 服务密钥（**别提交进仓库**；本地配置会加密保存）")
    if prov == "http_callback" and not get("AC_HTTP_CALLBACK_URL"):
        miss("AC_HTTP_CALLBACK_URL", "AC_AI_PROVIDER=http_callback 但没有回调地址",
             "填回调地址，例如 https://your-host/callback")

    # ② 对外暴露却无令牌 —— **注意判据**：平台默认就是 `AC_HOST=0.0.0.0` 且
    #    未配 token 时**仅允许本机访问**（靠 Origin/Referer 检查）⇒ 这是**受支持的默认**，
    #    不是配置错误。所以这里只能是 **warn**（提醒你"外人连不上，是有意的"），
    #    **绝不能**报成 error —— 否则"装好就能用"会被这条闸门自己否掉
    #    （实测：第一版把它当 error，空环境下 ok=False，是错的）。
    host = get("AC_HOST").strip()
    _exposed_now = bool(host) and host not in ("127.0.0.1", "localhost", "::1")
    # ⚠️ 生产 + 对外暴露时，这条由 ④ 升格为**硬错**上报（别在这一条里报两遍）
    if _exposed_now and not get("AC4_TOKEN") and not prod:
        issues.append({"name": "AC4_TOKEN",
                       "why": f"AC_HOST={host}（非回环）且未设访问令牌",
                       "fix": "想让外部机器访问就设 AC4_TOKEN=<你的令牌>；"
                              "只想本机用则保持现状（平台会拒绝非本机请求）",
                       "level": "warn"})

    # ③ 公司间协作：有对端就该有身份
    if get("AC4_PEER_URL"):
        for n in ("AC4_COMPANY_ID", "AC4_COMPANY_TOKEN"):
            if not get(n):
                miss(n, "设了 AC4_PEER_URL（要连对端平台）但缺身份",
                     f"设 {n}=<对端给的值>")

    # ④ 生产模式：**这里必须是硬错**（老板 2026-10-14 的三层制度：「生产环境缺失必需项时
    #    启动直接失败并提示缺少什么」）。
    #    ⚠️ 2026-10-14 实测踩到的坑：本段第一版把两条都写成 `warn`，理由是"别让开箱即用被
    #    自己的闸门否掉"——但那只对**开发**成立。结果是 `AC_ENV=production` 一路警告着
    #    照样起服务，"生产缺必需项就失败"这条承诺**等于没做**（文档承诺了不存在的闸门）。
    #    正确切法：**开发 → 警告；生产 → 失败**。开箱即用不受影响（开发不设 AC_ENV）。
    if prod:
        if not get("AC_DATA_DIR"):
            issues.append({"name": "AC_DATA_DIR",
                           "why": "production 模式没有显式指定数据目录（会落在仓库内）",
                           "fix": "设 AC_DATA_DIR=/var/lib/agent-community（或你的数据卷路径）",
                           "level": "error"})
        exposed = bool(host) and host not in ("127.0.0.1", "localhost", "::1")
        if not get("AC4_SHUTDOWN_PIN"):
            # 对外暴露时缺关停口令 = 硬错；只在本机跑则只是提醒（该端点本来就只允许本机来源）
            issues.append({"name": "AC4_SHUTDOWN_PIN",
                           "why": "production 模式没设关停口令"
                                  + ("，而平台对外暴露（AC_HOST 非回环）" if exposed else ""),
                           "fix": "设一个口令（别提交进仓库）"
                                  + ("" if exposed else "；只想本机可关停可暂不设"),
                           "level": "error" if exposed else "warn"})
        if not get("AC4_TOKEN") and exposed:
            # 回环以外的生产部署：访问令牌从"提醒"升格为**硬错**
            issues.append({"name": "AC4_TOKEN",
                           "why": f"production 模式且 AC_HOST={host}（对外暴露）但没有访问令牌",
                           "fix": "设 AC4_TOKEN=<你的令牌>，或用 AC_HOST=127.0.0.1 只在本机跑",
                           "level": "error"})
    return issues


def check_requirements() -> dict:
    """体检：返回 `{"ok": bool, "errors": [...], "warnings": [...], "checked": n}`（不抛错）。"""
    issues = _conditional_issues()
    # 无条件必需（当前登记表里为空 —— 开箱即用优先；这里保留机制）
    for name, v in SPEC.items():
        if v.required and not get(name):
            issues.append({"name": name, "why": f"{v.summary}（必需）",
                           "fix": f"设 {name}=<值>，例如 {v.example or '<示例>'}", "level": "error"})
    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    return {"ok": not errors, "errors": errors, "warnings": warns,
            "checked": len(SPEC), "registered": len(SPEC)}


def assert_ready(*, strict: Optional[bool] = None) -> dict:
    """启动路径调用：不满足就 **抛 `EnvError`**（消息里带修复清单）。

    `strict=None` ⇒ 跟着 `AC_ENV=production` 走：生产**必须**过，开发只提示。
    """
    r = check_requirements()
    strict = is_production() if strict is None else strict
    if r["errors"] and strict:
        lines = ["环境变量不满足启动条件："]
        for e in r["errors"]:
            lines.append(f"  ✗ {e['name']}：{e['why']}\n      → {e['fix']}")
        lines.append("（提示：先 `cp .env.example .env` 再按需改；开发环境不设 AC_ENV=production 只告警）")
        raise EnvError("\n".join(lines))
    return r


def table_markdown(*, kinds: tuple = ("core", "feature", "dev")) -> str:
    """给 README 用的**环境变量表**（由本表生成，别手抄）。"""
    label = {"core": "部署核心", "feature": "功能开关", "dev": "开发/测试内部"}
    out = ["| 变量 | 说明 | 默认值 | 必需 |",
           "|---|---|---|---|"]
    for k in kinds:
        out.append(f"| **— {label.get(k, k)} —** | | | |")
        for name, v in SPEC.items():
            if v.kind != k:
                continue
            need = "必需" if v.required else (f"条件必需（{v.required_when}）" if v.required_when else "否")
            dflt = v.default or "（空）"
            out.append(f"| `{name}` | {v.summary} | `{dflt}` | {need} |")
    return "\n".join(out)


def dotenv_example() -> str:
    """给 `.env.example` 用的文本（同一份登记表生成）。

    ★约定（踩过才写下来）：**值一律取"默认值"，示例只写在注释里**。
      第一版我填的是 `example`，于是 `.env.example` 里出现 `AC_ENV=production` /
      `AC_HOST=127.0.0.1` —— 用户"照抄样例"就**改变了行为**（莫名其妙进了严格模式、
      或从"容器可达"变成"只有本机"）。样例文件的语义应当是**可安全照抄的默认**。
    """
    lines = [
        "# 外端Agent生产合作社 · 环境变量样例（由 agent_community/platform/env_spec.py 生成，勿手改）",
        "# 用法：cp .env.example .env   然后按需修改；**密钥类不要提交进仓库**。",
        "# 说明：下面给的都是**默认值**，照抄不会改变行为；想改哪项就改哪项。",
        "#       全部都有默认值 —— 一个都不设也能跑（不配 AI 时走规则兜底）。",
        "",
    ]
    label = {"core": "部署核心", "feature": "功能开关", "dev": "开发/测试内部（一般不用管）"}
    for k in ("core", "feature", "dev"):
        lines.append(f"# ── {label[k]} ────────────────────────────────────────")
        for name, v in SPEC.items():
            if v.kind != k:
                continue
            if v.required_when:
                lines.append(f"# 条件必需：{v.required_when}")
            lines.append(f"# {v.summary}")
            if v.example and v.example != v.default:
                lines.append(f"# 例如：{v.example}")
            safe_empty = name in ("AC_AI_API_KEY", "AC4_TOKEN", "AC4_COMPANY_TOKEN",
                                  "AC4_SHUTDOWN_PIN", "AC_AGENT_TOKEN", "DEEPSEEK_API_KEY")
            val = "" if safe_empty else ("" if v.default.startswith("<") else v.default)
            lines.append(f"{name}={val}")
            lines.append("")
    return "\n".join(lines)
