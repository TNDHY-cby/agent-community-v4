"""platform/core/security：安全与密钥相关共享工具（V-9 从 server/config/shell_tool 下沉）。

统一收纳：
- Windows DPAPI 密钥加解密（CryptProtectData / CryptUnprotectData，ctypes 免 pywin32）
- API Key 脱敏显示
- 原子写盘（临时文件 + os.replace）
- 危险命令检测（黑名单，shell 工具与插件 invoke 共用）
"""
from __future__ import annotations

import base64
import os
import re
import sys
from pathlib import Path
from typing import Any

# ── 危险命令模式（黑名单） ──────────────────────────────────
_DANGEROUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\b", re.IGNORECASE),               # rm 任意用法（含 --recursive --force 长参数变体）
    re.compile(r"\brmdir\b", re.IGNORECASE),
    re.compile(r"\brd\s+/[sq]", re.IGNORECASE),         # rd /s /q 递归删除目录
    re.compile(r"\berase\b", re.IGNORECASE),
    re.compile(r"\bdel\s+/[fsq]\b", re.IGNORECASE),
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\breg\s+delete\b", re.IGNORECASE),
    re.compile(r"\breg\s+add\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\s+-Recurse\s+-Force\b", re.IGNORECASE),
    re.compile(r"\bnet\s+stop\b", re.IGNORECASE),
    re.compile(r"\bstop-service\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\brestart-computer\b", re.IGNORECASE),
    re.compile(r"\bstop-computer\b", re.IGNORECASE),
    re.compile(r">\\\\.\\[A-Z]:", re.IGNORECASE),  # 直接写磁盘设备
    re.compile(r":\\Windows\\", re.IGNORECASE),      # 操作系统目录
    re.compile(r":\\Program Files", re.IGNORECASE),  # 程序目录
]


def _is_dangerous(command: str) -> str | None:
    """检查命令是否包含危险操作。返回命中的模式描述或 None。"""
    for pattern in _DANGEROUS_PATTERNS:
        m = pattern.search(command)
        if m:
            return f"检测到危险命令模式: {m.group().strip()}"
    return None


# ── 密钥落盘保护（DPAPI）──
_SECRET_PREFIX = "dpapi:"


def _is_windows() -> bool:
    return sys.platform == "win32" or sys.platform.startswith("cygwin")


def _dpapi(data: bytes, protect: bool) -> bytes:
    """调用 Windows CryptProtectData / CryptUnprotectData（ctypes，免装 pywin32）。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _to_blob(buf: bytes) -> DATA_BLOB:
        arr = (ctypes.c_byte * len(buf)).from_buffer_copy(buf)
        return DATA_BLOB(len(buf), ctypes.cast(arr, ctypes.POINTER(ctypes.c_byte)))

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob = _to_blob(data)
    out_blob = DATA_BLOB()
    ok = False
    if protect:
        ok = bool(crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)))
    else:
        ok = bool(crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)))
    if not ok:
        raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _encrypt_secret(plain: str) -> str:
    """加密明文：Windows 用 DPAPI（CryptProtectData），非 Windows 降级返回原样（由外层告警）。"""
    if not plain:
        return plain
    if _is_windows():
        try:
            blob = _dpapi(plain.encode("utf-8"), protect=True)
            return _SECRET_PREFIX + base64.b64encode(blob).decode("ascii")
        except Exception as e:  # DPAPI 失败（无权限/异常环境）降级明文
            print(f"[config] 警告: DPAPI 加密失败，密钥将明文保存: {e}", flush=True)
            return plain
    return plain


def _decrypt_secret(stored: str) -> str:
    """解密存储值：dpapi: 前缀走 DPAPI，否则视为历史明文原样返回。"""
    if not stored:
        return stored
    if stored.startswith(_SECRET_PREFIX):
        try:
            if _is_windows():
                raw = _dpapi(base64.b64decode(stored[len(_SECRET_PREFIX):]), protect=False)
                return raw.decode("utf-8")
        except Exception as e:
            print(f"[config] 警告: DPAPI 解密失败，密钥不可用: {e}", flush=True)
            return ""
        # 非 Windows 遇到 dpapi: 密文（配置从 Windows 迁移而来）无法解密
        print("[config] 警告: 检测到 DPAPI 密文但当前非 Windows，密钥不可用，请改用环境变量 AC_AI_API_KEY", flush=True)
        return ""
    return stored


def mask_api_key(key: str) -> str:
    """脱敏显示 API Key，只展示前4后4位。"""
    if not key or len(key) <= 8:
        return key or ""
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


# ── 原子写盘 ──────────────────────────────────────────────
def atomic_write_text(path: Path, text: str) -> None:
    """临时文件 + os.replace 原子替换写盘，避免写盘中断留下损坏的 JSON。"""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
