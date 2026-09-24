---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: fee6cb6926e215051ee1772520098e59_1fe34450b59711f193fb525400393706
    ReservedCode1: tdg2imkedXh0Is72I/JdkdyaYapkCQbF0E8ZDoDyKcv6nAIZ/eefQnOYqQXAiuVxEhVKpT6dhsHYZ8lZiUMmeV3mz6r0GHonFJT0fTTZVZPFEeAT8zedQaZal9r4uZT52zlNAlMhQz96kozmci/WG+ZzfOSklaINtM6UH/wuU7LEpz+gBoOCJhWVE7Q=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: fee6cb6926e215051ee1772520098e59_1fe34450b59711f193fb525400393706
    ReservedCode2: tdg2imkedXh0Is72I/JdkdyaYapkCQbF0E8ZDoDyKcv6nAIZ/eefQnOYqQXAiuVxEhVKpT6dhsHYZ8lZiUMmeV3mz6r0GHonFJT0fTTZVZPFEeAT8zedQaZal9r4uZT52zlNAlMhQz96kozmci/WG+ZzfOSklaINtM6UH/wuU7LEpz+gBoOCJhWVE7Q=
---

# 工作间模式 parallel（并行快进）落地设计

> 决策来源：用户明确「并行放第一位，并行分工合作是我设计这个平台的初衷」（手机确认）
> 决策点（用户拍板）：
> 1. 并行分工裁决 = **混合**：组长主裁 + 平台兜底（组长超时未分工，平台自动广播给空闲成员）
> 2. 并行度 = **限量并行**：同时最多 N 个成员并行，N 可由用户手动调节（默认 3，范围 1~全体）
> 日期：2026-09-21

## 一、现状（已核实，2026-09-21）

- `WSV_MODES` 定义于 `frontend/workshop.html`：standard / parallel / token_save / strict 四档
- `parallel` 目前**仅持久化标记**：`workshop_modes.json` 存值，调度链路零分支
- 唯一读取工作间模式的入口是 `server.py` 的 `_is_silent_mode(ws_id)`（仅认 `token_save`）
- 调度链路现状为**串行指挥**：
  - `_activate_by_hooks` 解析 `@唤` 多目标，但逐个 `_dispatch_to_harness` 派发
  - 员工回报经 `_apply_harness_reply` 推进 `_worker_last_seq` 事件水位 → `_leader_has_event` 判真 → `_leader_poll_loop`（300s 限频）唤醒组长逐条审阅
  - `_dispatch_to_harness` 单播，无并行度概念

## 二、parallel 模式目标语义

多成员并行分工、减少串行等待：
1. **批量并行派发**：组长一条消息可 `@唤:角色A @唤:角色B` 或 `@唤:全体`，平台一次性并行派发任务给多个目标成员，各自开工
2. **限量并行**：并行成员数上限 N（可配置，默认 3，`@唤:全体` 也受 N 限制，超出部分排队等下一批）
3. **回报积压不打断**：parallel 模式下，员工正常回报（done/progress）不逐条唤醒组长，按窗口批量汇总；异常（blocked/stuck/failed）仍即时升级
4. **组长主裁 + 平台兜底**：默认组长广播分工；组长超过 T 秒（默认 300s）未分工且有空闲成员时，平台自动把积压待办广播给空闲成员

## 三、改动点

### server.py

#### 3.1 模式判定扩展
```python
def _workshop_mode(ws_id: str) -> str:
    """读工作间模式：standard / parallel / token_save / strict，默认 standard。"""
    modes = _load_workshop_modes()
    return str(modes.get(ws_id, "")).strip().lower() or "standard"

def _is_parallel_mode(ws_id: str) -> bool:
    return _workshop_mode(ws_id) == "parallel"

def _parallel_limit(ws_id: str) -> int:
    """限量并行上限 N：默认 3，范围 1~成员数；用户可调（见 3.6 API）。"""
    # 存于 workshop_modes.json 的 {"<ws_id>": {"mode":"parallel","parallel_limit":N}} 或扁平 key
```
> 兼容现状：保持扁平 key 存储（`modes[ws_id] = "parallel"`），并行度 N 另存 `modes[f"{ws_id}:parallel_limit"] = N`，不破坏现有 `_is_silent_mode` 读取。

#### 3.2 批量并行派发（`_activate_by_hooks` 增强）
- 命中 parallel 模式时：
  - 解析全部目标成员（`@唤:全体` 展开 + 点名），按 `_parallel_limit` 取前 N 个并行派发
  - 其余目标进入**排队队列** `ws._parallel_queue`（新字段，列表），待批内成员完成后再补位
  - 非 parallel 模式保持原逐个派发逻辑不变
- 派发消息增加并行语义提示：「平台已并行派发任务，共 N 位成员同时推进；完成后请统一汇总回报」

#### 3.3 回报积压批量唤醒（`_apply_harness_reply` / `_leader_has_event`）
- parallel 模式 + 员工正常回报（done/progress/none）：
  - 不逐条推进事件水位唤醒组长，改记 `ws._parallel_reports`（列表：member_id, status, summary, seq）
  - 批内成员全部完成（或窗口超时，默认 60s）时，一次性唤醒组长，摘要带「并行批次汇总」
- 异常回报（blocked/stuck/failed）不受影响，仍即时 `_system_interject` 升级
- 组长/用户发言、review 态等既有唤醒路径不变

#### 3.4 平台兜底分工（`_leader_poll_loop` 内新增分支）
- parallel 模式 + 组长超时 T（默认 300s）未分工 + 存在空闲成员（entered/pending）且有积压待办：
  - 平台自动广播任务给空闲成员（受 N 限制），并给组长发「平台兜底分工」说明
- 幂等：兜底后置 `ws._auto_assign_at` 时间戳，T 内不重复

#### 3.5 前端模式 UI（`frontend/workshop.html`）
- `WSV_MODES` 中 parallel 描述更新为「并行快进·多成员并行分工」
- 当前模式为 parallel 时，模式面板显示并行度 N 调节控件（1~成员数滑块/数字框）
- 调用新 API 持久化 N

#### 3.6 并行度调节 API
```python
@app.get("/api/workshop/{ws_id}/parallel_limit")
@app.post("/api/workshop/{ws_id}/parallel_limit")   # body: {"limit": N}
```

## 四、验收标准

1. parallel 模式选「parallel」后，组长 `@唤:角色A @唤:角色B @唤:角色C @唤:角色D`（N=3）→ 前 3 个立即并行收到任务，第 4 个排队，批内完成后自动补位
2. parallel 模式下员工 done 回报不逐条唤醒组长；批次完成/超时后一次性汇总唤醒
3. 组长 300s 未分工 → 平台自动兜底广播给空闲成员（限 N）
4. 非 parallel 模式行为不变（回归：standard 逐个派发、token_save 静默）
5. 服务重启后 N 持久化生效

## 五、风险与边界

- `_leader_has_event` / `_worker_last_seq` 水位语义在 parallel 模式下被绕行，需保证切回 standard/token_save 时水位状态自洽（复用 token_save 的"不推进水位"经验）
- 排队补位需要批内完成事件驱动：在 `_apply_harness_reply` 完成分支里检查 `ws._parallel_queue`，批内全部完成即补位并触发批量唤醒
- 平台兜底广播只在「确有积压待办」时触发，避免空闲广播噪音
*（内容由AI生成，仅供参考）*
