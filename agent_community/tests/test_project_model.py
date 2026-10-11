# -*- coding: utf-8 -*-
"""V29 项目建模测试：`library/model.md` 五块骨架 / 写入前备份 / 只读状态 / 路由接入。

老板 2026-10-10「按你建议」拍板四条：
① 组长执笔（AI 可出草案） ② 改了留历史 ③ 普通成员能看、只有组长能改 ④ 与 plan.md 冲突听 plan。
本文件覆盖 ①②③ 的**已实现部分**（草案路径 B 与界面归 MiMo，另测）。
"""
from __future__ import annotations

import asyncio
import json
import pathlib

from agent_community.platform.workshop import (
    LIBRARY_DIRNAME,
    MODEL_PLACEHOLDER,
    MODEL_SECTIONS,
    Workshop,
    WorkshopMember,
    model_template,
    read_model_info,
    write_library,
    write_model,
)


def _ws(tmp_path, **kw):
    return Workshop(workshop_id="w1", name="看板项目",
                    workspace_dir=str(tmp_path / "ws"), hall_content="做看板",
                    created_at="now", members=[WorkshopMember(member_id="m0", role="码农",
                                                              display_name="小码", harness_ids=["h1"])],
                    **kw)


class TestTemplate:
    def test_template_has_five_sections_and_placeholder(self, tmp_path):
        tpl = model_template(_ws(tmp_path))
        assert tpl.count("## ") == len(MODEL_SECTIONS) == 5
        assert MODEL_PLACEHOLDER in tpl
        # 头部规矩：与 plan.md 的关系要写在文件里（V29 §4）
        assert "plan.md" in tpl and "组长" in tpl


class TestWriteModel:
    def test_first_write_is_v1_and_reports_missing(self, tmp_path):
        ws = _ws(tmp_path)
        out = write_model(ws, blocks={"goal": "做出看板"}, author="组长")
        assert out["ok"] and out["version"] == 1 and out["backed_up"] == ""
        assert len(out["missing"]) == 4          # 只给了 goal ⇒ 其余 4 块如实报"缺"
        txt = (pathlib.Path(ws.workspace_dir) / LIBRARY_DIRNAME / "model.md").read_text(encoding="utf-8")
        assert "做出看板" in txt and "执笔：组长" in txt

    def test_second_write_backs_up_and_bumps_version(self, tmp_path):
        """★老板裁定②「改了留历史」：每次保存前备份一份，可回看当初为什么这么分工。"""
        ws = _ws(tmp_path)
        write_model(ws, blocks={"goal": "第一版"}, author="组长")
        out2 = write_model(ws, blocks={"goal": "第二版"}, author="组长")
        assert out2["version"] == 2 and out2["backed_up"], "必须备份旧版"
        hist = pathlib.Path(ws.workspace_dir) / LIBRARY_DIRNAME / "model.history"
        files = list(hist.glob("*.md"))
        assert len(files) == 1
        assert "第一版" in files[0].read_text(encoding="utf-8"), "备份里应是旧内容"
        cur = (pathlib.Path(ws.workspace_dir) / LIBRARY_DIRNAME / "model.md").read_text(encoding="utf-8")
        assert "第二版" in cur

    def test_raw_text_write(self, tmp_path):
        ws = _ws(tmp_path)
        out = write_model(ws, text="# 我自己写的模型\n\n全都写完了", author="用户")
        assert out["ok"]
        txt = (pathlib.Path(ws.workspace_dir) / LIBRARY_DIRNAME / "model.md").read_text(encoding="utf-8")
        assert "我自己写的模型" in txt and "执笔：用户" in txt


class TestReadInfo:
    def test_not_exists(self, tmp_path):
        info = read_model_info(_ws(tmp_path))
        assert info["exists"] is False and info["version"] == 0

    def test_reads_header_and_missing(self, tmp_path):
        ws = _ws(tmp_path)
        write_model(ws, blocks={k: "x" for k, _t, _h in MODEL_SECTIONS}, author="组长")
        info = read_model_info(ws)
        assert info["exists"] and info["author"] == "组长" and info["version"] == 1
        assert info["missing"] == []             # 五块都填了 ⇒ 不缺
        assert info["history_count"] == 0 and info["preview"]

    def test_read_only_no_side_effect(self, tmp_path):
        """只读**不建目录**（界面刷新不该产生副作用）。"""
        ws = _ws(tmp_path)
        read_model_info(ws)
        assert not (pathlib.Path(ws.workspace_dir) / LIBRARY_DIRNAME).exists()


class TestWriteLibraryIntegration:
    def test_without_model_does_not_create_file(self, tmp_path):
        ws = _ws(tmp_path)
        out = write_library(ws, framework="框架A", updated_by="api")
        assert out["ok"] and out["model"]["error"] == "未提供 model"
        assert not (pathlib.Path(ws.workspace_dir) / LIBRARY_DIRNAME / "model.md").exists()

    def test_with_model_writes_and_readme_lists_it(self, tmp_path):
        ws = _ws(tmp_path)
        out = write_library(ws, framework="", updated_by="api",
                            model={"goal": "目标A"}, model_author="组长")
        assert out["model"]["ok"] and out["model"]["version"] == 1
        lib = pathlib.Path(ws.workspace_dir) / LIBRARY_DIRNAME
        assert (lib / "model.md").is_file()
        assert "model.md" in (lib / "README.md").read_text(encoding="utf-8")


class TestRoutes:
    """路由层：`POST library/update` 收 model/author；`GET library` 回 model 状态。"""

    class _Req:
        def __init__(self, body):
            self._b = body

        async def json(self):
            return self._b

    def _seed(self, tmp_path):
        from agent_community.platform.state import workshops
        ws = _ws(tmp_path)
        workshops[ws.workshop_id] = ws
        return ws

    def test_update_accepts_dict_model_and_author(self, tmp_path):
        from agent_community.platform.routers.workshops_lifecycle import workshop_library_update
        ws = self._seed(tmp_path)
        r = asyncio.run(workshop_library_update(ws.workshop_id, self._Req(
            {"model": {"goal": "做出看板", "parts": "前后端"}, "author": "组长"})))
        assert r["success"] is True and r["model"]["ok"] is True
        assert r["model"]["version"] == 1
        assert "缺 3 块" in r["note"], "缺块要在 note 里如实说明（界面显示未完成）"
        txt = (pathlib.Path(ws.workspace_dir) / LIBRARY_DIRNAME / "model.md").read_text(encoding="utf-8")
        assert "执笔：组长" in txt and "做出看板" in txt

    def test_get_library_reports_model_state(self, tmp_path):
        from agent_community.platform.routers.workshops_lifecycle import (
            workshop_library,
            workshop_library_update,
        )
        ws = self._seed(tmp_path)
        before = asyncio.run(workshop_library(ws.workshop_id))
        assert before["model"]["exists"] is False
        asyncio.run(workshop_library_update(ws.workshop_id, self._Req(
            {"model": {k: "x" for k, _t, _h in MODEL_SECTIONS}, "author": "组长"})))
        after = asyncio.run(workshop_library(ws.workshop_id))
        assert after["model"]["exists"] is True and after["model"]["missing"] == []
        assert "model.md" in after["preview"], "preview 要带 model.md 正文给界面"


class TestFrameworkInfo:
    """`read_framework_info`：让界面不用拿 preview 文本猜"是不是平台模板"（MiMo 76 号如实反映的缺口）。"""

    def _ws(self, tmp_path):
        return Workshop(workshop_id="w1", name="看板", workspace_dir=str(tmp_path / "ws2"),
                        hall_content="x", created_at="now")

    def test_absent(self, tmp_path):
        from agent_community.platform.workshop import read_framework_info
        info = read_framework_info(self._ws(tmp_path))
        assert info["exists"] is False and info["history_count"] == 0

    def test_platform_template_is_flagged(self, tmp_path):
        """建库只跑了平台初始化（没人写过）⇒ 必须标记为**平台模板**，界面才显示"未建立"。"""
        from agent_community.platform.workshop import read_framework_info, write_library
        ws = self._ws(tmp_path)
        write_library(ws, updated_by="api")          # 不传 framework ⇒ 只留平台模板
        info = read_framework_info(ws)
        assert info["exists"] is True and info["is_platform_template"] is True

    def test_human_write_clears_flag_and_backs_up(self, tmp_path):
        from agent_community.platform.workshop import read_framework_info, write_library
        ws = self._ws(tmp_path)
        write_library(ws, updated_by="api")
        write_library(ws, framework="# 我们自己的框架\n\n三条线推进。", updated_by="api")
        info = read_framework_info(ws)
        assert info["is_platform_template"] is False and info["size"] > 0 and info["updated_at"]
        write_library(ws, framework="# 改过的框架\n", updated_by="api")
        assert read_framework_info(ws)["history_count"] == 1, "覆盖前必须备份"

    def test_get_library_exposes_framework(self, tmp_path):
        import asyncio
        from agent_community.platform.routers.workshops_lifecycle import workshop_library
        from agent_community.platform.state import workshops
        ws = self._ws(tmp_path)
        workshops[ws.workshop_id] = ws
        out = asyncio.run(workshop_library(ws.workshop_id))
        assert "framework" in out and out["framework"]["exists"] is False
