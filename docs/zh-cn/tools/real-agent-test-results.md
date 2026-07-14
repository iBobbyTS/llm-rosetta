# Agent 实机工具测试结果

本文档记录截至 2026-07-14 已完成隔离 Agent 实机测试的各工具最终状态。大多数被测
Codex 工具行为以 Codex 0.144.1 为基准；生图一行明确记录 Codex 0.144.4 的结果。

每一行只记录工具的最终状态。如果修复后的测试成功，最终状态以修复后的结果为准；
如果没有修复后复测，则使用最后一次已记录结果。

状态含义：

- **成功**：模型调用了目标工具面，Rosetta 正确重建原生调用，并且 Codex 返回所需结果。
- **失败**：最后一次相关测试没有完成核心行为。
- **当前运行器不可用**：非交互测试运行器无法暴露或驱动该工具，因此没有评价模型行为。
- **按设计未实现**：Rosetta 正确返回文档约定的受控错误。

模型使用 `Codex 对外模型名 → 实际上游模型` 表示，实际上游身份以 Gateway Logs
为准。

## 命令和 Code Mode 工具

| 工具 | 最终状态 | 测试模型 | 最终观察 |
|---|---|---|---|
| `exec_command` | **成功** | `deepseek-v4-flash → deepseek-v4-flash`；`gpt-5.6-sol → deepseek-v4-flash` | 前台进程和需要续接的进程都能成功启动。 |
| `write_stdin` | **成功** | `deepseek-v4-flash → deepseek-v4-flash` | 输入按顺序写入同一个运行中进程会话。 |
| Code Mode `exec` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | Codex 成功执行 Rosetta 重建的嵌套工具调用。 |
| 顶层 `wait` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 成功续接已 yield 的 Code Mode cell，并取得第二阶段输出。 |
| `request_user_input` | **当前运行器不可用** | 未进行模型测试 | Codex 0.144.1 在非交互 `codex exec` 模式拒绝该工具，需要 app-server JSON-RPC 运行器。 |
| `update_plan` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 两次投影调用都重建为具有预期状态的原生计划更新。 |
| `Glob` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 本地化调用重建为原生嵌套命令，并返回预期文件。 |
| `Grep` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 本地化调用重建为原生嵌套命令，并返回预期匹配。 |
| `Read` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 本地化调用返回预期文件内容。 |
| `Edit` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 模型侧调用成功重建为原生补丁执行。 |
| `Write` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 模型侧新建文件调用成功重建为原生补丁执行。 |
| `apply_patch` | **仅内部成功，不向模型暴露** | `gpt-5.6-sol → deepseek-v4-flash` | Chat Default 已隐藏直接工具；Rosetta 仍在内部使用 Codex 的实际声明翻译 `Edit` 和 `Write`。 |
| `view_image` | **成功** | `gpt-5.6-sol → qwen3.7-plus` | Qwen 收到原生图片结果，并正确识别测试图的全部色块位置。纯文本 DeepSeek 上游能执行调用，但不能完成视觉识别。 |
| `image_gen.imagegen` | **失败：未暴露** | `qwen3.7-plus → qwen3.7-plus` | Codex 0.144.4 本地模式运行中，Codex 源请求没有带生图工具，因此 Rosetta 没有可投影的实时声明。Qwen 从未收到该工具，所以这不是 Qwen 能力失败的证据。 |
| `get_goal` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 成功返回原生 Goal 状态。 |
| `create_goal` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 成功创建 Goal，且没有错误设置 token budget。 |
| `update_goal` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | 成功把已创建 Goal 更新为 `complete`。 |

较早的单次交互输入场景曾因模型重复发送 stdin 并重启进程而失败；后续两阶段续接测试
证明 `write_stdin` 本身工作正常，因此该工具最终状态为成功。

## 网络工具

| 工具或操作 | 最终状态 | 测试模型 | 最终观察 |
|---|---|---|---|
| Hosted `web_search` | **成功** | `deepseek-v4-flash → deepseek-v4-flash` | Rosetta 本地化 Hosted 工具后，Tavily 返回了可用搜索结果。 |
| `web.run` / `search_query` | **成功** | `gpt-5.6-sol → deepseek-v4-flash`；`gpt-5.6-terra → gpt-5.6-terra` | 搜索经本地 Codex Search API 和 Tavily 执行成功。 |
| `web.run` / `open` | **成功** | `gpt-5.6-sol → deepseek-v4-flash` | Rosetta 成功解析已存储的 `turnXsearchY` 引用，并返回可读页面内容。 |
| `web.run` / `find` | **按设计未实现** | `gpt-5.6-sol → deepseek-v4-flash` | 调用成功到达 Rosetta，并返回包含 Browser Use 提示的约定 Not Implemented 错误。 |
| `web.run` / `click` | **按设计未实现** | `gpt-5.6-sol → deepseek-v4-flash` | 调用成功到达 Rosetta，并返回包含 Browser Use 提示的约定 Not Implemented 错误。 |

`find` 和 `click` 的契约测试成功只证明路由和受控失败行为正确，不代表浏览操作已经实现。

## Namespace 工具

| 工具 | 最终状态 | 测试模型 | 最终观察 |
|---|---|---|---|
| `clock.curr_time` | **成功** | `deepseek-v4-flash → deepseek-v4-flash`；`gpt-5.6-terra → gpt-5.6-terra` | 原生 Namespace 调用返回非错误时间结果。 |
| `memories.list` | **成功** | `deepseek-v4-flash → deepseek-v4-flash`；`gpt-5.6-terra → gpt-5.6-terra` | 调用返回隔离的记忆 fixture。 |
| `skills.list` | **当前运行器不可用** | `deepseek-v4-flash`；`gpt-5.6-terra` | `codex exec` 运行环境没有暴露 app-server orchestrator Skills Namespace。 |

## Collaboration 工具

修复后的别名矩阵使用 `gpt-5.6-sol → deepseek-v4-flash`。后续本地模式测试使用
`deepseek-v4-flash → deepseek-v4-flash`；当它测试了同一个 Function 时，以后者
作为最终结果。

| 工具 | 最终状态 | 测试模型 | 最终观察 |
|---|---|---|---|
| `collaboration.spawn_agent` | **成功** | `deepseek-v4-flash → deepseek-v4-flash` | 子任务在预期规范路径创建，并返回所需标记。 |
| `collaboration.wait_agent` | **失败** | `deepseek-v4-flash → deepseek-v4-flash` | 最新本地模式测试中，原生 Codex 漏掉了第一次等待前已经完成的子任务；Rosetta 名称和参数重建正确。更早的别名测试曾成功。 |
| `collaboration.list_agents` | **成功** | `deepseek-v4-flash → deepseek-v4-flash` | 返回了预期的已完成常驻子任务。 |
| `collaboration.send_message` | **成功** | `deepseek-v4-flash → deepseek-v4-flash` | 队列消息到达运行中的子任务，并产生所需子任务结果。 |
| `collaboration.followup_task` | **成功** | `deepseek-v4-flash → deepseek-v4-flash` | 在同一个已完成子任务上启动第二轮，并产生所需标记。 |
| `collaboration.interrupt_agent` | **成功** | `deepseek-v4-flash → deepseek-v4-flash` | 运行中的子任务被中断，并继续保留在 collaboration 状态中。 |

`wait_agent` 的最终失败归类为 Codex 原生生命周期行为，不是 Rosetta 翻译失败，也不是
上游模型调用参数错误。

## 尚无成功实机测试覆盖

现有结果不能确定以下工具的最终状态：

- `tool_search`；
- `clock.sleep` 和其他未列出的 Clock 操作；
- 上表以外的 Memories 和 Skills 操作；
- 其他 `web.run` 命令，例如 `image_query`、`finance`、`weather`、`sports`、
  `time` 和 `screenshot`；
- GitHub、MCP、App 和 Connector Namespace 工具；
- 默认禁用的旧工具面，例如 `shell_command` 和 `multi_agent_v1`。

这些项目是**尚未测试**，不是测试失败。

## 测试定义

- [命令执行](../../../tests/agent_workspace/command_execution/README.md)
- [网页搜索](../../../tests/agent_workspace/network_search/README.md)
- [Namespace 工具](../../../tests/agent_workspace/namespace_tools/README.md)
- [Collaboration 工具](../../../tests/agent_workspace/subagent_tools/README.md)
- [内置 Code Mode 工具](../../../tests/agent_workspace/builtin_tools/README.md)
- [生图与视觉检查](../../../tests/agent_workspace/image_generation/README.md)
