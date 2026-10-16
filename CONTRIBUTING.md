# 贡献指南（CONTRIBUTING）

感谢参与 外端Agent生产合作社（External Agent Community）v4。本文说明本地开发、测试与提交规范。

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
- 本地绝对路径（如 `%USERPROFILE%`、`<私有目录>`）
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

## 6. 分支与 PR（**主仓库在 Gitee**）

> 2026-10-16 起，**Gitee 是唯一主仓库**：<https://gitee.com/TNDHY-cby/external-agents-community>
> （GitHub 那份**已停止更新**，仅作历史归档 —— 别再从那儿拉/推，也别在那儿开 issue。）
> 发行版与变更记录：<https://gitee.com/TNDHY-cby/external-agents-community/releases>

- 从 `master` 切出功能分支：`feat/xxx`、`fix/xxx`
- 一个 PR 聚焦一件事；描述里请**贴命令原文输出**（模板：`.gitee/PULL_REQUEST_TEMPLATE.md`）
- 涉及协议变更时，同步更新 `design-docs/` 下对应文档（规则 20：**对外表达一次全改**）
- 缺陷/建议用 Gitee 的 issue（模板在 `.gitee/ISSUE_TEMPLATE/`）：**带上版本号 + `server_stdout.log` 尾部**

**提交前请跑**（判据只有一处真相：**本机双平台真跑替代 CI**）：

```bash
python tools/check_portability.py       # 必须 fail=0
python tools/check_productization.py    # 必须 fail=0
python tools/gen_env_docs.py --check    # 环境变量文档不许漂移
python tools/dual_platform_check.py     # 双平台真跑；退出码 2＝只跑成一侧＝不算通过
```
