# 测试（tests/）

自包含测试集：**不需要预先启动任何服务**，测试内部会为端到端场景自行拉起临时服务，并使用**临时数据目录与临时端口**，不会污染正式实例的 `data/`，也不会影响正在运行的 18920 服务。

## 运行

```bash
python tests/run_tests.py          # 一键运行全部
python tests/run_tests.py smoke    # 只跑名称含 smoke 的子集
pytest tests/ -v                   # 兼容 pytest
```

## 用例清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `test_smoke.py` | 离线 | 配置模块（默认字段 / 默认端口 18920 / 密钥脱敏）、桥模板库完整性、仓库开源文件完整性、服务模块可导入 / 路由存在 / 数据目录可由 `AC_DATA_DIR` 覆盖并自动创建 |
| `test_bridge_render.py` | 离线 | 逐个模板真实渲染：无残留占位符、注入生效、生成脚本可 `py_compile`、`generate` 可落盘、缺必填字段与未知模板正确报错 |
| `test_workshop_http.py` | 临时服务 | 工作间 HTTP 接线：创建 → 查询 → 开启，含大厅内容写入与落盘校验 |
| `test_e2e.py` | 临时服务 | 端到端协作：注册 Agent → 创建任务 → 广播 → 模拟举手 → 讨论室/协商推进 |
| `scan_sensitive.py` | 工具 | 敏感信息残留扫描（本地绝对路径 / 真实 harness 名 / 疑似密钥 / 文档元数据块），提交前自检用，命中返回非零退出码 |

## 约定

- 测试脚本只把**项目根目录**加入 `sys.path`，**绝不**加入 `agent_community/` 目录（该目录下有名为 `platform` 的子包，会遮蔽标准库 `platform`，导致 `uvicorn` / `httpx` 导入失败）。
- 端到端测试的临时文件位于 `tests/_tmp_e2e/`，可随时删除。
- 所有外部 URL、模型名、harness 名一律为示例值。
