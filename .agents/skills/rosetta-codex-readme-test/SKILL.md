---
name: rosetta-codex-readme-test
description: Run controlled Codex-through-Codex-Rosetta tool-use tasks copied from tests/agent_workspace into an isolated repository-local runtime. Use when Codex needs to compare model-facing and native tool calls, validate command execution or process continuation, identify the resulting Codex session and Rosetta trace, or smoke-test real providers without measuring general model quality.
---

# Rosetta Codex Agent Tool Test

## Purpose

Run repeatable real-agent tests from `tests/agent_workspace` through
`codex-rosetta-gateway`. Test only whether the model selects and sequences tools
correctly. Do not score reasoning, coding skill, prose, or general agent quality.

Never execute a template in place. Copy one task into a disposable workspace
under this repository. Keep temporary configuration and Codex sessions inside
the repository-local run root. On macOS, only the stream trace enabled by the
Web Admin **Gateway Logs** page belongs on a RAM Disk.

## Defaults

- Repository root: resolve with `git rev-parse --show-toplevel`.
- Default suite: `tests/agent_workspace/command_execution`.
- Default task: `01`; select `02` for polling, `03` for one stdin intervention,
  and `04` for two ordered interventions.
- Network-search suite: `tests/agent_workspace/network_search`; task `01`
  verifies a successful model-facing network search without command or browser
  fallbacks.
- Default third-party model: `deepseek-v4-flash`.
- Native GPT comparison model: `gpt-5.6-terra`. Confirm the upstream route in
  the Rosetta trace; the Codex-facing alias alone is not evidence.
- Default real-provider matrix: `deepseek-v4-flash` and `gpt-5.6-terra`.
- Runtime root: `tmp/agent_testing_workspace/YYYYMMDDHHMM`, using local time.
- macOS Gateway Logs root: `/Volumes/RAMDisk/YYYYMMDDHHMM`.

The timestamp must be exactly 12 digits and the complete directory name. Do not
include a model, task, suite, protocol, or tool name. If that minute already
exists, stop and use another unused minute rather than adding a suffix.

## Create The Isolated Run

1. Resolve and validate the selected template:

   ```bash
   ROOT=$(git rev-parse --show-toplevel)
   SUITE="$ROOT/tests/agent_workspace/command_execution"
   TASK_ID=02
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

4. Copy the user's gateway configuration into the run root. Never edit or stop
   the user's main gateway:

   ```bash
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
   or print credentials while editing it.

5. Create `RUN_ROOT/codex_home/config.toml` pointing to the isolated gateway.
   Use a client API key from the copied gateway config as the bearer token, but
   never include that value in reports:

   ```toml
   model_provider = "rosetta"
   model = "deepseek-v4-flash"
   sandbox_mode = "danger-full-access"
   approval_policy = "never"
   model_reasoning_effort = "medium"
   # Add `web_search = "live"` for tests/agent_workspace/network_search.

   [model_providers.rosetta]
   name = "rosetta"
   wire_api = "responses"
   requires_openai_auth = true
   base_url = "http://127.0.0.1:18765/v1"
   experimental_bearer_token = "<copied-gateway-client-key>"

   [projects."<RUN_ROOT>/worktree"]
   trust_level = "trusted"
   ```

   The network-search suite requires `web_search = "live"` at the top level of
   this isolated Codex config so the model-facing search surface is present.
   Do not add it for unrelated suites. When the tested model uses Responses
   Lite, set the isolated provider's display `name = "openai"` while retaining
   the Rosetta provider id and localhost `base_url`; current Codex gates the
   standalone `web.run` extension on that provider identity. Record this
   test-only identity override in the final report.

## Run One Task

1. Launch a separate gateway from the current checkout and copied config.
   Capture its process output, PID, and configuration under the repository-local
   run root. Gateway process stdout/stderr are not Web Admin Gateway Logs and
   must not be written to the RAM Disk:

   ```bash
   codex-rosetta-gateway --config "$RUN_ROOT/gateway/config.jsonc" --host 127.0.0.1 --port 18765 --no-banner \
     >"$RUN_ROOT/gateway/stdout.log" 2>"$RUN_ROOT/gateway/stderr.log" &
   GATEWAY_PID=$!
   printf '%s\n' "$GATEWAY_PID" >"$RUN_ROOT/gateway/pid"
   ```

   Poll `/v1/models` with the copied client key until ready. Do not use or
   modify the user's main gateway.

2. Read `TASK.md` as the exact prompt. Do not paraphrase or add hints. Read
   `expected.json` for the timeout and evidence contract, not as prompt text.

3. Run Codex non-interactively with the isolated home and bounded duration:

   ```bash
   MODEL=deepseek-v4-flash
   PROMPT=$(<"$RUN_ROOT/worktree/TASK.md")
   CODEX_HOME="$RUN_ROOT/codex_home" codex exec --json --skip-git-repo-check \
     -C "$RUN_ROOT/worktree" -m "$MODEL" "$PROMPT" \
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

Compare the evidence with `worktree/expected.json`:

- final output should contain `success_marker`;
- `command_starts` counts new process starts for the scenario;
- `continuations_min` and `continuations_max` count later operations on a
  returned process session;
- `non_empty_writes` counts continuation operations that send input;
- when `same_session_required` is true, every continuation must reuse the
  session returned by the single initial command.
- for network-search tasks, `network_search_calls_min` counts model-facing
  namespace or hosted search calls, `successful_search_result_required`
  requires a non-error result satisfying the task, and the command/browser
  maxima prohibit bypassing the search surface.

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

For Responses-to-Chat tests, explicitly distinguish:

- the localized command call visible to the upstream model;
- the native Codex command-start call returned by Rosetta;
- any later native continuation call.

This distinction is required when diagnosing a route that handles initial
execution but loses polling or stdin intervention.

For network-search tests, distinguish the model-facing search definition and
call (`web.run`, `web_search`, or a localized bridge), the Codex-facing output
item, and any separate HTTP request made by Codex or Rosetta. Inspect the
gateway process and stream trace to record the actual destination host and port
without exposing credentials.

## Real Provider Matrix

When comparing providers, create a separate timestamp run root for every model
and task. Never reuse a Codex home, copied gateway config, process state, or
workspace across matrix cells. Start with task `01`, then run `02` through `04`
only for models whose basic command execution succeeds.

For every cell, record:

- model and task id;
- Codex exit status and exact final marker;
- thread id and rollout path;
- Rosetta trace path and observed upstream model;
- initial command count, continuation count, non-empty write count, and session
  reuse result;
- terminal stream shape and any warning that changes interpretation.

## Safety Rules

- Work only inside `tmp/agent_testing_workspace/<timestamp>` after copying the
  selected template.
- Do not run agent tasks in the repository source tree or any external project.
- Do not edit the canonical files under `tests/agent_workspace` during a run.
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
- Preserve completed run artifacts unless the user explicitly requests
  deletion. Do not use `git restore`, `git reset`, or cleanup commands for runs.

## Final Report

Report the model, task id, exit status, observed marker, native interaction
counts, session reuse result, thread id, rollout path, trace path, observed
upstream model, and any warning affecting interpretation. Classify each run as
`success`, `success with deviations`, or `failure`, and briefly separate minor
deviations from failures of the core objective. State explicitly that the
result measures tool-call behavior only.
