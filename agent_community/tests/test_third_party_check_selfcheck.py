# -*- coding: utf-8 -*-
"""守卫：第三方许可闸门**不许假绿**（外部审查 EXT-20261025-03 打回的三处）。

| # | 判据 |
|---|---|
| ① | **工具不扫自己**：`scan()` 结果里不许出现本工具（它文档串里写着 SPDX 关键字） |
| ② | **目录名带 `temp`/`data` 也要扫**：否则仓库放在这种目录下，整闸静默失效（实测假绿） |
| ③ | **登记判定按路径精确相等**：文档里"随口提到路径"不算登记；登记表中的路径才算 |
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))


def _tp():
    """按文件路径加载工具模块。

    ⚠️ 不能用 `from tools import …` —— 那个写法会被 `test_foundation_base` 的
    "第三方 import 必须登记在 requirements.txt" 守卫当成外部依赖（`tools` 不是包）。
    """
    import importlib.util

    path = _ROOT / "tools" / "third_party_check.py"
    spec = importlib.util.spec_from_file_location("_tp_check_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tool_does_not_scan_itself():
    tp = _tp()
    hits = tp.scan()
    assert "tools/third_party_check.py" not in hits, "工具把自己算成未登记的第三方代码：%s" % hits


def test_temp_and_data_dirs_are_scanned_by_default():
    tp = _tp()
    # 默认不该再跳过 temp / data（那正是"仓库放在带 temp 的目录下就整闸失效"的根因）
    for name in ("temp", "data"):
        p = tp.ROOT / name / "x.py"
        assert tp._skip(p) is False, "%s/ 下被跳过了（假绿复发）：%s" % (name, p)
    # 真正的构建/依赖目录仍要跳过
    assert tp._skip(tp.ROOT / ".git" / "x.py") is True
    assert tp._skip(tp.ROOT / "node_modules" / "x.py") is True


def test_registration_requires_exact_table_path(tmp_path, monkeypatch):
    tp = _tp()
    notice = tmp_path / "THIRD_PARTY_NOTICES.md"
    # 只在正文里"提到"路径（子串）——按新口径**不算登记**
    notice.write_text("# 说明\n\n用法： python tools/third_party_check.py\n", encoding="utf-8")
    monkeypatch.setattr(tp, "NOTICE", notice)
    assert tp.registered_paths() == set(), "正文里提一句就被当成登记了（子串匹配复发）"

    # 登记表里写了路径 —— 才算
    notice.write_text("| 项目 | 仓库 | 许可证 | 被借鉴的文件 | 改动说明 | 日期 |\n"
                      "|---|---|---|---|---|---|\n"
                      "| Demo | x/y | MIT | `vendor/demo.py` | 原样引入 | 2026-10-25 |\n",
                      encoding="utf-8")
    reg = tp.registered_paths()
    assert "vendor/demo.py" in reg, reg
    assert "tools/third_party_check.py" not in reg, reg
