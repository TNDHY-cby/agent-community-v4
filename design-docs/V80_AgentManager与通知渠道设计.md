# V80 · Agent Manager（一键接入安装器）+ 通知渠道 · 设计稿

> 2026-10-22。老板拍板：「4 一键安装器也落地」「IM 渠道**如不需申请与对象软件**则可以落地」「移动端暂时不做」。
> 本文两块：**A. Agent Manager**（检测 / 版本 / 安装 / 升级）；**B. 通知渠道**（只做不需要第三方账号申请的）。

## A. Agent Manager（安全边界先定死）

### A.1 判据（老板口头要求 + 我们的反私人定制铁律）

1. **平台不替你下载来路不明的二进制**：只执行**我们自己清单里登记过**的命令模板；
2. **必须显式同意**：`POST …/install` 必须带 `{"confirm": true}`，且服务端开关 `AC_AGENT_INSTALL_ALLOW=1` 才允许执行；
3. **可干跑**：`{"dry_run": true}` 只回"将要执行什么命令"，**不执行**；
4. **不猜你的环境**：检测走 `platform/procprobe.py`（标准库）/`sysprobe.py`，不写死路径、不认某个人的机器；
5. **失败要说话**：装失败要把**退出码 + 输出尾部**原样回给用户，并给一条"手工装"的命令。

### A.2 清单（`platform/agent_catalog.py`，数据不是代码）

每条：`id / name / kind(cli|desktop|api) / detect(bin) / version_args / install_cmd(列表) / docs_url / note`。
首批只登记**通用 CLI agent**（例：任意 npm 全局包、任意 pip 包），**不绑定厂商**：
安装命令由用户环境决定 ⇒ 清单允许"**用户自定义条目**"（`AC_AGENT_CATALOG_EXTRA` 指向 JSON）。

### A.3 端点（4 个）

```
GET  /api/agents/catalog                  清单（含每条"能不能装"：可以/需要开关/没有安装命令）
POST /api/agents/{id}/detect              → {installed, version, path, why}
POST /api/agents/{id}/install             ← {confirm:true, dry_run?:bool}  → {executed, cmd, exit_code, tail}
POST /api/agents/{id}/upgrade             同上（升级=同一套命令模板，带 --upgrade 语义由清单决定）
```

**执行**：`subprocess.run(cmd, timeout=AC_AGENT_INSTALL_TIMEOUT 默认 600)`，只取 `stdout/stderr` 尾部 2000 字，
不流式、不后台、不留僵尸。**不是 shell**（`shell=False`，命令是列表）。

### A.4 判据 `test_agent_manager.py`（6 格）

① 清单可读且每条字段齐；② 开关没开时 install → 403 + 说怎么开；③ 没 `confirm` → 400；
④ `dry_run` → `executed:false` 且返回命令；⑤ 检出/检出失败都要有人话（假 bin 名 → `installed:false` + why）；
⑥ 自定义清单（`AC_AGENT_CATALOG_EXTRA`）能加载且非法 JSON → 报错不静默。

## B. 通知渠道（只做"不需要申请第三方账号"的）

### B.1 选型理由（老板口径）

飞书/钉钉/企微/Telegram 都要**申请应用或 Bot**（要账号、要审核）⇒ **本版不做**。
只做两种"用户自己就有、不需要申请"的：

| 渠道 | 用户要给的 | 适合 |
|---|---|---|
| **Webhook（出站 POST）** | 一个 URL（+可选 header） | 自建 n8n/自建服务/任何能收 HTTP 的地方 |
| **SMTP 邮件** | 自己的 SMTP host/端口/账号（或本机 relay） | 最通用，谁都有邮箱 |

> 未来要接飞书/钉钉/企微：**它们本质也是 webhook**（自定义机器人 URL）⇒ 用户填 URL 即可用，
> 不需要我们做"应用申请"那套。

### B.2 端点（3 个）

```
GET  /api/notify/channels                  → 配了哪些（**不返回凭据明文**，只说 configured）
POST /api/notify/channels                  ← {webhook_url, headers?, smtp:{host,port,user,password,to,from}}（写 .env 语义的配置项）
POST /api/notify/test                      ← {channel:"webhook"|"smtp"} → {ok, why, tail}
```

- 凭据**只从环境变量读**（`AC_WEBHOOK_URL` / `AC_SMTP_*`），端点只做"测一下通不通"，**不落明文**；
- 触发点（后续接）：任务完成、预算超限、审批待办、巡逻员告警 —— 本版只做"通道 + 测试"，
  触发点接入下一版（避免一次改太多处）。

### B.3 判据 `test_notify_channels.py`（4 格）

① 没配时说清"没配"（不是 500）；② webhook 测试用本地临时 HTTP 服务真发一次并收到；③ SMTP 没配 → 明确报"缺哪几项"；
④ 端点返回里**不出现**凭据明文（扫一遍响应文本）。

## 不做

- 不做移动端（老板：暂时不做）；
- 不做"自动下载安装包/校验签名"（超出本版边界，且容易变成私人定制）；
- 不做 IM 应用申请流程。
