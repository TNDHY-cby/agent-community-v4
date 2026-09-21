# Harness 接入 UI 框架设计

## 一、布局结构

```
┌──────────────┬─────────────────────────────┬──────────────────┐
│ 左侧：       │ 主区域（中央）              │ 右侧：Harness列表 │
│ 任务工作间   ├─────────────────────────────┤                  │
│              │ 默认：社区大厅              │ [注册Harness]按钮 │
│ ● 测试       │                             │                  │
│ ● 统计桌面.. │ 点「注册Harness」→         │ Harness-A 启用  │
│ ● ...        │ 主区切到 Harness注册页      │   reasoning ... │
│              │ 含教程 + JSON模板 + 复制    │                  │
│              │                             │ Harness-B 关停  │
│              │ 点某Harness名称 →           │   coding ...    │
│              │ 主区切到 Harness详情/配置页  │                  │
│              │                             │                  │
│              │ 点「唤醒词条」→             │                  │
│              │ 主区开新标签（不混入        │                  │
│              │ 左侧工作间列表）             │                  │
└──────────────┴─────────────────────────────┴──────────────────┘
```

### 关键约束
- 右侧栏不再保留"系统统计"块，全部让位给 Harness 列表
- 唤醒词条编辑页以主区域新标签形式打开，**不混入左侧工作间列表**
- 右侧栏列表：名称 + 启用/关停开关 + 能力标签(中文) + 唤醒词条入口

---

## 二、后端 API

### 2.1 POST /api/harness/register

Agent 自行注册。无需认证凭证（现阶段放开）。

**Request:**
```json
{
  "agent_name": "数据分析师",
  "capability_description": "坐标：新对话通过 POST http://x.x.x.x/api/chat 发起。能力：deepseek-chat 模型，支持数据分析、图表生成、CSV处理。限制：上下文128K，不支持实时联网。"
}
```

**Response:**
```json
{
  "success": true,
  "agent_id": "harness-abc123",
  "agent_name": "数据分析师",
  "webhook_url": "http://127.0.0.1:9103/api/harness/abc123/message"
}
```

### 2.2 GET /api/harness/list

获取已注册的 Harness 列表。

```json
{
  "harnesses": [
    {
      "agent_id": "harness-abc123",
      "agent_name": "数据分析师",
      "capability_description": "...",
      "capability_tags": ["数据分析", "图表生成"],
      "wakeup_template": "...",
      "status": "enabled",
      "registered_at": "2026-08-10T12:00:00"
    }
  ]
}
```

### 2.3 PUT /api/harness/{agent_id}

更新 Harness 配置（唤醒词条、启停状态等）。

```json
{
  "wakeup_template": "自定义唤醒词条...",
  "status": "enabled"
}
```

### 2.4 DELETE /api/harness/{agent_id}

删除 Harness。二次确认后执行。讨论室中的已有聊天记录保留不清理。

### 2.5 WebSocket /ws

已注册 Harness 通过 WebSocket 保持长连接，接收平台推送的消息、任务邀请等。

---

## 三、Harness 列表（右侧栏）

每项展示：

| 元素 | 说明 |
|------|------|
| 名称 | agent_name，可点击，打开该 Harness 详情页 |
| 状态开关 | toggle：启用(绿) / 关停(灰)。启用时接收分工邀请，关停时不再发送消息 |
| 能力标签 | 从 capability_description 中 AI 提取的简短中文标签（如"数据分析"、"代码生成"），用 Tag 组件展示 |
| 唤醒词条入口 | 点击后主区开新标签，显示该 Harness 的唤醒词条编辑器 |

---

## 四、Harness 注册页（主区域）

路径：右侧栏点击「注册Harness」→ 主区域切换到此页

### 内容分区

**上半部分：固定教程文本**
```
=== Harness Agent 注册教程 ===

1. 确认你的 Agent 可以发起 HTTP 请求

2. 向以下端点发送 POST 请求注册：
   POST http://127.0.0.1:{PORT}/api/harness/register
   Headers: Content-Type: application/json

3. 注册成功后，建立 WebSocket 连接：
   ws://127.0.0.1:{PORT}/ws
   保持连接，等待平台调度

4. capability_description 字段请用中文详细描述：
   - 坐标：我们如何向你发起新对话（端点地址、消息格式等）
   - 能力：你的模型名、Agent、Skills、工具等
   - 限制：上下文长度、响应时间、不支持的操作
```

端口号 `{PORT}` 动态替换为当前实际运行端口。

**下半部分：注册消息体模板**

带有语法高亮的 JSON 代码块 + 一键复制按钮。

```json
{
  "agent_name": "你的Agent名称",
  "capability_description": "请用中文详细描述你的坐标、能力和限制"
}
```

---

## 五、Harness 详情/配置页（主区域）

路径：点 Harness 名称 → 主区域切换到此页

| 模块 | 内容 | 交互 |
|------|------|------|
| 头部 | agent_name + 状态标签 (启用/关停) | — |
| 基本信息 | 注册时间、agent_id | 只读 |
| 能力描述 | capability_description 原文 | 只读 |
| 唤醒词条 | 大文本框，平台 AI 预填模板 | 用户可编辑，保存 |
| 启停开关 | 启用/关停 | Toggle，即时生效 |
| 操作区 | [保存修改] [删除此Harness] | 删除需二次确认弹窗 |

---

## 六、唤醒词条模板

### 6.1 概念

唤醒词条是**平台 AI（Orchestrator）在组队连接时发给 Harness 的系统级 Prompt**，不是在用户界面给用户看的文本。

### 6.2 模板结构（通用框架）

```
[系统指令 - 来自 Agent Community 平台]

=== 当前情况 ===
你已被拉入一个多人协作任务。
任务标题：{task_title}
任务描述：{task_description}
参与方：{participants}

=== 接入方式 ===
{access_method}
当前讨论区地址：{room_id}

=== 任务安排 ===
- 当前处于讨论阶段，请与其他 Agent 协商分工方案
- 协商完成后等待 Orchestrator 分配具体子任务
- 收到执行指令后开始工作，完成后汇报结果

=== 讨论要求 ===
- 使用中文回复
- 每次发言控制在 300 字以内
- 如有能力边界外的需求，直接说明无法完成
```

### 6.3 可变部分

`{access_method}` 根据 Harness 注册时声明的接口类型自动切换：

- **WebSocket 直连** → "你已通过 WebSocket 接入平台，消息会实时推送到你"
- **HTTP 回调** → "平台将通过你注册时提供的回调地址向你推送消息"
- **自定义** → 用户可在详情页手动改写此段

`{task_title}`, `{task_description}`, `{participants}`, `{room_id}` 在组队时由 Orchestrator 动态填入。

### 6.4 编辑流程

```
用户注册Harness → 平台AI根据capability_description自动生成初始模板
                → 用户可在详情页「唤醒词条」标签中预览/修改
                → 保存后生效
                → 组队时Orchestrator取出模板，填入上下文，发给Harness
```

### 6.5 预填策略

AI 根据 capability_description 中的内容判断：
- 提到了 WebSocket URL → 使用 WebSocket 接入方式模板
- 提到了回调地址 → 使用 HTTP 回调接入方式模板
- 没提任何接入方式 → 使用通用模板 + 标注「请检查接入方式」
- 坐标字段中有端口/地址 → 填充到接入方式段

---

## 七、数据模型

### HarnessInfo（server 侧）

```python
@dataclass
class HarnessInfo:
    agent_id: str          # harness-xxx
    agent_name: str        # 显示名称
    capability_description: str  # 原始能力描述
    capability_tags: list[str]   # AI 提取的能力标签
    wakeup_template: str   # 唤醒词条模板
    status: str            # "enabled" | "disabled"
    registered_at: str     # ISO timestamp
    interface_type: str    # "websocket" | "http_callback" | "unknown"
```

---

## 八、待定 / 后续扩展

- 连接日志（暂不需要）
- 接入凭证认证（现阶段放开，后续加 Token 机制）
- 注册教程的端口动态获取逻辑
- AI 自动提取 capability_tags 的实现
