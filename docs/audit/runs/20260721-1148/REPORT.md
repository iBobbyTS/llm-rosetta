# 独立遗漏审计报告

## 结论

本轮由新的独立 subagent 进行只读审计，并由主线程复核源码和本地复现。发现
3 个新的 `Must Fix`：

1. `AUD-022`（可直接修复）：Responses 流式参数累计器只 `rstrip()`，合法
   JSON 的前导空白会使语义检查被跳过；Unicode 转义的 active-provider 凭据
   可以穿过 raw/parsed SSE 边界。
2. `AUD-023`（可直接修复）：Chat 并行工具流用到达顺序列表解释 wire
   `index`，非顺序到达时会把片段写入错误调用，导致凭据重建漏检。
3. `AUD-024`（业务语义待决定）：`computer_call_output` 不在 Responses
   tool-result 分派中，被静默丢弃；当前 IR/输出转换也没有该原生结果的映射。

因此，AUD-022/AUD-023 作为具体子 finding 重开 AUD-019，AUD-024 重开
AUD-021 的相邻 computer-result 契约；上一轮修复的 consumer inventory 和
`computer_call` 本体证据仍保留为历史 closure evidence。

本轮没有修改产品代码、没有提交、没有部署、没有发起真实 API/provider/Codex
调用。当前工作树中的既有未提交修改均保留。

## Findings

### AUD-022 — 前导空白绕过流式 JSON 语义凭据门

- **严重性**：Must Fix；**归属**：可直接修复；不需要业务决策。
- **证据**：`credential_semantics.py:134-141` 对累计文本调用 `rstrip()`，
  但随后要求同一个未 `lstrip()` 的文本 `startswith("{")`。因此
  `  {"value":"\\u0073ecret"}` 不进入 `contains_json_semantic()`。
- **复现**：向 `ProviderCredentialSemanticGate` 发送两个 Responses
  `response.function_call_arguments.delta`，片段为
  `  {"value":"\\u00` 与 `73ecret"}`；当前实现释放，未抛出
  `SecretCollisionError`。同一 redactor 对完整 JSON 的语义检查为真。
- **影响**：active-provider 凭据可在参数 JSON 被下游解析前从 raw/parsed SSE
  边界泄漏。
- **建议**：按 JSON 的空白规则统一规范化前缀/后缀后再做完成对象判定，并为
  raw SSE 与 parsed-event 两条路径增加前导空白反例；继续保持 fail closed。

### AUD-023 — Chat 并行工具按到达顺序关联 `index`

- **严重性**：Must Fix；**归属**：可直接修复；不需要业务决策。
- **证据**：`credential_semantics.py:247-257` 把带 `id` 的调用 append 到
  `_chat_tool_order`，随后把 wire `index` 当作该列表下标；列表没有保存
  `index -> call_id` 的稳定映射，也没有检测重映射/冲突。
- **复现**：先发送 `index=1,id=call-1` 的 `{"value":"\\u00`，再发送
  `index=0,id=call-0`，最后发送 `index=1` 的 `73ecret"}`。最后片段被写入
  `call-0`，`call-1` 永远不完整，门释放全部事件而不会重建出 `secret`。
- **影响**：恶意或故障 upstream 可利用并行工具到达顺序绕过跨事件凭据检查，
  同时也会破坏工具参数关联。
- **建议**：按显式 wire `index` 建立稳定映射；首次出现的 id/index 冲突、重映射
  或缺失身份应清空状态并 fail closed，而不是猜测。

### AUD-024 — `computer_call_output` 被静默丢弃

- **严重性**：Must Fix；**归属**：业务语义待决定。
- **证据**：`message_ops.py:332-335` 的 `_TOOL_RESULT_TYPES` 只有
  `function_call_output`、`custom_tool_call_output`、`mcp_call_output`；
  `message_ops.py:355-402` 对未知 item 没有拒绝分支。`tool_ops.py:783-813`
  的 IR 结果输出也固定生成 `function_call_output`。
- **复现**：`request_from_provider()` 输入一个 `computer_call` 及其
  `computer_call_output`（含截图输出）后，IR 只保留 assistant 的
  `computer_use` call，截图/结果 item 完全消失。
- **影响**：完整 computer-use 循环的截图/结果历史被破坏，可能导致上游拒绝、
  重复动作或回放错误；这是协议数据丢失，不是可接受的未知字段忽略。
- **需要你的决定**：
  - **推荐：显式拒绝** `computer_call_output`，与当前“只支持 Responses
    非流式 `computer_call`、不扩展通用 computer-control”边界一致；或
  - **完整支持**：为 IR、Responses request/response、截图内容和 streaming
    关联设计可往返的原生结果模型，并重新审计所有目标格式。

  无论选择哪条，当前的静默丢弃都应先改成可观测的 fail-closed 行为。

## Verification

| 检查 | 结果 |
| --- | --- |
| 相关 focused tests | `242 passed`（transport credential redaction + Responses converter/stream/tool tests） |
| AUD-022 直接 gate probe | 稳定复现，前导空白片段未阻断 |
| AUD-023 直接 gate probe | 稳定复现，非顺序 index 片段错配且未阻断 |
| AUD-024 converter probe | 稳定复现，`computer_call_output` 从 IR 消失 |
| `git diff --check` | 通过 |
| `codegraph sync` | 通过，索引已是最新 |
| 真实 API/provider/Codex 调用 | 未执行（按 profile 禁止） |
| 提交/部署 | 未执行 |

## Scope and residual unknowns

本轮聚焦 `gateway/transport/credential_semantics.py` 的 raw/parsed SSE
路径、Responses message/tool dispatch、IR computer-tool 边界及对应测试。未
重新深审 persistence、Admin、sidecar、release、浏览器/LAN 部署、真实 provider
时序或完整 fuzz；这些仍是 Unknown/既有轮次的证据状态。既有 AUD-020
active-provider-only 凭据域和本机/内网部署边界未被挑战。

## 维护性判断

AUD-022/AUD-023 是局部状态映射与解析条件修正，适合在现有 transport gate 内
补丁并增加回归测试；AUD-024 若选“完整支持”会扩大 IR/Responses/stream 复杂度，
若选“显式拒绝”则保持当前 ownership 边界，建议优先后者。
