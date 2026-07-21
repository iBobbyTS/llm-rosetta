# 定向修复复审报告

## 结论

本轮复审针对独立遗漏审计 `20260721-1148` 的三个 `Must Fix`：

1. `AUD-022`：累计参数现在对前导和尾随 JSON 空白统一规范化，再执行完成值和语义凭据检查。
2. `AUD-023`：Chat 工具片段按显式 wire `index` 建立稳定的 `index -> call_id` 映射；缺失、冲突和重映射均清空状态并 fail closed。
3. `AUD-024`：按照 owner 已记录的业务决定，`computer_call_output` 现在明确抛出受控的 `NotImplementedError`；不扩展通用 computer-control 范围。

## 提交

- `f30d167 fix(gateway): normalize streamed credential arguments`
- `6bd24b4 fix(gateway): map chat tool fragments by index`
- `04efc74 fix(responses): reject computer call outputs`

## 验证

| 检查 | 结果 |
| --- | --- |
| 受影响 focused suite | `248 passed` |
| 完整 deterministic suite | `3624 passed, 5 skipped, 11 warnings` |
| `make lint` | 通过 |
| 真实 provider/API/Codex 调用 | 未执行（profile 禁止） |
| 部署/公网承诺 | 未执行；本机/内网边界不变 |

## 复审判断

三个 finding 均在当前批准范围内闭合。AUD-019 和 AUD-021 的父 finding 同步关闭，
但关闭仅表示当前源码、测试和本机 deterministic 证据满足验收标准；不代表真实
provider 时序、外部 sink、可用性、数据恢复或公网安全承诺已经验证。

## 维护性判断

修复保持在现有 transport credential gate 与 Responses message dispatch 的所有权边界内，
只增加有界身份映射、空白归一化和窄范围拒绝分支；回归测试覆盖三个根因。没有引入
新的持久化层或通用 computer-control 抽象，后续仅在产品明确扩大 computer-output 范围时
重新设计并启动新的审计波次。
