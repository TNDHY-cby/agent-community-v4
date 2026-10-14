# CLAUDE.md — 给 Claude / 其它 agent 的入口（**精简指针**）

> ⚠️ **本文件不重复规则**（规则 20：一件事只有一处真相）。
> 完整规则看 [`AGENTS.md`](./AGENTS.md) 与 **[`PORTABILITY.md`](./PORTABILITY.md)**。

## 三条最容易踩的（先记住这三条再做别的）

1. **可移植性硬规则**：不许硬编码端口/IP/域名/绝对路径/密钥/API 地址；
   环境差异一律从环境变量读（登记在 `agent_community/platform/env_spec.py`），
   端口与平台地址只经 `agent_community/platform_address.py` 解析。
   **判据：当前电脑能跑不算合格；陌生环境只改配置就能跑才算合格。**
2. **完成后跑三条自查**（CI 与发布前同款）：
   ```bash
   python tools/check_portability.py      # 必须 fail=0
   python tools/gen_env_docs.py --check   # 环境文档不许漂移
   python -m pytest -q
   ```
3. **改了对外表达就整体同步更新**：后端字段 / 界面话术 / 文档 / 模板 / 验收脚本 / 示例**一次全改**
   （改一处而别处还写旧的 = 不合格）。

## 派任务时的标准话术（复制给 agent）

> 实现 X。**按 `PORTABILITY.md` 的可移植性规则**。先列出涉及的环境变量和默认值，再写代码。
> **禁止针对我的电脑。** 验收：在陌生 Linux 容器里只改环境变量即可运行。

## 本项目的定位（一句话）

外端Agent生产合作社 = 把多个异构 agent 组织到一个工作间里协作的**本地自部署平台**；
平台不替 agent 干活，只做「接入、激活、交接、留痕、兜底」。
新会话先读 `AGENTS.md`（铁律与目录地图）→ `PORTABILITY.md`（可移植性）→ 需要时读 `design-docs/V*.md`。
