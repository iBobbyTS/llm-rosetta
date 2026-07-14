# Real-Agent Tool Test Results

This document records the final observed status of each tool covered by the
isolated real-agent tests available on 2026-07-14. Most Codex-facing behavior
was tested with Codex 0.144.1; the image-generation row records a Codex 0.144.4
run explicitly.

Each row reports only the final tool status. When a later test verified a
repair, the repaired result supersedes the earlier failure. If no repair test
exists, the latest recorded result is used.

Status meanings:

- **Success**: the model called the intended surface, Rosetta reconstructed the
  expected native call, and Codex returned the required result.
- **Failed**: the latest relevant test did not complete the core behavior.
- **Unavailable in runner**: the non-interactive test runner could not expose
  or drive the tool, so model behavior was not evaluated.
- **Not implemented by design**: Rosetta returned the documented bounded error.

Model labels use `Codex-facing model → actual upstream model`. The upstream
identity is taken from Gateway Logs.

## Command and Code Mode tools

| Tool | Final status | Tested model | Final observation |
|---|---|---|---|
| `exec_command` | **Success** | `deepseek-v4-flash → deepseek-v4-flash`; `gpt-5.6-sol → deepseek-v4-flash` | Foreground and yielded processes started successfully. |
| `write_stdin` | **Success** | `deepseek-v4-flash → deepseek-v4-flash` | Ordered input was delivered to the same running process session. |
| Code Mode `exec` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | Codex executed Rosetta-reconstructed nested tool calls. |
| Top-level `wait` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | It resumed the yielded Code Mode cell and received its second-phase output. |
| `request_user_input` | **Unavailable in runner** | Not model-tested | Codex 0.144.1 rejects this tool in non-interactive `codex exec` mode; an app-server JSON-RPC runner is required. |
| `update_plan` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | Both projected calls became native plan updates with the expected states. |
| `Glob` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | The localized call became a native nested command and returned the expected files. |
| `Grep` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | The localized call became a native nested command and returned the expected match. |
| `Read` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | The localized call returned the expected file content. |
| `Edit` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | The model-facing call was reconstructed as native patch execution. |
| `Write` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | The model-facing create-file call was reconstructed as native patch execution. |
| `apply_patch` | **Success internally; not model-facing** | `gpt-5.6-sol → deepseek-v4-flash` | Chat Default now hides the direct tool. Its actual Codex declaration remains available internally for `Edit` and `Write` translation. |
| `view_image` | **Success** | `gpt-5.6-sol → qwen3.7-plus` | Qwen received the native image result and correctly identified all fixture quadrants. The text-only DeepSeek upstream could execute the call but could not perform visual recognition. |
| `image_gen.imagegen` | **Failed: not exposed** | `qwen3.7-plus → qwen3.7-plus` | In the Codex 0.144.4 local-mode run, the Codex source request omitted image generation and Rosetta therefore had no live declaration to project. Qwen never received the tool, so this is not evidence of a Qwen capability failure. |
| `get_goal` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | The native Goal state was returned. |
| `create_goal` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | A Goal was created without an unintended token budget. |
| `update_goal` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | The created Goal was updated to `complete`. |

The earlier single-input command scenario failed because the model duplicated
stdin and restarted the process. The later two-stage continuation proved that
`write_stdin` itself works correctly, so the final tool status is Success.

## Network tools

| Tool or operation | Final status | Tested model | Final observation |
|---|---|---|---|
| Hosted `web_search` | **Success** | `deepseek-v4-flash → deepseek-v4-flash` | Rosetta localized the hosted tool and Tavily returned a usable search result. |
| `web.run` / `search_query` | **Success** | `gpt-5.6-sol → deepseek-v4-flash`; `gpt-5.6-terra → gpt-5.6-terra` | Search completed through the local Codex Search API and Tavily executor. |
| `web.run` / `open` | **Success** | `gpt-5.6-sol → deepseek-v4-flash` | Rosetta resolved the stored `turnXsearchY` reference and returned readable page content. |
| `web.run` / `find` | **Not implemented by design** | `gpt-5.6-sol → deepseek-v4-flash` | The call reached Rosetta and returned the documented Not Implemented error with the Browser Use hint. |
| `web.run` / `click` | **Not implemented by design** | `gpt-5.6-sol → deepseek-v4-flash` | The call reached Rosetta and returned the documented Not Implemented error with the Browser Use hint. |

The successful `find` and `click` contract tests prove correct routing and
bounded failure behavior; they do not mean those browser operations are
implemented.

## Namespace tools

| Tool | Final status | Tested model | Final observation |
|---|---|---|---|
| `clock.curr_time` | **Success** | `deepseek-v4-flash → deepseek-v4-flash`; `gpt-5.6-terra → gpt-5.6-terra` | The native Namespace call returned a non-error time result. |
| `memories.list` | **Success** | `deepseek-v4-flash → deepseek-v4-flash`; `gpt-5.6-terra → gpt-5.6-terra` | The call returned the isolated memory fixture. |
| `skills.list` | **Unavailable in runner** | `deepseek-v4-flash`; `gpt-5.6-terra` | The `codex exec` runtime did not expose the app-server orchestrator Skills Namespace. |

## Collaboration tools

The repaired alias matrix used `gpt-5.6-sol → deepseek-v4-flash`. The later
local-mode check used `deepseek-v4-flash → deepseek-v4-flash`; its result is
preferred when it tested the same Function after the alias matrix.

| Tool | Final status | Tested model | Final observation |
|---|---|---|---|
| `collaboration.spawn_agent` | **Success** | `deepseek-v4-flash → deepseek-v4-flash` | The child was created at the expected canonical path and returned its marker. |
| `collaboration.wait_agent` | **Failed** | `deepseek-v4-flash → deepseek-v4-flash` | In the latest local-mode test, native Codex missed a child that completed before the first wait. Rosetta name and argument reconstruction were correct. An earlier alias test succeeded. |
| `collaboration.list_agents` | **Success** | `deepseek-v4-flash → deepseek-v4-flash` | The expected completed resident child was returned. |
| `collaboration.send_message` | **Success** | `deepseek-v4-flash → deepseek-v4-flash` | The queued message reached the running child and produced the required child result. |
| `collaboration.followup_task` | **Success** | `deepseek-v4-flash → deepseek-v4-flash` | A second turn ran on the same completed child and produced the required marker. |
| `collaboration.interrupt_agent` | **Success** | `deepseek-v4-flash → deepseek-v4-flash` | The running child was interrupted and remained present in the collaboration state. |

The final `wait_agent` failure is classified as native Codex lifecycle
behavior, not a Rosetta translation failure or malformed upstream-model call.

## Not covered by a successful real-agent test

The existing result set does not establish a final status for:

- `tool_search`;
- `clock.sleep` and other unlisted Clock operations;
- Memories and Skills operations other than the rows above;
- other `web.run` commands such as `image_query`, `finance`, `weather`,
  `sports`, `time`, and `screenshot`;
- GitHub, MCP, App, and Connector Namespace tools;
- disabled legacy surfaces such as `shell_command` and `multi_agent_v1`.

These entries are **not tested**, rather than failed.

## Test definitions

- [Command execution](../../../tests/agent_workspace/command_execution/README.md)
- [Network search](../../../tests/agent_workspace/network_search/README.md)
- [Namespace tools](../../../tests/agent_workspace/namespace_tools/README.md)
- [Collaboration tools](../../../tests/agent_workspace/subagent_tools/README.md)
- [Built-in Code Mode tools](../../../tests/agent_workspace/builtin_tools/README.md)
- [Image generation and visual inspection](../../../tests/agent_workspace/image_generation/README.md)
