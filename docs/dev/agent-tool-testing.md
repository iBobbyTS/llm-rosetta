# Real-Agent Tool-Use Testing

Codex-Rosetta keeps deterministic agent workspaces under
`tests/agent_workspace`. They test whether a model uses exposed tools correctly
after protocol conversion. They are not benchmarks of reasoning, coding,
instruction following in general, or answer quality.

## Design principles

- Give the model one obvious operation and a fixed result marker.
- Make tool sequencing, not task discovery, the only meaningful variable.
- Require actual execution; source inspection is not an acceptable substitute.
- Separate initial command execution from polling or input intervention.
- Evaluate the rollout and Rosetta trace, not only the final answer.
- Keep templates immutable and run only disposable copies.

The first suite is
[`tests/agent_workspace/command_execution`](../../tests/agent_workspace/command_execution/README.md).
It covers a foreground command, delayed completion, one interactive input, and
two ordered interactive inputs.

The network-search suite is
[`tests/agent_workspace/network_search`](../../tests/agent_workspace/network_search/README.md).
It verifies that the agent selects the model-facing search surface, receives a
successful result containing an official Python documentation URL, and does not bypass the
tool with a shell command or browser automation.

## Runtime layout

Every invocation uses one repository-local run root:

```text
tmp/agent_testing_workspace/YYYYMMDDHHMM/
├── worktree/       # merged common files and one selected task
├── codex_home/      # isolated Codex configuration and sessions
├── gateway/         # copied Rosetta configuration and gateway process output
└── artifacts/       # Codex JSONL, stderr, paths, and evaluation notes
```

`YYYYMMDDHHMM` is local time and is the complete directory name. If it already
exists, wait for or choose another unused minute; never append a model, task,
suite, or tool name. The root is ignored by Git because copied gateway
configuration may contain credentials. On macOS, only the stream trace enabled
by the Web Admin **Gateway Logs** page is stored at
`/Volumes/RAMDisk/YYYYMMDDHHMM/rosetta-trace.jsonl`. All other files remain in
the repository-local run root.

## Evidence and pass criteria

For each run, retain:

- the selected task's `expected.json`;
- Codex CLI JSONL and stderr;
- the Codex rollout under the isolated `codex_home`;
- the isolated gateway process output under `gateway/`;
- the Web Admin Gateway Logs stream trace under the recorded Gateway Logs root;
- the upstream model actually observed in the trace.

A pass requires the exact success marker and the native call pattern declared
in `expected.json`. For continuation tasks, confirm that the returned session
identifier is reused. Restarting the scenario is a failure even if it produces
the same final marker.

For network-search tasks, confirm at least one model-facing search call, a
non-error search result satisfying the task, and the absence of prohibited
command or browser calls. Record whether the model used a namespace function,
a hosted tool, or a Rosetta-translated bridge.

Responses Lite models use Codex's standalone `web.run` extension instead of a
hosted Responses search tool. An isolated custom-provider test must retain its
Rosetta localhost base URL but identify the provider as `openai` so Codex
registers that extension; record the override as part of the test evidence.

When testing a Responses-to-Chat route, compare both layers:

- model-facing calls show the localized command interface;
- Codex-facing calls show the reconstructed native command-start or
  continuation interface.

This distinction makes the suite suitable for detecting cases where an initial
command is translated correctly but later polling or stdin intervention is not.

## Canonical procedure

Use the project skill
`.agents/skills/rosetta-codex-readme-test/SKILL.md`. Despite its stable skill
identifier, it now runs repository-owned agent task workspaces and no longer
depends on an external README repository.
