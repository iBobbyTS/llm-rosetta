# Codex Model Catalog Field Reference

This document describes the model catalog consumed by Codex and how
Codex-Rosetta should use it when exposing third-party models. It is based on
Codex CLI `0.144.1`, source commit
`44918ea10c0f99151c6710411b4322c2f5c96bea`, especially:

- `codex-rs/models-manager/models.json`;
- `codex-rs/protocol/src/openai_models.rs`;
- `codex-rs/models-manager/src/model_info.rs`;
- the consumers under `codex-rs/core`, `codex-rs/tools`, and `codex-rs/ext`.

These are internal Codex contracts, not fields defined by the public OpenAI
API. Re-check the source-contract compatibility points whenever Codex is
upgraded.

The file envelope is `{"models":[ModelInfo,...]}`. `models` is the only
top-level catalog key; the tables below describe every supported `ModelInfo`
input field and its nested values.

## Scope and field status

The bundled catalog currently contains eight entries:
`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4`,
`gpt-5.4-mini`, `gpt-5.2`, and `codex-auto-review`. Local custom catalog
entries are intentionally excluded from this reference.

Local mode writes all eight bundled entries only when the gateway has no
configured models. If at least one model is configured, the generated catalog
contains only the configured LLM names; embedding models are never included, so
an embedding-only configuration produces an empty Codex catalog. A configured
name matching one of the eight bundled slugs reuses that entry byte-for-byte at
the parsed JSON value level.

Local mode also configures Codex to use Rosetta, not only the catalog. It
selects the custom Provider ID `codex_rosetta`, while the generated provider's
`name` is exactly `OpenAI`. This distinction is intentional: Codex resolves
`model_provider` as an ID, but `provider.is_openai()` checks the selected
provider's case-sensitive `name`. The managed provider uses Responses, the
gateway's stable `codex` API key, and the effective local listening port.
Existing non-Rosetta provider tables and their parameters remain untouched.

Rosetta also packages Terra-derived presets for `deepseek-v4-pro`,
`deepseek-v4-flash`, `glm-5.2`, `qwen3.7-plus`, `qwen3.7-max`,
`mimo-v2.5-flash`, `mimo-v2.5-pro`, `minimax-m3`, and `kimi-k2.7-code`.
These are materialized only when the exact alias exists in an LLM model group;
they are not part of the eight-entry upstream catalog. Each preset retains the
Terra instruction structure with its model identity replaced and declares its
own context, modalities, reasoning levels, and explicitly selected Codex capabilities.

The bundled JSON uses 41 distinct keys. `ModelInfo` also accepts
`effective_context_window_percent`, which all bundled entries omit and
therefore receive the default value `95`. This reference consequently covers
42 catalog input fields.

Four keys exist in the bundled JSON but are not members of the current Rust
`ModelInfo` type. Serde ignores them when the bundled file is loaded:

- `available_in_plans`;
- `minimal_client_version`;
- `prefer_websockets`;
- `reasoning_summary_format`.

They are marked **ignored in 0.144.1** below. Rosetta must not implement runtime
protocol behavior from them unless a later Codex version starts consuming
them. `used_fallback_model_metadata` is the inverse case: it is an internal
`ModelInfo` runtime flag with deserialization disabled, so it is not a valid
catalog input field.

## Identity, discovery, and UI

| Field | Type and example | Codex behavior | Rosetta guidance |
| --- | --- | --- | --- |
| `slug` | string, `"third-party-agent"` | Stable model identifier used for catalog lookup, selection, requests, routing, and telemetry. | Make it equal to the model alias exposed by Rosetta. Map that alias to the real upstream model in the Rosetta model group; do not use `display_name` for routing. |
| `display_name` | string, `"Third-Party Agent"` | Human-readable name in model pickers. It is not added to model instructions. | Treat as UI copy only. It must not imply capabilities that the remaining fields do not enable. |
| `description` | string or null, `"Agent model adapted through Rosetta."` | Short model-picker description. It is not added to model instructions. | Describe the tested use case, not the upstream vendor's marketing claims. |
| `priority` | integer, `20` | Sorts model presets; the highest-priority available model can become the default. Lower numeric values have higher priority in the bundled catalog. | Choose a stable, non-conflicting order. Do not copy a top GPT priority merely to force a third-party default. |
| `visibility` | `"list"`, `"hide"`, or `"none"` | Controls whether the preset is shown in model-selection surfaces. `hide` is used for `codex-auto-review`; fallback metadata uses `none`. | Use `list` for user-selectable aliases, `hide` for internal helper models, and `none` for non-picker metadata. |
| `supported_in_api` | boolean, `true` | Propagates into the model preset's API-support marker. | Set only when the exposed alias can actually be routed by Rosetta. This flag does not validate the upstream API. |
| `availability_nux` | object or null, `{"message":"New model available."}` | Optional new-user-experience message shown when a model becomes available. | Usually `null` for private aliases. Never use it as a capability switch. |
| `upgrade` | object or null, `{"model":"replacement","migration_markdown":"Use replacement."}` | Supplies a recommended replacement and migration message for an older model. | Use only for a deliberate alias migration. Keep upstream routing changes in Rosetta configuration, not in this UI hint. |
| `available_in_plans` | string array, `["plus","team"]` | **Ignored in 0.144.1:** not present in `ModelInfo`. | Do not use it for Rosetta authorization or routing. Enforce access at the gateway/provider layer. |
| `minimal_client_version` | string in bundled JSON, `"0.144.0"` | **Ignored in 0.144.1:** not present in `ModelInfo`. | Do not rely on it to reject old clients. Use an explicit gateway compatibility policy if required. |

## Reasoning, output, and service tiers

| Field | Type and example | Codex behavior | Rosetta guidance |
| --- | --- | --- | --- |
| `default_reasoning_level` | reasoning effort or null, `"medium"` | Default effort when the user has not selected one. | Choose an effort accepted by the upstream or mapped by Rosetta. It must also appear in `supported_reasoning_levels`. |
| `supported_reasoning_levels` | object array, `[{"effort":"low","description":"Fast"},{"effort":"high","description":"Deep"}]` | Populates selectable reasoning efforts and their UI descriptions. Current enums include `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`, subject to version changes. | Advertise only efforts that the upstream accepts or that Rosetta intentionally maps. Do not copy `ultra` merely to obtain delegation behavior. |
| `supports_reasoning_summaries` | boolean, `false` | Allows Codex to request reasoning summaries. | Use `false` unless the upstream supports the request/response shape or Rosetta reliably converts or strips it. |
| `default_reasoning_summary` | `"auto"`, `"concise"`, `"detailed"`, or `"none"` | Default summary mode when the user has not configured one. | Prefer `none` for third-party models until summary delivery is verified end to end. |
| `reasoning_summary_format` | string, `"experimental"` | **Ignored in 0.144.1:** not present in `ModelInfo`. | Do not branch Rosetta conversion on this key. Inspect actual request and stream fields instead. |
| `support_verbosity` | boolean, `true` | When true, Codex sends the configured or default Responses `text.verbosity`; when false it omits it. | Enable only when the upstream accepts it or Rosetta strips/maps it. |
| `default_verbosity` | `"low"`, `"medium"`, `"high"`, or null | Default Responses verbosity when supported and not overridden by the user. | Use a value actually supported by the upstream. `null` is safest when `support_verbosity` is false. |
| `service_tiers` | object array, `[{"id":"priority","name":"Fast","description":"Higher speed"}]` | Lists allowed service tiers for UI and subagent/model selection; requested tiers are validated against this list. | Keep empty unless the Rosetta provider maps the tier to a real upstream service class. |
| `default_service_tier` | string or null, `"priority"` | Catalog default tier when no explicit user tier is selected. It still must be valid for the model. | Keep `null` unless the matching ID exists in `service_tiers` and Rosetta forwards or maps it. |
| `additional_speed_tiers` | string array, `["fast"]` | Deprecated predecessor to `service_tiers`. | Prefer `service_tiers`; keep this empty for new third-party entries unless the target Codex UI still requires the legacy flag. |

## Context, compaction, and input

| Field | Type and example | Codex behavior | Rosetta guidance |
| --- | --- | --- | --- |
| `context_window` | positive integer or null, `128000` | Current context-window size used for budgeting and compaction. | Set the real usable upstream limit after accounting for any provider limit. Never copy a GPT value to unlock a larger UI limit. |
| `max_context_window` | positive integer or null, `128000` | Upper bound for a user/config override of `context_window`; also a fallback when the current window is absent. | Set to the upstream's verified maximum. Usually make it equal to `context_window` for a fixed-limit model. |
| `effective_context_window_percent` | integer percentage, `95` | Reserves headroom when calculating effective input capacity. It defaults to `95` when omitted; bundled entries rely on that default. | Lower it if prompts, tool definitions, or provider output reserves need more headroom. Keep it within a meaningful `1..=100` range even though the current type alone does not enforce that range. |
| `auto_compact_token_limit` | positive integer or null, `100000` | Explicit automatic-compaction threshold. When null, Codex derives 90% of the resolved context window; an explicit value is also clamped to that 90% ceiling. | Usually leave null. Set it only after real long-session tests show that the upstream needs an earlier threshold. |
| `comp_hash` | opaque string or null, `"third-party-prompt-v1"` | Marks compact-history compatibility. When switching models, non-null unequal hashes cause Codex to compact with the previous model first; if either side is null, this compatibility action is skipped. | Default to null. Share a hash only when prompts, tools, compact history, and replay semantics are intentionally compatible. |
| `truncation_policy` | object, `{"mode":"tokens","limit":10000}` or `{"mode":"bytes","limit":10000}` | Locally truncates tool output/history according to a token or byte budget. It is not the model context window or API output-token limit. | Prefer `tokens` when the model's context pressure is token-based and the tokenizer estimate is acceptable; use `bytes` for a conservative provider-neutral bound. Test large command and web results. |
| `input_modalities` | array of `"text"` and/or `"image"`, `["text"]` | Declares accepted user-input modalities. If `image` is absent, Codex removes images from carried history for that model. Omission defaults to both text and image for backward compatibility. | Explicitly use `["text"]` for a text-only third-party model. Add `image` only after Rosetta and the upstream both preserve image inputs. |
| `supports_image_detail_original` | boolean, `false` | Allows image inputs with `detail: "original"`; false causes original detail to be downgraded/cleared. It does not by itself disable all images. | Keep false unless original-resolution input is accepted across the selected protocol and upstream. Coordinate with `input_modalities`. |

## Tools and execution mode

| Field | Type and example | Codex behavior | Rosetta guidance |
| --- | --- | --- | --- |
| `shell_type` | `"default"`, `"local"`, `"unified_exec"`, `"disabled"`, or `"shell_command"` | Selects the Codex shell-tool family before feature/config overrides. Bundled models use `shell_command`. | Choose the tool form the third-party model can call reliably and that Rosetta maps. `disabled` is safer than advertising an unsupported command schema. |
| `apply_patch_tool_type` | `"freeform"` or null | Non-null registers the custom/freeform `apply_patch` tool. | Use `freeform` only when the model produces stable patches and the selected Rosetta protocol preserves custom tool source and results. Otherwise use null. |
| `tool_mode` | `"direct"`, `"code_mode"`, `"code_mode_only"`, or null | Selects direct native tools, a combination with Code Mode, or Code Mode only. Invalid selector strings deserialize to null. | Start weak tool callers on `direct`. Use `code_mode_only` only when the model reliably writes valid JavaScript for custom `exec`; use `code_mode` when both surfaces are intentionally supported. |
| `experimental_supported_tools` | string array, `[]` | Enables named experimental tools known to the current client; current source uses it for narrow test/experimental gates. | Keep empty unless a specific Codex source version defines and Rosetta supports the named tool. |
| `supports_parallel_tool_calls` | boolean, `false` | Allows parallel tool calls. Responses Lite additionally forces parallel calls off in its request shape. | `false` is the safe third-party default. Turn it on only after call IDs, result ordering, and history replay pass concurrent tests. |
| `supports_search_tool` | boolean, `false` | Controls Codex namespace/tool discovery such as `tool_search`; it is distinct from hosted `web_search` and standalone `web.run`. | Enable only if the client-visible discovery flow and Rosetta namespace restoration are tested. Configure hosted search and `web.run` independently in Tool Profiles. |
| `web_search_tool_type` | `"text"` or `"text_and_image"` | Chooses the content types declared for hosted web search. | Use `text` unless image-search results are supported by both the client path and upstream. This does not configure Tavily credentials or `web.run` mapping. |
| `use_responses_lite` | boolean, `false` | Enables Codex's Responses Lite dialect: tools/instructions may move into input items, internal headers are used, hosted tools are disabled, and standalone namespace tools are expected. | Set true only if Rosetta handles `input[].type="additional_tools"`, developer instructions, custom `exec`, standalone `/v1/alpha/search`, compact/header behavior, and the resulting stream. |
| `multi_agent_version` | `"disabled"`, `"v1"`, `"v2"`, or null | Selects legacy multi-agent, collaboration v2, disabled, or feature/config fallback behavior. It affects tool definitions and subagent lifecycle. | Use `disabled` or null until the third-party model reliably completes the corresponding tool loop. Prefer v1 over v2 when collaboration-specific schemas are not stable for that model. |
| `auto_review_model_override` | string or null, `"review-model"` | Redirects command-execution approval review from the selected model to another model. | Set to a Rosetta-exposed alias that is actually routable and suited to approval review. Keep null for ordinary models. |
| `prefer_websockets` | boolean, `true` | **Ignored in 0.144.1:** not present in `ModelInfo`; WebSocket selection is controlled elsewhere. | Do not claim WebSocket support or change Rosetta transport from this field. Verify the actual client request path. |

## Instructions and skills

| Field | Type and example | Codex behavior | Rosetta guidance |
| --- | --- | --- | --- |
| `base_instructions` | string, `"You are a coding agent..."` | Base model instructions used when no valid instruction template overrides them. | Write instructions for the third-party model's real tool and reasoning behavior. Do not copy a large GPT prompt solely to make Codex expose tools. |
| `model_messages` | object or null, `{"instructions_template":"... {{ personality }} ...","instructions_variables":{"personality_default":"","personality_friendly":"...","personality_pragmatic":"..."},"approvals":{"on_request":"...","on_request_auto_review":"..."}}` | If `instructions_template` exists, it always replaces `base_instructions`. A `{{ personality }}` placeholder plus complete variables enables personality-specific text. `approvals` supplies approval-mode messages. | Use null or a small tested template first. If a template is present, keep all critical instructions there because `base_instructions` will not be appended automatically. |
| `include_skills_usage_instructions` | boolean, `false` | Controls whether Codex appends the full “How to use skills” tutorial to the skills fragment. It does not control whether the available-skills list itself is sent. | Keep false unless the third-party model materially benefits from the longer tutorial and has enough context budget. |

### `base_instructions` and `model_messages` in bundled models

The catalog stores full strings, but the useful compatibility distinction is
their structure rather than their exact prose:

| Models | Prompt shape |
| --- | --- |
| `gpt-5.6-sol` | Newer app-oriented instructions with a fixed personality. Its template mirrors the base instructions and does not use a personality placeholder. |
| `gpt-5.6-terra`, `gpt-5.6-luna` | Identical to each other and close to Sol, with small ordering, visualization, and formatting differences. |
| `gpt-5.5` | Separate engineering/frontend prompt with dynamic Friendly and Pragmatic personalities. It is the only bundled entry with `include_skills_usage_instructions: true`. |
| `gpt-5.4`, `codex-auto-review` | Identical base instructions and model-message templates, with dynamic personalities. |
| `gpt-5.4-mini` | Similar to 5.4 but shorter file-link and final-answer guidance, with the same personality variables. |
| `gpt-5.2` | Longer legacy prompt containing AGENTS, plan examples, validation guidance, tool guidance, and `update_plan`; no dynamic personality. |

## Rosetta adaptation model

Treat catalog metadata as a declaration of what the Codex client should expose
and how it should budget a session. It is not a second Rosetta routing system.

```text
Codex catalog slug and capabilities
             |
             v
Codex request shape and exposed tools
             |
             v
Rosetta model group: alias -> upstream model + protocol
             |
             v
Rosetta Tool Profile: pass through / modify / disable / inject
```

The model group remains the source of truth for provider, upstream model,
protocol, and Tool Profile. The catalog determines what Codex attempts to send;
Rosetta must then either support that shape or advertise a safer catalog value.

### Recommended decisions for a third-party model

| Capability | Safe starting value | Enable only after proving |
| --- | --- | --- |
| Tool surface | `tool_mode: "direct"`, conservative `shell_type` | The model emits the exact custom/code-mode payload if moving to `code_mode` or `code_mode_only`. |
| Responses dialect | `use_responses_lite: false` | Additional tools, developer instructions, headers, custom `exec`, Search, compact, and stream continuation all survive Rosetta. |
| Parallel calls | `supports_parallel_tool_calls: false` | Concurrent call IDs, result association, replay, and model behavior are stable. |
| Collaboration | `multi_agent_version: "disabled"` or null | The model can call and recover across every tool in the selected v1/v2 namespace. |
| Vision | `input_modalities: ["text"]`, `supports_image_detail_original: false` | Images and original detail survive the selected protocol and upstream. |
| Reasoning summaries | `supports_reasoning_summaries: false`, `default_reasoning_summary: "none"` | Request mapping, streamed summaries, encrypted content, and later turns remain valid. |
| Verbosity | `support_verbosity: false`, `default_verbosity: null` | The upstream accepts or Rosetta removes/maps Responses `text.verbosity`. |
| Search discovery | `supports_search_tool: false`, `web_search_tool_type: "text"` | Namespace discovery, hosted search, and `web.run` are separately tested and configured. |
| Context | Real provider limits, `auto_compact_token_limit: null` | Long sessions compact and resume before the upstream rejects the request. |
| Compact compatibility | `comp_hash: null` | Two aliases truly share prompts, tools, compact history, and replay semantics. |
| Patch editing | `apply_patch_tool_type: null` | The model and Rosetta reliably preserve freeform patch calls and error recovery. |

For a new third-party model integration, prefer the current Codex tool surface
when both choices can be supported reliably: prefer standalone namespace
`web.run` over legacy hosted `web_search`, and collaboration v2 over legacy
`multi_agent_v1`. Keep the older surface only for a model that cannot yet call
the newer schema reliably. “Prefer newer” does not override the evidence gates
above: start disabled, then enable the newer surface after its complete
search/open or subagent lifecycle succeeds through Rosetta.

### Conservative third-party example

This example is intentionally a starting point, not a claim that every
third-party model should use the same prompt or limits:

```json
{
  "slug": "third-party-agent",
  "display_name": "Third-Party Agent",
  "description": "Third-party model adapted through Codex-Rosetta.",
  "default_reasoning_level": "medium",
  "supported_reasoning_levels": [
    {"effort": "low", "description": "Lower latency"},
    {"effort": "medium", "description": "Balanced"}
  ],
  "shell_type": "shell_command",
  "visibility": "list",
  "supported_in_api": true,
  "priority": 20,
  "additional_speed_tiers": [],
  "service_tiers": [],
  "default_service_tier": null,
  "availability_nux": null,
  "upgrade": null,
  "base_instructions": "You are a coding agent. Use the provided tools exactly as specified.",
  "model_messages": null,
  "include_skills_usage_instructions": false,
  "supports_reasoning_summaries": false,
  "default_reasoning_summary": "none",
  "support_verbosity": false,
  "default_verbosity": null,
  "apply_patch_tool_type": null,
  "web_search_tool_type": "text",
  "truncation_policy": {"mode": "tokens", "limit": 10000},
  "supports_parallel_tool_calls": false,
  "supports_image_detail_original": false,
  "context_window": 128000,
  "max_context_window": 128000,
  "auto_compact_token_limit": null,
  "comp_hash": null,
  "effective_context_window_percent": 90,
  "experimental_supported_tools": [],
  "input_modalities": ["text"],
  "supports_search_tool": false,
  "use_responses_lite": false,
  "auto_review_model_override": null,
  "tool_mode": "direct",
  "multi_agent_version": "disabled"
}
```

The four currently ignored bundled keys are omitted deliberately. Adding them
would not change Codex 0.144.1 runtime behavior.

## Upgrade review requirements

On every Codex source upgrade, compare both the schema and the bundled values:

1. Diff `ModelInfo`, nested structs, enums, serde rename/default/skip behavior,
   and the unknown-model fallback initializer.
2. Diff the complete key set and model entries in
   `codex-rs/models-manager/models.json`, including fields that the current
   client ignores.
3. Trace each changed consumed field to its callers in core, tools, extensions,
   UI/model preset conversion, session persistence, and request construction.
4. Reclassify Rosetta's third-party defaults and aliases. A copied GPT value is
   not compatible merely because it still deserializes.
5. Test every changed request/tool mode with a real Codex client and the actual
   upstream model, then confirm the model-facing request in Rosetta logs.

The authoritative checklist and compatibility point are maintained under
[`docs/dev/version-compatibility`](../dev/version-compatibility/README.md).
