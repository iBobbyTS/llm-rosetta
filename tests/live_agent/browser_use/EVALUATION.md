# In-App Browser GUI Live Evaluation

This is a **judge-only** contract. The Browser test executor must not read or
apply it. Evaluation occurs in a new user-created judge-agent session after the
user copies the executor's complete final response, supplies the
exact `.agent-work/live-agent-test/{YYYYMMDD-HHMM}` run root plus its
`execution.json` path, and identifies the source test session/thread.

The judge must not rerun Browser actions or modify `execution.json`.

## Inputs

Require all of the following before issuing a verdict:

1. The executor's copied final response.
2. The supplied `<run_root>/execution.json`.
3. The source test session/thread id supplied by the user.
4. `01/expected.json` for capability order, execution boundaries, and exact
   postconditions.

If the source session id is absent, report that the judge cannot complete
provenance/log correlation and ask the user for it. Do not guess from the most
recent file alone when multiple candidate sessions exist.

The judge must verify that `execution.json`'s absolute `run_root` and
`run_stamp` match the user-supplied directory, that the basename has the exact
`YYYYMMDD-HHMM` shape, and that it resides directly under
`.agent-work/live-agent-test/`. Never select a run by latest modification time.
Write only `<run_root>/evaluation.json`; do not overwrite another run or use a
shared `artifacts/browser_use/01` destination.

## Gate 1: execution validity

The judge verifies, using the execution report and bounded source-session
evidence, that:

- the host was the Codex GUI app;
- one main executor task performed the entire Browser matrix;
- the executor used the `Browser` plugin and
  `browser:control-in-app-browser` skill;
- the selected browser/backend was IAB;
- no Codex CLI, subagent, Chrome, Chrome extension backend, or substitute
  browser-control surface was used;
- the executor did not inspect Gateway Logs, Gateway traces/databases, Request
  Logs, session/rollout JSONL, or archived session metadata.

Use `invalid_environment` when IAB or the required skill was unavailable and
the executor correctly stopped. Use `invalid_execution` when a prohibited
surface/evidence source was used or provenance cannot rule one out. Do not
report Browser capability success for either classification.

## Gate 2: bounded judge evidence

The judge, and only the judge, may use these evidence sources:

1. The executor's Browser operations and fixture postconditions in
   `execution.json`.
2. Bounded source rollout JSONL records for Browser call ordering, results,
   `codex/browserUse=true`, IAB backend identity, tab ids, and fixture URLs.
3. Bounded Rosetta Gateway Logs for provider/model route, request ids,
   projected/raw Browser tool traffic when present, and terminal stream state.

For a Responses-to-Chat route, additionally verify all of the following:

- the first deferred lookup is the projected `tool_search`, returned to Codex
  as the marked search `exec` rather than a native `tool_search_call`;
- the following target Chat request exposes only Node REPL Functions whose exact
  names and declarations appeared in that paired result;
- every actual Browser runtime call is a structured
  `mcp__node_repl__js` Function call at the model boundary, not model-authored
  outer `exec` JavaScript;
- Rosetta rebuilds each call as custom `exec` using the matching nested tool and
  a content-block forwarder for text, image, and `isError`;
- `mcp__node_repl__js_reset` and
  `mcp__node_repl__js_add_node_module_dir` are absent unless each was returned
  by the live search and used for its documented recovery/setup purpose.

Treat a model-authored raw Browser wrapper after the structured Function became
available as a tool-adaptation failure. The Rosetta-generated search `exec` is
not a Browser runtime call and does not violate this rule. Direct
Responses-to-Responses runs retain their native Code Mode behavior and are not
judged against the Chat-boundary shape.

Gateway Logs do not carry the Codex session id. Correlate by a bounded time
window, provider/model, request id or request-log id, and call ordering. Mark
correlation `ambiguous` when those fields are insufficient. Never copy complete
prompts, tool source, DOM snapshots, screenshots, credentials, headers, data
URLs, model payloads, or raw JSONL/trace records into the evaluation artifact.

## Capability classification

Classify each executor observation independently:

- `pass`: the required Browser call succeeded and the exact page-level
  postcondition was observed;
- `partial`: useful core behavior worked but a documented branch or reliable
  postcondition was unavailable;
- `fail`: the call or required postcondition failed;
- `unsupported`: the selected IAB backend explicitly rejected the capability;
- `skip_policy`: the capability was outside the safe scope defined by the task.

A returned tool call without a fixture/visual postcondition is not a pass.
Drag requires pointer-move/up or another completed page-state marker. Dialog
handling must distinguish user and automation actions. Corroborate claims with
bounded source-session evidence rather than trusting prose alone.

## Matrix completeness and recovery

`execution.json` must contain exactly one observation for every executor
capability group in `01/expected.json`, in the same order. Missing rows make the
run incomplete and therefore `failure`, even when every attempted observation
would otherwise pass.

An executor-side assertion error, unsupported operation, stale tab, or missing
postcondition is not a valid early-stop condition. The execution report should
show bounded recovery by fixture reload or a fresh tab from the existing IAB
binding, followed by continued matrix execution. Only an invalid execution
gate or an actual unavailable/disconnected IAB binding may justify an
incomplete matrix.

## Required judge result

Write `<run_root>/evaluation.json` with this shape:

```json
{
  "classification": "success | success_with_limitations | failure | invalid_environment | invalid_execution",
  "task_id": "01",
  "role": "judge",
  "run_root": "/absolute/workspace/.agent-work/live-agent-test/YYYYMMDD-HHMM",
  "run_stamp": "YYYYMMDD-HHMM",
  "source_execution_path": "/absolute/workspace/.agent-work/live-agent-test/YYYYMMDD-HHMM/execution.json",
  "source_thread_id": "user-supplied GUI test thread id",
  "source_rollout_path": "matching rollout JSONL path",
  "execution_gates": {
    "gui_app": true,
    "main_task_only": true,
    "iab_selected": true,
    "required_skill_used": true,
    "codex_cli_calls": 0,
    "subagent_calls": 0,
    "chrome_calls": 0,
    "substitute_browser_calls": 0,
    "executor_gateway_log_reads": 0,
    "executor_session_jsonl_reads": 0
  },
  "matrix_completed": true,
  "expected_capability_count": 23,
  "recorded_capability_count": 23,
  "missing_capability_ids": [],
  "capabilities": [
    {
      "id": "stable capability id",
      "status": "pass | partial | fail | unsupported | skip_policy",
      "browser_call": "bounded operation summary",
      "postcondition": "bounded synthetic observation",
      "warning": null
    }
  ],
  "gateway_correlation": {
    "status": "correlated | ambiguous | unavailable",
    "request_ids": [],
    "observations": []
  },
  "rollout_observations": [],
  "cleanup": {
    "viewport_reset": true,
    "test_tabs_finalized": true,
    "fixture_server_stopped": true
  },
  "warning": null
}
```

`success` requires every applicable Browser capability to pass and sufficient
execution provenance. Use `success_with_limitations` when all core Browser
interactions pass and remaining partial/unsupported results are explicit IAB or
observability limitations. Any failed core interaction or unjustified missing
capability row is `failure`. A violated execution gate overrides capability
rows with `invalid_execution`.
