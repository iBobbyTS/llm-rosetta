# Capability Exposure And Discovery Evaluation

This file guides the outer evaluator and must not be inserted into the tested
model's prompt.

## Evidence layers

Use three bounded, credential-free sources:

1. `artifacts/codex.jsonl`: process result, thread id, visible reads/tool calls,
   final proof, and prohibited fallbacks.
2. The matching isolated rollout: initial `<skills_instructions>` and
   `<plugins_instructions>`, explicit `<skill>` injection, selected skill read,
   `exec` cells, nested MCP call, and consumed result.
3. Gateway Logs: actual upstream model and route, source Responses request,
   target Chat request when converted, the projected `tool_search`, the `exec`
   runtime-discovery contract, contextual ordering, and terminal stream state.

Do not count installation output, prompt text, reasoning text, or a tool
description as a tool call. A private body marker proves a skill body was read
only when that marker was absent from the task prompt and appeared through the
host injection or selected skill read required by `expected.json`.

## Structural rules

- All tasks expose exactly three candidates in the order declared by
  `catalog_exposure.candidate_order`.
- `<skills_instructions>` is contextual developer content. On a converted Chat
  route it becomes ordered system context without losing any candidate line.
- Explicit task `03` additionally contains the complete target body in a
  turn-scoped `<skill>` developer fragment.
- Generic `<plugins_instructions>` is expected whenever installed plugins are
  visible. The plugin-specific `Capabilities from the ... plugin` fragment is
  expected only for task `01` and prohibited for tasks `06` and `07`.
- The source Responses request carries the `exec` description that defines
  `ALL_TOOLS`; deferred candidate names are not placed in the model request.
  The converted Chat request must retain raw `exec` and expose the synthetic
  ordinary `tool_search` Function. A converted search call must return to Codex
  as custom `exec`, with no native `tool_search_call/output` or Gateway-loaded
  namespace. The final `exec` output must contain the three runtime
  `{name, description}` entries in Codex's stable canonical-name order declared
  by `catalog_exposure.candidate_order`.
- For plugin MCP tasks, runtime names must retain the plugin/server namespace,
  and the selected `mcp_tool_call_end` event must retain `plugin_id` provenance.
- Implicit task prompts must not name a capability, plugin URI, tool, private
  body marker, or result prefix. The target must be selected from model-visible
  metadata.
- Only the archive target may be read/called. Calls to arithmetic or palette
  candidates fail the task, even if the final response is later corrected.

## Stage classification

Record these checks independently:

- `metadata_exposed`: expected catalog entries are present in the correct
  contextual section and order.
- `target_selected`: the archive candidate, and no distractor, was selected.
- `body_read`: required skill body was host-injected or read by the agent.
- `tool_exposed`: the source model request retained the `ALL_TOOLS` discovery
  contract; on a converted route, the target request also exposed ordinary
  `tool_search`, retained raw `exec`, and translated the search call back to
  custom `exec`; the runtime catalog contained all three expected MCP entries;
  for plugin tasks, also verify selected-call provenance.
- `tool_called`: expected tool received the exact arguments.
- `result_used`: final answer came from the body/tool result.

Use `not_listed`, `not_injected`, `not_exposed`, `not_selected`, `not_called`,
and `failed` to identify the first missing stage. An implicit task whose paired
explicit control passed may be classified `active_discovery_failed`.

## Required result file

Write `artifacts/evaluation.json` with this shape:

```json
{
  "classification": "success | success with deviations | failure | active_discovery_failed",
  "task_id": "01",
  "model": "model alias used by Codex",
  "provider_identity": "deferred-tool-test (display name: openai)",
  "upstream_model": "model proven by Gateway Logs",
  "route": "responses_direct | responses_to_chat",
  "thread_id": "Codex thread id",
  "rollout_path": "isolated rollout path",
  "process_exit_code": 0,
  "capability_kind": "skill | mcp | plugin_skill | plugin_mcp",
  "invocation_mode": "explicit | implicit",
  "candidate_count": 3,
  "prompt_contract": {
    "mentions_capability": false,
    "leaked_identifiers": []
  },
  "checks": {
    "metadata_exposed": true,
    "target_selected": true,
    "body_read": true,
    "tool_exposed": true,
    "tool_called": true,
    "result_used": true
  },
  "first_failed_stage": null,
  "plugin_guidance_observed": false,
  "distractor_reads_or_calls": [],
  "prohibited_fallback_calls": [],
  "success_marker_observed": true,
  "stream_completed": true,
  "source_request_evidence": [],
  "target_request_evidence": [],
  "runtime_catalog_evidence": [],
  "paired_control": {
    "task_id": "03",
    "classification": "success",
    "evaluation_path": "path or null"
  },
  "warning": null
}
```

For inapplicable checks, use `null`, not `true`. Keep evidence structural and
short; never copy authorization headers, tokens, complete prompts, or whole
trace records.
