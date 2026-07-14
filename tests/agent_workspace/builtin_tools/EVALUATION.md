# Built-in Code Mode Tool Evaluation

This file guides the outer evaluating agent. Do not include it in the tested
model's prompt.

## Required evidence

Use all three bounded evidence sources:

1. `artifacts/codex.jsonl` for process exit, thread id, visible tool activity,
   final marker, and Codex turn events.
2. The matching rollout under `codex_home/sessions` for native tool calls,
   returned cell ids, plan updates, file-change events, image content, and Goal
   state transitions.
3. Gateway Logs for the actual upstream model, model-facing tool names,
   projected calls, reconstructed Codex calls, and terminal stream state.

Text in prompts, descriptions, or tool declarations is not a call. A correct
final marker without the task's native call sequence is a failure. Small extra
reads, waits, or explanation are deviations when the core behavior still
succeeds.

## Per-scenario decisions

### `01` / Code Mode `wait`

Require one model-facing and native custom `exec` call whose raw JavaScript
contains `yield_control()`. Its first result must identify a running cell.
Require a later top-level `wait` call using the same cell id and a successful
result containing `WAIT_PHASE_2`. Process-session polling through
`write_stdin` is not a substitute for Code Mode `wait`.

### `02` / `update_plan`

Require two model-facing `update_plan` calls, two reconstructed Code Mode
`tools.update_plan(...)` invocations, and two native `PlanUpdate` events. The
first state must be `Inspect fixture: in_progress` and `Report result: pending`;
the second must mark both steps `completed`. A prose checklist is not a plan
tool call.

### `03` / localized file workflow

Require model-facing `Glob`, `Grep`, `Read`, `apply_patch`, `Edit`, and `Write`
calls. Gateway Logs must prove that `Glob`, `Grep`, and `Read` became nested
`exec_command` executions and that projected `apply_patch`, `Edit`, and `Write`
reached native `apply_patch` semantics. The final workspace must satisfy these
exact checks:

- `fixtures/alpha.txt` contains `status=edited` and no `status=original`;
- `fixtures/beta.txt` contains `status=patched` and no `status=unchanged`;
- `fixtures/created.txt` contains exactly `CREATED_BY_WRITE` followed by one
  newline.

Shell commands or direct Python calls selected by the model do not satisfy the
localized alias requirements, even if the files end in the expected state.

### `04` / `view_image`

Require one model-facing projected `view_image` call, one reconstructed
`tools.view_image(...)` call inside native custom `exec`, and a successful
image result for `fixtures/quadrants.png`. The native result must contain an
image content item or data URL; a textual claim that the image exists is not
enough.

### `05` / Goal lifecycle

Require the ordered native sequence `get_goal`, `create_goal`, `get_goal`,
`update_goal`. The created objective must be `Verify projected Goal tools`, no
token budget may be supplied, and the final update status must be `complete`.
All calls must belong to the same fresh thread.

### `06` / visual recognition

Require one model-facing projected `view_image` call, one reconstructed
`tools.view_image(...)` call inside native custom `exec`, and a successful
image result for `fixtures/vision_quadrants.png`. Gateway Logs must prove the
actual upstream is `qwen3.7-plus`. The exact success marker must identify red
top-left, green top-right, blue bottom-left, and yellow bottom-right. Reading
or inspecting the file through any other tool is a failure even if the marker
is correct.

## Required result file

Write `artifacts/evaluation.json` with this shape:

```json
{
  "classification": "success | success with deviations | failure",
  "task_id": "01 through 06",
  "target_scope": "wait | update_plan | localized_file_workflow | view_image | goal_lifecycle | visual_recognition",
  "model": "Codex-facing model alias",
  "model_catalog_equivalent_to": "gpt-5.6-sol",
  "provider_identity": "isolated provider id",
  "provider_display_name": "OpenAI",
  "upstream_model": "model proven by Gateway Logs",
  "thread_id": "Codex thread id",
  "rollout_path": "isolated rollout path",
  "process_exit_code": 0,
  "success_marker_observed": true,
  "model_facing_calls": ["observed names in order"],
  "native_calls": ["observed native calls in order"],
  "successful_target_results": true,
  "state_observations": ["bounded task-specific evidence"],
  "workspace_assertions": {},
  "prohibited_fallback_calls": 0,
  "gateway_log_evidence": [
    {
      "stage": "bounded Gateway Logs stage",
      "request_id": "request id when available",
      "observation": "short credential-free structural observation"
    }
  ],
  "stream_completed": true,
  "warning": null
}
```

Keep evidence structural and credential-free. Never copy full prompts,
authorization headers, tokens, image data URLs, file bodies beyond the tiny
expected markers, or entire trace records into this file.
