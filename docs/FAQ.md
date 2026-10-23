# 常见问题与排障（FAQ）

> 这里放"装不上 / 卡住 / 看起来成功其实没通"的情况。**首屏只留一行链接，别占首页**。
> 每条都写清「现象 → 为什么 → 怎么办 → 怎么确认修好了」。

## 1. 装了十分钟没动静

- **现象**：`pip install -r requirements.txt` 一直转圈或报超时。
- **为什么**：依赖从 PyPI 拉，网络不好就会卡在某个包上。
- **怎么办**：换国内镜像重试一次，例如
  `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
- **怎么确认**：`python -c "import fastapi, uvicorn; print('ok')"` 打印 `ok`。

## 2. `pip install agent-community` 报 404

- **为什么**：**没有发包**。这是一个可直接运行的源码仓库。
- **怎么办**：按 README 的「30 秒跑通」走源码路径；别照那句 `pip install` 做。

## 3. 起服务时说端口被占

- **现象**：`Address already in use` / 启动后立刻退出。
- **为什么**：默认端口 18920 上已有别的进程（很可能是上一次没停干净的平台）。
- **怎么办**：`python -m agent_community.cli stop` 先停；或换个端口
  `AC_PORT=18921 python -m agent_community.cli start`（Windows：`$env:AC_PORT=18921`）。
- **怎么确认**：`python tools/watchdog.py --check` 打印"活着：version=…"。

## 4. 窗口打开了，但页面一直转圈

- **为什么**：窗口起来了但**后端没起成功**（缺依赖 / 端口被占 / 数据目录不可写）。
- **怎么办**：先看后端日志（`AC_DATA_DIR/logs/`）；用 `python -m agent_community.cli start --no-gui`
  在终端里起，报错会直接打印出来。
- **怎么确认**：浏览器打开 `http://127.0.0.1:18920/api/status`，能看到 `{"status":"running",...}`。

## 5. 任务建出来了，但一直"进行中"

- **为什么**：**还没有接入任何 agent**（harness）。平台不替 agent 干活。
- **怎么办**：在「对接中心」按引导接入一个（六种协议各一套话术）；
  不知道自己的 agent 怎么被叫醒，就按页面里的自测步骤先自己测一遍。
- **怎么确认**：接入后点「验一次」（回话探针，默认关闭，需 `AC_REPLY_PROBE_ENABLED=1`），
  或手工发一条消息看它有没有回。

## 6. 我改了自己的配置，别人怎么用一样的？

- **不改代码**：所有环境差异都从环境变量读（见 `.env.example`）。
- **判据**：换一个目录 + 换一个端口 + 换一个数据目录，不改一行代码能走通。

## 6b. 我明明填了 key / token，为什么还是连不上？

- **先体检一次，别靠猜**：
  ```bash
  python tools/credential_check.py
  ```
  它会扫 `.env`、`config.local.json`、`data/keys/`、桥脚本，然后**只报位置与指纹**（绝不打印明文）：
  - 「`.env:12（AC_AI_API_KEY）`只有 8 位，太短，多半是示例/临时值」
  - 「`bridges/…/start.ps1:3` 这里是**明文**，这份脚本外发就等于把凭据送出去」
  - exit 0 = 没发现问题；exit 1 = 有弱值或明文（按提示逐条改）
- **界面里同一个能力**：点「体检」（`POST /api/credentials/scan`）；
  `GET /api/status` 的 `credentials` 段会显示摘要（`weak` / `plaintext_in_bridge`）。
- **为什么它只给指纹**：体检**不搬走**你的明文，平台只记"这条曾经是什么"（sha256 前 12 位），
  明文永远留在你自己的文件里。
- **没体检过时**它不会假装"没问题"：`credentials.scanned` 会是 `false` 并让你去体检一次。

## 7. 报告问题

- Gitee Issues：<https://gitee.com/TNDHY-cby/external-agents-community/issues>
- 请带上：你的系统 / Python 版本、`--version`、以及出错那一段日志（含 `AC_DATA_DIR/logs/` 里对应文件）。
