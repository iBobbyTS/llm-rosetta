# Other Codex Tools

Codex has several agent/runtime tools whose behavior depends on more than a simple function call schema. Codex-Rosetta keeps those tools usable for Chat-only upstream models by preserving Responses-specific structure where needed and adding targeted Chat-model guidance where the native tool descriptions are too terse.

## Plan Mode

Plan mode uses `request_user_input` when the model needs a real user decision before producing or revising a plan. Chat models can confuse that with the final approval step and ask the user whether to proceed after they already emitted a proposed plan.

For Chat targets, Rosetta appends extra guidance to the `request_user_input` tool description:

- Use it only for preferences or decisions that materially change the plan.
- Do not use it to ask whether to approve, proceed with, or implement a proposed plan.
- After the final `<proposed_plan>` block, let the Codex UI handle approval and implementation.
- Keep option labels short and natural, without `A:`, `B:`, or `C:` prefixes.

This is a prompt-level/tool-description adaptation. It does not change the tool schema.

## TODO / update_plan

The `update_plan` tool is currently passed through the normal conversion path without a dedicated localization rule.

It is exposed to Chat providers as a regular function tool after Responses-to-Chat conversion, and its calls are converted back through the normal tool-call pipeline. No special prompt suffix, namespace restoration, or schema rewrite is currently applied specifically for `update_plan`.

## Goal Tools

Goal state is managed through `get_goal`, `create_goal`, and `update_goal`. Chat models may not infer the right sequence from the terse native tool descriptions.

For Chat targets, Rosetta appends extra guidance to:

- `create_goal`: call it when the user explicitly asks to mark a goal complete or blocked but no active goal exists, or when `update_goal` reports that the thread has no goal. Do not set `token_budget` unless the user explicitly provided a numeric token budget.
- `update_goal`: when goal state is uncertain, call `get_goal` first. If there is no active goal, call `create_goal` with a concise objective and no token budget unless explicitly requested, then retry `update_goal`.

`get_goal` itself is not modified.

## Subagents And Namespace Tools

Codex exposes subagent capabilities through Responses namespace tools such as `multi_agent_v1`. Chat Completions does not have the same nested namespace tool shape.

For Responses-to-Chat routes, Rosetta flattens namespace child tools into ordinary Chat function tools. For example:

```text
multi_agent_v1.spawn_agent -> spawn_agent
```

During request conversion, Rosetta records the mapping from child tool name to Responses namespace. When the upstream Chat model returns a `spawn_agent` tool call, Rosetta restores the Responses namespace metadata before returning the event to Codex:

```json
{
  "type": "function_call",
  "name": "spawn_agent",
  "namespace": "multi_agent_v1"
}
```

For Responses-to-Responses routes, namespace tools stay in their native Responses shape.

## Plugin And Deferred Tools

Plugin and deferred tool discovery use the same general tool conversion path. Rosetta does not currently add a dedicated localization rule for every plugin tool.

The important behavior is that tool calls must survive the round trip:

- Tool definitions are converted into a Chat-compatible function shape when sent to Chat providers.
- Tool calls are converted back into Responses events for Codex.
- Namespace metadata is restored when the tool came from a Responses namespace.
- Message `phase` metadata is preserved so work-process output remains foldable in Codex.

## Tool Profile Scope

**OpenAI Responses (Tool Mapping only)** supports Tool Profiles while keeping the rest of the Responses request and response on the direct path. The bundled **Responses pass through** Profile preserves incoming tools; **Responses web.run mapping** changes only `web.run` so `/v1/alpha/search` uses Rosetta's local mapping. Responses Rosetta, Chat, Anthropic, and Google model groups continue to support Profile selection and processing.
