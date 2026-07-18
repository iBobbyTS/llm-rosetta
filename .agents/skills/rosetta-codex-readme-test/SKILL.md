---
name: rosetta-codex-readme-test
description: Run controlled Codex-through-Codex-Rosetta tool-use tasks copied from tests/live_agent into an isolated repository-local runtime. Use when Codex needs to compare model-facing and native tool calls, validate command execution or process continuation, identify the resulting Codex session and Rosetta trace, or smoke-test real providers without measuring general model quality.
---

# Rosetta Codex Agent Tool Test

## Purpose

Run repeatable real-agent tests from `tests/live_agent` through
`codex-rosetta-gateway`. Usually test only whether the model selects and
sequences tools correctly. Do not score reasoning, coding skill, prose, or
general agent quality. The deterministic context-compaction summary-quality
suite is the narrow exception: score only its fixed fact-retention fields after
its protocol preconditions pass.

Never execute a template in place. Copy one task into a disposable workspace
under this repository. Keep temporary configuration and Codex sessions inside
the repository-local run root. On macOS, only the stream trace enabled by the
Web Admin **Gateway Logs** page belongs on a RAM Disk.

## Runtime Contract

- Resolve the repository root with `git rev-parse --show-toplevel`.
- Select the suite and task from `tests/live_agent` as requested, then read
  `tests/live_agent/runtime-contract.json`, that suite's `README.md`, optional
  `EVALUATION.md`, and task `expected.json` before configuring the run. Feature
  flags, provider identities, task order, and result fields live there rather
  than in this skill. Suites may define capability roles and default model
  choices, but must not require a CLI model override for an ordinary cell.
- Check the task's runner and auth prerequisites before creating a CLI cell.
  If a suite requires an app-server orchestrator, GUI Browser, or a Codex auth
  path unavailable to the isolated local-mode bearer Provider, follow its
  explicit unsupported classification. Do not force it through `codex exec`
  and attribute the missing surface to the model.
- Keep local filesystem Skills and orchestrator-owned Skills as separate test
  surfaces. A local Skill is discovered from the copied `.agents/skills` tree,
  appears as catalog metadata in developer context, and has its full body
  injected after explicit selection; it does not use `skills.list` or
  `skills.read`. Those Namespace tools are only for orchestrator-owned
  resources and require the app-server runner, `[orchestrator.skills]`, a
  provisioned orchestrator provider, and no attached local execution
  environment.
- Unless the suite explicitly tests a different capability role, use
  `gpt-5.6-sol` as the native GPT shape reference, `deepseek-v4-flash` for
  third-party non-multimodal cells, and `mimo-v2.5` for third-party multimodal
  cells. Put the selected default in the isolated `config.toml`; do not force
  it with `codex exec -m`.
- Every Gateway-backed CLI or app-server cell uses the gateway's local mode
  with both ChatGPT OAuth login state and the managed Provider's
  `experimental_bearer_token`. OAuth supplies Codex identity and passes
  auth-gated capability checks; the bearer token takes provider-auth
  precedence and routes actual model requests through the isolated Gateway.
  Neither credential alone is a valid matrix cell. Let local mode generate and
  own the isolated `model_catalog.json`, root `model_catalog_json`, and managed
  Provider. Use Provider ID `codex_rosetta` with the exact case-sensitive
  display name `OpenAI`. Do not define suite-specific provider IDs or any
  provider whose display name is `openai`, `custom`, or another spelling.
- Source every Gateway credential needed by a test, including model-provider,
  Images, Tavily, and sidecar credentials, only from the user's
  `~/.config/codex-rosetta-gateway` directory. Source Codex login state only
  from `/Users/ibobby/.codex-multi-2/auth.json`. Copy both classes only into
  the Git-ignored isolated run root. They must never appear in tracked
  fixtures, patches, reports, extracted log evidence, or Git history.
- Use `tmp/agent_testing_workspace/YYYYMMDDHHMM` as the runtime root, with
  local time.
- Use `/Volumes/RAMDisk/YYYYMMDDHHMM` as the macOS Gateway Logs root.

The timestamp must be exactly 12 digits and the complete directory name. Do not
include a model, task, suite, protocol, or tool name. If that minute already
exists, stop and use another unused minute rather than adding a suffix.

## Create The Isolated Run

1. Resolve and validate the selected template:

   ```bash
   ROOT=$(git rev-parse --show-toplevel)
   SUITE="$ROOT/tests/live_agent/<suite>"
   TASK_ID=<task-id>
   test -f "$SUITE/README.md"
   test -f "$SUITE/$TASK_ID/TASK.md"
   test -f "$SUITE/$TASK_ID/expected.json"
   ```

2. Create a timestamp-only run root and merge the shared fixture plus exactly
   one task into `worktree`:

   ```bash
   RUN_ID=$(date +%Y%m%d%H%M)
   RUN_ROOT="$ROOT/tmp/agent_testing_workspace/$RUN_ID"
   test ! -e "$RUN_ROOT"
   mkdir -p "$RUN_ROOT/worktree" "$RUN_ROOT/codex_home" "$RUN_ROOT/gateway" "$RUN_ROOT/artifacts"
   cp -R "$SUITE/common/." "$RUN_ROOT/worktree/"
   cp -R "$SUITE/$TASK_ID/." "$RUN_ROOT/worktree/"
   ```

3. Select the Web Admin **Gateway Logs** root. On macOS, prefer an existing
   `/Volumes/RAMDisk`. If it is not mounted, create a 1 GiB HFS+ RAM Disk named
   `RAMDisk`:

   ```bash
   if ! mount | grep -Fq "on /Volumes/RAMDisk "; then
     diskutil erasevolume HFS+ 'RAMDisk' "$(hdiutil attach -nobrowse -nomount ram://2097152)"
   fi
   GATEWAY_LOG_ROOT="/Volumes/RAMDisk/$RUN_ID"
   mkdir -p "$GATEWAY_LOG_ROOT"
   printf '%s\n' "$GATEWAY_LOG_ROOT" >"$RUN_ROOT/artifacts/gateway-log-root.txt"
   ```

   The mount command above is the non-nested equivalent of:

   ```bash
   diskutil erasevolume HFS+ 'RAMDisk' `hdiutil attach -nobrowse -nomount ram://2097152`
   ```

   `ram://2097152` creates 2,097,152 512-byte sectors, or 1 GiB. Never erase an
   already-mounted volume. On non-macOS systems, or when RAM Disk creation is
   unavailable, use `GATEWAY_LOG_ROOT="$RUN_ROOT/artifacts"` and record the
   fallback in the final report. This location is only for the Gateway Logs
   stream trace. All other files stay under `RUN_ROOT`.

4. Confirm the secret destinations are ignored, then copy the user's gateway
   configuration into the run root. Every Gateway credential needed by the
   selected suite must come from `~/.config/codex-rosetta-gateway`; do not read
   a model API key, Images token, Tavily key, or sidecar token from another
   source. Never edit or stop the user's main gateway:

   ```bash
   for SECRET_DEST in \
     "$RUN_ROOT/gateway/config.jsonc" \
     "$RUN_ROOT/codex_home/auth.json"; do
     git -C "$ROOT" check-ignore -q "$SECRET_DEST" || exit 1
   done
   cp "$HOME/.config/codex-rosetta-gateway/config.jsonc" "$RUN_ROOT/gateway/config.jsonc"
   ```

   Edit only the copied config. Choose a free localhost port and set:

   ```json
   {
     "server": {
       "host": "127.0.0.1",
       "port": 18765,
       "stream_trace": {
         "enabled": true,
         "filter": "<tested-model>",
         "path": "<GATEWAY_LOG_ROOT>/rosetta-trace.jsonl"
       }
     }
   }
   ```

   Preserve all providers, model groups, profiles, keys, and unrelated server
   settings from the copied config. The trace path must be absolute. Do not log
   or print credentials while editing it. Never use `git add -f` on the run
   root or move a copied secret into a tracked path.

5. Create `RUN_ROOT/codex_home/config.toml` pointing to the isolated gateway.
   Use a client API key from the copied gateway config as the bearer token, but
   never include that value in reports. Use the model, fixed Provider identity,
   feature flags, and any diagnostic limits required by the selected suite's
   own guide. Seed the managed local-mode Provider shape as:

   ```toml
   model_provider = "codex_rosetta"
   model = "<default-model>"
   sandbox_mode = "danger-full-access"
   approval_policy = "never"
   model_reasoning_effort = "medium"

   [model_providers.codex_rosetta]
   name = "OpenAI"
   wire_api = "responses"
   requires_openai_auth = true
   base_url = "http://127.0.0.1:<port>/v1"
   experimental_bearer_token = "<copied-gateway-client-key>"

   [projects."<RUN_ROOT>/worktree"]
   trust_level = "trusted"
   ```

   The provider's `experimental_bearer_token` is a request credential, not a
   Codex login. Do not treat it as proof that an auth-gated standalone tool is
   visible. For every Gateway-backed cell, use the user-authorized ChatGPT
   OAuth source at `/Users/ibobby/.codex-multi-2/auth.json`:

   ```bash
   AUTH_SOURCE=/Users/ibobby/.codex-multi-2/auth.json
   jq -e '.auth_mode == "chatgpt" and (.tokens | type == "object")' \
     "$AUTH_SOURCE" >/dev/null
   install -m 600 "$AUTH_SOURCE" "$RUN_ROOT/codex_home/auth.json"
   CODEX_HOME="$RUN_ROOT/codex_home" codex login status
   ```

   The status must identify ChatGPT authentication before invoking the tested
   model. Never print, trace, or copy token values into evaluation artifacts.
   Keep `experimental_bearer_token` on `codex_rosetta`: after the OAuth state
   passes Codex's exposure gate, provider-auth precedence still routes model
   and Images API requests through the isolated Gateway.

   Before the tested turn, write `artifacts/runtime-auth.json` without secret
   values. It must record the execution mode, both source paths, the observed
   ChatGPT login class, local-mode state, Provider ID/display name,
   `requires_openai_auth`, bearer-token presence as a boolean, and the
   localhost Gateway base URL. It must not contain OAuth tokens, API keys,
   bearer values, cookies, authorization headers, or copied configuration.

## Run One Task

1. Launch a separate gateway from the current checkout and copied config.
   Capture its process output, PID, and configuration under the repository-local
   run root. Gateway process stdout/stderr are not Web Admin Gateway Logs and
   must not be written to the RAM Disk:

   ```bash
   codex-rosetta-gateway --config "$RUN_ROOT/gateway" --codex-home "$RUN_ROOT/codex_home" \
     --host 127.0.0.1 --port 18765 --no-banner --local-mode \
     --confirm-clear-existing-catalog \
     >"$RUN_ROOT/gateway/stdout.log" 2>"$RUN_ROOT/gateway/stderr.log" &
   GATEWAY_PID=$!
   printf '%s\n' "$GATEWAY_PID" >"$RUN_ROOT/gateway/pid"
   ```

   Poll `/v1/models` with the copied client key until ready. Do not use or
   modify the user's main gateway.

2. Read `TASK.md` as the exact prompt. Do not paraphrase or add hints. Read the
   suite `README.md`, optional `EVALUATION.md`, and task `expected.json` for
   configuration, timeout, and evidence requirements. They are guidance for
   the test executor (including a coding agent or developer) and must not be
   inserted into the tested model's prompt.

3. Run Codex non-interactively with the isolated home and bounded duration:

   ```bash
   PROMPT=$(<"$RUN_ROOT/worktree/TASK.md")
   CODEX_HOME="$RUN_ROOT/codex_home" codex exec --json --skip-git-repo-check \
     -C "$RUN_ROOT/worktree" "$PROMPT" \
     >"$RUN_ROOT/artifacts/codex.jsonl" \
     2>"$RUN_ROOT/artifacts/codex.stderr"
   ```

   Enforce the task's `timeout_seconds` externally if the runner supports it.
   A timeout is a test failure, not permission to restart the scenario.

4. Stop only the isolated gateway PID after Codex exits, including failure and
   timeout paths. Because tracing exists only in the copied config, no global
   logging setting needs restoration.

## Evaluate Tool Use

Use three bounded evidence sources:

1. `RUN_ROOT/artifacts/codex.jsonl`: exit status, thread id, final message, and
   visible tool-call sequence.
2. `codex_home/sessions`: locate the rollout by thread id or timestamp. Search
   filenames and extract only relevant tool calls/results; never dump a whole
   rollout into context.
3. `GATEWAY_LOG_ROOT/rosetta-trace.jsonl`: verify the actual upstream model,
   converted model-facing tool calls, reconstructed Codex-facing calls, and
   successful stream completion. This is the only artifact stored on the RAM
   Disk. Filter by model, request id, thread id, and timestamp.

Also validate `RUN_ROOT/artifacts/runtime-auth.json` against
`tests/live_agent/runtime-contract.json` before interpreting model behavior.
Missing dual-auth evidence or a model request that bypasses the isolated
Gateway invalidates the cell as a runner/configuration failure.

For every upstream request in the run, inspect its usage record. Do not
calculate or report a prompt-cache hit rate. Group interleaved parent and child
requests by conversation or prompt-cache key before choosing an adjacent
request. For every non-first request in that group, calculate exactly this
signed adjacent-request delta:

```text
current.cached_input_tokens
- (previous.input_tokens
   + previous.output_tokens)
```

Do not add a separate carried-token term. Tool results, injected conversation
items, compaction output, and other new content belong in the bounded body
comparison, not in the arithmetic. Record the current request id, the previous
request's input and output tokens, the current cached input tokens, and the
computed signed delta. The first request has no adjacent-request delta and
should be recorded only as the baseline usage.

The previous output is already subtracted by this formula. It is still useful
to decompose a negative result algebraically: if current cached input is 82
tokens below previous input and previous output is 1,463 tokens, the delta is
`-82 - 1463 = -1545`. This explanation does not subtract output a second time.

When the absolute delta is greater than 200 tokens, inspect the bounded actual
request content and explain the cause. At minimum compare the prompt-cache key,
model and route, tool definitions, stable instructions, whether the previous
messages/items remain an exact prefix, and the newly added or rewritten
conversation items. Distinguish cache-prefix breakage from expected changes
such as model switching, compaction, tool-result insertion, token-block
alignment, or deliberate instruction/tool mutations. Missing usage or an
unexplained discontinuity must be called out instead of silently omitted.

Compare the evidence with `worktree/expected.json` and apply the field meanings
and output schema defined by the selected suite's README/EVALUATION guide. Do
not infer a suite-specific pass condition from this skill.

For compaction suites, inspect the Gateway request-log profile in addition to
stream tracing. Rosetta may recognize a valid in-band trigger and return its
compaction response before the normal stream-trace request stage; a bounded
profile containing `compaction_mode` and `compaction_reason`, combined with
mapping or native-item persistence and installed follow-up evidence, is not a
rollout-only signal.

For `context_compaction_summary_quality`, read canonical expected facts from
the suite root, not the isolated worktree, and verify the two model cells use
byte-identical `TASK.md`, `scenario.py`, and `QUERY.md` bytes before running
either cell. Run phase 1 from `TASK.md`, retain its thread id, and only after
the required compaction resume that same thread and model with `QUERY.md`.
Capture phase 1 and resume output separately. Never place the evaluator's
expected values in either tested-model prompt.

The outer evaluating agent decides success by the task's core objective, not by
perfect compliance with every incidental instruction. Mark the task successful
when the expected scenario result is reached and the tool behavior central to
that task is demonstrated, as long as the run does not materially diverge from
the intended test. Treat extra source reads, extra explanation, harmless extra
polls, or other small unnecessary calls as recorded deviations rather than
automatic failures.

Fail the task when the core behavior is missing or bypassed. Examples include
never executing the scenario, restarting the scenario instead of continuing
the returned session, sending required input to a different session, obtaining
the marker only by inference, modifying unrelated files, or returning the wrong
scenario result. `expected.json` is evidence guidance for this judgment; its
counts are not a rigid benchmark when the core tool sequence is still clear.

When the suite requires `artifacts/evaluation.json`, write exactly the schema
specified by its `EVALUATION.md`. Keep all extracted evidence bounded and
credential-free.

## Real Provider Matrix

When a suite guide requests a model matrix, create a separate timestamp run
root for every model and task. Never reuse a Codex home, copied gateway config,
process state, or workspace across cells.

For every cell, record:

- model, fixed `codex_rosetta`/`OpenAI` provider identity, and task id;
- credential-free runtime-auth evidence proving ChatGPT OAuth plus Provider
  bearer local mode, without recording either credential value;
- Codex exit status and exact final marker;
- thread id and rollout path;
- Rosetta trace path and observed upstream model;
- terminal stream shape and any warning that changes interpretation;
- per-request input/output/cached-input tokens, each non-first request's signed
  adjacent-request cache delta,
  and the bounded request-content analysis required when its absolute value is
  greater than 200;
- every additional measurement required by the suite guide.

## Safety Rules

- Work only inside `tmp/agent_testing_workspace/<timestamp>` after copying the
  selected template.
- Do not run agent tasks in the repository source tree or any external project.
- Do not edit the canonical files under `tests/live_agent` during a run.
- Do not alter, reload, kill, or reuse the user's main gateway.
- Keep the copied gateway config and Codex home under the run root.
- On macOS, write only the Web Admin **Gateway Logs** stream trace under
  `/Volumes/RAMDisk/<timestamp>`; do not use `/Volumes/RAM Disk` with a space.
- Keep Gateway process stdout/stderr, Codex stdout/stderr, Codex sessions,
  copied configuration, and all other artifacts under `RUN_ROOT`.
- Never erase or remount an existing `/Volumes/RAMDisk`; mount it only when the
  path is not already an active volume.
- Do not read whole session or trace files into context.
- Redact API keys, bearer tokens, cookies, and authorization headers.
- Copy Gateway credentials only from `~/.config/codex-rosetta-gateway` and
  Codex OAuth only from `/Users/ibobby/.codex-multi-2/auth.json`; keep both
  under the ignored run root and never allow either into Git history.
- Preserve completed run artifacts unless the user explicitly requests
  deletion. Do not use `git restore`, `git reset`, or cleanup commands for runs.

## Final Report

Report the model, provider identity, task id, exit status, observed marker,
thread id, rollout path, trace path, observed upstream model, and any warning
affecting interpretation. Classify each run as `success`,
`success with deviations`, or `failure`, and briefly separate minor deviations
from failures of the core objective. State explicitly what the selected suite
measures. The summary-quality suite measures only deterministic fact retention
after compaction; it is one small regression scenario, not a general
model-quality benchmark. Report both phase exit statuses, the phase-1 marker,
same-thread resume evidence, and any command or compaction during resume.
Include every non-first request's adjacent-request cache-delta calculation and
explicitly analyze the bounded request content whenever the absolute delta is
greater than 200. Do not report cache hit rates or replace the per-request
calculations with one aggregate cache number.

Include every additional field required by the selected suite's guide. When an
evaluation artifact is required, the final report must agree with it.
