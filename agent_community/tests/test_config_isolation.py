# -*- coding: utf-8 -*-
"""守卫：**每个实例一套配置**（V61 双实例/公司间合作的隔离前提，2026-10-20）。

判据（按真行为）：
  ① 显式设了 `AC_DATA_DIR` ⇒ 配置落在 `<AC_DATA_DIR>/config/config.json`
     （同一台机器跑第二个实例时，改它的模型**不会**改到第一个实例的）；
  ② 没设 `AC_DATA_DIR` ⇒ 仍然用用户级 `~/.agent_community/config.json`（老用户零变化）；
  ③ 两个不同 data 目录各自 `save_config()` 写进去的**是不同的文件**，互不覆盖。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _reload_config(monkeypatch, data_dir: str | None):
    """在指定 `AC_DATA_DIR` 下重新加载 config 模块（路径是模块级常量，必须 reload 才生效）。"""
    from agent_community import config as cfgmod
    if data_dir is None:
        monkeypatch.delenv("AC_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("AC_DATA_DIR", data_dir)
    return importlib.reload(cfgmod)


def test_config_follows_data_dir_when_set(tmp_path, monkeypatch):
    cfg = _reload_config(monkeypatch, str(tmp_path / "instance_b"))
    assert Path(cfg.CONFIG_FILE) == tmp_path / "instance_b" / "config" / "config.json", \
        "设了 AC_DATA_DIR 时配置文件必须跟着它走（否则两个实例共用一份配置）：%s" % cfg.CONFIG_FILE


def test_config_stays_user_level_when_unset(monkeypatch):
    cfg = _reload_config(monkeypatch, None)
    assert Path(cfg.CONFIG_FILE) == Path.home() / ".agent_community" / "config.json", \
        "没设 AC_DATA_DIR 时必须保持用户级（老用户零变化）：%s" % cfg.CONFIG_FILE


def test_two_instances_write_different_config_files(tmp_path, monkeypatch):
    from agent_community import config as cfgmod
    a = tmp_path / "inst_a"
    b = tmp_path / "inst_b"
    monkeypatch.setenv("AC_DATA_DIR", str(a))
    importlib.reload(cfgmod)
    cfgmod.save_config(dict(cfgmod.DEFAULT_CONFIG, ai_model="model-for-A"))
    monkeypatch.setenv("AC_DATA_DIR", str(b))
    importlib.reload(cfgmod)
    cfgmod.save_config(dict(cfgmod.DEFAULT_CONFIG, ai_model="model-for-B"))

    fa = a / "config" / "config.json"
    fb = b / "config" / "config.json"
    assert fa.is_file() and fb.is_file(), "两个实例必须各写自己的配置文件：%s / %s" % (fa, fb)
    import json
    assert json.loads(fa.read_text(encoding="utf-8"))["ai_model"] == "model-for-A"
    assert json.loads(fb.read_text(encoding="utf-8"))["ai_model"] == "model-for-B", \
        "B 的写入污染了 A（或反之）—— 隔离失效"
