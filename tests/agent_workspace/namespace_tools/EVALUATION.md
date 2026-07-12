# Namespace Tool Evaluation

This file guides the outer evaluating agent. Do not include it in the tested
model's prompt.

## Required evidence

Use all three bounded evidence sources:

1. `artifacts/codex.jsonl` for process exit, thread id, visible tool activity,
   and the final marker.
2. The matching rollout under `codex_home/sessions` for native Namespace calls,
   successful results, the child task path, and child completion.
3. Gateway Logs for the actual upstream model, model-facing tool names,
   conversion route, and terminal stream state.

Do not count tool descriptions or prompt text as calls. Do not count shell,
browser, direct local-file, or ordinary local Skill discovery as Namespace
execution.

## Per-Namespace decisions

- `clock`: require one successful native `clock.curr_time` call.
- `memories`: require one successful native `memories.list` call whose result
  includes the seeded `memory_summary.md` fixture. The call must omit `path`;
  passing `/` is an invalid request rather than a missing memory fixture.
- `skills`: require one successful native `skills.list` call with authority
  `{ "kind": "orchestrator" }`. An empty skills array is acceptable; an absent
  tool is `not_exposed` and an error result is `failed`.
- `collaboration`: require `collaboration.spawn_agent`, a returned canonical
  child task path, and `collaboration.wait_agent` or an equivalent repeated
  wait until the child completes with `SUBAGENT:NAMESPACE_OK`.

For each Namespace set `status` to `success`, `not_exposed`, `not_called`, or
`failed`. The overall run succeeds only when all four statuses are `success`,
the exact parent marker is present, no prohibited fallback occurred, and the
stream completed.

Small extra calls or prose are deviations rather than failures when the core
Namespace calls all succeed. Do not excuse a missing Namespace as model
quality: report separately whether the tool was absent from the request or was
available but not selected.

The prompt requires attempting later Namespaces after an earlier failure. If
the model stops early, preserve the earlier evidence but mark every unattempted
available Namespace `not_called`.

## Required result file

Write `artifacts/evaluation.json` with this shape:

```json
{
  "classification": "success | success with deviations | failure",
  "model": "model alias used by Codex",
  "provider_identity": "namespace-test (display name: openai)",
  "provider_identity_override": true,
  "upstream_model": "model proven by Gateway Logs",
  "thread_id": "Codex thread id",
  "rollout_path": "isolated rollout path",
  "process_exit_code": 0,
  "success_marker_observed": true,
  "namespaces": {
    "clock": {
      "status": "success | not_exposed | not_called | failed",
      "native_calls": ["clock.curr_time"],
      "model_facing_calls": ["observed name"],
      "successful_result": true
    },
    "memories": {
      "status": "success | not_exposed | not_called | failed",
      "native_calls": ["memories.list"],
      "model_facing_calls": ["observed name"],
      "successful_result": true,
      "fixture_observed": true
    },
    "skills": {
      "status": "success | not_exposed | not_called | failed",
      "native_calls": ["skills.list"],
      "model_facing_calls": ["observed name"],
      "successful_result": true
    },
    "collaboration": {
      "status": "success | not_exposed | not_called | failed",
      "native_calls": ["collaboration.spawn_agent", "collaboration.wait_agent"],
      "model_facing_calls": ["observed names"],
      "successful_result": true,
      "child_task_path": "/root/namespace_probe",
      "child_marker_observed": true
    }
  },
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
authorization headers, tokens, or entire trace records into this file.
