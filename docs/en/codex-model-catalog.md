# Codex Model Catalog Field Reference

This document describes the model catalog consumed by Codex and how
Codex-Rosetta should use it when exposing third-party models. The current
source-first review target is Codex `0.145.0-alpha.23`, source commit
`655224ffae098a85efeddf8289171ff3bd2624d1`, especially:

- `codex-rs/models-manager/models.json`;
- `codex-rs/protocol/src/openai_models.rs`;
- `codex-rs/models-manager/src/model_info.rs`;
- the consumers under `codex-rs/core`, `codex-rs/tools`, and `codex-rs/ext`.

These are internal Codex contracts, not fields defined by the public OpenAI
API. The alpha.23 source-first review found open adaptation gaps, so this page
is not a compatibility approval. Whenever Codex is upgraded, use the centralized
[`version-compatibility checklist`](../dev/version-compatibility/upgrade-checklist.md)
rather than treating this field reference as an upgrade procedure.

The file envelope is `{"models":[ModelInfo,...]}`. `models` is the only
top-level catalog key; the tables below describe every supported `ModelInfo`
input field and its nested values.

## Scope and field status

The bundled catalog currently contains eight entries:
`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4`,
`gpt-5.4-mini`, `gpt-5.2`, and `codex-auto-review`. Local custom catalog
entries are intentionally excluded from this reference.

Local mode starts from all eight bundled entries only when the gateway has no
configured models. If at least one model is configured, the generated catalog
contains only the configured model names. A configured name matching one of the
eight bundled slugs reuses that entry at the parsed JSON value level before
Rosetta applies its runtime overlays. The packaged asset remains byte-identical
to alpha.23; generated local-mode catalogs additionally carry the legacy
`supports_reasoning_summaries` boolean for Codex 0.144.x clients and therefore
are intentionally not byte-identical to the packaged asset.

### Compaction-hash overlay

Rosetta preserves the upstream catalog asset and applies a runtime-only
`comp_hash` overlay while materializing a catalog. `gpt-5.6-sol`/`terra`/`luna`
retain their reviewed upstream group value (currently `3000`), as do
`gpt-5.5`/`5.4`/`5.4-mini` (currently `2911`). The compact third-party presets
declare reviewed values for DeepSeek V4, GLM 5.2, each Qwen 3.7 variant, MiMo
V2.5, MiniMax M3, Kimi K2.7 Code, and Kimi K3; Rosetta retains built-in fallback groups
for presets that omit the field, as well as `gpt-5.2` and
`codex-auto-review`. Every group is non-empty; equal groups share a value and
different groups must not collide. Unknown aliases get the deterministic
`rosetta-comp-v1:custom:<sha256(upstream_model)>` value. The
configured upstream model name, falling back to the exposed alias when omitted,
selects the preset and is the only routing input to this overlay: Provider
identity never changes the hash, and two aliases mapped to the same upstream
model share it. A compact preset may declare a non-empty `comp_hash`; that exact
value takes precedence over Rosetta's built-in group or deterministic fallback
and follows the preset when an exposed alias maps to its slug. An upstream
missing hash, invalid preset hash, or unreviewed collision fails compatibility
checks instead of silently disabling model-switch compaction.

Local mode also configures Codex to use Rosetta, not only the catalog. It
selects the custom Provider ID `codex_rosetta`, while the generated provider's
`name` is exactly `OpenAI`. This distinction is intentional: Codex resolves
`model_provider` as an ID, but `provider.is_openai()` checks the selected
provider's case-sensitive `name`. The managed provider uses Responses, the
gateway's stable `codex` API key, and the effective local listening port.
Existing non-Rosetta provider tables and their parameters remain untouched.
Local-mode synchronization compares the complete target bytes before each
managed write, so a Provider-only model-group change hot-reloads gateway routing
without rewriting Codex files or asking for a Codex restart.

The Admin Models page also owns three Codex task-model selections while
confirmed local mode is active. The gateway persists them under `codex` in its
own configuration. `codex.auto_review_model_override` is copied onto every
generated catalog entry because Guardian reads the override from the current
turn model's `ModelInfo`; `codex.memories.extract_model` and
`codex.memories.consolidation_model` are written as `extract_model` and
`consolidation_model` in Codex's `[memories]` TOML table. An unset or deleted
selection stays yellow in the editable UI, while a configured alias is green.
When local mode is not confirmed and active, the selects are locked to Codex's
defaults (`codex-auto-review`, `gpt-5.4`, and `gpt-5.4-mini`) and are green only
when that alias is configured, otherwise red. Disabling local mode removes the
two managed TOML memory assignments with the other local-mode artifacts but
retains the gateway selections for a later re-enable.

`codex-auto-review` has one local-mode exception. When its upstream model is
omitted or is also `codex-auto-review`, Rosetta preserves the official bundled
entry, including its unset `tool_mode`. This lets OpenAI or GPT relay services
receive the more native Responses request shape without an unnecessary catalog
override. When the alias is explicitly mapped to a different upstream model,
local mode writes `tool_mode: "code_mode_only"` for that entry. The different
upstream name is the user's signal that the review model is being provided by a
non-OpenAI service; constraining it to the newer Code Mode reduces the number
of legacy and mixed tool surfaces that Rosetta must adapt. This rule uses only
the configured model mapping and does not infer provider identity from names or
URLs.

Rosetta also packages Terra-derived presets for `deepseek-v4-pro`,
`deepseek-v4-flash`, `glm-5.2`, `qwen3.7-plus`, `qwen3.7-max`,
`qwen3.7-max-2026-06-08`, `mimo-v2.5`, `mimo-v2.5-pro`, `minimax-m3`, and
`kimi-k2.7-code`, and `kimi-k3`.
These are materialized only when the exact alias exists in an LLM model group;
they are not part of the eight-entry upstream catalog. Each preset retains the
Terra instruction structure with its model identity replaced and declares its
own context, modalities, reasoning levels, and explicitly selected Codex catalog
fields. MiniMax M3 additionally overrides reasoning-summary support, the default
summary, and byte-based truncation in its preset. Parallel tool calls remain
disabled for every third-party preset until their cross-format call/replay
behavior is independently proven.

The current preset resource contains 25 reviewed `shared_overrides`. Alpha.23
renames the reasoning capability to `supports_reasoning_summary_parameter`; the
runtime and presets now use that exact field. The third-party snapshot
deliberately differs from official Terra by keeping service/speed tiers empty,
original image detail disabled, parallel tool calls disabled, and the reasoning
summary parameter disabled by default. These values are Rosetta safety claims,
not copies of capabilities that were only proven for the official model.

The design uses Terra as the reference client surface while
keeping identity, context, modalities, reasoning levels and their default,
priority, `comp_hash`, and identity-bearing instructions model-specific. Every
supported shared key is also accepted on an individual `models[]` entry, and
`template_slug` fills only fields Rosetta does not yet recognize. Known removed
or ignored fields cannot be resurrected through the template fallback.

The target alpha.23 bundled JSON uses 40 distinct keys. Four are ignored by
`ModelInfo`, leaving 36 consumed bundled fields. `ModelInfo` additionally
accepts the omitted defaulted inputs `effective_context_window_percent=95` and
`supports_reasoning_summary_parameter=true`; `used_fallback_model_metadata` is
runtime-only and cannot be supplied by catalog JSON. The tables therefore
document 42 catalog-facing names: 38 accepted inputs and the four ignored
bundled keys. The runtime-only field is described separately.

Four keys exist in the bundled JSON but are not members of the current Rust
`ModelInfo` type. Serde ignores them when the bundled file is loaded:

- `available_in_plans`;
- `minimal_client_version`;
- `prefer_websockets`;
- `reasoning_summary_format`.

They are marked **ignored in the reviewed 0.144.x baseline** below. Alpha.23
still does not consume these four keys. Rosetta omits them from generated
third-party presets and explicitly blocks `template_slug` from restoring them.
Rosetta must not implement runtime protocol behavior from them unless a later
Codex version starts consuming them. `used_fallback_model_metadata` is the
inverse case: it is an internal `ModelInfo` runtime flag with deserialization
disabled, so it is not a valid catalog input field.

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
| `available_in_plans` | string array, `["plus","team"]` | **Ignored in the reviewed 0.144.x baseline and alpha.23:** not present in `ModelInfo`; omitted from Rosetta third-party presets. | Do not use it for Rosetta authorization or routing. Enforce access at the gateway/provider layer. |
| `minimal_client_version` | string in bundled JSON, `"0.144.0"` | **Ignored in the reviewed 0.144.x baseline and alpha.23:** not present in `ModelInfo`; omitted from Rosetta third-party presets. | Do not rely on it to reject old clients. Use an explicit gateway compatibility policy if required. |

## Reasoning, output, and service tiers

| Field | Type and example | Codex behavior | Rosetta guidance |
| --- | --- | --- | --- |
| `default_reasoning_level` | reasoning effort or null, `"medium"` | Default effort when the user has not selected one. | Choose an effort accepted by the upstream or mapped by Rosetta. It must also appear in `supported_reasoning_levels`. |
| `supported_reasoning_levels` | object array, `[{"effort":"low","description":"Fast"},{"effort":"high","description":"Deep"}]` | Populates selectable reasoning efforts and their UI descriptions. Current enums include `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`, subject to version changes. | Advertise only efforts that the upstream accepts or that Rosetta intentionally maps. Do not copy `ultra` merely to obtain delegation behavior. |
| `supports_reasoning_summary_parameter` | boolean, default `true` | Alpha.23 controls whether Codex may send the Responses `reasoning.summary` parameter. The field is serde-defaulted to true and the old `supports_reasoning_summaries` key is no longer in the target catalog. Rosetta may emit that old boolean only as a runtime compatibility alias for 0.144.x clients; alpha.23 ignores the extra key. | Set false only when the target client must omit the summary parameter and the generated catalog is known to be consumed by the matching Codex source. Treat the legacy key as a compatibility shim, never as the alpha.23 capability source of truth. |
| `default_reasoning_summary` | `"auto"`, `"concise"`, `"detailed"`, or `"none"` | Default summary mode when the user has not configured one. | Prefer `none` for third-party models until summary delivery is verified end to end. |
| `reasoning_summary_format` | string, `"experimental"` | **Ignored in the reviewed 0.144.x baseline and alpha.23:** not present in `ModelInfo`; omitted from Rosetta third-party presets. | Do not branch Rosetta conversion on this key. Inspect actual request and stream fields instead. |
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
| `apply_patch_tool_type` | `"freeform"` or null | Non-null registers the custom/freeform `apply_patch` tool. | Keep `freeform` for the Terra-compatible latest-model profile; Rosetta localizes custom/freeform calls for Chat upstreams. |
| `tool_mode` | `"direct"`, `"code_mode"`, `"code_mode_only"`, or null | Selects direct native tools, a combination with Code Mode, or Code Mode only. Invalid selector strings deserialize to null. | Keep `code_mode_only` for the supported latest-model profile. Rosetta owns the Responses Lite/custom `exec` projection onto Chat. |
| `experimental_supported_tools` | string array, `[]` | Enables named experimental tools known to the current client; current source uses it for narrow test/experimental gates. | Keep empty unless a specific Codex source version defines and Rosetta supports the named tool. |
| `supports_parallel_tool_calls` | boolean, `false` | Allows parallel tool calls. Responses Lite additionally forces parallel calls off in its request shape. | `false` is the safe third-party default. Turn it on only after call IDs, result ordering, and history replay pass concurrent tests. |
| `supports_search_tool` | boolean, `false` | Controls Codex namespace/tool discovery such as native `tool_search`; it is distinct from hosted `web_search` and standalone `web.run`. | Keep true for the Terra-compatible latest-model profile. On Responses→Chat Code Mode routes, Rosetta projects request-local `ALL_TOOLS` search and exact read when the live `exec` description advertises deferred tools. Paired exact Node REPL reads authorize structured dispatcher calls converted back to `exec`; no Gateway namespace/discovery cache is retained. Configure Modified `web.run` in the global Web Search settings. |
| `web_search_tool_type` | `"text"` or `"text_and_image"` | For hosted `web_search`, `text` omits `search_content_types`; `text_and_image` sends `["text", "image"]`. Responses Lite suppresses hosted tools before this field is consulted. | Keep Terra's `text_and_image`. It is dormant on Rosetta's mandatory Responses Lite path: search uses standalone namespace `web.run`, and this field does not configure its provider or output modalities. |
| `use_responses_lite` | boolean, `true` | Enables Codex's Responses Lite dialect: tools/instructions move into input items, internal headers are used, hosted tools are disabled, and standalone namespace tools are expected. | Keep true. Rosetta owns `input[].type="additional_tools"`, developer instructions, custom `exec`, standalone `/v1/alpha/search`, compact/header behavior, and stream conversion for Chat upstreams. |
| `multi_agent_version` | `"disabled"`, `"v1"`, `"v2"`, or null | Selects legacy multi-agent, collaboration v2, disabled, or feature/config fallback behavior. It affects tool definitions and subagent lifecycle. | Keep `v2` for the Terra-compatible latest-model profile. Do not add weak-model downgrade presets to this catalog. |
| `auto_review_model_override` | string or null, `"review-model"` | Redirects command-execution approval review from the selected model to another model. | Set to a Rosetta-exposed alias that is actually routable and suited to approval review. Keep null for ordinary models. |
| `prefer_websockets` | boolean, `true` | **Ignored in the reviewed 0.144.x baseline and alpha.23:** not present in `ModelInfo`; omitted from Rosetta third-party presets, and WebSocket selection is controlled elsewhere. | Do not claim WebSocket support or change Rosetta transport from this field. Verify the actual client request path. |

## Instructions and skills

| Field | Type and example | Codex behavior | Rosetta guidance |
| --- | --- | --- | --- |
| `base_instructions` | string, `"You are a coding agent..."` | Base model instructions used when no valid instruction template overrides them. | Write instructions for the third-party model's real tool and reasoning behavior. Do not copy a large GPT prompt solely to make Codex expose tools. |
| `model_messages` | object or null, `{"instructions_template":"... {{ personality }} ...","instructions_variables":{"personality_default":"","personality_friendly":"...","personality_pragmatic":"..."},"approvals":{"on_request":"...","on_request_auto_review":"..."},"auto_review":{"policy":"...","policy_template":"..."},"permissions":{"danger_full_access":"...","workspace_write":"...","read_only":"..."}}` | If `instructions_template` exists, it always replaces `base_instructions`. A `{{ personality }}` placeholder plus complete variables enables personality-specific text. `approvals` supplies approval-mode messages. Alpha.23 adds `auto_review.policy_template` and sandbox-specific `permissions` messages. | Use null or a small tested template first. If a template is present, keep all critical instructions there because `base_instructions` will not be appended automatically. Treat approval, auto-review, and permission messages as security-sensitive model guidance and copy them only after their behavior has been reviewed. |
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

In the Admin model-group dialog, Rosetta checks the configured upstream model
name first, or the exposed model name when no upstream mapping is present. An
exact slug match in the unified `codex_models.json` catalog or
`codex_model_presets.json` displays the model's `display_name` and derives
text/vision badges from `input_modalities`; partial or suffixed names do not
match. Gateway-side image filtering is narrower: it reads `input_modalities`
only from an exact match in `codex_model_presets.json`. Full Codex catalog
metadata and saved `model_info` remain Codex-facing metadata and do not impose
runtime modality restrictions. The always-visible manual button opens a panel
to the right with every per-model preset field: `slug`, `display_name`,
`description`, `identity`, `priority`, `context_window`, `input_modalities`, and
`supported_reasoning_levels`. A detected preset pre-fills the panel; an
unmatched model starts empty. Saved `model_info` overrides the detected preset,
while the exposed model name remains the catalog slug used for routing. Input
modalities and supported reasoning levels use checkboxes constrained to values
that the catalog template can materialize. When a saved override differs from
the detected preset in any editable field, Admin marks the detection as
modified; the panel's restore action names the detected preset and removes the
override so the preset becomes authoritative again.

### Required baseline for current third-party models

This catalog targets current flagship third-party models, not weak-model
fallbacks. Rosetta exposes one Terra-compatible client surface and translates
its mandatory Responses Lite request shape to the configured Chat protocol.

| Capability | Baseline | Meaning |
| --- | --- | --- |
| Tool surface | `tool_mode: "code_mode_only"`, `apply_patch_tool_type: "freeform"` | Codex exposes the Terra Code Mode surface; Rosetta translates custom tools to Chat. |
| Responses dialect | `use_responses_lite: true` | Additional tools, developer instructions, headers, custom `exec`, Search, compact, and stream continuation are Rosetta-owned compatibility behavior. |
| Parallel calls | `supports_parallel_tool_calls: false` | Concurrent call IDs, result association, replay, and model behavior are stable. |
| Collaboration | `multi_agent_version: "v2"` | Use the current collaboration namespace and lifecycle. |
| Vision | Per-model `input_modalities`, shared `supports_image_detail_original: false` | Model entries declare whether image input is available; original-detail handling remains a protocol-specific shared choice. |
| Reasoning summaries | `supports_reasoning_summary_parameter: false` (only when intentionally disabled), `default_reasoning_summary: "none"` | Alpha.23 defaults the parameter capability to true when omitted. Verify request mapping, streamed summaries, encrypted content, and later turns instead of using the removed old field. |
| Verbosity | `support_verbosity: true`, `default_verbosity: "low"` | Preserve Terra's client behavior. The current Responses→Chat converter does not forward `text.verbosity`, so this is client-surface metadata until an upstream mapping is added. |
| Search | `supports_search_tool: true`, `web_search_tool_type: "text_and_image"` | Responses Lite uses standalone `web.run`; the hosted-search type remains Terra-compatible but dormant. |
| Context | Real provider limits, `auto_compact_token_limit: null` | Long sessions compact and resume before the upstream rejects the request. |
| Compact compatibility | Per-model `comp_hash` | The upstream model name selects the reviewed compact-compatibility group. |

For a new third-party model integration, preserve this surface and add only the
model-specific identity, limits, modalities, reasoning levels, and `comp_hash`.
Do not add `direct`, non-Lite, disabled collaboration, or hosted-search fallback
profiles. A model that cannot use this surface is outside this catalog's scope.

### Current third-party example

The shared values below show the fixed Terra-compatible client surface; the
identity, limits, modalities, reasoning levels, and hash remain model-specific:

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
  "additional_speed_tiers": ["fast"],
  "service_tiers": [
    {
      "id": "priority",
      "name": "Fast",
      "description": "1.5x speed, increased usage"
    }
  ],
  "default_service_tier": null,
  "availability_nux": null,
  "upgrade": null,
  "base_instructions": "You are a coding agent. Use the provided tools exactly as specified.",
  "model_messages": null,
  "include_skills_usage_instructions": false,
  "supports_reasoning_summary_parameter": false,
  "default_reasoning_summary": "none",
  "support_verbosity": true,
  "default_verbosity": "low",
  "apply_patch_tool_type": "freeform",
  "web_search_tool_type": "text_and_image",
  "truncation_policy": {"mode": "tokens", "limit": 10000},
  "supports_parallel_tool_calls": false,
  "supports_image_detail_original": false,
  "context_window": 128000,
  "max_context_window": 128000,
  "auto_compact_token_limit": null,
  "comp_hash": "third-party-agent",
  "effective_context_window_percent": 90,
  "experimental_supported_tools": [],
  "input_modalities": ["text"],
  "supports_search_tool": true,
  "use_responses_lite": true,
  "auto_review_model_override": null,
  "tool_mode": "code_mode_only",
  "multi_agent_version": "v2"
}
```

The four currently ignored bundled keys are omitted deliberately. The
alpha.23 reasoning field rename and permission-message additions must be
reviewed against the exact target source before this example is used for
compatibility claims.

## Upgrade review requirements

The version-specific schema/value diff, consumer tracing, third-party default
review, and real-client gates are centralized in the authoritative
[`Codex version-compatibility checklist`](../dev/version-compatibility/upgrade-checklist.md).
This page remains the user-facing field reference and must be refreshed when
that checklist finds a catalog contract change.
