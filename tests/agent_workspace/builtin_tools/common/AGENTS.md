# Built-in Tool Test Workspace

This workspace is a deterministic Codex built-in tool-use test.

- Follow `TASK.md` exactly and preserve its call order.
- Use only the model-facing tools named by the task.
- Do not inspect parent directories or unrelated files.
- Do not substitute shell commands, browser automation, Skills, MCP, or prior
  knowledge for a required tool.
- Modify files only when the selected task explicitly requires it.
- Keep the final response to the exact result line requested by the task.

