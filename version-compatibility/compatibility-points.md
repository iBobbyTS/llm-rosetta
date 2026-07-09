# Codex 强相关兼容点

## 判定标准

本文件只收录满足以下任一条件的行为：

- 依赖 Codex 特有的 header、request item、tool schema、SSE event 或 model metadata；
- 为保持 Codex agent loop、历史重放、工具执行或 UI phase 语义而存在；
- Codex 升级后即使 OpenAI Responses API 仍然“格式合法”，也可能发生行为回归。

普通的 provider 转换能力不单独列为 Codex 兼容点。

## 日常维护要求

本文件是 Codex 专用兼容点的唯一清单。日常开发中只要新增、修改或发现一个 Codex
专用行为，就必须在同一任务更新：

1. 当前实现、主要代码位置和升级风险；
2. **可自动化完成**的静态、fixture、组件或本地集成检查；
3. **必须实际测试**的真实 Codex/API 场景。

即使某项自动化尚未实现，也要写出应有的自动化检查并标记 backlog。即使某次升级
判断为高置信度没有变化，也不能删除其真实测试定义；真实测试是否在本次升级触发，
由升级分类决定。

## 当前兼容性总览

| 边界 | 当前实现 | 主要位置 | 升级风险 |
| --- | --- | --- | --- |
| Agent-facing API | 对 Codex 暴露 `/v1/responses`；Chat/Anthropic/Google 作为上游目标格式 | `gateway/app.py`, `gateway/proxy.py` | Codex 改 endpoint、transport 或 request shape |
| Responses 原样透传 | Responses→Responses 保留未知 body 字段、原始响应 JSON 和原始 SSE bytes | `gateway/proxy.py`, `test_responses_passthrough.py` | 低；仅显式 tool adaptation 会改 body |
| 请求与 window 身份 | 读取 `x-codex-window-id`，作为工具映射、deferred tools 和 phase 行为的 session key；不向上游透传 | `gateway/app.py`, `gateway/headers.py` | Codex 改为只发送 canonical `client_metadata` 或改变 window 语义 |
| Responses→Chat bridge | 将 Codex 的 Responses request 经 IR 转为 Chat，再重建 Responses output | `converters/openai_responses/**`, `gateway/proxy.py` | 高；新增 item/event/字段不会自动透传 |
| custom `apply_patch` | 识别 Responses `type: custom`，转换成模型可调用形式，并恢复 Codex-native tool call/output | `openai_responses/tool_ops.py`, `gateway/tool_adaptation.py` | Codex 改 custom grammar、call/output 字段或 delta event |
| 代码工具本地化 | 将 `apply_patch`/`exec_command`/`write_stdin` 等替换为模型熟悉的 `Read`/`Edit`/`Write`/`Glob`/`Grep`/`Bash`，响应再翻译回 Codex-native 调用 | `gateway/tool_adaptation.py`, `test_tool_adaptation.py` | 工具名称、参数 schema、call id 或执行结果格式变化 |
| 工具历史一致性 | 按 call id 记忆 native/localized 映射，重写后续历史，并可持久化和 TTL 清理 | `gateway/proxy.py`, persistence/observability modules | Codex 历史重放、compact 或 output shape 变化 |
| Deferred tool discovery | 按 Codex window 暂存 `namespace` tools，注入/处理 `tool_search`，把 `tool_search_call/output` 恢复为 native Responses items | `gateway/proxy.py::WindowToolSearchStore`, Responses converter | namespace/tool_search schema、execution 或 compact 行为变化 |
| Codex 工具使用提示 | 为 Chat 模型补充 `request_user_input`、`create_goal`、`update_goal` 等 Codex 工具的调用约束 | `converters/openai_chat/tool_ops.py`, related pipeline tests | schema、mode availability 或 Desktop/runtime 工具契约变化 |
| Web search bridge | 可把 Codex `web_search` 暴露给 Chat 模型，经 Tavily 执行后重建 `web_search_call` 事件并续轮 | `gateway/web_search.py`, `test_web_search_bridge.py` | native web-search item/event 或工具配置变化 |
| Stream lifecycle | 从 Chat chunks 重建 `response.created`、item added/delta/done、`response.completed` 等 Responses SSE | `openai_responses/converter.py`, `gateway/proxy.py` | Codex parser新增必需事件、顺序或终止条件 |
| Message phase | 用工具调用和 terminal event 推断 `commentary`/`final_answer`，把 phase 写回 message item；覆盖 native tool/web search 信号 | `gateway/stream_phase_buffer.py`, `test_stream_phase_buffer.py` | phase 枚举或 Codex mailbox/final-answer 语义变化 |
| Reasoning | 转换 reasoning effort/summary，保留 reasoning summary/content、`reasoning_content` 与 `encrypted_content` | Responses/Chat content、config、stream converters | 新 effort、summary delivery、reasoning event 或加密状态变化 |
| Context compaction resilience | 移除 compact 后没有 tools 却残留的 orphan `tool_choice/tool_config`；保持工具历史可重放 | `converters/base/helpers/tool_orphan_fix.py`, `test_strip_orphaned_tool_config.py` | Codex compact 输出、window generation 或历史裁剪变化 |
| 模型级开关 | 配置代码工具本地化、`apply_patch` fallback、移除 `image_generation`、tool description 优化和 phase detection | `gateway/config.py`, admin config/UI | Codex model catalog 控制字段或默认值变化 |

## 兼容点测试矩阵

| 兼容点 | 可自动化完成 | 必须实际测试 |
| --- | --- | --- |
| Agent-facing API | 路由、method、content type、SSE terminal/error fixture；fake upstream 单轮和多轮回放 | 真实 Codex 经 gateway 完成单轮/多轮，会话正常结束且错误可见 |
| Responses 原样透传 | 未知 request 字段、原始 JSON、原始 SSE bytes 和 terminal event 不被改写 | 真实 same-format Responses 路由保留实际 Codex request/response 字段且可续轮 |
| 请求与 window 身份 | header/body metadata 提取、fallback、并发 window 隔离和 cache key 测试 | 捕获真实 turn、compact、resume、fork、subagent 的 header/body/window 变化 |
| Responses→Chat bridge | request/response/stream/history 四方向 fixture；fake Chat upstream 多轮工具回放 | 用 `deepseek-v4-flash` 完成文本、多轮工具、错误恢复和最终回答 |
| custom `apply_patch` | tool schema、grammar hash、delta 拼接、call/output round-trip 和失败 fixture | 真实 Codex 执行成功 patch，再执行一次失败 patch 并修正续轮 |
| 代码工具本地化 | native/localized schema 映射、参数转换、call id、结果恢复和历史重放 | 真实执行 read/edit/write/search/shell，下一轮仍能正确消费工具历史 |
| 工具历史一致性 | TTL、持久化、失败结果、compact 后 history、并发 session 隔离 | 多轮工具后 compact/resume/restart，确认无重复调用或孤立 output |
| Deferred tool discovery | namespace defer、搜索匹配、多次搜索、call/output 和双向 window 隔离 | 真实 plugin/MCP namespace 搜索、调用、消费结果，并验证两个会话不串扰 |
| Codex 工具使用提示 | tool description/schema 注入和 mode availability fixture | 真实调用 `request_user_input`、Goal/Plan 和可用的 Desktop runtime tools |
| Web search bridge | 配置、禁用/缺 key、search result、事件重建和续轮 fixture | 真实搜索、读取结果并继续生成最终答案；验证错误路径可恢复 |
| Stream lifecycle | created、item/delta/done、completed/failed/incomplete 顺序和异常 EOF | 真实流式 turn 无重复/截断/卡死，terminal 与错误呈现正确 |
| Message phase | 所有工具信号、completed-only、added/done/completed phase 一致性 | Codex UI 中 commentary/final 展示正确，mailbox/steering 可工作 |
| Reasoning | effort/summary/content/encrypted state 跨格式 round-trip 和工具续轮 fixture | `deepseek-v4-flash` reasoning 在工具前后及下一轮可续传，无重复思考 |
| Context compaction resilience | orphan tool config、history trimming、compact fixture 和 window generation | 长会话触发 compact 后继续工具任务，并验证 resume/restart |
| 模型级开关 | 配置默认值、保存/加载、runtime 生效和 ModelInfo contract fixture | 用目标模型验证 tool mode、apply_patch/search/reasoning/multi-agent 开关行为 |

## 1. 请求、header 与 session 身份

当前 Codex 源码在 `codex-rs/core/src/responses_metadata.rs` 中明确：完整 turn
metadata 的 canonical 载体是 request body 的
`client_metadata["x-codex-turn-metadata"]`，HTTP `x-codex-*` headers 是兼容投影。

Rosetta 当前行为：

- `gateway/app.py::_proxy_handler` 从 HTTP header 读取 `x-codex-window-id`；
- window id 同时作为工具历史映射和 window-scoped `tool_search`/phase 状态的 key；
- `gateway/headers.py` 只向上游转发 `x-request-id`、`User-Agent` 和
  `OpenResponses-Version`；
- Responses→Responses 直接路径原样保留 body，因此 canonical `client_metadata`
  不会被 IR 丢失；
- Responses→Chat 路径不把 Codex metadata 发送给 Chat upstream，本地状态仍依赖
  HTTP `x-codex-window-id`。

升级审查必须同时捕获并比较：

```text
HTTP x-codex-window-id
HTTP x-codex-turn-metadata
client_metadata["x-codex-window-id"]
client_metadata["x-codex-turn-metadata"]
session-id / thread-id / turn-id / parent-thread-id / subagent metadata
```

当前源码中 window id 形如 `{thread_id}:{auto_compact_window_number}`。compact、
resume、fork 和 subagent 会影响它的生命周期；不能把它当作永不变化的 thread UUID。

## 2. Responses 请求与直接透传

当前 Codex `ResponsesApiRequest` 包含 `instructions`、`input`、`tools`、
`tool_choice`、`parallel_tool_calls`、`reasoning`、`store`、`stream`、
`stream_options`、`include`、`service_tier`、`prompt_cache_key`、`text` 和
`client_metadata`。

同格式路由使用直接透传是重要的前向兼容策略：未知字段不会先被压缩进 IR，响应
也不会被重新序列化。升级时必须保留这个边界，避免为了复用 converter 而把 direct
path 改成 decode/re-encode。

Responses→Chat 则是显式兼容层。Codex 新增 request item、tool type、reasoning
字段或 SSE event 后，必须确认 converter 有明确的降级/恢复策略，不能把“请求成功”
当作 agent loop 兼容。

## 3. Codex-native 工具与历史重放

当前 Codex 源码把 `apply_patch` 暴露为 Responses `type: "custom"` 的 freeform
grammar tool；调用使用 `custom_tool_call`，参数为字符串，结果使用
`custom_tool_call_output`。Rosetta 同时维护两层兼容：

1. Responses converter 把 native/custom 工具安全降级到 IR/Chat 可表达的形式，再按
   metadata 恢复 native Responses item；
2. 可选 tool localization 把 Codex 编辑工具替换为 Chat 模型更熟悉的工具，并把模型
   调用翻译回 `apply_patch`、`exec_command` 或受控 fallback。

因为 Codex 会在后续请求中重发历史，本项目按 `call_id` 保存 native/localized 映射，
在发给上游前恢复模型最初看到的工具调用。这是 prompt cache 与多轮工具一致性的关键
路径，必须与 compact、resume、失败工具结果和 TTL/persistence 一起测试。

`namespace` 和 `tool_search` 是另一条 Codex 专用路径。Rosetta 会隐藏不适合一次性
展开给 Chat 的 namespace，按 window 保存它们，注入合成 `tool_search`，并用后续
`tool_search_output` 恢复匹配工具。

当前 direct namespace whitelist 只包含 `codex_app` 和 `multi_agent_v1`。Codex 源码
已经存在 `multi_agent_v2`/`collaboration` 方向的工具规划；虽然通用 defer 路径可能
承接新 namespace，但目前没有专项端到端回归，不能据此声明兼容。

OpenAI Chat tool converter 还会给 `request_user_input`、`create_goal` 和 `update_goal`
添加模型可见的使用提示。`request_user_input` 可与开源源码核对；部分 Goal 工具来自
Desktop/runtime 的真实 payload，在相邻开源源码中没有同名定义，升级时必须保留真实
session/tool fixture，不能只做源码搜索。

## 4. SSE、phase 与终止语义

Codex 先通过 `response.output_item.added` 注册 item，再消费 text/tool delta，最后处理
item done 和 `response.completed`。Rosetta 的重建顺序必须至少保持：

```text
response.created
response.output_item.added
response.output_text.delta / tool input delta
response.output_item.done
response.completed
```

`phase` 位于 message item 内，而不是单独 event。`commentary` 不只是 UI 标签：当前
Codex 会在 commentary item 完成后检查 mailbox，并可能改变后续采样行为。因此 added、
done 和 completed output 中的 phase 必须一致。

当前 `ResponsesPhaseBuffer` 把 function/custom/MCP/shell/computer/tool_search/
web_search call 都作为工具信号。自动化回归覆盖“文本后接 native search tool”和
`response.completed.output` 中只有 native search call 的场景，确保之前的文本被标为
`commentary`，而不是误标 `final_answer`。新增 Codex output item 类型时仍需明确判断它
是否会继续 agent loop，并相应扩展这一集合和两种事件路径的测试。

## 5. Reasoning 状态

Codex 在 reasoning 开启时会请求 `reasoning.encrypted_content`，并消费 summary part、
summary text delta/done 和 raw reasoning delta。Rosetta 当前通过 IR metadata 保留
Responses summary/content/encrypted state，并在 Chat upstream 使用
`reasoning_content` 等 provider 扩展字段维持工具续轮。

升级时必须检查：

- reasoning effort 的新增值和降级规则；
- summary `auto/concise/detailed/none` 及 delivery 顺序；
- `include: ["reasoning.encrypted_content"]`；
- 空字符串 `reasoning_content` 与工具调用共存；
- reasoning item 在历史重放、compact 和跨格式转换后的可续轮性。

## 6. 当前明确限制和观察项

### Canonical metadata 只在 direct path 自然保留

Bridge 的 window-scoped 逻辑仍依赖兼容 header。如果未来 Codex 停止发送
`x-codex-window-id` header，只保留 body `client_metadata`，phase 与 deferred tool
状态将不再正确分窗。每次升级都要用真实请求捕获确认。

header 缺失时，工具映射 cache 会退化为 `model:{model}`。这会让同一 model 的多个
Codex 会话共享一个映射域，因此只能作为兼容 fallback，不能视为等价的 session
隔离。

### Gateway `/v1/models` 不是 Codex dynamic catalog

`gateway/app.py::handle_list_models` 返回的是 OpenAI SDK 风格
`{"object":"list","data":[...]}`。当前 Codex 源码的动态 catalog 请求是
`GET models?client_version=...`，响应为 `{"models":[ModelInfo...]}`，其中
`apply_patch_tool_type`、reasoning、parallel tools、context window、Responses Lite、
tool mode 和 multi-agent version 都会改变 Codex 发出的请求与工具。

因此当前 `/v1/models` 不能被视为 Codex catalog 实现。只有在证实 Codex 会从自定义
provider 使用该 endpoint 后，才应按 Codex `ModelInfo` contract 单独实现并测试，不能
把两种响应格式混成一个模糊 endpoint。

### 尚未实现 Responses WebSocket、Responses Lite 和 `/responses/compact`

当前 gateway 的 Codex surface 是 HTTP `/v1/responses` + SSE。源码中的
Responses WebSocket `response.create`、增量 `previous_response_id`、Responses Lite
的 `AdditionalTools` input 形式，以及远程 `/responses/compact` 都不是已验证能力。

Codex model/provider 配置不得在没有测试的情况下声明这些能力；每次升级必须确认
Codex 对自定义 provider 仍会使用 HTTP/SSE，或能可靠 fallback。

当前 Responses→Chat 还依赖 Codex 重发完整 input/history 的行为。若 Codex 开始默认
使用 WebSocket/HTTP 增量请求和 `previous_response_id`，Rosetta 没有对应的服务端
Responses 会话存储，bridge 会缺少历史。这一项必须通过真实请求捕获判断，不能仅从
request type 中存在 `previous_response_id` 推断已经启用。

### Code mode 和新版多 agent 工具缺少专项验证

通用 custom-tool converter 不等于 code-mode `exec/wait` 已兼容。当前没有覆盖
JavaScript/code-mode payload、nested tool call 或 wait 续轮的 Rosetta fixture；
`multi_agent_v2`/`collaboration` 也缺少完整 namespace discovery + call + output 回归。

这些能力在 Codex model catalog 或真实请求中出现时，必须先增加 fixture 和端到端
测试，再更新支持声明。

### Phase 的 native search 信号已纳入自动化回归

`tool_search_call`/`web_search_call` 已纳入 phase 工具信号集合，并覆盖流式 item event
和 completed-only fallback。这个修复只改变 phase 分类，不改变 search bridge 或
tool execution；真实 Codex UI/mailbox 行为仍属于必须实际测试的门禁。

### 现有真实集成基线不足以证明工具兼容

仓库现有 agentabi 覆盖以单轮算术为主，启动脚本也只负责打开 Codex。它们不能证明
`apply_patch`、Goal/Plan、request_user_input、plugin/tool_search、web search、phase、
compact/resume 或 subagent 正常。升级门禁必须运行升级清单中的多轮工具矩阵。
