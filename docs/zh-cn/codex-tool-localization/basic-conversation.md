# 基础对话

Codex 通过 OpenAI Responses API 接口与模型通信。许多第三方供应商只暴露 OpenAI Chat Completions 兼容的端点。Codex-Rosetta 根据路由不同，以两种方式填补这一差距：

- Responses 到 Responses 的路由直接透传。
- Responses 到 Chat 的路由通过 LLM-Rosetta 的 IR 进行转换，然后再转换回 Responses 事件供 Codex 使用。

目标是保留 Codex 运行时的语义，而不仅仅是让上游请求在语法上有效。

## Responses 透传

对于相同协议的 OpenAI Responses 路由，网关不会解码和重新编码请求体。它直接转发原始请求，并将上游的原始 SSE 字节流式传输回 Codex。

这一点很重要，因为 Codex 依赖的某些字段不属于最小跨供应商 IR 的一部分，包括：

- 消息输出项上的 `phase` 字段，Codex 用它来折叠工作流程输出。
- 推理项和加密的推理载荷。
- 原生的 Responses 工具项结构。
- 供应商特定的请求字段，如 `include`。

模型级别的工具适配仍然可以在透传之前运行。例如，如果某个模型配置了 `remove_image_generation`，网关可以从请求中移除 `image_generation`。

## Responses 转 Chat 转换

对于仅支持 Chat 的供应商，Codex-Rosetta 将传入的 Responses 请求转换为 IR，再转换为 Chat Completions 请求。转换后的 Chat 请求会在目标 API 允许的范围内，保留对话、工具、工具选择、推理配置和流配置。

当 Chat 响应返回时，Rosetta 将其转换回 Responses 兼容的输出，以便 Codex 继续驱动 agent 循环。

网关还会在请求和响应阶段之间保留选定的运行时状态：

- Responses 命名空间工具映射存储在转换上下文中。
- 供应商元数据（如推理或工具调用元数据）可以按工具调用 ID 缓存，并重新注入到后续请求中。
- User-Agent 和 OpenResponses-Version 头通过显式的头部允许列表转发。

## 流式形态

对于转换后的流式响应，Rosetta 从上游 Chat 块中重建 Responses SSE 事件。它发出 Codex 期望的相同大类事件：

- `response.output_item.added`
- 文本增量和文本完成事件
- 工具调用开始、参数增量和完成事件
- 推理事件（可用时）
- `response.completed`

转换器会在消息项的添加和完成事件上保留消息 `phase` 元数据，以便 Codex 在生成最终答案时折叠评论/工作输出。

## 推理

对于在供应商特定字段中输出推理的 Chat 供应商，当上游单独提供推理内容时，Rosetta 会将其与普通助手输出分开保存。DeepSeek 风格的 `reasoning_content` 会在工具循环中保留，以便后续请求能够满足那些要求回显推理内容的供应商。

如果模型在普通文本中输出推理，例如 `<think>...</think>`，Rosetta 会将其视为普通输出，因为供应商没有从语义上将其分离。

## 上下文处理

Codex 当前在重复请求中发送完整的对话上下文，而不是仅依赖 `previous_response_id`。因此，Rosetta 将传入的请求体视为当前轮次的真实来源。它不实现服务端的 Responses 对话存储。

这对 Chat 转换很重要：如果 Codex 重新发送历史助手工具调用，Rosetta 必须使这些历史调用与上游模型最初看到的内容保持一致。代码编辑本地化为此使用了持久化的映射缓存。
