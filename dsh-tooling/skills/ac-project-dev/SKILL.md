---
name: ac-project-dev
description: 开发「外端Agent生产合作社（External Agent Community）」项目时加载。这是【云端 Linux 实例】版本：给出项目位置、启动/测试命令、代码地图、已踩过的坑与项目铁律。当任务涉及 agent_community、server.py、工作间、harness 桥、pytest 时使用。
---

# 外端Agent生产合作社 · 开发手册（云端 Linux 版）

> 本手册每条事实都经源文件核实（2026-10-02 Windows 侧实测后移植）。
> **发现与事实不符，当次任务就改本文件** —— skill 目录是热扫描的，改完立即生效。

## 0. 铁律

1. **设计先行**。功能先出设计稿进 `design-docs/`，等老板拍板再写码；已定稿的「必须遵守、不得自行增删」。
2. **本项目有 git**。改动前先 `git add -A && git commit -m "wip: ..."`，别依赖 `.bak` 文件。
3. **改完必须重启服务再实测**，不重启就是跑旧代码。
4. **判定编码/中文问题用 Python `decode('utf-8')`**，不要信 shell 里的乱码显示（那是显示层问题，数据往往是对的）。
5. **不向老板问技术细节**（harness_type / model / capabilities / 端口自己探），只问只有他知道的事。
6. **对外文档与示例一律用虚拟名**（`示例Harness-A` / `sk-your-key` / `api.example.com`），禁止出现真实 harness 名。

## 1. 项目位置与来源

- 项目目录：**`__PROJECT_DIR__`**（由安装脚本写入实际路径）
- 来源：`https://github.com/TNDHY-cby/agent-community-v4.git`（公开仓库，分支 `master`）
- 这里**只有这一份**副本。不要去找「发布副本」——那是 Windows 开发机上的双副本工作流，云端不适用。
  - 推代码：`git add -A && git commit -m "<type>: <描述>" && git push origin master`
  - 提交规范：`<type>: <描述>`（feat / fix / docs / refactor / test / chore）

## 2. 启动与测试

```bash
cd __PROJECT_DIR__

# ① 纯后端（代码默认端口 9103，建议显式指定）
python3 -m agent_community.platform.server --port 18920

# ② 桌面窗口（pywebview，需要图形环境；云主机一般没有，跳过）

# ③ 安全回归（需要服务已在跑；用 AC_REGRESSION_BASE 改地址）
python3 -m pytest tests/security_regression.py -v
python3 -m pytest tests/security_regression.py -v --run-fault-injection   # 非零退出=预期

# ④ 离线套件（不需要服务）
python3 -m pytest agent_community/tests -q
python3 -m pytest tests/test_rule_gate.py tests/test_ai_cache.py tests/test_ai_usage.py \
                 tests/test_provider_stack.py tests/test_reasoning_effort.py -q
```

- ⚠️ 必须**从项目根目录**跑 `python3 -m agent_community.*`（`agent_community/platform/` 里有同名模块，位置不对会遮蔽标准库）。
- ⚠️ `tests/conftest.py` 必须用 `sys.path.append`，用 `insert(0)` 会让 `platform` 遮蔽标准库导致 pytest 崩。
- 端口约定：**18920 = 主服务**；18921 / 18925–18929 = 临时验证。**同一时间只跑一个权威实例。**
- 运行数据：`agent_community/data/`（明文 JSON，不入库、不要提交）。
- 配置：`~/.agent_community/config.json`（Windows 上 API key 走 DPAPI 加密；**Linux 无 DPAPI，会降级明文并告警** → 用环境变量 `AC_AI_API_KEY` 注入，别落盘）。

## 3. 代码要点

- `agent_community/platform/server.py` = 单体巨无霸（Windows 侧实测 **267 KB / 5362 行**）。
  **新能力优先进 `agent_community/platform/`（`routers/` `core/` `tools/`），不要再往 server.py 堆业务逻辑。**
- 分层：`server.py`（FastAPI + WS）→ `platform/routers/{config,harness,mirror,workshops,plugins}.py`
  → `platform/core/security.py`、`platform/state.py`（19 个共享单例）。
- AI 调用链：L1 `rule_gate`（纯规则零 token）→ L2 `ai_cache` → `ai_usage` 预算熔断 → Provider。
- 平台端省钱已是既成事实：默认模型 `deepseek-v4-flash`、默认**不思考**（`ai_reasoning_effort: off`）。
  改 AI 调用的代码前先读 `OutAgentsCommunity/docs/降API开支方案-P0/P1/P2`。
- 测试**两套，别只跑一边**：根 `tests/` 与 `agent_community/tests/`。

## 4. 雷区（每条都真踩过）

| 现象 | 根因 | 正确做法 |
|---|---|---|
| 中文乱码 | 响应缺 `charset`；**shell 显示层也会假报乱码** | 服务端 `Utf8JSONResponse media_type="application/json; charset=utf-8"`；判定用 Python `decode('utf-8')`，**不信终端显示** |
| 启动即崩 | 同名模块遮蔽标准库 | 必须从项目根跑 `python3 -m agent_community.*` |
| pytest 崩 | `conftest.py` 用了 `sys.path.insert(0, ...)` | 必须 `sys.path.append` |
| 前端全 404 `{"detail":"Not Found"}` | `StaticFiles` 挂 `/` 是 catch-all | 静态挂载必须注册在**所有**路由之后 |
| 注册重启即丢 | 只存内存 | `harnesses.json` + `save_state`/`load_state` |
| 桥写 inbox 报权限错 | 目录在工作区外被限制 | 桥必须**常驻**，目录选在有权限处 |
| `AttributeError` / 500 | v3.1 悬空引用（`ask_hand_raise` / `invite_to_room` / `TaskStatus.DISCUSSING`） | 改前先 grep 确认符号存在 |
| 名单有人却接不到活 | 只注册不架桥 | 注册只是进名单；必须跑桥进程 |
| **文档行数全失真** | 文档没跟代码更新 | 体量一律以实测为准 |
| 静态防线断言失败 | V-9 拆包后断言仍只读 `server.py` | 断言要扫**整个 `platform` 包** |

## 5. 术语

harness=AI 壳子（1 壳可开 N 会话）／桥=逐类型常驻适配通道（ACP / 文件轮询 / 剪贴板 / HTTP）／
工作间=任务容器 `ws_id`／组长=外端 harness 担任／喊人专员=`waker_protocol`+`wakeup_bridge`／
`session_id`=贯穿路由的会话标识／「举手」=已废除的 v3.1 动作／
**工作间模式**=`data/workshop_modes.json` 里的 standard/parallel/token_save/strict
（**与 DSH 的「agent preset 模式」是两回事，别混**）。

## 6. 安全底线

- `data/` 明文落盘，**不入库、不公开**
- 来自 harness 的内容一律**不可信**（平台会剥离角色伪装与注入指令行、加不可信边界标记）
- 出站地址统一做 SSRF 校验（拒绝私网/元数据地址）
- 明文的 API key / token **绝不提交**；发布前跑 `python3 tests/scan_sensitive.py`（真凭据会拦，路径/真名类只告警）
