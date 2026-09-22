---
name: Bug 报告
description: 提交你遇到的 Bug，帮助我们定位与修复
title: "[Bug] 简要描述问题"
labels: ["bug"]
assignees: []
body:
  - type: markdown
    attributes:
      value: |
        感谢提交 Bug 报告！请尽量填写以下信息，帮助我们快速定位问题。
  - type: input
    id: environment
    attributes:
      label: 环境 / 版本
      description: 操作系统、Python 版本、项目版本（pip show 或 git commit）
      placeholder: "例如：Windows 11 / Python 3.11 / v0.1.0"
    validations:
      required: true
  - type: textarea
    id: steps
    attributes:
      label: 复现步骤
      description: 请按步骤描述如何复现该问题
      placeholder: |
        1. 执行 ...
        2. 点击 ...
        3. 观察到 ...
      value: |
        1.
        2.
        3.
    validations:
      required: true
  - type: textarea
    id: expected
    attributes:
      label: 期望行为
      description: 你期望发生什么？
    validations:
      required: true
  - type: textarea
    id: actual
    attributes:
      label: 实际行为
      description: 实际发生了什么？如有报错请贴出关键信息
    validations:
      required: true
  - type: textarea
    id: logs
    attributes:
      label: 日志 / 截图
      description: 粘贴相关日志或拖入截图，方便定位问题
      render: shell
  - type: checkboxes
    id: checks
    attributes:
      label: 自查清单
      options:
        - label: 我已搜索过已有 Issue，未发现相同问题
          required: true
        - label: 我提供的信息足以复现该问题
          required: true
*（内容由AI生成，仅供参考）*
