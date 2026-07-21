# 第八轮独立遗漏审计报告

## 结论

本轮在冻结范围内确认两个 `Must Fix / Agent-Fixable`：

- `AUD-019` 重新打开。当前 credential semantic gate 只覆盖 Responses 的 `function_call/mcp_call.arguments`，但 converter 还会对 `custom_tool_call.input`、`shell_call.arguments` 和 `code_interpreter_call.arguments` 执行第二次 JSON 解析。转义后的当前 provider credential 可先通过 gate，再被 converter 还原为明文并输出到下游。
- 新增 `AUD-021`。Responses 类型契约使用 `computer_tool_call`，converter 却只识别 `computer_call`；前者被静默丢弃，后者生成 IR 不允许的 `computer_use` 并在校验时报错。

本轮没有修复产品代码，没有真实 API/provider/Codex 调用，没有部署或提交。

## AUD-019 复现

使用当前 provider credential `secret`，在三个当前 consumer 字段中放入 wire JSON `{"value":"\u0073ecret"}`：

| 字段 | semantic gate | converter consumer |
| --- | --- | --- |
| `custom_tool_call.input` | 放行 | 解析为 `{'value': 'secret'}` |
| `shell_call.arguments` | 放行 | 解析为 `{'value': 'secret'}` |
| `code_interpreter_call.arguments` | 放行 | 解析为 `{'value': 'secret'}` |

同格式 Responses round trip 最终输出 `input='{"value": "secret"}'`，因此这是确定性的当前 provider credential return 绕过，不是仅凭源码推断。

此前 `20260720-2255` 对 duplicate member、Responses/Chat function arguments、consumer identity、状态上限和安全内容 byte identity 的证据仍然成立；但“已覆盖全部当前 consumer schema”的关闭条件已经被反证。AUD-020 的 active-provider-only 决定未被挑战。

custom tool 的 stream delta/done 事件也不在当前 gate 注册表中，但本轮没有把它扩大为已确认的 stream 泄漏：stream consumer 对 freeform input 的处理与非流式第二次 JSON 解析不同，需在修复后单独复核。

## AUD-021 复现

权威 Responses 类型和类型测试都使用 `computer_tool_call`，而 converter 的 tool item 集合使用 `computer_call`。完整 converter probe 结果：

```text
computer_tool_call choices=[]
computer_call ValidationError ... got 'computer_use'
```

这意味着一个已声明支持的 Responses tool response 无法通过中心 IR：标准拼写发生静默协议丢失，内部备用拼写则确定性失败。

## 覆盖影响

本轮将以下 deterministic 覆盖标记为失效，等待修复与针对性复核：

- `PROVIDER-01`
- `TOOL-01`
- `SCN-03`
- `SCN-05`
- `CTRL-03`

`SCN-04` 仍保留此前对 Responses/Chat function argument 跨事件累计的确定性证据；custom-tool stream schema 只作为下一轮复核项，不在本轮作无证据扩张。

## 验证

| 检查 | 结果 |
| --- | --- |
| scoped focused suite | `249 passed` |
| `conda run -n llm-rosetta make lint` | 通过 |
| `conda run -n llm-rosetta make test` | `3604 passed, 5 skipped, 11 warnings` |
| `conda run -n llm-rosetta make check-codex-compat` | 通过；11 个语义项仍是 possibly unchanged |
| isolated adversarial/full-converter probes | 两个发现均复现 |

这些绿色检查说明现有行为没有触发已写入的断言，不能替代缺失的安全和 tool-contract oracle。

## 修复边界与验收条件

本轮未获修复授权。建议下一 remediation wave 保持两个根因分离：

1. `AUD-019`：从真实 Responses consumer parser/accumulator 清单机械地派生或校验 semantic gate schema，至少覆盖当前三个遗漏字段；增加 gate-to-converter round-trip 回归，并独立判断 custom-tool stream 的语义。
2. `AUD-021`：统一 canonical wire item 名、converter dispatch、IR `tool_type` 和各目标 converter 的降级/保真规则；同时覆盖标准拼写、未知拼写不得静默丢弃、IR validation 和 round trip。

## 剩余未知

真实 provider/Codex、外部 sink、网络分块与时序、浏览器/LAN、部署和生产 telemetry 均未验证。本报告只覆盖当前冻结的 credential semantic gate、Responses tool consumer、IR tool-type contract 和审计状态四个切片。

## 维护性判断

问题集中在 transport 的手工 schema 注册表与 converter/IR 各自维护的协议枚举之间，属于高风险语义边界的多源真相漂移。建议用可执行的一致性清单和回归测试约束现有模块边界；修复前不建议再增加新的独立工具类型分支。
