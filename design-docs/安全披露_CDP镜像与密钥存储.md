# 安全披露：CDP 镜像与密钥存储（2026-09-27）

## 1. CDP 代码级窗口映射（_mirror_by_cdp）

**功能**：`GET /api/harness/{harness_id}/mirror` 优先通过 CDP（Chrome DevTools Protocol）连接宿主浏览器/Electron 的调试端口，提取页面实时状态（title / url / body 文本流）。实现位于 `platform/server.py::_mirror_by_cdp`。

**披露事项**：
- 本功能会静默连接 9222-9225 端口探测浏览器调试服务，若命中则通过 WebSocket 读取页面内容。
- **默认关闭**：需显式设置环境变量 `AC_CDP_MIRROR_ENABLED=1` 才会启用 CDP 探测；未启用时 mirror 端点仅返回协议级映射（状态/消息流），响应含 `cdp_enabled: false` 与 `cdp_error: cdp_disabled` 说明。
- 启动日志会打印当前 CDP 开关状态。
- 建议仅在可信本机环境开启；开启前确认 9222-9225 端口无未授权调试服务。

## 2. API Key 落盘加密（DPAPI）

**变更**：`agent_community/config.py` 中 `ai_api_key` 落盘前加密。

**机制**：
- Windows 下使用 DPAPI（CryptProtectData / CryptUnprotectData，ctypes 调用，无需 pywin32），密文带 `dpapi:` 前缀，**绑定当前 Windows 用户**——配置文件被复制/备份到其他机器或换用户登录时密文不可解。
- 非 Windows（Linux/WSL）降级为明文保存并在保存时打印告警，建议改用环境变量 `AC_AI_API_KEY` 注入。
- 历史明文配置自动兼容：读取时透传旧明文，下次保存自动迁移为加密存储。
- 接口回显一律经 `mask_api_key` 脱敏（前4后4）。

**影响**：配置跨机器迁移时，需在新机器重新通过 `/api/config` 或环境变量设置 Key，旧 dpapi 密文在新机器不可复用。
