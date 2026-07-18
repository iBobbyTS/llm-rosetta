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

### Stable compatibility point registry

The following registry is the canonical point list. Point IDs are stable and
must be used in upgrade reports; names are descriptive and may be clarified
without changing the ID. Never reuse a retired ID. The compatibility overview
and test matrix below must each contain every registered name exactly once.

| ID | Compatibility point |
| --- | --- |
| `CP-01` | Agent-facing API |
| `CP-02` | Responses transparent handling |
| `CP-03` | Codex Search and Images endpoints |
| `CP-04` | Request and window identity |
| `CP-05` | Responses→Chat bridge |
| `CP-06` | Responses Lite / `additional_tools` |
| `CP-07` | Codex model catalog |
| `CP-08` | custom/freeform tool |
| `CP-09` | Code tool localization |
| `CP-10` | Tool history consistency |
| `CP-11` | Deferred tool discovery |
| `CP-12` | Codex tool usage tips |
| `CP-13` | Skill delivery surfaces |
| `CP-14` | Live-agent runtime authentication |
| `CP-15` | Web search bridge |
| `CP-16` | Self-hosted Bing search |
| `CP-17` | Stream lifecycle |
| `CP-18` | Message phase |
| `CP-19` | Reasoning |
| `CP-20` | Context compaction resilience |
| `CP-21` | GPT relay provider identity |
| `CP-22` | Model-group tool profiles |
| `CP-23` | Static tool catalog |

## Current upgrade status

The code-derived ledger has been validated against Codex `0.142.0` through
`0.144.6`, but `0.144.6` runtime compatibility is **not approved**. This
documentation-only review did not adapt Rosetta code or packaged assets, refresh
the source-contract baseline, advance the package version, or run triggered live
gates. The packaged Codex catalog still carries `0.144.4` Sol/Terra/Luna
instructions and 372,000-token context values, while `0.144.6` uses refreshed
instructions and 272,000-token values; the static tool catalog is also still
bound to the `0.144.4` CLI/source identifiers. See
[`reports/range-coverage-review.md`](reports/range-coverage-review.md) for
the range review and [`rosetta-source-map.md`](rosetta-source-map.md) for the
maintained owner index.

## Current compatibility overview

The `Primary Locations` column names the shortest useful entry points. The
exhaustive code-derived owner and deterministic-test map is maintained in
[`rosetta-source-map.md`](rosetta-source-map.md); it takes precedence when this
summary omits a transitive owner.

| Boundaries | Current Implementation | Primary Locations | Upgrade Risks |
| --- | --- | --- | --- |
| Agent-facing API | Expose `/v1/responses` to Codex; Chat/Anthropic/Google as upstream target format; accept full-history image sessions under a configurable 64/128/256/512/1024 MiB or unlimited inbound body limit, defaulting to 128 MiB. Authenticated `/v1` Zstd bodies are decoded before JSON parsing, with the same live WebUI limit independently enforced before and after decompression. Attested Responses requests retain a request-local reference to their original wire body and an explicit safe-header allowlist for exact passthrough. The current 12-header baseline is `Accept`, `Content-Encoding`, `Content-Type`, `Originator`, `Session-Id`, `Thread-Id`, `x-client-request-id`, `x-codex-beta-features`, `x-codex-turn-metadata`, `x-codex-window-id`, `x-oai-attestation`, and `x-openai-internal-codex-responses-lite`; every Codex version review must diff source plus a real capture against it | `gateway/app.py`, `gateway/headers.py`, `gateway/inbound_content_encoding.py`, `gateway/config.py`, Admin config/UI, `gateway/proxy.py` | Codex changes endpoint, transport, request shape, request compression, attestation headers, or retained image/history size |
| Responses transparent handling | Admin exposes one `responses` wire protocol; unsupported former protocol values are rejected during configuration loading. Every Responses→Responses route is direct regardless of Provider: the gateway applies the selected Tool Profile, retains other request fields, and returns original response JSON/SSE bytes. Model-switch compaction is the sole semantic exception: it uses the previous model with Rosetta's prompt, stores a principal-scoped plaintext replacement for seven days, returns an `rskc_v1_` handle, and rehydrates it before the next Provider request. Native `context_limit` and `user_requested` remain unchanged. Uncompressed response bytes remain unchanged. Authenticated Zstd ingress is decoded once for validation and routing; when an attested streaming request remains exactly equal to the parsed ingress object after all gateway processing, the transport forwards its original compressed bytes and allowlisted Codex headers. Any Profile, alias, rehydration, or cross-protocol mutation falls back to rebuilt JSON and drops the stale attestation. The response transport enforces 1 MiB per line and 8 MiB per event with no total successful-stream cap | `gateway/config.py`, `gateway/headers.py`, `gateway/inbound_content_encoding.py`, `gateway/tool_profiles.py`, `gateway/codex_compaction.py`, `routing.py`, Admin provider UI, `gateway/proxy.py`, `gateway/transport/http/transport.py`, `test_responses_passthrough.py`, `test_tool_profiles.py`, `test_inbound_content_encoding.py`, `test_config.py`, `test_http_transport_limits.py` | A gateway hook mutates the request without forcing JSON fallback, stale attestation is paired with rebuilt bytes, the model-switch mapping cannot be rehydrated, Codex changes request content encoding or attestation headers, emits a required single line or event above the safety envelope, or changes away from HTTP/SSE framing |
| Codex Search and Images endpoints | Expose JSON `POST /v1/alpha/search`, `/v1/images/generations`, and `/v1/images/edits`. `image_gen.imagegen` Passthrough retains direct Tool Mapping only routing; Modified resolves its Profile Base URL/Token and forwards unchanged OpenAI Images generation/edit JSON with the configured model alias across every LLM protocol, while Disabled rejects the endpoint. No vendor-private image API translation is attempted. Codex 0.144.4 separately gates model-facing image generation on image modality, the feature toggle, provider capability, and either OpenAI actor authorization or real Codex-backend auth; the local-mode bearer-token Provider does not satisfy that auth gate even though its display name is `OpenAI`. Rosetta projects only a live Codex declaration and deliberately does not invent a missing `image_gen.imagegen`, so the endpoint can be configured and directly tested while the agent tool remains absent. A Profile with Modified `web.run` advertises only the live declaration branches supported by active Rosetta executors, then locally implements atomic `search_query` through configured Tavily or self-hosted Google in the authenticated `web-run` sidecar, direct-public-URL or `turnXsearchY` `open`, and fixed-offset `time`; Passthrough preserves the complete live Codex declaration. Search references are allocated atomically and cached for retry stability in an app-owned, bounded 24-hour store scoped by authenticated principal plus `SearchRequest.id`; model and `x-codex-window-id` are deliberately excluded so references survive model changes and compaction. Without the optional sidecar, open uses the bounded static Python fetcher and the projected schema omits browser commands. With the authenticated `web-run` Docker sidecar, self-hosted Google uses short-lived bounded Patchright contexts, while open uses a session context and adds scoped `turnXfetchY` references, numbered-link `click`, text `find`, and PDF `turnXviewY` plus `screenshot`; PyMuPDF extracts/renders PDF pages and Tesseract provides OCR fallback. SearchResponse remains text-only, so normalized Google/Tavily sources and PDF screenshot metadata stay inside the string `output` instead of changing the Codex response shape. Public-address checks cover navigation/subresources/redirects/PDF downloads; unknown, expired, or cross-session references fail closed, and remaining unsupported commands/settings fail before partial execution. Gateway Logs record local request/result/error stages, executor choice, operation counts, and reference/cache counts without tokens | `gateway/codex_auxiliary.py`, `gateway/codex_images.py`, `gateway/codex_search.py`, `gateway/web_run_capabilities.py`, `gateway/web_run_sidecar.py`, `gateway/codex_search_references.py`, `gateway/codex_page.py`, `gateway/tool_profiles.py`, `gateway/app.py`, `routing.py`, `gateway/resources/web_run/app.py`, `gateway/resources/web_run/google_search.py`, `test_codex_search.py`, `test_web_run_sidecar.py`, `test_web_run_google_search.py`, `test_codex_search_references.py`, `test_codex_page.py`, `test_codex_auxiliary.py`, `test_downstream_routes.py`, `../openai-codex-src/codex-rs/core/src/tools/spec_plan.rs`, `../openai-codex-src/codex-rs/codex-api/src/search.rs`, `../openai-codex-src/codex-rs/ext/image-generation/` | Codex changes endpoint paths, Images API body/response, SearchRequest commands/settings, required headers, SearchResponse shape, `SearchRequest.id` lifecycle, image-generation auth/feature/modality exposure gates, Patchright/browser behavior, Google result-page behavior, open/click/find/screenshot reference semantics, PDF screenshot result expectations, or no longer includes routing model/session identity |
| Request and window identity | Read `x-codex-window-id` as the authenticated session key for tool mapping, provider continuation metadata, and phase behavior; enforce the documented 128-byte window and 256-byte model identity envelopes before routing/state allocation; keep external `x-request-id` correlation-only and require 1–128 visible ASCII bytes before body/log/trace/persistence/state/upstream use, generating a UUID when absent; use a private nonce when no window exists, and clear request-local state at normal/error/cancel completion | `gateway/app.py`, `gateway/proxy.py`, `gateway/state_scope.py`, `gateway/headers.py` | Codex changes to only send canonical `client_metadata`, changes window or request-ID semantics, or needs an identity above the safety envelope |
| Responses→Chat bridge | Convert Codex Responses request to Chat via IR, expand Namespace children as the regex-safe canonical `namespace-function`, and then rebuild Responses output. Response restoration also accepts `namespace_function`, `namespace.function`, or a bare child name only when exactly one Namespace owns it and no top-level Function has that name; ambiguous names remain flat so Codex fails closed. Codex `agent_message` input becomes a Chat-visible user message, including its inter-agent payload carried in `content[].encrypted_content`, while ordinary message/reasoning encrypted content remains opaque | `converters/openai_responses/**`, `gateway/proxy.py` | High; new item/event/fields will not be automatically transparently transmitted, Namespace or Function naming changes can create ambiguous restore candidates, and inter-agent payload carriers can change independently of ordinary messages |
| Responses Lite / `additional_tools` | Responses→Responses can be transmitted transparently as is; Responses→Chat merges the top-level tools with `input[].type=additional_tools`, retains the developer instructions, and removes duplication according to the final Chat name | `converters/openai_responses/message_ops.py`, `converter.py`, `gateway/proxy.py` | High; 0.144.0 model catalog Responses Lite has been enabled for some models, the location of tools and developer instructions will change |
| Codex model catalog | Treat the bundled catalog and local catalog overrides as Codex client capability declarations, while Rosetta model groups remain the routing source of truth for alias, upstream model, protocol, and Tool Profile. Default-enabled local mode preserves the eight Codex 0.144.4 entries in source order, selects an exact-slug Terra-derived third-party preset from the upstream model name (or exposed name when unmapped), then falls back to generic Terra. Its runtime `comp_hash` overlay is selected only by that upstream model name (falling back to the alias when unmapped): an explicit non-empty preset hash takes precedence, otherwise the name selects a reviewed group or deterministic fallback. Provider identity never participates, so aliases sharing one upstream model remain compaction-compatible across Provider changes. Admin exact-slug detection combines the full Codex catalog and compact third-party presets, shows the detected `display_name`, derives text/vision from model modalities, and exposes a right-side editor for every compact per-model preset field. `gateway/codex_models.json` plus `gateway/codex_models_version.md` are the versioned packaged-catalog pair: update and review them atomically against the target Codex `models.json`; while `codex_models_0_144_4.json` remains consumed by current runtime, it must be compared too and may not diverge. The Models page also manages confirmed-local-mode task models: it copies `auto_review_model_override` onto every generated entry and writes `extract_model`/`consolidation_model` under Codex `[memories]`; inactive local mode locks the UI to the three provider defaults and reports missing routes. For `codex-auto-review`, it preserves the official unset `tool_mode` when the upstream is omitted or has the same name, but forces `code_mode_only` when the alias maps to another upstream model. It transactionally maintains `model_catalog.json`, root `model_catalog_json`, one stable `codex` gateway key, and the managed OpenAI-named `codex_rosetta` Provider while preserving unrelated Provider tables; byte-identical targets are not rewritten and therefore do not emit a restart notice. The compact preset resource owns identity, context, modality, reasoning, compaction compatibility, and shared capability overrides without duplicating Terra's large prompts. CLI/WebUI first-use confirmation, startup rebuild, and model-group mutations share this owner; CLI `--no-local-mode` disables synchronization without touching Codex Home, while the explicit clear command removes managed artifacts | `gateway/codex_models.json`, `gateway/codex_models_version.md`, `gateway/codex_models_0_144_4.json`, `gateway/codex_model_presets.json`, `gateway/model_presets.py`, `gateway/local_mode.py`, `gateway/cli.py`, `gateway/config.py`, Admin config/UI, `test_model_presets.py`, `test_local_mode.py`, `test_cli_local_mode.py`, `test_admin_config_routes.py`, `../openai-codex-src/codex-rs/models-manager/models.json`, `../openai-codex-src/codex-rs/model-provider/src/provider.rs`, `../openai-codex-src/codex-rs/core/src/guardian/review.rs`, `../openai-codex-src/codex-rs/memories/write/src/phase1.rs`, `../openai-codex-src/codex-rs/memories/write/src/phase2.rs`, `docs/en/codex-model-catalog.md`, `docs/zh-cn/codex-model-catalog.md` | A Codex upgrade changes fields, nested types, enum values, serde defaults/skip behavior, fallback metadata, bundled model count/order/values, prompt precedence, catalog file loading, `model_catalog_json`, `model_provider`, configured Provider parsing, `is_openai()` semantics, task-model provider defaults, Guardian override lookup, memory model config fields, or the requests/tools selected by those values. The version sidecar, current/legacy bundled catalog copies, exact-slug detection, manual `model_info`, Terra-derived presets, upstream-name preset/hash selection and Provider invariance, auto-review tool mode/override, memory model selection, and managed Provider identity must be revalidated |
| custom/freeform tool | Identify `apply_patch` and Code Mode `exec` of Responses `type: custom`, convert into Chat callable form, and restore Codex-native tool type, call/output in response return. For Chat Default, treat the Disabled parent `exec` as a conversion container: retain it through Responses source filtering, parse selected nested declarations from its live description, project them as normal Chat Functions, and remove only the exact heading-through-declaration-fence spans whose replacements were actually emitted. Remove the parent only when every recognized section was consumed and no deferred guidance, unknown section, duplicate section, direct-name conflict, malformed known section, or unparsed source description remains; otherwise send the pruned raw `exec(input: string)` beside the ordinary Functions. On Tool Mapping only, a copied Profile with Modified `web.run` instead retains the parent `exec` and structurally rewrites only its live `### web__run` section in both ordinary `tools` and Lite `additional_tools`; Passthrough remains unchanged, missing declarations are never invented, and malformed Modified sections are removed fail-closed. Model calls are deterministically rebuilt as custom `exec` JavaScript without duplicating Codex schemas. JSON Schema constraints omitted by Codex's TypeScript renderer remain irrecoverable. The source contract hashes the current exec description builder, nested heading/declaration renderers, web description and command schema | `openai_responses/converter.py`, `openai_responses/tool_ops.py`, `gateway/tool_profiles.py`, `gateway/proxy.py`, `gateway/code_mode_projection.py`, `gateway/tool_adaptation.py`, `test_tool_profiles.py`, `test_code_mode_projection.py` | Codex changes the custom grammar, TypeScript schema renderer, exec section/declaration syntax, normalized nested names, web description/schema, helper output contract, call/output/delta event, or internal-container lifecycle |
| Code tool localization | Inject selected `Read`/`Edit`/`Write`/`Glob`/`Grep` definitions and translate responses back to Codex-native calls. Chat Default projects selected nested `exec_command`, `write_stdin`, `update_plan`, `view_image`, `web.run`, Goal, Clock, Memories, Skills, conditionally `image_gen`, and conditionally assembled deferred/environment/context/plugin/MCP-resource/Agent-Job tools from Code Mode `exec`, while preserving same-named direct Functions. It keeps `new_context` direct because Codex marks it Direct Model Only. Every projected schema and description starts from the live `exec` declaration; a Feature- or runtime-gated declaration that Codex omitted is never synthesized. Modified `web.run` applies one shared capability whitelist to top-level Functions, Responses→Chat projection, and nested Tool Mapping only exec descriptions: `open`/`time`/`response_length` are always model-visible, `search_query` requires a configured global Tavily Key or ready self-hosted Google sidecar, and `click`/`find`/`screenshot` require shared sidecar health with `browser_ready=true`. Passthrough preserves the live definition unchanged and runtime validation still rejects unsupported or stale calls. Image generation is projected only when Codex supplied its live declaration after its own auth/feature/modality gates; Profile Modified state does not inject a declaration that Codex omitted. Passthrough and Modified both enable the representation-only projection; only Modified applies Profile description mutations. The parent `exec` is Disabled as an ordinary upstream surface, but selective pruning keeps it whenever raw deferred, unknown, duplicate, conflicting, malformed, or wholly unparsed capability remains. It emits `generatedImage(...)` for projected image generation and text/image helpers for their matching result contracts. It disables model-facing `apply_patch` but retains its parsed nested declaration as an internal projection so localized `Edit`/`Write` can rebuild native `exec → tools.apply_patch` calls; catalog `description_replaced_by` metadata requires both replacements to be emitted before that raw section is consumed. It leaves top-level `wait`/`request_user_input` and `collaboration` direct, disables `shell_command`, and does not define or inject `Bash`. Parse failures, missing projections, duplicate sections, and direct-name conflicts fail closed by retaining the exact raw section and withholding projection metadata; persisted historical Bash mappings remain readable | `gateway/code_mode_projection.py`, `gateway/web_run_capabilities.py`, `gateway/tool_adaptation.py`, `gateway/admin/tool_catalog.json`, `test_code_mode_projection.py`, `test_tool_adaptation.py`, `test_admin_tools_catalog.py` | Tool name, exec declaration/schema syntax, helper output contract, call id, execution result format, direct-versus-nested availability, conditional Feature/runtime assembly, Codex image-generation exposure gates, or Chat Default Profile state changes |
| Tool history consistency | Persist exact native/localized mappings by call id as authenticated encrypted SQLite state, including projected Chat Function to custom `exec` mappings; rewrite subsequent history from SQLite within the 24-hour TTL, apply TTL cleanup, and enforce 16 MiB per row, 2,048 rows/64 MiB per session, 8,192 rows/256 MiB per principal, and 32,768 rows/512 MiB globally | `gateway/proxy.py`, `gateway/tool_adaptation.py`, `observability/persistence.py`, `observability/tool_mapping_crypto.py` | Codex history replay, compact or output shape changes; custom `exec` call representation; database/key backup mismatch; mapping size or session fan-out changes |
| Deferred tool discovery | On Responses→Chat Code Mode routes, detect the live deferred-tool guidance in `exec.description` and expose byte-stable ordinary Chat `tool_search`, `tool_read`, and `invoke_deferred_tool` Functions beside raw `exec`. Translate search and exact read into deterministic custom `exec` JavaScript over the request-local `ALL_TOOLS` array. Search returns bounded `{name, summary}` candidates; read returns one complete exact-name declaration. A valid paired, version-marked `tool_read` call/output authorizes an exact `mcp__` name only after same-name declaration validation; the three Browser Node tools retain their static projections and other MCP tools use request-local generic projections. Read results for dispatchable MCP tools carry dispatcher instructions; non-MCP declarations remain raw-`exec` tools. Never add discovered tools to top-level Chat `tools`, rewrite history, or allocate Gateway discovery state. Same-named direct Functions win, malformed or unauthorized calls fail closed, and native Responses `tool_search_call/output` parsing remains a separate protocol path | `gateway/code_mode_projection.py`, `gateway/tool_adaptation.py`, `test_code_mode_projection.py` | `ALL_TOOLS` stops being an Array of `{name, description}`, deferred guidance changes, the V8 runtime loses required Array/RegExp/String APIs or `text`/`image`/`exit`, Codex changes custom `exec` call/output semantics, MCP naming/declarations change, or a direct tool conflicts with a synthetic name |
| Codex tool usage tips | Supplement the Chat model with `request_user_input`, `create_goal`, `update_goal` and other Codex tool calling constraints; their Admin card descriptions are shown only in Modified state | `converters/openai_chat/tool_ops.py`, tool catalog, related pipeline tests | schema, mode availability or Desktop/runtime tool contract changes |
| Skill delivery surfaces | Keep local filesystem Skills and orchestrator-owned Skills as two distinct Codex client contracts. Local `codex exec` discovers filesystem roots and injects the selected `SKILL.md` body without `skills.list`/`skills.read`; orchestrator-owned Skills require app-server, no attached local execution environment, `[orchestrator.skills]`, and exact opaque handles returned by `skills.list` then reused by `skills.read`. Rosetta does not convert either surface, but its isolated runtime fixtures must preserve the distinction | `tests/live_agent/local_skills/`, `tests/live_agent/orchestrator_skills/`, `test_live_agent_configuration_contract.py`, `docs/dev/agent-tool-testing.md`, `../openai-codex-src/codex-rs/core-skills/`, `../openai-codex-src/codex-rs/ext/skills/` | Skill root discovery, catalog metadata, selection/body injection, orchestrator availability gates, Namespace schemas, resource pagination, opaque-handle semantics, or runner/environment rules change |
| Live-agent runtime authentication | Every Gateway-backed live cell uses the production-intended identity/routing combination: ChatGPT OAuth copied into the ignored isolated Codex home plus the local-mode `codex_rosetta`/`OpenAI` Provider's `experimental_bearer_token`. OAuth owns Codex identity and capability gates; Provider auth precedence keeps model and auxiliary requests on the isolated Gateway. Gateway credentials come only from `~/.config/codex-rosetta-gateway`, OAuth only from `/Users/ibobby/.codex-multi-2/auth.json`, and neither source nor any secret value may enter Git history. The run records only credential-free runtime-auth evidence | `tests/live_agent/runtime-contract.json`, live-agent runner skill and documentation, `test_live_agent_configuration_contract.py` | Codex auth storage, auth-mode recognition, Provider auth precedence, `requires_openai_auth`, local-mode Provider generation, or auth-gated capability exposure changes |
| Web search bridge | Codex `web_search` can be exposed to the Chat model, the `web_search_call` event can be reconstructed and continued after Tavily execution, and Tavily responses use the primary bounded identity-encoded HTTP reader. Hosted `web_search` resolves Provider/Token from the selected Tool Profile. Modified `web.run` instead resolves global `server.web_search`: Tavily uses its credential, while Self-hosted (Google) calls the authenticated existing `web-run` sidecar and normalizes result-page sources into the same Codex string `output` contract. A shared five-second health cache coalesces bounded two-second sidecar probes for Admin and model requests; only online `browser_ready=true` adds self-hosted `search_query` plus model-visible browser commands, failures remove those commands without exposing connection details, and hot reload invalidates the cache. Passthrough accepts Codex at `/v1/alpha/search` and appends relative `alpha/search` to the configured Tool Mapping only upstream Base URL; it never inserts `/v1` when the upstream Base URL omits it | `gateway/web_search.py`, `gateway/codex_search.py`, `gateway/codex_auxiliary.py`, `gateway/config.py`, `gateway/admin/routes/config.py`, `gateway/web_run_sidecar.py`, `gateway/resources/web_run/google_search.py`, `gateway/transport/http/transport.py`, `test_web_search_bridge.py`, `test_codex_auxiliary.py`, `test_web_run_google_search.py`, `test_admin_config_routes.py` | native web-search item/event, tool configuration, global search configuration, provider options, auxiliary response behavior, Google result-page behavior, upstream Base URL semantics, shared-cache lifecycle, or sidecar health semantics change |
| Self-hosted Bing search | `server.web_search.provider = self_hosted_bing` retains the backward-compatible RSS executor, while `self_hosted_bing_browser` explicitly selects interactive HTML parsing. Both project the same reduced Codex `search_query` contract when the authenticated sidecar is browser-ready. The Gateway sends the selected provider to `/v1/search`; the sidecar uses an isolated Patchright context, extracts bounded results, unwraps `bing.com/ck/a` targets when present, reapplies domain filters, and fails closed on challenges without cross-provider fallback or changes to the text-only Codex `output` response | `gateway/config.py`, `gateway/app.py`, `gateway/codex_search.py`, `gateway/web_run_sidecar.py`, `gateway/resources/web_run/app.py`, `gateway/resources/web_run/bing_search.py`, `test_web_run_bing_search.py`, `test_web_run_sidecar.py`, `test_codex_search.py` | Bing RSS or HTML markup, redirect encoding, challenge behavior, provider selection, sidecar request schema, or Codex Search response shape changes |
| Stream lifecycle | Rebuild `response.created`, item added/delta/done, `response.completed`, etc. from Chat chunks Responses SSE; classify normal EOF, provider error, client cancellation, and bounded line/event overflow consistently in transport/telemetry/trace | `openai_responses/converter.py`, `gateway/proxy.py`, `gateway/transport/http/transport.py` | Codex parser adds required events, sequences or termination conditions; downstream disconnect semantics or maximum required event size changes |
| Message phase | Use tool calls and terminal events to infer `commentary`/`final_answer`, write phase back to message item; override native tool/web search signal | `gateway/stream_phase_buffer.py`, `test_stream_phase_buffer.py` | phase enumeration or Codex mailbox/final-answer semantic changes |
| Reasoning | Convert reasoning effort/summary, retain reasoning summary/content, `reasoning_content` and `encrypted_content` | `capabilities.py`, `reasoning_mapping.py`, `pipeline.py`, `converters/base/helpers/reasoning.py`, Responses/Chat content, config and stream converters, provider reasoning transforms, `test_reasoning_mapping.py`, `test_provider_reasoning_transforms.py` | New effort, summary delivery, reasoning event or encryption status change |
| Context compaction resilience | Remove orphan `tool_choice/tool_config` that has no tools but remains after compact; keep tool history replayable | `converters/base/helpers/tool_orphan_fix.py`, `test_strip_orphaned_tool_config.py` | Codex compact output, window generation or historical clipping changes |
| GPT relay provider identity | Codex gates sequential-cutoff reasoning delivery and internal ChatGPT item metadata on the selected Provider's case-sensitive display name `OpenAI`, not its `model_provider` ID; request Zstd and current-model compact fallback additionally require Codex-backend auth. Local mode therefore uses ID `codex_rosetta` with `name = "OpenAI"`, plus an explicit bearer token that keeps the configured Provider on the custom-token auth path. The provider-neutral real-service A/B suite sends both identities through the same selected relay and labels synthetic backend-auth cells | `gateway/local_mode.py`, `tests/integration/gpt_relay/`, `../openai-codex-src/codex-rs/model-provider-info/src/lib.rs`, `../openai-codex-src/codex-rs/model-provider/src/provider.rs`, `../openai-codex-src/codex-rs/core/src/session/turn.rs`, Codex request client and history normalization | Codex changes `is_openai`, configured bearer-token precedence, auth classification, request compression, metadata clearing, reasoning-summary delivery, remote compact error classification, or fallback order; a relay accepts ordinary API-key requests but rejects an OpenAI-identity wire variant |
| Model-group tool profiles | Select the bundled `builtin` Chat Default or a complete user Profile for every LLM model group. The bundled tool delivery states are immutable, while visible card fields can be explicitly saved as input-only `tool_profile_input_overrides` without modifying the packaged JSON. The Admin catalog is presented as Exec Expansion, Function, Namespace, and Rosetta Injection. Chat Default disables legacy `multi_agent_v1`, hosted `web_search`, upstream-visible `apply_patch`, and the parent `exec`; the latter two remain available only at their declared internal conversion layers. It preserves direct `wait`/`request_user_input`/`collaboration`, uses Modified `web.run` as its search surface, projects the selected Code Mode nested tools, injects `Read`/`Glob`/`Grep` plus `Edit`/`Write`, and marks `image_gen__imagegen` Modified so its Base URL/Token fields can configure the OpenAI Images bridge; any Disabled Namespace forces all child Function states to Disabled and locks their selectors. Namespace states are Expanded, Passthrough (ineffective for Chat API), and Disabled. Function Passthrough is localized as `直通`; for pure exec expansion it projects and reverses the current declaration without adding catalog text. Modified is reserved for entries with Profile-owned guidance or additional Rosetta behavior. Its cards normally show localized mutation summaries; `create_goal` and `update_goal` expose the actual selected-Profile guidance through a localized, read-only textarea. Supported paths apply per-tool disabled/passthrough/modified and injection disabled/injected states. Inputs support localized text/password/select/textarea values, state-based display, read-only textarea presentation, and UI-hidden runtime values. Modified `image_gen__imagegen` consumes its OpenAI Images Base URL/Token, while copied Profiles may enable Modified `web_search` or `web__run` with independent search Provider/Token values. The bundled Profile omits hosted `image_generation` | `gateway/tool_profiles.py`, `gateway/config.py`, `gateway/codex_auxiliary.py`, `gateway/code_mode_projection.py`, Admin tools/model-group UI, `gateway/proxy.py` | Codex tool IDs, input-definition IDs/types/defaults/localization, internal-container declarations, exec projection declarations, supported transformations, namespace expansion, injected localization dependencies, or Responses processing-mode semantics change |
| Static tool catalog | Package the fixed tool inventory, policy capabilities, optional Profile inputs/mutations, Codex normal/Code-Mode placement metadata, source-side availability conditions, and immutable Chat Default Profile source, bound to Codex CLI `0.144.4` and source commit `8c68d4c87dc54d38861f5114e920c3de2efa5876`. Code Mode Namespace members nested under `exec` are listed by their actual flat `namespace__function` names; only direct Responses Namespaces retain parent Namespace items. Keep `test_sync_tool` in the internal Profile contract but disable it in Chat Default and hide it from WebUI. Exclude `tool_search`, `tool_read`, and `invoke_deferred_tool` as static catalog items because their request-local synthetic projections are owned by `code_mode_projection.py`; also exclude obsolete hosted `image_generation` and runtime-dynamic MCP/plugin/app/connector tools | `src/codex_rosetta/gateway/admin/tool_catalog.json` | Fixed tool sets, direct-versus-exec placement, conditional exposure metadata, hidden-item rules, namespace members, profile input definitions, wire types, aliases, or model-controlled availability change |

## Compatibility point test matrix

| Compatibility points | Can be automated | Must be actually tested |
| --- | --- | --- |
| Agent-facing API | Routing, method, content type, SSE terminal/error fixture; fixed-tier body-limit validation before and after Zstd decoding, malformed/trailing Zstd rejection, authenticated decode ordering, Admin persistence/hot reload, default and unlimited runtime mapping; fake upstream single-round and multi-round playback | Real Codex completes single/multi-round via gateway, including an OpenAI-identity Zstd request and an image-heavy request above the old 50 MB ceiling; the session ends normally and errors are visible |
| Responses transparent handling | Admin exposes one `responses` protocol. Official OpenAI selects the immutable all-Passthrough Profile, OpenAI Custom and Custom + Custom select `web.run` injection, and listed third-party providers select Tool Mapping; all of them use the same direct Responses transport. Removed split protocol values are rejected during config loading. Only Profile tool changes and model-switch plaintext compaction may alter a request; other non-tool fields, native compaction, response JSON, and SSE bytes are preserved, subject to bounded raw-stream limits. Exact, attested streaming requests preserve their original wire body; changed requests use rebuilt JSON without stale attestation | Confirm each Provider-selection branch chooses the documented Profile while using the same direct transport. Capture OpenAI, relay, and listed-provider requests; verify byte-identical body plus attestation for unchanged native same-model compact, JSON fallback after Profile mutation, Profile-only differences for ordinary traffic, and Rosetta plaintext rehydration across a Provider switch |
| Codex Search and Images endpoints | Verify all three POST routes, model validation/aliasing, native pass-through path/body/status/header/error behavior, Profile-selected OpenAI Images Base URL/Bearer token forwarding for generation and edits on every LLM protocol, missing/invalid image configuration, Disabled handling, secret-free Gateway Logs, configured local Tavily and self-hosted Google search, bounded static direct-URL and stored-reference open, authenticated principal/`SearchRequest.id` isolation, retry-stable and concurrent reference allocation, TTL/capacity cleanup, Python time behavior, public-address/redirect/content-type/size/line handling, domain/context/length mapping, atomic mixed-command rejection, and stable unsupported-feature errors. Modified `web.run` projection fixtures must cover four capability states across top-level Functions, ordinary nested `exec`, Lite `additional_tools`, and Responses→Chat: no external executor exposes `open`/`time`/`response_length`; a Tavily Key or ready self-hosted Google sidecar additionally exposes `search_query` (`q`, `domains`); browser-ready sidecar health additionally exposes `click` (`ref_id`, `id`), `find` (`ref_id`, `pattern`), and `screenshot` (`ref_id`, `pageno`); both expose the union. Unsupported guidance must be removed, Passthrough must retain every live schema branch and the original description text, malformed Modified nested sections must be removed without changing sibling exec sections, and missing declarations must not be invented. Validate the shared five-second health TTL, concurrent refresh coalescing, hot-reload invalidation, sidecar bearer authentication, bounded HTTP responses, self-hosted Google query/result bounds and domain filtering, session/reference isolation and expiry, public-address enforcement, JavaScript-rendered open, numbered-link click, find, PDF embedded-text extraction, page rendering, OCR fallback, and behavior when the sidecar is unavailable. Projection fixtures must also cover both a supplied live `image_gen` declaration and the fail-closed case where Codex omits it | Invoke standalone `web.run` through Tool Mapping only with a copied Profile set to Passthrough and confirm upstream forwarding and the complete live declaration; repeat with `web.run` Modified and inspect the upstream `exec.description`: without a configured search executor it must omit `search_query`; with a Tavily Key or Self-hosted (Google) plus ready sidecar it must include the reduced query schema; browser commands must appear only after sidecar health reports `browser_ready=true`. Complete successful Tavily and self-hosted Google local searches, `turnXsearchY` static open and time calls. Enable the isolated `web-run` container, then run JavaScript `open`, `click`, `find`, a PDF open, and PDF `screenshot`; inspect Gateway Logs for sidecar executor and operation counts. Confirm Google challenge/rate-limit failures surface without fallback, expired/cross-session/unknown references fail closed, and remaining unsupported commands return fatal 501. Test a converted third-party model with Chat Default separately. Invoke `image_gen.imagegen` generation and edit through a Modified Profile and confirm the selected OpenAI Images endpoint, model alias, response, and saved artifact. Run `tests/live_agent/image_generation/01` only after `view_image` and visual recognition pass; seed the isolated home from the authorized ChatGPT OAuth source while retaining the Gateway bearer Provider, prove both model and Images requests reach the isolated Gateway, and classify an absent declaration as an auth/exposure failure before attributing anything to the upstream model |
| Request and window identity | header/body metadata extraction; exact/+1 model, window, and request-ID budgets across source formats; request-ID visible-ASCII/control validation and missing-ID generation; rejection before body/log/trace/persistence/state/upstream use; correlation/state-key separation; sequential/concurrent no-window isolation; persistent-window continuity; principal-fair provider-metadata entry/byte quotas; normal/error/cancel cleanup; and explicit attested-wire header allowlisting that never forwards client Authorization, cookies, Host, or Content-Length | Capture header/body/window/request-ID changes and maximum observed identity lengths of real turn, compact, resume, fork, subagent; verify Provider auth replaces every inbound credential on wire passthrough |
| Responses→Chat bridge | request/response/stream/history four-way fixture; fake Chat upstream multi-round tool playback; hyphenated Namespace expansion plus hyphen, dotted, underscore and uniquely unqualified child restoration in streaming/non-streaming responses; top-level Function, multi-Namespace child and alias-collision cases must remain fail-closed; `agent_message` exposes only its own encrypted payload while unrelated encrypted content remains opaque | Use `deepseek-v4-flash` to complete text, multi-round tools, error recovery and final answer; for collaboration, verify all six native calls, inter-agent payload delivery, and the model-facing name against the restored Namespace |
| Responses Lite / `additional_tools` | Accurate replay of Lite requests; extract embedded tools and developer instructions; override top/embedded tool mixing, deduplication, `reasoning.context=all_turns`, `parallel_tool_calls=false` and embedded image-generation removal | Use local mode with Provider ID `codex_rosetta`, display name `OpenAI`, and its generated catalog; use `gpt-5.6-sol` as the reference shape and `deepseek-v4-flash` for the third-party text cell; complete real multiple rounds of tool calls and confirm that the second round can consume the results |
| Codex model catalog | Diff the complete bundled JSON key set and per-model values; verify the packaged eight-entry upstream asset byte-for-byte; validate Admin detection across both the full Codex catalog and compact third-party presets, exact upstream/exposed slug detection, non-match behavior, complete manual `model_info`, alias-slug retention, compact-preset-only runtime modality filtering, Terra identity substitution, shared and per-model overrides, reasoning subsets, and unconfigured-preset exclusion. Test local-mode defaults, custom Codex Home, CLI/WebUI lifecycle, stable alias generation, TOML preservation, managed cleanup, mutation synchronization, key non-rotation, Provider replacement, rollback, and package resources | Start Codex against generated automatic and manually edited catalogs; confirm the selected alias, display metadata, Provider identity and bearer key. Restart after a WebUI model-info mutation and confirm exact persisted metadata without changing gateway image filtering. For every preset selector, run the actual third-party upstream and inspect Gateway Logs for command/code mode, Responses Lite, reasoning, context/compact, image input, search, collaboration v2, and OpenAI-identity compact behavior |
| custom/freeform tool | `apply_patch` schema/grammar/delta/call-output round-trip; Code Mode `exec` in Responses→Chat→Responses, non-streaming, added/delta/done/completed return trips are restored to `custom_tool_call`; non-compliant third-party parameters are retained, and no guessing is rewritten to JavaScript | Real Codex execution success patch, failed patch Post-fix correction; execute `exec/wait` and nested tool call for catalog with code mode enabled, confirm that tool failure is visible and fatal incompatible-payload will not appear |
| Code tool localization | native/localized schema mapping, parameter conversion, call id, result recovery and history replay | Really execute read/edit/write/search/shell, and the tool history can still be correctly consumed in the next round |
| Tool history consistency | Exact encrypted at-rest payload, authenticated restart replay, missing/wrong key and tamper fail-closed, plaintext and encrypted-v1 schema migration, row/session/principal/global row+byte budgets, replacement accounting, TTL release, transactional write rollback, abnormal replay bounds, and concurrent principal/session isolation | compact/resume/restart after multiple rounds of tools, confirm that there are no repeated calls or orphaned output; restore a matched database/key backup; exercise a session near the documented replay envelope |
| Deferred tool discovery | Fixed synthetic `tool_search`, `tool_read`, and `invoke_deferred_tool` exposure only with live deferred guidance; byte-identical top-level Chat `tools` across search/read/call; exact schemas; natural-language and regex search; bounded whole summary matches; bounded exact declaration read; paired-read allowlist authorization; direct-name conflicts; custom `exec` round trip; raw `exec` retention; no Gateway window/cache state; ordered skill/plugin contextual metadata, explicit skill/plugin injection, implicit selected-skill read, and plugin provenance | Run `tests/live_agent/deferred_tool_search/01` through `07`. Require `tool_search` and `tool_read` to translate to custom `exec`, request-local ordered candidates, selected declaration provenance, only the selected archive body/tool through raw `exec`, a consumed result, and no implicit-prompt identifier leakage. Separately require Browser Node matches to follow `tool_search → tool_read → invoke_deferred_tool`, while Gateway emits the Node custom `exec` wrapper and model-facing top-level tool bytes remain unchanged. Repeat with regex and verify that search alone or a fresh request cannot authorize dispatcher use |
| Skill delivery surfaces | Validate filesystem Skill root discovery, catalog rendering, explicit `$skill` selection, full body injection, and the separate orchestrator `skills.list`/`skills.read` schemas and opaque-handle validation against the pinned Codex source | Run `tests/live_agent/local_skills/01` through ordinary local `codex exec` and require catalog plus explicit body injection with zero Skills Namespace calls. Run `tests/live_agent/orchestrator_skills/01` only through app-server with no local execution environment, `[orchestrator.skills]`, and a provisioned Codex Apps MCP provider; require `skills.list → skills.read` to reuse the returned package/main-resource handles exactly |
| Live-agent runtime authentication | Validate the shared runtime contract, fixed credential-source paths, local-mode Provider identity, dual-auth requirement, ignored secret destinations, credential-free evidence schema, and browser-only exception | For every Gateway-backed CLI and app-server cell, copy Gateway credentials only from `~/.config/codex-rosetta-gateway` and ChatGPT OAuth only from `/Users/ibobby/.codex-multi-2/auth.json`; prove `codex login status` is ChatGPT, the bearer is present without recording it, and actual model requests reach the isolated localhost Gateway. Reject OAuth-only, bearer-only, bypassed-Gateway, or secret-bearing artifacts as invalid runner configurations |
| Codex tool usage tips | tool description/schema injection and mode availability fixture | Under local mode with Provider ID `codex_rosetta`, display name exactly `OpenAI`, and its generated catalog, use `gpt-5.6-sol` as the reference shape and execute `builtin_tools/01` through `06` for Code Mode `wait`, Plan, protocol-neutral file modification, image viewing, Goal lifecycle, and upstream visual recognition. On Chat record natural use of `Glob`/`Grep`/`Read`/`Edit`/`Write`; on direct GPT record native `apply_patch`. Use `mimo-v2.5` by default for task 06. `request_user_input` requires an app-server JSON-RPC runner because `codex exec` explicitly rejects it; do not count an exec-mode rejection as model evidence |
| Web search bridge | Configure, disabled/missing keys, search results, event reconstruction and continuation fixtures; real-loopback Content-Length/chunked/EOF/compression/timeout/cancel response limits | Real search, read results and continue to generate final answers; verify that error paths are recoverable |
| Self-hosted Bing search | Validate RSS and browser provider selection, authenticated sidecar request shape, bounded result parsing, Bing redirect unwrapping, domain filtering, challenge detection, no cross-provider fallback, reduced `search_query` projection, and text-only Codex output | Run Self-hosted (Bing RSS) and Self-hosted (Bing Browser) separately through a real Codex turn. Confirm the selected executor and counts in Gateway Logs, successful search/result continuation, redirect targets and domain filters, and a visible fail-closed error on challenge or parser failure without Google/Tavily fallback |
| Stream lifecycle | created, item/delta/done, completed/failed/incomplete sequence; huge declared HTTP chunks; no-newline/no-delimiter SSE; converted/raw/web-search overflow and early-close classification | Real streaming turn and client disconnect without duplication/truncation/stuck; terminal, cancellation, limits, and errors are presented correctly |
| Message phase | All tool signals, completed-only, added/done/completed phase consistency | Commentary/final in Codex UI is displayed correctly, mailbox/steering can work |
| Reasoning | effort/summary/content/encrypted state Cross-format round-trip and tool continuation round fixture | `deepseek-v4-flash` reasoning can be continued before, after, and in the next round of the tool without repeated thinking |
| Context compaction resilience | orphan tool config, history trimming, compact fixture and window generation; protocol fixtures are separated from one byte-identical deterministic fact-retention scenario; unchanged attested Remote V2 requests retain their exact compressed wire body, while every changed request drops stale attestation | Run the four protocol-only context/switch cells through local-mode `codex_rosetta`/`OpenAI`. Context-limit commands must retain at least 20,000 output tokens and 60,000 characters so the fixture actually crosses its threshold. Classify `/responses/compact` as legacy remote, `/responses` plus a final `compaction_trigger` as Remote V2, a later installed `compaction` as follow-up, and rollout-only compact events as local/internal. For native Remote V2, require byte-identical upstream request body and matching attestation headers. When Rosetta returns Remote V2 before stream tracing, require request-log `compaction_mode`/`compaction_reason` plus mapping/install evidence. Quality scenario completed separately: GPT native and DeepSeek Rosetta each installed exactly one compaction, resumed the same thread without another command/compaction, and preserved 8/11 fixed checks; both executor reviews are `ineffective` and non-gating |
| GPT relay provider identity | Unit-test prompt-free capture/redaction, attested-wire passthrough, and C0-C5 evaluation contracts; compile the path-pinned Codex harness | Against the same real relay/model, run C0-C5 separately and compare non-OpenAI versus `OpenAI`. Require real SSE completion and actual forwarded-model evidence; additionally require original Zstd bytes plus attestation for C3, old/current compact order plus follow-up for C4, and negative controls for C5. Confirm the Provider credential, never the gateway client credential, reaches the relay. Never count harness mocks as relay evidence |
| Model-group tool profiles | Validate the bundled/user Profile contracts, Admin CRUD and reference guards, bundled input-only override validation, model-group resolution for every protocol, all four Admin categories, per-tool filtering, text/password/select persistence, UI-hidden inputs, input/description state visibility, namespace pass-through/expansion, Disabled Namespace child-state coercion and selector locking, injected-tool selection, absence of hosted `image_generation`, and `web.run`/`image_gen.imagegen` endpoint selection | Use real Codex sessions on Chat Default and copied pass-through, web.run-mapping, and restrictive Profiles; verify bundled visible fields survive explicit save/reset while delivery states reject edits, Chat Default disables `multi_agent_v1` and upstream-visible `apply_patch`, injects exactly three read plus two write tools, keeps Function guidance text hidden behind localized summaries, preserves namespace expansion/restoration, and uses the selected search/image credentials without leaking tokens |
| Static tool catalog | Validate unique IDs, visible or explicitly UI-hidden placement, Function/Hosted input IDs/types/defaults/localization keys, normal/Code-Mode placement and condition labels, Chat Default Profile defaults, supported states, excluded dynamic tools and obsolete hosted `image_generation`, and exact CLI/source binding | Compare the catalog and Chat Default Profile against tool definitions exposed by real normal-mode and Code Mode Only Codex sessions for the target version; record Feature/runtime-gated declarations separately and verify Rosetta never synthesizes an absent declaration |

## 1. Request, header and session identity

The current Codex source code is clarified in `codex-rs/core/src/responses_metadata.rs`: the canonical carrier of the complete turn metadata is the `client_metadata["x-codex-turn-metadata"]` of the request body, and the HTTP `x-codex-*` headers are compatible projections.

Rosetta's current behavior:

- `gateway/app.py::_proxy_handler` reads `x-codex-window-id` from HTTP header;
- model IDs are limited to 256 UTF-8 bytes and window IDs to 128 UTF-8 bytes before routing/state allocation;
- window id serves as the key for tool history mapping and phase status; request-local `ALL_TOOLS` search does not use it;
- provider continuation metadata uses the same authenticated principal/window scope; it enforces 1 MiB per entry, 8 MiB per scope, 1,024 entries/16 MiB per principal, and 10,000 entries/64 MiB globally, and global count replacement never evicts another principal;
- `x-request-id` remains a trace/response correlation value and never becomes a state key; without a window header, each inbound request receives a private non-reusable scope that is cleared when non-streaming or streaming delivery ends normally, fails, or is cancelled;
- `gateway/headers.py` only forwards `x-request-id`, `User-Agent` and `OpenResponses-Version` upstream;
- Responses→Responses always leaves non-tool body fields intact, so canonical `client_metadata` never passes through IR; only cross-protocol routes require explicit metadata coverage;
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

Direct same-format Responses routing is an important forward compatibility strategy: after the selected Profile is applied, unknown non-tool fields are not compressed into the IR first, and the response is not reserialized. This invariant applies to every Provider. The Provider selection still chooses the default Profile—official OpenAI uses all-Passthrough, OpenAI/custom relays use `web.run` injection, and listed third-party providers use Tool Mapping—but never selects a different protocol-processing path. Model-switch compaction is deliberately handled before this direct path so an old Provider's opaque encrypted item is replaced by Rosetta-managed plaintext. These Profile branches and the compaction exception must remain separately testable during upgrades.

Responses→Chat is an explicit compatibility layer. After adding request item, tool type, reasoning field or SSE event to Codex, you must confirm that the converter has a clear downgrade/recovery strategy, and "request successful" cannot be regarded as agent loop compatibility.

### Responses Lite and `additional_tools`

The bundled model catalog of Codex 0.144.0 has `use_responses_lite=true` enabled for some models. In this mode, Codex no longer puts tools at the top level `tools`: it inserts a `type: "additional_tools"` item at the beginning of `input` and uses a developer message to carry the original instructions; reasoning may also use `context: "all_turns"`.

Direct Responses retains this body except for selected Profile tool changes and model-switch compaction rehydration, regardless of Provider.
Responses→Chat depends on explicit IR coverage and now merges
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
copy with only `slug`, `display_name`, and `description` replaced. The runtime
shared preset fields start from the target Terra catalog, currently the 24
client-consumed identity-independent fields reviewed from official
`0.145.0-alpha.20`. The catalog targets current flagship third-party models and
keeps Responses Lite, Code Mode only, collaboration v2, and the Terra search
metadata as fixed shared behavior; only Rosetta's verified protocol semantics
or model-specific facts justify a different value.
Every shared key may be overridden by a model entry. Known fields are
materialized from model-specific values, dedicated prompt/reasoning logic, or
this shared snapshot; `template_slug` copies only unknown future fields as a
forward-compatible fallback and cannot resurrect a known removed field or the
client-ignored `available_in_plans`, `minimal_client_version`,
`prefer_websockets`, and `reasoning_summary_format` keys. This catalog-only
review is not a full `0.145` compatibility classification. The runtime
`comp_hash` overlay uses the configured upstream model name to select a preset,
falling back to the exposed alias when no mapping exists. A preset's explicit
non-empty hash takes precedence; otherwise the name selects a reviewed group or
the deterministic custom hash. Provider identity is deliberately excluded,
aliases mapped to one upstream model share a hash, and changing the upstream
model or its selected preset can change the hash. Automated coverage must hold
alias and Provider inputs independently while varying the upstream name and
must verify explicit preset-hash inheritance. A real Codex test must
switch Providers for one unchanged upstream model without triggering
model-switch compaction, then switch the upstream name and confirm the normal
non-empty/unequal-hash compaction path. The gateway removes every active `model_catalog_json`
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
gateway key for later reuse. Synchronization compares the complete generated
catalog and TOML bytes with their snapshots and skips byte-identical writes; a
Provider-only model-group edit therefore hot-reloads gateway routing without a
Codex-file write or restart notice. Replacing managed `[memories]` assignments
reuses any retained blank-line separator before the next table, so repeated
synchronization is byte-idempotent instead of accumulating whitespace. Gateway
config, catalog, TOML, and hot activation use compensating rollback. An upgrade
must rebind the packaged asset and retest this ownership, idempotence,
compaction-hash, and Provider-identity contract before changing the declared
Codex version.

Admin exact-slug detection compares a saved `model_info` override with the
matched preset across all eight editable fields, including ordered modality and
reasoning arrays. Any mismatch is marked as modified. The editor constrains
those two arrays to checkbox values the Terra template can materialize, and its
named restore action removes the override so the detected preset is once again
authoritative. Upgrade checks must keep the comparison field list aligned with
the editable preset contract.

The Models page exposes task-model routing only while local mode is both enabled
and confirmed. It stores the source selections under gateway `codex`, copies
`auto_review_model_override` onto every generated catalog entry because
Guardian reads the current turn model's metadata, and manages only
`extract_model` plus `consolidation_model` inside Codex `[memories]`. Editable
unset or stale values are yellow and configured values are green. Inactive
local mode locks the selectors to `codex-auto-review`, `gpt-5.4`, and
`gpt-5.4-mini`, reporting each configured default in green and each missing
route in red. Clear removes the managed TOML memory assignments while retaining
the gateway selections for a later local-mode activation.

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

Deferred Code Mode tools use the live `ALL_TOOLS` runtime catalog instead of a
Gateway namespace map. When the source `exec.description` contains Codex's
deferred-tool guidance, Rosetta exposes fixed ordinary Chat `tool_search`,
`tool_read`, and `invoke_deferred_tool` Functions beside raw `exec`. Their
complete top-level definitions and order do not change across search, read, or
invocation turns.

`tool_search` validates its `query`, optional `limit`, and optional
natural-language/regex mode before Rosetta builds deterministic custom `exec`
JavaScript. The script searches only the current runtime Array and returns
versioned, bounded `{name, summary}` entries through `text(...)`. Summaries are
derived from the declaration introduction, whitespace-normalized, and limited
to 240 characters. The serialized result has a 24,000-character budget, admits
only whole candidates, and reports `returned_matches`, `total_matches`, and
`truncated`. Invalid regex is a structured zero-match result.

`tool_read` takes one exact name and generates a second custom `exec` script
that retrieves the complete declaration. Its versioned result also has a
24,000-character serialized budget and fails closed with `result_too_large`
instead of slicing the declaration. For an unblocked `mcp__` tool, the read
result appends the exact `invoke_deferred_tool` instruction. Non-MCP
declarations use raw `exec` with `tools[entry.name](...)`.

On the next Responses-to-Chat request, Rosetta recovers exactly paired
`tool_read` call/output items from request history. It accepts an exact searched
`mcp__` name only when the paired read description contains a parseable
declaration for that same tool. The three Browser Node tools retain their
existing static projections; other MCP names receive request-local generic
projections. The model supplies the fixed dispatcher name, exact deferred name,
and structured JSON arguments; Rosetta generates the outer custom `exec` call
with JSON-safe bracket access for unknown names. Discovered MCP tools never
become independent top-level Chat Functions. `CallToolResult.content` text and
image blocks are forwarded with `text(...)` and `image(...)`; other blocks are
serialized as text and `isError` remains model-visible. A result containing
only `js` never exposes either helper. Direct same-named Functions still win.
The paired search summary and complete read result remain in their original
history positions.
Malformed, oversized, unpaired, mismatched, unauthorized, and non-object calls
fail closed without invoking a dynamic tool. A direct dispatcher conflict
disables synthetic dispatcher guidance and leaves all deferred calls on raw
`exec`; a direct same-named Node Function prevents authorization for that name.
Direct Node Function calls are not accepted as a compatibility alias; Browser
runtime execution must use the fixed dispatcher after a valid paired read.

There is no discovered/deferred store, authenticated-window ownership, TTL,
quota, namespace hiding, synthesized native `tool_search_call/output`, or later
IR injection. The next request must carry the paired read history; compaction
or a fresh request without it requires another search and read. The generic
localized-call mapping may restore the model-facing names, but activation also
recognizes Rosetta's marked raw read `exec` history and therefore does not
depend on that mapping cache. Tool Profiles still own static namespace expansion
and filtering; runtime plugin/MCP availability is owned by Codex's `ALL_TOOLS`.
The generic Responses converter continues to parse native
`tool_search_call/output` for protocol compatibility, but that path does not
load tools into a Rosetta discovery cache.

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

`src/codex_rosetta/gateway/admin/tool_catalog.json` is a read-only conceptual snapshot of fixed Codex tools. Its metadata is bound to Codex CLI `0.144.4` and source commit `8c68d4c87dc54d38861f5114e920c3de2efa5876`. It intentionally excludes `tool_search`, `tool_read`, and `invoke_deferred_tool` as static items because these synthetic Functions are projected request-locally from live deferred guidance; it also excludes the obsolete hosted `image_generation` tool and runtime-dynamic MCP, plugin, app, and connector tools, including GitHub. Under Code Mode, Codex flattens namespaced tools nested in `exec` to `namespace__function` properties, so the catalog directly lists `clock__*`, `web__run`, `image_gen__imagegen`, `memories__*`, and `skills__*` without synthetic parent Namespace items. Only directly model-visible Responses Namespaces such as `collaboration` and legacy `multi_agent_v1` retain Namespace parents. The catalog does not describe the exact tools available to any individual request because features, environment availability, model metadata, Provider capabilities, and runtime extensions still control exposure.

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

Codex will request `reasoning.encrypted_content` when reasoning is turned on, and consume summary part, summary text delta/done and raw reasoning delta. Rosetta currently retains Responses summary/content/encrypted state through IR metadata, and uses provider extension fields such as `reasoning_content` in Chat upstream to maintain tool continuation. Codex's inbound `light` display value is normalized immediately to the backend value `low`; no provider request or mapping metadata should contain `light`. OpenAI Responses and Chat preserve `max`. Gateway reasoning mapping assumes every routed LLM supports reasoning; model-group reasoning/tool capability fields no longer exist. Runtime image filtering is driven only by an exact `codex_model_presets.json` match and otherwise leaves input modalities unrestricted.

Third-party reasoning controls are protocol-specific. DeepSeek V4 uses `thinking.type=enabled` with Chat `reasoning_effort` or Anthropic `output_config.effort`; GLM 5.2 Chat uses `thinking`, `reasoning_effort`, and `clear_thinking=false` when history is retained; Qwen 3.7 uses Chat `enable_thinking`/`thinking_budget`/`preserve_thinking`, Anthropic `thinking.budget_tokens`, or Responses `reasoning.effort`; Kimi K2.7 Code Chat sends no thinking control; MiniMax M3 uses Chat/Anthropic adaptive thinking and Responses `reasoning.effort`; MiMo V2.5 uses Chat/Anthropic enabled thinking and Responses `reasoning.effort`. Model-specific Responses ladders clamp unsupported `xhigh`/`max` to `high`, while generic OpenAI Chat and Responses retain `max`.

Must check when upgrading:

- New value and degradation rules for reasoning effort;
- summary `auto/concise/detailed/none` and delivery order;
- `include: ["reasoning.encrypted_content"]`;
- Empty string `reasoning_content` coexists with tool calls;
- Renewability of reasoning items after history replay, compaction and cross-format conversion.

## 6. Current clear limitations and observations

### Canonical metadata is only naturally retained in the direct path

Bridge's window-scoped logic still relies on the compatibility header. If Codex stops sending the `x-codex-window-id` header in the future and only retains the body `client_metadata`, phase and persisted tool-history mapping will no longer be windowed correctly. Deferred `ALL_TOOLS` search itself is request-local and does not use this header. Every upgrade must be confirmed with real request capture.

When the header is missing, `GatewayStateScope` creates a request-local,
non-persistent conversation ID. Tool mappings are not reused across requests,
preventing same-model sessions from sharing a fallback mapping domain. The
trade-off is loss of cross-turn restoration for that
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
