# 其他 Codex 工具

Codex 有几个 agent/运行时工具，其行为不仅仅依赖于简单的函数调用架构。Codex-Rosetta 通过保留必要的 Responses 特定结构，以及在原生工具描述过于简洁时添加针对性的 Chat 模型指导，来确保这些工具在仅支持 Chat 的上游模型中仍然可用。

## 计划模式

计划模式使用 `request_user_input`，当模型需要在生成或修改计划之前获得真实的用户决策时。Chat 模型可能会将其与最终的审批步骤混淆，并在已经发出提议计划后询问用户是否继续。

对于 Chat 目标，Rosetta 会在 `request_user_input` 工具描述中追加额外指导：

- 仅在需要做出实质性改变计划的偏好或决策时使用。
- 不要用它来询问是否批准、继续或执行提议的计划。
- 在最终的 `<proposed_plan>` 块之后，让 Codex UI 处理审批和实施。
- 保持选项标签简短自然，不要使用 `A:`、`B:` 或 `C:` 前缀。

这是一种提示级别/工具描述的适配，不会改变工具架构。

## TODO / update_plan

`update_plan` 工具当前通过正常的转换路径传递，没有专门的本地化规则。

在 Responses 到 Chat 转换后，它作为常规函数工具暴露给 Chat 供应商，其调用通过正常的工具调用管道转换回来。当前没有为 `update_plan` 专门应用特殊的提示后缀、命名空间恢复或架构重写。

## 目标工具

目标状态通过 `get_goal`、`create_goal` 和 `update_goal` 管理。Chat 模型可能无法从简洁的原生工具描述中推断出正确的顺序。

对于 Chat 目标，Rosetta 会追加额外指导到：

- `create_goal`：当用户明确要求标记目标完成或受阻但不存在活跃目标时调用，或者当 `update_goal` 报告线程没有目标时。除非用户明确提供了数字令牌预算，否则不要设置 `token_budget`。
- `update_goal`：当目标状态不确定时，先调用 `get_goal`。如果没有活跃目标，使用简洁的目标调用 `create_goal`，除非明确要求否则不设置令牌预算，然后重试 `update_goal`。

`get_goal` 本身不做修改。

## 子工具与命名空间工具

Codex 通过 Responses 命名空间工具（如 `multi_agent_v1`）暴露子工具能力。Chat Completions 没有相同的嵌套命名空间工具结构。

对于 Responses 到 Chat 的路由，Rosetta 将命名空间子工具扁平化为普通的 Chat 函数工具。例如：

```text
multi_agent_v1.spawn_agent -> spawn_agent
```

在请求转换过程中，Rosetta 记录从子工具名称到 Responses 命名空间的映射。当上游 Chat 模型返回 `spawn_agent` 工具调用时，Rosetta 在将事件返回给 Codex 之前恢复 Responses 命名空间元数据：

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

## Image Generation 移除

某些模型不应看到 `image_generation`，因为它们无法很好地使用它，或者因为路由应完全避免图像生成。当为某个模型启用 `remove_image_generation` 时，Rosetta 会从出站请求中移除 `image_generation` 工具，并在必要时清除不兼容的 tool choice/config 字段。

此适配可以在直接 Responses 透传和 Responses 到 Chat 转换之前运行。
