# FE 验收归档（示例Harness-F）

本目录存放前端交付的**可复核验收材料**：自动化脚本 + 实测截图 + 结果 JSON。
对应设计稿：`design-docs/FE-01_审计时间线与在线看板设计.md`、`design-docs/FE-02_交付回执台设计.md`。

## 内容

| 文件 | 用途 |
|---|---|
| `ops_e2e_check.js` | FE-01 ops.html 八条验收（解析自测/空态/筛选/三档视口/错误分支/恢复） |
| `ops_hb_check.js` | FE-01 心跳两阶段：在线卡片 → 等 65s → 失联卡片 |
| `ops_name_check.js` | FE-01 工单名映射与已删除降级 |
| `ops_unknown_detail_check.js` | 陌生 detail 三条样本降级确认（回 示例Harness-E 3 号发言） |
| `handover_e2e_check.js` | FE-02 回执台四条验收（400×2 逐条 / 200 入列 / 三档视口 / 展开原文） |
| `booths_e2e_check.js` | FE-03 隔间与桥验收（公式对拍 6 条 / 成员卡渲染 / 选择器与降级 / 三档视口） |
| `ops_验证/`、`handover_验证/`、`booths_验证/` | 上述脚本产出的截图与 `result.json` |

## 运行方式

需要 Node（带 `playwright-core`，通过 `NODE_PATH` 指向其所在 `node_modules`）与本机 Edge：

```powershell
$env:NODE_PATH = "<含 playwright-core 的 node_modules 路径>"
node <脚本名>.js
```

**输出目录可覆盖（83 号）**：所有 `*_check.js` 支持 `AC_VERIFY_OUT` —— 复核者设
`$env:AC_VERIFY_OUT = "temp\复核\<名字>"` 再跑，截图与 `result.json` 都落进该目录，
**不碰已提交的 `*_验证/` 证据**（默认路径不变，不设即旧行为）。

脚本默认地址各代不一（老脚本 `18920`、新脚本 `18930`）—— **一律显式设 `AC_FE_BASE` 指向隔离实例**
（规则 6：别拿在用的 18920 当测试台）；截图输出到 `temp/验收证据截图/<脚本名>/`（或 `AC_VERIFY_OUT` 指定目录）。
截图中的时间戳、列表条数为当时实测快照；`result.json` 为同批运行的机器可读结论。

## 纪律

- 这里的断言是**产出当时**的证据，重跑可能因数据/时间变化而不同（如相对时间、列表计数）。
- 判定中文/编码问题以 Python/浏览器为准，不看 PowerShell 显示。
- **输出重定向**：PowerShell 的 `>` 会写成 **UTF-16**（示例Harness-E 33 号踩过）——解析请用脚本自写的 `result.json`（各脚本均已内置），或用 `Set-Content -Encoding UTF8` / `Out-File -Encoding utf8` 重定向。

---

## ⚖️ 发布口径（示例Harness-E 2026-10-07 代老板落档，**改了就要遵守**）

老板 2026-10-07 裁定：**截图不入库（还没打磨）**；脚本与 `result.json` 可以入库。
另按项目铁律 6「对外文档与示例一律用虚拟名，**禁止出现真实 harness 名**」，
本目录会被 `publish_acv4.ps1` 镜像进开源仓，故：

| 东西 | 去哪 | 为什么 |
|---|---|---|
| **`*.png` 截图** | `<仓库根>/temp/验收证据截图/<同名子目录>/` | 根 `temp/` 既被 `.gitignore` 忽略、又被发布脚本 `$excludeTop` 排除 ⇒ **永不进开源仓** |
| `*.js` 脚本 | 本目录（入库） | 可复跑、可复核 |
| `result.json` | 本目录（入库） | 机器可读结论；**必须脱敏**：真实本机绝对路径 → 虚拟路径；真实 harness 名 → `示例Harness-A/B/…` |

⚠️ **脚本的截图输出目录要改到上面那个 path**：否则每次复跑都会把 PNG 又写回本目录
（发布路径里），就得再搬一次——2026-10-07 已经这样反复过一次。

### 复跑需要的东西（此前没写清，补上）

```powershell
$env:NODE_PATH = "<含 playwright-core 的 node_modules>"
node <脚本名>.js
```

**怎么找到本机的 `playwright-core`**（一条命令自查；本机坐标按铁律 6 **不写进本文件**，
真实坐标留档在协作通道 `92-2322-*-NODE_PATH与撞号.md`）：

```powershell
$c = npm config get cache
Get-ChildItem "$c\_npx" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  if (Test-Path (Join-Path $_.FullName 'node_modules\playwright-core')) { Join-Path $_.FullName 'node_modules' }
}
```

（原理：`npx` 跑过的任何包会把依赖留在 `<npm缓存>\_npx\<hash>\node_modules`；
也可以用你自己项目的 `node_modules`，只要含 `playwright-core` 即可。）

> 示例Harness-E 2026-10-11 已按此真复跑三件（`fe18` / `nextstep_flow` / `ui_links`）全部 exit=0 —— 见通道 96 号。

