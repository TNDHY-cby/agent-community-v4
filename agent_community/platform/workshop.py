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

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .acp_harness_bridge import AcpHarnessBridge, AcpSession
# 隔间路径的**唯一推导点**在 compartment.py；分工文件夹里要写出该员工隔间路径，故这里引用它
# （compartment.py 只依赖标准库，不会造成循环导入）
from .compartment import compartment_dir as _compartment_dir
from .compartment import safe_part as _safe_part


# ── 数据模型（§4，最小子集）────────────────────────────────

@dataclass
class WorkshopMember:
    member_id: str
    role: str
    display_name: str
    harness_ids: list[str] = field(default_factory=list)
    session: Optional[AcpSession] = None
    status: str = "pending"  # pending / activating / entered / working / idle / blocked / offline

    # ── V-15 步骤3：进入状态机 ────────────────────────────────────
    # 与 status **正交**：status 是成员生命周期，entry_state 是**派发进度**。
    # 合一的代价是 `activating` 分不清「推都推出去没」—— 前端看不出假成功。
    # 取值与转移见 platform/entry_state.py（含各唤醒方式的最高可达表）。
    entry_state: str = "pending"   # pending/dispatched/acked/entered/blocked/timeout/needs_human
    entry_reason: str = ""         # 受阻/超时原因（如「桥进程未运行」），前端据此显示原因

    # ── V25 §4.4：会话归属（老板流程步骤 7~9 的落点）──────────────────────
    # session_aliases[0] = 平台生成的名字（`<工作间id>-<项目>-<职位>-<成员id>`），用户可增删改；
    # session_ref = 该成员对应的真实会话定位信息（建会话脚本回报的 evidence，含 id/name/window）。
    session_aliases: list[str] = field(default_factory=list)
    session_ref: dict = field(default_factory=dict)

    # ── 老板口述②「员工掉线」的抢救进度（member_offline.py 用）────────────
    # 三件都要**落盘**：否则重启后计数清零，就会无限重试、永远不上报人。
    offline_attempts: int = 0
    offline_last_at: float = 0.0
    offline_last_reason: str = ""

    # ── V28：**通道生命周期**（老板 2026-10-09 拍板：停用通道 / 新建通道）────────
    # 名词：**通道**＝该成员在本工作间里"通着的那条路"（一个会话 + 它的桥 + 它的邮箱收发）；
    #       **隔间**＝成员的数据目录（跟人走、跨通道存活，**停用通道不动隔间**）。
    # 老板要的场景：用户不满意 / agent 上下文过长老化 ⇒ 换成员，或**换成员的对话**。
    # 现状缺口：`session_aliases` 非空就跳过重建 ⇒ 之前**换不了对话**（V28 §1）。
    channel_seq: int = 1              # 当前通道序号（第 1 个通道 = 1；换通道后 +1）
    channel_state: str = "pending"    # pending(未接入) / active(运行中) / retired(已停用)
    channel_since: float = 0.0        # 本通道建立时间（unix 秒）
    # 历次退役通道（留痕：序号/名字/会话引用/何时停/为什么）——停用≠删除，这里只记不删隔间
    retired_channels: list = field(default_factory=list)

    # ── V36 §2.2：**具体桥**（老板 2026-10-11 口径：「一条桥 = 一个具体会话」）──────────
    # 为什么单独立这份：`session_ref` 只说"这个成员对应哪个会话"，**没说"它的桥是哪一条"**；
    #   而老板要的是 ① 能为该成员**显式挑一条具体桥模板**（V36 §3.1 的 bridge-template），
    #   ② 这条桥**绑到该成员的具体会话**，③ 界面上能直显「桥 → 会话名 → 隔间 → 邮箱」。
    # 字段口径见 `design-docs/V36_桥模型接口与数据契约.md §2.2`：
    #   template_name 用了哪条模板 / bridge_path 实际落点（隔间内）/ session_ref+session_name 绑的会话
    #   state: none(未架接) | attached(已架接) | retired(已停用) —— **none 时界面必须明说"未架接"，不许空白**
    #   verified/verified_at: 平台不采信声明（准则⑤），自证材料单独记
    bridge_ref: dict = field(default_factory=dict)


@dataclass
class Workshop:
    workshop_id: str
    name: str
    workspace_dir: str
    hall_content: str          # 用户最初发在大厅的内容
    members: list[WorkshopMember] = field(default_factory=list)
    discussion: list = field(default_factory=list)  # 讨论消息 [{role, content, timestamp}]
    resources: list = field(default_factory=list)   # 工作间资源库 [{rid,name,kind,path,note,uploader,task_node,created_at}]
    task_tree: list = field(default_factory=list)   # 任务树节点 [{node_id,label,parent_id,kind,status,note,resources[],created_at}]
    status: str = "draft"      # draft / discussing(一级讨论) / selecting(选定员工) / division(二级讨论) / running(工作) / review(三级讨论) / done
    pinned: bool = False       # 是否置顶（大厅侧边栏）
    created_at: str = ""       # 创建时间 ISO（侧边栏排序）
    assignments: dict = field(default_factory=dict)  # 组长委派细化/转向记录 {member_id: [{kind,instruction,note,at,by,dispatched}]}
    # 老板主流程 ②：本工作间由哪条大厅广播任务**自动建成**（空串=人工建的）。
    # 用它做幂等键与反查（GET /api/tasks/{task_id}/workshop），避免为此再开一个状态文件。
    origin_task_id: str = ""

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
            "origin_task_id": self.origin_task_id or "",
            "members": [
                {
                    "member_id": m.member_id,
                    "role": m.role,
                    "display_name": m.display_name,
                    "harness_ids": m.harness_ids,
                    "status": m.status,
                    # ── 2026-10-08 修：这三项此前**没落盘** ⇒ 重启即丢 ──────────────
                    # 实测（22 个工作间）落盘成员只有 member_id/role/display_name/harness_ids/status，
                    # 于是 `entry_state`（**派发进度**，老板明确要求前端读它）在重启后一律回落到
                    # `pending` —— 属于"状态静默丢失"，正是准则⑤ 要杜绝的"看起来没派发/看起来成功"。
                    # 一并补上 V25 §4.4 的会话归属字段（步骤 7~9 的落点）。
                    "entry_state": getattr(m, "entry_state", "pending"),
                    "entry_reason": getattr(m, "entry_reason", ""),
                    "session_aliases": list(getattr(m, "session_aliases", []) or []),
                    "session_ref": dict(getattr(m, "session_ref", {}) or {}),
                    # 掉线抢救进度也落盘：否则重启后计数清零 ⇒ 无限重试、永不上报人
                    "offline_attempts": int(getattr(m, "offline_attempts", 0) or 0),
                    "offline_last_at": float(getattr(m, "offline_last_at", 0) or 0),
                    "offline_last_reason": getattr(m, "offline_last_reason", ""),
                    # V28 通道生命周期（不落盘的话，重启后"当前是第几个通道/是否已停用"就丢了）
                    "channel_seq": int(getattr(m, "channel_seq", 1) or 1),
                    "channel_state": getattr(m, "channel_state", "pending"),
                    "channel_since": float(getattr(m, "channel_since", 0) or 0),
                    "retired_channels": list(getattr(m, "retired_channels", []) or []),
                    # V36 §2.2：具体桥（一条桥 ↔ 一个具体会话）——不落盘则重启后"这条桥绑哪个会话"就丢了
                    "bridge_ref": dict(getattr(m, "bridge_ref", {}) or {}),
                }
                for m in self.members
            ],
            "discussion": self.discussion,
            "resources": self.resources,
            "task_tree": self.task_tree,
            "assignments": self.assignments,
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
                # 与 to_dict 对称：旧数据无这些键时用默认值（向后兼容）
                entry_state=m.get("entry_state", "pending"),
                entry_reason=m.get("entry_reason", ""),
                session_aliases=list(m.get("session_aliases") or []),
                session_ref=dict(m.get("session_ref") or {}),
                # V28：旧数据没有这些键时给默认值（向后兼容：老工作间=第 1 个通道、状态待定）
                channel_seq=int(m.get("channel_seq", 1) or 1),
                channel_state=m.get("channel_state", "pending"),
                channel_since=float(m.get("channel_since", 0) or 0),
                retired_channels=list(m.get("retired_channels") or []),
                # V36 §2.2：具体桥引用（旧数据无此键 ⇒ 空 dict = "未架接"，界面据此明说不许空白）
                bridge_ref=dict(m.get("bridge_ref") or {}),
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
            resources=d.get("resources", []),
            task_tree=d.get("task_tree", []),
            status=d.get("status", "draft"),
            pinned=d.get("pinned", False),
            created_at=d.get("created_at", ""),
            assignments=d.get("assignments", {}),
            origin_task_id=d.get("origin_task_id", ""),
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

LIBRARY_DIRNAME = "library"


def member_folder_name(member, taken: set | None = None) -> str:
    """该员工在共享资料库里的**分工文件夹**名：`<agent名>-<职位>`（老板 2026-10-08 定的形态）。

    老板原话：「**每个员工分配到一个文件夹（名称：agent名-职位），里面有详细分工内容**」。
    重名时补 `-<成员id>` 兜底（绝不覆盖别人的分工）。
    """
    name = _safe_part(getattr(member, "display_name", "") or getattr(member, "member_id", ""), "成员")[:24]
    role = _safe_part(getattr(member, "role", ""), "员工")[:16]
    base = f"{name}-{role}"
    if taken is None or base not in taken:
        return base
    return f"{base}-{_safe_part(getattr(member, 'member_id', ''), 'm')[:12]}"


def member_assignment_dir(workshop, member) -> Path:
    """该员工的**分工文件夹路径**（唯一推导点 —— 资料库与激活消息共用，避免两处口径分叉）。"""
    taken: set = set()
    for m in (getattr(workshop, "members", None) or []):
        if m is member:
            break
        taken.add(member_folder_name(m))
    return Path(workshop.workspace_dir) / LIBRARY_DIRNAME / member_folder_name(member, taken)


# ── V29 项目建模（老板 2026-10-10「按你建议」拍板）────────────────────────────
# 二级讨论的产出物：`library/model.md` —— 目标/组成/分工/依赖/产物落点 五块。
# 规则（与 framework.md 不同，理由写在这里）：
#   · framework.md 是「已存在非空就不覆盖」（防平台自动生成顶掉人的字）；
#   · model.md **允许反复更新**（二级讨论会来回改），但**每次写入前备份**到
#     `model.history/<时间戳>.md` ⇒ 既能迭代、又能回看当初为什么这么分工。
#   · 权限：平台**没有按 agent 身份的写入 ACL**（本端点由界面调用）⇒ 不假装有，
#     改为**记录执笔人**（进头部与审计；界面显示由谁执笔）。
MODEL_SECTIONS = (
    ("goal", "1. 目标与验收", "这个项目要做成什么；怎么算做完"),
    ("parts", "2. 组成（模块/任务分解）", "项目由哪几块组成；每块做什么"),
    ("roles", "3. 分工对应", "谁负责哪块（与 plan.md 的关系见文件头）"),
    ("deps", "4. 依赖与顺序", "谁必须先做、谁等谁；哪些可以并行"),
    ("outputs", "5. 产物落点", "每块的成果放在谁的 output/；整合者按这里核对"),
)
MODEL_PLACEHOLDER = "（待补）"



def read_model_info(workshop) -> dict:
    """**只读**项目模型状态（界面用）：是否存在 / 执笔人 / 更新时间 / 版本 / 缺哪些块 / 历史版本数。

    只读**不建目录**（界面一次刷新不该产生副作用）；缺块据实列出，供界面显示"未完成"。
    """
    info = {"exists": False, "path": "", "author": "", "updated_at": "", "version": 0,
            "missing": [], "history_count": 0, "preview": ""}
    try:
        lib = Path(getattr(workshop, "workspace_dir", "") or "") / LIBRARY_DIRNAME
        p = lib / "model.md"
        hist = lib / "model.history"
        if hist.is_dir():
            info["history_count"] = len(list(hist.glob("*.md")))
        if not p.is_file():
            return info
        txt = p.read_text(encoding="utf-8")
        info.update({"exists": True, "path": str(p), "preview": txt[:2000]})
        for line in txt.splitlines()[:6]:
            if line.startswith("> 执笔："):
                for part in line.lstrip("> ").split("｜"):
                    part = part.strip()
                    if part.startswith("执笔："):
                        info["author"] = part[3:].strip()
                    elif part.startswith("更新："):
                        info["updated_at"] = part[3:].strip()
                    elif part.startswith("版本："):
                        _v = part[3:].strip().lstrip("v")
                        info["version"] = int(_v) if _v.isdigit() else 0
                break
        for _key, title, _hint in MODEL_SECTIONS:
            if f"## {title}" not in txt:
                info["missing"].append(title)
            else:
                _seg = txt.split(f"## {title}", 1)[1]
                _seg = _seg.split("## ", 1)[0]
                if MODEL_PLACEHOLDER in _seg:
                    info["missing"].append(title)
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}: {e}"
    return info


# 框架文件里"平台还没被人写过"的判据：建库时平台写的模板句（见 write_library 的占位文案）。
# 由**后端判定**并作为字段给出 —— 界面不用再拿 preview 文本做字符串猜（MiMo 76 号如实反映了这个缺口）。
FRAMEWORK_TEMPLATE_MARK = "待一级/二级讨论收敛后更新"


def read_framework_info(workshop) -> dict:
    """**只读**项目框架状态（界面用）：是否存在 / 大小 / 最后写入时间 / 历史版本数 / 是否仍是平台模板。

    只读**不建目录**（界面刷新不该有副作用）；`is_platform_template=True` 表示
    「建库后**还没有人写过**」（内容还是平台占位模板），界面应显示「未建立」而不是「已建立」。
    """
    info = {"exists": False, "size": 0, "updated_at": "", "history_count": 0,
            "is_platform_template": False, "error": ""}
    try:
        lib = Path(getattr(workshop, "workspace_dir", "") or "") / LIBRARY_DIRNAME
        p = lib / "framework.md"
        hist = lib / "framework.history"
        if hist.is_dir():
            info["history_count"] = len(list(hist.glob("*.md")))
        if not p.is_file():
            return info
        txt = p.read_text(encoding="utf-8")
        st = p.stat()
        info.update({"exists": True, "size": st.st_size,
                     "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                     "is_platform_template": FRAMEWORK_TEMPLATE_MARK in txt and len(txt.strip()) < 1200})
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}: {e}"
    return info

def model_header(workshop, *, author: str = "", version: int = 1, updated_at: str = "") -> str:
    """模型文件的头部（执笔人/更新时间/版本/规矩）。"""
    return "\n".join([
        f"# 项目模型 · {getattr(workshop, 'name', '') or '未命名工作间'}",
        "",
        f"> 执笔：{author or '（未记录）'} ｜ 更新：{updated_at or '（尚未建立）'} ｜ 版本：v{int(version)}"
        f" ｜ 工作间：`{getattr(workshop, 'workshop_id', '')}`",
        "> 规矩：本文件由**组长**执笔（普通成员只读）；`plan.md` 是平台自动生成的**事实分工**，",
        "> 两者若不一致，**分工以 `plan.md` 为准**（V29 §4）。",
        "",
    ])


def model_template(workshop) -> str:
    """模型的**空骨架**（给组长照填；缺的块一律写「（待补）」，**不假装完整**）。"""
    body = []
    for _key, title, hint in MODEL_SECTIONS:
        body += [f"## {title}", "", f"<!-- 提示：{hint} -->", "", MODEL_PLACEHOLDER, ""]
    return model_header(workshop) + "\n" + "\n".join(body).rstrip() + "\n"


def write_model(workshop, *, blocks: dict | None = None, text: str = "",
                author: str = "", updated_by: str = "api") -> dict:
    """写入 `library/model.md`（V29 §2）。**每次写入前备份**；失败如实返回，不假装成功。"""
    ws_dir = Path(getattr(workshop, "workspace_dir", "") or "")
    lib = ws_dir / LIBRARY_DIRNAME
    hist = lib / "model.history"
    out = {"ok": False, "path": "", "version": 0, "backed_up": "", "missing": [],
           "author": author or "", "error": ""}
    try:
        lib.mkdir(parents=True, exist_ok=True)
        hist.mkdir(parents=True, exist_ok=True)
        path = lib / "model.md"
        if path.is_file():
            try:
                _old = path.read_text(encoding="utf-8")
                if _old.strip():
                    bak = hist / f"{time.strftime('%Y%m%d-%H%M%S')}.md"
                    bak.write_text(_old, encoding="utf-8")
                    out["backed_up"] = str(bak)
            except Exception as e:  # noqa: BLE001
                out["error"] = f"备份旧版失败：{type(e).__name__}: {e}"
        version = len(list(hist.glob("*.md"))) + 1   # 备份之后算 ⇒ 第二次写就是 v2
        head = model_header(workshop, author=author or updated_by, version=version,
                            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        _blk = {str(k): str(v or "").strip() for k, v in (blocks or {}).items()}
        body_parts = []
        for key, title, hint in MODEL_SECTIONS:
            _v = _blk.get(key, "")
            if not _v:
                out["missing"].append(title)
                _v = MODEL_PLACEHOLDER
            body_parts += [f"## {title}", "", _v, ""]
        _raw = str(text or "").strip()
        content = head + "\n" + (_raw if _raw else "\n".join(body_parts).rstrip()) + "\n"
        path.write_text(content, encoding="utf-8")
        out.update({"ok": True, "path": str(path), "version": version})
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def write_library(workshop: Workshop, framework: str = "", updated_by: str = "system",
                 model: dict | None = None, model_text: str = "",
                 model_author: str = "") -> dict:
    """老板主流程 ⑥：**共享资料库**（自动建立的文件夹）。

    老板 2026-10-07 的话：「共享资料库**可以是一个自动建立的文件夹**，也可以是 git 之类的库」。
    本函数实现前者（零依赖、离线可用、可离线自托管），布局对 git **向后兼容**
    （纯文本 + 一条 .gitignore，日后 `git init` 即可升格为库，不需要改结构）。

    位置：`<ws.workspace_dir>/library/`（工作间**共享区**，成员都能读）
      - `README.md`     索引与规矩（谁写、谁能读）
      - `plan.md`       **分工安排**——平台按当前名单+委派记录**自动生成**（勿手改，改了会被覆盖）
      - `framework.md`  **项目框架**——讨论收敛后写入；**传了就覆盖**（界面上"编辑框架"用），覆盖前自动备份到 `framework.history/`；不传则保持原样
      - `.gitignore`    为将来 `git init` 预留（不忽略任何内容，只占位说明）

    与 `resources/` + `RESOURCES.md` 的分工：那两者是**上传资源的清单**（谁传了什么文件）；
    本目录是**协作共识**（谁干什么、项目怎么搭）。**不是同一件事，不要合并。**
    """
    ws = Path(workshop.workspace_dir)
    lib = ws / LIBRARY_DIRNAME
    made: list[str] = []
    try:
        lib.mkdir(parents=True, exist_ok=True)
        made.append(str(lib))

        # ① 分工安排（纯规则生成，永远重写）
        rows = []
        for m in (getattr(workshop, "members", None) or []):
            _hid = ", ".join(m.harness_ids or []) or "（未绑定 harness）"
            _comp = f"{m.role}_{m.member_id}"
            rows.append(f"| {m.display_name or '—'} | {m.role} | {_hid} | `{_comp}/` | {m.status} |")
        _assign = getattr(workshop, "assignments", None) or {}
        _assign_lines = []
        for mid, recs in _assign.items():
            for r in (recs or []):
                _assign_lines.append(
                    f"- [{mid}] {r.get('kind', '')}｜{r.get('instruction', '')}"
                    + (f"（{r.get('note')}）" if r.get("note") else "")
                )
        plan = [
            "# 分工安排（平台自动生成，请勿手改）",
            "",
            f"- 工作间：{workshop.name}（`{workshop.workshop_id}`）",
            f"- 状态：{getattr(workshop, 'status', '')}",
            f"- 更新时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 任务",
            "",
            (workshop.hall_content or "（空）").strip(),
            "",
            "## 名单与隔间",
            "",
            "| 成员 | 职位 | 负责 harness | 隔间（唯一可写目录） | 状态 |",
            "|---|---|---|---|---|",
        ] + (rows or ["| （尚无成员） | — | — | — | — |"]) + [
            "",
            "> 隔间路径 = `<工作间目录>/<职位>_<成员id>/`，内含 input/ output/ temp/。",
            "> 各成员**只在自己的隔间里写文件**；本库与工作间根都是共享区。",
            "",
            "## 组长委派记录",
            "",
        ] + (_assign_lines or ["（暂无）"]) + [""]
        (lib / "plan.md").write_text("\n".join(plan), encoding="utf-8")
        made.append(str(lib / "plan.md"))

        # ④ **每个员工一个分工文件夹** `<agent名>-<职位>/`（老板 2026-10-08 定的形态）：
        #    里面写"我要做的事 + 我的工作区 + 去哪看全局"。外端 agent **拿路径自己去看**，
        #    平台不把内容贴进激活消息（消息保持通用模板、只换路径）。
        _taken: set = set()
        _member_lines = []
        for m in (getattr(workshop, "members", None) or []):
            folder = member_folder_name(m, _taken)
            _taken.add(folder)
            mdir = lib / folder
            mdir.mkdir(parents=True, exist_ok=True)
            _mine = [r for r in (_assign.get(m.member_id) or [])]
            _who = [
                f"- 显示名：{m.display_name or '—'}",
                f"- 职位：{m.role}",
                f"- 成员 id：`{m.member_id}`",
                f"- 负责 harness：{', '.join(m.harness_ids or []) or '（未绑定）'}",
            ]
            _aliases = list(getattr(m, "session_aliases", []) or [])
            _who.append(f"- 我的会话名：`{_aliases[0]}`" if _aliases else "- 我的会话名：（尚未建立）")
            _todo = ([f"- {r.get('kind', '')}｜{r.get('instruction', '')}"
                      + (f"（{r.get('note')}）" if r.get("note") else "") for r in _mine]
                     or ["- （平台尚未对你细分派：请看 `../plan.md` 的全员分工，按职位自行推进）"])
            (mdir / "assignment.md").write_text("\n".join([
                f"# 我的分工（{m.display_name or m.member_id} · {m.role}）",
                "",
                "> 这是**给这位员工**的分工说明，平台自动生成、请勿手改；有异议在讨论区提。",
                "",
                "## 我是谁", "",
            ] + _who + [
                "",
                "## 我要做的事", "",
            ] + _todo + [
                "",
                "## 我的工作区（**唯一可写目录**）", "",
                f"- 隔间：`{_compartment_dir(workshop.workspace_dir, m.role, m.member_id)}/`",
                "  - 产物放 `output/`——**阶段整合会来收这里的东西**；",
                "  - 邮箱 `inbox/`：平台往这里投消息，我的桥从这里取。",
                "- 共享资料库（共识产物，别当草稿区）：`../`",
                "",
                "## 去哪看全局", "",
                "- 全员分工：`../plan.md`",
                "- 项目框架：`../framework.md`",
                "- 工作间指引：`<工作间目录>/AGENTS.md`",
                "",
                "## 怎么回报", "",
                "把进展/结果回报到平台（回报地址与字段格式见平台下发的激活消息）。",
                "",
            ]), encoding="utf-8")
            made.append(str(mdir / "assignment.md"))
            _member_lines.append(f"| `{folder}/` | {m.display_name or '—'}（{m.role}） | 该员工的详细分工 |")

        # ② 项目框架（讨论收敛后写入；已有内容即视为人工成果，不覆盖）
        fw = lib / "framework.md"
        if framework.strip():
            # ★真实语义（2026-10-10 核对）：**传了就覆盖**（界面上"编辑框架"要能改得动）。
            #   但覆盖前**备份**旧版（与 model 同款），免得一次误操作把人的字抹掉。
            #   ⚠️ 本函数文档与 README 原先写"平台不覆盖已有内容" —— 与代码不符，已一并订正。
            try:
                if fw.is_file() and fw.read_text(encoding="utf-8").strip():
                    _fh = lib / "framework.history"
                    _fh.mkdir(parents=True, exist_ok=True)
                    (_fh / f"{time.strftime('%Y%m%d-%H%M%S')}.md").write_text(
                        fw.read_text(encoding="utf-8"), encoding="utf-8")
                    made.append(str(_fh))
            except Exception as e:  # noqa: BLE001
                print(f"[library] framework 备份失败（继续写入）: {type(e).__name__}: {e}", flush=True)
            fw.write_text(framework.strip() + "\n", encoding="utf-8")
            made.append(str(fw))
        elif not fw.exists():
            fw.write_text(
                "# 项目框架\n\n"
                "> 待一级/二级讨论收敛后更新（在看板上「编辑框架」，或 `POST /api/workshop/{id}/library/update` 带 framework 字段）。\n\n"
                "## 任务原文\n\n" + (workshop.hall_content or "（空）").strip() + "\n",
                encoding="utf-8",
            )
            made.append(str(fw))

        # ③ 索引与规矩
        _mf = "\n".join(_member_lines) if _member_lines else "| （尚无成员） | — | — |"
        (lib / "README.md").write_text(
            "# 共享资料库\n\n"
            "本目录**全工作间共享**：所有成员都能读。\n\n"
            "| 文件/目录 | 是什么 | 谁能改 |\n|---|---|---|\n"
            "| `plan.md` | 分工安排（谁干什么） | **平台自动生成**，勿手改 |\n"
            "| `model.md` | **项目模型**（目标/组成/分工/依赖/产物落点） | 由**组长**执笔（V29）；普通成员只读；每次保存前备份到 `model.history/` |\n"
            "| `framework.md` | 项目框架（项目怎么搭） | 整合者/组长写（界面可编辑）；**覆盖前自动备份**到 `framework.history/` |\n"
            "| `<agent名>-<职位>/` | **每个员工一个分工文件夹**，内含 `assignment.md`（他要做的事） | **平台自动生成**，勿手改 |\n\n"
            "## 员工分工文件夹\n\n"
            "| 目录 | 是谁 | 里面是什么 |\n|---|---|---|\n"
            + _mf + "\n\n"
            "> **外端 agent 请拿路径自己去看**（激活消息里会给你这些路径），"
            "平台不会把分工内容贴在消息里——消息是**通用模板**，只换路径。\n\n"
            "**规矩**：各成员**只在自己的隔间**（`<工作间目录>/<职位>_<成员id>/`）里写文件；\n"
            "本目录与工作间根是共享区，写入前先确认这是共识产物而不是你的草稿。\n\n"
            "> 形态说明（老板 2026-10-07）：共享资料库**可以就是这样一个自动建立的文件夹**，\n"
            "> 也可以是 git 之类的库。当前实现是前者；布局为纯文本，日后 `git init` 即可升格，不需要改结构。\n",
            encoding="utf-8",
        )
        made.append(str(lib / "README.md"))
        (lib / ".gitignore").write_text(
            "# 预留：本目录日后可整体 git init 升格为「库」。当前不忽略任何内容。\n"
            "# 若启用 git：建议忽略各成员隔间里的临时产物，而不是本库内容。\n",
            encoding="utf-8",
        )
        # ⑤ V29 项目模型：**给了才写**（给了就允许更新；写前自动备份，见 write_model 注释）
        _model_out: dict = {"ok": False, "path": "", "version": 0, "backed_up": "",
                            "missing": [], "author": model_author or "", "error": "未提供 model"}
        if (model is not None) or str(model_text or "").strip():
            _model_out = write_model(workshop, blocks=model, text=model_text,
                                     author=model_author, updated_by=updated_by)
            if _model_out.get("ok"):
                made.append(str(_model_out.get("path") or ""))
        return {"ok": True, "dir": str(lib), "written": made,
                "framework_written": bool(framework.strip()) or str(fw) in made,
                "model": _model_out}
    except Exception as e:  # noqa: BLE001 — 资料库写失败不阻断建间/开工
        return {"ok": False, "dir": str(lib), "error": f"{type(e).__name__}: {e}", "written": made}


def collect_member_outputs(workshop: Workshop) -> dict:
    """**收各成员隔间 `output/` 里的产物**（老板流程 ⑧：「阶段完成由一个 agent 整合」）。

    为什么单独做这一步：此前⑧只做到"**汇报文本**汇总"（批次完成时 `_parallel_batch_summary`
    把成员的**文字回报**攒成一段唤醒组长）——**各成员隔间 `output/` 里真正的产物没人收**。
    隔间建好了、桥也发了，产物却躺在各自目录里没人整合，等于流程没走完。

    **只读**、不搬动成员文件（避免"整合者把别人隔间搞乱"）；产出的是**清单**，
    让整合者按路径自己去取。**如实报告空目录**（准则⑤：不许假装整合成功）。
    """
    rows = []
    total_files = 0
    empty_members = []
    for m in (getattr(workshop, "members", None) or []):
        outdir = Path(workshop.workspace_dir) / f"{m.role}_{m.member_id}" / "output"
        files = []
        try:
            if outdir.is_dir():
                for p in sorted(outdir.rglob("*")):
                    if p.is_file():
                        st = p.stat()
                        files.append({"name": str(p.relative_to(outdir)),
                                      "bytes": st.st_size, "mtime": int(st.st_mtime)})
        except Exception as e:  # noqa: BLE001
            print(f"[integrate] 读取 {outdir} 失败: {e}", flush=True)
        total_files += len(files)
        if not files:
            empty_members.append(m.member_id)
        rows.append({
            "member_id": m.member_id, "role": m.role,
            "display_name": getattr(m, "display_name", ""),
            "status": getattr(m, "status", ""), "entry_state": getattr(m, "entry_state", ""),
            "output_dir": str(outdir), "files": files, "file_count": len(files),
        })
    return {
        "members": rows, "total_files": total_files,
        "with_output": [r["member_id"] for r in rows if r["file_count"]],
        "without_output": empty_members,
        "note": ("没有任何成员的 output/ 里有产物——本次整合无内容可汇"
                 if total_files == 0 else ""),
    }


def write_integration_manifest(workshop: Workshop, integrator: str = "") -> dict:
    """把「谁产出了什么」落成 `library/integration.md`，供**整合者**（组长/主管）按路径取。

    与 `_parallel_batch_summary` 的分工：那个汇总的是**文字回报**（讨论了什么）；
    本函数汇总的是**磁盘产物**（真交了什么文件）。两者互补，不重复。
    """
    col = collect_member_outputs(workshop)
    lib = Path(workshop.workspace_dir) / LIBRARY_DIRNAME
    lib.mkdir(parents=True, exist_ok=True)
    p = lib / "integration.md"
    lines = [
        "# 阶段整合清单（平台自动生成，请勿手改）",
        "",
        f"- 工作间：{workshop.name}（`{workshop.workshop_id}`）",
        f"- 整合者：{integrator or '（未指定，默认组长）'}",
        f"- 状态：{getattr(workshop, 'status', '')}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 产物总数：**{col['total_files']}** 个",
        "",
        "> 各成员产物在**他们自己的隔间** `output/` 下；本清单只给路径，**整合者按路径去取**，",
        "> 平台不搬动成员文件（避免把别人的隔间搞乱）。",
        "",
    ]
    if col["total_files"] == 0:
        lines += ["## 无产物可整合（如实标注）", "",
                  "所有成员的 `output/` 都是空的（或隔间尚未创建）。",
                  "**这不等于「整合完成」** —— 请先确认成员是否真的交付了东西。", ""]
    for r in col["members"]:
        lines.append(f"## {r['display_name'] or r['member_id']}（{r['role']} · `{r['member_id']}`）")
        lines.append("")
        lines.append(f"- 隔间：`{r['output_dir']}`")
        lines.append(f"- 状态：{r['status']} ｜ 派发进度：{r['entry_state']}")
        if r["files"]:
            lines.append(f"- 产物 {r['file_count']} 个：")
            for f in r["files"]:
                lines.append(f"  - `{f['name']}`（{f['bytes']} B）")
        else:
            lines.append("- **产物：无**（该成员 output/ 为空）")
        lines.append("")
    lines += [
        "## 整合者要做的事",
        "",
        "1. **逐个核对**上面的路径，确认产物真的在（清单只证明生成清单那一刻它在）；",
        "2. 把可用产物整合成**一份阶段成果**，写进 `framework.md` 或另建文件放本目录；",
        "3. **如实报告缺项**：谁没交、交的对不对，别把缺的算成有（准则⑤ 失败要说话）。",
        "",
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return {"ok": True, "path": str(p), "manifest": p.name,
            "total_files": col["total_files"],
            "with_output": col["with_output"], "without_output": col["without_output"],
            "note": col["note"]}


def library_dir(workshop: Workshop) -> Path:
    """共享资料库目录（不做 I/O）。"""
    return Path(workshop.workspace_dir) / LIBRARY_DIRNAME


def write_resources_manifest(workshop: Workshop) -> None:
    """把工作间资源库清单落盘为 RESOURCES.md（供成员/外端 harness 读取共享）。

    每个资源一行：`- [类型] 名称 | 路径 | 说明 | 上传者 | 关联任务`。
    resources 目录缺失时自动创建（组长上传资源前的落点）。
    """
    ws = Path(workshop.workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    res_dir = ws / "resources"
    res_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# 工作间资源库", "", "本目录（resources/）用于共享工作产出与资源路径，成员可自行查看。", ""]
    if not workshop.resources:
        lines.append("（暂无资源）")
    else:
        for r in workshop.resources:
            kind = r.get("kind", "file")
            name = r.get("name", "")
            path = r.get("path", "")
            note = r.get("note", "")
            uploader = r.get("uploader", "")
            task_node = r.get("task_node", "")
            tail = f" | 说明: {note}" if note else ""
            tail += f" | 上传者: {uploader}" if uploader else ""
            tail += f" | 关联任务: {task_node}" if task_node else ""
            lines.append(f"- [{kind}] {name} | 路径: {path}{tail}")
    (ws / "RESOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_workspace_files(workshop: Workshop) -> None:
    """落盘工作区共享文件：hall.md（任务）/ AGENTS.md（流程指引）/ 资料清单。

    ⚠️ **本函数不建成员隔间**（2026-10-07 老板更正时机两次后的结论）：
    链条是「新建工作间 → 选定员工 → **架起具体桥** → 此刻才建隔间」。
    我一度把建隔间塞进这里（"建间就建"），**放早了**，已退回。
    隔间的创建点唯一确定在**架桥那一步**（`server._auto_bridge_for_member`）——
    桥产物正好落进刚建好的隔间里；未选中的员工、以及还没架桥的房间，本来就不该有空隔间。
    """
    ws = Path(workshop.workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "hall.md").write_text(workshop.hall_content, encoding="utf-8")
    (ws / "AGENTS.md").write_text(WORKFLOW_GUIDE, encoding="utf-8")
    write_resources_manifest(workshop)
    # 老板主流程 ⑥：**共享资料库是自动建立的文件夹**。它属共享区（不是成员隔间），
    # 故与 hall.md/AGENTS.md 一样在**建间时**就建好；内容随后由讨论收敛更新。
    # （与 §9 隔间的时机规则不冲突：隔间是成员私有、架桥时才建。）
    _lib = write_library(workshop)
    if not _lib.get("ok"):
        print(f"[library] 工作间 {workshop.workshop_id} 资料库建立失败（不阻断建间）: {_lib.get('error')}",
              flush=True)


# ── 最小竖切流程 ────────────────────────────────────────────

def activate_member(bridge: AcpHarnessBridge, workshop: Workshop, member: WorkshopMember,
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
        member_id="m1", role="码农", display_name="dsh",
        harness_ids=["dsh"],
    ))

    write_workspace_files(workshop)

    bridge = AcpHarnessBridge()
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
