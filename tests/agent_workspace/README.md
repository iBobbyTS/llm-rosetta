# Agent Tool-Use Workspaces

This directory contains small, deterministic workspaces for testing whether an
agent selects and sequences tools correctly through Codex-Rosetta.

These are not model-quality, reasoning, coding, or general agent benchmarks.
Each task deliberately has an obvious instruction, a tiny local program, and a
fixed success marker. Evaluation should inspect tool calls and process/session
handling rather than prose quality.

## Suites

- [`command_execution`](command_execution/README.md): starting commands,
  polling a running process, and sending input to an existing process.
- [`network_search`](network_search/README.md): selecting network search and
  receiving a usable result without shell or browser fallbacks; its evaluator
  uses Rosetta Gateway Logs to distinguish `web.run` from hosted
  `web_search`.
- [`context_compaction`](context_compaction/README.md): forcing a second
  model turn across Codex remote compaction and recording whether an
  OpenAI-identified provider returns a valid compaction item.
- [`namespace_tools`](namespace_tools/README.md): directly exercising the
  `clock`, `memories`, and `skills` Namespace tools.
- [`subagent_tools`](subagent_tools/README.md): six isolated lifecycle
  scenarios covering every `collaboration` Function: spawn, wait, list,
  message, follow-up, and interrupt.
- [`builtin_tools`](builtin_tools/README.md): fixed OpenAI-identified,
  `gpt-5.6-sol`-equivalent Code Mode scenarios for top-level `wait`, projected
  plan/file/image tools, the three-tool Goal lifecycle, and actual upstream
  visual recognition. It also records why `request_user_input` cannot be
  driven by the current non-interactive `codex exec` runner.

When no suite is specified, start with `command_execution/01`. Suite README
and EVALUATION files, rather than the runner skill, define task order, Codex
configuration, feature flags, and pass criteria.

## Real-provider defaults

The default third-party comparison model is `deepseek-v4-flash`; the native
GPT comparison model is `gpt-5.6-terra`. A normal real-provider comparison uses
both. Always confirm the Gateway provider and actual upstream model in Rosetta
Gateway Logs: a Codex-facing alias by itself is not evidence of the upstream
route.

Every model, provider identity, and task matrix cell requires a separate
timestamp run root. Never reuse the Codex home, copied Gateway configuration,
process state, or workspace across cells.

## Execution model

Never run a task in place. Copy `common/` and exactly one numbered task into:

```text
tmp/agent_testing_workspace/YYYYMMDDHHMM/worktree/
```

The same timestamp directory owns the isolated Codex home, copied gateway
configuration, Gateway process output, and Codex CLI output. On macOS, only the
Web Admin Gateway Logs stream trace is written to the matching timestamp
directory under `/Volumes/RAMDisk`. The repository runtime directory name must
remain exactly a 12-digit local timestamp; do not add a model, suite, task, or
tool name.

The canonical procedure is maintained in
`.agents/skills/rosetta-codex-readme-test/SKILL.md` and documented for
developers in [`docs/dev/agent-tool-testing.md`](../../docs/dev/agent-tool-testing.md).
