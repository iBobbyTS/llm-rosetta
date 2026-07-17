# Real-Agent Tool-Use Testing

Codex-Rosetta keeps deterministic agent workspaces under
`tests/live_agent`. They test whether a model uses exposed tools correctly
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
[`tests/live_agent/command_execution`](../../tests/live_agent/command_execution/README.md).
It covers a foreground command, delayed completion, one interactive input, and
two ordered interactive inputs.

The network-search suite is
[`tests/live_agent/network_search`](../../tests/live_agent/network_search/README.md).
It retains a basic search-surface comparison, then covers every model-facing
operation currently implemented by the local `web.run` bridge: domain-filtered
Tavily search and response length, scoped `turnXsearchY` static open with
`lineno`, sidecar-backed page `open`/`find`/`click` with `turnXfetchY`, PDF
`open`/`find`/`screenshot` with `turnXviewY`, and Python fixed-offset `time`.
The scenarios do not bypass the target surface with a shell, direct HTTP
client, or external browser tool. Its outer evaluator follows the suite's
`EVALUATION.md`, classifies the surface and local executor from Gateway Logs,
and writes `artifacts/evaluation.json`.

The context-compaction suite is
[`tests/live_agent/context_compaction`](../../tests/live_agent/context_compaction/README.md).
It forces one command result followed by a second model turn while Codex uses
an OpenAI-identified provider. It validates only the routing, wire,
persistence, and replay protocol.

Deterministic summary quality is one small two-provider scenario under
[`tests/live_agent/context_compaction_summary_quality`](../../tests/live_agent/context_compaction_summary_quality/README.md).
Its GPT and DeepSeek cells use byte-identical `TASK.md`, `scenario.py`, and
`QUERY.md` files. Phase 1 hides the eventual question, then the same thread and
model resumes with `QUERY.md` only after compaction. The test executor scores
only the fixed post-compaction values. GPT is routed to `Pixel (K12)` in the
copied test config, while DeepSeek remains `deepseek-v4-flash` on its sole
provider.

The Namespace-tools suite is
[`tests/live_agent/namespace_tools`](../../tests/live_agent/namespace_tools/README.md).
It gives the agent a fixed sequence of direct calls to `clock.curr_time`,
`memories.list`, and `skills.list`. It tests Namespace exposure,
Responses-to-Chat flattening/restoration, and local tool execution rather than
planning quality. The suite enables `current_time_reminder` and `memories`,
seeds an isolated memory root, and treats an unavailable app-server
orchestrator skill provider as a real `skills` Namespace failure.

The capability-exposure suite is
[`tests/live_agent/deferred_tool_search`](../../tests/live_agent/deferred_tool_search/README.md).
It provisions exactly three unrelated local candidates (archive proof, integer
addition, and color normalization) for each standalone-skill, standalone-MCP,
plugin-skill, or plugin-MCP surface. Tasks `01` through `03` are explicit
controls; tasks `04` through `07` use only natural-language intent and never
name the target capability. Evaluation separately records catalog exposure,
selection, skill-body access, deferred tool exposure, call, and consumed result
across the Codex rollout, source Responses request, and converted Chat request.
For code-mode models the source discovery surface is runtime `ALL_TOOLS`. On a
Responses-to-Chat route, Rosetta projects an ordinary `tool_search` Function
only when the live `exec` description advertises deferred nested tools; calling
it must round-trip as custom `exec` JavaScript that searches that request-local
Array. Generic matches still use raw `exec`. Exact matches for the three Node
REPL tools are a specialized exception: on the next request Rosetta validates
their declarations from the paired search history, exposes only the matched
ordinary Functions, and converts structured model arguments back to custom
`exec` with MCP text/image result forwarding. Search output admits only whole
matches within a 24,000-character serialized budget. Once a Node match is
projected, its declaration is replaced by a short projected-status marker only
in the model-facing history copy; unknown matches retain their full declaration.
Codex injects candidate metadata into the V8 runtime, so live evidence covers
projection, search, selection, call, and consumed result without a Gateway discovery cache. Browser,
authenticated apps, and real user or third-party capabilities remain outside
this deferred-tool suite and use their dedicated live tests.

For Responses-to-Chat profile routes, the converted `exec(input: string)`
function must remain model-callable whenever the Codex description advertises
runtime-only deferred nested tools. Static exec projections alone are not a
replacement for `ALL_TOOLS`, because those candidates do not exist in the
source request. Chat `exec` calls must round-trip back to Codex custom-tool raw
JavaScript input.

The Subagent-tools suite is
[`tests/live_agent/subagent_tools`](../../tests/live_agent/subagent_tools/README.md).
It isolates all six `collaboration` Functions into separate tasks for
`spawn_agent`, `wait_agent`, `list_agents`, `send_message`, `followup_task`,
and `interrupt_agent`. Supporting lifecycle calls may prepare or verify a
scenario, but each task has exactly one core Function so failures remain
attributable. The suite enables `multi_agent_v2` and evaluates canonical child
paths, mailbox delivery, idle-agent follow-up, completion notifications, and
resident state after interruption. It measures tool-call behavior rather than
delegation judgment or subagent answer quality. Its provider matrix uses exact
case-sensitive IDs and display names `custom` and `OpenAI`; lowercase `openai`
does not satisfy the OpenAI identity cell.

The built-in Code Mode suite is
[`tests/live_agent/builtin_tools`](../../tests/live_agent/builtin_tools/README.md).
It fixes the provider display name to `OpenAI` and the model catalog shape to
`gpt-5.6-sol`, then exercises a yielded `exec` cell through top-level `wait`,
two projected `update_plan` calls, one grouped localized file workflow
(`Glob`, `Grep`, `Read`, two `Edit` calls, and create-file `Write`) where
`apply_patch` remains model-hidden, projected `view_image`, and one grouped
`get_goal`/`create_goal`/`update_goal` lifecycle. A separate visual-recognition
task verifies that projected `view_image` returns real image content to a
vision-capable upstream model rather than only proving that Codex can open the
fixture.

The image-generation suite is
[`tests/live_agent/image_generation`](../../tests/live_agent/image_generation/README.md).
It may run only after projected `view_image` transport and deterministic visual
recognition have passed for the same visual model and route. The task generates
the exact scene `草坪上一只狗在跑`, uses the saved result path for one projected
`view_image` call, and asks the tested model for a one-sentence Chinese visual
description. The outer developer or development agent, not the tested model,
decides whether that description contains a dog, grass or a lawn, and running.
The suite also requires a Profile-configured OpenAI-compatible Images endpoint;
it does not measure artistic quality.
The network-search suite records Tesseract fallback as container-test coverage
rather than an agent fixture because the deterministic public PDF contains
embedded text. The agent PDF task still exercises the complete model-facing
`screenshot` operation and verifies render metadata plus returned text.

`request_user_input` is not represented by a `codex exec` task. Codex 0.144.1
explicitly rejects that server request in exec mode. Codex's own integration
test drives it through the app-server protocol, captures the
`ToolRequestUserInput` JSON-RPC request, sends a deterministic answer, and only
then waits for turn completion. A future real-agent fixture must add an
app-server JSON-RPC runner; `auto_resolution_ms` does not make the current exec
runner capable of answering the request.

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

For network-search tasks, confirm every required model-facing operation, its
non-error result, scoped references where applicable, and the absence of
prohibited command, direct-HTTP, or external-browser calls. The final
`web.run` versus `web_search` classification must come from Rosetta Gateway
Logs. Do not infer it from the Codex CLI item type because Codex displays both
paths as `web_search`. Responses-to-Chat localization and Tavily execution
remain a `web_search` surface; record Tavily separately as the executor.
Sidecar-backed browser/PDF execution behind `web.run` is a target path, not a
fallback. Missing or inconclusive Gateway Logs make the surface `ambiguous`
and the run cannot pass.

For context-compaction tasks, require a genuine `compaction_trigger` input item
in the Gateway Logs trace. Classify the run as completed, error reproduced, or
not triggered according to the suite README and retain the exact compact
response item types. A source listing or log message containing the same text
does not prove that Codex issued a compaction request. Count both the canonical
`compaction` item and the accepted `compaction_summary` wire alias.
The test executor (including a coding agent or developer) follows the selected
suite's `EVALUATION.md` and writes `artifacts/evaluation.json`. Protocol tests
report the end-to-end compaction result and method without scoring text.
Summary-quality tests first require exactly one command and one compaction,
then submit the previously unseen query through a same-thread, same-model
resume. Resume must not run another command or compaction. Only then does the
test executor score deterministic fact retention; the tested model never
evaluates its own summary. Do not confuse context compaction with HTTP
compression such as zstd.
When comparing provider identities, `openai` is expected to select remote v2,
while a provider whose id and display name are `custom` is expected to run a
normal no-tools Responses turn that produces a local summary message.

For Namespace-tool tasks, verify every required native Namespace call and
successful result in the isolated rollout. Use Gateway Logs to record the
model-facing call name: native Responses routes retain Namespace calls, while
Responses-to-Chat routes expose unique flattened names such as
`memories.list` and Rosetta must reconstruct the original Namespace before
Codex executes it. A textual mention, a shell substitute, or a local file read
does not count.

For deferred-plugin tasks, preserve the marketplace-add, plugin-add, plugin-list,
and MCP-list JSON artifacts, but do not treat provisioning as execution proof.
The rollout must show `tool_search`, the loaded plugin MCP call, and its marker
result. If installation succeeds but no plugin MCP tool enters the request,
classify it as `not_exposed`; do not replace the plugin with a top-level manual
MCP registration because that would bypass the contract under test.

For Subagent-tool tasks, evaluate the one core `collaboration` Function named
by `expected.json` plus the scenario-specific lifecycle proof. Spawn and wait
calls used to prepare or clean up another scenario are supporting evidence, not
additional target coverage. Run every scenario separately; a child or mailbox
state created by one scenario must never be reused by another.

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
