"""platform/routers/workshops_common：workshops 端点组的共享辅助（V-13 自 workshops.py 拆分）。

共享：_sv()（双副本活跃 server 取用）、_leader_*（组长派工辅助）、_platform_base_url_safe。
四个业务子模块只依赖本模块，彼此无跨组调用（拆分前已实测：10 处跨组引用全指向 common）。
"""
from __future__ import annotations
import sys
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from ..task_state_machine import CREATED
from ..task_state_machine import DISCUSSING
from ..task_state_machine import EV_COMPLETE
from ..task_state_machine import EV_DROP
from ..task_state_machine import EV_RESOLVE
from ..task_state_machine import EV_RESUME
from ..task_state_machine import EV_TIMEOUT
from ..task_state_machine import EXECUTING
from ..protocol import Message
from ..protocol import MessageType
from pathlib import Path
from ..protocol import Task
from ..protocol import TaskStatus
from ..workshop import Workshop
from ..workshop import WorkshopMember
from ..experience_v2 import harness_reputation_bonus as _v2_harness_reputation_bonus
from ..experience_v2 import evaluate_workshop_completion as _v2_evaluate_workshop_completion
from ..audit import audit_log as _audit_log
from ..ai_external import run_ai_call as ai_external_run_ai_call
import asyncio
from ..memory import capability_ledger
from ..harness_adapter import harness_manager
import json
import os
from ..task_state_machine import should_interject
from ..memory import task_memory
from uuid import uuid4
from ..workshop import write_resources_manifest
from ..workshop import write_workspace_files
from ..state import interject_store, pending_activations, task_state_machine, tasks, workshops

router = APIRouter()

def _sv():
    """返回活跃 server 副本（__main__，uvicorn 实际运行的实例）模块。"""
    return sys.modules["__main__"]


def _leader_pick_member(ws, member_id: str):
    """按 member_id 从工作间中取成员，找不到返回 None。"""
    if not member_id:
        return None
    for m in ws.members:
        if m.member_id == member_id:
            return m
    return None


def _leader_member_hid(member) -> str:
    """取成员首个 harness_id；未绑定返回空串。"""
    return (member.harness_ids or [None])[0] or ""


def _leader_build_assign_payload(ws, member, instruction: str, note: str, kind: str) -> dict:
    """构造组长委派/转向 payload（与 _leader_division_discuss 同构，供 _dispatch_to_harness 使用）。"""
    hid = _leader_member_hid(member)
    if kind == "redirect":
        label = "任务转向·承接"
        body = (
            "你是本工作间的员工，组长刚把一项任务转交给你承接，请立即接手并推进。\n"
            "【转向指令】" + instruction + ("\n【组长备注】" + note if note else "") + "\n\n"
            "工作区实时路径：" + ws.workspace_dir + "\n"
            "任务（大厅内容）：\n" + ws.hall_content
        )
    else:
        label = "组长委派细化"
        body = (
            "你是本工作间的员工，组长刚给你下发/调整了细化工作指令，请按指令执行或调整当前工作。\n"
            "【细化指令】" + instruction + ("\n【组长备注】" + note if note else "") + "\n\n"
            "工作区实时路径：" + ws.workspace_dir + "\n"
            "任务（大厅内容）：\n" + ws.hall_content
        )
    return {
        "type": "leader_assign" if kind == "assign" else "leader_redirect",
        "kind": kind,
        "workshop_id": ws.workshop_id,
        "member_id": member.member_id,
        "role": member.role,
        "workspace_dir": ws.workspace_dir,
        "instruction": instruction,
        "report_endpoint": "/api/harness/task-result",
        "message": body + (
            "\n\n【回报要求】处理完必须把结果回报给平台，否则用户看不到：\n"
            "POST " + _platform_base_url_safe() + "/api/harness/task-result\n"
            "body: {\"workshop_id\":\"" + ws.workshop_id + "\",\"member_id\":\"" + member.member_id
            + "\",\"harness_id\":\"" + hid + "\",\"ok\":true,\"result\":\"你的完整回复/结果\"}\n"
            "把你的回复内容放进 result 字段回报上去。"
        ),
    }


def _platform_base_url_safe() -> str:
    """取平台基准地址，避免重复 import 冲突（函数体内延迟导入）。"""
    from ..server import _platform_base_url
    try:
        return _platform_base_url()
    except Exception:
        return "http://127.0.0.1:18920"


