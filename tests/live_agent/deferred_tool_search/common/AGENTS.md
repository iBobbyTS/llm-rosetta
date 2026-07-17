# Isolated Capability Discovery Workspace

This workspace tests isolated discovery of a plugin, standalone MCP server, or
standalone skill.

- Follow `TASK.md` exactly and perform the operations in its stated order.
- The task prompt is already present in the conversation. Do not reopen or
  reread `TASK.md` from disk.
- Choose capabilities from their model-visible names and descriptions. The
  archive, arithmetic, and palette candidates intentionally have unrelated
  purposes; do not inspect fixture source to identify an answer.
- `ALL_TOOLS` is a JavaScript global array inside the `exec` runtime. It is not
  a shell environment variable. If an ordinary `tool_search` Function is
  available, use it first with a generic capability query and then invoke the
  selected runtime entry through raw `exec`. Otherwise filter `ALL_TOOLS`
  directly inside `exec`. In either case call the match through
  `tools[entry.name](args)`; do not launch Node or a shell through
  `exec_command` as a substitute.
- For an MCP task, include a compact `catalog` array in the same `text()` output
  as the nested result. Build it by selecting every `{name, description}` entry
  whose description contains `Rosetta live candidate`, preserving runtime
  order. It must contain the archive-proof, integer-addition, and
  color-normalization candidates. Emit the catalog and nested return value with
  exactly one argument: `text(JSON.stringify({ catalog, result }))`. The
  `text()` helper does not accept multiple output arguments. This is diagnostic
  evidence only; do not mention it in the final response.
- When a request matches a skill, first check whether its complete body is
  already present in a turn-scoped `<skill>` fragment. Use that injected body
  directly when present. Otherwise read only the selected skill's complete
  `SKILL.md`. Do not read other fixture or marketplace files.
- Do not use shell commands, browser tools, network tools, file tools, or other
  plugins as substitutes for an MCP tool result.
- If runtime discovery, invocation, or result serialization fails, use the
  task's failure response. Do not inspect fixture, config, session, artifact,
  cache, or server source files to recover an answer.
- Do not modify files.
- Keep the final response to the proof or result requested by the task.
