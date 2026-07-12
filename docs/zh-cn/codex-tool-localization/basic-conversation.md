# 基础对话

Codex 通过 OpenAI Responses API 接口与模型通信。许多第三方供应商只暴露 OpenAI Chat Completions 兼容的端点。Codex-Rosetta 根据路由不同，以不同方式填补这一差距：

- 配置为 **OpenAI Responses (Tool Mapping only)** 的 Responses 到 Responses 供应商先应用所选 Tool Profile，再直接透传 Responses 内容。
- 配置为 **OpenAI Responses (Rosetta)** 的 Responses 到 Responses 供应商通过 Codex-Rosetta 的 IR 解码，再编码回 Responses 格式。
- Responses 到 Chat 的路由通过 Codex-Rosetta 的 IR 进行转换，然后再转换回 Responses 事件供 Codex 使用。

目标是保留 Codex 运行时的语义，而不仅仅是让上游请求在语法上有效。

## 两个 Responses 选项不是真正的新协议

**OpenAI Responses (Tool Mapping only)** 和 **OpenAI Responses (Rosetta)** 使用相同的 OpenAI Responses 线上协议和端点形态。管理界面将其拆成两个选项，只是因为网关内部采用不同的处理方式：

- **Tool Mapping only** 应用所选 Tool Profile，但不让完整 Responses 请求经过 IR；响应 JSON 和流式 SSE 字节仍直接转发。适合 OpenAI 官方或能保持 OpenAI Responses 行为的 GPT 中转站。
- **Rosetta** 让请求和响应经过 Responses → IR → Responses 处理链。适合其他支持 Responses 协议、但需要 Rosetta 归一化处理的模型提供商，如千问。

配置中的 `api_type` 分别为 `responses_passthrough` 和 `responses_rosetta`。二者都会解析到内部的 `openai_responses` provider type；它们不会增加新的公开网关端点或 API 标准。

目前只保证 Tool Mapping only 使用的直接传输路径和 Responses 到 Chat 的转换。Responses (Rosetta)、Anthropic 转换和 Google 转换暂不作保证；本次处理模式分流不会扩展 Responses 字段或事件的解包能力。

## Responses 仅工具映射

对于配置为 **Tool Mapping only** 的同协议 OpenAI Responses 路由，网关不会通过 IR 解码和重新编码完整请求体。它先应用所选 Tool Profile，再转发处理后的请求，并将上游原始 SSE 字节流式传输回 Codex。传输层只有一个例外：经过认证且带有 `Content-Encoding: zstd` 的请求会先在配置的解压前、解压后大小限制内解码，并移除编码 header。

这一点很重要，因为 Codex 依赖的某些字段不属于最小跨供应商 IR 的一部分，包括：

- 消息输出项上的 `phase` 字段，Codex 用它来折叠工作流程输出。
- 推理项和加密的推理载荷。
- 原生的 Responses 工具项结构。
- 供应商特定的请求字段，如 `include`。

此模式可以选择 Tool Profile。内置的 **Responses pass through** Profile 保持原生工具不变，并禁用仅由 Rosetta 注入的工具。内置的 **Responses web.run mapping** Profile 仅把 `web.run` 设为 Modified：Codex 仍暴露并调用 `tools.web__run`，但 `/v1/alpha/search` 由 Rosetta 可可靠实现的 Tavily/Python 子集处理。其他 Responses 字段和上游响应字节仍走直接路径。

Codex 的独立 Search 和 Images 客户端还会使用三个 JSON 端点：

- `POST /v1/alpha/search`
- `POST /v1/images/generations`
- `POST /v1/images/edits`

Images 端点按照所选 Profile 中 `image_gen.imagegen` 的状态处理：

- **Passthrough**：只有请求模型解析到 **OpenAI Responses (Tool Mapping
  only)** 供应商时才透传。
- **Modified**：把生成和编辑请求发送到 Function 卡片中配置的 OpenAI
  Images API Base URL，并使用 Token 作为 Bearer 凭据。Tool Mapping only、
  Responses Rosetta、Chat、Anthropic 和 Google 模型组均可使用此路径。
- **Disabled**：拒绝 Images 端点请求。

请求中的模型必须能够解析到选择该 Profile 的模型组。网关会应用配置的上游模型
别名，其余 OpenAI Images 请求和 JSON 响应不经过 IR 转换。Modified 当前仅支持
OpenAI `images/generations` 和 `images/edits` 线协议；Rosetta 不转换供应商私有的
生图 API。

独立 Search 还提供本地 bridge。当所选 Profile 把 `web.run` 设为 Modified
时，`/v1/alpha/search` 会在本地执行可靠子集：`search_query` 使用该 Profile
中 `web.run` 卡片下配置的 Provider 和 Token；目前 Provider 只支持 Tavily。
直接 URL 的 `open` 获取公开静态 HTML 或纯文本，`time` 使用 Python 的固定
UTC offset 计算。Open 会逐跳校验重定向目标，拒绝凭据和非公开地址，最多
允许五次重定向，并限制为 15 秒和 2 MiB；返回规范化、带行号的正文并支持
`lineno`。`turn0search0` 等已保存引用、JavaScript 渲染、压缩页面和非文本
内容仍不支持。支持的搜索选项包括查询/domain 过滤、搜索上下文大小、响应
长度和保守输出预算。请求中只要包含 `click`、`find`、`image_query`、
`screenshot`、finance、weather、sports、recency、blocked-domain、location
或非 live 访问语义，就会在任何部分操作执行前返回 HTTP `501` 和
`code: "not_implemented"`。这些辅助端点的所有 `501` 文案还会以
`Consider "Browser Use" skill` 结尾，提示 Codex 改用浏览器回退。使用
**Responses pass through** Profile 时，即使配置了 Tavily Token，`/alpha/search`
也会继续原生透传给上游。使用 **Responses web.run mapping** 时，受支持命令
走本地 executor；搜索查询需要在 `web.run` 卡片配置 Tavily Token，直接 URL
的 `open` 和只有 `time` 的请求使用 Python。

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
