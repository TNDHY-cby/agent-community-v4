"""platform/core/security.py 模块级单元测试。

覆盖：
- _is_dangerous 危险命令黑名单拦截（format/net stop/rm -rf/rd /s /q/reg delete 等命中，
  echo 等安全命令不命中）
- 密钥加解密往返（**两个平台都验**：Windows 走 DPAPI，非 Windows 明文回退；平台差异如实断言）
- 存密钥时的**消息必须与磁盘事实一致**（条 D：曾出现"磁盘明文、屏幕说已加密"）
- mask_api_key 脱敏
- atomic_write_text 原子写盘
"""
import sys

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


# ── 密钥落盘：两种平台都必须**往返正确**，且"说的话"必须与磁盘事实一致 ──────
# ★2026-10-15（可移植性缺陷**条 D**）：原来这一条**整条 skip 在非 Windows 上**
#   ⇒ Linux 上"密钥到底怎么存"根本没人守。而实际上 Linux 上存的是**明文**、
#   屏幕上却打印"已加密保存（DPAPI 当前用户绑定）"（真 Linux 实测复现，见 `PORTABILITY.md` §5.1 条 D）。
#   现在：往返在**两个平台都验**，平台差异**如实断言**，再加一条"消息 vs 磁盘"的硬判据。
def test_secret_storage_roundtrip_on_every_platform():
    secret = "sk-ant-0123456789abcdef-中文密钥"
    enc = _encrypt_secret(secret)
    assert _decrypt_secret(enc) == secret, "加解密往返必须成立（两个平台都是硬要求）"
    if sys.platform == "win32":
        assert enc.startswith("dpapi:"), "Windows 上必须真的走 DPAPI，不是明文"
        assert enc != secret
    else:
        assert enc == secret, (
            "非 Windows 是**明文回退**（既定设计，不是 bug）；但它必须被如实说出来 —— "
            "见 test_save_config_message_matches_disk")


def test_save_config_message_matches_disk(capsys, tmp_path, monkeypatch):
    """★条 D 的核心判据：**存密钥时说的话，必须与磁盘上的字节一致**。

    实测过的错误形态（真 Linux）：磁盘是明文 `sk-repro-…`，屏幕却说
    「AI API Key 已加密保存（DPAPI 当前用户绑定）」—— **对安全属性撒谎**。
    这条判据**与平台无关**：加密了就必须说加密，明文就必须说明文（并给出路）。
    """
    from agent_community import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    config_mod.save_config({**config_mod.DEFAULT_CONFIG,
                            "ai_api_key": "sk-repro-0123456789abcdef"})
    out = capsys.readouterr().out
    raw = (tmp_path / "config.json").read_text(encoding="utf-8")
    encrypted_on_disk = '"ai_api_key": "dpapi:' in raw
    if encrypted_on_disk:
        assert "已用 DPAPI 加密保存" in out, out
        assert "明文" not in out, "磁盘是密文却说成明文：%r" % out
    else:
        assert "明文" in out, "密钥以明文落盘却没说 —— 这就是对用户撒谎：%r" % out
        assert "已用 DPAPI 加密保存" not in out, out
        assert "AC_AI_API_KEY" in out, "明文时必须告诉用户该改用什么：%r" % out


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
