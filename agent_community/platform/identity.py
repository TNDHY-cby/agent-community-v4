"""P1 身份凭证：Agent-Token（HMAC 签发 / 校验 / 查询）

对标 AGNTCY 身份层简化版：注册时平台为每个 Harness 签发不可逆的
agent-token（HMAC-SHA256(secret, harness_id)），harness 后续消息头
携带 `X-Agent-Token`，平台侧可校验来源身份、审计可溯源。

设计约束：
- 最小侵入：token 存入 HarnessInfo.metadata["agent_token"]，随既有
  harnesses.json 持久化，不新增存储文件；
- 向后兼容：已注册但未持有 token 的旧 harness 不受影响（校验为可选项，
  仅在目标 harness 已签发 token 时强制要求携带）；
- 密钥来源：config.agent_token_secret，首次启动时自动生成并持久化。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from ..config import load_config, save_config

# 消息头名称（统一约定）
AGENT_TOKEN_HEADER = "x-agent-token"


def _ensure_secret() -> str:
    """读取 agent_token_secret，不存在则生成随机密钥并持久化。"""
    cfg = load_config()
    secret = (cfg.get("agent_token_secret") or "").strip()
    if not secret:
        secret = secrets.token_hex(32)
        cfg["agent_token_secret"] = secret
        try:
            save_config(cfg)
        except Exception:
            pass  # 写盘失败不阻断：进程内仍可用，下次启动再补
    return secret


def issue_agent_token(harness_id: str) -> str:
    """为指定 harness 签发 agent-token（HMAC-SHA256，hex）。"""
    secret = _ensure_secret()
    return hmac.new(secret.encode("utf-8"), harness_id.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_agent_token(harness_id: str, token: str) -> bool:
    """校验 token 是否与 harness_id 匹配（常数时间比较）。"""
    if not token:
        return False
    expected = issue_agent_token(harness_id)
    return hmac.compare_digest(expected, token)


def extract_token_from_headers(headers) -> str:
    """从请求头提取 X-Agent-Token（支持多种大小写，FastAPI Header 直取）。"""
    return (headers.get(AGENT_TOKEN_HEADER) or "").strip()


def has_agent_token(info_metadata: dict) -> bool:
    """判断该 harness 是否已签发 token（向后兼容开关）。"""
    return bool((info_metadata or {}).get("agent_token"))


def check_request_token(harness_id: str, info_metadata: dict, headers) -> Optional[tuple[int, str]]:
    """P1 准入校验统一入口（三个挂接点共用，避免逻辑三处漂移）。

    返回 None 表示放行；返回 (http_status, error_message) 表示拒绝。
    向后兼容：仅当目标 harness 已签发 token 时才强制要求携带，
    未签发 token 的存量旧 harness 一律放行。
    """
    if not harness_id or not has_agent_token(info_metadata or {}):
        return None
    provided = extract_token_from_headers(headers)
    if not provided:
        return (401, f"缺少身份凭证头 {AGENT_TOKEN_HEADER}（该 Harness 已启用 Agent-Token）")
    if not verify_agent_token(harness_id, provided):
        return (403, "Agent-Token 校验失败")
    return None
