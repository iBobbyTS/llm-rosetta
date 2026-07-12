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
tool with a shell command or browser automation. Its outer evaluator follows
the suite's `EVALUATION.md` and writes `artifacts/evaluation.json`.

The context-compaction suite is
[`tests/agent_workspace/context_compaction`](../../tests/agent_workspace/context_compaction/README.md).
It forces one command result followed by a second model turn while Codex uses
an OpenAI-identified provider. Its result distinguishes a valid remote
compaction response from the known zero-output-item failure; it does not score
the generated summary.

The Namespace-tools suite is
[`tests/agent_workspace/namespace_tools`](../../tests/agent_workspace/namespace_tools/README.md).
It gives the agent a fixed sequence of direct calls to `clock.curr_time`,
`memories.list`, `skills.list`, `collaboration.spawn_agent`, and
`collaboration.wait_agent`. It tests Namespace exposure, Responses-to-Chat
flattening/restoration, and local tool execution rather than planning or
subagent quality. The suite enables `current_time_reminder`, `memories`, and
`multi_agent_v2`, seeds an isolated memory root, and treats an unavailable
app-server orchestrator skill provider as a real `skills` Namespace failure.

The GPT relay provider-identity suite is
[`tests/integration/gpt_relay`](../../tests/integration/gpt_relay/README.md).
It sends the same real relay/model through non-OpenAI and `OpenAI` Codex
provider identities, then compares sequential reasoning-summary delivery,
internal item metadata, Zstd request compression, and current-model compact
fallback. Synthetic Codex-backend header auth is used only to activate the
Codex-side Zstd/fallback gates; the capture proxy replaces authentication and
still requires the selected real relay to complete the request. Results label
that distinction explicitly and never count a mock response as relay evidence.

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
command or browser calls. The final `web.run` versus `web_search`
classification must come from Rosetta Gateway Logs. Do not infer it from the
Codex CLI item type because Codex displays both paths as `web_search`.
Responses-to-Chat localization and Tavily execution remain a `web_search`
surface; record Tavily separately as the executor. Missing or inconclusive
Gateway Logs make the surface `ambiguous` and the run cannot pass.

For context-compaction tasks, require a genuine `compaction_trigger` input item
in the Gateway Logs trace. Classify the run as completed, error reproduced, or
not triggered according to the suite README and retain the exact compact
response item types. A source listing or log message containing the same text
does not prove that Codex issued a compaction request. Count both the canonical
`compaction` item and the accepted `compaction_summary` wire alias.
The parent agent follows the suite's `EVALUATION.md`, writes
`artifacts/evaluation.json`, and explicitly reports the end-to-end compaction
result and Codex compaction method. Do not confuse context compaction with HTTP
compression such as zstd.
When comparing provider identities, `openai` is expected to select remote v2,
while a provider whose id and display name are `custom` is expected to run a
normal no-tools Responses turn that produces a local summary message.

For Namespace-tool tasks, verify every required native Namespace call and
successful result in the isolated rollout. Use Gateway Logs to record the
model-facing call name: native Responses routes retain Namespace calls, while
Responses-to-Chat routes expose unique flattened names such as
`memories__list` and Rosetta must reconstruct the original Namespace before
Codex executes it. A textual mention, a shell substitute, or a local file read
does not count. The collaboration check requires both spawning a child and
waiting until the child returns the fixed marker.

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
