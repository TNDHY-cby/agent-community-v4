"""platform/core/security.py 模块级单元测试。

覆盖：
- _is_dangerous 危险命令黑名单拦截（format/net stop/rm -rf/rd /s /q/reg delete 等命中，
  echo 等安全命令不命中）
- Windows DPAPI 密钥加解密往返（_encrypt_secret / _decrypt_secret）
- mask_api_key 脱敏
- atomic_write_text 原子写盘
"""
import pytest

from agent_community.platform.core.security import (
    _is_dangerous,
    _encrypt_secret,
    _decrypt_secret,
    mask_api_key,
    atomic_write_text,
)


# ── _is_dangerous：危险命令必须命中 ─────────────────────────────
@pytest.mark.parametrize(
    "cmd",
    [
        "format c:",
        "format d: /q /y",
        "net stop wuauserv",
        "net stop Spooler",
        "rm -rf /tmp/x",
        "rm --recursive --force /some/dir",
        "rd /s /q C:\\foo",
        "rd /S /Q D:\\bar",
        "reg delete HKLM\\Software\\X /f",
        "reg add HKLM\\Software\\X /v y /t REG_SZ /d 1",
        "diskpart /s script.txt",
        "erase foo.txt",
        "del /f C:\\x",
        "del /q D:\\y",
        "shutdown /s /t 0",
        "Remove-Item -Recurse -Force C:\\x",
        "stop-service spooler",
        "C:\\Windows\\system32\\cmd.exe",
        "C:\\Program Files\\SomeApp\\app.exe",
    ],
)
def test_is_dangerous_blocks_known_dangerous(cmd):
    hit = _is_dangerous(cmd)
    assert hit is not None, f"危险命令未被拦截: {cmd!r}"


# ── _is_dangerous：安全命令必须放行 ─────────────────────────────
@pytest.mark.parametrize(
    "cmd",
    [
        "echo hello",
        "dir C:\\",
        "ping 127.0.0.1",
        "python script.py",
        "git status",
        "tasklist",
        "type README.md",
        "ipconfig",
        "",
        "   ",
    ],
)
def test_is_dangerous_allows_safe(cmd):
    assert _is_dangerous(cmd) is None, f"安全命令被误拦截: {cmd!r}"


# ── DPAPI 加解密往返 ────────────────────────────────────────────
def test_dpapi_roundtrip():
    secret = "sk-ant-0123456789abcdef-中文密钥"
    enc = _encrypt_secret(secret)
    assert enc.startswith("dpapi:")
    assert enc != secret
    assert _decrypt_secret(enc) == secret


def test_encrypt_empty_returns_empty():
    assert _encrypt_secret("") == ""
    assert _decrypt_secret("") == ""


def test_decrypt_plain_legacy_passthrough():
    # 历史明文（无 dpapi: 前缀）原样返回，保证兼容
    assert _decrypt_secret("plain-old-key") == "plain-old-key"


def test_decrypt_corrupt_dpapi_returns_empty():
    # 无法 base64 解码的 dpapi: 密文 -> 解密失败返回空串，不抛异常
    assert _decrypt_secret("dpapi:!!!not-base64!!!") == ""


# ── mask_api_key 脱敏 ───────────────────────────────────────────
@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (None, ""),
        ("", ""),
        ("12345678", "12345678"),          # len<=8 原样
        ("123456789", "1234*6789"),        # 前4后4
        ("sk-abcdefghijklmnop", "sk-a***********mnop"),
    ],
)
def test_mask_api_key(key, expected):
    assert mask_api_key(key) == expected


# ── atomic_write_text 原子写盘 ──────────────────────────────────
def test_atomic_write_text_create(tmp_path):
    target = tmp_path / "cfg.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    # 不应残留 .tmp 临时文件
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_text_overwrite(tmp_path):
    target = tmp_path / "cfg.json"
    atomic_write_text(target, "old")
    atomic_write_text(target, "new-content")
    assert target.read_text(encoding="utf-8") == "new-content"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_text_unicode(tmp_path):
    target = tmp_path / "cfg.json"
    atomic_write_text(target, '{"name": "外端Agent生产合作社"}')
    assert target.read_text(encoding="utf-8") == '{"name": "外端Agent生产合作社"}'
    assert not list(tmp_path.glob("*.tmp"))
