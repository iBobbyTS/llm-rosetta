# Other Codex Tools

Codex has several agent/runtime tools whose behavior depends on more than a simple function call schema. Codex-Rosetta keeps those tools usable for Chat-only upstream models by preserving Responses-specific structure where needed. Targeted model guidance is a Profile-owned Function field rather than a hard-coded converter rule.

## Plan Mode

Plan mode uses `request_user_input` when the model needs a real user decision before producing or revising a plan. Chat models can confuse that with the final approval step and ask the user whether to proceed after they already emitted a proposed plan.

The bundled **Chat Default** Profile marks `request_user_input` as Modified and appends guidance with these effects:

- Use it only for preferences or decisions that materially change the plan.
- Do not use it to ask whether to approve, proceed with, or implement a proposed plan.
- After the final `<proposed_plan>` block, let the Codex UI handle approval and implementation.
- Keep option labels short and natural, without `A:`, `B:`, or `C:` prefixes.

This is a prompt-level/tool-description adaptation. It does not change the tool schema. The Tools page shows this summary when the Function is Modified; it does not expose the complete packaged prompt text.

## TODO / update_plan

When Codex exposes `update_plan` only as a nested Code Mode tool, the bundled **Chat Default** Profile projects it into an ordinary Chat function. Rosetta derives the current parameter schema and description from Codex's `exec` declaration instead of maintaining a duplicate schema. A model call is rebuilt as a deterministic custom `exec` script for Codex. If Codex already exposes a direct `update_plan` Function, that direct definition is preserved.

## Goal Tools

Goal state is managed through `get_goal`, `create_goal`, and `update_goal`. Chat models may not infer the right sequence from the terse native tool descriptions.

The bundled **Chat Default** Profile marks `create_goal` and `update_goal` as Modified and appends guidance for:

- `create_goal`: call it when the user explicitly asks to mark a goal complete or blocked but no active goal exists, or when `update_goal` reports that the thread has no goal. Do not set `token_budget` unless the user explicitly provided a numeric token budget.
- `update_goal`: when goal state is uncertain, call `get_goal` first. If there is no active goal, call `create_goal` with a concise objective and no token budget unless explicitly requested, then retry `update_goal`.

`get_goal` is Pass through: Rosetta projects its current Code Mode declaration and translates the call back to `exec` without appending guidance. `create_goal` and `update_goal` remain Modified because they retain the Profile-owned guidance above.

Their Tools-page cards display the actual complete guidance from the selected Profile in a read-only textarea titled **Appended Description Prompt**, rather than using the card description for this content.

## Code Mode Nested Tools

Recent Codex Code Mode surfaces keep several runtime tools inside the custom `exec` description instead of exposing every tool as a top-level Function. For Responses-to-Chat routes, the Chat Default projection rules map the following nested declarations into ordinary Chat functions when those declarations are present and their Profile state is Pass through or Modified:

- `exec_command`, `write_stdin`, `update_plan`, and `view_image`
- `web__run` (Codex runtime identity `web.run`), exposed to Chat as `web-run`
- `image_gen__imagegen` (runtime identity `image_gen.imagegen`), exposed as `image_gen-imagegen`
- `get_goal`, `create_goal`, and `update_goal`
- `clock__curr_time` and `clock__sleep`, exposed as `clock-curr_time` and `clock-sleep`
- flat `memories__*` and `skills__*` entries, exposed with canonical `namespace-function` Chat names

Rosetta reads each schema and description from the actual Codex `exec` declaration. Its reverse parser covers the TypeScript grammar emitted by Codex, including literals, unions, intersections, arrays, tuples, and object index signatures. Constraints that Codex itself omits while rendering JSON Schema to TypeScript cannot be reconstructed. Rosetta does not invent a Function when a declaration cannot be parsed. A same-named direct Function wins, and projection fails closed for that name.

Once at least one model-visible nested Function has been projected successfully, Rosetta removes the parent `exec` tool from the outbound Chat tool list. The parent declaration remains available internally for projection and reverse translation. If no model-visible declaration can be parsed, Rosetta keeps the original `exec` tool instead of silently removing all command capability.

For Exec Expansion cards, **Pass through** means representation-only adaptation: expose the current declaration as a normal Chat Function and translate its call back to `exec`, without appending any catalog text. Chat Default uses this state for `exec_command`, `write_stdin`, `update_plan`, `view_image`, `get_goal`, Clock, Memories, and Skills. **Modified** is retained where the Profile changes model-visible guidance or behavior: `create_goal` and `update_goal` append guidance, while `web.run` uses the selected Tavily-backed Rosetta search mapping.

Chat Default keeps `image_gen__imagegen` Disabled until a copied Profile sets that Function to Modified and supplies the required image endpoint credentials.

Calls to projected Functions are rebuilt as deterministic JavaScript calls on the nested `tools` object and returned to Codex as `custom_tool_call` calls to `exec`. The exact Chat-to-Codex call mapping is stored in the existing encrypted tool-history cache, so a subsequent request within its 24-hour TTL restores the original Chat Function and arguments before it is sent upstream. `view_image` forwards its result through `image(...)`, `image_gen.imagegen` uses `generatedImage(...)`, and text-bearing projected tools use `text(...)`.

Chat Default disables the `apply_patch` exec projection. Instead, Rosetta injects the three read tools `Read`, `Glob`, and `Grep`, plus the two write tools `Edit` and `Write`. `Edit` and `Write` may use Codex's nested `apply_patch` implementation internally without exposing `apply_patch` to the upstream model.

The top-level `wait` and `request_user_input` Functions are not projected through `exec`. They remain direct Functions in both directions.

## Subagents And Namespace Tools

Codex exposes subagent capabilities through Responses namespace tools such as `collaboration` and legacy `multi_agent_v1`. Chat Completions does not have the same nested namespace tool shape.

For Responses-to-Chat routes, Rosetta flattens namespace child tools into ordinary Chat function tools. For example:

```text
multi_agent_v1-spawn_agent
```

During request conversion, Rosetta records the mapping from the flattened tool name to its Responses namespace. The hyphenated `multi_agent_v1-spawn_agent` form is canonical and valid on Chat APIs that restrict Function names to letters, digits, underscores, and hyphens. On return Rosetta also accepts `multi_agent_v1_spawn_agent`, `multi_agent_v1.spawn_agent`, and a bare `spawn_agent` when the selected name belongs to exactly one namespace and does not collide with an ordinary Function. Ambiguous names fail closed. Rosetta then restores the Responses namespace metadata before returning the event to Codex:

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

**OpenAI Responses (Tool Mapping only)** supports Tool Profiles while keeping the rest of the Responses request and response on the direct path. Responses Rosetta, Chat, Anthropic, and Google model groups also support Profile selection and processing. **Chat Default** is temporarily the only bundled Profile; create a copy when a separate pass-through or mapping policy is needed.

The bundled Profile manages current Codex image generation through `image_gen.imagegen`. It does not contain the obsolete hosted `image_generation` tool.

### Tools Page Categories And Card Inputs

The Tools page has four categories:

- **Exec Expansion**: ordinary Chat Functions projected from tools nested in Codex `exec`. Codex flattens namespaced runtime identities into `namespace__function` properties such as `clock__sleep`, `web__run`, and `image_gen__imagegen`; the catalog lists those Functions directly and does not invent parent Namespace cards.
- **Function**: direct Functions and hosted tools managed with the same card shape.
- **Namespace**: fixed tools directly exposed by Codex as Responses Namespaces: `collaboration` and legacy `multi_agent_v1`. Installed plugin, MCP, app, and connector Namespaces are runtime-dynamic and are not part of this static catalog.
- **Rosetta Injection**: the injected `Read`, `Glob`, `Grep`, `Edit`, and `Write` tools.

Namespace states are shown as Expanded, Passthrough (ineffective for Chat API), and Disabled. Disabling a Namespace forces and locks all of its children to Disabled.

Function state **Pass through** is displayed as a direct pass-through choice. For Exec Expansion entries it still performs the representation-only projection and reverse translation described above; it does not add a card description or mutate the model-facing tool description.

A Function, Hosted, or Namespace catalog item may declare multiple `profile_inputs`. Each entry has a stable ID, a localized subtitle, a default value, and a `text`, `password`, `select`, or `textarea` input type. A select declares ordered `{value, label}` options: the Tools page displays each label and persists its value. A textarea may be catalog-owned and read-only so the current Profile value can be inspected and copied without being edited. The Tools page renders the entries in catalog order beneath the tool status selector. The `web_search` and `web.run` cards each own their search Provider and Token; Tavily is currently the only provider. The former standalone Web Search settings tab has been removed.

An input may declare `visible_when` with a list of tool states, for example `["modified"]`. Hidden inputs retain their saved Profile values. A catalog-owned input may also be hidden from the UI entirely while remaining available to runtime Profile mutation. Card descriptions appear in every supported state by default; an item may restrict them with `description_visible_when` using the same state-list format. Modified Functions normally display a localized summary of how the tool description is changed; `create_goal` and `update_goal` instead expose their actual Profile guidance through the read-only textarea above. A catalog item may declare `profile_mutations`: generic Profile processing applies its configured description or parameter-description append operations in Modified, or in Expanded for a Namespace. The Chat Default guidance for `request_user_input`, the Goal tools, and selected `collaboration` Functions uses this mechanism; the converter contains no Function-name-specific guidance. Hosted `web_search` remains protocol-converted in either state, but only Modified can append its Profile guidance.

All Namespace rows start expanded on the Tools page. This display default is independent of each Namespace Profile state, and users can still collapse rows locally.

The bundled **Chat Default** Profile disables the legacy `multi_agent_v1` Namespace while leaving `collaboration` enabled. Collaboration children are flattened for Chat and restored to native Responses namespace calls; they are not translated through Code Mode `exec`. Whenever any Namespace is Disabled, every child Function is forced to Disabled and its state selector is locked until the Namespace is enabled again.

User-entered values are saved with a user Profile under `inputs.<function-item-id>.<input-id>`. Creating a Profile copy carries the current values into the new Profile; switching or resetting a Profile restores its saved values. The bundled Profile allows visible fields to be edited and explicitly saved; those values are stored in `tool_profile_input_overrides.<profile-id>` without changing the bundled JSON. Its tool delivery states remain read-only. Inputs have no effect unless their runtime feature consumes them; currently Modified Functions consume hidden catalog guidance, search and image tools consume their visible provider credentials, and `image_gen.imagegen` consumes its Base URL and Token.
