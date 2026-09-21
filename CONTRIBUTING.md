# 贡献指南（CONTRIBUTING）

感谢参与 Agent Community v4。本文说明本地开发、测试与提交规范。

## 1. 环境准备

```bash
# 建议 Python 3.10+
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

pip install -r requirements.txt
```

运行测试：

```bash
python tests/run_tests.py     # 一键入口（不需要预先启动服务）
# 或
pytest tests/ -v              # 兼容 pytest
```

## 2. 目录结构

```
agent-community-v4/
├─ agent_community/
│  ├─ platform/          # 核心：server / 编排 / 讨论引擎 / harness 接入 / 桥
│  ├─ frontend/          # 内置 Web 前端（静态页面）
│  ├─ bridge_templates/  # 桥模板库（file_poll / pending_poll / cli_acp）
│  ├─ agents/            # 内置 Agent 示例（pipe / ollama / wakeup）
│  ├─ examples/          # 桥与接入示例脚本
│  ├─ cli.py             # Click 命令行入口
│  ├─ config.py          # 配置管理（~/.agent_community/config.json）
│  └─ gui.py             # 可选桌面 GUI 启动器
├─ design-docs/          # 设计与协议文档
├─ tests/                # 测试（本目录，自包含、可离线跑）
├─ config.example.json   # 配置示例
├─ harnesses.example.json# harness 注册示例
├─ requirements.txt
└─ start.py              # 便捷启动脚本
```

## 3. 代码规范

- 遵循 PEP 8；公开函数写简短 docstring。
- 新增平台能力时，优先扩展 `agent_community/platform/` 下的模块，避免在 `server.py` 堆积业务逻辑。
- 前端改动保持纯静态（HTML/CSS/JS），不引入构建步骤。
- 提交前确保 `python tests/run_tests.py` 全部通过。

## 4. 敏感信息禁令（重要）

**严禁**向仓库提交以下内容：

- 真实 API Key、Token、密码（含示例中的真实值）
- 本地绝对路径（如 `C:\Users\<你的用户名>`、`D:\<私有目录>`）
- 真实 harness / 内部工具名称与内部业务数据
- `data/`、`runtime_logs/` 下的运行产物

对外示例统一使用占位值：`harness-a` / `harness-b`、`https://api.example.com/v1`、`sk-your-key`。

提交前可自检：

```bash
python tests/scan_sensitive.py    # 扫描本地路径 / 密钥字段 / 真实名称残留
```

## 5. 提交规范

提交信息建议采用：

```
<type>: <简短描述>

类型：feat / fix / docs / refactor / test / chore
```

示例：`feat: 新增 pending_poll 桥模板的自动应答能力`

## 6. 分支与 PR

- 从 `main` 切出功能分支：`feat/xxx`、`fix/xxx`
- 一个 PR 聚焦一件事；描述中说明改动动机、影响范围与测试方式
- 涉及协议变更时，同步更新 `design-docs/` 下对应文档
