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
import json
import secrets
from typing import Optional

from ..config import load_config, save_config

# 消息头名称（统一约定）
AGENT_TOKEN_HEADER = "x-agent-token"
# 公司级 token 的 HMAC 消息前缀：HMAC(secret, "company:" + company_id)，
# 与成员级 HMAC(secret, harness_id) 同族同库、互不冲突（候选 A 升公司级，M-1）。
COMPANY_TOKEN_PREFIX = "company:"


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


# ── M-1 公司级身份（候选 A 升公司级：成员→公司实体，公司间 B2B 对等协作）──
def issue_company_token(company_id: str) -> str:
    """为公司实体签发公司级 token（HMAC-SHA256(secret, "company:" + company_id)）。

    与成员级 agent_token 同族同库：不造新轮、单一签发源（平台侧）、边缘只校验不签发。
    公司间对等协作时，公司 B 持此 token 经 Tailscale 访问公司 A 的 9104。
    """
    secret = _ensure_secret()
    msg = COMPANY_TOKEN_PREFIX + company_id
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_company_token(company_id: str, token: str) -> bool:
    """校验公司级 token 是否与 company_id 匹配（常数时间比较）。"""
    if not token:
        return False
    expected = issue_company_token(company_id)
    return hmac.compare_digest(expected, token)


def known_company_ids() -> list[str]:
    """读公司登记册 `companies.json`（M-1 落盘登记），返回已登记 company_id 列表。

    与 `known_harness_ids` 同构：读不到/损坏时返回空列表（fail-closed，一律拒绝）。
    登记文件位置：data_dir/companies.json（缺省 data/companies.json），
    结构与 harnesses.json 一致：`{"company_id": {...}}` 或 `[{"company_id": ...}, ...]`。
    """
    from pathlib import Path

    try:
        cfg = load_config()
        data_dir = (cfg.get("data_dir") or "").strip()
        base = Path(data_dir) if data_dir else Path(__file__).resolve().parents[1] / "data"
        raw = json.loads((base / "companies.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  读不到就当"没有任何已登记公司" → 一律拒绝
        return []
    if isinstance(raw, dict):
        # 兼容两种登记结构：{"company_id": {...}}（与 harnesses.json 一致）
        # 或 {"companies": [...]}（数组包装）。dict 直存优先取键。
        items = list(raw.values()) if "companies" not in raw else raw["companies"]
        if not isinstance(items, list):
            items = list(raw.keys())
    else:
        items = raw if isinstance(raw, list) else []
    ids = []
    for c in items:
        if isinstance(c, dict):
            cid = c.get("company_id") or c.get("id")
            if cid:
                ids.append(str(cid))
    return ids


# ── V18/候选C 边缘鉴权（38 号实施任务书：9104 与 9105 **同一决策函数、两个适配**）──
def auth_secret_ready() -> bool:
    """只读判定"鉴权配置可用"：config 里 `agent_token_secret` 非空。

    ⚠️ **不调用 `_ensure_secret()`** —— 那会在为空时生成并**写盘**，
    启动守卫（C4 的 fail-closed）必须是纯读，不能为了检查而偷偷改配置。
    """
    try:
        return bool((load_config().get("agent_token_secret") or "").strip())
    except Exception:  # noqa: BLE001  读配置失败按"不可用"处理（fail-closed）
        return False


def known_harness_ids() -> list[str]:
    """读平台持久化里已登记的 harness_id（边缘鉴权用来界定 token 的归属范围）。

    `known_ids` 可由调用方/测试注入；默认读 `data_dir`（缺省为包内 `data/`）。
    """
    from pathlib import Path

    try:
        cfg = load_config()
        data_dir = (cfg.get("data_dir") or "").strip()
        base = Path(data_dir) if data_dir else Path(__file__).resolve().parents[1] / "data"
        raw = json.loads((base / "harnesses.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  读不到就当"没有任何已知 harness" → 一律拒绝
        return []
    items = raw if isinstance(raw, list) else list(raw.values())
    ids = []
    for h in items:
        if isinstance(h, dict):
            hid = h.get("harness_id") or h.get("id")
            if hid:
                ids.append(str(hid))
    return ids


def check_edge_bearer(token: str, known_ids: list[str] | None = None) -> Optional[tuple[int, str]]:
    """边缘鉴权**统一决策**（候选C §3.1 / 稿B §7 的"同一函数两个适配"）。

    返回 `None` = 放行；`(401, msg)` = 缺失凭据；`(403, msg)` = 校验失败 ——
    **与 P1 的 401/403 语义完全一致**（各传输层自行映射：
    HTTP 直接用 401/403；gRPC 把 401→`UNAUTHENTICATED`、403→`PERMISSION_DENIED`）。

    规则（**fail-closed**）：
    - token 为空 → 401；
    - token 必须等于**某个已登记 harness** 的 `HMAC(secret, harness_id)`，
      或 **某个已登记公司** 的 `HMAC(secret, "company:" + company_id)`（M-1 升公司级）；
    - **两个维度都查完**后才判定：harness 与公司登记册**均为空**（无持久化/读取失败）→ 403
      （**不因"没数据"而放行**；任一维度有登记数据但不匹配 → 403）；
    - 签发只在平台侧，边缘只校验不签发（与 V18 R3 同理）。
    """
    if not token:
        return (401, "缺少 Authorization: Bearer <agent_token>")
    ids = list(known_ids) if known_ids is not None else known_harness_ids()
    for hid in ids:
        if verify_agent_token(hid, token):
            return None
    # M-1：公司级身份兜底 —— 成员 token 不中时再查公司登记册（公司间对等协作入口）
    company_ids = known_company_ids()
    for cid in company_ids:
        if verify_company_token(cid, token):
            return None
    if not ids and not company_ids:
        return (403, "无可校验的已登记 harness/公司（拒绝一切 token）")
    return (403, "Agent-Token 校验失败")


# ── 公司登记册读写（companies.json，供注册页「远程接入」弹窗与 issue_token.py 共用）──
def load_company_registry() -> dict:
    """读公司登记册 companies.json，归一化为 `{"company_id": rec}` 字典。

    与 `known_company_ids` 同构：兼容 dict / {"companies": [...]} / list 三种形态，
    读不到或损坏返回空 dict（fail-closed，调用方按"无登记"处理）。
    """
    from pathlib import Path

    try:
        cfg = load_config()
        data_dir = (cfg.get("data_dir") or "").strip()
        base = Path(data_dir) if data_dir else Path(__file__).resolve().parents[1] / "data"
        raw = json.loads((base / "companies.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    items: list = []
    if isinstance(raw, dict):
        items = raw["companies"] if "companies" in raw and isinstance(raw["companies"], list) else list(raw.values())
    elif isinstance(raw, list):
        items = raw
    out: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("company_id") or item.get("id") or "").strip()
        if not cid:
            continue
        rec = {k: v for k, v in item.items() if k not in ("company_id", "id")}
        rec["company_id"] = cid
        out[cid] = rec
    return out


def save_company_registry(records: dict) -> None:
    """将公司登记册写回 companies.json（统一 dict 形态 {"company_id": rec}）。"""
    from pathlib import Path

    cfg = load_config()
    data_dir = (cfg.get("data_dir") or "").strip()
    base = Path(data_dir) if data_dir else Path(__file__).resolve().parents[1] / "data"
    try:
        base.mkdir(parents=True, exist_ok=True)
        (base / "companies.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001  写盘失败不阻断：进程内仍可用
        pass
