"""外端Agent生产合作社（外端Agent生产合作社（Agent Community））v4 安全回归测试（黑盒 + 静态断言混合）

黑盒用例直连运行中的服务（默认 http://127.0.0.1:18920），
只发会被安全校验拒绝的恶意请求，不产生任何注册/状态副作用；
静态用例读取源码断言关键防线存在，防止后续改动回退。

运行:
    python -m pytest tests/security_regression.py -v
    python -m pytest tests/security_regression.py -v --run-fault-injection   # 含故障注入验真组

依赖: pytest, httpx
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("AC_REGRESSION_BASE", "http://127.0.0.1:18920")
REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = REPO_ROOT / "agent_community" / "platform" / "server.py"
CONFIG_PY = REPO_ROOT / "agent_community" / "config.py"

TIMEOUT = 10.0


def _get(path: str, **kw):
    return httpx.get(BASE_URL + path, timeout=TIMEOUT, **kw)


def _post(path: str, payload: dict, **kw):
    return httpx.post(BASE_URL + path, json=payload, timeout=TIMEOUT, **kw)


# ── 服务可达性 ─────────────────────────────────────────────
def test_health():
    r = _get("/api/status")
    assert r.status_code == 200, f"服务不可达: {r.status_code} {r.text[:200]}"
    data = r.json()
    assert data.get("status") == "running"


# ── A 类 SSRF（register 三入口校验）────────────────────────
def _reg_body(**overrides):
    body = {
        "harness_id": "reg-fi-ssrf-x",
        "harness_name": "回归测试伪造",
        "callback_url": "",
        "wakeup_url": "",
        "api_base_url": "",
        "acp_command": "",
        "acp_cwd": "",
        "wakeup_dir": "",
        "api_message_path": "",
        "wakeup_method": "clipboard",
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize("malicious", [
    "http://169.254.169.254/latest/meta-data",   # 云元数据
    "http://192.168.1.1/",                        # 私网
    "http://10.0.0.1/",                           # 私网
    "http://172.16.0.1/",                         # 私网
])
def test_ssrf_register_metadata_private_blocked(malicious):
    r = _post("/api/harness/register", _reg_body(api_base_url=malicious))
    assert r.status_code == 400, f"恶意 api_base_url 未被拒绝: {malicious} -> {r.status_code}"


def test_ssrf_register_ftp_blocked():
    r = _post("/api/harness/register", _reg_body(api_base_url="ftp://evil.com/x"))
    assert r.status_code == 400


# ── B 类 RCE（V-4 acp_command 校验）────────────────────────
@pytest.mark.parametrize("cmd", [
    "cmd /c dir",
    "powershell -c whoami",
    "python -c \"import os; os.system('id')\"",
    "bash -c 'rm -rf /'",
    "node -e 'process.exit()'",
    "sh -c 'echo x > /tmp/f'",
    "echo a | cmd",
    "whoami && dir",
])
def test_rce_register_interpreter_blocked(cmd):
    r = _post("/api/harness/register", _reg_body(acp_command=cmd))
    assert r.status_code == 400, f"恶意 acp_command 未被拒绝: {cmd!r} -> {r.status_code}"


# ── H 类 归属校验（V-16/V-17）──────────────────────────────
def test_wakeup_response_forged_403():
    r = _post("/api/wakeup/response", {
        "harness_id": "reg-fi-fake-wake",
        "harness_name": "伪造",
        "task_id": "task-fi-none",
        "hand_raised": True,
        "capability_claim": "x",
    })
    assert r.status_code == 403, f"伪造唤醒举手未被拒绝: {r.status_code}"


def test_bridge_test_result_forged_403():
    lst = _get("/api/harness/list").json().get("harnesses") or []
    if not lst:
        pytest.skip("无已注册 harness，跳过桥测试伪造用例")
    hid = lst[0].get("harness_id", "")
    r = _post("/api/harness/bridge-test-result", {
        "harness_id": hid,
        "test_id": "forged-test-000",
        "ok": True,
    })
    assert r.status_code == 403, f"伪造桥测试回报未被拒绝: {r.status_code}"


# ── D 类 信息泄露（V-7 pending 脱敏）───────────────────────
def test_ai_pending_redacted():
    r = _get("/api/ai/pending")
    assert r.status_code == 200
    data = r.json()
    for item in data.get("pending", []):
        assert "prompt" not in item, "pending 列表泄露 prompt 全文"
        assert "system_prompt" not in item, "pending 列表泄露 system_prompt"
        assert "reply" not in item, "pending 列表泄露 reply"
        assert "extra" not in item, "pending 列表泄露 extra 上下文"
        if "prompt_excerpt" in item:
            assert len(item["prompt_excerpt"]) <= 300
        if "prompt_digest" in item:
            assert len(item["prompt_digest"]) <= 120


# ── 静态防线断言（防回退）──────────────────────────────────
def test_sanitize_wired_static():
    src = SERVER_PY.read_text(encoding="utf-8")
    assert "_sanitize_harness_content" in src, "提示注入清洗函数缺失"
    # 三个入口必须接入清洗：task-result 委托链、task-result 讨论区、harness message
    assert src.count("_sanitize_harness_content(") >= 4, "清洗函数未覆盖全部外部内容入口"


def test_limits_static():
    src = SERVER_PY.read_text(encoding="utf-8")
    assert "MAX_TASKS = 2000" in src or "MAX_TASKS=2000" in src, "任务上限缺失"
    assert "MAX_WORKSHOPS = 200" in src or "MAX_WORKSHOPS=200" in src, "工作间上限缺失"


def test_key_env_warning_static():
    src = CONFIG_PY.read_text(encoding="utf-8")
    assert "AC_AI_API_KEY" in src, "config.py 缺少环境变量注入提示"


def test_validate_acp_blacklist_static():
    src = SERVER_PY.read_text(encoding="utf-8")
    assert "validate_acp_command" in src
    assert "powershell" in src.lower() or "cmd" in src.lower()


# ── 故障注入验真组（默认跳过，--run-fault-injection 启用）──
# 故意取反的断言必须失败：证明测试不是"永远绿的摆设"


@pytest.mark.usefixtures("_fi_gate")
def test_fi_ssrf_metadata_ALLOWED(request):
    # 断言元数据地址被【放行】——校验逻辑正确时本用例必须失败
    if not request.config.getoption("--run-fault-injection"):
        pytest.skip("故障注入组需显式启用")
    r = _post("/api/harness/register", _reg_body(api_base_url="http://169.254.169.254/latest/meta-data"))
    assert r.status_code == 200, "故障注入未生效：恶意地址已被拒绝（这是正确的，本用例应红）"


@pytest.fixture
def _fi_gate():
    return True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
