# Context-Compaction Protocol Evaluation

This guide is for the test executor, including a coding agent or developer.
The tested model must not classify its own compaction. Tasks `01` through `04`
evaluate the Codex/Rosetta protocol and routing contract. Task `05` separately
evaluates post-compaction exactly-once model behavior. Summary quality belongs
to the separate `context_compaction_summary_quality` suite.

## Required output

Write a bounded object to `RUN_ROOT/artifacts/evaluation.json`:

```json
{
  "suite": "context_compaction",
  "task_id": "01",
  "target_scope": "remote_compaction_protocol",
  "classification": "completed_with_deviations",
  "protocol_classification": "completed",
  "model_behavior_classification": "not_scored",
  "compaction_triggered": true,
  "remote_compaction_trigger_observed": true,
  "compaction_success": true,
  "compaction_method": "remote_v2_responses",
  "request_kind": "remote_v2_in_band",
  "request_http_path": "/v1/responses | /v1/responses/compact | unknown",
  "request_evidence_source": "gateway_request_profile | wire_body | access_log",
  "trigger_is_final_input_item": true,
  "compaction_reason": "context_limit",
  "gateway_compaction_mode": "rosetta",
  "wire_compaction_item_type": "compaction",
  "accepted_compaction_item_count": 1,
  "complete_protocol_chain_count": 1,
  "followup_compaction_input_observed": true,
  "error": null,
  "model": "observed Codex-facing model alias",
  "gateway_provider": "Deepseek (Official)",
  "codex_model_provider": "codex_rosetta",
  "thread_id": "<thread-id>",
  "command_starts": 1,
  "baseline_tokens": 14500,
  "post_compaction_tokens": 10000,
  "command_output_chars": 128000,
  "command_max_output_tokens": 20000,
  "rosetta_mapping_rows": 1
}
```

Do not include credentials, prompts, compaction payloads, summary plaintext,
or unbounded error bodies.

## Split success decision

For every counted Remote Compaction V2 protocol chain, require all of these:

1. A genuine outgoing item has `type: "compaction_trigger"`, or the Gateway
   request-log profile proves it recognized an in-band trigger by recording a
   non-empty `compaction_mode` and `compaction_reason` on `/v1/responses`.
2. The metadata reason and gateway mode match `expected.json`.
3. The compact response contains one completed `compaction` item (or
   the accepted `compaction_summary` compatibility alias).
4. A later request carries the installed `type: "compaction"` item.
5. Codex reaches the task marker without a compact-task error.
6. `baseline_tokens` and `post_compaction_tokens` are both below
   `model_auto_compact_token_limit`, the command call retains at least 20,000
   output tokens and 60,000 output characters, and the genuine `context_limit`
   compaction occurs after that command result and before the final model
   response.

For `target_scope=remote_compaction_protocol` (`01` and `02`), one complete
chain is sufficient. Additional model-issued command starts, triggers,
compactions, or Rosetta mappings after that chain are deviations. Set
`protocol_classification=completed`,
`model_behavior_classification=not_scored`, and use top-level
`completed_with_deviations` when deviations exist.

For `target_scope=post_compaction_exactly_once` (`05`), require one complete
protocol chain plus exactly one command start, one compaction result, one
Rosetta mapping, and the task marker. Set
`model_behavior_classification=failed` when the model restarts the command or
causes another compaction; do not change a completed protocol classification.

Set `request_kind` from the path/body/profile/event classification in the suite
README. A rollout-only `compacted` or `context_compacted` event is
`local_internal`; it cannot satisfy Remote V2. A request-log compaction profile
plus a mapping or native compaction item and installed follow-up is request
evidence, even when the early response bypasses stream tracing.

For Rosetta protocol scope, verify the expected mapping lower bound. For
native mode, verify the mapping table remains unchanged. For task `05`, verify
the exact mapping count. For model-switch tasks, verify the first model
initiates compaction and the target model produces the resume marker.

## Classification

- `completed`: every selected-scope condition is satisfied.
- `completed_with_deviations`: the protocol scope passed, with later model
  actions recorded outside that scope.
- `model_behavior_failure`: the protocol chain completed, but task `05` failed
  the exactly-once behavior contract.
- `remote_compaction_error_reproduced`: a genuine trigger is followed by the
  bounded compact-task error recorded in the artifact.
- `not_triggered`: the scenario ran but no genuine trigger was sent.
- `infrastructure_failure`: the tested provider was never reached; do not use
  this classification for model command repetition after a completed chain.

Never use summary wording or fact retention to change this suite's protocol
classification.
