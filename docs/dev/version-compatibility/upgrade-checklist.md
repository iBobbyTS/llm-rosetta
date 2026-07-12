# Codex Upgrade Review and Testing Checklist

Execute this checklist whenever you update the Codex CLI, sync `../openai-codex-src`, or change Codex-facing gateway/converter behavior. List unfinished items explicitly as limitations in the compatibility statement.

## Authoritative execution order

The upgrade must be completed in the following order, and the latter step cannot replace the previous one:

1. Record the pre-upgrade Codex CLI version, Codex source commit, and Codex-Rosetta version and commit;
2. Confirm that `../openai-codex-src` has no uncommitted tracked modifications, then fetch and fast-forward-only pull to the latest remote version;
3. Record the target Codex release and new source commit, then run the contract diff;
4. Classify every item in `compatibility-points.md` as **high-confidence unchanged**, **possibly unchanged**, or **changed**;
5. Define repair plans for changed items and manual-review/live-test plans for possibly unchanged items;
6. Review and refresh `src/codex_rosetta/gateway/admin/tool_catalog.json` against the target tool specifications and bundled extensions, including its CLI/source metadata binding;
7. Complete repairs and run all automated checks, including compatibility-specific tests, lint, and the full non-integration test suite;
8. Run real API tests for every possibly unchanged or changed compatibility point. Use `gpt-5.6-terra` to observe native Codex/GPT request shapes and low-cost `deepseek-v4-flash` by default for third-party conversion debugging; record substitutions and reasons;
9. After every gate passes, update the contract baseline, upgrade report, documentation, and package version, and record the exact source commit.

The output of `make check-codex-compat` is the contract group classification, which is only one of the evidences in step 4; it cannot replace the compatibility point classification one by one, nor can it cover the real client behavior.

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

Write the old CLI version, Codex source code complete commit, Codex-Rosetta version/commit and date into the upgrade report. Confirm that the source code checkout is not tracked and then update again:

```bash
test -z "$(git -C ../openai-codex-src status --porcelain --untracked-files=no)"
git -C ../openai-codex-src fetch origin
git -C ../openai-codex-src pull --ff-only
git -C ../openai-codex-src rev-parse HEAD
git -C ../openai-codex-src log -1 --date=iso-strict --format='%H%n%ad%n%s'
```

If the tracked checkout is not clean or the pull cannot be fast-forwarded, stop the upgrade and process the source code repository status first. Do not reset, stash, or overwrite existing work. Do not use `0.0.0`/`0.0.0-dev` in the Codex source code manifest instead of commit, and do not regard the local `origin/main` that has not executed fetch as the latest remote state.

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
codex-rs/core/src/tools/spec_plan.rs
codex-rs/core/src/tools/handlers/apply_patch_spec.rs
codex-rs/core/src/tools/handlers/tool_search.rs
codex-rs/protocol/src/models.rs
codex-rs/protocol/src/openai_models.rs
codex-rs/tools/src/tool_spec.rs
codex-rs/models-manager/models.json
```

Key points to confirm:

- Whether the selection/fallback of HTTP `/responses`, SSE and WebSocket has changed;
- Whether standalone Search and Images still use JSON `POST alpha/search`, `POST images/generations`, and `POST images/edits`, including their model field and required headers;
- request body fields added, deleted or changed;
- The source and life cycle of `client_metadata`, `x-codex-*`, session/thread/window/turn;
- `response.output_item.*`, text/tool/reasoning delta and terminal event;
- `MessagePhase` enumeration and fallback of missing phase;
- Tools such as function/custom/namespace/tool_search/web_search wire shape;
- tool type, grammar, call/output item of `apply_patch`;
- reasoning effort, summary, encrypted content and response headers;
- History and window generation after compact, resume, fork, subagent;
- `ModelInfo` new fields, enum/default and unknown model fallback.

## 3. Compare Rosetta ownership boundaries

For each Codex contract diff, clearly fall into one of the following ownership disposition categories:

1. **Direct passthrough has been naturally retained**: supplementary testing proves that unknown fields or original SSE have not been overwritten;
2. **Bridge must be adapted**: modify converter/gateway, and add request, stream and multiple rounds of testing;
3. **Currently not supported**: Record disabling methods, fallback dependencies and test conditions before enabling.

Key review:

```text
src/codex_rosetta/gateway/app.py
src/codex_rosetta/gateway/codex_auxiliary.py
src/codex_rosetta/gateway/headers.py
src/codex_rosetta/gateway/proxy.py
src/codex_rosetta/gateway/stream_phase_buffer.py
src/codex_rosetta/gateway/tool_adaptation.py
src/codex_rosetta/gateway/web_search.py
src/codex_rosetta/gateway/admin/tool_catalog.json
src/codex_rosetta/converters/openai_responses/
src/codex_rosetta/converters/openai_chat/
src/codex_rosetta/converters/base/helpers/tool_orphan_fix.py
src/codex_rosetta/types/openai/responses/
```

### Compatibility point classification and repair plan one by one

After completing the source diff and automated contract report, copy every item from `compatibility-points.md` and fill in these fields. The number of report rows must match the source list. Save reports under `docs/dev/version-compatibility/reports/YYYYMMDD-codex-vX.Y.Z.md`:

| Compatibility points | Classification | Source code/contract evidence | Fix or review plan | Automation results | Real API results |
| --- | --- | --- | --- | --- | --- |
| `<Compatibility point name>` | High confidence no change / probably no change / yes change | `<commit/diff/code location>` | `<no fix, review step, or fix required>` | `<command and result>` | `<model, route, result, or non-trigger reason>` |

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
- Export and compare `ModelInfo` fields, enum values, default values and bundled model configurations;
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

Still to be implemented: field types, serde rename/default/skip strategy, SSE match arm digest, full generic tool schema, tool_search defaults, bundled model capability subset, and model fallback initializer. Therefore, the current baseline only proves that the above extracted collection has no drift, and cannot claim that all projects in Section A have been completed.

#### B. Fixture and unit/component testing

The following behavior can be automatically verified using the fixed Codex request/SSE fixture:

- Responses→Responses Tool Mapping only applies the selected Profile, retains unknown non-tool fields and preserves original response JSON/SSE bytes below the transport safety envelope, while Rosetta mode selects Responses→IR→Responses without changing the wire protocol;
- header allowlist; `x-codex-window-id` extraction; exact/+1 model, window, and request-ID budgets; visible-ASCII/control rejection and missing request-ID generation; rejection before body/log/trace/persistence/state/upstream use; correlation/state-key separation; private no-window scope and terminal cleanup;
- Responses request → IR/adapter → Chat/Anthropic/Google upstream request;
- Verify model-group Tool Profiles on Tool Mapping only, Responses Rosetta, Chat, Anthropic, and Google routes, including Disabled filtering, Modified handling, Namespace pass-through/expansion, Rosetta injection selection, and all bundled defaults; verify Responses pass through preserves native tools and Responses web.run mapping changes only `web.run` endpoint handling;
- Responses Lite `additional_tools`, developer instructions, `reasoning.context=all_turns` and embedded tool filtering/deduplication;
- non-streaming/streaming upstream response → Codex Responses output;
- The order of `response.created`, item added/delta/done, completed/failed/incomplete;
- message `commentary`/`final_answer` is consistent in added, done and completed;
- Text followed by phase inference of function/custom/MCP/shell/computer/tool_search/web_search call;
- custom/freeform `apply_patch` and code-mode `exec` definition, grammar, delta splicing, call/output and fallback command; among them, `exec`’s Chat downgrade return must be restored to `custom_tool_call`, and non-compliant JSON function parameter guessing must not be rewritten into JavaScript;
- native/localized tool history mapping with exact encrypted SQLite payloads,
  authenticated same-process/restart replay, missing/wrong key and tamper
  fail-closed behavior, plaintext/encrypted-v1 migration, row/session/principal/
  global row+byte budgets, replacement accounting, TTL release, transaction
  rollback, abnormal replay bounds, failure results and subsequent-round replay;
- provider continuation metadata principal entry quotas, same-principal global-oldest replacement, no cross-principal eviction, replacement/TTL/clear accounting, and concurrent saturation;
- namespace defer, `tool_search_call/output`, multiple searches, window isolation, atomic per-scope/principal/global retained-state budgets, same-principal global-oldest replacement, no cross-principal eviction, and lifecycle accounting;
- Captured wire fixtures for `multi_agent_v1`, `multi_agent_v2/collaboration`;
- Captured wire fixtures for code mode `exec/wait`, nested call and wait continuation;
- web search multi-round event reconstruction, downgrade paths for disabled/missing keys, and bounded identity-encoded auxiliary HTTP responses;
- Cross-format round trip of reasoning effort/summary/content/encrypted state;
- orphan call/result, residual tool choice/config and history trimming after compact;
- Concurrency windows, cache expiration, normal EOF, abnormal EOF,
  huge peer-declared HTTP chunks, oversized no-newline SSE lines, accumulated
  no-delimiter events, converted/raw/web-search client cancellation, upstream
  4xx/5xx and retry boundaries; verify that below-limit raw Responses SSE is
  byte-identical and that overflow closes the upstream;
- Inbound request-body default, fixed tiers, Admin persistence/hot reload,
  rollback, unlimited mapping, and a real Codex image-history request above the
  former 50 MB ceiling;
- `/v1/models` current universal response, and future separately implemented Codex `ModelInfo` catalog contract;
- Configuration/admin UI saving, defaults and runtime loading of Codex tool-adaptation switches.
- Static tool-catalog contract: unique IDs, valid placement/policy references, required fixed tools, excluded dynamic tools and obsolete hosted `image_generation`, current `image_gen.imagegen` coverage, Built-in Profile defaults, supported states, and exact CLI/source metadata binding.

New fixture/component coverage in this round:

- Streaming item event and completed-only phase fallback of `tool_search_call`/`web_search_call`;
- Two-way isolation of deferred namespace search of two Codex windows;
- Reused `x-request-id` sequential/concurrent isolation, real-window continuity, and request-local normal/error/cancel cleanup;
- Deferred-tool canonical UTF-8 byte/count budgets, atomic overflow, replacement, concurrent writers, and TTL/eviction/clear budget return;
- Deferred-tool and provider-metadata per-principal count quotas, same-principal global-oldest replacement, cross-principal rejection, unique-scope accounting across loaded/deferred maps, and concurrent saturation;
- Encrypted tool-mapping row/session/principal/global row+byte quotas, replacement without double counting, expiry budget release, raw SQLite accounting, encrypted-v1 backfill, bounded replay, concurrent saturation, and transactional write rollback;
- Tavily real-loopback normal JSON, Content-Length/chunked/EOF overflow, compressed-response rejection, timeout and cancellation;
- Tool-mapping TTL validation for environment strings, booleans, non-finite/overflow values, and the inclusive 720-hour boundary;
- Codex source contract extractor tests for Rust comment/string/braces, enum wire rename, commit and contract drift separation, and baseline canonical serialization.

The remaining items are still listed in this section as the automation backlog. Request-handler lifecycle playback now covers concurrent no-window isolation and persistent-window continuity, while complete local gateway conversion playback with concurrent real HTTP clients remains to be implemented.

Existing special test commands:

```bash
CONDA_ENV="${CODEX_ROSETTA_CONDA_ENV:-llm-rosetta}"
conda run -n "$CONDA_ENV" python -m pytest \
  tests/gateway/test_app_headers.py \
  tests/gateway/test_request_state_lifecycle.py \
  tests/gateway/test_http_transport_limits.py \
  tests/gateway/test_responses_passthrough.py \
  tests/gateway/test_stream_phase_buffer.py \
  tests/gateway/test_window_tool_search_store.py \
  tests/gateway/test_tool_adaptation.py \
  tests/gateway/test_codex_page.py \
  tests/gateway/test_codex_search.py \
  tests/gateway/test_codex_auxiliary.py \
  tests/gateway/test_web_search_bridge.py \
  tests/gateway/test_config.py \
  tests/gateway/test_admin_config_routes.py \
  tests/converters/openai_chat/test_message_ops.py \
  tests/converters/openai_chat/test_tool_ops.py \
  tests/converters/openai_responses/test_tool_ops.py \
  tests/converters/openai_responses/test_stream.py \
  tests/converters/test_strip_orphaned_tool_config.py \
  tests/test_codex_source_contract.py \
  tests/test_pipeline.py -q
```

The current development machine inherits the historical environment name `llm-rosetta`; if the environment has been renamed by project, it is overwritten by `CODEX_ROSETTA_CONDA_ENV`. Don't count a test as passing when the environment doesn't exist.

#### C. Local Integration and Quality Gate Control

The following projects can also be fully automated and do not require real models:

- Start the local gateway and play back multiple rounds of fixtures on fake Responses/Chat upstream;
- Send two windows concurrently to verify that phase, tool mapping, and deferred tools are not in the same state;
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

- When you need to observe the Codex/GPT native request, tool, reasoning, transport or stream shape, use `gpt-5.6-terra`, and use Rosetta trace to confirm that the upstream is indeed a GPT route; the alias with the same name forwarded to a third-party provider cannot be used as evidence of the original GPT request;
- When debugging the Responses→Chat bridge, reasoning and multi-round tool calls of third-party models, `deepseek-v4-flash` is used by default because the cost is lower;
- Actual testing of version upgrades should at least include scenarios related to this change in the above two categories;
- If an item is only applicable to same-format Responses, specific hosted tools or model-specific capabilities, add the corresponding upstream;
- If `deepseek-v4-flash` is temporarily unavailable, use a low-cost model with similar capabilities and support tool calls, and record the alternative model in the results, and cannot skip it without explanation;
- Actual tests must record model, provider/route, Codex CLI version, Codex source commit and Codex-Rosetta commit.

#### A. Basic session and real request

- Start the real Codex CLI/Desktop and connect `deepseek-v4-flash` via Rosetta;
- Complete a single round of text and multiple rounds of dialogue, and confirm that there are no repeated, truncated or unended turns;
- Capture real HTTP headers and body `client_metadata`, confirm identity/turn metadata;
- Verify window/thread changes of the same turn, compact, resume, fork, subagent;
- Confirm that Responses Tool Mapping only with the Responses pass through Profile does not lose fields, and that Responses web.run mapping changes only Search endpoint handling; record Responses Rosetta with the intended third-party provider as unverified unless it completes through IR and can be continued; confirm the Chat bridge does not leak Rosetta's internal metadata to the upstream;
- Confirm that the actual stream will not end abnormally before `response.completed`, and failed/incomplete can be rendered correctly.

#### B. UI, phase and steering behavior

- The commentary is displayed/folded as a working process in the Codex, and the final answer is not folded by mistake;
- After the commentary is completed, mailbox/steering can interrupt or supplement the current task;
- The order of multiple paragraphs of commentary, reasoning, tool calls and final answer conforms to the actual UI;
- Streaming output is not buffered by errors for long periods of time, and intermediate work is not misinterpreted as the final answer.

#### C. Real tool closed loop

- Read the real file and use native `apply_patch` to complete the modification;
- Let a patch fail, then confirm that the model can read the error, correct the patch and continue the round;
- When function tool and custom tool coexist, model selection and Codex execution are correct;
- `request_user_input`, Goal/Plan and Desktop/runtime-only tools can be called according to the real schema;
- plugin/MCP namespace finds the tool through `tool_search`, actually calls it and consumes the result;
- `multi_agent_v1` and `multi_agent_v2/collaboration` provided by the version can spawn, communicate, wait and return results, and parent/window/thread does not cross talk;
- When model catalog enables code mode, actually verify `exec/wait` and nested tool continuation; especially check that the payload received by `exec` is custom/raw-source, not function/JSON payload, and confirm that third-party models will recover with visible tool errors when they misuse the freeform tool and will not be fatal;
- When web search is enabled, the model can initiate a search, read the results, and proceed to generate the final answer.

#### D. Reasoning, History and Recovery

- `reasoning_content` of `deepseek-v4-flash` remains renewable before and after the tool and in subsequent turns;
- reasoning summary/encrypted state does not lead to invalid requests or repeated thinking content;
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
