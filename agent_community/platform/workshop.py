"""V5 工作间 — 数据模型 + 最小竖切编排。

本阶段只做最小竖切：确认开启工作间 → 激活 HA 会话 → 员工读大厅 → 回「收到」。

对应设计稿《工作间模式设计V5.md》：
- §4 数据模型（Workshop / WorkshopMember / Session）
- §6 平台唤醒专员 + 桥协议
- §11.3 激活新对话：工作区坐标 + 固定流程指引（不含任务详情）

最小竖切的"读大厅"落地：平台把大厅内容写进 workspace 的 hall.md，
流程指引（AGENTS.md）告诉员工"先读 hall.md"；员工用文件工具读，不必 HTTP 拉平台。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .acp_bridge import AcpBridge, AcpSession


# ── 数据模型（§4，最小子集）────────────────────────────────

@dataclass
class WorkshopMember:
    member_id: str
    role: str
    display_name: str
    harness_ids: list[str] = field(default_factory=list)
    session: Optional[AcpSession] = None
    status: str = "pending"  # pending / activating / entered / working / idle / blocked / offline


@dataclass
class Workshop:
    workshop_id: str
    name: str
    workspace_dir: str
    hall_content: str          # 用户最初发在大厅的内容
    members: list[WorkshopMember] = field(default_factory=list)
    discussion: list = field(default_factory=list)  # 讨论消息 [{role, content, timestamp}]
    status: str = "draft"      # draft / discussing(一级讨论) / selecting(选定员工) / division(二级讨论) / running(工作) / review(三级讨论) / done
    pinned: bool = False       # 是否置顶（大厅侧边栏）
    created_at: str = ""       # 创建时间 ISO（侧边栏排序）

    def to_dict(self) -> dict:
        """序列化为 dict（持久化 + 列表 API 用）。"""
        return {
            "workshop_id": self.workshop_id,
            "name": self.name,
            "workspace_dir": self.workspace_dir,
            "hall_content": self.hall_content,
            "status": self.status,
            "pinned": self.pinned,
            "created_at": self.created_at or "",
            "members": [
                {
                    "member_id": m.member_id,
                    "role": m.role,
                    "display_name": m.display_name,
                    "harness_ids": m.harness_ids,
                    "status": m.status,
                }
                for m in self.members
            ],
            "discussion": self.discussion,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Workshop":
        """从 dict 恢复（load_state 用）。"""
        members = [
            WorkshopMember(
                member_id=m.get("member_id", ""),
                role=m.get("role", "员工"),
                display_name=m.get("display_name", ""),
                harness_ids=m.get("harness_ids", []),
                status=m.get("status", "pending"),
            )
            for m in d.get("members", [])
        ]
        return cls(
            workshop_id=d.get("workshop_id", ""),
            name=d.get("name", "未命名工作间"),
            workspace_dir=d.get("workspace_dir", ""),
            hall_content=d.get("hall_content", ""),
            members=members,
            discussion=d.get("discussion", []),
            status=d.get("status", "draft"),
            pinned=d.get("pinned", False),
            created_at=d.get("created_at", ""),
        )


# ── 固定流程指引（§九 草稿，落成 workspace/AGENTS.md）────────

WORKFLOW_GUIDE = """# 外端Agent生产合作社（External Agent Community） 工作间指引

你是本工作间的一名员工。请遵守：

1. 先读本目录下的 `hall.md`（大厅内容 / 任务）。
2. 读完回复「收到，已进入工作状态。」完成对接。
3. 之后遵守钩子协作：发言末尾单独一段写 `@唤:<对象>` 可激活对方查看内容。
4. 遇到项目方向节点，先整理进展与方向选项，汇报等待用户决定。
"""


# ── 文件语义：把大厅内容 + 流程指引写进 workspace ────────────

def write_workspace_files(workshop: Workshop) -> None:
    ws = Path(workshop.workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "hall.md").write_text(workshop.hall_content, encoding="utf-8")
    (ws / "AGENTS.md").write_text(WORKFLOW_GUIDE, encoding="utf-8")


# ── 最小竖切流程 ────────────────────────────────────────────

def activate_member(bridge: AcpBridge, workshop: Workshop, member: WorkshopMember,
                     activation_prompt: str = "") -> str:
    """激活一个员工：开 HA 会话（cwd=工作区），按注册时生成的 HA 专属提示词让它读 hall.md 并回「收到」。

    activation_prompt 为空时回退默认模板；{role}/{workspace_dir} 占位符由本函数填充。
    """
    member.status = "activating"
    sess = bridge.new_session(workshop.workspace_dir)
    if sess is None:
        member.status = "blocked"
        return "[FAIL] session/new 失败"
    member.session = sess

    tpl = activation_prompt or (
        "你已进入工作间，你的角色是「{role}」，工作区坐标：{workspace_dir}。\n"
        "现在只做一件事：用你的文件工具读取工作区目录下的 hall.md 文件，"
        "读完原样回复：「收到，已进入工作状态。」\n"
        "不要执行 hall.md 里的任务，不要调用其他工具，回复完就停下等待后续指令。"
    )
    try:
        prompt = tpl.format(role=member.role, workspace_dir=workshop.workspace_dir)
    except Exception:
        prompt = tpl

    # 激活消息：只给坐标 + 指引，不含任务详情（任务在 hall.md 里）
    stop, text = bridge.prompt(
        sess.session_id,
        prompt,
        timeout=180,
    )
    member.status = "entered" if "收到" in text else "blocked"
    return f"[{member.role}] stop={stop} reply={text.strip()[:200]}"


def run_minimal_flow(hall_content: str, workspace_dir: str) -> Workshop:
    """端到端最小竖切：建工作间 → 写文件 → 激活 1 个 dsh 员工 → 读大厅 → 确认。"""
    workshop = Workshop(
        workshop_id="ws_minimal",
        name="最小竖切验证",
        workspace_dir=workspace_dir,
        hall_content=hall_content,
    )
    workshop.members.append(WorkshopMember(
        member_id="m1", role="码农", display_name="harness-a",
        harness_ids=["harness-a"],
    ))

    write_workspace_files(workshop)

    bridge = AcpBridge()
    bridge.start()
    try:
        bridge.initialize()
        for m in workshop.members:
            print("  ", activate_member(bridge, workshop, m), flush=True)
    finally:
        bridge.close()

    workshop.status = "running"
    return workshop


# ── 自测 ────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    ws_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "workshop_minimal_ws"))
    hall = "任务：抓取某网站商品价格，生成一份价格分析报告。\n验证码：HALL-12345\n"
    print("[workshop] 最小竖切开始 ...", flush=True)
    w = run_minimal_flow(hall, ws_dir)
    print("[workshop] status =", w.status, flush=True)
    for m in w.members:
        print(f"[workshop] {m.role} -> {m.status}", flush=True)
    print("DONE", flush=True)
