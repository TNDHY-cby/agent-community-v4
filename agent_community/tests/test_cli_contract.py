# -*- coding: utf-8 -*-
"""CLI ↔ 平台端点 的**契约守卫**（2026-10-14 实测事故换来的）。

**事故**（朋友第一次照 README 做就会撞到的那种）：
README 第 3 步 `python -m agent_community.cli task "写一个排序函数"`，
真跑的结果是 `[ERROR] 提交失败: HTTP 400` —— 而 CLI **只打状态码、不说原因**。
问服务端才知道真因：`{"error":"command 不能为空"}` ——
CLI 发的字段叫 `content`，而 `/api/command` 只认 `input` / `command`（见 `api_command`）。
**两边各自都"没错"，合起来就是坏的** ⇒ 必须有跨模块的契约守卫。

**本文件怎么判**（按真结果，不读源码文本）：
  ① 把 CLI **真正会发的那份请求体**（`cli._task_payload`，只有这一处）直接喂给
     真端点函数 `server.api_command` ⇒ 必须成功建任务；
     ⚠️**不在测试里复制一份 payload**：那样一改又漂移，守卫就白守了。
  ② CLI 的 `task-result` 读的字段（status / result / created_at / agent_ids / title）
     必须在 `GET /api/task/{id}` 的真返回里存在 —— 否则它又会"静默少显示一栏"
     （实测：它此前**从不打印 result**，README 第 4 步叫"看结果"却看不到结果）。
"""
from __future__ import annotations

import asyncio

import pytest


class _Req:
    """最小请求替身（`api_command` 只用 `.json()`）。"""

    def __init__(self, body=None):
        self._body = body or {}
        self.headers = {}
        self.query_params = {}
        self.client = type("C", (), {"host": "127.0.0.1"})()

    async def json(self):
        return self._body


@pytest.fixture()
def srv(monkeypatch):
    import agent_community.platform.server as s
    monkeypatch.setattr(s, "save_state", lambda: None, raising=False)
    return s


def test_cli_task_payload_is_accepted_by_the_real_endpoint(srv):
    """★核心：CLI 发的请求体，真端点必须收下（字段名漂移当场变红）。"""
    from agent_community import cli

    payload = cli._task_payload("契约守卫：写一个排序函数")
    out = asyncio.run(srv.api_command(_Req(payload)))

    body = getattr(out, "body", None)
    if body is not None:                     # Utf8JSONResponse = 出错分支
        raise AssertionError(
            "★CLI 的请求体被端点拒了（这就是 README 第 3 步失败的那个坑）：%s"
            % body.decode("utf-8", "replace")[:300])
    assert out.get("success") is True and out.get("task_id"), \
        "端点应回 success + task_id：%r" % (out,)


def test_cli_task_result_reads_fields_the_api_really_returns(srv):
    """CLI 的 `task-result` 依赖的字段，必须在真返回里 —— 少一个就是"静默少显示"。"""
    from agent_community import cli

    out = asyncio.run(srv.api_command(_Req(cli._task_payload("契约守卫：字段核对"))))
    task_id = out.get("task_id")
    assert task_id, "没建成任务，本用例的前提不成立：%r" % (out,)

    got = asyncio.run(srv.get_task(task_id))
    assert isinstance(got, dict), "GET /api/task/{id} 应回任务字典：%r" % (got,)
    # ⚠️这里必须列 **CLI 真正读的**字段。实测教训：原 CLI 读 `agent_ids`，
    #   而 Task 模型里**没有这个字段** ⇒ 它那句"Agent: N 个参与"永远是 0
    #   （对用户说的假话）。本条守卫就是照出这类"字段名对不上"的。
    need = {"status", "result", "created_at", "completed_at", "title",
            "hand_raises", "delegations", "delegation_results"}
    missing = sorted(need - set(got))
    assert not missing, (
        "★CLI 要显示的字段在接口返回里没有了：%s ⇒ 用户会看到「完成了却没有结果」（准则⑤）" % missing)
