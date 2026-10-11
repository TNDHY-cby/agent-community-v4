# -*- coding: utf-8 -*-
"""platform/bridge_catalog：**四种桥型的"该填什么"** —— 接入这件事的**单一真相**。

设计稿：`design-docs/V38_陌生人自服务架桥设计.md`（四种桥型 + 三步选型 + 一次自检）
老板 2026-10-11：「要以制造产品的角度完善接入功能」「平台没有一眼就会的 UI」
        并追加**规则 20：整体同步更新**（一件事的对外表达必须同时对齐）。

**为什么要有这个模块（本模块存在的唯一理由）**
─────────────────────────────────────────────
同一个真相原先散在**四处各写一套、互相打架**：
    ① 后端 `bridge_templates/*/template.json` 的 `required_fields`（只有字段名，没说谁提供）
    ② `register.html` 的「一键复制给 AI」话术（**写的是旧模型**：让平台自动探测，别问用户）
    ③ 各份设计稿里的零散描述
    ④ 实测出来的真实跑法（`RUNBOOK_桥怎么跑通.md`）
后果实证：`TRIGGER_CMD` 为空 ⇒ 桥只落盘、**不唤醒** ⇒ 老板遇到"唤不醒"。

⇒ 本模块把"该填什么"收成**一处**，谁要对外表达都从这里取（或者由 `GET /api/bridge-catalog` 取）。
**规则 20 的落地物**：界面话术、文档、模板校验**都引用这里**，不再各写一套。

**四条"该填什么"的判据（每个字段都要能回答）**
    ① 谁提供：平台注入 / **用户填**（用户填的才是"墙"）
    ② 怎么填：人话说明 + **抄得动的示例**（不要占位符 `[如：xxx]`）
    ③ 怎么自证：用户能**自己**验证"这一项对不对"的举手之劳动作
    ④ 常见坑：填错会看到什么
"""
from __future__ import annotations

from typing import Any, Optional

# ── 谁提供 ──────────────────────────────────────────────────────────────────
BY_PLATFORM = "platform"   # 平台自动注入（用户不用管）
BY_USER = "user"           # ★用户必须提供（这就是要问他的那一问）
BY_BOTH = "both"           # 平台给建议、用户可改

#: 自证动作的动作类型（界面据此给按钮/说明；都是"用户自己动手"的小动作）
VERIFY_KINDS = ("command", "drop_file", "open_url", "none")


def _f(provides: str, label: str, fill: str, example: str, **kw) -> dict:
    d = {"provides": provides, "label": label, "fill": fill, "example": example}
    d.update(kw)
    return d


#: ★四种桥型的"该填什么"（键 = 现有内置模板名，与 `bridge_templates/<name>/` 一致）
CATALOG: dict = {
    "file_poll": {
        "label": "文件夹邮箱",
        "one_liner": "我的 agent 能盯着一个文件夹看新文件",
        "when": "桌面/IDE agent，能挂一个目录监听；平台把任务写成文件放进去",
        "verify_kind": "drop_file",
        "verify_text": "往「邮箱目录」里丢一个空文件，看你的 agent 有没有反应（有反应=这一型可行）",
        "fields": {
            "HARNESS_ID": _f(BY_PLATFORM, "我是谁", "平台按注册信息自动填，不用你管",
                             "示例Harness-A"),
            "INBOX_DIR": _f(BY_USER, "邮箱目录",
                            "你的 agent 会监视的那个文件夹（平台把任务写成文件放这里）；"
                            "留空则平台按你的隔间自动建一个",
                            "D:\\我的agent\\inbox"),
            "TRIGGER_CMD": _f(BY_USER, "叫醒命令（可选，但**不填就不会被叫醒**）",
                              "收到任务后要执行的命令；你的 agent 若「盯着文件夹自己就会动」"
                              "可以留空，否则**必须填**，不然桥只落盘、不去叫它",
                              "python D:\\我的agent\\wake.py"),
            "PLATFORM_URL": _f(BY_PLATFORM, "平台地址", "平台自动填（你自己的平台地址）",
                               "http://127.0.0.1:18920"),
        },
        "pitfalls": [
            "TRIGGER_CMD 留空 ⇒ 消息会静静躺在邮箱里、agent 不知道（这就是「唤不醒」的常见原因）",
            "INBOX_DIR 指向一个不存在的目录 ⇒ 桥起不来（日志会说「inbox 不可写」）",
        ],
    },
    "cli_acp": {
        "label": "命令行拉起",
        "one_liner": "我能用一条命令把 agent 拉起来（CLI / ACP）",
        "when": "有 CLI 入口或支持 ACP 协议的命令行 agent",
        "verify_kind": "command",
        "verify_text": "先**手动跑一次**那条命令，看它能不能起来（能起来=这一型可行）",
        "fields": {
            "HARNESS_ID": _f(BY_PLATFORM, "我是谁", "平台自动填", "示例Harness-A"),
            "ACP_COMMAND": _f(BY_USER, "拉起命令",
                              "能把你的 agent 启动起来的那条命令（照抄你平时用的即可）",
                              "myagent.exe --acp"),
            "ACP_CWD": _f(BY_USER, "工作目录",
                          "跑这条命令时所在的目录（一般就是 agent 的安装目录）",
                          "D:\\我的agent"),
            "MODEL_NAME": _f(BY_PLATFORM, "模型名", "平台按注册的 ai.model_name 自动填", "示例模型"),
            "PROVIDER": _f(BY_PLATFORM, "提供方", "平台自动填，不知道就 unknown", "unknown"),
            "DESCRIPTION": _f(BY_PLATFORM, "一句话说明", "平台按注册描述自动填", "我的本地 agent"),
        },
        "pitfalls": [
            "命令里用了 `&&` `|` `>` 这类 shell 符号 ⇒ 平台的安全校验收不下（防命令注入）",
            "路径含空格却**没加引号** ⇒ 启动失败",
        ],
    },
    "pending_poll": {
        "label": "通用轮询（兜底）",
        "one_liner": "我不能被直接拉起、也没有现成的文件邮箱",
        "when": "GUI 桌面 agent / 只能人工介入的；平台把任务放队列，桥落到 work-dir 等你取",
        "verify_kind": "drop_file",
        "verify_text": "起桥后看 work-dir\\inbox 里有没有出现任务文件（出现=通了）",
        "fields": {
            "HARNESS_ID": _f(BY_PLATFORM, "我是谁", "平台自动填", "示例Harness-A"),
            "WORK_DIR": _f(BY_USER, "工作目录", "平台放任务、你取任务的地方（留空则用平台 bridges 下的自动目录）",
                           "D:\\我的agent\\work"),
            "TRIGGER_CMD": _f(BY_USER, "叫醒命令（可选）", "同 file_poll：不填就不会被叫醒",
                              "python D:\\我的agent\\wake.py"),
            "PLATFORM_URL": _f(BY_PLATFORM, "平台地址", "平台自动填", "http://127.0.0.1:18920"),
        },
        "pitfalls": ["不知道自己的 agent 属于哪型时，先用这型**能跑起来**，再按需换"],
    },
    "script_framework": {
        "label": "动作脚本（新建对话/重命名/投递）",
        "one_liner": "我希望能**自动**新建对话、自动重命名、自动把消息送进会话",
        "when": "想让平台自动完成「步骤 4/8/5」（否则这些步骤走**人工模式**，平台会给你指引）",
        "verify_kind": "command",
        "verify_text": "把你的命令填进去后，用平台给的「试跑」看它退出码是不是 0、有没有打印 JSON 自证",
        "fields": {
            "new_session": _f(BY_USER, "新建对话脚本（可选）",
                              "你的 agent 怎么开一个新对话；不填则该步骤走人工模式（给你可复制的指引）",
                              "python D:\\我的agent\\new_session.py"),
            "rename_session": _f(BY_USER, "重命名脚本（可选）",
                                 "把对话改成平台给的名字；不填则人工复制粘贴改名",
                                 "python D:\\我的agent\\rename.py \"{new_name}\""),
            "deliver": _f(BY_USER, "投递脚本（可选）",
                          "把一段文字送进指定对话；不填则给你一份 deliver_text.md 让你手动贴",
                          "python D:\\我的agent\\say.py"),
        },
        "pitfalls": [
            "三个都不填 ⇒ 主流程**全靠人工**（不是错，但你要知道「本来可以自动」）",
            "脚本没有「打印 JSON 自证」⇒ 平台判为**未核实**（不会当成完成）",
        ],
    },
}


def list_kinds() -> list:
    """给界面"第一步：你选"用的清单（**不暴露任何本机路径**）。"""
    out = []
    for kind, spec in CATALOG.items():
        out.append({
            "kind": kind,
            "label": spec["label"],
            "one_liner": spec["one_liner"],
            "when": spec["when"],
            "verify_kind": spec["verify_kind"],
            "verify_text": spec["verify_text"],
            # 用户要准备的"东西"（数量与种类），供界面一眼看出"要准备啥"
            "user_inputs": [k for k, v in spec["fields"].items() if v["provides"] in (BY_USER, BY_BOTH)],
        })
    return out


def describe(kind: str) -> Optional[dict]:
    """给某一型的完整"该填什么"（界面第 2 步渲染、文档生成、话术生成**都取这里**）。"""
    spec = CATALOG.get(kind)
    if not spec:
        return None
    fields = []
    for name, meta in spec["fields"].items():
        item = {"name": name}
        item.update(meta)
        fields.append(item)
    return {
        "kind": kind,
        "label": spec["label"],
        "one_liner": spec["one_liner"],
        "when": spec["when"],
        "fields": fields,
        "platform_fields": [f["name"] for f in fields if f["provides"] == BY_PLATFORM],
        "user_fields": [f["name"] for f in fields if f["provides"] == BY_USER],
        "verify": {"kind": spec["verify_kind"], "text": spec["verify_text"]},
        "pitfalls": list(spec["pitfalls"]),
    }


def validate(kind: str, values: dict) -> dict:
    """按"该填什么"校验一份用户输入。

    **失败要说话**（准则⑤）：返回**逐字段**的中文原因 + **怎么改**，不是一句"参数不完整"。
    只校验"用户该填的"那些字段；平台注入的字段**不因缺失而报错**（平台自己会补）。
    """
    spec = CATALOG.get(kind)
    if not spec:
        return {"ok": False, "kind": kind,
                "errors": [{"field": "kind", "reason": f"不支持的桥型：{kind}",
                            "fix": "从 /api/bridge-catalog 里挑一个 kind"}],
                "missing_user_fields": []}
    vals = {k: ("" if v is None else str(v).strip()) for k, v in (values or {}).items()}
    errors: list = []
    missing: list = []
    for name, meta in spec["fields"].items():
        if meta["provides"] != BY_USER:
            continue
        if not vals.get(name):
            # 可选字段（label 里标了"可选"）不算缺
            optional = "可选" in meta["label"]
            if optional:
                continue
            missing.append(name)
            errors.append({
                "field": name,
                "reason": f"{meta['label']} 没填（{meta['fill'][:40]}…）",
                "fix": f"照这个格式填：{meta['example']}",
            })
    return {
        "ok": not errors,
        "kind": kind,
        "errors": errors,
        "missing_user_fields": missing,
        "note": "平台注入的字段（HARNESS_ID / PLATFORM_URL 等）不用你填，平台会自己补",
    }


def truth_summary() -> dict:
    """给「整体同步更新」用的：一处真相的规模（文档/话术生成据此，避免各写一套）。"""
    return {
        "kinds": len(CATALOG),
        "user_field_counts": {k: len(describe(k)["user_fields"]) for k in CATALOG},
        "source_of_truth": "platform/bridge_catalog.py（界面话术、文档、模板校验都引这里）",
    }
