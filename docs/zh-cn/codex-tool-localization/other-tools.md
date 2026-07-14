# 其他 Codex 工具

Codex 有几个 agent/运行时工具，其行为不仅仅依赖于简单的函数调用架构。Codex-Rosetta 会在需要时保留 Responses 特有结构。定向的模型使用指导属于 Profile 的 Function field，而不是 Converter 中按工具名称写死的规则。

## 计划模式

计划模式使用 `request_user_input`，当模型需要在生成或修改计划之前获得真实的用户决策时。Chat 模型可能会将其与最终的审批步骤混淆，并在已经发出提议计划后询问用户是否继续。

内置 **Chat Default** Profile 会将 `request_user_input` 设为 Modified，并追加具有以下效果的工具使用指导：

- 仅在需要做出实质性改变计划的偏好或决策时使用。
- 不要用它来询问是否批准、继续或执行提议的计划。
- 在最终的 `<proposed_plan>` 块之后，让 Codex UI 处理审批和实施。
- 保持选项标签简短自然，不要使用 `A:`、`B:` 或 `C:` 前缀。

这是一种提示级别/工具描述的适配，不会改变工具架构。工具页面会在 Function 为 Modified 时显示这段效果摘要，但不会展示打包的完整提示词文本。

## TODO / update_plan

当 Codex 只把 `update_plan` 作为 Code Mode 的嵌套工具暴露时，内置 **Chat Default** Profile 会将其投影为普通 Chat Function。Rosetta 从 Codex 当前的 `exec` 声明中提取参数 schema 和 description，不在本地维护重复 schema。模型调用会被重组为确定性的 custom `exec` 脚本交还 Codex。如果 Codex 已直接暴露 `update_plan` Function，则保留直接定义。

## 目标工具

目标状态通过 `get_goal`、`create_goal` 和 `update_goal` 管理。Chat 模型可能无法从简洁的原生工具描述中推断出正确的顺序。

内置 **Chat Default** Profile 会将 `create_goal` 和 `update_goal` 设为“修改”，并追加对应工具使用指导：

- `create_goal`：当用户明确要求标记目标完成或受阻但不存在活跃目标时调用，或者当 `update_goal` 报告线程没有目标时。除非用户明确提供了数字令牌预算，否则不要设置 `token_budget`。
- `update_goal`：当目标状态不确定时，先调用 `get_goal`。如果没有活跃目标，使用简洁的目标调用 `create_goal`，除非明确要求否则不设置令牌预算，然后重试 `update_goal`。

`get_goal` 使用“直通”：Rosetta 会投影它当前的 Code Mode 声明并把调用翻译回 `exec`，但不追加指导。`create_goal` 和 `update_goal` 因为继续使用上述 Profile 管理的指导，所以保持“修改”。

它们在工具页面的卡片会用标题为**追加的描述提示词**的只读 textarea，显示所选 Profile 中实际追加的完整指导，不再用卡片 description 承载这段内容。

## Code Mode 嵌套工具

新版 Codex Code Mode 会把部分运行时工具放在 custom `exec` 的 description 中，而不再把每项工具都作为顶层 Function 暴露。对于 Responses 到 Chat 的路由，Chat Default 的投影规则会在声明实际存在且 Profile 状态为“直通”或“修改”时，把以下嵌套工具投影为普通 Chat Function：

- `exec_command`、`write_stdin`、`update_plan` 和 `view_image`
- `web__run`（Codex 运行时身份为 `web.run`），在 Chat 中显示为 `web-run`
- `image_gen__imagegen`（运行时身份为 `image_gen.imagegen`），显示为 `image_gen-imagegen`
- `get_goal`、`create_goal` 和 `update_goal`
- `clock__curr_time` 和 `clock__sleep`，在 Chat 中显示为 `clock-curr_time` 和 `clock-sleep`
- 扁平的 `memories__*` 和 `skills__*` 条目，在 Chat 中使用标准的 `namespace-function` 名称显示

Rosetta 从实际 Codex `exec` 声明中读取每项工具的 schema 和 description。反向解析器覆盖 Codex 会输出的 TypeScript 语法，包括 literal、union、intersection、array、tuple 和对象 index signature；Codex 在把 JSON Schema 渲染成 TypeScript 时已经省略的约束无法恢复。声明无法解析时不会凭空生成 Function。同名的直接 Function 优先，该名称的投影会 fail-closed。

Chat Default 会把父级 `exec` 设为“禁用”，禁止向模型暴露。对于 Responses 到 Chat 的路由，Rosetta 仍会让这个容器通过源请求的 Profile 过滤，在内部读取并展开子工具，随后在发给 Chat 上游之前移除父级。这个移除过程 fail-closed：即使没有任何模型可见声明能够成功解析，也不会把已禁用的父级 `exec` 当作回退暴露。只有复制出的 Profile 明确把父级设为“直通”或“修改”时，才允许有意暴露原始 `exec`。

对 exec 展开卡片而言，**直通**只做形态适配：把当前声明暴露为普通 Chat Function，再把调用翻译回 `exec`，不会追加任何 catalog 文本。Chat Default 中的 `exec_command`、`write_stdin`、`update_plan`、`view_image`、`get_goal`、Clock、Memories 和 Skills 都使用该状态。只有 Profile 会改变模型可见指导或工具行为时才保留**修改**：`create_goal` 和 `update_goal` 会追加指导，`web.run` 则使用所选的 Tavily Rosetta 搜索映射。

Chat Default 会保持 `image_gen__imagegen` 为 Modified，并显示可编辑的 Base URL 和 Token 字段。保存凭据后，Rosetta 会把该 Function 投影给上游模型，并通过 `/v1/images/generations` 或 `/v1/images/edits` 处理随后产生的 OpenAI 风格生图或改图请求。在明确配置生图前，应保持 Token 为空。

投影 Function 的调用会被重组为调用嵌套 `tools` 对象的确定性 JavaScript，并作为调用 `exec` 的 `custom_tool_call` 返回 Codex。精确的 Chat 到 Codex 调用映射会写入现有的加密工具历史缓存，因此后续请求在其 24 小时 TTL 内可先恢复原始 Chat Function 和参数，再发送给上游。`view_image` 使用 `image(...)`，`image_gen.imagegen` 使用 `generatedImage(...)`，承载文本结果的其他投影工具使用 `text(...)`。

Chat Default 会禁用 `apply_patch` 的 exec 投影，改为由 Rosetta 注入三个读取工具 `Read`、`Glob`、`Grep`，以及两个写入工具 `Edit`、`Write`。`Edit` 和 `Write` 可以在内部使用 Codex 嵌套的 `apply_patch` 实现，但不会向上游模型暴露 `apply_patch`。

顶层 `wait` 和 `request_user_input` Function 不会投影到 `exec`，在两个方向都保持直接 Function。

## 子工具与命名空间工具

Codex 通过 Responses 命名空间工具（如 `collaboration` 和旧版 `multi_agent_v1`）暴露子工具能力。Chat Completions 没有相同的嵌套命名空间工具结构。

对于 Responses 到 Chat 的路由，Rosetta 将命名空间子工具扁平化为普通的 Chat 函数工具。例如：

```text
multi_agent_v1-spawn_agent
```

在请求转换过程中，Rosetta 记录展开后的工具名称到 Responses 命名空间的映射。连字符形式 `multi_agent_v1-spawn_agent` 是正式名称，也符合 Chat API 通常只允许字母、数字、下划线和连字符的限制。返回时 Rosetta 还接受 `multi_agent_v1_spawn_agent`、`multi_agent_v1.spawn_agent`；当裸 `spawn_agent` 只属于一个 Namespace 且不与普通 Function 重名时，也会恢复该裸名称。任一名称存在普通 Function 或其他 Namespace 冲突时都保持 fail-closed。随后 Rosetta 在将事件返回给 Codex 之前恢复 Responses 命名空间元数据：

```json
{
  "type": "function_call",
  "name": "spawn_agent",
  "namespace": "multi_agent_v1"
}
```

对于 Responses 到 Responses 的路由，命名空间工具保持原生的 Responses 形态。

## 插件与延迟加载工具

插件和延迟加载工具发现使用相同的通用工具转换路径。Rosetta 当前没有为每个插件工具添加专门的本地化规则。

重要的行为是工具调用必须能完成往返：

- 工具定义在发送给 Chat 供应商时被转换为 Chat 兼容的函数形态。
- 工具调用被转换回 Responses 事件供 Codex 使用。
- 当工具来自 Responses 命名空间时，命名空间元数据被恢复。
- 消息 `phase` 元数据被保留，以便工作流程输出在 Codex 中保持可折叠状态。

## Tool Profile 作用范围

**OpenAI Responses (Tool Mapping only)** 支持 Tool Profile，同时让 Responses 请求和响应的其余部分继续走直接路径。Responses Rosetta、Chat、Anthropic 和 Google 模型组也支持 Profile 选择与处理。目前暂时只打包 **Chat Default** 一个 Profile；需要独立的透传或映射策略时，请先复制它再修改。

打包的 Profile 通过 `image_gen.imagegen` 管理当前 Codex 图片生成工具，不包含已废弃的 Hosted `image_generation` 工具。

### 工具页面分类与卡片输入项

工具页面分为四类：

- **exec 展开**：从 Codex `exec` 嵌套工具投影出的普通 Chat Function。Codex 会把带 Namespace 的运行时身份扁平化成 `namespace__function` 属性，例如 `clock__sleep`、`web__run` 和 `image_gen__imagegen`；目录直接列出这些 Function，不再虚构父 Namespace 卡片。
- **Function**：直接 Function 和使用相同卡片形态管理的 Hosted 工具。
- **Namespace**：Codex 直接暴露为 Responses Namespace 的固定工具，即 `collaboration` 和旧版 `multi_agent_v1`。已安装的 plugin、MCP、app 和 connector Namespace 是运行时动态内容，不属于这个静态目录。
- **Rosetta 注入**：注入的 `Read`、`Glob`、`Grep`、`Edit` 和 `Write`。

Namespace 的状态显示为展开、透传（Chat API 无效）和禁用。禁用 Namespace 会强制其所有子项为禁用并锁定选择器。

Function 的 Passthrough 状态在中文界面显示为**直通**。对 exec 展开条目，它仍会完成上述只改变表示形态的投影与反向翻译，但不会显示额外卡片说明，也不会修改传给模型的工具 description。

Function、Hosted 或 Namespace 目录项可以声明多组 `profile_inputs`。每组包含稳定 ID、本地化小标题、默认值，以及 `text`、`password`、`select` 或 `textarea` 输入类型。Select 使用有序的 `{value, label}` 选项：工具页面显示 label，并将 value 保存进 Profile。Textarea 可以由 catalog 声明为只读，让用户查看和复制当前 Profile 值但不能编辑。工具页面会按照目录中的声明顺序，在工具状态选择器下方渲染这些输入项。`web_search` 和 `web.run` 卡片分别保存自己的搜索 Provider 与 Token；目前 Provider 只支持 Tavily。原先独立的 Web Search 设置页签已移除。

输入项可以通过 `visible_when` 声明需要显示的工具状态，例如 `["modified"]`。输入项隐藏后，其已保存的 Profile 值不会被清除。目录也可以把运行时仍需使用的输入项完全从 UI 隐藏。卡片 description 默认在该工具支持的所有状态下显示；条目可以使用相同状态列表格式的 `description_visible_when` 自定义显示条件。“修改”状态的 Function 通常只显示如何修改工具描述的本地化摘要；`create_goal` 和 `update_goal` 则通过上述只读 textarea 显示实际的 Profile guidance。目录项还可声明 `profile_mutations`：通用 Profile 处理在 Modified 状态执行对应的 description 或 parameter description 追加操作，Namespace 则可在 Expanded 状态执行。Chat Default 中 `request_user_input`、Goal 工具和部分 `collaboration` Function 的工具使用指导都使用此机制；Converter 不再按 Function 名称写死指导文本。Hosted `web_search` 无论状态都会进行协议转换，但只有 Modified 会追加其 Profile guidance。

工具页面首次加载时会默认展开所有 Namespace 行。该展示状态与 Namespace 在 Profile 中的状态无关，用户仍可在当前页面手动折叠。

内置的 **Chat Default** Profile 会禁用旧版 `multi_agent_v1` Namespace，同时保持 `collaboration` 启用。Collaboration 子工具会为 Chat 展开，并恢复为原生 Responses Namespace 调用；它们不会通过 Code Mode `exec` 转译。任何 Namespace 设为 Disabled 时，其所有子 Function 都会被强制设为 Disabled，并锁定状态选择器，直到重新启用该 Namespace。

用户填写的值随用户 Profile 保存到 `inputs.<function-item-id>.<input-id>`。从当前 Profile 创建副本时会复制当前值；切换或重置 Profile 时会恢复已保存的值。打包的内置 Profile 允许编辑并显式保存可见 field；保存值写入 `tool_profile_input_overrides.<profile-id>`，不会改写打包 JSON。它的工具传递状态仍保持只读。输入项只有被对应运行时功能读取后才会生效；当前 Modified Function 使用目录中隐藏的 guidance，搜索和图片工具使用其可见的供应商凭据，`image_gen.imagegen` 使用 Base URL 和 Token。
