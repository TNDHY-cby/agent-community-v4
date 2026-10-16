---
name: 缺陷报告
about: 跑不通 / 结果不对 / 行为与文档不符
title: "[缺陷] "
labels: bug
---

> 这是 **Gitee** 侧模板（`.gitee/ISSUE_TEMPLATE/`）；规则与反馈方式只有一处真相：见仓库根
> [`CONTRIBUTING.md`](../../CONTRIBUTING.md) 与 [`README.md`](../../README.md)。

## 你做了什么（照抄命令，别总结）

```bash
# 例如：
python -m agent_community.cli start --no-gui
```

## 你看到什么（**贴原文**）

```
（错误输出 / 结果正文 / 截图）
```

## 版本与日志尾部（**这两样最有用**）

- 版本号：`python -m agent_community.cli status` 或 `python -c "import agent_community;print(agent_community.__version__)"`
- 日志：`<数据目录>/logs/server_stdout.log` 的**尾部**（默认数据目录是 `agent_community/data/logs/`）

## 环境

- 系统 / Python 版本：
- 端口与数据目录（若是改过）：`AC_PORT` / `AC_DATA_DIR`

## 一句话：这是"文档承诺了没做到"，还是"我用错了"？

（说不清也没关系，如实写就行 —— 本项目最反对"看起来成功"的死状态。）
