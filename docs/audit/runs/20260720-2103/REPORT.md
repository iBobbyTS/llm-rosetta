# 第六轮独立遗漏审计报告

## 执行摘要

- 审计模式：`Periodic`
- 审计基线：`99218427824047a416030675c19c9ba4908925ac`（`origin/main@da6d108..9921842`）
- Audit Profile：`Approved`
- 本轮结论：发现阶段新增两个开放发现；随后 owner 授权逐项修复。
- `AUD-019 / Must Fix / Agent-Fixable / Closed`：共享边界现同时执行 exact-wire 与 parsed-JSON 语义检查，合法但非 canonical 的 JSON escape 无法再绕过 raw SSE/raw error/Tavily/sidecar gate。
- `AUD-020 / Decision Recorded / Closed`：owner 明确选择返回边界只使用当前 provider／当前辅助客户端的凭据；全局 configured-token inventory 仅用于 diagnostics。另一个 provider/client 的凭据可原样返回，这一跨域 reflection 风险在 local/LAN-only 边界内被明确接受。
- live-call gate 与所抽样 Anthropic/Google converter 路径均为 `No Action`；没有执行真实 API 调用。

## 发现

### AUD-019 — JSON 等价编码绕过 raw 凭据检测

当前 return gate 对 wire bytes 做子串检查。它预注册 token 本身及 `json.dumps(... ensure_ascii=False/True)` 产生的两种 escape，但 JSON 允许更多语义等价拼写。确定性探针证明：配置 token `secret` 时，raw SSE 中的 `{"delta":"\\u0073ecret"}` 被完整释放，下游 JSON 解码得到 `secret`。`a/b` 对应的 `a\\/b` 也同样未命中。

影响范围包括 primary raw SSE/raw error，以及 Tavily/web-run sidecar 中“先查 raw bytes、再解析并返回”的同类边界。该问题违反已批准的 configured-token no-leak 要求，并使 PROVIDER-01、SIDE-01、SCN-03、SCN-04、CTRL-03 失效。它不是 AUD-017 的协议改写根因，而是独立的表示规范化遗漏。

冻结的修复验收方向：在 JSON/SSE 语义边界比较解析后的字符串，同时保持安全 wire bytes 原样返回；覆盖 Unicode escape 大小写、逐字符 escape、非 BMP surrogate pair、solidus/backslash/quote、JSON key/value、任意 stream split、success/error/raw SSE/Tavily/sidecar，以及 invalid/non-JSON 的无泄漏受控行为。

### AUD-020 — 返回边界的凭据域仅限当前 provider

全局日志/metrics/persistence redactor 使用 `GatewayConfig.token_values`，但 `CredentialRedactingTransport` 只从当前 `ProviderInfo.credential_values` 构造 return redactor。确定性 probe 中，Provider A 的请求收到已配置 Provider B 的 key 时，parsed body 与 raw body 均原样返回。

owner 决策：

- 采用较窄语义：只保护当前 outbound provider/client 配置的 credentials，避免另一路由的短/常见 token 阻断当前响应。
- 全局 diagnostics redactor 继续使用完整 `GatewayConfig.token_values`；不扩展 return gate inventory。
- 明确接受跨 provider/跨 client 的 configured-secret reflection，限当前 local/LAN-only profile。

该决策已写入 profile、compatibility ledger，并由 cross-provider deterministic regression 固化，因此状态为 `Closed / Decision Recorded`。

## No Action 结果

| 范围 | 结论 | 证据边界 |
| --- | --- | --- |
| AGENT-01 / SCN-11 / CTRL-06 | No Action | 当前可达 integration、agentabi、relay、24 个 executable examples、live dev script 与 shell runner 均受 shared approval gate 约束；`rosetta-test-kilo.sh` 仅打印说明，不执行外部调用 |
| Anthropic / Google converter 抽样 | No Action | focused deterministic converter tests 通过；不等于完整 converter matrix 或 live provider 兼容性 |
| Admin model discovery | 本轮未发现 AUD-019 同类绕过 | 它在解析 JSON 后对 object 做 exact-token 检查；仍不代表真实 provider pagination/browser UX 已验证 |

## 验证结果

| 检查 | 结果 | 限制 |
| --- | --- | --- |
| corrected focused suite | `723 passed, 2 warnings` | fake/in-process only |
| `make lint` | 通过 | 静态检查 |
| `make test` | `3576 passed, 5 skipped, 11 warnings` | integration 被排除，无真实 API |
| 两个直接 deterministic probes | 均复现 | 证明现有测试存在 oracle/coverage omission |
| AUD-019 remediation focused suite | `105 passed` | fake/in-process only |
| AUD-019 remediation `make lint` / `make test` / compatibility | 通过；`3591 passed, 5 skipped, 11 warnings`；Codex contract 无阻断变化 | integration 被排除，无真实 API |
| AUD-020 decision-contract focused/full gates | focused `19 passed`; lint 通过；full `3592 passed, 5 skipped, 11 warnings`；Codex contract 无阻断变化 | 仅 deterministic 契约；integration 被排除，无真实 API |

首次 focused 命令误写了不存在的 `tests/gateway/test_web_search.py`，因此 collection 失败且未运行测试；随后使用实际路径 `test_web_search_bridge.py` 重跑并取得上述结果。

## 覆盖与剩余未知

- `SCN-04`：AUD-019 phase-separated deterministic re-audit 后恢复为 `Fresh`。
- `PROVIDER-01`、`SIDE-01`、`SCN-03`、`CTRL-03`：AUD-020 决策契约与 deterministic regression 完成后恢复为 `Fresh`。
- `AGENT-01`、`SCN-11`、`CTRL-06`：保持 `Fresh (deterministic)`。
- 真实 provider/Codex/Tavily/sidecar、网络 chunk timing、浏览器/LAN、部署、生产 telemetry、恢复与外部 GitHub 设置仍为 `Unknown` 或本轮排除项。
- 未执行完整 converter matrix、agentabi/live matrix、Docker/Compose smoke 或任何真实 API 调用。

## 维护性判断

问题集中在共享 return-security boundary 及其 credential inventory ownership。AUD-019 通过共享 `SecretRedactor` 语义入口修复，没有复制 provider-specific escape 规则；AUD-020 保持现有 active-client ownership，不引入全局 return-gate 耦合。
