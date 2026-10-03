# 外端Agent生产合作社（External Agent Community）— 工作指引

> 无论你用哪个 AI / 哪个模式进来，先读这一页。更详细的项目手册在 DSH 技能
> `ac-project-dev`（若你的环境支持技能，直接加载它；不支持就读
> `docs/` 与 `design-docs/平台设计总纲.md`）。

## 铁律（违反即为错）

1. **设计先行**。功能先出设计稿进 `design-docs/`，等老板拍板再写码；已定稿的设计「必须遵守、不得自行增删」。
2. **改任何文件前先留备份**：`*.bak` / `*.bak_<日期>` / `*.old_` / `*.before_*` / `temp\*.bak_<时间戳>`。**用这些后缀，`publish_acv4.ps1` 与 oss 的 `.gitignore` 才会自动排除它们。**
   （本副本自 2026-10-02 起**有了本地 git**，但它是本地快照仓库、**没有 remote**，不能当异地备份用——备份纪律照旧。）
3. **改完必须重启服务再实测**，否则跑的是旧代码。
4. **判定编码/中文问题用 Python `decode('utf-8')`，不要信 PowerShell 的显示结果**。PowerShell 把正常 UTF-8 显示成乱码是常态。
5. **不向老板问技术细节**（harness_type / model / capabilities / 端口自己探），只问只有他知道的事。
6. **对外文档与示例一律用虚拟名**（`示例Harness-A` / `sk-your-key` / `api.example.com`），禁止出现真实 harness 名。

## 副本地图（2026-10-02 实测核实）

| 副本 | 路径 | git | 说明 |
|---|---|---|---|
| **开发副本（这里）** | `D:\DSH工作区1\外端Agent生产合作社（External Agent Community）` | 有（本地，**无 remote**） | 实际开发在这里 |
| **发布副本（开源）** | `D:\外端Agent生产合作社（External Agent Community）-oss` | 有（`origin/master`，与 dev 已同步至 `2a25611`） | 只由 `publish_acv4.ps1` 单向推入 |
| ⚠️ 已退休副本 | `D:\外端Agent生产合作社（External Agent Community）.retired-20261002` | 无 | 2026-10-02 退休，架构停在 V-9 拆包之前。**P0/P1/P2 成果已由 V-10 回收入主线**，此副本仅存档 |
| 另一条线 | `D:\OutAgentsCommunity` | — | 独立项目线（**代码零回收价值**，dev 是超集；仅 2 份全盘唯一文档可捞），勿混 |

- ⚠️ `publish_acv4.ps1` **只同步 `agent_community` 一个子目录**；根文档与 `design-docs/` 不进发布副本，要单独处理。
- ⚠️ 发布脚本已带**反向分叉闸**（oss 独有代码文件 → 中止），防止 robocopy 静默摧毁 oss 上的独有成果。
- ⚠️ 开发副本**缺 `requirements.txt`**（oss 有），环境不可复现——接手请先补。

## 最快上手

```powershell
cd "D:\DSH工作区1\外端Agent生产合作社（External Agent Community）"
python -m agent_community.gui --port 18920            # 桌面窗口（日常开发）
python -m agent_community.platform.server --port 18920 # 纯后端（代码默认 9103）
python -m pytest tests/security_regression.py -v       # 安全回归（需服务在跑）
.\run_all_tests.bat                                    # 一键回归
```

必须**在项目根目录**（即 `agent_community` 包的父目录）跑 `python -m agent_community.*`，否则包解析失败。

`agent_community/platform/server.py` 是 **271 KB / 5438 行**，但**已不含任何路由**（只剩 `include_router` 与编排）。HTTP 端点全在 `platform/routers/`：

| 端点组 | 结构（V-13 拆分后，每个 ≤500 行） |
|---|---|
| `workshops*` | `workshops.py`(25 行聚合器) + `_lifecycle` / `_discuss` / `_assign` / `_task` / `_common` |
| `harness*` | `harness.py`(25 行聚合器) + `_register` / `_messaging` / `_bridge` / `_common` |
| 其余 | `config.py` / `mirror.py` / `plugins.py` / `audit.py` / `protocols.py` / `protocol_brief.py` |

**新端点加到对应端点组**；聚合器只做 `router.routes.extend`（别用 `include_router`，会让 `router.routes` 自省不到真实路径）。协议适配器（`mcp_server.py` / `a2a_server.py` / `grpc_gateway.py`）放**顶层包**——`platform/__init__.py` 会拽入 FastAPI 并污染 stdout。

### 关键设计稿

`design-docs/取长补短成果回收设计.md`（V-10，已实施）、`design-docs/多协议接入与前端落地设计.md`（V-11）、`design-docs/V13_Router拆分与状态注入设计.md`（V-13，已实施）。

> 本文件由 AI 维护。发现哪条与事实不符，**当次任务就改**。
