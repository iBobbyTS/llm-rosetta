# Context Compaction Protocol Test

This suite verifies Codex's remote-compaction request and response contract
through Codex-Rosetta. It is a protocol diagnostic, not a model-quality or
summary-quality benchmark.

## Scenario

- `01`: run one deterministic foreground command, then require a second model
  turn to report the fixed result marker.

The isolated Codex configuration must use the built-in `openai` provider ID and
set `model_auto_compact_token_limit = 1000`. The deliberately low limit makes
Codex attempt compaction after the first tool result and before the second
model turn. Use a normal provider configuration and token limit for any
non-diagnostic agent test.

For a provider-identity comparison, keep every other runtime field unchanged
and replace the Codex provider with an explicitly configured provider whose id
and display name are both `custom`. The expected method then changes from
remote v2 to `local_model_summary`.

## Result interpretation

Inspect both the Codex stderr/rollout and the Gateway Logs trace.
The parent agent must follow [`EVALUATION.md`](EVALUATION.md), write the bounded
machine-readable conclusion to `RUN_ROOT/artifacts/evaluation.json`, and state
both whether compaction succeeded and which compaction method Codex used.

- `completed`: the trace contains a request whose input includes
  `compaction_trigger`, the response contains exactly one completed
  `type: "compaction"` or `type: "compaction_summary"` output item, and Codex
  reaches `RESULT:COMPACT_OK`. Codex accepts `compaction_summary` as the wire
  compatibility alias for its internal compaction item.
- `remote_compaction_error_reproduced`: Codex sends `compaction_trigger`, then
  reports the error pattern declared in `expected.json` before reaching the
  marker.
- `not_triggered`: no genuine `compaction_trigger` input item is present. This
  is an invalid run rather than evidence that compaction works.

Do not count the string `compaction_trigger` when it merely appears inside a
prompt, source listing, tool result, or error message. It must be the `type` of
an item in the outgoing Responses request.
