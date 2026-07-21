# Codex Upgrade Review and Testing Checklist

Execute this checklist whenever you update the Codex CLI, sync `../openai-codex-src`, or change Codex-facing gateway/converter behavior. List unfinished items explicitly as limitations in the compatibility statement.

## Select the review mode

The developer selects the review mode and records it in the report. Do not
infer the mode from the semantic version number alone.

- **Routine release review** uses the maintained Rosetta owner map and stable
  compatibility ledger. It is intended for a release the developer judges to
  be bounded enough that the current documentation remains a trustworthy
  inventory.
- **Full inventory review** repeats the code-to-document reverse map,
  scattered-document scan, and bounded release-range validation in
  [`rosetta-source-map.md`](rosetta-source-map.md). It is the same class of
  review used to establish the current ledger.

During a routine review, stop and ask the developer whether to switch modes if
any of the following occurs:

- a Codex source change cannot be assigned to an existing `CP-*` point;
- a changed or newly discovered Rosetta owner is absent from the source map;
- a new endpoint, transport, request item, SSE event, tool family, client
  extension, identity lifecycle, compaction path, or model-catalog consumer
  crosses the existing ownership boundaries;
- the source-contract extractor and the source semantics disagree;
- point names/counts, source owners, tests, or version-specific documents have
  drifted out of sync.

These are escalation signals, not an automatic definition of a "major"
release. The developer makes the final review-mode decision.

## Authoritative execution order

The upgrade must be completed in the following order, and the latter step cannot replace the previous one:

1. Record the selected review mode, pre-upgrade Codex CLI version, Codex source commit, and Codex-Rosetta version and commit;
2. Confirm that `../openai-codex-src` has no uncommitted tracked modifications, fetch tags and remote references, resolve the exact target release commit, and use the safe update method described below;
3. Record the target Codex release and new source commit, then run the contract diff;
4. Verify the stable ledger integrity, then classify every `CP-*` item in `compatibility-points.md` as **high-confidence unchanged**, **possibly unchanged**, or **changed**;
5. Define repair plans for changed items and manual-review/live-test plans for possibly unchanged items;
6. Review the complete Codex model catalog contract and refresh `docs/en/codex-model-catalog.md` plus its Chinese counterpart when fields, defaults, bundled values, consumers, or third-party guidance changed; recheck configured Provider ID/name resolution, `is_openai()` and explicit bearer-token precedence used by local mode; then review and refresh `src/codex_rosetta/gateway/admin/tool_catalog.json` against the target tool specifications and bundled extensions, including its CLI/source metadata binding;
7. Complete repairs and run all automated checks, including compatibility-specific tests, lint, and the full non-integration test suite;
8. Run real API tests for every possibly unchanged or changed compatibility point. Use `gpt-5.6-sol` as the native Codex/GPT request-shape reference, low-cost `deepseek-v4-flash` by default for third-party non-multimodal conversion debugging, and `mimo-v2.5` for third-party multimodal tests; record substitutions and reasons;
9. After every gate passes, update the contract baseline, upgrade report, documentation, and package version, and record the exact source commit.

For a full inventory review, complete the reverse-map and documentation scan
before step 4. The output of `make check-codex-compat` is the contract group
classification, which is only one of the evidences in step 4; it cannot replace
the compatibility point classification one by one, nor can it cover the real
client behavior. Release-note labels such as "maintenance-only", "version-only",
or "no user-facing changes" never replace an exact stable-tag source diff.

## 1. Record upgrade baseline

Keep the pre-upgrade status first:

```bash
codex --version
git -C ../openai-codex-src status --short --branch
git -C ../openai-codex-src rev-parse HEAD
git -C ../openai-codex-src log -1 --date=iso-strict --format='%H%n%ad%n%s'
git status --short --branch
git rev-parse HEAD
```

Write the review mode, old CLI version, full Codex source commit,
Codex-Rosetta version/commit, and date into the upgrade report. Confirm that
the source checkout has no tracked modifications, fetch the remote state, and
resolve the exact target release before changing `HEAD`:

```bash
test -z "$(git -C ../openai-codex-src status --porcelain --untracked-files=no)"
git -C ../openai-codex-src fetch origin --tags --prune
git -C ../openai-codex-src rev-parse 'rust-vX.Y.Z^{}'
git -C ../openai-codex-src show -s --date=iso-strict \
  --format='%H%n%ad%n%s' 'rust-vX.Y.Z^{}'
```

Use `git pull --ff-only` when advancing a branch to an ancestor-compatible
target. Release patch tags may be backport snapshots and therefore may not be
descendants of the currently detached source commit. When the requested target
is an exact release tag, a clean detached checkout may instead use:

```bash
git -C ../openai-codex-src switch --detach 'rust-vX.Y.Z'
```

Record the prior commit, target tag, peeled target commit, and whether the move
was fast-forward or exact-tag detach. If the tracked checkout is dirty, the
target cannot be resolved after fetch, or an in-place branch update is not
fast-forwardable, stop before changing source state. Do not reset, stash, or
overwrite existing work. Do not substitute a `0.0.0`/`0.0.0-dev` manifest
value for the source commit, and do not treat an unfetched local `origin/main`
as the latest remote state.

## 2. Review Codex source contract diff

At least check the following files before and after the upgrade:

```text
codex-rs/codex-api/src/common.rs
codex-rs/codex-api/src/endpoint/responses.rs
codex-rs/codex-api/src/endpoint/search.rs
codex-rs/codex-api/src/endpoint/images.rs
codex-rs/codex-api/src/sse/responses.rs
codex-rs/core/src/client.rs
codex-rs/core/src/responses_metadata.rs
codex-rs/core/src/session/turn.rs
codex-rs/core/src/context_manager/
codex-rs/core/src/compact_remote.rs
codex-rs/core/src/compact_remote_v2.rs
codex-rs/core/src/compact_remote_v2_attempt.rs
codex-rs/prompts/templates/compact/prompt.md
codex-rs/prompts/templates/compact/summary_prefix.md
codex-rs/core/src/tools/spec_plan.rs
codex-rs/core/src/tools/handlers/apply_patch_spec.rs
codex-rs/core/src/tools/handlers/tool_search.rs
codex-rs/protocol/src/models.rs
codex-rs/protocol/src/openai_models.rs
codex-rs/tools/src/tool_spec.rs
codex-rs/models-manager/models.json
codex-rs/model-provider/src/provider.rs
codex-rs/model-provider-info/src/lib.rs
codex-rs/code-mode/
codex-rs/code-mode-host/
codex-rs/code-mode-protocol/
codex-rs/core-skills/
codex-rs/ext/skills/
codex-rs/ext/web-search/
codex-rs/ext/image-generation/
codex-rs/core-plugins/
codex-rs/app-server/
codex-rs/app-server-protocol/
codex-rs/shell-command/
src/codex_rosetta/gateway/codex_models.json
src/codex_rosetta/gateway/codex_models_version.md
```

Key points to confirm:

- Whether the selection/fallback of HTTP `/responses`, SSE and WebSocket has changed;
- Whether standalone Search and Images still use JSON `POST /v1/alpha/search`, `POST /v1/images/generations`, and `POST /v1/images/edits`, including their model field and required headers;
- request body fields added, deleted or changed;
- The source and life cycle of `client_metadata`, `x-codex-*`, session/thread/window/turn;
- `response.output_item.*`, text/tool/reasoning delta and terminal event;
- `MessagePhase` enumeration and fallback of missing phase;
- Tools such as function/custom/namespace/tool_search/web_search wire shape;
- tool type, grammar, call/output item of `apply_patch`;
- reasoning effort, summary, encrypted content and response headers;
- History and window generation after compact, resume, fork, subagent;
- `RemoteCompactionV2` stage/default, final `compaction_trigger`, exactly one
  `compaction` output with required string `encrypted_content`, metadata enum
  values, and the non-empty/unequal `comp_hash_changed` predicate;
- `ModelInfo` and nested-struct fields, enum wire values, serde rename/default/skip behavior, instruction-template precedence, unknown-model fallback, and every runtime consumer;
- local catalog `comp_hash` selection from the upstream model name, including explicit non-empty preset-hash precedence and alias inheritance, deterministic fallback, stability across exposed-alias and Provider changes, plus a change when the upstream model changes;
- For every Codex model-catalog upgrade, compare the target `gpt-5.6-sol` entry field by field with `codex_model_presets.json`: classify identity/context/modality/reasoning/prompt fields as model-specific, trace every remaining field to the target client's `ModelInfo` and runtime consumers, copy client-consumed shareable fields into `shared_overrides`, preserve the fixed Responses Lite/Code Mode only/collaboration v2 surface for current flagship third-party models, record any protocol-specific deviation from Terra, verify every shared key can be overridden by each `models[]` entry, and verify `template_slug` fills only previously unknown fields rather than restoring removed or client-ignored fields;
- The complete bundled `models.json` key set and per-model values, including keys ignored by the current client and valid defaulted fields omitted from the JSON;
- Rosetta's packaged `src/codex_rosetta/gateway/codex_models.json` and its
  adjacent `codex_models_version.md` as one atomic review unit: compare every
  JSON value with the target Codex `models.json`, set the sidecar version to
  the reviewed Codex release, and record both the semantic diff and source
  commit. Until the legacy version-named catalog resource is retired, compare
  it too and do not accept divergent bundled catalog copies;
- Whether catalog-selected tool surfaces changed, especially `web.run` versus hosted `web_search` and collaboration v2 versus `multi_agent_v1`.
- Whether local filesystem Skills still use catalog plus selected-body injection,
  while orchestrator-owned Skills still require app-server, no local executor,
  and exact `skills.list`/`skills.read` resource handles;
- Whether command-policy rejection remains a client-side error surface or
  changes the model-facing custom/Code-Mode tool schema, call/output item, or
  recovery loop.

## 3. Compare Rosetta ownership boundaries

For each Codex contract diff, clearly fall into one of the following ownership disposition categories:

1. **Direct passthrough has been naturally retained**: supplementary testing proves that unknown fields or original SSE have not been overwritten;
2. **Bridge must be adapted**: modify converter/gateway, and add request, stream and multiple rounds of testing;
3. **Currently not supported**: Record disabling methods, fallback dependencies and test conditions before enabling.

Use [`rosetta-source-map.md`](rosetta-source-map.md) as the exhaustive owner
index. The following are the high-level entry points, not a substitute for that
map:

```text
src/codex_rosetta/gateway/app.py
src/codex_rosetta/gateway/auth.py
src/codex_rosetta/gateway/codex_auxiliary.py
src/codex_rosetta/gateway/codex_compaction.py
src/codex_rosetta/gateway/codex_compact_prompt.md
src/codex_rosetta/gateway/codex_compact_summary_prefix.md
src/codex_rosetta/gateway/headers.py
src/codex_rosetta/gateway/proxy.py
src/codex_rosetta/gateway/providers.py
src/codex_rosetta/gateway/stream_phase_buffer.py
src/codex_rosetta/gateway/tool_adaptation.py
src/codex_rosetta/gateway/web_search.py
src/codex_rosetta/gateway/web_run_capabilities.py
src/codex_rosetta/gateway/web_run_health.py
src/codex_rosetta/gateway/web_run_sidecar.py
src/codex_rosetta/gateway/web_run_supervisor.py
src/codex_rosetta/gateway/local_mode.py
src/codex_rosetta/gateway/cli.py
src/codex_rosetta/gateway/codex_models.json
src/codex_rosetta/gateway/codex_models_version.md
src/codex_rosetta/gateway/codex_model_presets.json
src/codex_rosetta/gateway/admin/tool_catalog.json
src/codex_rosetta/capabilities.py
src/codex_rosetta/reasoning_mapping.py
src/codex_rosetta/pipeline.py
src/codex_rosetta/observability/persistence.py
src/codex_rosetta/observability/tool_mapping_crypto.py
src/codex_rosetta/converters/openai_responses/
src/codex_rosetta/converters/openai_chat/
src/codex_rosetta/converters/base/helpers/tool_orphan_fix.py
src/codex_rosetta/types/openai/responses/
```

### Compatibility point classification and repair plan one by one

Before classification, verify that the stable registry, compatibility overview,
and test matrix contain the same current point set (23 points at the
`2026-07-18` baseline). A missing, duplicated, or renamed row is a
documentation defect and must be fixed before the release decision; when a new
point is added, update all three structures in the same task.

After completing the source diff and automated contract report, copy every
`CP-*` ID and canonical name from `compatibility-points.md` and fill in these
fields. The number of report rows must match the registry. Save reports under a
descriptive date-free name such as
`docs/dev/version-compatibility/reports/range-coverage-review.md`, then put
the date followed by the target label, for example
`Codex version: 0.145.0-alpha.23`, directly below the title:

| ID and compatibility point | Classification | Source code/contract evidence | Fix or review plan | Automation results | Real API results |
| --- | --- | --- | --- | --- | --- |
| `CP-XX — <canonical name>` | High confidence no change / probably no change / yes change | `<commit/diff/code location>` | `<no fix, review step, or fix required>` | `<command and result>` | `<model, route, result, or non-trigger reason>` |

Classification rules:

- **High confidence no change**: The relevant source code semantics and Rosetta ownership boundaries can be proven unchanged by a complete diff/hash/ automatic test; "the same field name" alone is not enough to enter this category;
- **Possibly no change**: The name or surface structure is consistent, but the type, default, serde, calling sequence, client consumption or model behavior are not fully proven;
- **Changes**: any changes in source code, wire contract, default values, behavior, Rosetta adaptation or test expectations.

"There are changes" must be written down first and then implemented; "There may be no changes" must be written clearly about unknown points and how real testing can eliminate uncertainty. Both categories must have real API results and cannot be replaced by mocks/fixtures.

## 4. Test layering

Upgrade testing is divided into two categories, and the results must be recorded separately:

1. **Testing that can be completed through automation**: It can be completed entirely using fixtures, fake upstreams, local gateways, static checks or deterministic assertions, and does not require the participation of the real model.
2. **Items that must be actually tested**: A real Codex client must be started and connected to a real model/upstream. Can be orchestrated with scripts or agentabi, but cannot substitute mocks or recorded responses for actual model behavior.

The current phase does not require that all of the automated tests listed below have been implemented. The list also assumes the automation construction backlog: unimplemented items are marked as "automatable and to be implemented" instead of mistakenly marked as "no testing required".

### 4.1 Can be accomplished through automation

#### A. Codex source code contract change detection

The following checks can be made into automatic diff/snapshot jobs for commits before and after upgrades:

- Monitor whether the Codex source code files listed in Section 2 have changed;
- Export and compare the field collection and default value of `ResponsesApiRequest`;
- Export and compare `ResponseItem`, `ResponseInputItem`, `MessagePhase` and SSE event names;
- Export and compare the tool schema of function/custom/namespace/tool_search/web_search;
- Export and compare `apply_patch` freeform grammar and call/output wire shape;
- Export and compare `ModelInfo` and nested-struct fields, enum wire values, serde rename/default/skip behavior, instruction-template precedence, unknown-model fallback, and runtime consumers;
- Export and compare the complete bundled `models.json` key set and per-model values, including ignored keys and omitted fields that receive defaults;
- Monitor the header/body key of `x-codex-*` and session/thread/window/turn metadata;
- Monitor endpoint, feature flag and fallback changes of `/responses`, Responses WebSocket, `/responses/compact` and Responses Lite.

Automatic diff is responsible for indicating "what has changed" and cannot replace the maintainer's review of semantics and Rosetta impact.

The first batch has been achieved:

```bash
make check-codex-compat
```

`scripts/check_codex_compatibility.py` currently extracts and compares from the `../openai-codex-src` fixed point:

- Codex source code commit;
- Responses HTTP/WebSocket/compact request field collection;
- Collection of Rust variants of Response/Input/Content item, phase and internal response event;
- The event name actually processed by SSE parser;
- Codex HTTP header, turn metadata, WebSocket metadata key, endpoint and beta value;
- `apply_patch` name, format, syntax and grammar SHA-256;
- tool spec wire type, `ModelInfo` field and key model enum variant.

The parser, deterministic baseline and diff semantics of the extractor are covered by ordinary pytest; the current source code comparison across warehouses is an explicit upgrade gate. If the source code path/anchor is missing, an error will be returned and no skip or false positive will be passed.

Three categories of results must be retained each time a check is performed:

- **High confidence that there is no change**: The specific contract values of the current automated complete comparison are consistent;
- **Possibly unchanged**: only proves that the currently extractable name/member set is consistent, still requires manual review of uncovered types, defaults, serde and behavioral semantics;
- **Changed**: Commit or extracted value changes, the impact of Rosetta must be determined in combination with detailed diff.

You can't incorporate "probably no changes" into the pass, and you can't omit these three categories just because unified diff is empty. The automated final exit code only expresses whether there is a blocking change/extraction error and does not replace the review of the second category.

Still to be implemented: field types, nested model-catalog structs, serde rename/default/skip strategy, instruction-template precedence, full bundled model values and consumer mapping, SSE match arm digest, full generic tool schema, tool_search defaults, and model fallback initializer. The extractor currently covers the `ModelInfo` field set, key model enums, and a Responses Lite capability subset only. Alpha.23 additionally renames the reasoning-summary capability, adds permission-message fields, types response-item IDs, adds structured Search results and cache-write usage, and changes Code Mode and Realtime contracts. The current baseline therefore cannot claim complete target compatibility, and the extractor must be adapted before baseline refresh.

The `0.144.6` range audit and the alpha.23 source review prove why the manual catalog step is mandatory:
Codex changed only bundled Sol/Terra/Luna instruction and context-window values,
while the extracted content groups still matched the `0.144.4` baseline. For
every routine or full review, diff the complete target `models.json` values
against both the previous Codex release and Rosetta's packaged catalog asset,
even when the contract output reports only source-commit drift.

#### B. Fixture and unit/component testing

The attested-wire allowlist is an exact, case-insensitive 12-header baseline for
the current Codex version. Every routine and full Codex version review must
enumerate the target Codex request headers from both source and a real captured
request, diff them against this list, and explicitly accept, reject, add, or
retire every difference:

1. `Accept`
2. `Content-Encoding`
3. `Content-Type`
4. `Originator`
5. `Session-Id`
6. `Thread-Id`
7. `x-client-request-id`
8. `x-codex-beta-features`
9. `x-codex-turn-metadata`
10. `x-codex-window-id`
11. `x-oai-attestation`
12. `x-openai-internal-codex-responses-lite`

The runtime owners are
`src/codex_rosetta/gateway/headers.py`,
`src/codex_rosetta/gateway/inbound_content_encoding.py`,
`src/codex_rosetta/gateway/proxy.py`, and
`src/codex_rosetta/gateway/transport/http/transport.py`. The deterministic
contract owners are `tests/gateway/test_app_headers.py`,
`tests/gateway/test_inbound_content_encoding.py`,
`tests/gateway/test_responses_passthrough.py`, and
`tests/gateway/test_http_transport_limits.py`. An upgrade report must name the
captured target-version header set and the resulting allowlist decision; a
passing old fixture or the absence of a new header from Rosetta source is not
sufficient evidence. Client `Authorization`, `Cookie`, `Host`, and inbound
`Content-Length` remain excluded, and Provider configuration must continue to
own upstream authentication. The Gateway-owned `x-request-id` is also excluded
from exact attested-wire forwarding even though it remains valid ordinary
upstream correlation metadata; injecting a header absent from the captured
client wire invalidates the transparency contract. The ingress contract test must run the decoder
through the real App dispatcher, not only call it directly, so request-local
wire capture cannot be lost if a synchronous middleware hook is moved to the
server's worker thread.

The following behavior can be automatically verified using the fixed Codex request/SSE fixture:

- The single Admin Responses protocol always uses direct Responses transport for every Provider; Provider selection changes only the default Tool Profile. Unknown non-tool fields and original response JSON/SSE bytes remain unchanged below the transport safety envelope except for mandatory replacement of reflected configured Provider credentials. Unchanged attested streaming requests retain their original compressed body and allowlisted Codex wire headers; any request mutation rebuilds JSON without stale attestation. Native `context_limit`/`user_requested` compaction evaluates exact raw-wire eligibility before Tool Profile and web-search adaptation, while model-switch compaction must use the previous model with Rosetta's prompt and a seven-day plaintext mapping;
- header allowlists for ordinary metadata and exact attested-wire passthrough; Provider-owned Authorization on every upstream request; `x-codex-window-id` extraction; exact/+1 model, window, and request-ID budgets; visible-ASCII/control rejection and missing request-ID generation; rejection before body/log/trace/persistence/state/upstream use; correlation/state-key separation; private no-window scope and terminal cleanup;
- Responses request → IR/adapter → Chat/Anthropic/Google upstream request;
- Responses Namespace children expand to canonical regex-safe `namespace-function` names; streaming and non-streaming return paths restore hyphenated names, unique `namespace_function` and `namespace.function` compatible names, and unique bare children, while ordinary Function conflicts, shared child names, and alias collisions remain flat and fail closed;
- Responses→Chat converts `agent_message` into model-visible user content, including its inter-agent `encrypted_content` payload, without exposing encrypted content from ordinary message or reasoning items;
- Verify model-group Tool Profiles on direct Responses, Chat, Anthropic, and Google routes, including all four Admin categories, Disabled filtering, Modified handling, input persistence, immutable bundled delivery states, and endpoint selection. Verify the four bundled Profile names and states: Chat Default retains its established mapping; 透传 leaves every native tool Passthrough with synthetic injections Disabled; web.run 注入 differs from 透传 only by Modified `web.run`; 工具映射 inherits Chat Default. Verify new model groups choose 透传 for OpenAI Official, web.run 注入 for OpenAI Custom and Custom + Custom Responses, 工具映射 for listed-provider Responses, and Chat Default for Chat plus the intentionally separate fallback branch. All Responses choices must use identical direct protocol handling;
- Verify model-group rows prefer an exact upstream-model slug, fall back to the exposed model name, search the unified `codex_models.json` resource and `codex_model_presets.json`, reject partial/suffixed matches, display modalities from matched catalog metadata, and always expose the right-side manual model-info editor. Confirm gateway image filtering uses only exact compact-preset `input_modalities`, while full-catalog and saved `model_info` values do not impose runtime restrictions; matched rows prefill all compact editable fields, unmatched rows start empty, saved `model_info` survives reload, and the exposed alias remains the generated catalog slug;
- Responses Lite `additional_tools`, developer instructions, `reasoning.context=all_turns` and embedded tool filtering/deduplication;
- non-streaming/streaming upstream response → Codex Responses output;
- The order of `response.created`, item added/delta/done, completed/failed/incomplete;
- message `commentary`/`final_answer` is consistent in added, done and completed;
- Text followed by phase inference of function/custom/MCP/shell/computer/tool_search/web_search call;
- custom/freeform `apply_patch` and code-mode `exec` definition, grammar, delta splicing, call/output and fallback command; among them, `exec`'s Chat downgrade return must be restored to `custom_tool_call`, and non-compliant JSON function parameter guessing must not be rewritten into JavaScript;
- Modified code-mode `web.run` derives from the live Codex declaration but advertises only Rosetta-supported commands and nested fields, removes unsupported command guidance, and keeps the same capability whitelist across top-level Functions, Responses→Chat projection, ordinary Tool Mapping only `exec.description`, Lite `additional_tools`, and runtime rejection. Test absent/configured Tavily and Self-hosted (Google) with absent/browser-ready sidecar: `open`/`time`/`response_length` are always retained, `search_query` requires either the Tavily Key or selected self-hosted provider plus `browser_ready=true`, and `click`/`find`/`screenshot` require `browser_ready=true`. Verify the shared five-second health cache, concurrent refresh coalescing and hot-reload invalidation. Passthrough retains the complete live declaration; missing declarations are not invented; malformed Modified nested sections are removed without altering sibling exec sections;
- Chat Default Code Mode projection keeps the Disabled parent `exec` through Responses-to-Chat conversion, parses the live Codex declarations for `exec_command`, `write_stdin`, `update_plan`, `apply_patch`, `view_image`, `web.run`, conditional `image_gen`, Goal, Clock, Memories, Skills, and conditional deferred/environment/context/plugin/MCP-resource/Agent-Job tools; covers the Codex renderer's literal, union, intersection, array, tuple, object-index-signature, and normalized-heading forms; rejects unknown TypeScript tokens; emits ordinary Chat Functions only for valid model-visible declarations; never invents a tool when Codex's Feature, runtime, auth, environment, modality, or session-source gates omit it; removes only exact successfully replaced heading-through-declaration-fence spans and retains raw `exec` for deferred guidance, unknown or duplicate sections, malformed known sections, direct-name conflicts, and unparsed source formats; removes the parent only after all recognized sections are consumed; uses the matching `text(...)`, `image(...)`, or `generatedImage(...)` result helper; keeps `apply_patch` model-hidden while retaining its internal projection for localized `Edit`/`Write`, and consumes its description only when all catalog-declared replacements were emitted; rebuilds deterministic custom `exec` calls in streaming and non-streaming paths; preserves same-named direct Functions; keeps `wait`, `request_user_input`, `new_context`, and `collaboration` direct; restores collaboration namespaces; and replays projected history from the encrypted mapping cache within TTL;
- native/localized tool history mapping with exact encrypted SQLite payloads,
  authenticated same-process/restart replay, missing/wrong key and tamper
  fail-closed behavior, plaintext/encrypted-v1 migration, row/session/principal/
  global row+byte budgets, replacement accounting, TTL release, transaction
  rollback, abnormal replay bounds, failure results and subsequent-round replay;
- provider continuation metadata principal entry quotas, same-principal global-oldest replacement, no cross-principal eviction, replacement/TTL/clear accounting, and concurrent saturation;
- request-local `ALL_TOOLS` discovery only with live deferred guidance; fixed `tool_search`, `tool_read`, and `invoke_deferred_tool` definitions beside raw `exec`; byte-identical top-level Chat `tools` across search/read/call; exact schemas and validation; natural-language/regex search JavaScript; versioned `{name, summary}` output with 240-character summaries and a 24,000-character whole-match budget; exact-name read JavaScript with a complete-declaration 24,000-character fail-closed budget; custom `exec` round trips; direct-name conflicts; and no Gateway discovery state. Verify search alone cannot authorize the dispatcher; paired read history authorizes exact `mcp__` names only after same-name declaration parsing; the three Node REPL names retain their static projections while unknown MCP names use JSON-safe bracket dispatch; latest same-name reads replace older outcomes while unrelated reads do not revoke authorization; `js` does not imply helper authorization; structured dispatcher arguments produce custom `exec` with text/image/`isError` forwarding in streaming and non-streaming paths; malformed, oversized, unpaired, fake-protocol, mismatched-name, unauthorized, non-MCP, non-object, legacy-v1, and direct-call inputs fail closed; direct MCP/read/dispatcher Functions take precedence. Verify search summaries and complete read results retain history order, unsupported non-MCP declarations retain raw-`exec` instructions, and no discovered MCP Function is added to top-level `tools`. Keep native `tool_search_call/output` converter fixtures separately as protocol compatibility coverage;
- Captured wire fixtures for `multi_agent_v1`, `multi_agent_v2/collaboration`;
- Captured wire fixtures for code mode `exec/wait`, nested call and wait continuation;
- web search multi-round event reconstruction, downgrade paths for disabled/missing search executors, bounded identity-encoded auxiliary HTTP responses, Tavily and self-hosted Google result normalization into the unchanged Codex string `output`, and standalone Search `turnXsearchY` allocation/open scoped by authenticated principal plus `SearchRequest.id`, including retry stability, concurrent allocation, cross-session failure, TTL/capacity cleanup, and app shutdown cleanup;
- Cross-format round trip of reasoning effort/summary/content/encrypted state, including exact OpenAI Responses and Chat `max` preservation, immediate Codex `light` to backend `low` normalization with no downstream `light`, no model-group reasoning/tool capability gates, and protocol-specific DeepSeek V4, GLM 5.2, Qwen 3.7, Kimi K2.7 Code, MiniMax M3, and MiMo V2.5 controls;
- orphan call/result, residual tool choice/config and history trimming after compact;
- Concurrency windows, cache expiration, normal EOF, abnormal EOF,
  huge peer-declared HTTP chunks, oversized no-newline SSE lines, accumulated
  no-delimiter events, converted/raw/web-search client cancellation, upstream
  4xx/5xx and retry boundaries; verify that below-limit raw Responses SSE is
  byte-identical except for configured Provider credentials split at every
  possible chunk position, and that overflow closes the upstream;
- Inbound request-body default, fixed tiers, Admin persistence/hot reload,
  rollback, unlimited mapping, and a real Codex image-history request above the
  former 50 MB ceiling;
- `/v1/models` current OpenAI-style response remains distinct from Codex's dynamic `ModelInfo` catalog endpoint; statically verify the complete bundled catalog/schema contract without treating the gateway route as that endpoint;
- Local-mode upstream catalog asset/source equality, eight-entry order, exact-name Terra-derived preset validation and prompt identity substitution, target-Terra-derived `shared_overrides` field/value policy, per-model override precedence for every shared key, unknown-template-field fallback without inheritance of removed or client-ignored known fields, generic fallback aliases, Admin checkbox values plus all-field modified-preset detection and named restore behavior, CLI/WebUI first-use confirmation and clear behavior, `--no-local-mode` persistence without Codex Home mutation, custom Codex Home, TOML preservation, repeated synchronization with memory overrides remaining byte-idempotent, managed-only deletion, startup/model-mutation synchronization, remote-host warning, compensating rollback, and wheel/sdist resource inclusion;
- Configuration/admin UI saving, defaults and runtime loading of Codex tool-adaptation switches.
- Static tool-catalog contract: unique IDs, valid visible or explicitly UI-hidden placement/policy references, required fixed tools, direct Responses Namespace parents versus flat Code Mode `namespace__function` entries, normal/Code-Mode placement and conditional-exposure localization keys, generic per-state description keys, Function/Hosted input IDs/types/defaults/localization keys, no Profile inputs on `web.run`, hidden and Chat-Default-disabled `test_sync_tool`, excluded runtime-dynamic plugin/MCP/app/connector tools and obsolete hosted `image_generation`, current `image_gen__imagegen` coverage, bundled Profile defaults, supported states, and exact CLI/source metadata binding.

New fixture/component coverage in this round:

- Streaming item event and completed-only phase fallback of `tool_search_call`/`web_search_call`;
- Two-way isolation of deferred namespace search of two Codex windows;
- Reused `x-request-id` sequential/concurrent isolation, real-window continuity, and request-local normal/error/cancel cleanup;
- Deferred-tool canonical UTF-8 byte/count budgets, atomic overflow, replacement, concurrent writers, and TTL/eviction/clear budget return;
- Deferred-tool and provider-metadata per-principal count quotas, same-principal global-oldest replacement, cross-principal rejection, unique-scope accounting across loaded/deferred maps, and concurrent saturation;
- Encrypted tool-mapping row/session/principal/global row+byte quotas, replacement without double counting, expiry budget release, raw SQLite accounting, encrypted-v1 backfill, bounded replay, concurrent saturation, and transactional write rollback;
- Tavily real-loopback normal JSON, Content-Length/chunked/EOF overflow, compressed-response rejection, timeout and cancellation; self-hosted Google and Bing sidecar bearer authentication, provider routing, request/result bounds, domain filtering, concurrency limit, challenge detection, Bing redirect unwrapping, and no-fallback failures; pinned Patchright/Chromium image build, headful Xvfb startup under the seccomp profile, and browser/PDF regression without any Playwright runtime dependency;
- Tool-mapping TTL validation for environment strings, booleans, non-finite/overflow values, and the inclusive 720-hour boundary;
- Codex source contract extractor tests for Rust comment/string/braces, enum wire rename, commit and contract drift separation, and baseline canonical serialization.

The remaining items are still listed in this section as the automation backlog. Request-handler lifecycle playback now covers concurrent no-window isolation and persistent-window continuity, while complete local gateway conversion playback with concurrent real HTTP clients remains to be implemented.

Existing special test commands:

```bash
CONDA_ENV="${CODEX_ROSETTA_CONDA_ENV:-llm-rosetta}"
conda run -n "$CONDA_ENV" python -m pytest \
  tests/gateway/test_auth.py \
  tests/gateway/test_app_headers.py \
  tests/gateway/test_request_state_lifecycle.py \
  tests/gateway/test_http_transport_limits.py \
  tests/gateway/test_responses_passthrough.py \
  tests/gateway/test_codex_compaction.py \
  tests/gateway/test_stream_phase_buffer.py \
  tests/gateway/test_code_mode_projection.py \
  tests/gateway/test_tool_adaptation.py \
  tests/gateway/test_codex_page.py \
  tests/gateway/test_codex_search.py \
  tests/gateway/test_codex_auxiliary.py \
  tests/gateway/test_web_run_health.py \
  tests/gateway/test_web_run_sidecar.py \
  tests/gateway/test_web_run_supervisor.py \
  tests/gateway/test_web_run_google_search.py \
  tests/gateway/test_web_run_bing_search.py \
  tests/gateway/test_web_search_bridge.py \
  tests/gateway/test_model_presets.py \
  tests/gateway/test_local_mode.py \
  tests/gateway/test_config.py \
  tests/gateway/test_admin_config_routes.py \
  tests/converters/openai_chat/test_message_ops.py \
  tests/converters/openai_chat/test_tool_ops.py \
  tests/converters/openai_responses/test_tool_ops.py \
  tests/converters/openai_responses/test_stream.py \
  tests/converters/test_strip_orphaned_tool_config.py \
  tests/test_reasoning_mapping.py \
  tests/test_provider_reasoning_transforms.py \
  tests/test_codex_source_contract.py \
  tests/live_agent/test_live_agent_configuration_contract.py \
  tests/test_pipeline.py -q
```

The current development machine inherits the historical environment name `llm-rosetta`; if the environment has been renamed by project, it is overwritten by `CODEX_ROSETTA_CONDA_ENV`. Don't count a test as passing when the environment doesn't exist.

#### C. Local Integration and Quality Gate Control

The following projects can also be fully automated and do not require real models:

- Start the local gateway and play back multiple rounds of fixtures on fake Responses/Chat upstream;
- Send two windows concurrently to verify that phase and tool mapping are not in the same state; verify independently that `ALL_TOOLS` search allocates no Gateway discovery state, activates Node REPL projections only from each request's own paired history, and cannot reuse another request's catalog;
- Play back the desensitization Codex request captured before and after the upgrade, and compare the conversion results with the SSE transcript;
- Run four-way regression of request, response, stream, and history for each newly added wire shape;
- lint, format, type/build, full warehouse non-integration testing;
- Check whether the compatibility baseline, source code commit and upgrade records are updated simultaneously;
- Check that the log/trace fixture does not contain the API key or the sensitive header is not desensitized.

Current full warehouse access control:

```bash
make lint
make test
```

When a transformation, gateway, or public type has a behavior change, you must not just run the newly added test file.

### 4.2 Must connect real Codex and model testing

Whether these project-dependent models understand tool specifications, form correct multi-round behavior, and how Codex clients actually consume events cannot be proven by fixtures or mocks.

#### Recommended actual test model

Select a model by debugging target, don't just look at the Codex-facing alias:

- When you need to observe the Codex/GPT native request, tool, reasoning, transport or stream shape, use `gpt-5.6-sol` as the reference, and use Rosetta trace to confirm that the upstream is indeed a GPT route; the alias with the same name forwarded to a third-party provider cannot be used as evidence of the original GPT request;
- When debugging the Responses→Chat bridge, reasoning and multi-round tool calls of third-party models, `deepseek-v4-flash` is used by default because the cost is lower;
- For third-party multimodal live-agent paths, use `mimo-v2.5` by default and verify real image input/output in Gateway Logs;
- Actual testing of version upgrades should at least include scenarios related to this change in the above two categories;
- If an item is only applicable to same-format Responses, specific hosted tools or model-specific capabilities, add the corresponding upstream;
- If `deepseek-v4-flash` is temporarily unavailable, use a low-cost model with similar capabilities and support tool calls, and record the alternative model in the results, and cannot skip it without explanation;
- Actual tests must record model, provider/route, Codex CLI version, Codex source commit and Codex-Rosetta commit.

#### A. Basic session and real request

- Start the real Codex CLI/Desktop and connect `deepseek-v4-flash` via Rosetta;
- Start once with local mode and verify that Codex selects Provider ID `codex_rosetta`, classifies its exact `name = "OpenAI"` through `is_openai()`, authenticates with the stable generated `codex` gateway key, and reaches the effective CLI/configured port; restart and confirm the key is not rotated;
- With confirmed local mode, select separate approval-review, memory-consolidation, and memory-extraction aliases in Admin; verify every generated catalog entry carries the selected `auto_review_model_override`, `[memories].consolidation_model` and `[memories].extract_model` match, and real Guardian/phase-one/phase-two requests route to those aliases. Delete one selected alias and verify Admin marks it stale without silently changing the stored value; disable local mode and verify the UI locks to `codex-auto-review`, `gpt-5.4`, and `gpt-5.4-mini` while the managed TOML memory fields are removed;
- Complete a single round of text and multiple rounds of dialogue, and confirm that there are no repeated, truncated or unended turns;
- Capture real HTTP headers and body `client_metadata`, confirm identity/turn metadata;
- Verify window/thread changes of the same turn, compact, resume, fork, subagent;
- Confirm that the bundled 透传 Profile does not lose fields or alter any native tool shape, that web.run 注入 changes only Search endpoint handling, and that listed-provider Tool Mapping changes only tools while preserving every ordinary non-tool request field and raw response. For native Remote V2 compact, capture the inbound and upstream body plus attestation and require byte identity under both 透传 and web.run 注入/another Profile that mutates Responses Lite `additional_tools` on ordinary traffic; verify the compact bypasses that mutation and Provider auth replaces gateway-client auth. Switch from GPT Responses to a different-`comp_hash` third-party Responses model and verify the old GPT request uses the Rosetta prompt, the returned handle maps to seven-day plaintext, and the new Provider receives rehydrated plaintext rather than GPT's encrypted compaction item; confirm the Chat bridge does not leak Rosetta's internal metadata to the upstream;
- Confirm that the actual stream will not end abnormally before `response.completed`, and failed/incomplete can be rendered correctly.

#### B. UI, phase and steering behavior

- The commentary is displayed/folded as a working process in the Codex, and the final answer is not folded by mistake;
- After the commentary is completed, mailbox/steering can interrupt or supplement the current task;
- The order of multiple paragraphs of commentary, reasoning, tool calls and final answer conforms to the actual UI;
- Streaming output is not buffered by errors for long periods of time, and intermediate work is not misinterpreted as the final answer.

#### C. Real tool closed loop

- Before every Gateway-backed live cell, copy Gateway configuration and
  credentials only from `~/.config/codex-rosetta-gateway` and ChatGPT OAuth
  only from `/Users/ibobby/.codex-multi-2/auth.json` into the Git-ignored run
  root. Retain the local-mode `codex_rosetta`/`OpenAI` Provider bearer, verify
  ChatGPT login status, emit only credential-free `runtime-auth.json` evidence,
  and prove model requests reach the isolated Gateway. Treat OAuth-only,
  bearer-only, secret-bearing, or bypassed-Gateway cells as invalid runner
  configurations; never stage copied credentials or allow them into Git
  history;
- Read the real file and use native `apply_patch` to complete the modification;
- Let a patch fail, then confirm that the model can read the error, correct the patch and continue the round;
- When function tool and custom tool coexist, model selection and Codex execution are correct;
- Run `tests/agent_workspace/builtin_tools/01` through `06` with provider display name `OpenAI` and a model catalog exactly equivalent to `gpt-5.6-sol`; verify Code Mode `wait`, two Plan updates, model-hidden `apply_patch` with localized `Edit`/`Write` execution, `view_image`, the grouped Goal lifecycle, and real upstream visual recognition with a vision-capable model;
- Run `tests/live_agent/local_skills/01` through ordinary `codex exec` and verify filesystem catalog metadata plus explicit Skill-body injection without `skills.list`/`skills.read`; separately run `tests/live_agent/orchestrator_skills/01` through app-server with no local execution environment and a provisioned orchestrator provider, then verify exact `skills.list → skills.read` opaque-handle reuse;
- After `view_image` transport and deterministic visual recognition pass, run `tests/live_agent/image_generation/01` with a vision-capable model; seed the isolated Codex home from the authorized ChatGPT OAuth source while retaining the local-mode Gateway bearer Provider, verify that the current Codex auth path exposes `image_gen.imagegen`, prove both model and Images requests reach the isolated Gateway, confirm the endpoint saves an artifact, open that exact path through projected `view_image`, and have the outer evaluator confirm dog, grass/lawn, and running. If the tool is absent from the Codex source request, classify it as an exposure/auth-path failure before evaluating model capability;
- Test `request_user_input` through an app-server JSON-RPC client that answers `ToolRequestUserInput`; `codex exec` explicitly rejects this request and cannot provide valid real-agent coverage;
- Run `tests/live_agent/deferred_tool_search/01` through `07`, each in a fresh
  isolated Codex home. Require all three explicit controls to pass before
  interpreting the four natural-language discovery tasks. Verify the ordered
  three-candidate skill/plugin metadata, explicit skill and plugin guidance,
  implicit selected-skill reads, the code-mode `exec` discovery contract,
  runtime `ALL_TOOLS` catalogs, plugin call provenance, actual archive-tool
  calls, and consumed fixed results in rollout plus source/target Gateway Logs.
  Installation/list output alone does not pass. Run all Terra cells before
  starting the DeepSeek matrix;
- On a Responses-to-Chat profile route with deferred nested tool guidance,
  verify the target request still declares `exec(input: string)` and a Chat
  call round-trips to a Codex `custom_tool_call` with raw JavaScript. Verify
  every successfully emitted ordinary Function is absent from the retained
  `exec` description while deferred guidance and unknown sections remain byte
  identical. Repeat with duplicate known headings, malformed known schemas,
  direct-name conflicts, and a wholly unparsed description; each must retain
  the raw capability without emitting ambiguous projection metadata. Without
  deferred or unresolved content, verify the fully consumed parent is omitted;
- `multi_agent_v1` and `multi_agent_v2/collaboration` provided by the version can spawn, communicate, wait and return results, and parent/window/thread does not cross talk;
- For a new third-party alias, test collaboration v2 before retaining legacy `multi_agent_v1`; use v1 only when the newer lifecycle is demonstrably unreliable for that model;
- When model catalog enables code mode, actually verify `exec/wait` and nested tool continuation; especially check that the payload received by `exec` is custom/raw-source, not function/JSON payload, and confirm that third-party models will recover with visible tool errors when they misuse the freeform tool and will not be fatal;
- With Tavily, Self-hosted (Google), Self-hosted (Bing RSS), and Self-hosted (Bing Browser) tested separately, the model can initiate a search, read normalized results, and proceed to generate the final answer without a Codex response-shape difference.
- For a new third-party alias, test standalone `web.run` search/open before retaining legacy hosted `web_search`; verify the actual surface in Gateway Logs. Also run `network_search/02` to confirm the current explicit Not Implemented contract for `find` and `click` until those operations are implemented.

#### D. Reasoning, History and Recovery

- `reasoning_content` of `deepseek-v4-flash` remains renewable before and after the tool and in subsequent turns;
- reasoning summary/encrypted state does not lead to invalid requests or repeated thinking content;
- Run `tests/live_agent/context_compaction` as a protocol-only four-cell matrix,
  retaining the configured GPT route without requiring a specific upstream
  provider. Any configured provider is valid when the GPT model responds.
  Separately run
  `tests/live_agent/context_compaction_summary_quality` for GPT and DeepSeek;
  require byte-identical task/scenario/resume-query input, exactly one command
  plus one installed compaction, and a same-thread resume with no additional
  command or compaction before scoring deterministic fact retention;
- Run `tests/live_agent/context_compaction/run_live.py --model gpt-5.6-terra`
  for a low-cost native manual-compact smoke cell. It must use an isolated
  Codex Home seeded from the authorized ChatGPT OAuth source, answer the
  app-server `attestation/generate` request through the installed Desktop
  DeviceCheck module without logging the token, and require exact wire
  passthrough plus one installed compaction and a successful follow-up turn.
  If the selected GPT model is not configured or its provider is unreachable,
  stop and request a user decision;
- No orphan tool output, repeated tool calls or history cache confusion after compact/resume;
- Long conversations can still continue to complete file tool tasks after reaching the compact threshold;
- Historical tool mapping and final behavior remain consistent after closing and restoring the Codex session.

#### E. Real transport and failure behavior

- HTTP SSE is the current basic must-test path;
- WebSocket, Responses Lite, and remote compact can only be declared supported after being explicitly enabled;
- When provider-name identity, auth classification, request compression,
  internal item metadata, sequential reasoning-summary delivery, or compact
  fallback changes, rerun `tests/integration/gpt_relay/` C0-C5 against one
  representative real relay/model. Record `OpenAI` and non-OpenAI results
  separately; synthetic backend-auth cells are wire-compatibility evidence,
  not native relay authentication support;
- If these capabilities are not implemented, it must be physically confirmed that Codex will fall back to HTTP/SSE safely;
- Actually create an upstream current limit, authentication failure or interruption, and confirm that Codex can display understandable errors and will not enter infinite retry/repeat tool execution.

The actual test can be repeatedly executed by agentabi or scripts, but the passing conditions must come from the real Codex + the results and traces of the real upstream, and cannot be replaced by local fake upstream.

## 5. Complete recording and version upgrade

Update after completion:

- Baseline in `docs/dev/version-compatibility/README.md`;
- Implementation and limitations in `docs/dev/version-compatibility/compatibility-points.md`;
- Affected test paths and results;
- Incomplete live tests, credential restrictions, or explicitly unsupported capabilities.

Listed separately when recording:

- Implemented and passed automated tests;
- Projects that can be automated but have not yet been implemented;
- Real-world testing done using `deepseek-v4-flash` or other real models;
- Actual testing that was not performed due to capabilities not being enabled, missing credentials, or environmental constraints.

After confirming that there are no unresolved items in the itemized report:

1. Update `docs/dev/version-compatibility/codex-source-contract.json` to the new reviewed commit;
2. Update the old/new version, commit, test results and item-by-item classification report location of `README.md`;
3. Refresh `src/codex_rosetta/gateway/admin/tool_catalog.json` and its CLI/source binding after reviewing the target tool set;
4. Upgrade the package version of `src/codex_rosetta/__init__.py` to the target Codex **release version**;
5. Run `make check-codex-compat`, `make lint`, and `make test` again.

If the Codex source code manifest is still `0.0.0-dev`, the package version cannot be generated based on this. You must use the target Codex release/CLI version corresponding to the source code checkout as the package version, and continue to use the complete commit to identify the source code snapshot; when the mapping cannot be confirmed, the upgrade cannot be marked as complete.

Only after source code review, applicable automated testing, full repository access, and Codex testing that must actually be performed can a new version be marked as verified for compatibility.
