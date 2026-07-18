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
- [`network_search`](network_search/README.md): selecting network search plus
  the implemented `web.run` search, static-page, browser-navigation, PDF, and
  fixed-offset time operations without shell or external-browser fallbacks;
  its evaluator uses Rosetta Gateway Logs to distinguish `web.run` from hosted
  `web_search` and to prove which local executor ran.
- [`context_compaction`](context_compaction/README.md): forcing a second
  model turn across Codex remote compaction and validating only its routing,
  wire, persistence, and replay protocol.
- [`context_compaction_summary_quality`](context_compaction_summary_quality/README.md):
  running one deterministic coding-handoff scenario through GPT and DeepSeek
  with byte-identical phase-1 and post-compaction resume prompts; evaluation
  belongs to the test executor.
- [`namespace_tools`](namespace_tools/README.md): directly exercising the
  locally available `clock` and `memories` Namespace tools through ordinary
  `codex exec`.
- [`local_skills`](local_skills/README.md): verifying filesystem Skill catalog
  discovery and explicit Skill-body injection without `skills.list` or
  `skills.read`.
- [`orchestrator_skills`](orchestrator_skills/README.md): exercising the
  orchestrator-owned `skills.list` and `skills.read` resource path through an
  app-server runner with no attached local execution environment.
- [`deferred_tool_search`](deferred_tool_search/README.md): installing a local
  three-candidate skill/plugin/MCP fixture catalog, pairing explicit controls
  with natural-language discovery, and verifying contextual exposure plus
  deferred invocation.
- [`subagent_tools`](subagent_tools/README.md): six isolated lifecycle
  scenarios covering every `collaboration` Function: spawn, wait, list,
  message, follow-up, and interrupt.
- [`builtin_tools`](builtin_tools/README.md): OpenAI-identified Code Mode
  scenarios using `gpt-5.6-sol` as the reference shape for top-level `wait`,
  projected plan/file/image tools, the three-tool Goal lifecycle, and actual
  upstream visual recognition. It also records why `request_user_input` cannot
  be driven by the current non-interactive `codex exec` runner.
- [`image_generation`](image_generation/README.md): a gated visual-model
  scenario that generates an image, opens the saved artifact through projected
  `view_image`, and leaves semantic agreement with the requested scene to the
  outer developer or agent evaluator.
- [`browser_use`](browser_use/README.md): a Codex GUI app-only, main-task-only
  live test for the bundled in-app Browser. It must use the `Browser` plugin and
  its `browser:control-in-app-browser` skill; Codex CLI, subagents, Chrome, and
  substitute browser-control surfaces are prohibited.

When no suite is specified, start with `command_execution/01`. Suite README
and EVALUATION files, rather than the runner skill, define task order, Codex
configuration, feature flags, and pass criteria.

## Shared local-mode authentication contract

Every Gateway-backed CLI or app-server cell must follow
[`runtime-contract.json`](runtime-contract.json): use Gateway local mode with
Provider ID `codex_rosetta`, display name exactly `OpenAI`, ChatGPT OAuth copied
from `/Users/ibobby/.codex-multi-2/auth.json`, and the managed Provider's
`experimental_bearer_token`. OAuth supplies Codex identity and passes
auth-gated capability checks; the bearer token is the actual provider request
credential and keeps model traffic on the isolated localhost Gateway. Neither
OAuth-only nor bearer-only execution is a valid matrix cell.

All Gateway configuration and credentials required by a test, including model
API keys, Images credentials, Tavily keys, and sidecar tokens, must be copied
only from `~/.config/codex-rosetta-gateway` into the ignored timestamp run
root. The OAuth file must likewise be copied only into that run root. These
files and every contained value are forbidden from tracked fixtures, patches,
reports, log excerpts, staging, and Git history. Each cell writes only a
credential-free `artifacts/runtime-auth.json` proving source paths, login
class, local-mode state, Provider identity, bearer presence, and localhost
routing. The GUI-only `browser_use` suite is not Gateway-backed and remains the
explicit exception.

`browser_use` is an explicit exception to the CLI-oriented execution model
below. Run it only in the Codex GUI app by following its own README; never pass
it to the repository `rosetta-codex-readme-test` runner. Its GUI executor writes
only `execution.json` and must not read Gateway/session logs or judge itself;
the user hands the result to a new judge-agent session for `evaluation.json`.
Each run uses one non-reused
`.agent-work/live-agent-test/{YYYYMMDD-HHMM}` directory shared only by that
executor/judge pair.

`orchestrator_skills` is runner-gated because local `codex exec` suppresses
orchestrator-owned Skills. Local filesystem Skills remain available to
`codex exec` and are tested separately by `local_skills`; they do not use the
`skills` Namespace. The app-server orchestrator cell still uses the shared
OAuth-plus-bearer Gateway contract, but must not attach a local execution
environment. `image_generation` additionally verifies the auth-gated tool
surface; bearer-only execution does not satisfy that exposure check. Follow
each suite README and report its explicit unsupported status instead of
starting an invalid cell.

## Real-provider defaults

Use `gpt-5.6-sol` as the default native GPT model and model-shape reference.
Use `deepseek-v4-flash` for third-party non-multimodal tests and `mimo-v2.5`
for third-party multimodal tests. For context-compaction protocol and
summary-quality provider cells, route GPT to `Pixel (K12)` in the copied test
config. A normal non-multimodal real-provider comparison uses the GPT and
DeepSeek defaults. Always confirm the Gateway provider and actual upstream
model in Rosetta Gateway Logs: a Codex-facing alias by itself is not evidence
of the upstream route.

These are defaults, not hard requirements embedded in task contracts. Select
the default through the isolated `config.toml`; ordinary suite commands must
not pass a CLI `-m` override. Prefer the copied gateway's local mode and retain
its generated catalog. Every eligible CLI suite uses the managed Provider ID
`codex_rosetta` with display name exactly `OpenAI`; do not define suite-specific
Provider IDs or lowercase/alternate display names. A deliberate same-thread
model-switch cell may select its target model explicitly because the switch
itself is the protocol under test. Record any model substitution and verify the
observed request/tool shape against the `gpt-5.6-sol` reference.

Every model and task matrix cell requires a separate
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
