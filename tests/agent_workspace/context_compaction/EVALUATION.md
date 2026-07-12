# Parent-Agent Evaluation Guide

This document is for the parent agent running the context-compaction fixture.
The tested model must not decide whether compaction succeeded or which
compaction implementation Codex used. Those conclusions come from the isolated
Codex configuration, rollout, stderr, and Gateway Logs trace.

## Required output

Write the following object to `RUN_ROOT/artifacts/evaluation.json` and mirror
its conclusion in the parent agent's final response:

```json
{
  "suite": "context_compaction",
  "task_id": "01",
  "classification": "completed",
  "compaction_triggered": true,
  "remote_compaction_trigger_observed": true,
  "compaction_success": true,
  "compaction_method": "remote_v2_responses",
  "wire_compaction_item_type": "compaction_summary",
  "summary_output_item_type": null,
  "accepted_compaction_item_count": 1,
  "followup_compaction_input_observed": true,
  "followup_summary_message_observed": false,
  "error": null,
  "model": "gpt-5.5",
  "gateway_provider": "TURNING",
  "codex_model_provider": "openai",
  "thread_id": "<thread-id>"
}
```

Use `null` for `wire_compaction_item_type` when no accepted remote compaction
item is returned. Use `summary_output_item_type` for the ordinary output item
that carries a local model summary. Do not include credentials, full prompts,
compaction payloads, summaries, or unbounded error bodies.

## Success decision

Set `compaction_triggered` when either a remote compaction trigger or a local
compaction request/event is observed. Keep
`remote_compaction_trigger_observed` separate so a successful local compaction
is not mislabeled as remote v2.

For `remote_v2_responses`, set `compaction_success` to `true` only when all of
these are observed:

1. A genuine outgoing input item has `type: "compaction_trigger"`.
2. The compact response contains exactly one completed item whose wire type is
   `compaction` or `compaction_summary`.
3. A later request carries the installed `type: "compaction"` input item.
4. Codex does not report a compact-task error and reaches the task marker.

An accepted output item without the later installed compaction input is not a
successful end-to-end compaction.

For `local_model_summary`, require all of these instead:

1. A normal `/v1/responses` request has `request_kind: "compaction"`, no
   `compaction_trigger` item, and no tools.
2. The request contains the local summarization instruction and the upstream
   returns one completed summary message.
3. A later turn contains the installed summary message rather than an opaque
   `compaction` item.
4. The rollout records compaction, Codex reports no compact-task error, and the
   task reaches its marker.

## Compaction method

Report Codex's context-compaction implementation, not HTTP content encoding or
request compression:

| `compaction_method` | Evidence |
|---|---|
| `remote_v2_responses` | Normal `/v1/responses` request containing `compaction_trigger` |
| `remote_legacy_responses_compact` | Request sent to `/v1/responses/compact` |
| `local_model_summary` | Normal `/v1/responses` request with `request_kind: "compaction"`, a summarization prompt, no trigger, and no tools |
| `token_budget_local` | The Token Budget feature selects its local compaction path |
| `not_triggered` | No compaction request or local compaction event is observed |
| `unknown` | Evidence is incomplete or contradictory |

For this fixture's required configuration, the expected method is
`remote_v2_responses`. The wire response may still use the accepted
`compaction_summary` alias; that does not change the method.

## Classification

- `completed`: every success condition above is satisfied.
- `remote_compaction_error_reproduced`: a genuine remote compaction request is
  followed by the declared compact-task error.
- `not_triggered`: the scenario ran but no compaction occurred.
- `infrastructure_failure`: the scenario never reached the tested provider due
  to gateway startup, authentication, timeout, or connection failure.
