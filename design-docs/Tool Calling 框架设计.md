# Tool Calling 框架设计 — agent-community-v4

> 设计日期: 2026-08-08
> 状态: 已完成
> 参考: D:\O泡知识库\AI_Rust视频笔记\Rust Agent 开发\（ReAct 知识库）

---

## 一、整体分层

```
┌─────────────────────────────────────────────────┐
│                 server.py                        │
│  _ai_direct_response()                           │
│    └─ 创建 ReActLoop → run() → 拿到最终答案       │
│    └─ 回调中 bcast_to_clients 推送工具过程         │
└─────────────────┬───────────────────────────────┘
                  │ 依赖
┌─────────────────▼───────────────────────────────┐
│              react_loop.py                       │
│  ReActLoop.run(system, user) → LoopResult        │
│    ┌─ 组装 messages（含历史 tool 往返）           │
│    ├─ 调 LLM（带 tools 参数）                     │
│    ├─ 有 tool_calls → execute → 追加 → 下一轮     │
│    ├─ 纯 text → 结束                              │
│    └─ ≥max_steps → 强制终止                       │
└────┬──────────────────────────────┬──────────────┘
     │ 调用 LLM                      │ 执行工具
┌────▼──────────┐          ┌────────▼──────────────┐
│ ai_provider   │          │   tool_registry.py     │
│ .chat_with_   │          │   registry + tools     │
│   tools()     │          │                        │
└───────────────┘          └────────┬───────────────┘
                                    │
                          ┌─────────▼──────────────┐
                          │      tools/             │
                          │  read_file   write_file │
                          │  shell_exec  web_fetch  │
                          └────────────────────────┘
```

---

## 二、核心抽象

### 1. ToolSchema

```python
@dataclass
class ToolSchema:
    name: str              # "read_file"
    description: str       # "读取指定路径的文件内容"
    parameters: dict       # JSON Schema，OpenAI function calling 格式
```

### 2. ToolResult

```python
@dataclass
class ToolResult:
    tool_name: str
    success: bool
    content: str           # 成功时返回内容，失败时返回错误信息
```

### 3. BaseTool（抽象基类）

```python
class BaseTool(ABC):
    """所有工具的基类。"""
    schema: ToolSchema     # 类属性，子类必须覆盖

    @abstractmethod
    async def execute(self, **params) -> ToolResult:
        """执行工具，接收 LLM 传入的参数，返回执行结果。"""
        ...
```

### 4. ToolRegistry

```python
class ToolRegistry:
    """工具注册中心，管理所有可用工具。"""

    def register(self, tool: BaseTool):
        """注册一个工具实例。"""

    def get_schema(self, name: str) -> ToolSchema | None:
        """按名查找工具 schema。"""

    def get_openai_schemas(self) -> list[dict]:
        """生成 OpenAI function calling 所需的 tools 数组。"""

    async def execute(self, name: str, params: dict) -> ToolResult:
        """按名执行工具。"""
```

### 5. ChatResponse（AIProvider 扩展）

```python
@dataclass
class ChatResponse:
    content: str | None         # 纯文本回复（结束）
    tool_calls: list | None     # [{id, name, arguments}]（需继续）
    # 二者互斥：一种是最终答案，一种是工具调用请求
```

`AIProvider` 新增抽象方法：

```python
async def chat_with_tools(
    self, messages: list[dict], tools: list[dict]
) -> ChatResponse:
    """
    发送带 tools 参数的对话请求。
    messages 可能包含多轮 tool 往返。
    """
```

### 6. ReActLoop

```python
@dataclass
class LoopStep:
    """单步记录。"""
    step: int
    thought: str             # LLM 思考内容（如有）
    tool_call: dict | None   # 工具调用
    tool_result: str | None  # 工具结果
    observation: str         # 本轮总结

@dataclass
class LoopResult:
    final_answer: str
    steps: list[LoopStep]
    step_count: int

class ReActLoop:
    """
    Thought → Action → Observation 循环引擎。
    """
    tool_registry: ToolRegistry
    provider: AIProvider
    max_steps: int = 10
    on_step: Callable | None = None   # 每步回调，用于前端推送

    async def run(self, system: str, user: str) -> LoopResult:
        """
        执行 ReAct 循环直到 LLM 返回纯文本或无 tool_calls。
        达到 max_steps 时强制终止。
        """
```

---

## 三、消息流示例（前端可见）

```
用户: "帮我统计 D:\Downloads 下有多少文件"

  TOOL_CALL  → shell_exec {"command": "Get-ChildItem D:\\Downloads | Measure-Object"}
  TOOL_RESULT → "Count: 152"

  TOOL_CALL  → shell_exec {"command": "...分类统计各扩展名..."}
  TOOL_RESULT → ".pdf: 30, .zip: 45, .exe: 20, ..."

  FINAL      → "D:\\Downloads 共有 152 个文件，其中 PDF 30 个、压缩包 45 个..."
```

前端通过 `MessageType.EVENT` + `payload.event` 区分：

| payload.event | 时机 |
|---------------|------|
| `tool_call` | 每轮 tool_calls 发出前 |
| `tool_result` | 每个工具执行完成后 |
| `result` | ReAct 循环结束，最终答案 |

---

## 四、首批 4 个工具

| 工具 | 能力 | 安全约束 |
|------|------|----------|
| `read_file` | 读取文本文件内容 | 限工作目录下，禁 .env/.ssh 等敏感路径 |
| `write_file` | 创建/覆盖文本文件 | 限工作目录下，自动创建目录 |
| `shell_exec` | 执行 PowerShell 命令 | 拦截 `rm -rf` / `format` / `del /f /s` 等破坏性命令 |
| `web_fetch` | 抓取网页正文 | 仅 GET，超时 15s |

### 工具 JSON Schema 设计

#### read_file

```json
{
  "name": "read_file",
  "description": "读取指定文件的内容。支持纯文本文件（.txt .md .py .json .yaml .html .css .js 等）。不支持二进制文件。",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "description": "文件的绝对路径"
      },
      "limit": {
        "type": "integer",
        "description": "最大读取行数，默认 500"
      }
    },
    "required": ["path"]
  }
}
```

#### write_file

```json
{
  "name": "write_file",
  "description": "创建或覆盖文本文件。会自动创建不存在的父目录。",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "description": "文件绝对路径"
      },
      "content": {
        "type": "string",
        "description": "要写入的文本内容"
      }
    },
    "required": ["path", "content"]
  }
}
```

#### shell_exec

```json
{
  "name": "shell_exec",
  "description": "在 Windows 上执行 PowerShell 命令。用于文件管理、系统查询、文本处理等。禁止执行破坏性命令（格式化、删除系统文件、修改注册表等）。",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "要执行的 PowerShell 命令"
      }
    },
    "required": ["command"]
  }
}
```

#### web_fetch

```json
{
  "name": "web_fetch",
  "description": "抓取指定网页的正文内容（纯文本/Markdown）。仅支持 GET 请求。",
  "parameters": {
    "type": "object",
    "properties": {
      "url": {
        "type": "string",
        "description": "要抓取的网页 URL（以 http:// 或 https:// 开头）"
      }
    },
    "required": ["url"]
  }
}
```

---

## 五、适配分析

### DeepSeek API 兼容性

`deepseek-chat` 完整支持 OpenAI function calling 协议：
- 请求体支持 `tools` 字段
- 响应支持 `tool_calls`（与 OpenAI 格式一致）
- 当前 `chat/completions` 端点无需变更 URL

### 现有代码改动面

| 文件 | 改动方式 | 破坏性 |
|------|---------|--------|
| `ai_provider.py` | 新增 `chat_with_tools()` 抽象方法 + OpenAI 实现，`chat()` 保留 | 无 |
| `tool_registry.py` | 新增文件 | 无 |
| `tools/` 目录 | 新增目录，4 个工具文件 | 无 |
| `react_loop.py` | 新增文件 | 无 |
| `server.py` | `_ai_direct_response` 内部改为创建 ReActLoop 并调用 run() | 仅该函数内部 |

### 不影响的功能

- `_orchestrated_flow`（多 Agent 协商路径）完全不动
- 举手/讨论室/投票/委托链路零改动
- 前端大厅/工作间消息渲染零破坏

---

## 六、实施步骤

1. **第一步**：新建 `tool_registry.py` + `tools/` 目录，实现 ToolSchema / BaseTool / ToolRegistry + 4 个工具
2. **第二步**：扩展 `ai_provider.py`，新增 `chat_with_tools()` 方法（OpenAI 实现）
3. **第三步**：新建 `react_loop.py`，实现 ReActLoop
4. **第四步**：修改 `server.py` 的 `_ai_direct_response`，接入 ReActLoop
5. **第五步（可选）**：前端 `index.html` 追加 tool_call / tool_result 事件渲染

---

## 七、文件结构

```
agent_community/platform/
├── tool_registry.py      # 新增: ToolSchema / BaseTool / ToolRegistry
├── tools/                # 新增目录
│   ├── __init__.py
│   ├── file_tools.py     # ReadFileTool / WriteFileTool
│   ├── shell_tool.py     # ShellExecTool
│   └── web_tool.py       # WebFetchTool
├── react_loop.py         # 新增: ReActLoop / LoopResult / ChatResponse
├── ai_provider.py        # 修改: 新增 chat_with_tools() 抽象方法 + OpenAI 实现
└── server.py             # 修改: _ai_direct_response 接 ReActLoop
```

---

## 八、关联资产

| 资产 | 路径 |
|------|------|
| 项目主目录 | `C:\Users\1\AppData\Roaming\Tencent\Marvis\User\oAN1i2c7nvyo0aiI5MP0j80nFLxI\workspace\conv_19fda2bf984_daff50666c61\output\agent-community-v4` |
| ReAct 理论参考 | `D:\O泡知识库\AI_Rust视频笔记\Rust Agent 开发\使用 Rust 开发 AI Agent - 07.1 ReAct.md` |
| ReAct 实现参考 | `D:\O泡知识库\AI_Rust视频笔记\Rust Agent 开发\使用 Rust 开发 AI Agent - 07.2 ReAct 实现.md` |
