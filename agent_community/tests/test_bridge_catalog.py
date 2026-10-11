# -*- coding: utf-8 -*-
"""`platform/bridge_catalog.py`：四种桥型的「该填什么」——接入这件事的**单一真相**。

契约（V38 + 规则 19/20）：
- 每型都要能回答「谁提供 / 怎么填 / 怎么自证 / 常见坑」；
- 用户要填的字段必须**给得出抄得动的示例**（禁 `[如：xxx]` 占位符）；
- **失败要说话**：校验失败给**逐字段**的中文原因 + 怎么改，不是一句"参数不完整"；
- **规则 19**：不得出现真实 harness 名或本机路径（换台机器照样能用）；
- **规则 20**：本模块是"该填什么"的单一真相，界面话术/文档/校验都引这里。
"""
from __future__ import annotations

import json

from agent_community.platform import bridge_catalog as bc


def test_four_kinds_available():
    kinds = [k["kind"] for k in bc.list_kinds()]
    assert kinds == ["file_poll", "cli_acp", "pending_poll", "script_framework"]


def test_every_kind_answers_the_four_questions():
    """每型必须有：label / 一句话 / 何时用 / 字段 / 自证 / 坑。"""
    for k in bc.list_kinds():
        assert k.get("label") and k.get("one_liner") and k.get("when")
        assert k.get("verify_text"), f"{k['kind']} 没说怎么自证"
        assert k.get("verify_kind") in bc.VERIFY_KINDS
        d = bc.describe(k["kind"])
        assert d["fields"], f"{k['kind']} 没有任何字段说明"
        assert d["pitfalls"], f"{k['kind']} 没写常见坑"


def test_every_field_has_fill_guide_and_concrete_example():
    """每个字段都要能回答「怎么填」+ 给**具体示例**（不许占位符）。"""
    for kind in bc.CATALOG:
        for f in bc.describe(kind)["fields"]:
            assert f.get("provides") in (bc.BY_PLATFORM, bc.BY_USER, bc.BY_BOTH), f
            assert f.get("label"), f"{kind}.{f.get('name')} 缺 label"
            assert f.get("fill"), f"{kind}.{f.get('name')} 缺 fill（怎么填）"
            ex = str(f.get("example") or "")
            assert ex, f"{kind}.{f.get('name')} 缺 example"
            assert "[如" not in ex and "xxx" not in ex.lower(), \
                f"{kind}.{f.get('name')} 的示例是占位符，用户抄不动：{ex}"


def test_validate_speaks_per_field_with_a_fix():
    """失败要说话：逐字段原因 + 怎么改（不是一句"参数不完整"）。"""
    r = bc.validate("file_poll", {})
    assert r["ok"] is False
    assert r["missing_user_fields"] == ["INBOX_DIR"]
    e = r["errors"][0]
    assert e["field"] == "INBOX_DIR"
    assert e["reason"] and e["fix"], "必须同时给「为什么」和「怎么改」"
    assert "D:" in e["fix"], "fix 里要给抄得动的具体格式"


def test_optional_user_fields_are_not_required():
    """标了「可选」的字段不能算缺失（TRIGGER_CMD 就是可选 —— 但不填不会被叫醒，
    这一点写在 pitfalls 里说明，而不是把它变成必填）。"""
    r = bc.validate("file_poll", {"INBOX_DIR": r"D:\x"})
    assert r["ok"] is True, r["errors"]
    r2 = bc.validate("script_framework", {})
    assert r2["ok"] is True, "三个动作脚本都是可选的（不填走人工模式）"


def test_platform_fields_never_required_from_user():
    """平台注入的字段（HARNESS_ID / PLATFORM_URL / MODEL_NAME…）不该要用户填。"""
    d = bc.describe("file_poll")
    for name in ("HARNESS_ID", "PLATFORM_URL"):
        assert name in d["platform_fields"]
        assert name not in d["user_fields"]
    r = bc.validate("file_poll", {"INBOX_DIR": r"D:\x"} if False else {"INBOX_DIR": "D:\\x"})
    assert r["ok"] is True


def test_unknown_kind_returns_fix_hint():
    r = bc.validate("不存在的型", {})
    assert r["ok"] is False
    assert r["errors"][0]["reason"].startswith("不支持的桥型")
    assert r["errors"][0]["fix"], "不支持的型也要给「怎么改」"


def test_rule19_no_real_harness_name_or_host_path():
    """★规则 19：这份真相里不得出现真实 harness 名或本机路径。"""
    blob = json.dumps(bc.CATALOG, ensure_ascii=False)
    for token in ("traecode", "TraeCode", "小白龙", "DSH工作区", "D:\\DSH", "Marvis"):
        assert token not in blob, f"出现了不该有的真实坐标/名字：{token}"


def test_truth_summary_points_to_itself():
    """规则 20：本模块自称单一真相，别处应引它。"""
    s = bc.truth_summary()
    assert s["kinds"] == 4
    assert "bridge_catalog" in s["source_of_truth"]
