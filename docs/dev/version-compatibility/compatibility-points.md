# Codex-Specific Compatibility Points

## Judgment criteria

This document only includes behaviors that meet any of the following conditions:

- Rely on Codex-specific header, request item, tool schema, SSE event or model metadata;
- Exists to maintain Codex agent loop, history replay, tool execution or UI phase semantics;
- Behavior regression may occur after a Codex upgrade even if the OpenAI Responses API is still "well-formed".

Common provider conversion capabilities are not separately listed as Codex compatibility points.

## Daily maintenance requirements

This document is the only list of Codex-specific compatibility points. As long as a Codex-specific behavior is added, modified or discovered during daily development, it must be updated in the same task:

1. Current implementation, main code locations and upgrade risks;
2. **Can automatically complete** static, fixture, component or local integration checks;
3. Real Codex/API scenarios that **must be actually tested**.

Even if a certain automation has not yet been implemented, write out the necessary automated checks and mark the backlog. Even if a certain upgrade is judged to have no change with high confidence, its real test definition cannot be deleted; whether the real test is triggered in this upgrade is determined by the upgrade classification.

## Current upgrade status

Codex `0.144.4` compatibility is **pending / not approved**. The controlled `deepseek-v4-flash` runs from earlier versions remain evidence only for the third-party Responses→Chat, Lite/code-mode, and successful `exec` paths they exercised. They are not native GPT or 0.144.4 auto-review evidence, and they do not close the required compact/resume/fork, plugin/MCP/deferred-tool, UI-phase, Desktop-tool, changed-error, or mapped/unmapped auto-review scenarios. Responses WebSocket, incremental history, and remote compact remain unsupported. The current scoped review is recorded in [`reports/20260714-codex-v0.144.4.md`](reports/20260714-codex-v0.144.4.md); older reports remain historical evidence.

## Current compatibility overview

| Boundaries | Current Implementation | Primary Locations | Upgrade Risks |
| --- | --- | --- | --- |
| Agent-facing API | Expose `/v1/responses` to Codex; Chat/Anthropic/Google as upstream target format; accept full-history image sessions under a configurable 64/128/256/512/1024 MiB or unlimited inbound body limit, defaulting to 128 MiB. Authenticated `/v1` Zstd bodies are decoded before JSON parsing, with the same live WebUI limit independently enforced before and after decompression | `gateway/app.py`, `gateway/inbound_content_encoding.py`, `gateway/config.py`, Admin config/UI, `gateway/proxy.py` | Codex changes endpoint, transport, request shape, request compression, or retained image/history size |
| Responses internal handling mode | Admin exposes Responses Tool Mapping only and experimental Responses Rosetta as internal handling choices, not distinct wire protocols. Both target `openai_responses`; Tool Mapping only applies the selected Profile before direct transport and retains unknown non-tool body fields, original response JSON and original SSE bytes, while Rosetta selects the existing Responses→IR→Responses pipeline without expanding field/event coverage or guaranteeing compatibility. Uncompressed response bytes remain unchanged; authenticated Zstd ingress is decoded once before JSON handling and forwarded without the encoding header. The response transport enforces 1 MiB per line and 8 MiB per event with no total successful-stream cap | `gateway/config.py`, `gateway/inbound_content_encoding.py`, `gateway/tool_profiles.py`, `routing.py`, Admin provider UI, `gateway/proxy.py`, `test_responses_passthrough.py`, `test_tool_profiles.py`, `test_inbound_content_encoding.py`, `test_config.py`, `test_http_transport_limits.py` | Codex emits a required field/event that IR cannot preserve, changes request content encoding, emits a required single line or event above the safety envelope, or changes away from HTTP/SSE framing |
| Codex Search and Images endpoints | Expose JSON `POST /v1/alpha/search`, `/v1/images/generations`, and `/v1/images/edits`. `image_gen.imagegen` Passthrough retains direct Tool Mapping only routing; Modified resolves its Profile Base URL/Token and forwards unchanged OpenAI Images generation/edit JSON with the configured model alias across every LLM protocol, while Disabled rejects the endpoint. No vendor-private image API translation is attempted. Codex 0.144.4 separately gates model-facing image generation on image modality, the feature toggle, provider capability, and either OpenAI actor authorization or real Codex-backend auth; the local-mode bearer-token Provider does not satisfy that auth gate even though its display name is `OpenAI`. Rosetta projects only a live Codex declaration and deliberately does not invent a missing `image_gen.imagegen`, so the endpoint can be configured and directly tested while the agent tool remains absent. A Profile with Modified `web.run` and converted routes locally implement atomic `search_query` through Tavily, direct-public-URL or `turnXsearchY` static HTML/plain-text `open` through a bounded Python fetcher, and fixed-offset `time`. Search references are allocated atomically and cached for retry stability in an app-owned, bounded 24-hour store scoped by authenticated principal plus `SearchRequest.id`; model and `x-codex-window-id` are deliberately excluded so references survive model changes and compaction. Open revalidates public addressing on every redirect and returns normalized line-addressable text; unknown/cross-session references fail closed, while browser rendering, compressed/non-text pages and the remaining unsupported commands/settings fail before partial execution. Gateway Logs record local request/result/error stages and reference/cache counts without image tokens | `gateway/codex_auxiliary.py`, `gateway/codex_images.py`, `gateway/codex_search.py`, `gateway/codex_search_references.py`, `gateway/codex_page.py`, `gateway/tool_profiles.py`, `gateway/app.py`, `routing.py`, `test_codex_search.py`, `test_codex_search_references.py`, `test_codex_page.py`, `test_codex_auxiliary.py`, `test_downstream_routes.py`, `../openai-codex-src/codex-rs/core/src/tools/spec_plan.rs`, `../openai-codex-src/codex-rs/ext/image-generation/` | Codex changes endpoint paths, Images API body/response, SearchRequest commands/settings, required headers, SearchResponse shape, `SearchRequest.id` lifecycle, image-generation auth/feature/modality exposure gates, open reference/line semantics, or no longer includes routing model/session identity |
| Request and window identity | Read `x-codex-window-id` as the authenticated session key for tool mapping, provider continuation metadata, deferred tools and phase behavior; enforce the documented 128-byte window and 256-byte model identity envelopes before routing/state allocation; keep external `x-request-id` correlation-only and require 1–128 visible ASCII bytes before body/log/trace/persistence/state/upstream use, generating a UUID when absent; use a private nonce when no window exists, and clear request-local state at normal/error/cancel completion | `gateway/app.py`, `gateway/proxy.py`, `gateway/state_scope.py`, `gateway/headers.py` | Codex changes to only send canonical `client_metadata`, changes window or request-ID semantics, or needs an identity above the safety envelope |
| Responses→Chat bridge | Convert Codex Responses request to Chat via IR, expand Namespace children as the regex-safe canonical `namespace-function`, and then rebuild Responses output. Response restoration also accepts `namespace_function`, `namespace.function`, or a bare child name only when exactly one Namespace owns it and no top-level Function has that name; ambiguous names remain flat so Codex fails closed. Codex `agent_message` input becomes a Chat-visible user message, including its inter-agent payload carried in `content[].encrypted_content`, while ordinary message/reasoning encrypted content remains opaque | `converters/openai_responses/**`, `gateway/proxy.py` | High; new item/event/fields will not be automatically transparently transmitted, Namespace or Function naming changes can create ambiguous restore candidates, and inter-agent payload carriers can change independently of ordinary messages |
| Responses Lite / `additional_tools` | Responses→Responses can be transmitted transparently as is; Responses→Chat merges the top-level tools with `input[].type=additional_tools`, retains the developer instructions, and removes duplication according to the final Chat name | `converters/openai_responses/message_ops.py`, `converter.py`, `gateway/proxy.py` | High; 0.144.0 model catalog Responses Lite has been enabled for some models, the location of tools and developer instructions will change |
| Codex model catalog | Treat the bundled catalog and local catalog overrides as Codex client capability declarations, while Rosetta model groups remain the routing source of truth for alias, upstream model, protocol, and Tool Profile. Default-enabled local mode preserves the eight Codex 0.144.4 entries in source order, selects exact-name Terra-derived third-party presets before the generic Terra fallback, and transactionally maintains the selected Codex Home's `model_catalog.json` plus root `model_catalog_json`. For `codex-auto-review`, it preserves the official unset `tool_mode` when the upstream is omitted or has the same name, but forces `code_mode_only` when the alias maps to another upstream model. It also ensures one stable, non-rotating gateway key named `codex`, selects Provider ID `codex_rosetta`, and replaces only the managed `[model_providers.codex_rosetta]` table with a Responses provider named exactly `OpenAI`, using the effective loopback port while preserving every other Provider table. Named presets are materialized only for configured aliases; their compact resource owns identity, context, modality, reasoning, and shared capability overrides without duplicating Terra's large prompts. CLI and WebUI first-use confirmation, startup rebuild, and model-group mutations share this file owner; CLI `--no-local-mode` persistently disables synchronization without touching Codex Home, while the explicit clear command removes managed artifacts | `gateway/codex_models_0_144_4.json`, `gateway/codex_model_presets.json`, `gateway/local_mode.py`, `gateway/cli.py`, `gateway/config.py`, Admin config/UI, `test_local_mode.py`, `test_cli_local_mode.py`, `test_admin_config_routes.py`, `../openai-codex-src/codex-rs/models-manager/models.json`, `docs/en/codex-model-catalog.md`, `docs/zh-cn/codex-model-catalog.md` | A Codex upgrade changes fields, nested types, enum values, serde defaults/skip behavior, fallback metadata, bundled model count/order/values, prompt precedence, catalog file loading, `model_catalog_json`, `model_provider`, configured Provider parsing, `is_openai()` semantics, or the requests/tools selected by those values; Terra-derived presets, auto-review tool-mode selection, and the managed Provider identity must be revalidated against the new source and actual upstreams |
| custom/freeform tool | Identify `apply_patch` and Code Mode `exec` of Responses `type: custom`, convert into Chat callable form, and restore Codex-native tool type, call/output in response return. For Chat Default, treat the Disabled parent `exec` as a conversion-only container: retain it through Responses source filtering, parse selected nested declarations from its live description, project them as normal Chat Functions, then remove the parent before the outbound Chat request. Model calls are deterministically rebuilt as custom `exec` JavaScript without duplicating Codex schemas. Unknown declaration syntax and an entirely unparseable container fail closed without re-exposing Disabled `exec`. JSON Schema constraints omitted by Codex's TypeScript renderer remain irrecoverable | `openai_responses/converter.py`, `openai_responses/tool_ops.py`, `gateway/tool_profiles.py`, `gateway/proxy.py`, `gateway/code_mode_projection.py`, `gateway/tool_adaptation.py`, `test_tool_profiles.py`, `test_code_mode_projection.py` | Codex changes the custom grammar, TypeScript schema renderer, exec section/declaration syntax, normalized nested names, helper output contract, call/output/delta event, or internal-container lifecycle |
| Code tool localization | Inject selected `Read`/`Edit`/`Write`/`Glob`/`Grep` definitions and translate responses back to Codex-native calls. Chat Default projects selected nested `exec_command`, `write_stdin`, `update_plan`, `view_image`, `web.run`, Goal, Clock, Memories, Skills, and conditionally `image_gen` tools from Code Mode `exec`, while preserving same-named direct Functions. Image generation is projected only when Codex supplied its live declaration after its own auth/feature/modality gates; Profile Modified state does not inject a declaration that Codex omitted. Passthrough and Modified both enable the representation-only projection; only Modified applies Profile description mutations. The parent `exec` is Disabled for upstream model exposure but retained internally until projection and reverse-translation metadata are established, then always omitted from outbound Chat tools. It emits `generatedImage(...)` for projected image generation and text/image helpers for their matching result contracts. It disables model-facing `apply_patch` but retains its parsed nested declaration as an internal projection so localized `Edit`/`Write` can rebuild native `exec → tools.apply_patch` calls. It leaves top-level `wait`/`request_user_input` and `collaboration` direct, disables `shell_command`, and does not define or inject `Bash`. Parse failures, missing projections, and direct-name conflicts fail closed; persisted historical Bash mappings remain readable | `gateway/code_mode_projection.py`, `gateway/tool_adaptation.py`, `gateway/admin/tool_catalog.json`, `test_code_mode_projection.py`, `test_tool_adaptation.py`, `test_admin_tools_catalog.py` | Tool name, exec declaration/schema syntax, helper output contract, call id, execution result format, direct-versus-nested availability, Codex image-generation exposure gates, or Chat Default Profile state changes |
| Tool history consistency | Persist exact native/localized mappings by call id as authenticated encrypted SQLite state, including projected Chat Function to custom `exec` mappings; rewrite subsequent history from SQLite within the 24-hour TTL, apply TTL cleanup, and enforce 16 MiB per row, 2,048 rows/64 MiB per session, 8,192 rows/256 MiB per principal, and 32,768 rows/512 MiB globally | `gateway/proxy.py`, `gateway/tool_adaptation.py`, `observability/persistence.py`, `observability/tool_mapping_crypto.py` | Codex history replay, compact or output shape changes; custom `exec` call representation; database/key backup mismatch; mapping size or session fan-out changes |
| Deferred tool discovery | Temporary `namespace` tools by authenticated Codex window, inject/process `tool_search`, restore `tool_search_call/output`, and atomically enforce 1,024 tools/16 MiB per scope, 256 unique scopes per principal, 1,000 scopes per retained map, and 64 MiB per app without cross-principal eviction | `gateway/proxy.py::WindowToolSearchStore`, Responses converter | namespace/tool_search schema, execution, compact behavior, or retained payload size/window fan-out changes |
| Codex tool usage tips | Supplement the Chat model with `request_user_input`, `create_goal`, `update_goal` and other Codex tool calling constraints; their Admin card descriptions are shown only in Modified state | `converters/openai_chat/tool_ops.py`, tool catalog, related pipeline tests | schema, mode availability or Desktop/runtime tool contract changes |
| Web search bridge | Codex `web_search` can be exposed to the Chat model, the `web_search_call` event can be reconstructed and continued after Tavily execution, and Tavily responses use the primary bounded identity-encoded HTTP reader. `web_search` and `web.run` independently resolve their Provider/Token from the selected Tool Profile; Tavily is the only supported provider and the former global Web Search settings tab is absent | `gateway/web_search.py`, `gateway/codex_search.py`, `gateway/codex_auxiliary.py`, `gateway/transport/http/transport.py`, `test_web_search_bridge.py` | native web-search item/event, tool configuration, provider options, or auxiliary response behavior changes |
| Stream lifecycle | Rebuild `response.created`, item added/delta/done, `response.completed`, etc. from Chat chunks Responses SSE; classify normal EOF, provider error, client cancellation, and bounded line/event overflow consistently in transport/telemetry/trace | `openai_responses/converter.py`, `gateway/proxy.py`, `gateway/transport/http/transport.py` | Codex parser adds required events, sequences or termination conditions; downstream disconnect semantics or maximum required event size changes |
| Message phase | Use tool calls and terminal events to infer `commentary`/`final_answer`, write phase back to message item; override native tool/web search signal | `gateway/stream_phase_buffer.py`, `test_stream_phase_buffer.py` | phase enumeration or Codex mailbox/final-answer semantic changes |
| Reasoning | Convert reasoning effort/summary, retain reasoning summary/content, `reasoning_content` and `encrypted_content` | Responses/Chat content, config, stream converters | New effort, summary delivery, reasoning event or encryption status change |
| Context compaction resilience | Remove orphan `tool_choice/tool_config` that has no tools but remains after compact; keep tool history replayable | `converters/base/helpers/tool_orphan_fix.py`, `test_strip_orphaned_tool_config.py` | Codex compact output, window generation or historical clipping changes |
| GPT relay provider identity | Codex gates sequential-cutoff reasoning delivery and internal ChatGPT item metadata on the selected Provider's case-sensitive display name `OpenAI`, not its `model_provider` ID; request Zstd and current-model compact fallback additionally require Codex-backend auth. Local mode therefore uses ID `codex_rosetta` with `name = "OpenAI"`, plus an explicit bearer token that keeps the configured Provider on the custom-token auth path. The provider-neutral real-service A/B suite sends both identities through the same selected relay and labels synthetic backend-auth cells | `gateway/local_mode.py`, `tests/integration/gpt_relay/`, `../openai-codex-src/codex-rs/model-provider-info/src/lib.rs`, `../openai-codex-src/codex-rs/model-provider/src/provider.rs`, `../openai-codex-src/codex-rs/core/src/session/turn.rs`, Codex request client and history normalization | Codex changes `is_openai`, configured bearer-token precedence, auth classification, request compression, metadata clearing, reasoning-summary delivery, remote compact error classification, or fallback order; a relay accepts ordinary API-key requests but rejects an OpenAI-identity wire variant |
| Model-group tool profiles | Select the bundled `builtin` Chat Default or a complete user Profile for every LLM model group. The bundled tool delivery states are immutable, while visible card fields can be explicitly saved as input-only `tool_profile_input_overrides` without modifying the packaged JSON. The Admin catalog is presented as Exec Expansion, Function, Namespace, and Rosetta Injection. Chat Default disables legacy `multi_agent_v1`, upstream-visible `apply_patch`, and the parent `exec`; the latter two remain available only at their declared internal conversion layers. It preserves direct `wait`/`request_user_input`/`collaboration`, projects the selected Code Mode nested tools, injects `Read`/`Glob`/`Grep` plus `Edit`/`Write`, and marks `image_gen__imagegen` Modified so its Base URL/Token fields can configure the OpenAI Images bridge; any Disabled Namespace forces all child Function states to Disabled and locks their selectors. Namespace states are Expanded, Passthrough (ineffective for Chat API), and Disabled. Function Passthrough is localized as `直通`; for pure exec expansion it projects and reverses the current declaration without adding catalog text. Modified is reserved for entries with Profile-owned guidance or additional Rosetta behavior. Its cards normally show localized mutation summaries; `create_goal` and `update_goal` expose the actual selected-Profile guidance through a localized, read-only textarea. Supported paths apply per-tool disabled/passthrough/modified and injection disabled/injected states. Inputs support localized text/password/select/textarea values, state-based display, read-only textarea presentation, and UI-hidden runtime values. Modified `image_gen__imagegen` consumes its OpenAI Images Base URL/Token, while Modified `web_search` and `web__run` consume their independent search Provider/Token. The bundled Profile omits hosted `image_generation` | `gateway/tool_profiles.py`, `gateway/config.py`, `gateway/codex_auxiliary.py`, `gateway/code_mode_projection.py`, Admin tools/model-group UI, `gateway/proxy.py` | Codex tool IDs, input-definition IDs/types/defaults/localization, internal-container declarations, exec projection declarations, supported transformations, namespace expansion, injected localization dependencies, or Responses processing-mode semantics change |
| Static tool catalog | Package the fixed tool inventory, policy capabilities, optional Profile inputs/mutations, and immutable Chat Default Profile source, bound to Codex CLI `0.144.4` and source commit `8c68d4c87dc54d38861f5114e920c3de2efa5876`. Code Mode Namespace members nested under `exec` are listed by their actual flat `namespace__function` names; only direct Responses Namespaces retain parent Namespace items. Exclude `tool_search`, obsolete hosted `image_generation`, and runtime-dynamic MCP/plugin/app/connector tools | `src/codex_rosetta/gateway/admin/tool_catalog.json` | Fixed tool sets, direct-versus-exec placement, namespace members, profile input definitions, wire types, aliases, or model-controlled availability change |

## Compatibility point test matrix

| Compatibility points | Can be automated | Must be actually tested |
| --- | --- | --- |
| Agent-facing API | Routing, method, content type, SSE terminal/error fixture; fixed-tier body-limit validation before and after Zstd decoding, malformed/trailing Zstd rejection, authenticated decode ordering, Admin persistence/hot reload, default and unlimited runtime mapping; fake upstream single-round and multi-round playback | Real Codex completes single/multi-round via gateway, including an OpenAI-identity Zstd request and an image-heavy request above the old 50 MB ceiling; the session ends normally and errors are visible |
| Responses internal handling mode | Tool Mapping only changes only the selected Profile's tool behavior and does not rewrite unknown non-tool request fields, original response JSON, original SSE bytes or terminal events; experimental Rosetta mode demonstrably selects the conversion pipeline without claiming full field/event compatibility; raw response passthrough enforces line/event caps without byte changes and closes on overflow | Confirm OpenAI/GPT proxies use Tool Mapping only with Chat Default and copied pass-through/web.run-mapping Profiles. Treat third-party Responses implementations such as Qwen through Rosetta as unverified until real requests complete and can be continued; observe unsupported fields/events and whether any required event approaches the cap |
| Codex Search and Images endpoints | Verify all three POST routes, model validation/aliasing, native pass-through path/body/status/header/error behavior, Profile-selected OpenAI Images Base URL/Bearer token forwarding for generation and edits on every LLM protocol, missing/invalid image configuration, Disabled handling, secret-free Gateway Logs, Profile-selected local Tavily search, bounded static direct-URL and stored-reference open, authenticated principal/`SearchRequest.id` isolation, retry-stable and concurrent reference allocation, TTL/capacity cleanup, Python time behavior, public-address/redirect/content-type/size/line handling, domain/context/length mapping, atomic mixed-command rejection, and stable unsupported-feature errors. Projection fixtures must cover both a supplied live `image_gen` declaration and the fail-closed case where Codex omits it | Invoke standalone `web.run` through Tool Mapping only with a copied Profile set to Passthrough and confirm upstream forwarding; repeat with `web.run` Modified and confirm successful local search, `turnXsearchY` open and time results, plus fail-closed cross-session/unknown references and fatal 501 for unsupported browser navigation. Test a converted third-party model with Chat Default separately. Invoke `image_gen.imagegen` generation and edit through a Modified Profile and confirm the selected OpenAI Images endpoint, model alias, response, and saved artifact. Run `tests/agent_workspace/image_generation/01` only after `view_image` and visual recognition pass; record whether Codex's current auth path actually exposes the tool before attributing any failure to the upstream model |
| Request and window identity | header/body metadata extraction; exact/+1 model, window, and request-ID budgets across source formats; request-ID visible-ASCII/control validation and missing-ID generation; rejection before body/log/trace/persistence/state/upstream use; correlation/state-key separation; sequential/concurrent no-window isolation; persistent-window continuity; principal-fair provider-metadata entry/byte quotas; and normal/error/cancel cleanup | Capture header/body/window/request-ID changes and maximum observed identity lengths of real turn, compact, resume, fork, subagent |
| Responses→Chat bridge | request/response/stream/history four-way fixture; fake Chat upstream multi-round tool playback; hyphenated Namespace expansion plus hyphen, dotted, underscore and uniquely unqualified child restoration in streaming/non-streaming responses; top-level Function, multi-Namespace child and alias-collision cases must remain fail-closed; `agent_message` exposes only its own encrypted payload while unrelated encrypted content remains opaque | Use `deepseek-v4-flash` to complete text, multi-round tools, error recovery and final answer; for collaboration, verify all six native calls, inter-agent payload delivery, and the model-facing name against the restored Namespace |
| Responses Lite / `additional_tools` | Accurate replay of Lite requests; extract embedded tools and developer instructions; override top/embedded tool mixing, deduplication, `reasoning.context=all_turns`, `parallel_tool_calls=false` and embedded image-generation removal | Enable Lite for `deepseek-v4-flash` using controlled catalog override, complete real multiple rounds of tool calls and confirm that the second round can consume the results |
| Codex model catalog | Diff the complete bundled JSON key set and per-model values; verify the packaged eight-entry upstream asset byte-for-byte against its bound source; validate every named third-party preset, Terra prompt identity substitution, shared overrides, supported reasoning subsets, and unconfigured-preset exclusion; extract `ModelInfo`, nested structs, enum wire values, serde rename/default/skip behavior, unknown-model fallback, instruction-template precedence, and each field's runtime consumers. Test local-mode defaults and confirmation, custom Codex Home, CLI/WebUI enable/disable, `--no-local-mode` persistence without Codex Home mutation, stable alias generation, TOML preservation, managed-only cleanup, model mutation synchronization, gateway-key creation/non-rotation, Provider-table replacement with unrelated parameters retained, effective CLI/runtime port selection, three-file plus runtime-state rollback, remote-host warning, and wheel/sdist resource inclusion | Start Codex against a generated catalog and managed `codex_rosetta` Provider, confirm the selected Provider ID and exact `OpenAI` name, and verify the generated bearer key reaches the gateway. Restart after a WebUI model mutation and confirm the eight upstream entries plus only the expected configured aliases are loaded without rotating the key. For every preset capability selector, run the real client against its actual third-party upstream and inspect Gateway Logs. Verify command/code mode, Responses Lite shape, reasoning, context/compact behavior, image input, search, collaboration v2, and OpenAI-identity compact behavior. Remote clients must be configured separately |
| custom/freeform tool | `apply_patch` schema/grammar/delta/call-output round-trip; Code Mode `exec` in Responses→Chat→Responses, non-streaming, added/delta/done/completed return trips are restored to `custom_tool_call`; non-compliant third-party parameters are retained, and no guessing is rewritten to JavaScript | Real Codex execution success patch, failed patch Post-fix correction; execute `exec/wait` and nested tool call for catalog with code mode enabled, confirm that tool failure is visible and fatal incompatible-payload will not appear |
| Code tool localization | native/localized schema mapping, parameter conversion, call id, result recovery and history replay | Really execute read/edit/write/search/shell, and the tool history can still be correctly consumed in the next round |
| Tool history consistency | Exact encrypted at-rest payload, authenticated restart replay, missing/wrong key and tamper fail-closed, plaintext and encrypted-v1 schema migration, row/session/principal/global row+byte budgets, replacement accounting, TTL release, transactional write rollback, abnormal replay bounds, and concurrent principal/session isolation | compact/resume/restart after multiple rounds of tools, confirm that there are no repeated calls or orphaned output; restore a matched database/key backup; exercise a session near the documented replay envelope |
| Deferred tool discovery | namespace defer, search matching, multiple searches, call/output, two-way window isolation, UTF-8 byte/count budgets, per-principal unique-scope quota, same-principal global-oldest eviction, no cross-principal eviction, atomic overflow, concurrency, TTL/eviction/clear accounting | Real plugin/MCP namespace search, call, consume results, and verify that the two principals do not cross talk or exceed the retained-state envelope |
| Codex tool usage tips | tool description/schema injection and mode availability fixture | Under a run with provider display name exactly `OpenAI` and a `gpt-5.6-sol`-equivalent catalog, execute `builtin_tools/01` through `06` for Code Mode `wait`, Plan, projected/localized file operations, image viewing, Goal lifecycle, and upstream visual recognition. Use a vision-capable upstream for task 06. `request_user_input` requires an app-server JSON-RPC runner because `codex exec` explicitly rejects it; do not count an exec-mode rejection as model evidence |
| Web search bridge | Configure, disabled/missing keys, search results, event reconstruction and continuation fixtures; real-loopback Content-Length/chunked/EOF/compression/timeout/cancel response limits | Real search, read results and continue to generate final answers; verify that error paths are recoverable |
| Stream lifecycle | created, item/delta/done, completed/failed/incomplete sequence; huge declared HTTP chunks; no-newline/no-delimiter SSE; converted/raw/web-search overflow and early-close classification | Real streaming turn and client disconnect without duplication/truncation/stuck; terminal, cancellation, limits, and errors are presented correctly |
| Message phase | All tool signals, completed-only, added/done/completed phase consistency | Commentary/final in Codex UI is displayed correctly, mailbox/steering can work |
| Reasoning | effort/summary/content/encrypted state Cross-format round-trip and tool continuation round fixture | `deepseek-v4-flash` reasoning can be continued before, after, and in the next round of the tool without repeated thinking |
| Context compaction resilience | orphan tool config, history trimming, compact fixture and window generation | Continue tool tasks after long session triggers compact and verify resume/restart |
| GPT relay provider identity | Unit-test prompt-free capture/redaction and C0-C5 evaluation contracts; compile the path-pinned Codex harness | Against the same real relay/model, run C0-C5 separately and compare non-OpenAI versus `OpenAI`. Require real SSE completion and actual forwarded-model evidence; additionally require Zstd for C3, old/current compact order plus follow-up for C4, and negative controls for C5. Never count harness mocks as relay evidence |
| Model-group tool profiles | Validate the bundled/user Profile contracts, Admin CRUD and reference guards, bundled input-only override validation, model-group resolution for every protocol, all four Admin categories, per-tool filtering, text/password/select persistence, UI-hidden inputs, input/description state visibility, namespace pass-through/expansion, Disabled Namespace child-state coercion and selector locking, injected-tool selection, absence of hosted `image_generation`, and `web.run`/`image_gen.imagegen` endpoint selection | Use real Codex sessions on Chat Default and copied pass-through, web.run-mapping, and restrictive Profiles; verify bundled visible fields survive explicit save/reset while delivery states reject edits, Chat Default disables `multi_agent_v1` and upstream-visible `apply_patch`, injects exactly three read plus two write tools, keeps Function guidance text hidden behind localized summaries, preserves namespace expansion/restoration, and uses the selected search/image credentials without leaking tokens |
| Static tool catalog | Validate unique IDs, references, Function/Hosted input IDs/types/defaults/localization keys, Chat Default Profile defaults, supported states, excluded dynamic tools and obsolete hosted `image_generation`, and exact CLI/source binding | Compare the catalog and Chat Default Profile against tool definitions exposed by a real Codex session for the target version; record conditionally available tools separately |

## 1. Request, header and session identity

The current Codex source code is clarified in `codex-rs/core/src/responses_metadata.rs`: the canonical carrier of the complete turn metadata is the `client_metadata["x-codex-turn-metadata"]` of the request body, and the HTTP `x-codex-*` headers are compatible projections.

Rosetta's current behavior:

- `gateway/app.py::_proxy_handler` reads `x-codex-window-id` from HTTP header;
- model IDs are limited to 256 UTF-8 bytes and window IDs to 128 UTF-8 bytes before routing/state allocation;
- window id serves as the key for both tool history mapping and window-scoped `tool_search`/phase status;
- provider continuation metadata uses the same authenticated principal/window scope; it enforces 1 MiB per entry, 8 MiB per scope, 1,024 entries/16 MiB per principal, and 10,000 entries/64 MiB globally, and global count replacement never evicts another principal;
- `x-request-id` remains a trace/response correlation value and never becomes a state key; without a window header, each inbound request receives a private non-reusable scope that is cleared when non-streaming or streaming delivery ends normally, fails, or is cancelled;
- `gateway/headers.py` only forwards `x-request-id`, `User-Agent` and `OpenResponses-Version` upstream;
- Responses→Responses Tool Mapping only leaves non-tool body fields intact, so canonical `client_metadata` will not be lost by IR; Rosetta mode must explicitly preserve any required metadata through IR;
- Responses→Chat path does not send Codex metadata to Chat upstream, local status still relies on HTTP `x-codex-window-id`.

The upgrade review must capture and compare both:

```text
HTTP x-codex-window-id
HTTP x-codex-turn-metadata
client_metadata["x-codex-window-id"]
client_metadata["x-codex-turn-metadata"]
session-id / thread-id / turn-id / parent-thread-id / subagent metadata
```

The window id in the current source code is in the form of `{thread_id}:{auto_compact_window_number}`. compact, resume, fork and subagent will affect its life cycle; it cannot be treated as a thread UUID that never changes.

## 2. Responses request and direct transparent transmission

The current Codex `ResponsesApiRequest` contains `instructions`, `input`, `tools`, `tool_choice`, `parallel_tool_calls`, `reasoning`, `store`, `stream`, `stream_options`, `include`, `service_tier`, `prompt_cache_key`, `text` and `client_metadata`.

Using Tool Mapping only for same-format routing is an important forward compatibility strategy: after the selected Profile is applied, unknown non-tool fields will not be compressed into the IR first, and the response will not be reserialized. Rosetta mode deliberately takes the Responses→IR→Responses path for third-party implementations. These are internal handling modes over the same wire protocol and must remain separately selectable during upgrades.

Responses→Chat is an explicit compatibility layer. After adding request item, tool type, reasoning field or SSE event to Codex, you must confirm that the converter has a clear downgrade/recovery strategy, and "request successful" cannot be regarded as agent loop compatibility.

### Responses Lite and `additional_tools`

The bundled model catalog of Codex 0.144.0 has `use_responses_lite=true` enabled for some models. In this mode, Codex no longer puts tools at the top level `tools`: it inserts a `type: "additional_tools"` item at the beginning of `input` and uses a developer message to carry the original instructions; reasoning may also use `context: "all_turns"`.

Responses→Responses Tool Mapping only retains this body except for selected Profile tool changes. Responses Rosetta mode
and Responses→Chat depend on explicit IR coverage. Responses→Chat now merges
top-level tools with `input[].type=additional_tools`, preserves the embedded
developer instructions, deduplicates by the final Chat tool name, and applies
image-generation filtering to both locations. Converter and gateway regression
tests cover these paths, and the 0.144.0 upgrade report records a controlled
multi-turn Lite/code-mode run through `deepseek-v4-flash`. Native GPT routing
and untriggered catalog combinations remain real-test gaps, not an
`additional_tools` implementation gap.

Codex returns freeform Code Mode results as `custom_tool_call_output`, including
multi-part `input_text` output from `exec`. Responses→Chat converts those items
to the same IR tool-result path as `function_call_output` so the paired Chat
tool message contains the actual script/search result instead of an orphan-call
placeholder. A real `gpt-5.6-sol` alias backed by `deepseek-v4-flash` consumed
that output and completed `web.run` through local Tavily in the controlled
network-search test.

### Model catalog metadata and third-party aliases

The catalog is an input to Codex client behavior, not a Rosetta routing table.
`slug` must match the alias exposed by Rosetta, while the model group remains
responsible for selecting the upstream model, provider, protocol, and Tool
Profile. Capability fields then determine which request dialect, prompts,
reasoning options, context budgets, and tools Codex attempts to use.

Every upgrade must compare the full bundled `models.json` key/value set with
`ModelInfo`, its nested structs and enums, serde rename/default/skip behavior,
the unknown-model fallback initializer, and the runtime consumers of each
field. This includes catalog keys ignored by the current client and valid
defaulted fields absent from the bundled JSON. The maintained field inventory
and third-party decisions are documented in
[`docs/en/codex-model-catalog.md`](../../en/codex-model-catalog.md) and its Chinese
counterpart.

For new third-party aliases, the target design should prefer the current Codex
surface: `web.run` over legacy hosted `web_search`, and collaboration v2 over
legacy `multi_agent_v1`. This is a preference after capability verification,
not permission to copy a built-in model's catalog wholesale. Until the actual
model completes the newer search/open or subagent lifecycle through Rosetta,
the corresponding field/tool should remain disabled or use the proven older
surface.

Rosetta local mode is the sole owner of `<codex-home>/model_catalog.json`. With
no configured models it copies the eight bound Codex 0.144.4 entries unchanged;
otherwise it writes only configured aliases in stable name order. Exact aliases found in
`codex_model_presets.json` receive their declared Terra-derived preset,
including prompt identity substitution; other aliases use the generic Terra
copy with only `slug`, `display_name`, and `description` replaced. The gateway removes every active `model_catalog_json`
assignment from Codex `config.toml` before writing one root absolute path, but
preserves unrelated TOML text and never deletes a file referenced by an old
assignment. On each confirmed startup or Admin synchronization it also ensures
one gateway key with ID/label `codex`; an existing key is reused without
rotation. It replaces root `model_provider` with `codex_rosetta`, removes the
buggy root-level reasoning setting written by older Rosetta builds, and updates
`[desktop].enabled-reasoning-efforts` when it does not already contain all six
values (`low`, `medium`, `high`, `xhigh`, `max`, and `ultra`). It replaces
the managed `[model_providers.codex_rosetta]` table with an OpenAI-named
Responses provider using the resolved bearer key and effective loopback port.
Other Provider tables and their parameters remain unchanged. Disabling local
mode removes the managed catalog, selection, and Provider table but retains the
gateway key for later reuse. Gateway config, catalog, TOML, and hot activation
use compensating rollback. An upgrade must rebind the packaged asset and retest
this ownership and Provider-identity contract before changing the declared
Codex version.

The hidden `codex-auto-review` alias is selected by model mapping rather than
provider heuristics. An absent/empty upstream model or the same
`codex-auto-review` upstream keeps the official entry byte-equivalent at the
parsed JSON level, including its null `tool_mode`; this supports native
Responses passthrough to OpenAI and GPT relay services. An explicitly different
upstream model forces only `tool_mode` to `code_mode_only`. That mapping acts as
the user's non-OpenAI-service signal and intentionally narrows Rosetta's review
path to the newer Code Mode instead of requiring support for every legacy and
mixed Guardian tool surface. Tests must cover object and shorthand-string model
configuration for both branches.

## 3. Codex-native tools and history replay

The current Codex source code exposes `apply_patch` as a freeform grammar tool with Responses `type: "custom"`; the call uses `custom_tool_call`, the parameter is a string, and the result uses `custom_tool_call_output`. Catalogs with code mode enabled also expose `exec` on the same wire type, whose `input` must be a raw JavaScript source, not a shell parameter object. Rosetta maintains two layers simultaneously and is compatible with:

1. The Responses converter safely downgrades a native custom tool into an IR/Chat representation, then restores the native Responses item from preserved metadata;
2. Every Responses→Chat route localizes Codex editing tools into forms more familiar to Chat models, then translates model calls back to `apply_patch`, `exec_command`, or a controlled fallback. This is protocol policy, not model configuration. Direct Responses→Responses routes bypass this adaptation and preserve the upstream body.

When Chat upstream downgrades the custom/freeform tool to a normal function call, the Responses return must restore `custom_tool_call` according to the `metadata.provider_type="custom"` recorded during the request period; this applies to both non-streaming responses and streaming added/delta/done/completed. The `{"cmd": "..."}` returned by a third-party model cannot be synthesized into JavaScript without authorization: it is evidence that the model does not adhere to freeform semantics, and should be handled by Codex as a visible tool error and let the model retry, rather than letting Rosetta guess the execution intention.

Because Codex will resend history on subsequent requests, this project saves the native/localized mapping by `call_id` and restores the exact tool call originally seen by the model before sending it upstream. Authenticated window-scoped gateway requests use encrypted SQLite state as the cross-request authority; AES-256-GCM binds each payload to its principal/provider/model/session/call scope, and diagnostic redaction never substitutes `[REDACTED]` into executable replay data. Missing, mismatched, damaged, over-budget, or inconsistently-accounted key/ciphertext state fails closed. Ciphertext plus ownership metadata is capped at 16 MiB per row, 2,048 rows/64 MiB per session, 8,192 rows/256 MiB per principal, and 32,768 rows/512 MiB globally. Cleanup, replacement-aware accounting, validation, and the final upsert share one immediate transaction; encrypted-v1 accounting migration backfills without deleting valid history. This is a critical path for prompt cache consistency with multi-round tools and must be tested with compact, resume, failed tool results, TTL/persistence, restart, quota/rollback failure, and matched database/key backup restoration.

`namespace` and `tool_search` are another Codex-specific path. Rosetta will hide namespaces that are not suitable for one-time expansion to Chat, save them by authenticated window, inject the synthesized `tool_search`, and restore the matching tool with a subsequent `tool_search_output`. The single store owner accounts canonical UTF-8 JSON bytes and nested tool count across both discovered and deferred state. It rejects a request atomically before mutation when a scope would exceed 1,024 tools or 16 MiB, a principal would exceed 256 unique scopes across both maps, or the app would exceed 64 MiB. Each retained map holds at most 1,000 scopes; when full, only the inserting principal's oldest scope may be replaced, otherwise the request is rejected. A scope present in both maps counts once toward the principal quota, and TTL, replacement, eviction, and clear paths return all accounting.

For Namespace children expanded onto a flat Chat tool surface, Rosetta uses
`namespace-function` as the canonical Chat-visible name. The return path also
recognizes `namespace_function`, `namespace.function`, and a bare child name.
The bare form is
restored only when exactly one Namespace owns that child and no ordinary
top-level Function uses the same name. The underscore form is likewise restored
only when it maps uniquely and does not collide with a top-level Function.
Ambiguity is never guessed: the call remains flat and Codex rejects an
unsupported call instead of Rosetta routing it to the wrong Namespace.

Codex collaboration messages are carried as Responses `agent_message` items.
For the Responses→Chat bridge, Rosetta converts these to user messages and
includes both ordinary `input_text` and the inter-agent task payload stored in
that item's `encrypted_content` part. This exception is scoped to
`agent_message`: ordinary message and reasoning encrypted content is not
exposed as model-visible text. Without this conversion a `fork_turns="none"`
child receives an empty `Payload:` and may reconstruct the wrong task from the
workspace.

The deferred Namespace whitelist remains separate from this one-time expansion
rule. The Codex `0.144.4` source still carries an optional Namespace on
`ResponseItem::FunctionCall`, and the reviewed release diff did not change the
`multi_agent_v2`/`collaboration` tool contract. Dedicated six-scenario real
coverage is nevertheless required before this route is considered verified.

The OpenAI Chat tool converter also adds model-visible usage hints for `request_user_input`, `create_goal`, `update_goal`, and selected collaboration lifecycle Functions. Collaboration guidance clarifies complete child messages, future-only waits, canonical path filtering, and canonical message targets. `request_user_input` can be checked against the adjacent source checkout; some Goal tools come from real Desktop/runtime payloads and do not have matching definitions there. Retain real session/tool fixtures during upgrades instead of relying only on source searches.

### Static tool catalog version binding

`src/codex_rosetta/gateway/admin/tool_catalog.json` is a read-only conceptual snapshot of fixed Codex tools. Its metadata is bound to Codex CLI `0.144.4` and source commit `8c68d4c87dc54d38861f5114e920c3de2efa5876`. It intentionally excludes `tool_search`, the obsolete hosted `image_generation` tool, and runtime-dynamic MCP, plugin, app, and connector tools, including GitHub. Under Code Mode, Codex flattens namespaced tools nested in `exec` to `namespace__function` properties, so the catalog directly lists `clock__*`, `web__run`, `image_gen__imagegen`, `memories__*`, and `skills__*` without synthetic parent Namespace items. Only directly model-visible Responses Namespaces such as `collaboration` and legacy `multi_agent_v1` retain Namespace parents. The catalog does not describe the exact tools available to any individual request because features, environment availability, model metadata, Provider capabilities, and runtime extensions still control exposure.

Every Codex upgrade must review the built-in tool specifications and bundled extension registrations, refresh the catalog contents and version metadata when needed, and run the catalog contract tests. Even when the tool set is unchanged, the source binding may be advanced only after that review is recorded in the upgrade report.

## 4. SSE, phase and termination semantics

Codex first registers an item through `response.output_item.added`, then consumes text/tool deltas, and finally processes item-done and `response.completed`. Rosetta's rebuilt stream must preserve at least this order:

```text
response.created
response.output_item.added
response.output_text.delta / tool input delta
response.output_item.done
response.completed
```

Gateway transport keeps the total successful HTTP/SSE stream size and duration
unlimited, but it applies a 1 MiB per-line and 8 MiB per-event `data:` limit to
both converted parsing and byte-preserving Responses passthrough. Chunked HTTP
payloads are read in fixed bounded subchunks instead of materializing the
peer-declared chunk size. Overflows close the upstream and become a stable
`UpstreamStreamLimitError`; raw passthrough bytes below the limits are not
rewritten. A Codex upgrade that introduces larger required single events must
be measured and reviewed explicitly rather than disabling the limits.

The Gateway also bounds streaming connection establishment to 30 seconds,
upstream SSE inactivity to 60 seconds, and connection cleanup to 2 seconds.
This prevents a route change from leaving Codex attached to a black-holed
upstream socket: the downstream stream is closed so Codex can apply its own
request and stream retry policy. Rosetta does not replay a stream after any
upstream bytes have been delivered. Automated coverage must retain stalled
open, stalled parsed/raw body, bounded cleanup, and normal long-stream framing;
real Codex testing must switch Wi-Fi or enable a route-changing VPN during a
turn and confirm that a later retry completes without restarting the Gateway.
Expected upstream stream timeouts and disconnects are normalized to one
traceback-free `ERROR` line and an incomplete 502 stream outcome; protocol,
safety, and unknown failures retain their diagnostic exception path.

`phase` is inside the message item, not a separate event. `commentary` is not just a UI label: the current Codex checks the mailbox after the commentary item is completed, and may change subsequent sampling behavior. Therefore the phase in added, done and completed output must be consistent.

Currently `ResponsesPhaseBuffer` treats function/custom/MCP/shell/computer/tool_search/ web_search calls as tool signals. Automated regression covers "text followed by native search tool" and the scenario where there is only native search call in `response.completed.output`, ensuring that the previous text is marked as `commentary` instead of erroneously marked as `final_answer`. When adding a new Codex output item type, you still need to clearly determine whether it will continue the agent loop, and expand this set and the tests of the two event paths accordingly.

## 5. Reasoning state

Codex will request `reasoning.encrypted_content` when reasoning is turned on, and consume summary part, summary text delta/done and raw reasoning delta. Rosetta currently retains Responses summary/content/encrypted state through IR metadata, and uses provider extension fields such as `reasoning_content` in Chat upstream to maintain tool continuation.

Must check when upgrading:

- New value and degradation rules for reasoning effort;
- summary `auto/concise/detailed/none` and delivery order;
- `include: ["reasoning.encrypted_content"]`;
- Empty string `reasoning_content` coexists with tool calls;
- Renewability of reasoning items after history replay, compaction and cross-format conversion.

## 6. Current clear limitations and observations

### Canonical metadata is only naturally retained in the direct path

Bridge's window-scoped logic still relies on the compatibility header. If Codex stops sending the `x-codex-window-id` header in the future and only retains the body `client_metadata`, the phase and deferred tool status will no longer be windowed correctly. Every upgrade must be confirmed with real request capture.

When the header is missing, `GatewayStateScope` creates a request-local,
non-persistent conversation ID. Tool mappings and deferred-tool state are not
reused across requests, preventing same-model sessions from sharing a fallback
mapping domain. The trade-off is loss of cross-turn restoration for that
request path, which must remain visible in real compact/resume testing.

### Gateway `/v1/models` is not a Codex dynamic catalog

`gateway/app.py::handle_list_models` returns the OpenAI SDK style `{"object":"list","data":[...]}`. The current dynamic catalog request of Codex source code is `GET models?client_version=...`, and the response is `{"models":[ModelInfo...]}`, among which `apply_patch_tool_type`, reasoning, parallel tools, context window, Responses Lite, tool mode and multi-agent version will change the requests and tools issued by Codex.

Therefore currently `/v1/models` cannot be considered a Codex catalog implementation. Only after it is confirmed that Codex will use the endpoint from the custom provider, it should be implemented and tested separately according to the Codex `ModelInfo` contract, and the two response formats cannot be mixed into one ambiguous endpoint.

### Responses WebSocket and `/responses/compact` are not implemented yet

The current gateway's Codex surface is HTTP `/v1/responses` + SSE. Responses WebSocket `response.create`, incremental `previous_response_id` and remote `/responses/compact` in the source code are not verified capabilities. Responses Lite is supported on both the direct and Responses→Chat paths described above; that support does not imply WebSocket, incremental-history, or remote-compact support.

Codex model/provider configurations must not declare these capabilities without testing; each upgrade must confirm that Codex still uses HTTP/SSE for custom providers, or has reliable fallback.

Currently Responses→Chat also relies on Codex to resend the complete input/history. Even if `additional_tools` is added, if Codex starts to use WebSocket/HTTP incremental requests and `previous_response_id` by default, Rosetta still does not have a corresponding server-side Responses session storage, and the bridge will lack history. This item must be determined through real request capture, and it cannot be inferred that it is enabled just from the presence of `previous_response_id` in the request type.

### Remaining code-mode and multi-agent verification gaps

The generic custom/freeform path preserves code-mode `exec` as
`custom_tool_call` with raw JavaScript input across non-streaming and streaming
added/delta/done/completed events; ordinary function tools such as `wait`
continue through the function-tool path. Automated fixtures cover the `exec`
wire round-trip, and the upgrade report records one controlled live `exec`
run. Nested call/wait continuation, malformed third-party recovery, and
`multi_agent_v2`/`collaboration` namespace discovery + call + output use the
hyphenated canonical expansion and fail-closed compatible-name restoration described above.
Automated streaming/non-streaming collision fixtures exist; dedicated real
six-scenario coverage is still required before the live gap is closed.

### Phase's native search signal has been incorporated into automated regression

`tool_search_call`/`web_search_call` has been incorporated into the phase tool signal collection and overrides the streaming item event and completed-only fallback. This fix only changes the phase classification, not the search bridge or tool execution; the real Codex UI/mailbox behavior is still a gatekeeper that must be actually tested.

### Existing real integration baselines are insufficient to prove tool compatibility

The recorded controlled 0.144.0 run covers Lite/code-mode short answers,
file-read, multi-turn read/write/diff, `ultra`, and one `exec` path through
`deepseek-v4-flash`. It does not validate native GPT routing, Goal/Plan,
request_user_input, plugin/tool_search, web search, compact/resume, nested wait,
or subagent behavior. Future upgrades must run the remaining matrix in the
upgrade checklist instead of treating the controlled alias as complete model
coverage.
