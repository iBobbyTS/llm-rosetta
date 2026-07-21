# AUD-019 / AUD-021 修复与定向复审报告

## 结论

`AUD-019` 与 `AUD-021` 均已关闭，分类保持 `Must Fix / Agent-Fixable`。

本轮没有发现需要新增业务决策的事项。未产生真实 API 调用，未部署，
未提交。

## 修复结果

- Responses 内会二次解析 JSON 字符串的字段改由一个可执行清单统一拥有，
  覆盖 function、MCP、custom、shell 和 code-interpreter 调用。
- custom-tool 流式 input 的 delta/done 进入同一个有界语义累计器；完成事件
  在凭据可被重建前失败关闭。
- `computer_call` 统一为公开 Responses 线协议名称，IR 允许
  `computer_use`，并保留完整原生 item 以实现非流式同格式往返。
- Chat、Anthropic、Google 目标格式和通用流式转换明确报不支持，不再猜测
  为普通 function，也不再静默丢弃。
- 直接 Responses 透传不经过上述转换器，继续保留既有安全边界内的字节透明。

## 验证

| 检查 | 结果 |
| --- | --- |
| 修复前失败 oracle | `9 failed, 218 passed` |
| 修复后 focused suite | `326 passed` |
| `conda run -n llm-rosetta make lint` | 通过 |
| `conda run -n llm-rosetta make test` | `3621 passed, 5 skipped, 11 warnings` |
| `conda run -n llm-rosetta make check-codex-compat` | 通过，无 contract 变化 |
| `git diff --check` | 通过 |
| `codegraph sync` | 通过 |

第一次直接运行 `make lint` 因当前 shell 没有 `ruff`，在任何 lint 检查前
失败；进入项目规定的 `llm-rosetta` 环境后全部通过。

## 业务语义

没有新增决策：

- 仍只检查当前 provider/client 的凭据。
- 仍只承诺本机/内网部署，不增加公网安全承诺。
- 不增加通用 computer-control 跨 provider 映射。
- 不改变手动 release、无迁移层、无可用性或恢复保证等现有 profile。

## 剩余未知

真实 provider/Codex、网络时序、外部 parser、浏览器/LAN 部署和生产 sink
未验证。跨格式及转换流中的 computer-control 明确不支持；未来若要支持，
需要单独设计目标格式语义并重新审计。

## 维护性判断

安全字段清单、消息分派和回归契约现在共享同一协议所有权；computer 调用
没有被扩展成新的通用抽象，只增加了保真元数据和明确拒绝边界。复杂度棘轮、
类型检查和全量测试均通过，目前不建议额外重构。
