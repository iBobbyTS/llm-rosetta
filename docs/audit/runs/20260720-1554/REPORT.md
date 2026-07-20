# 第三轮遗漏修复复审报告

## 结论

本轮遗漏检查涉及的四个产品路径均已修复并分别提交；phase-separated re-audit 又发现并关闭了 Tavily 原始异常 cause 的残余泄漏。完整 lint 与非 integration 测试通过，没有执行真实 API 调用。当前没有新增事项需要项目所有者决定。

## 已修复的逻辑问题

- **AUD-012：redirect 策略边界。** Provider 请求默认拒绝 redirect；显式 `allow_redirects` 使用隔离的 client pool，并由模型发现继承。模型列表、Tavily、WebRun sidecar、Admin 自测等非 provider 辅助路径统一强制禁止 redirect。
- **AUD-014：Tavily credential 回显。** 按 Tavily 的 Bearer 请求认证边界，成功响应、HTTP 错误和 transport 异常中的已配置 key 都会递归脱敏；原始异常 cause 被移除，完整 traceback 也不会重新暴露 key。
- **AUD-006：live SSE 开发脚本门禁。** `test_roundtrip_live.py` 在读取 `.env` 前要求开发者显式批准，并纳入 `dev_scripts/*live*.py` 动态清单。
- **AUD-009：`api_type` 存在性。** 只有后端支持表中的精确字符串才算存在；未知、空值和非字符串都按 URL 推断并打印 WARNING。推断值只进入运行时和 Admin 响应，不写回 config。
- **AUD-008：审计账本。** 唯一 profile、findings、coverage、system map、README 和本轮证据已对齐代码 baseline `de9c96b`。

## 已冻结的业务语义

- **AUD-011：custom URL 与 redirect。** 任意 HTTP(S) custom URL 的直接出站和 API-key 交付风险继续只在本机/可信内网边界内接受。Redirect 默认关闭；用户在 provider 设置中显式勾选后允许该 provider 及其模型发现跟随 redirect。启用后 redirect target 可能收到 provider credential，这是明确的 operator opt-in 风险。
- **AUD-009：协议推断。** 后端不承认的 `api_type` 与缺失值完全等价；推断顺序保持 `responses -> chat -> anthropic -> google`，custom URL 默认 Responses。
- **AUD-013：不立项。** 缺少或禁用 provider 的 model group 继续静默跳过；当前规模下不增加新的校验和错误传播状态机。

## 验证与未验证

最终结果为 lint 通过、`3505 passed, 5 skipped`。真实 provider/Codex/Tavily 行为、浏览器与 LAN 部署、Docker、恢复、长期容量、DNS/proxy 对抗和公网安全均未验证，也不在本轮承诺内。
