# 代码编辑

Codex 提供了原生编辑能力，例如 `apply_patch`、`exec_command` 和 `write_stdin`。许多开源模型在训练或产品使用中接触更多的是 Claude Code 风格的编辑工具，因此它们可能会选择 shell 命令或临时 Python 脚本，而不是 Codex 的 patch 工作流。

Codex-Rosetta 可以在 Responses 到 Chat 的路由上本地化模型端编辑界面，同时仍然在返回端向 Codex 提供原生工具调用。

## 模型配置

网关管理 UI 提供了一个名为 `Tool Adaption for Codex`（Codex 工具适配）的模型级配置区域。

当前选项：

- `Localize code editing tools`（本地化代码编辑工具）：将 Codex 原生编辑工具替换为面向上游模型的本地化 Chat 工具。
- Tool Profile 管理当前的 `image_gen.imagegen` Namespace 工具。已废弃的 Hosted `image_generation` 工具不再属于打包的 Profile 目录。
- `Tool call mapping cache TTL`（工具调用映射缓存 TTL）：持久化的本地化/原生工具调用映射的有效时长。

只有配置了该选项的模型路由会受到影响。

## 模型端工具

当为 OpenAI Responses 到 OpenAI Chat 的路由启用 `localize_code_editing_tools` 时，Rosetta 会从上游 Chat 请求中移除原生代码编辑工具，并暴露以下 Claude Code 风格的工具：

- `Read(file_path, offset?, limit?)`
- `Edit(file_path, old_string, new_string, replace_all?)`
- `Write(file_path, content)`
- `Glob(pattern, path?)`
- `Grep(pattern, path?, glob?, type?, output_mode?, case_insensitive?, line_numbers?, before_context?, after_context?, context?, head_limit?, offset?, multiline?)`
- `Bash(command, timeout?, description?, run_in_background?)`

本地化的 `Edit` 描述明确要求模型尽可能替换完整的行或连续的代码块。这有助于提升转换到 Codex patch 的质量，因为当 `old_string` 包含完整的行上下文时，`apply_patch` 的可靠性要高得多。

## 原生翻译

在 Codex 收到响应之前，本地化的工具调用会被转换回来：

- `Bash` 变为 `exec_command`。
- `Read` 变为一个 `exec_command`，打印 UTF-8 文件内容，支持可选的 offset 和 limit。
- `Glob` 变为一个通过 Python `glob` 实现的 `exec_command`。
- `Grep` 变为一个通过 `rg` 实现的 `exec_command`。
- `Write` 通常变为自定义的 `apply_patch` 新增文件调用。
- `Edit` 通常变为自定义的 `apply_patch` 调用。
- `Edit(replace_all=true)` 变为一个执行受控全局替换操作的 `exec_command`。

如果原始请求没有暴露自定义的 `apply_patch`，`Edit` 会回退到 `exec_command` 或 `shell_command`，在可用时通过 heredoc 调用 `apply_patch`；`Write` 会回退到一个通过 base64 安全 Python 辅助函数写入 UTF-8 内容的 `exec_command`。

## Read 输出扩展

某些模型在读取文件后仍然发出狭窄的子字符串编辑。Rosetta 在重建转换后的 Chat 请求时维护了一个会话级别的读取输出缓存。当后续的 `Edit` 针对一个可以从先前 `Read` 中无歧义扩展为完整行的子字符串时，Rosetta 会将 `old_string` 和 `new_string` 扩展为完整行替换，然后再生成 patch。

在成功对该文件执行修改操作后，该文件的缓存会被失效，因此过期的读取结果不会在后续编辑中被重用。

## 历史工具调用映射

Codex 在本地会话历史中存储助手工具调用，并在后续轮次中重新发送该历史。本地化之后，Codex 看到的是 `apply_patch` 这样的原生调用，但上游 Chat 模型最初看到的是 `Edit` 这样的本地化调用。

为了保持供应商端提示缓存和模型连续性，Rosetta 存储了一个映射：

- `session_id`（会话 ID）
- 原始本地化工具调用
- Codex 原生工具调用
- 过期时间

对于已认证且带 window scope 的 gateway 请求，SQLite 是跨请求 source of truth；映射
不会作为跨轮内存缓存保留。精确、可逆的 payload 使用 AES-256-GCM 进行 at-rest
保护。诊断脱敏不会应用到这份可执行 payload，因为 `[REDACTED]` 调用已无法描述 Codex
真实执行的工具动作。Key lifecycle、备份、失败和 legacy row 语义见
[网关安全与认证](../gateway-security.md#可执行工具历史存储)。

在后续同一会话的请求中，Rosetta 会遍历历史消息，在发送请求到上游之前，将已知的 Codex 原生调用替换为原始本地化调用。如果加载的映射未被当前出站请求使用，Rosetta 会在请求发送后将其删除。过期的行会定期清理。

这样既保持了 Codex 下游历史的原生性，又使上游模型的重复上下文保持稳定。

## 当前限制

本地化层有意保持保守：

- 它仅在 Responses 到 Chat 的路由上运行。
- 它只更改模型配置中启用了该功能的路由。
- 它不会尝试将任意的 shell 编辑解析回结构化编辑。
- 它无法隐藏模型放在普通文本中的推理内容。
