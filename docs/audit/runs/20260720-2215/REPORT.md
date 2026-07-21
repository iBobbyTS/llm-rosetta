# 第七轮独立遗漏审计报告

## 执行摘要

- 审计模式：`Periodic`
- 当前基线：`353a795a00fb42ecbe307653f12877900e831bf9`
- Audit Profile：`Approved`
- 结论：重开 `AUD-019 / Must Fix / Agent-Fixable`。此前修复只覆盖一次外层 JSON 解码；当前 provider 凭据仍可通过重复 JSON key、嵌套 JSON 字符串、跨 Responses SSE 事件拼接等受支持的下游消费语义被恢复。
- `AUD-020` 不重开：本轮所有探针均使用当前 provider 的凭据，符合已记录的 active-provider-only 边界。
- 未修改实现代码，未执行真实 API 调用。

## 需要用户敲定的业务/风险语义

无。当前 profile 已明确当前 provider/client 的凭据不能进入下游；本轮是同一控制根因的实现遗漏。

## 主要发现

### AUD-019 — 单层 JSON 检查未覆盖下游重组语义

- 分类：必须修复
- 状态：`Open / Reopened / Agent-Fixable`
- 影响：provider raw JSON、Responses raw SSE、function/tool arguments，以及依赖这些内容的 Codex/tool 消费路径。
- 触发路径：return gate 只执行一次 `json.loads`；普通 dict 解析丢弃较早重复 key，嵌套 arguments 需要第二次解析，多个 SSE delta 需要跨事件拼接。
- 确定性结果：三类 gate 均放行；对应 consumer 语义均恢复精确字符串 `secret`。
- 风险排序：当前 provider 凭据泄漏属于 profile 的 `Must Fix`；路径位于共享 return-security boundary，系统性和爆炸半径高，且现有完整测试无法发现。
- 建议方向：以实际受支持的 consumer parse/reconstruction contract 为边界检查，不要无界递归解析所有字符串；重复 key、已知 JSON-string 字段和跨事件增量需有明确状态模型与 fail-closed 行为。

## 审计范围与证据

- Always-on critical：SCN-03 provider Responses 路由、SCN-04 SSE、CTRL-03 凭据返回控制。
- Changed/high-churn：`73afaeb` 的 JSON-semantic gate 和 `353a795` 的 active-provider scope contract。
- Rotating slice：OpenAI Responses function arguments 与 delta accumulation。
- 独立性：新 subagent 仅获得仓库路径与 audit skill，未获得主线程本轮假设或复现细节；平台在其写报告阶段两次拦截，主线程随后独立复核并落盘。

| 检查 | 结果 | 限制 |
| --- | --- | --- |
| 相关 focused suite | `266 passed` | 缺少 consumer-semantic oracle |
| `make lint` | 通过 | 静态检查 |
| `make test` | `3592 passed, 5 skipped, 11 warnings` | integration 排除，无真实 API |
| 三个直接 probe | 均复现 | 本机确定性语义 |

## 覆盖新鲜度

- `PROVIDER-01`、`TOOL-01`、`SCN-03`、`SCN-04`、`SCN-05`、`CTRL-03`：`Invalidated`。
- `AUD-020` 及 active-provider-only credential domain：保持 `Closed / Decision Recorded`。
- 其他未受依赖边影响的覆盖保持原状态。

## 建议与冻结验收方向

1. 为 raw-preserving JSON 通道检测重复 key 中的 credential，不允许普通 dict collapse 擦除安全证据。
2. 对受支持且会再次解析的 JSON-string 字段建立明确 schema-aware 检查，包括 function/tool arguments。
3. 对 Responses argument delta 建立跨事件、跨任意 HTTP chunk 的 bounded accumulation/gating；发现碰撞前不得释放可组成风险值的事件。
4. 保持安全响应 byte-identical，并保留 AUD-017 的合法短/common token fail-closed 与协议完整性要求。
5. 新测试必须证明当前 provider 凭据被阻断、其他 provider 凭据仍按 AUD-020 放行、异常/无关字符串不被无界递归解释。

## 剩余未知

真实 provider/Codex/Tavily/sidecar、网络时序、浏览器/LAN、部署、生产 telemetry、外部 sink/GitHub 设置仍未验证。本轮不作完整系统安全或生产可用性声明。

## 维护性判断

这是重复出现在共享 return-security boundary 的同根问题。后续修复应升级 GP-003 的 consumer-semantic 测试矩阵和单一状态所有权，避免继续按单个 escape 或单个 route 打补丁。
