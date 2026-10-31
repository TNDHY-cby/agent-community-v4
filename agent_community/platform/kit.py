# -*- coding: utf-8 -*-
"""`platform/kit`：**对接单拼装**（设计 `V116` §8 第 4/9 步 / `V115` P5 批）。

## 三条硬规则

1. **纯字符串拼装，不用模型**（平台不判断；同一输入两次拼出**逐字相同**的文本）；
2. **模板里不许出现令牌**：A 档 token 由调用方**现签**后单独放进来（`token` 参数），
   不进任何模板文件，库里只存指纹（`V110` §5）；
3. **两个根写死**（`workshop_path` / `mind_path`）：架构师 2026-10-27 答案 2 —— 激活那一瞬间它就是事实。

## 命名（`V116` §10）

`<workshop_id>-<项目短名>-<岗位/角色>-<成员序号>`，序号 **N+1 不复用**。
"""
from __future__ import annotations

from . import boot_prompt as _boot

KIT_HEADER = """# 对接单（{role}）· {workshop_id}

> 这份文本**整段复制**给你的外端 agent（不要只挑一半）。它包含：你是谁、两个根在哪、
> 从哪读、别碰哪、怎么把结果交回来。**平台不会替你写它的会话**。

## 1. 你是谁
- 角色：{role}
- 工作间：{workshop_id}（{project_short}）
- 会话名（请照这个名字改）：`{session_label}`

## 2. 两个根（**写死，别猜**）
- 工作区（根B，一次性的）：`{workshop_path}`
- 心智根（根A，永久的）：`{mind_path}`

## 3. 先读这些（**禁止全盘扫描**）
{entry_points}
- 工作区清单：`{workshop_path}/{manifest_name}`
- 你的变更通知：`{notice_path}`

## 4. 别碰这些（黑名单）
{ignore_zones}

## 5. 你的干活位置
- 任务（派活落这里）：`{slot_dir}/任务/`
- 产物（**只写这里**）：`{slot_dir}/产物/`

## 6. 怎么把结果交回来（回执，整单原子）
```
#回执 v1
类型: 名单|派活|验收|状态
工作间: {workshop_id}
成员: {member_field}
凭据: {credential}
序号: <每次比上次大 1 的整数>
---
<体：一行一条，字段用半角 | 分隔>
---
行数: <体的行数>
```
{credential_note}

## 6b. 回话口令（**这一节是给平台自己验的，不用你打勾**）
{handshake_section}

## 7. Boot Protocol（激活时你自己照做）
{boot}

## 8. 何时算"接上了"
- **A 档（有协议）**：平台**自己观测**到它回过话 —— 用上面第 6b 节的口令打一次
  `POST …/members/<序号>/handshake`（口令**一次性**，用过就作废，重放不算）；
  以前只要求"它真发过请求"，但那样"随便发一次"也能算，所以现在必须**答得出这张单子上的口令**。
- **B 档（只有桥）**：桥**真取走**过一次文件（平台看得见文件从有到无）。
- **C 档（只能人工）**：粘贴 + 回车 + 改名 三件都做到，并写一句"覆盖到哪一段"。
**未打勾不算接上（最高只记 acked）**；平台不会替你打勾。
账上会区分来源：`platform-observed`（平台自己看到的，带时间戳）与 `user-check`（你打的勾），界面上不混。  

---
（这份对接单由平台拼装，**不含任何令牌**除 A 档现签那一个；换工作间请重新拼一份）
"""


def _bullets(items: list, empty: str = "- （没有）") -> str:
    vals = [str(x).strip() for x in (items or []) if str(x).strip()]
    return "\n".join("- `%s`" % v for v in vals) if vals else empty


def member_field(slot_dir: str, is_lead: bool) -> str:
    """回执头里 `成员:` 填什么 —— 组长填「组长」，其余填 `岗位/员工NN`。"""
    return "组长" if is_lead else str(slot_dir)


def session_label(workshop_id: str, project_short: str, role: str, seq: int) -> str:
    """`<工作间id>-<项目短名>-<角色>-<序号>`（N+1 由调用方保证不复用）。"""
    proj = (str(project_short or "").strip() or "未命名")[:20]
    role_s = (str(role or "").strip() or "成员")[:12]
    return "%s-%s-%s-%02d" % (workshop_id, proj, role_s, int(seq))


def build_kit(*, role: str, workshop_id: str, project_short: str, session_label_value: str,
              workshop_path: str, mind_path: str, slot_dir: str, notice_path: str,
              entry_points: list, ignore_zones: list, member_field_value: str,
              credential: str = "", credential_note: str = "", is_lead: bool = False,
              manifest_name: str = "manifest.json", handshake: str = "",
              member_seq: int = 0) -> str:
    """拼一份对接单（**纯函数**：同样的入参一定得到同样的字符串）。

    `handshake`：**一次性回话口令**（A 档「平台自验」用）。由调用方现生成，**平台库里只存它的指纹**；
    非 A 档（B/C）不传 ⇒ 那一段如实写"这个档位不用口令"。
    """
    boot = _boot.render(agent_key="%s-%s" % (workshop_id, role), workshop_path=workshop_path,
                        mind_path=mind_path, task_focus="", ignore_zones=ignore_zones)
    if handshake:
        hs = ("这张单子带**一次性**口令（只在这一步用一次，用过作废）：\n"
              "```\n"
              "POST /api/workshop/%s/members/%d/handshake\n"
              "Authorization: Bearer <第 6 节那份凭据>\n"
              "body: {\"code\": \"%s\", \"note\": \"我是 <你的会话名>\"}\n"
              "```\n"
              "- 口令：`%s`（一次有效）。答对了平台就记下「它真读到过这张单子」并**盖上时间戳**；\n"
              "- 平台**不会**因为你打了勾就记成接上 —— 这一下必须由它自己发出来。"
              % (workshop_id, int(member_seq or 0), handshake, handshake))
    else:
        hs = ("这个档位**不用口令**：B 档看桥有没有真取走文件，C 档靠你人眼确认三件事。\n"
              "平台不会凭空说「已验证」。")
    return KIT_HEADER.format(
        role=role, workshop_id=workshop_id, project_short=project_short or "（未填）",
        session_label=session_label_value, workshop_path=workshop_path, mind_path=mind_path,
        entry_points=_bullets(entry_points), ignore_zones=_bullets(ignore_zones, "- （没设黑名单）"),
        notice_path=notice_path, slot_dir=slot_dir, member_field=member_field_value,
        credential=credential or "（B/C 档留空）",
        credential_note=credential_note or "（B 档位置即凭据、C 档人工确认 —— 都不需要令牌）",
        boot=boot, manifest_name=manifest_name, handshake_section=hs)
