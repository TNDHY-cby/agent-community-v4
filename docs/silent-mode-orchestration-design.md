---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: fee6cb6926e215051ee1772520098e59_2d03e78cb58d11f193fb525400393706
    ReservedCode1: jWeJGGT7jMjIWFxW4OOWBb9sGIWxPxu/wdkeOoVEjHIquGhR2G87iqkPn008JYFuU06GLxqg1tPym3CHeaW7TbetIeQ3XHfOGDOqdqQYMBdzyyS7SsAgm0iK946kz77wMC5aLpJOZMeBBJVdKZK1sO2M/qwYGvob0dz7XzUYIaLCV4dKaNTl1SAKIJ8=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: fee6cb6926e215051ee1772520098e59_2d03e78cb58d11f193fb525400393706
    ReservedCode2: jWeJGGT7jMjIWFxW4OOWBb9sGIWxPxu/wdkeOoVEjHIquGhR2G87iqkPn008JYFuU06GLxqg1tPym3CHeaW7TbetIeQ3XHfOGDOqdqQYMBdzyyS7SsAgm0iK946kz77wMC5aLpJOZMeBBJVdKZK1sO2M/qwYGvob0dz7XzUYIaLCV4dKaNTl1SAKIJ8=
---

# 静默模式（token_save）调度接入设计

> 日期：2026-09-21
> 范围：agent-community-v4 平台 server.py 调度链路
> 目标：把"工作间工作模式 = token_save"从纯标记做实为真正的调度语义，实现省 token。

## 一、背景

当前 token_save 模式只是 `data/workshop_modes.json` 里的一行标记（GET/POST /api/workshop/{ws_id}/mode），
`_apply_harness_reply` / `_leader_has_event` / `_leader_poll_loop` / `_sm_heartbeat` 等调度逻辑完全不读它。

现状每条员工回报都会推进 `_worker_last_seq` 事件水位 → `_leader_poll_loop` 每 30s 扫描发现水位差 →
`_leader_digest` 组摘要 → `_dispatch_to_harness` 唤醒组长 LLM。常规进度刷屏 = 组长 token 持续消耗。

## 二、静默模式语义（已与用户拍板）

token_save 工作间下：

1. **员工普通进度回报**（done / progress / 无 status / none）：
   - 照常写入讨论区（role=member，无 system 标记，前端与用户可见）；
   - **不推进 `_worker_last_seq` 事件水位**，不唤醒组长 LLM；
   - 零 token 的平台侧逻辑照常执行（经验沉淀 `_v2_apply_completion_rewards`、成员状态机、capability_ledger）。

2. **员工异常回报**（blocked / stuck / failed）：
   - 照常写入讨论区（用户可见）；
   - **不唤醒组长**（不调 `_system_interject` L1 升级路径）；
   - **直接反馈给用户**：写入讨论区带 system 标记的醒目提示（复用现有 meta.system 机制，前端可见、事件判定自动过滤、不唤醒组长）。
   - 理由（用户拍板）：异常可能是故障，组长本身也可能唤不醒；交给用户人工裁决更可靠。

3. **组长获取与发送**：
   - 组长只在**显式派遣**时被激活：用户点名、任务激活、review 兜底、用户手动操作；
   - 显式派遣时 `_leader_digest` 放宽摘要条数（静默期积压全部带出），并在消息中注明"静默期积压 N 条"，
     让组长一次获取完整上下文后统一发送指示。

4. **超时心跳兜底**（`_sm_heartbeat` / task_state_machine）：
   - 状态机 300s 超时基于"无回报"判定，静默模式下员工不回报是常态，**会误触发** auto_retry / escalate_l1；
   - token_save 工作间：跳过 auto_retry（不派发给组长重试）、跳过 escalate_l1（不唤醒组长），
     超时事件改为 system 提示反馈给用户。

## 三、改动点清单（全部在 agent_community/platform/server.py）

### 3.1 新增辅助

```python
def _is_silent_mode(ws_id: str) -> bool:
    """token_save 静默模式判定：读 workshop_modes.json，默认 standard。"""
    modes = _load_workshop_modes()
    return str(modes.get(ws_id, "")).strip().lower() == "token_save"
```

（放 `_load_workshop_modes` 附近）

### 3.2 `_apply_harness_reply`（约 4127 行）

在角色与状态规整后、`_append_msg` 之前计算：

```python
_silent = _is_silent_mode(ws.workshop_id)
_silent_member_progress = _silent and role_key == "member" and status_norm in ("done", "progress", "none", "")
_silent_member_abnormal = _silent and role_key == "member" and status_norm in ("blocked", "stuck", "failed")
```

- 普通回报（`_silent_member_progress`）：消息不带 system，`elif notify_leader:` 分支改为不推水位；
- 异常回报（`_silent_member_abnormal`）：meta["system"]=True（与 failed 占位一致），不推水位；
  - blocked/stuck 的 `_system_interject(..., level="L1")` 调用在静默模式下替换为
    `_append_msg(..., meta={"system": True, "abnormal": status_norm})` 的醒目提示（内容格式保持"成员X回报卡点/卡死…请用户裁决"），
    **不经过 interject_store / 不写 role=user**（避免触发组长事件）；
- failed 分支保持现有 system 占位逻辑（静默与非静默一致），静默下不再额外处理。

### 3.3 `_leader_has_event`（约 4698 行）

token_save 工作间 running 阶段：
- `wseq > ack` 分支自然失效（水位不推进）；
- 补一道保险：`if _is_silent_mode(ws.workshop_id):` 时，遍历 unseen 的 member 分支整体跳过
  （员工消息永不构成唤醒事件），仅保留 role=user（用户点名/手动操作）与 review 兜底。

### 3.4 `_leader_digest`（约 4680 行）

- token_save 工作间：`unseen` 截断从 `[-8:]` 放宽到 `[-40:]`（积压全量带出），
  返回结构增加 `silent_pending = len(unseen)`（积压条数）供 poll 拼提示。

### 3.5 `_leader_poll_loop`（约 4733 行）

- token_save 工作间消息模板追加一行：`「静默期积压 N 条员工回报，请一次性审阅」`；
- 其余逻辑不变（事件判定已由 `_leader_has_event` 收敛）。

### 3.6 `_sm_heartbeat`（约 5152 行）

- token_save 工作间：
  - `auto_retry` 分支：不再 `_dispatch_to_harness` 派发给组长，改为写 system 提示反馈用户
    （"工作循环超时 N 次，静默模式未自动重试，请用户裁决"）；
  - `escalate_l1` 分支：不调 `_system_interject`，改为带 system 标记的用户可见提示。

## 四、不做的事（边界）

- 不改变讨论区存储结构、不新增表/文件；复用 `meta.system` 与现有 system 过滤。
- 不改 `_orchestrated_flow` / discussion_engine（编排讨论室流程不在本次范围）。
- 不改前端（前端已能展示 system 消息；如需"静默模式"徽标另行排期）。
- 不改变 standard / parallel / strict 模式行为。

## 五、验收标准

1. token_save 工作间：员工 POST task-result（done/progress）→ 讨论区可见，`_leader_has_event` 返回 False，
   poll 日志无"事件唤醒组长"。
2. token_save 工作间：员工回报 blocked/stuck → 讨论区 system 提示可见，组长不被唤醒；
   用户点名组长（role=user 消息）→ 正常唤醒，digest 带出静默期积压并注明条数。
3. standard 工作间：行为与改动前完全一致（回归）。
4. py_compile 通过；重启服务后抓前端/接口实测。
*（内容由AI生成，仅供参考）*
