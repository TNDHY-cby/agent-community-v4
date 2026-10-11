# -*- coding: utf-8 -*-
"""platform/compartment：成员级隔间（2026-10-07 老板口述设计）。

╔══════════════════════════════════════════════════════════════════════════╗
║  隔间（compartment）——**权威定义**（本节改了就同步 V23 §9 与前端 FE-03）  ║
╚══════════════════════════════════════════════════════════════════════════╝

- **是什么**：一个成员（岗位）在一块共享工作间里 **唯一可写** 的私有目录。
  老板原话：「**单个会话拥有一个隔间和一个具体桥**」。

- **归属 = 岗位（成员），不是会话** ⇒ **跨会话存活**。
  会话身份 ≠ 隔间身份：换了新会话要**复用同一隔间**，否则上一班的文件就丢了
  （这也是 D7 交接单必须写明「文件夹路径」的原因）。

- **创建时机 =「架起具体桥」那一步**（老板 2026-10-07 更正两次后的结论）：
  ```
  新建工作间  →  选定员工  →  ★架起具体桥  →  此刻创建隔间
  ```
  ❌ 不是"建工作间就建"（我一度这么放，**放早了**，已退回）。
  未选中的员工、还没架桥的房间，**本来就不该有空隔间**。
  唯一创建点：`server._auto_bridge_for_member()`（架桥那一步）；
  `ensure_compartment()` 幂等，故续作/重派复用同一隔间。

- **路径**：`<ws.workspace_dir>/<safe(职位)>_<safe(成员id)>/{input,output,temp}`
  工作间根是**共享区**（`hall.md`／共享资料库），**隔间内才是成员唯一可写区**。

- **配套**：每个会话在该成员隔间里发**自己那一份**具体桥（`bridge.py`）⇒ 永不与他人相撞。
  此前全房间共用一个根、同名 `bridge.py` **后写覆盖先写**（2026-10-07 隔离实例实测为证）。

- **唯一事实源 = 本模块（后端）**。前端**不得**自行镜像这个路径公式（后端一改就漂移）；
  要隔间路径/磁盘实况，走 `GET /api/workshop/{ws_id}/compartments`。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# 隔间内的三个**工作分区**（老板 43 号 / D8 的口径）
SUBDIRS = ("input", "output", "temp")

# 隔间内的**邮箱**（2026-10-08 老板指出：「一个员工一个具体桥和隔间，这样就可以隔离开来，
# 不会发生**谁收到就是谁的**」）——平台只往**该成员自己的** inbox 写，他的桥只从自己的 inbox 取。
# 与 SUBDIRS 分开维护：SUBDIRS 是对前端暴露的"三个工作分区"契约（FE-03 已按它渲染），
# 邮箱是新增的**寻址**契约；混进 SUBDIRS 会改掉前端已知字段。
MAILBOX_SUBDIRS = ("inbox", "replies")

# Windows 非法文件名字符 + 路径分隔符 + 控制字符
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_PART = 48


def safe_part(raw: Any, fallback: str = "未命名") -> str:
    """把一段文本变成**安全的单层路径名**。

    这是安全边界：`role` / `member_id` 可能来自请求体，**绝不能**借它们跳出工作间。
    做法：去掉分隔符与非法字符 -> 去掉 `.`/`..` 这类整段名 -> 截断。
    """
    s = str(raw or "").strip()
    s = _ILLEGAL.sub("_", s)
    s = s.replace("..", "_")            # 先掐掉穿越序列
    s = s.strip(" .")                   # 再去掉首尾的点与空格（Windows 会静默去掉）
    s = re.sub(r"_{2,}", "_", s)
    if not s or s in (".", ".."):
        return fallback
    return s[:_MAX_PART]


def compartment_dir(ws_dir: Any, role: Any, member_id: Any) -> Path:
    """隔间目录：`<ws_dir>/<职位>_<成员id>`（不做任何 I/O）。"""
    base = Path(str(ws_dir or ""))
    return base / f"{safe_part(role, '成员')}_{safe_part(member_id, 'x')}"


def is_inside(path: Path, root: Path) -> bool:
    """`path` 是否在 `root` 之内（含 root 自身）。用于把"不越界"变成可断言的事实。"""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def inspect_compartment(ws_dir: Any, role: Any, member_id: Any) -> dict:
    """**磁盘实况**：该成员隔间此刻到底存不存在、有哪些分区与文件。

    这是 `GET /api/workshop/{ws_id}/compartments` 的底层，也是"唯一事实源"的落点：
    前端的镜子再准也只是公式，公式证明不了文件此刻在不在（FE-03 §2 自己写明了这一点）。
    **只读**，失败不抛。
    """
    root = Path(str(ws_dir or ""))
    d = compartment_dir(root, role, member_id)
    out = {
        "member_id": str(member_id or ""),
        "role": str(role or ""),
        "dir": str(d),
        "inside_workspace": is_inside(d, root),
        "exists": False,
        "subdirs": {},
        "mailbox": {},          # 2016-10-08 新增（附加字段，不动既有 subdirs 契约）
        "mailbox_pending": {},  # 每个邮箱里积压的文件数——"有没有人取"看得见
        "files": [],
        "bridge_files": [],
    }
    try:
        out["exists"] = d.is_dir()
        if out["exists"]:
            out["subdirs"] = {name: (d / name).is_dir() for name in SUBDIRS}
            for name in MAILBOX_SUBDIRS:
                p = d / name
                out["mailbox"][name] = p.is_dir()
                try:
                    out["mailbox_pending"][name] = len(list(p.glob("*"))) if p.is_dir() else 0
                except Exception:  # noqa: BLE001
                    out["mailbox_pending"][name] = -1
            names = sorted(p.name for p in d.iterdir() if p.is_file())
            out["files"] = names
            out["bridge_files"] = [n for n in names
                                   if n in ("bridge.py", "connect_contract.json", "activation_prompt.txt")]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def mailbox_dir(ws_dir: Any, role: Any, member_id: Any, which: str = "inbox") -> Path:
    """该成员隔间里的**专属邮箱**目录（不做 I/O）。`which` ∈ {"inbox","replies"}。

    这是"谁收到就是谁的"的根治点：平台**只**往该成员的 inbox 写，
    他的桥**只**从自己的 inbox 取 ⇒ 同 harness 多会话也不会串。
    """
    name = which if which in MAILBOX_SUBDIRS else "inbox"
    return compartment_dir(ws_dir, role, member_id) / name


def ensure_compartment(ws_dir: Any, role: Any, member_id: Any) -> dict:
    """建隔间、input/output/temp 三个**工作分区**，以及 inbox/replies 两个**邮箱**目录。

    **失败不抛**（与 audit.py 同原则）：拿不到目录也要让激活流程继续，只如实报告 error。
    """
    root = Path(str(ws_dir or ""))
    d = compartment_dir(root, role, member_id)
    # 双保险：即使 safe_part 有漏网，也绝不建到工作间之外
    if not is_inside(d, root):
        return {"ok": False, "error": f"隔间路径越界（拒绝创建）: {d}", "dir": str(d)}
    made: list[str] = []
    try:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            made.append(str(d))
        subs = {}
        for name in SUBDIRS:
            p = d / name
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                made.append(str(p))
            subs[name] = str(p)
        # 邮箱（寻址用）：与工作分区分开报告，避免改动前端已知的 subdirs 契约
        mail = {}
        for name in MAILBOX_SUBDIRS:
            p = d / name
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                made.append(str(p))
            mail[name] = str(p)
        return {"ok": True, "dir": str(d), "created": made, "subdirs": subs,
                "mailbox": mail, "inbox": mail.get("inbox", ""),
                "role": safe_part(role, "成员"), "member_id": safe_part(member_id, "x")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "dir": str(d)}
