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
  receiving a usable result without shell or browser fallbacks.

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
