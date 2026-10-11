# -*- coding: utf-8 -*-
"""`platform/role_catalog.py`：**职业（职位）表** —— 一处真相、可扩展。

起因（老板 2026-10-11）：「职业列表落后了」

核实：给 AI 的提示词里**写死**了「职业从 组长/码农/画师/搜索者 里选」⇒
选不出产品/测试/运维/数据这类角色；做非"写代码画图"的项目明显不够用。

本文件锁四件事：
1. 「组长」必须在（平台流程依赖它）；
2. 职业覆盖面比原来宽（含产品/测试/运维/数据/文案等），且**不是封闭枚举**；
3. **提示词允许按任务新造职业**（否则换汤不换药，还是"一张表管死"）；
4. 老叫法有对应（画师→美术），避免同一件事两个名字。
"""
from __future__ import annotations

from agent_community.platform import role_catalog as rc


def test_lead_role_required():
    assert rc.REQUIRED_ROLE == "组长"
    assert rc.REQUIRED_ROLE in rc.role_names()


def test_coverage_is_wider_than_the_old_four():
    """旧表只有 组长/码农/画师/搜索者；新表必须明显更宽，且含常见职能。"""
    names = rc.role_names()
    old = {"组长", "码农", "画师", "搜索者"}
    assert len(names) > len(old), f"职业数没有变多：{names}"
    for must in ("产品", "测试", "运维", "数据", "文案"):
        assert must in names, f"缺少常见职能「{must}」"


def test_roles_are_grouped_and_annotated():
    for r in rc.list_roles():
        assert r.get("role") and r.get("group") and r.get("note"), r
    g = rc.groups()
    assert "工程" in g and "组长" in g.get("管理", [])


def test_no_duplicate_roles():
    names = rc.role_names()
    assert len(names) == len(set(names)), "职业表里有重复项"


def test_prompt_rules_are_not_a_closed_list():
    """★关键：提示词**不能**再把职业封闭在固定几个里。"""
    rules = rc.prompt_rules()
    assert "组长" in rules, "必须强调组长"
    assert "按任务需要" in rules or "按任务" in rules
    assert "自行命名" in rules or "可以按任务" in rules, \
        "必须允许 AI 按任务新造职业（否则还是封闭列表）"
    # 不能再出现"只从那几个里选"这种封闭表述
    for bad in ("里选（按任务需要）", "只能从", "仅限"):
        assert bad not in rules, f"提示词里仍有封闭表述：{bad}"


def test_legacy_alias_mapped():
    assert rc.LEGACY_ALIASES.get("画师") == "美术"
    assert "画师→美术" in rc.prompt_rules(), "提示词里应写明旧叫法的对应"


def test_catalog_is_read_only_module():
    """本模块只提供推荐，不做写操作（角色是自由文本，不该被白名单拦住）。"""
    import inspect
    src = inspect.getsource(rc)
    for forbidden in ("save_state(", "write_text", "os.remove"):
        assert forbidden not in src
