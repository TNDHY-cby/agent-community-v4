# -*- coding: utf-8 -*-
"""守卫：**公司间合作的两个工具**（V61 P1/P2）。

判据（按真行为）：
  ① `tools/gen_frpc.py` 缺必填项 ⇒ **退出码 1** 且逐条说清缺什么（不生成半成品配置）；
  ② 齐了 ⇒ 生成的 TOML 字段正确（地址/端口/本地端口/远端端口/代理名都对得上）；
  ③ **屏幕输出里不许出现完整 token**（明文只写进配置文件）；
  ④ 自定义 `--out` 写文件时内容含 token 明文（那是它该干的），但**文件落在指定位置**。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

# ⚠️ 用工具里登记过的**虚拟名**（`your-frp-token`），不要写"看起来像真密钥"的长串 ——
#    发布前的敏感扫描会把它判成真凭据并**拦住发布**（实测踩过一次）。
TOKEN = "your-frp-token"


def _load_tool():
    spec = importlib.util.spec_from_file_location("gen_frpc_mod", _ROOT / "tools" / "gen_frpc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _full_env(monkeypatch):
    monkeypatch.setenv("AC_FRP_SERVER_ADDR", "frp.example.com")
    monkeypatch.setenv("AC_FRP_SERVER_PORT", "7000")
    monkeypatch.setenv("AC_FRP_TOKEN", TOKEN)
    monkeypatch.setenv("AC_FRP_REMOTE_PORT", "19030")
    monkeypatch.setenv("AC_PORT", "18930")


def test_missing_items_exit_1_and_say_what(monkeypatch, capsys):
    monkeypatch.delenv("AC_FRP_SERVER_ADDR", raising=False)
    monkeypatch.delenv("AC_FRP_TOKEN", raising=False)
    monkeypatch.delenv("AC_FRP_REMOTE_PORT", raising=False)
    mod = _load_tool()
    rc = mod.main(["--check"])
    out = capsys.readouterr().out
    assert rc == 1, "缺必填项必须退出码 1（不能假装成功）"
    for need in ("AC_FRP_SERVER_ADDR", "AC_FRP_TOKEN", "AC_FRP_REMOTE_PORT"):
        assert need in out, "要说清缺哪一项：%s\n%s" % (need, out)


def test_render_has_all_fields(monkeypatch):
    _full_env(monkeypatch)
    mod = _load_tool()
    v, missing = mod.collect()
    assert not missing, missing
    text = mod.render(v)
    assert 'serverAddr = "frp.example.com"' in text
    assert "serverPort = 7000" in text
    assert "localPort = 18930" in text, "本地端口必须是被映射的那个实例端口"
    assert "remotePort = 19030" in text
    assert 'name = "ac-company-b"' in text


def test_token_never_printed_in_clear(monkeypatch, capsys):
    _full_env(monkeypatch)
    mod = _load_tool()
    rc = mod.main(["--check", "--print"])
    out = capsys.readouterr().out
    assert rc == 0
    assert TOKEN not in out, "屏幕上不许出现完整 token（只写进配置文件）"
    assert "frp.example.com" in out


def test_write_lands_where_asked(monkeypatch, tmp_path):
    _full_env(monkeypatch)
    mod = _load_tool()
    target = tmp_path / "out" / "frpc.toml"
    rc = mod.main(["--write", "--out", str(target)])
    assert rc == 0
    assert target.is_file(), "没写到我指定的位置：%s" % target
    text = target.read_text(encoding="utf-8")
    assert TOKEN in text, "配置文件里必须有真 token（否则 frpc 连不上）"
    assert "localPort = 18930" in text
