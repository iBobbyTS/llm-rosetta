# AUD-019 修复与复核报告

## 结论

`AUD-019 / Must Fix / Agent-Fixable` 已关闭。

同一个发现遗漏的 subagent 按冻结条件完成实现；主线程随后独立发现并要求返修了两个状态归属问题：安全 duplicate key 不应无条件失败，以及 Responses/Chat 的累计 key 必须与真实 converter consumer 一致。返修后通过 focused、lint 和完整 deterministic 验证。

未产生真实 API 调用，未部署，未提交。

## 修复内容

- 外层 JSON 解析保留全部重复 member，因此较早的 credential 不会被后续同名字段掩盖。
- 只对实际会再次解析的 Responses/Chat function/tool argument 字段执行嵌套 JSON 检查；普通文本和未知字符串不递归解析。
- Responses 使用真实 consumer 的 `call_id` 优先规则，并维护有界 `item_id -> call_id` 映射。
- Chat 使用真实 consumer 的 tool-call 注册顺序，将 id-only 起始 chunk 与 index-only 后续 chunk 归入同一状态。
- argument 状态限制为 1 MiB、4096 fragments 和 4096 identities，超限沿用 credential-collision fail-closed。
- 保留完整 SSE event gating、安全内容 byte-identical、短/common credential 的 AUD-017 语义，以及 AUD-020 的当前 provider/client 凭据范围。
- 首个 SSE frame 的 UTF-8 BOM 处理与项目 SSE parser 对齐。

## 验证

| 检查 | 结果 |
| --- | --- |
| 主线程 focused suite | `322 passed` |
| `conda run -n llm-rosetta make lint` | 通过 |
| `conda run -n llm-rosetta make test` | `3604 passed, 5 skipped, 11 warnings` |
| `git diff --check` | 通过 |
| `codegraph sync` | 通过 |

第一次直接运行 `make lint` 因当前 shell 没有 `ruff` 而在检查前失败；使用项目规定的 `llm-rosetta` 环境重跑后全部通过。

## 业务语义

不需要新增用户决定：

- 仍只检查当前 provider/client 的凭据。
- 不引入全局 provider credential return gate。
- 不改变本机/内网部署边界。
- 对超出新安全状态上限的异常大/高碎片 arguments 执行 fail-closed；当前 profile 不承诺此类 payload 的可用性。

## 剩余未知

真实 provider/Codex、网络时序、sidecar/Tavily、浏览器/LAN、部署、生产 telemetry 和外部 sink 仍未验证。本结论只关闭确定性代码路径上的 AUD-019，不扩大生产或完整系统安全声明。

## 维护性判断

协议知识和流状态集中在 transport credential boundary 的独立模块中；通用 redactor 只负责保留 duplicate members，没有引入任意递归解析。状态所有权、生命周期和资源上限明确，目前不建议额外重构。
