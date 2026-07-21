# Rosetta Codex Compatibility Source Map

This document is the code-derived ownership index for Codex compatibility work.
It exists so that a routine Codex release review can start from documented
Rosetta owners instead of rediscovering the repository on every release.

## Authority and interpretation

Use the following order when evidence disagrees:

1. Current Rosetta runtime code and deterministic tests are the implementation
   facts.
2. [`compatibility-points.md`](compatibility-points.md) defines the intended
   Codex contract and the stable `CP-*` ledger.
3. [`upgrade-checklist.md`](upgrade-checklist.md) defines how to review a new
   Codex release.
4. Upgrade reports record version-specific evidence; files under `evidence/`
   are historical supporting material only.

If code and documentation disagree, do not reinterpret the code to make the
ledger look complete. Record an implementation or documentation gap, update
the ledger in the same task, and adapt runtime code only when that task is in
scope. This map is an index, not a claim that every listed surface is supported.

## Review modes

The developer selects the review mode. A version number alone does not make
that decision.

- **Routine release review**: use this source map, the Codex source anchors in
  the upgrade checklist, and every `CP-*` row. This is the default workflow for
  a release the developer judges to be a small, bounded update.
- **Full inventory review**: repeat the code-to-document reverse mapping,
  scattered-document scan, source-anchor review, and range validation that
  produced this map. Use it whenever the developer requests it or decides that
  the existing inventory is no longer a trustworthy boundary.

A routine review must stop and ask for a review-mode decision when a Codex diff
cannot be mapped to an existing `CP-*` point, a new Rosetta owner is outside
this map, or an extractor result conflicts with source semantics. It must not
silently expand into an unrecorded partial audit.

## Rosetta ownership map

Paths are relative to the repository root. Within one table cell, an
unprefixed basename or subpath inherits the directory of the preceding fully
qualified path (for example, `auth.py` after
`src/codex_rosetta/gateway/app.py` means
`src/codex_rosetta/gateway/auth.py`). A directory entry means all files that
participate in the named behavior, not every generic behavior in that directory.

| Owner group | Runtime and data owners | Deterministic evidence | Compatibility points |
| --- | --- | --- | --- |
| Request ingress, authentication, and identity | `src/codex_rosetta/gateway/app.py`, `auth.py`, `headers.py`, `inbound_content_encoding.py`, `state_scope.py`, `config.py` | `tests/gateway/test_app_headers.py`, `test_auth.py`, `test_inbound_content_encoding.py`, `test_inbound_request_limits.py`, `test_request_state_lifecycle.py` | `CP-01`, `CP-04`, `CP-14`, `CP-21` |
| Routing, Provider identity, and HTTP/SSE transport | `src/codex_rosetta/routing.py`, `src/codex_rosetta/gateway/config.py`, `src/codex_rosetta/gateway/proxy.py`, `providers.py`, `transport/provider_info.py`, `transport/credential_redaction.py`, `transport/http/`, `transport/sse_format.py`, `src/codex_rosetta/observability/redaction.py` | `tests/gateway/test_downstream_routes.py`, `test_responses_passthrough.py`, `test_http_transport_limits.py`, `test_provider_return_redaction.py`, `test_transport_credential_redaction.py`, `test_providers.py`, `test_admin_config_routes.py`, `test_stream_telemetry_lifecycle.py`, `tests/observability/test_redaction.py`, `tests/integration/gpt_relay/` | `CP-01`, `CP-02`, `CP-03`, `CP-17`, `CP-21`, `CP-22` |
| Direct Responses and compaction | `src/codex_rosetta/gateway/codex_compaction.py`, `codex_compact_prompt.md`, `codex_compact_summary_prefix.md`, `headers.py`, `inbound_content_encoding.py`, `proxy.py`, `tool_profiles.py`, `transport/http/` | `tests/gateway/test_codex_compaction.py`, `test_responses_passthrough.py`, `test_tool_profiles.py`, `test_inbound_content_encoding.py`, `test_app_headers.py`, `test_http_transport_limits.py`, `tests/live_agent/context_compaction/`, `tests/live_agent/context_compaction_summary_quality/` | `CP-02`, `CP-04`, `CP-10`, `CP-20`, `CP-21` |
| Responses bridge and public wire types | `src/codex_rosetta/converters/openai_responses/`, `src/codex_rosetta/types/openai/responses/`, `src/codex_rosetta/pipeline.py`, `src/codex_rosetta/gateway/proxy.py` | `tests/converters/openai_responses/`, `tests/test_types/openai_responses/`, `tests/test_pipeline.py` | `CP-05`, `CP-06`, `CP-08`, `CP-11`, `CP-17`, `CP-18`, `CP-19`, `CP-20` |
| Model capability enforcement | `src/codex_rosetta/capabilities.py`, `src/codex_rosetta/pipeline.py`, `src/codex_rosetta/shims/provider_shim.py`, reasoning transforms under `src/codex_rosetta/shims/providers/`, `src/codex_rosetta/converters/base/helpers/image_limit.py` | `tests/test_pipeline.py`, `tests/test_shims.py`, `tests/test_provider_reasoning_transforms.py` | `CP-01`, `CP-07`, `CP-19`, `CP-22` |
| Chat bridge and reasoning mapping | `src/codex_rosetta/converters/openai_chat/`, `src/codex_rosetta/converters/base/helpers/reasoning.py`, `src/codex_rosetta/reasoning_mapping.py`, `src/codex_rosetta/pipeline.py`, provider reasoning transforms under `src/codex_rosetta/shims/providers/` | `tests/converters/openai_chat/`, `tests/converters/test_reasoning_helpers.py`, `tests/test_reasoning_mapping.py`, `tests/test_provider_reasoning_transforms.py`, `tests/test_pipeline.py` | `CP-05`, `CP-08`, `CP-12`, `CP-17`, `CP-19`, `CP-20` |
| Tool Profiles, Code Mode, localization, and discovery | `src/codex_rosetta/gateway/tool_profiles.py`, `code_mode_projection.py`, `tool_adaptation.py`, `codex_auxiliary.py`, `web_run_capabilities.py`, `admin/tool_catalog.py`, `admin/tool_catalog.json` | `tests/gateway/test_tool_profiles.py`, `test_code_mode_projection.py`, `test_tool_adaptation.py`, `test_codex_auxiliary.py`, `test_admin_tools_catalog.py`, `tests/live_agent/builtin_tools/`, `tests/live_agent/command_execution/`, `tests/live_agent/deferred_tool_search/`, `tests/live_agent/namespace_tools/`, `tests/live_agent/subagent_tools/` | `CP-03`, `CP-08`, `CP-09`, `CP-11`, `CP-12`, `CP-22`, `CP-23` |
| Tool-history persistence and replay | `src/codex_rosetta/gateway/proxy.py`, `tool_adaptation.py`, `state_scope.py`, `src/codex_rosetta/observability/persistence.py`, `tool_mapping_crypto.py` | `tests/gateway/test_persistence_sqlite.py`, `test_tool_adaptation.py`, `test_request_state_lifecycle.py` | `CP-04`, `CP-10`, `CP-20` |
| Search, page opening, browser sidecar, and Images | `src/codex_rosetta/gateway/codex_search.py`, `codex_search_references.py`, `codex_page.py`, `codex_images.py`, `web_search.py`, `web_run_capabilities.py`, `web_run_health.py`, `web_run_sidecar.py`, `web_run_supervisor.py`, `resources/web_run/`, `image_workers.py`, `codex_auxiliary.py`, `transport/credential_redaction.py`, `admin/routes/config.py`, `admin/routes/_shared.py` | `tests/gateway/test_codex_search.py`, `test_codex_page.py`, `test_codex_auxiliary.py`, `test_web_search_bridge.py`, `test_web_run_health.py`, `test_web_run_sidecar.py`, `test_web_run_supervisor.py`, `test_web_run_google_search.py`, `test_web_run_bing_search.py`, `test_provider_return_redaction.py`, `test_transport_credential_redaction.py`, `test_downstream_routes.py`, `test_admin_config_routes.py`, `tests/live_agent/network_search/`, `tests/live_agent/browser_use/`, `tests/live_agent/image_generation/` | `CP-03`, `CP-09`, `CP-15`, `CP-16`, `CP-22` |
| Model catalog, task models, and local-mode Provider | `src/codex_rosetta/gateway/codex_models.json`, `codex_models_version.md`, `codex_model_presets.json`, `model_presets.py`, `local_mode.py`, `cli.py`, `config.py`, `providers.py`, `admin/routes/config.py` | `tests/gateway/test_model_presets.py`, `test_local_mode.py`, `test_cli_local_mode.py`, `test_config.py`, `test_admin_config_routes.py` | `CP-07`, `CP-14`, `CP-21`, `CP-22`, `CP-23` |
| Stream lifecycle, phase, and trace evidence | `src/codex_rosetta/gateway/proxy.py`, `stream_phase_buffer.py`, `stream_trace.py`, `transport/http/`, stream converters under `src/codex_rosetta/converters/` | `tests/gateway/test_stream_phase_buffer.py`, `test_stream_trace.py`, `test_stream_telemetry_lifecycle.py`, `test_chat_stream_eof_finalize.py`, converter stream tests | `CP-17`, `CP-18`, `CP-19` |
| Skill delivery surfaces | Rosetta has no runtime converter for filesystem or orchestrator Skills. The maintained facts are the isolated fixtures under `tests/live_agent/local_skills/` and `tests/live_agent/orchestrator_skills/`, plus the shared runtime contract | `tests/live_agent/test_live_agent_configuration_contract.py` and the two suite evaluators | `CP-13`, `CP-14` |
| Live-agent runner contract | `tests/live_agent/README.md`, `runtime-contract.json`, `test_live_agent_configuration_contract.py`, `.agents/skills/rosetta-codex-readme-test/SKILL.md`, `docs/dev/agent-tool-testing.md` | Deterministic fixture-contract tests plus credential-free runtime evidence emitted by each executed suite | `CP-03`, `CP-08`, `CP-09`, `CP-11`–`CP-17`, `CP-19`, `CP-20`, `CP-22`, `CP-23` |
| Codex source contract extractor | `scripts/check_codex_compatibility.py`, `docs/dev/version-compatibility/codex-source-contract.json`, `Makefile` targets `check-codex-compat` and `update-codex-compat-baseline` | `tests/test_codex_source_contract.py` | All points as partial evidence only; never substitutes for per-point classification |

## Deliberate non-owners

The following files do not become compatibility points merely because Codex
uses the gateway:

- generic Admin HTML, localization, metrics, restart notices, and browser-session
  handling that remains confined to `/admin`; `/v1` bearer authentication stays
  in the request/auth owner row above;
- generic provider conversion code that has no Codex-specific input, history,
  tool, phase, or identity behavior;
- diagnostic and convenience scripts such as
  `scripts/analyze_codex_jsonl_errors.py`, `scripts/rosetta-test-codex.sh`, and
  provider-SDK schema extractors. They can observe or launch a run but do not
  define a maintained Codex wire/runtime contract;
- vendored code under `src/codex_rosetta/_vendor/`;
- deployment files whose only effect is packaging. Sidecar deployment remains
  in scope when readiness changes the model-visible `web.run` declaration.

When one of these starts changing a Codex-visible contract, move it into the
ownership map and add or update the corresponding `CP-*` point in the same
task.

## Full inventory review procedure

When the developer selects a full inventory review:

1. Use CodeGraph before text search to trace every Codex-facing owner and its
   callers/tests.
2. Enumerate files added or changed since the last full review, then scan the
   current tree for Codex headers, Responses items/events, tool declarations,
   model-catalog fields, compaction, reasoning, phase, and live-agent surfaces.
3. Map every runtime owner and client-only fixture to one or more stable
   `CP-*` points. Add a new point when no existing contract owns the behavior;
   never hide it under a merely adjacent point.
4. Verify that the compatibility overview and test matrix contain every
   registered canonical name exactly once and that every point defines
   automation plus a real-client scenario.
5. Scan repository documentation outside this directory, including `AGENTS.md`,
   user docs, `tests/live_agent` READMEs, and `.agent-work/debug`. Move
   version-specific research, procedures, and evidence here; keep user-facing
   field references and general test guides outside only when they link back to
   the authoritative workflow and do not own a second baseline.
6. Validate the resulting ledger against a bounded Codex release range and
   record both covered changes and out-of-scope client-only changes in a report.
