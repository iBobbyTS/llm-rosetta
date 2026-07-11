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

Codex `0.144.0` compatibility is **pending / not approved**. The controlled `deepseek-v4-flash` run is evidence for the third-party Responses→Chat, Lite/code-mode, and successful `exec` paths only. It is not native GPT evidence, and it does not close the required compact/resume/fork, plugin/MCP/deferred-tool, web-search, UI-phase, Desktop-tool, changed-error, or multi-agent scenarios. Responses WebSocket, incremental history, and remote compact remain unsupported. The itemized evidence and dirty Rosetta snapshot are recorded in [`reports/20260709-codex-v0.144.0.md`](reports/20260709-codex-v0.144.0.md).

## Current compatibility overview

| Boundaries | Current Implementation | Primary Locations | Upgrade Risks |
| --- | --- | --- | --- |
| Agent-facing API | Expose `/v1/responses` to Codex; Chat/Anthropic/Google as upstream target format; accept full-history image sessions under a configurable 64/128/256/512/1024 MiB or unlimited inbound body limit, defaulting to 128 MiB | `gateway/app.py`, `gateway/config.py`, Admin config/UI, `gateway/proxy.py` | Codex changes endpoint, transport, request shape, or retained image/history size |
| Responses internal handling mode | Admin exposes Responses Pass through and experimental Responses Rosetta as internal handling choices, not distinct wire protocols. Both target `openai_responses`; Pass through retains unknown body fields, original response JSON and original SSE bytes, while Rosetta selects the existing Responses→IR→Responses pipeline without expanding field/event coverage or guaranteeing compatibility. Raw pass-through bytes remain unchanged while the transport enforces 1 MiB per line and 8 MiB per event with no total successful-stream cap | `gateway/config.py`, `routing.py`, Admin provider UI, `gateway/proxy.py`, `test_responses_passthrough.py`, `test_config.py`, `test_http_transport_limits.py` | Codex emits a required field/event that IR cannot preserve, emits a required single line or event above the safety envelope, or changes away from HTTP/SSE framing |
| Codex Search and Images endpoints | Expose JSON `POST /v1/alpha/search`, `/v1/images/generations`, and `/v1/images/edits`; resolve the body model through normal model-group routing and forward only direct Responses Pass-through routes to the matching upstream path. Responses Rosetta, Chat, Anthropic, and Google routes return 501 without contacting upstream | `gateway/codex_auxiliary.py`, `gateway/app.py`, `routing.py`, `test_codex_auxiliary.py`, `test_downstream_routes.py` | Codex changes an endpoint path, request encoding, required headers, response shape, or stops including the routing model in the body |
| Request and window identity | Read `x-codex-window-id` as the authenticated session key for tool mapping, provider continuation metadata, deferred tools and phase behavior; enforce the documented 128-byte window and 256-byte model identity envelopes before routing/state allocation; keep external `x-request-id` correlation-only and require 1–128 visible ASCII bytes before body/log/trace/persistence/state/upstream use, generating a UUID when absent; use a private nonce when no window exists, and clear request-local state at normal/error/cancel completion | `gateway/app.py`, `gateway/embeddings.py`, `gateway/proxy.py`, `gateway/state_scope.py`, `gateway/headers.py` | Codex changes to only send canonical `client_metadata`, changes window or request-ID semantics, or needs an identity above the safety envelope |
| Responses→Chat bridge | Convert Codex Responses request to Chat via IR, and then rebuild Responses output | `converters/openai_responses/**`, `gateway/proxy.py` | High; new item/event/ fields will not be automatically transparently transmitted |
| Responses Lite / `additional_tools` | Responses→Responses can be transmitted transparently as is; Responses→Chat merges the top-level tools with `input[].type=additional_tools`, retains the developer instructions, and removes duplication according to the final Chat name | `converters/openai_responses/message_ops.py`, `converter.py`, `gateway/proxy.py` | High; 0.144.0 model catalog Responses Lite has been enabled for some models, the location of tools and developer instructions will change |
| custom/freeform tool | Identify `apply_patch` and Code Mode `exec` of Responses `type: custom`, convert into Chat callable form, and restore Codex-native tool type, call/output in response return | `openai_responses/converter.py`, `openai_responses/tool_ops.py`, `gateway/tool_adaptation.py` | Codex change custom grammar, call/output/delta event, or third-party models misinterpret freeform `exec` as JSON shell function |
| Code tool localization | Inject selected `Read`/`Edit`/`Write`/`Glob`/`Grep` definitions and translate responses back to Codex-native calls. The bundled Built-in Profile passes through `exec_command` and `write_stdin`, disables `shell_command`, and does not define or inject `Bash`; persisted historical Bash mappings remain readable | `gateway/tool_adaptation.py`, `gateway/admin/tool_catalog.json`, `test_tool_adaptation.py`, `test_admin_tools_catalog.py` | Tool name, parameter schema, call id, execution result format, or Built-in Profile state changes |
| Tool history consistency | Persist exact native/localized mappings by call id as authenticated encrypted SQLite state, rewrite subsequent history from SQLite, apply TTL cleanup, and enforce 16 MiB per row, 2,048 rows/64 MiB per session, 8,192 rows/256 MiB per principal, and 32,768 rows/512 MiB globally | `gateway/proxy.py`, `observability/persistence.py`, `observability/tool_mapping_crypto.py` | Codex history replay, compact or output shape changes; database/key backup mismatch; mapping size or session fan-out changes |
| Deferred tool discovery | Temporary `namespace` tools by authenticated Codex window, inject/process `tool_search`, restore `tool_search_call/output`, and atomically enforce 1,024 tools/16 MiB per scope, 256 unique scopes per principal, 1,000 scopes per retained map, and 64 MiB per app without cross-principal eviction | `gateway/proxy.py::WindowToolSearchStore`, Responses converter | namespace/tool_search schema, execution, compact behavior, or retained payload size/window fan-out changes |
| Codex tool usage tips | Supplement the Chat model with `request_user_input`, `create_goal`, `update_goal` and other Codex tool calling constraints | `converters/openai_chat/tool_ops.py`, related pipeline tests | schema, mode availability or Desktop/runtime tool contract changes |
| Web search bridge | Codex `web_search` can be exposed to the Chat model, the `web_search_call` event can be reconstructed and continued after Tavily execution, and Tavily responses use the primary bounded identity-encoded HTTP reader | `gateway/web_search.py`, `gateway/transport/http/transport.py`, `test_web_search_bridge.py` | native web-search item/event, tool configuration, or auxiliary response behavior changes |
| Stream lifecycle | Rebuild `response.created`, item added/delta/done, `response.completed`, etc. from Chat chunks Responses SSE; classify normal EOF, provider error, client cancellation, and bounded line/event overflow consistently in transport/telemetry/trace | `openai_responses/converter.py`, `gateway/proxy.py`, `gateway/transport/http/transport.py` | Codex parser adds required events, sequences or termination conditions; downstream disconnect semantics or maximum required event size changes |
| Message phase | Use tool calls and terminal events to infer `commentary`/`final_answer`, write phase back to message item; override native tool/web search signal | `gateway/stream_phase_buffer.py`, `test_stream_phase_buffer.py` | phase enumeration or Codex mailbox/final-answer semantic changes |
| Reasoning | Convert reasoning effort/summary, retain reasoning summary/content, `reasoning_content` and `encrypted_content` | Responses/Chat content, config, stream converters | New effort, summary delivery, reasoning event or encryption status change |
| Context compaction resilience | Remove orphan `tool_choice/tool_config` that has no tools but remains after compact; keep tool history replayable | `converters/base/helpers/tool_orphan_fix.py`, `test_strip_orphaned_tool_config.py` | Codex compact output, window generation or historical clipping changes |
| Model-group tool profiles | Select the immutable bundled `builtin` profile or a complete user profile for Responses Rosetta, Chat, Anthropic, and Google LLM model groups; only Responses Pass through omits and ignores Profiles. Supported conversion paths apply per-tool disabled/passthrough/modified, namespace disabled/expanded, and injection disabled/injected states | `gateway/tool_profiles.py`, `gateway/config.py`, Admin tools/model-group UI, `gateway/proxy.py` | Codex tool IDs, supported transformations, namespace expansion, injected localization dependencies, or Responses processing-mode semantics change |
| Static tool catalog | Package the fixed tool inventory, policy capabilities, and immutable Built-in Profile source, bound to Codex CLI `0.144.0` and source commit `2e8c3756f95789c215d9ea9a5ade6ec377934b3f`; exclude `tool_search` and runtime-dynamic MCP/plugin/app/connector tools | `src/codex_rosetta/gateway/admin/tool_catalog.json` | Fixed tool sets, namespace members, wire types, aliases, or model-controlled availability change |

## Compatibility point test matrix

| Compatibility points | Can be automated | Must be actually tested |
| --- | --- | --- |
| Agent-facing API | Routing, method, content type, SSE terminal/error fixture; fixed-tier body-limit validation, Admin persistence/hot reload, default and unlimited runtime mapping; fake upstream single-round and multi-round playback | Real Codex completes single/multi-round via gateway, including an image-heavy request above the old 50 MB ceiling; the session ends normally and errors are visible |
| Responses internal handling mode | Pass-through mode does not rewrite unknown request fields, original JSON, original SSE bytes or terminal events; experimental Rosetta mode demonstrably selects the conversion pipeline without claiming full field/event compatibility; raw passthrough enforces line/event caps without byte changes and closes on overflow | Confirm OpenAI/GPT proxies use Pass through. Treat third-party Responses implementations such as Qwen through Rosetta as unverified until real requests complete and can be continued; observe unsupported fields/events and whether any required event approaches the cap |
| Codex Search and Images endpoints | Verify all three POST routes, model validation and aliasing, upstream path/body/status forwarding, request-header allowlist, upstream errors, and 501 gating for every non-pass-through mode | Invoke standalone `web.run` and `image_gen.imagegen` through a Responses Pass-through provider; confirm the gateway log records `/v1/alpha/search`, `/v1/images/generations`, and an edit flow records `/v1/images/edits` when applicable |
| Request and window identity | header/body metadata extraction; exact/+1 model, window, and request-ID budgets across source formats and embeddings; request-ID visible-ASCII/control validation and missing-ID generation; rejection before body/log/trace/persistence/state/upstream use; correlation/state-key separation; sequential/concurrent no-window isolation; persistent-window continuity; principal-fair provider-metadata entry/byte quotas; and normal/error/cancel cleanup | Capture header/body/window/request-ID changes and maximum observed identity lengths of real turn, compact, resume, fork, subagent |
| Responses→Chat bridge | request/response/stream/history four-way fixture; fake Chat upstream multi-round tool playback | Use `deepseek-v4-flash` to complete text, multi-round tools, error recovery and final answer |
| Responses Lite / `additional_tools` | Accurate replay of Lite requests; extract embedded tools and developer instructions; override top/embedded tool mixing, deduplication, `reasoning.context=all_turns`, `parallel_tool_calls=false` and embedded image-generation removal | Enable Lite for `deepseek-v4-flash` using controlled catalog override, complete real multiple rounds of tool calls and confirm that the second round can consume the results |
| custom/freeform tool | `apply_patch` schema/grammar/delta/call-output round-trip; Code Mode `exec` in Responses→Chat→Responses, non-streaming, added/delta/done/completed return trips are restored to `custom_tool_call`; non-compliant third-party parameters are retained, and no guessing is rewritten to JavaScript | Real Codex execution success patch, failed patch Post-fix correction; execute `exec/wait` and nested tool call for catalog with code mode enabled, confirm that tool failure is visible and fatal incompatible-payload will not appear |
| Code tool localization | native/localized schema mapping, parameter conversion, call id, result recovery and history replay | Really execute read/edit/write/search/shell, and the tool history can still be correctly consumed in the next round |
| Tool history consistency | Exact encrypted at-rest payload, authenticated restart replay, missing/wrong key and tamper fail-closed, plaintext and encrypted-v1 schema migration, row/session/principal/global row+byte budgets, replacement accounting, TTL release, transactional write rollback, abnormal replay bounds, and concurrent principal/session isolation | compact/resume/restart after multiple rounds of tools, confirm that there are no repeated calls or orphaned output; restore a matched database/key backup; exercise a session near the documented replay envelope |
| Deferred tool discovery | namespace defer, search matching, multiple searches, call/output, two-way window isolation, UTF-8 byte/count budgets, per-principal unique-scope quota, same-principal global-oldest eviction, no cross-principal eviction, atomic overflow, concurrency, TTL/eviction/clear accounting | Real plugin/MCP namespace search, call, consume results, and verify that the two principals do not cross talk or exceed the retained-state envelope |
| Codex tool usage tips | tool description/schema injection and mode availability fixture | Real calls to `request_user_input`, Goal/Plan and available Desktop runtime tools |
| Web search bridge | Configure, disabled/missing keys, search results, event reconstruction and continuation fixtures; real-loopback Content-Length/chunked/EOF/compression/timeout/cancel response limits | Real search, read results and continue to generate final answers; verify that error paths are recoverable |
| Stream lifecycle | created, item/delta/done, completed/failed/incomplete sequence; huge declared HTTP chunks; no-newline/no-delimiter SSE; converted/raw/web-search overflow and early-close classification | Real streaming turn and client disconnect without duplication/truncation/stuck; terminal, cancellation, limits, and errors are presented correctly |
| Message phase | All tool signals, completed-only, added/done/completed phase consistency | Commentary/final in Codex UI is displayed correctly, mailbox/steering can work |
| Reasoning | effort/summary/content/encrypted state Cross-format round-trip and tool continuation round fixture | `deepseek-v4-flash` reasoning can be continued before, after, and in the next round of the tool without repeated thinking |
| Context compaction resilience | orphan tool config, history trimming, compact fixture and window generation | Continue tool tasks after long session triggers compact and verify resume/restart |
| Model-group tool profiles | Validate Built-in/user profile contracts, Admin CRUD and reference guards, model-group resolution for Responses Rosetta/Chat/Anthropic/Google, per-tool filtering, namespace expansion, injected-tool selection, and complete non-application only on Responses Pass through | Use real Codex sessions on Built-in and a restrictive user Profile across supported conversion routes; verify disabled tools disappear and supported adaptations behave as documented; separately confirm Pass-through bytes and tools remain unchanged |
| Static tool catalog | Validate unique IDs, references, Built-in Profile defaults, supported states, excluded dynamic tools, and exact CLI/source binding | Compare the catalog and Built-in Profile against tool definitions exposed by a real Codex session for the target version; record conditionally available tools separately |

## 1. Request, header and session identity

The current Codex source code is clarified in `codex-rs/core/src/responses_metadata.rs`: the canonical carrier of the complete turn metadata is the `client_metadata["x-codex-turn-metadata"]` of the request body, and the HTTP `x-codex-*` headers are compatible projections.

Rosetta's current behavior:

- `gateway/app.py::_proxy_handler` reads `x-codex-window-id` from HTTP header;
- model IDs are limited to 256 UTF-8 bytes and window IDs to 128 UTF-8 bytes before routing/state allocation;
- window id serves as the key for both tool history mapping and window-scoped `tool_search`/phase status;
- provider continuation metadata uses the same authenticated principal/window scope; it enforces 1 MiB per entry, 8 MiB per scope, 1,024 entries/16 MiB per principal, and 10,000 entries/64 MiB globally, and global count replacement never evicts another principal;
- `x-request-id` remains a trace/response correlation value and never becomes a state key; without a window header, each inbound request receives a private non-reusable scope that is cleared when non-streaming or streaming delivery ends normally, fails, or is cancelled;
- `gateway/headers.py` only forwards `x-request-id`, `User-Agent` and `OpenResponses-Version` upstream;
- Responses→Responses Pass-through mode leaves body intact, so canonical `client_metadata` will not be lost by IR; Rosetta mode must explicitly preserve any required metadata through IR;
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

Using Pass-through mode for same-format routing is an important forward compatibility strategy: unknown fields will not be compressed into the IR first, and the response will not be reserialized. Rosetta mode deliberately takes the Responses→IR→Responses path for third-party implementations. These are internal handling modes over the same wire protocol and must remain separately selectable during upgrades.

Responses→Chat is an explicit compatibility layer. After adding request item, tool type, reasoning field or SSE event to Codex, you must confirm that the converter has a clear downgrade/recovery strategy, and "request successful" cannot be regarded as agent loop compatibility.

### Responses Lite and `additional_tools`

The bundled model catalog of Codex 0.144.0 has `use_responses_lite=true` enabled for some models. In this mode, Codex no longer puts tools at the top level `tools`: it inserts a `type: "additional_tools"` item at the beginning of `input` and uses a developer message to carry the original instructions; reasoning may also use `context: "all_turns"`.

Responses→Responses Pass-through mode retains this body. Responses Rosetta mode
and Responses→Chat depend on explicit IR coverage. Responses→Chat now merges
top-level tools with `input[].type=additional_tools`, preserves the embedded
developer instructions, deduplicates by the final Chat tool name, and applies
image-generation filtering to both locations. Converter and gateway regression
tests cover these paths, and the 0.144.0 upgrade report records a controlled
multi-turn Lite/code-mode run through `deepseek-v4-flash`. Native GPT routing
and untriggered catalog combinations remain real-test gaps, not an
`additional_tools` implementation gap.

## 3. Codex-native tools and history replay

The current Codex source code exposes `apply_patch` as a freeform grammar tool with Responses `type: "custom"`; the call uses `custom_tool_call`, the parameter is a string, and the result uses `custom_tool_call_output`. Catalogs with code mode enabled also expose `exec` on the same wire type, whose `input` must be a raw JavaScript source, not a shell parameter object. Rosetta maintains two layers simultaneously and is compatible with:

1. The Responses converter safely downgrades a native custom tool into an IR/Chat representation, then restores the native Responses item from preserved metadata;
2. Every Responses→Chat route localizes Codex editing tools into forms more familiar to Chat models, then translates model calls back to `apply_patch`, `exec_command`, or a controlled fallback. This is protocol policy, not model configuration. Direct Responses→Responses routes bypass this adaptation and preserve the upstream body.

When Chat upstream downgrades the custom/freeform tool to a normal function call, the Responses return must restore `custom_tool_call` according to the `metadata.provider_type="custom"` recorded during the request period; this applies to both non-streaming responses and streaming added/delta/done/completed. The `{"cmd": "..."}` returned by a third-party model cannot be synthesized into JavaScript without authorization: it is evidence that the model does not adhere to freeform semantics, and should be handled by Codex as a visible tool error and let the model retry, rather than letting Rosetta guess the execution intention.

Because Codex will resend history on subsequent requests, this project saves the native/localized mapping by `call_id` and restores the exact tool call originally seen by the model before sending it upstream. Authenticated window-scoped gateway requests use encrypted SQLite state as the cross-request authority; AES-256-GCM binds each payload to its principal/provider/model/session/call scope, and diagnostic redaction never substitutes `[REDACTED]` into executable replay data. Missing, mismatched, damaged, over-budget, or inconsistently-accounted key/ciphertext state fails closed. Ciphertext plus ownership metadata is capped at 16 MiB per row, 2,048 rows/64 MiB per session, 8,192 rows/256 MiB per principal, and 32,768 rows/512 MiB globally. Cleanup, replacement-aware accounting, validation, and the final upsert share one immediate transaction; encrypted-v1 accounting migration backfills without deleting valid history. This is a critical path for prompt cache consistency with multi-round tools and must be tested with compact, resume, failed tool results, TTL/persistence, restart, quota/rollback failure, and matched database/key backup restoration.

`namespace` and `tool_search` are another Codex-specific path. Rosetta will hide namespaces that are not suitable for one-time expansion to Chat, save them by authenticated window, inject the synthesized `tool_search`, and restore the matching tool with a subsequent `tool_search_output`. The single store owner accounts canonical UTF-8 JSON bytes and nested tool count across both discovered and deferred state. It rejects a request atomically before mutation when a scope would exceed 1,024 tools or 16 MiB, a principal would exceed 256 unique scopes across both maps, or the app would exceed 64 MiB. Each retained map holds at most 1,000 scopes; when full, only the inserting principal's oldest scope may be replaced, otherwise the request is rejected. A scope present in both maps counts once toward the principal quota, and TTL, replacement, eviction, and clear paths return all accounting.

Currently the direct namespace whitelist only contains `codex_app` and `multi_agent_v1`. Codex source code already has tool planning in the direction of `multi_agent_v2`/`collaboration`; although the general defer path may take over the new namespace, there is currently no dedicated end-to-end regression, and compatibility cannot be declared based on this.

The OpenAI Chat tool converter also adds model-visible usage hints for `request_user_input`, `create_goal`, and `update_goal`. `request_user_input` can be checked against the adjacent source checkout; some Goal tools come from real Desktop/runtime payloads and do not have matching definitions there. Retain real session/tool fixtures during upgrades instead of relying only on source searches.

### Static tool catalog version binding

`src/codex_rosetta/gateway/admin/tool_catalog.json` is a read-only conceptual snapshot of fixed Codex tools. Its metadata is bound to Codex CLI `0.144.0` and source commit `2e8c3756f95789c215d9ea9a5ade6ec377934b3f`. It intentionally excludes `tool_search` and runtime-dynamic MCP, plugin, app, and connector tools, and it does not describe the exact tools available to any individual request.

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
`multi_agent_v2`/`collaboration` namespace discovery + call + output still need
dedicated real end-to-end coverage.

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
