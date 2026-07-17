# Codex 模型目录字段参考

本文说明 Codex 消费的模型目录，以及 Codex-Rosetta 在暴露第三方模型时应如何使用这些字段。内容基于 Codex CLI `0.144.4`、源码提交
`8c68d4c87dc54d38861f5114e920c3de2efa5876`，主要参考：

- `codex-rs/models-manager/models.json`；
- `codex-rs/protocol/src/openai_models.rs`；
- `codex-rs/models-manager/src/model_info.rs`；
- `codex-rs/core`、`codex-rs/tools` 和 `codex-rs/ext` 下的字段消费者。

这些是 Codex 内部契约，不是 OpenAI 公共 API 定义的字段。每次升级 Codex 时，都应重新执行源码契约兼容性检查。

文件顶层结构是 `{"models":[ModelInfo,...]}`。`models` 是唯一的 catalog 顶层键；下列表格说明每个受支持的 `ModelInfo` 输入字段及其嵌套值。

## 范围和字段状态

打包目录目前包含八个条目：`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`、`gpt-5.5`、`gpt-5.4`、`gpt-5.4-mini`、`gpt-5.2` 和 `codex-auto-review`。本文特意不包含本地自定义的目录条目。

只有网关未配置任何模型时，本地模式才会写入全部八个打包条目。只要配置了至少一个
模型，生成的目录就只包含已配置的模型名称。已配置名称与八个打包 slug 之一相同时，
会在解析后的 JSON 值层面原样复用该条目。

### 压缩哈希 overlay

Rosetta 保留上游 catalog 资产原文，只在 materialization 时应用运行时
`comp_hash` overlay。`gpt-5.6-sol`/`terra`/`luna` 保留已审查的上游组值（当前为
`3000`），`gpt-5.5`/`5.4`/`5.4-mini` 也保留上游组值（当前为 `2911`）。紧凑的第三方
preset 为 DeepSeek V4、GLM 5.2、各 Qwen 3.7 变体、MiMo V2.5、MiniMax M3 和 Kimi
K2.7 Code 声明已审查值；Rosetta 为未声明此字段的 preset 以及 `gpt-5.2`、
`codex-auto-review` 保留内置回退分组。每组都非空；同组共享值，不同组不得碰撞。未知
alias 使用确定性的
`rosetta-comp-v1:custom:<sha256(upstream_model)>`。overlay 的路由输入只取配置的上游
模型名并据此选择 preset；未配置时才回退到暴露 alias。Provider 身份不会改变 hash，
映射到同一上游模型的多个 alias 会共享 hash。compact preset 可以声明非空
`comp_hash`；该值优先于 Rosetta 内置分组和确定性回退，并会在暴露 alias 映射到此
preset slug 时随 preset 一起继承。上游 hash 缺失、preset hash 非法或未经审查的碰撞
都会使兼容检查失败，而不会静默关闭切模压缩。

本地模式还会配置 Codex 使用 Rosetta，而不只是写入模型目录。它选择自定义 Provider
ID `codex_rosetta`，但生成的 Provider `name` 会严格写成 `OpenAI`。这个区别是有意
的：Codex 把 `model_provider` 当作 ID 解析，而 `provider.is_openai()` 检查的是已选
Provider 区分大小写的 `name`。该托管 Provider 使用 Responses、稳定复用的
`codex` 网关 API Key，以及本地网关的实际监听端口；其他非 Rosetta Provider 表及
其参数保持不变。本地模式同步会在写入前比较完整目标字节，因此只修改模型组 Provider
时，网关路由会热更新，但不会重写 Codex 文件，也不会提示重启 Codex。

Admin 的模型页还会在已确认并实际启用本地模式时管理三个 Codex 任务模型选择。网关
把选择保存在自身配置的 `codex` 下。由于 Guardian 从当前工作模型的 `ModelInfo`
读取覆盖值，`codex.auto_review_model_override` 会复制到生成目录的每个模型条目；
`codex.memories.extract_model` 和 `codex.memories.consolidation_model` 则分别写入
Codex `config.toml` 的 `[memories]` 表中。可编辑状态下，未配置或已删除的选择显示
黄色边框，仍然存在的已配置别名显示绿色。未确认并实际启用本地模式时，下拉框锁定为
Codex 默认值 `codex-auto-review`、`gpt-5.4` 和 `gpt-5.4-mini`；对应别名已配置时
显示绿色，否则显示红色。关闭本地模式会随其他托管产物一起移除两个 TOML 记忆模型
字段，但保留网关中的选择，供下次重新启用时恢复。

`codex-auto-review` 在本地模式下有一条例外规则。上游模型未填写，或者上游也叫
`codex-auto-review` 时，Rosetta 会完整保留官方打包条目，包括未设置的
`tool_mode`。这样 OpenAI 或 GPT 中转服务可以收到更原生的 Responses 请求形态，
不会被目录配置无意义地覆盖。若该别名被显式映射到另一个上游模型，本地模式会为
该条目写入 `tool_mode: "code_mode_only"`。不同的上游模型名被视为用户给出的信号：
审查模型来自非 OpenAI 服务；限制为较新的 Code Mode 可以减少 Rosetta 同时适配
旧工具模式和混合工具模式的压力。此规则只读取模型映射，不根据 Provider 名称或 URL
猜测服务身份。

打包 JSON 一共出现 41 个不同的键。`ModelInfo` 还接受 `effective_context_window_percent`，但所有打包条目都省略它，因此使用默认值 `95`。所以本文覆盖 42 个可作为目录输入的字段。

Rosetta 另外打包了基于 Terra 的 `deepseek-v4-pro`、`deepseek-v4-flash`、
`glm-5.2`、`qwen3.7-plus`、`qwen3.7-max`、`qwen3.7-max-2026-06-08`、
`mimo-v2.5`、`mimo-v2.5-pro`、`minimax-m3` 和 `kimi-k2.7-code` 预设。只有 LLM 模型组中
存在完全相同的别名时才会展开对应预设；它们不属于上游八项目录。每个预设保留
Terra 的指令结构并替换模型身份，同时声明各自的上下文、输入模态、推理档位和
经过选择的 Codex catalog 字段。MiniMax M3 还在自身预设中覆盖推理摘要支持、
默认摘要、按字节截断策略和并行工具调用支持。

预设资源的 `shared_overrides` 固定为官方 Codex `0.145.0-alpha.20`
`gpt-5.6-terra` catalog 中 28 个与模型身份无关的字段。模型身份、上下文、模态、
推理档位及其默认值、priority、`comp_hash` 和带身份的指令内容仍由单个模型声明，
或通过专用的身份替换路径生成。`shared_overrides` 中的每个键也都允许写在单个
`models[]` 条目中，并由模型值覆盖共享默认值。`template_slug` 只为 Rosetta 尚未
识别的未来 catalog 字段提供向前兼容兜底；新 catalog 已删除的已知字段不会从旧
模板中悄悄继承。本段只记录模型 catalog 的局部快照，不代表已经完成 Codex
`0.145` 的完整兼容适配。

打包 JSON 中有四个键不属于当前 Rust `ModelInfo`。加载打包文件时，Serde 会忽略它们：

- `available_in_plans`；
- `minimal_client_version`；
- `prefer_websockets`；
- `reasoning_summary_format`。

下文把它们标记为 **0.144.4 未消费**。除非后续 Codex 版本开始消费这些字段，否则 Rosetta 不应根据它们实现运行时协议逻辑。`used_fallback_model_metadata` 则相反：它是 `ModelInfo` 的客户端内部运行时标志，而且禁止反序列化，因此不是合法的 catalog 输入字段。

## 身份、发现和 UI

| 字段 | 类型与示例 | Codex 行为 | Rosetta 适配建议 |
| --- | --- | --- | --- |
| `slug` | 字符串，`"third-party-agent"` | 稳定的模型标识，用于目录匹配、选择、请求、路由和遥测。 | 必须与 Rosetta 对外暴露的模型别名一致。再由 Rosetta 模型组把别名映射到真实上游模型；不要用 `display_name` 路由。 |
| `display_name` | 字符串，`"Third-Party Agent"` | 模型选择器中的可读名称，不会加入模型指令。 | 只当作 UI 文案，不能暗示其他字段并未启用的能力。 |
| `description` | 字符串或 null，`"Agent model adapted through Rosetta."` | 模型选择器中的简短说明，不会加入模型指令。 | 描述已经测试的用途，不要直接照抄上游厂商的能力宣传。 |
| `priority` | 整数，`20` | 排序模型预设；可用模型中优先级最高者可能成为默认模型。打包目录中数值越小优先级越高。 | 选择稳定且不冲突的顺序。不要为了强制第三方模型成为默认而复制 GPT 的最高优先级。 |
| `visibility` | `"list"`、`"hide"` 或 `"none"` | 控制预设是否出现在模型选择界面；`codex-auto-review` 使用 `hide`，fallback metadata 使用 `none`。 | 用户可选别名用 `list`，内部辅助模型用 `hide`，不应出现在 picker 的 metadata 用 `none`。 |
| `supported_in_api` | 布尔值，`true` | 传递为模型预设的 API 支持标志。 | 只有 Rosetta 确实能路由该别名时才设为 true；此字段不会验证真实上游 API。 |
| `availability_nux` | 对象或 null，`{"message":"New model available."}` | 模型刚可用时展示的可选 NUX 提示。 | 私有别名通常设为 null；不要把它当能力开关。 |
| `upgrade` | 对象或 null，`{"model":"replacement","migration_markdown":"Use replacement."}` | 为旧模型提供推荐替代模型和迁移说明。 | 只用于明确的别名迁移。真正的上游路由变化仍放在 Rosetta 配置中。 |
| `available_in_plans` | 字符串数组，`["plus","team"]` | **0.144.4 未消费：**不在 `ModelInfo` 中。 | 不要据此做 Rosetta 鉴权或路由；访问控制应在网关/provider 层实现。 |
| `minimal_client_version` | 打包 JSON 中的字符串，`"0.144.0"` | **0.144.4 未消费：**不在 `ModelInfo` 中。 | 不要依靠它拒绝旧客户端；需要时使用明确的网关兼容策略。 |

## 推理、输出和服务档位

| 字段 | 类型与示例 | Codex 行为 | Rosetta 适配建议 |
| --- | --- | --- | --- |
| `default_reasoning_level` | reasoning effort 或 null，`"medium"` | 用户未选择时使用的默认推理强度。 | 选择上游接受或 Rosetta 已映射的值，并保证它也出现在 `supported_reasoning_levels` 中。 |
| `supported_reasoning_levels` | 对象数组，`[{"effort":"low","description":"Fast"},{"effort":"high","description":"Deep"}]` | 提供可选的推理强度及 UI 说明。当前枚举包括 `none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max` 和 `ultra`，后续版本可能变化。 | 只声明上游接受或 Rosetta 明确映射的档位。不要为了获得委派行为而照抄 `ultra`。 |
| `supports_reasoning_summaries` | 布尔值，`false` | 允许 Codex 请求推理摘要。 | 除非上游支持请求/响应形态，或 Rosetta 能可靠转换/剥离，否则保持 false。 |
| `default_reasoning_summary` | `"auto"`、`"concise"`、`"detailed"` 或 `"none"` | 用户没有配置时的默认摘要模式。 | 第三方模型在端到端验证前优先使用 `none`。 |
| `reasoning_summary_format` | 字符串，`"experimental"` | **0.144.4 未消费：**不在 `ModelInfo` 中。 | 不要按此字段分支 Rosetta 转换；应检查实际请求和流事件。 |
| `support_verbosity` | 布尔值，`true` | 为 true 时，Codex 发送用户配置或默认的 Responses `text.verbosity`；为 false 时省略。 | 只有上游接受，或 Rosetta 会剥离/映射时才启用。 |
| `default_verbosity` | `"low"`、`"medium"`、`"high"` 或 null | 支持 verbosity 且用户未覆盖时的默认值。 | 使用上游真实支持的值；`support_verbosity` 为 false 时，null 最安全。 |
| `service_tiers` | 对象数组，`[{"id":"priority","name":"Fast","description":"Higher speed"}]` | 为 UI 和 subagent/模型选择列出允许的服务档位，并校验请求档位。 | 除非 Rosetta provider 会把档位映射到真实上游服务等级，否则保持空数组。 |
| `default_service_tier` | 字符串或 null，`"priority"` | 未显式选择时的目录默认档位，而且仍必须是该模型支持的档位。 | 只有对应 ID 存在于 `service_tiers` 且 Rosetta 会透传或映射时才设置。 |
| `additional_speed_tiers` | 字符串数组，`["fast"]` | `service_tiers` 的旧版、已弃用前身。 | 新第三方条目优先使用 `service_tiers`；除非目标 Codex UI 仍依赖旧标志，否则保持空数组。 |

## 上下文、压缩和输入

| 字段 | 类型与示例 | Codex 行为 | Rosetta 适配建议 |
| --- | --- | --- | --- |
| `context_window` | 正整数或 null，`128000` | 当前上下文窗口，用于预算和压缩。 | 填写考虑 provider 限制后的真实可用上游限制。不能复制 GPT 数值来解锁更大的 UI 限制。 |
| `max_context_window` | 正整数或 null，`128000` | 用户/配置覆盖 `context_window` 时的上限；当前窗口缺失时也作为 fallback。 | 使用经过验证的上游最大值。固定限制的模型通常与 `context_window` 相同。 |
| `effective_context_window_percent` | 整数百分比，`95` | 计算有效输入容量时预留 headroom。省略时默认 `95`，打包条目都使用该默认值。 | 如果 Prompt、工具定义或上游输出预留需要更大空间，可降低该值。虽然当前类型没有单独校验范围，仍应保持在有意义的 `1..=100`。 |
| `auto_compact_token_limit` | 正整数或 null，`100000` | 显式自动压缩阈值。null 时从 resolved context window 的 90% 推导；显式值也会被限制在该 90% 上限。 | 通常保持 null。只有真实长会话证明上游需要更早压缩时才设置。 |
| `comp_hash` | 不透明字符串或 null，`"third-party-prompt-v1"` | 标识 compact 历史兼容性。切换模型时，如果两边非 null 且不同，Codex 会先用旧模型压缩；任一为 null 则跳过此兼容动作。 | 默认使用 null。只有 Prompt、工具、compact 历史和 replay 语义明确兼容时才共享 hash。 |
| `truncation_policy` | 对象，`{"mode":"tokens","limit":10000}` 或 `{"mode":"bytes","limit":10000}` | 按 token 或 byte 预算在本地截断工具输出/历史。它不是模型上下文窗口，也不是 API 输出 token 上限。 | 上下文压力以 token 为主且估算可接受时用 `tokens`；需要 provider-neutral 的保守限制时用 `bytes`。应测试大命令输出和网页结果。 |
| `input_modalities` | `"text"`、`"image"` 的数组，`["text"]` | 声明模型接受的用户输入模态。没有 `image` 时，Codex 会从该模型承接的历史中移除图片。为了向后兼容，省略字段会默认同时支持文字和图片。 | 纯文本第三方模型应显式使用 `["text"]`。只有 Rosetta 和上游都能保留图片输入时才加入 `image`。 |
| `supports_image_detail_original` | 布尔值，`false` | 允许图片输入使用 `detail: "original"`；为 false 时会降级/清除 original detail，但不会单独禁用全部图片。 | 除非所选协议和上游都接受原始分辨率输入，否则保持 false，并与 `input_modalities` 协调。 |

## 工具和执行模式

| 字段 | 类型与示例 | Codex 行为 | Rosetta 适配建议 |
| --- | --- | --- | --- |
| `shell_type` | `"default"`、`"local"`、`"unified_exec"`、`"disabled"` 或 `"shell_command"` | 在 feature/config 覆盖前选择 Codex 命令工具家族；打包模型使用 `shell_command`。 | 选择第三方模型能够可靠调用且 Rosetta 已映射的工具形态。无法支持时，`disabled` 比虚假暴露 schema 更安全。 |
| `apply_patch_tool_type` | `"freeform"` 或 null | 非 null 时注册 custom/freeform `apply_patch`。 | 只有模型能稳定生成 patch 且 Rosetta 所选协议能保留 custom tool 源码和结果时才使用 `freeform`，否则为 null。 |
| `tool_mode` | `"direct"`、`"code_mode"`、`"code_mode_only"` 或 null | 选择直接原生工具、同时提供 Code Mode，或只提供 Code Mode。非法 selector 字符串会反序列化为 null。 | 工具能力较弱的模型从 `direct` 开始；只有模型能可靠为 custom `exec` 编写合法 JavaScript 时才用 `code_mode_only`；确实需要两种表面时用 `code_mode`。 |
| `experimental_supported_tools` | 字符串数组，`[]` | 启用当前客户端认识的命名实验工具；当前源码只用于很窄的测试/实验 gate。 | 除非指定 Codex 版本定义了该工具且 Rosetta 支持，否则保持空数组。 |
| `supports_parallel_tool_calls` | 布尔值，`false` | 允许并行工具调用。Responses Lite 还会在请求形态中强制关闭并行。 | 第三方模型安全默认值是 false。只有 call ID、结果顺序、历史 replay 和模型行为通过并发测试后才开启。 |
| `supports_search_tool` | 布尔值，`false` | 控制原生 `tool_search` 等 Codex namespace/tool discovery；它与 hosted `web_search`、独立 `web.run` 不同。 | 只有客户端可见的发现流程经过测试时才启用。Responses→Chat Code Mode 路径会在实时 `exec` 描述声明 deferred tools 时，另行投影基于当前请求 `ALL_TOOLS` 的搜索。精确命中的 Node REPL 工具会从成对请求历史恢复为结构化 Function，再转回 `exec`；Rosetta 不保留 Gateway namespace/discovery cache。hosted search 在 Tool Profile 中配置，Modified `web.run` 使用全局联网搜索设置。 |
| `web_search_tool_type` | `"text"` 或 `"text_and_image"` | 选择 hosted web search 声明的内容类型。 | 除非客户端路径和上游都支持图片搜索结果，否则使用 `text`。它不会配置 Tavily 凭证或 `web.run` 映射。 |
| `use_responses_lite` | 布尔值，`false` | 启用 Codex 的 Responses Lite 方言：工具/指令可能移入 input item，使用内部 header，禁用 hosted tool，并期望独立 namespace 工具。 | 只有 Rosetta 能处理 `input[].type="additional_tools"`、developer instructions、custom `exec`、独立 `/v1/alpha/search`、compact/header 行为和相应 stream 时才设为 true。 |
| `multi_agent_version` | `"disabled"`、`"v1"`、`"v2"` 或 null | 选择旧 multi-agent、collaboration v2、禁用，或 feature/config fallback；会影响工具定义和 subagent 生命周期。 | 在第三方模型能可靠完成相应工具闭环前使用 `disabled` 或 null。如果模型尚不稳定支持 collaboration schema，才退回 v1。 |
| `auto_review_model_override` | 字符串或 null，`"review-model"` | 把命令执行批准审查从当前模型改交给另一个模型。 | 指向 Rosetta 已暴露、确实可路由且适合批准审查的别名；普通模型保持 null。 |
| `prefer_websockets` | 布尔值，`true` | **0.144.4 未消费：**不在 `ModelInfo` 中，WebSocket 选择由其他位置控制。 | 不要根据这个字段宣称 WebSocket 支持或切换 Rosetta transport；应验证真实客户端请求路径。 |

## 指令和 Skills

| 字段 | 类型与示例 | Codex 行为 | Rosetta 适配建议 |
| --- | --- | --- | --- |
| `base_instructions` | 字符串，`"You are a coding agent..."` | 没有合法 instruction template 覆盖时使用的基础模型指令。 | 按第三方模型真实的工具和推理能力编写。不要只为了让 Codex 暴露工具而复制整套 GPT Prompt。 |
| `model_messages` | 对象或 null，`{"instructions_template":"... {{ personality }} ...","instructions_variables":{"personality_default":"","personality_friendly":"...","personality_pragmatic":"..."},"approvals":{"on_request":"...","on_request_auto_review":"..."},"auto_review":{"policy":"..."}}` | 只要存在 `instructions_template`，它就始终替代 `base_instructions`。`{{ personality }}` 占位符加完整 variables 可启用人格文本；`approvals` 提供批准模式消息。0.144.4 新增可选的 `auto_review.policy`，为自动审查模型提供目录策略，并在清除 instruction template 时保留。 | 先使用 null 或较小、经过测试的模板。只要有 template，所有关键指令都要放在其中，因为 Codex 不会自动追加 `base_instructions`。自动审查策略属于安全敏感的模型指导，只有审查过其行为后才应复制。 |
| `include_skills_usage_instructions` | 布尔值，`false` | 控制 Codex 是否把完整的“How to use skills”教程追加到 Skills fragment；不会控制 available skills 列表本身是否发送。 | 除非第三方模型确实受益于较长教程且上下文预算充足，否则保持 false。 |

### 打包模型的 `base_instructions` 和 `model_messages`

目录保存了完整字符串，但有用的兼容差异是结构，而不是逐字 Prompt：

| 模型 | Prompt 形态 |
| --- | --- |
| `gpt-5.6-sol` | 较新的 App 风格指令和固定人格；template 与 base 基本一致，不使用 personality 占位符。 |
| `gpt-5.6-terra`、`gpt-5.6-luna` | 两者完全相同，与 Sol 接近，只在段落顺序、可视化和格式规则上有少量差异。 |
| `gpt-5.5` | 独立的工程/前端 Prompt，支持动态 Friendly 和 Pragmatic 人格；它是唯一设置 `include_skills_usage_instructions: true` 的打包条目。 |
| `gpt-5.4`、`codex-auto-review` | base instructions 和 model-message template 完全相同，支持动态人格。 |
| `gpt-5.4-mini` | 结构接近 5.4，但文件链接和最终回答指导更短，使用相同人格变量。 |
| `gpt-5.2` | 更长的旧式 Prompt，包含 AGENTS、计划示例、验证指导、工具指导和 `update_plan`；没有动态人格。 |

## Rosetta 适配模型

应把 catalog metadata 看作 Codex 客户端应暴露什么、以及如何管理会话预算的能力声明，而不是第二套 Rosetta 路由系统。

```text
Codex catalog slug 和能力
             |
             v
Codex 请求形态和暴露工具
             |
             v
Rosetta 模型组：alias -> upstream model + protocol
             |
             v
Rosetta Tool Profile：直传 / 修改 / 禁用 / 注入
```

模型组仍是 provider、upstream model、protocol 和 Tool Profile 的唯一事实来源。catalog 决定 Codex 尝试发送什么；Rosetta 必须支持这种形态，或者在 catalog 中声明更保守的能力。

Admin 的模型组弹窗会优先检查配置的上游模型名；未填写上游映射时检查暴露模型名。只有与 `codex_models_0_144_4.json` 或 `codex_model_presets.json` 中 slug 完整一致才算匹配，匹配后显示模型的 `display_name`，并根据 `input_modalities` 显示 text/image 标签；带额外后缀或仅部分一致不会命中。网关运行时的图片过滤范围更窄：只读取 `codex_model_presets.json` 中完整匹配项的 `input_modalities`。完整 Codex catalog 和保存的 `model_info` 仍是面向 Codex 的目录元数据，不会施加网关运行时模态限制。每行始终显示“手动填写模型信息”按钮，点击后会在模型组弹窗右侧打开面板，包含单个预设的全部字段：`slug`、`display_name`、`description`、`identity`、`priority`、`context_window`、`input_modalities` 和 `supported_reasoning_levels`。已匹配时使用预设预填，未匹配时为空表；保存的 `model_info` 会覆盖自动预设，但暴露模型名仍作为实际路由使用的 catalog slug。输入模态和支持的推理等级使用 checkbox，并且只提供 catalog 模板能够物化的值。保存的覆盖配置只要任一可编辑字段与命中的预设不同，Admin 就会把自动检测标记为“已修改”；右侧面板的恢复按钮会显示命中的预设名称，点击后删除覆盖配置并重新以该预设为准。

### 第三方模型的推荐决策

| 能力 | 安全起点 | 满足以下证据后再启用 |
| --- | --- | --- |
| 工具表面 | `tool_mode: "direct"`，保守的 `shell_type` | 切换到 `code_mode` 或 `code_mode_only` 前，模型能生成准确的 custom/code-mode payload。 |
| Responses 方言 | `use_responses_lite: false` | additional tools、developer instructions、header、custom `exec`、Search、compact 和 stream continuation 都能通过 Rosetta。 |
| 并行调用 | `supports_parallel_tool_calls: false` | 并发 call ID、结果关联、replay 和模型行为稳定。 |
| Collaboration | `multi_agent_version: "disabled"` 或 null | 模型能完成所选 v1/v2 namespace 中所有工具的调用和错误恢复。 |
| 视觉 | `input_modalities: ["text"]`、`supports_image_detail_original: false` | 图片和 original detail 能通过所选协议和上游。 |
| 推理摘要 | `supports_reasoning_summaries: false`、`default_reasoning_summary: "none"` | 请求映射、流式摘要、encrypted content 和后续轮次都保持合法。 |
| Verbosity | `support_verbosity: false`、`default_verbosity: null` | 上游接受，或 Rosetta 会移除/映射 Responses `text.verbosity`。 |
| 搜索发现 | `supports_search_tool: false`、`web_search_tool_type: "text"` | namespace discovery、hosted search 和 `web.run` 已经分别测试和配置。 |
| 上下文 | 真实 provider 限制、`auto_compact_token_limit: null` | 长会话能在上游拒绝前完成 compact 和 resume。 |
| Compact 兼容 | `comp_hash: null` | 两个别名确实共享 Prompt、工具、compact 历史和 replay 语义。 |
| Patch 编辑 | `apply_patch_tool_type: null` | 模型和 Rosetta 能可靠保留 freeform patch call 并从错误中恢复。 |

为新第三方模型做适配时，如果新旧两种工具表面都能可靠支持，应优先使用 Codex 当前的新设计：网页能力优先独立 namespace `web.run`，而不是旧 hosted `web_search`；多 agent 能力优先 collaboration v2，而不是旧 `multi_agent_v1`。只有模型尚不能可靠调用新版 schema 时才保留旧表面。“优先新版”不能绕过上面的证据门槛：应先保持禁用，等完整 search/open 流程或 subagent 生命周期通过 Rosetta 后，再启用新版工具。

### 保守的第三方条目示例

这个示例只是安全起点，并不表示所有第三方模型都应使用相同 Prompt 或限制：

```json
{
  "slug": "third-party-agent",
  "display_name": "Third-Party Agent",
  "description": "Third-party model adapted through Codex-Rosetta.",
  "default_reasoning_level": "medium",
  "supported_reasoning_levels": [
    {"effort": "low", "description": "Lower latency"},
    {"effort": "medium", "description": "Balanced"}
  ],
  "shell_type": "shell_command",
  "visibility": "list",
  "supported_in_api": true,
  "priority": 20,
  "additional_speed_tiers": [],
  "service_tiers": [],
  "default_service_tier": null,
  "availability_nux": null,
  "upgrade": null,
  "base_instructions": "You are a coding agent. Use the provided tools exactly as specified.",
  "model_messages": null,
  "include_skills_usage_instructions": false,
  "supports_reasoning_summaries": false,
  "default_reasoning_summary": "none",
  "support_verbosity": false,
  "default_verbosity": null,
  "apply_patch_tool_type": null,
  "web_search_tool_type": "text",
  "truncation_policy": {"mode": "tokens", "limit": 10000},
  "supports_parallel_tool_calls": false,
  "supports_image_detail_original": false,
  "context_window": 128000,
  "max_context_window": 128000,
  "auto_compact_token_limit": null,
  "comp_hash": null,
  "effective_context_window_percent": 90,
  "experimental_supported_tools": [],
  "input_modalities": ["text"],
  "supports_search_tool": false,
  "use_responses_lite": false,
  "auto_review_model_override": null,
  "tool_mode": "direct",
  "multi_agent_version": "disabled"
}
```

示例特意省略当前未消费的四个打包键；添加它们不会改变 Codex 0.144.4 的运行时行为。

## 升级审查要求

每次升级 Codex 源码时，必须同时比较 schema 和打包值：

1. 对比 `ModelInfo`、嵌套 struct、枚举、Serde rename/default/skip 行为和未知模型 fallback initializer。
2. 对比 `codex-rs/models-manager/models.json` 的完整键集合和模型条目，包括当前客户端未消费的字段。
3. 把每个已消费字段的变化追踪到 core、tools、extensions、UI/model preset 转换、session persistence 和 request construction 中的调用方。
4. 重新判断 Rosetta 的第三方默认值和别名。某个复制的 GPT 值仍能反序列化，并不等于兼容。
5. 对每种变化后的请求/工具模式，用真实 Codex 客户端和真实上游模型测试，并在 Rosetta 日志中确认 model-facing request。

权威 checklist 和 compatibility point 位于
[`docs/dev/version-compatibility`](../dev/version-compatibility/README.md)。
