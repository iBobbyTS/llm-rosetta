# Audit System Map

Last reconciled: 2026-07-20
Repository base: `de9c96b8b346b5a338e81ea7fa66ba1a0c590d7b`; third omission-remediation re-audit `20260720-1554` is in the working tree
Profile status: Approved
Map owner: Bobby (project owner)

## 1. System Boundary

- Included workload/products: core Python conversion library, IR/types, provider shims, Codex-facing Gateway/Admin, local-mode and Codex compatibility assets, observability/persistence, Docker/Compose, CI/build/manual release, and repository-local agent/live-test harnesses.
- Supported environments: local process and trusted internal network. No current production deployment.
- Excluded adjacent systems: real Codex/provider services, public internet deployment, external GitHub settings, production operators/data, and provider model quality.
- Primary users: one Admin/operator and Codex as the only supported downstream client.
- Business goals: maintain Codex Responses/tool/stream/compaction semantics while routing to configured preset or operator-selected custom HTTP(S) providers through a hub-and-spoke IR.
- Crown-jewel data/assets/secrets: provider credentials, Gateway API keys, Admin password/session secret, prompt/source/tool payloads, persisted mappings and logs, catalog/compatibility artifacts.
- Privileged/irreversible actions: Admin config/key/provider/model mutation; reload; log/data deletion; local or remote dev deployment commands; manual release publication.
- External systems/processors: Codex source/CLI; configured upstream provider endpoints; optional web-run/Tavily; Python SDKs/cryptography; GitHub Actions and manual GitHub Releases.
- Legal/privacy/contractual constraints: none supplied; future obligations invalidate affected profile/coverage rows.

## 2. Component Inventory

| Component / domain | Purpose | Owner | Criticality | Entry points | Data/state | Dependencies | Runtime/deployment | Evidence/source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Public library + IR | Any-to-any provider conversion through shared IR | Project | High | `codex_rosetta.convert`, converter classes, `ConversionPipeline` | In-memory request/response/stream state | stdlib; optional SDK types | Python package | `src/codex_rosetta/__init__.py`, `pipeline.py`, `converters/`, `types/ir/` |
| Provider shims | URL/protocol-derived provider identity, defaults, transforms | Project | High | shim registry and `GatewayConfig` resolution | In-memory registry/config; URL is option authority; missing or backend-unrecognized `api_type` is inferred in memory with a warning | bundled YAML/Python shims | Package resources | `shims/`, config/Admin tests |
| Gateway HTTP app | Authenticated Codex API, health, model routes, admin registration | Project | Critical | `create_app`, `/v1/responses`, `/v1/models`, `/health*`, `/admin/*` | request-local context; app stores; auth state | vendored HTTP server/client | local process or Docker | `gateway/app.py`, `gateway/auth.py`, admin routes |
| Gateway proxy/conversion | Route, convert, stream, tool/compaction adaptation, upstream forwarding | Project | Critical | `_proxy_handler`, `handle_non_streaming`, `handle_streaming` | stream state, metadata stores, mappings | `pipeline`, transport, persistence | local/LAN upstream calls | `gateway/proxy.py`, `transport/`, gateway tests |
| Provider transport | URL/auth header construction, key rotation, client pool/SSE | Project | High | `ProviderInfo`, `HttpTransport`, SSE formatter | connection pools, key ring | vendored HTTP client | local process/container | `gateway/transport/`, `providers.py` |
| Admin control plane | Single-admin login, config/provider/model/key mutation, observability | Project | Critical | `register_admin_routes`, `AdminRuntimeState` | config files, session secret, task state | HTTP app, persistence | local/LAN only | `gateway/admin/`, auth/session tests |
| Observability/persistence | request/error logs, metrics, bounded mappings, schema-shape validation, and retention | Project | Critical | `PersistenceManager`, request/stream logging | SQLite WAL DB, mapping tables, trace JSONL | sqlite3; optional cryptography for mappings | local filesystem / `/config/data` | `observability/`, persistence tests |
| Codex compatibility/local mode | catalogs, compaction, tool localization, deferred tools, response metadata | Project | Critical | `local_mode`, `codex_compaction`, auxiliary handlers, model resources | catalog JSON, compaction mappings, per-window stores | Codex source contract | local/LAN Gateway | `gateway/codex_*.py`, `docs/dev/version-compatibility/` |
| Optional web/search/image bridges | external search, web-run sidecar, image fetch/generation | Project | Medium | search/image routes and sidecar supervisor | health state, fetched content, tool references; configured Tavily key is removed from returned/error data | Tavily/sidecar/provider endpoints | optional local/LAN | `gateway/web_run*`, `web_search.py`, `codex_images.py` |
| Build/release/deployment | lint/test/build, wheel/Docker/Compose, manual release checks | Project | High | Make targets, CI workflows, release scripts | wheel/dist, Docker layers, tags | GitHub Actions, Python/Docker base images | CI/local Docker/manual GitHub | `Makefile`, `.github/workflows/`, `docker/` |
| Agent/test control plane | deterministic fixtures, live-agent harness, tool/plugin fixtures, audit state | Project + developer | High | `tests/live_agent`, scripts, repo instructions | test artifacts, runtime contracts, audit ledgers | Codex/agentabi when live; local tools | local/CI; live calls gated | `tests/live_agent/`, `AGENTS.md`, `docs/audit/` |

## 3. Interfaces and Contracts

| ID | Producer/provider | Consumer | Contract/API/event/schema | Version/compatibility | Auth/trust boundary | Owner | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| IF-01 | Codex downstream client | Gateway | OpenAI Responses JSON/SSE `/v1/responses` | current reviewed Codex source/catalog; target version can drift | Gateway API key; input/tool/provider output untrusted | Gateway + compatibility owner | `gateway/app.py`, `proxy.py`, compatibility docs |
| IF-02 | Codex downstream client | Gateway | model catalog `/v1/models`, local mode settings | bundled Codex catalog and preset overlays | Gateway API key | Compatibility owner | `codex_models.json`, `local_mode.py`, tests |
| IF-03 | Gateway | upstream provider | OpenAI Chat/Responses, Anthropic Messages, Google GenAI HTTP | exact backend-supported or runtime-inferred `api_type`; URL-authoritative preset or custom endpoint | provider credential egress; arbitrary custom HTTP(S) egress accepted only within local/LAN scope; redirects denied by default and allowed only by explicit provider opt-in; provider response untrusted | Gateway/provider owner | `config.py`, `providers.py`, `transport/`, converters |
| IF-04 | Admin browser/client | Admin routes | login/session, config/key/provider/model/observability APIs | current JSONC/config schema | Admin password/session token; CORS boundary | Gateway owner | `gateway/admin/routes/`, auth/session tests |
| IF-05 | Converter A | IR | request/response/stream typed dictionaries and events | internal current IR; no Rosetta-version migration guarantee | in-process trusted code, provider payloads untrusted at ingress | Core owner | `types/ir/`, converter tests |
| IF-06 | Gateway | SQLite persistence | request logs, error dumps, encrypted tool mappings, compaction mappings | current column/constraint/index shape; incompatible old schemas/files are rejected, not migrated | principal-scoped state; local filesystem | Observability owner | `observability/persistence.py`, schema contract tests |
| IF-07 | Codex source | Rosetta compatibility checks | source contract extraction and compatibility points | exact source commit separate from installed CLI/package | read-only source evidence | Compatibility owner | `scripts/check_codex_compatibility.py`, docs/dev/version-compatibility |
| IF-08 | CI/manual release | wheel/Docker/GitHub Release | package version/tag and current-checkout wheel | `codex_version.rN` release convention | CI token and developer release authority | Release owner | `Makefile`, `check_release_version.py`, workflows |

## 4. Data and State Map

| Store/state/event | Classification | Readers | Writers | Source of truth | Retention/deletion | Backup/restore | Isolation boundary | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| API key/admin/session secrets | Secret | auth/admin/session code | config/env/atomic key-file creation | configured secret source and durable key file | operator-managed; no public retention claim | no recovery guarantee | single Admin; API-key principals | `auth.py`, `admin_session.py`, config |
| Request log SQLite rows | prompt/metadata potentially sensitive | Admin/observability | app request finalizers/persistence | `gateway.db` request_log | success/error caps; body content controlled by config | no backup/restore guarantee | Admin-wide; label only in log rows | `persistence.py`, retention tests |
| Error dumps/body blobs | sensitive request/response content | Admin/error diagnostics | error dump path | SQLite dump tables/blob hash | fixed dump cap and body cap; no external restore | no guarantee | Admin-wide; token redaction | `error_dump.py`, persistence |
| Tool-call mappings | executable tool history | proxy/localization | proxy/persistence | encrypted SQLite mapping rows | TTL + row/byte caps per session/principal/global | key loss intentionally fail-closed | `principal_id/provider/model/session/tool_call_id` | `tool_mapping_crypto.py`, persistence tests |
| Codex compaction mappings | prompt/summary/source-sensitive | compaction rehydration | compaction response path | SQLite compaction table | rolling 7-day TTL plus single-row, per-principal and global row/byte quotas | no guarantee | `principal_id` + token hash | `codex_compaction.py`, persistence |
| Stream trace JSONL | prompt/tool/response diagnostics | operator | stream logger | configured trace path | max string truncation; no global file cap observed | no guarantee | local filesystem permissions | `stream_trace.py`, tests |
| Provider/model route config | provider credentials and routing | app/admin | JSONC atomic writer/admin | config.jsonc + env substitutions | operator-managed; no migration guarantee | no guarantee | single Admin/operator | `config.py`, admin config routes |
| Model/catalog resources | public Codex metadata | local mode/model endpoint | repository/package release | bundled JSON resources | versioned with package | source control/manual release | package-wide | `codex_models.json`, `codex_model_presets.json` |
| Agent/audit ledgers | control/evidence metadata | agents/maintainers | audit workflow | current repo + ledger head | run evidence durable; current run deleted only after report | no external backup | repository-local | `docs/audit/` |

## 5. Trust and Privilege Map

| Boundary/action | Actor/identity | Input/source | Required authorization | Secrets/data exposed | Effective permissions | Approval gate | Audit evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/v1/*` request | Codex client/API-key principal | request body, headers, tool content | configured Gateway API key | response/tool state for same principal | route/conversion and provider calls | none beyond API key | `create_auth_hook`, auth tests |
| Admin API mutation | single Admin | browser JSON/config | Admin password → session/header token | all config/log/metrics/admin data | provider/key/model/config mutation, reload, deletion | Admin credential | `check_admin_auth`, admin route tests |
| Internal Admin test request | Gateway internal token | Admin test task | internal token generated per app | provider route/config as task requires | local request path with reserved principal | Admin initiated | `auth.py`, admin testing routes |
| Provider egress | Gateway process | converted request/model/config | provider API key in configured provider | prompt/tool payload and provider credential | request to configured preset or arbitrary custom HTTP(S) base URL/proxy; redirects fail closed by default, but explicit `allow_redirects` permits provider and model-discovery follow-ups | operator configuration; custom egress and opt-in redirect expansion accepted for local/LAN only | `GatewayConfig`, `ProviderInfo`, redirect regression tests, profile |
| Persistence read/write | Gateway process | logs/mappings/compaction data | principal scope for mappings; process filesystem access | prompt/tool/history and token metadata | local DB read/write, cleanup | none | `PersistenceManager`, scopes |
| Live API test | developer-approved agent/harness | live test prompt/config | explicit developer approval per test | real provider/Codex credentials and transcripts | external API calls and local artifacts | required by profile; prohibited in audit | `tests/live_agent`, profile |
| Release publication | project owner | built wheel/tag/release notes | manual GitHub Release | package artifact and source | publish/release | human only | Makefile/release docs |
| Agent audit execution | Codex/maintainer | repo files, test output, tool output | current task authorization | local repo/audit evidence; no real API secrets | task-authorized repair, audit artifacts, and local checks | explicit user authorization for repair/commit; no live API | current run report, `../audit-profile.md` |

## 6. Deployment and Operations Map

| Service/job/component | Environment | Build artifact | Deploy path | Config/flags | Dependencies | SLO/critical signal | Rollback/recovery | Owner |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Gateway process | local | editable install or wheel | CLI | JSONC/env, host/port, provider/model groups | provider endpoints | no SLO; health/readiness and request/stream errors | process restart/manual config restore only | Project/operator |
| Gateway Compose | local/LAN | current-checkout wheel in Docker build | `docker-compose` | `/config` volume, env, optional sidecar | Docker/Alpine/PyPI dependency resolution | health endpoint; no HA | manual rebuild/restart; no restore guarantee | Project/operator |
| Web-run sidecar | optional local/LAN | local sidecar image | Compose profile/supervisor | token/url, seccomp, read-only/tmpfs limits | browser/search dependencies | health probe; no SLO | supervisor restart/manual disable | Project/operator |
| CI lint/test/build | GitHub Actions | source checkout and wheel | workflows on push/PR | Python 3.14.6, optional deps | mutable action tags, package indexes | job status | rerun/revert manually | Project |
| SDK compatibility monitor | scheduled/manual CI | source + latest optional SDKs | weekly/manual workflow | issue creation permission | GitHub API, latest SDK versions | issue on test failure | manual triage | Project |
| Manual release | GitHub UI | local/current-checkout sdist/wheel or release artifact | owner manually publishes | tag/version checks | GitHub, Python build tooling | release gate checks | manual revert/withdraw; no signing guarantee | Project owner |

## 7. Agent and Tooling Map

| Agent/harness/tool | Model/version | Purpose | Identity/tokens | Filesystem/network/tools | Untrusted inputs | Durable state/memory | Approval boundary | Owner |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Codex agent | version varies by run | implementation/audit/test | local Codex auth outside repo | repo filesystem, shell, configured tools | repo/docs/issues/tool output | `.agent-work`, memory, git | no commit/deploy/release; no live API in audit | Project owner |
| CodeGraph | local index | symbol/call-path inspection | local process | repository/index only | source code | `.codegraph` index | read-only analysis | Project |
| Deterministic pytest harness | pinned dev tooling in pyproject | unit/integration-with-fakes | no real provider credentials required | local filesystem/network mocks | fixtures and generated payloads | pytest artifacts | normal local test execution | Project |
| live/integration/agent launchers | external model/CLI varies | real provider and agent compatibility/eval | real credentials when developer-approved | network, provider, Codex CLI, agentabi, relay/browser/MCP fixtures | prompts, web/plugin/tool content | run roots, transcripts, outputs | shared exact-marker gate before credentials/processes; excluded from audit | Developer/project owner |
| GitHub Actions | workflow-pinned major tags currently | CI and SDK alerting | GitHub token; scheduled job has issue write | checkout, build, tests, GitHub API | PR/source/dependencies | workflow artifacts/issues | GitHub workflow permissions | Project |
| Docker/Compose | Python 3.14.6 Alpine base tag | local/LAN package deployment | env/config secrets | container filesystem/network, optional sidecar | provider/config/tool content | `/config` volume | operator starts explicitly | Operator |

## 8. Scenario Portfolio

| Scenario ID | Attribute | Workflow/assets | Components/contracts | Expected response | Evidence target | Criticality | Owner |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SCN-01 | Security/correctness | Invalid/missing API key to `/v1/responses` | auth hook, route, error response | fail closed with no upstream call or state identity | auth tests + local request probe | Critical | Gateway |
| SCN-02 | Security/privacy | Two configured API keys use same window/tool IDs | principal context, state scope, SQLite mappings/compaction | no cross-key read/write/replay | isolation tests and SQL scope trace | Critical | Gateway/observability |
| SCN-03 | Correctness/reliability | Codex Responses request routed to preset upstream | app → proxy → route/shim → converter → transport | target body and returned response preserve required fields | deterministic converter/fake-upstream tests | Critical | Compatibility/core |
| SCN-04 | Correctness/reliability | Provider SSE stream has deltas, tool calls, usage, EOF/cancel | stream processor, phase buffer, SSE formatter | ordered downstream events, terminal state, resource cleanup | stream/lifecycle tests | Critical | Gateway/core |
| SCN-05 | Correctness/privacy | Deferred tool localization and response replay | tool adaptation, encrypted mapping, state scope | exact tool name/arguments for owning principal only | mapping tests and local replay | Critical | Gateway/compatibility |
| SCN-06 | Correctness/reliability | Codex compaction trigger/resume/fork | compaction state machine, summary mapping | native or Rosetta mode selected; token rehydrates only owned live mapping | compaction tests; no live summary call in audit | Critical | Compatibility |
| SCN-07 | Reliability/security | Repeated large diagnostic/compaction/tool payloads | request logs, trace, mapping/compaction stores | configured caps/TTL/cleanup prevent uncontrolled state growth | size/capacity tests and source evidence | High | Observability |
| SCN-08 | Security/operability | Admin login, reload, key/provider/model mutation | Admin auth/session/CORS/config atomic write | only single Admin mutates; invalid config fails without partial live state | admin/config tests | Critical | Gateway |
| SCN-09 | Security/compatibility | Provider URL edited, `api_type` omitted/unrecognized, unmatched custom path configured, or redirect policy changed | registry/shim/config/admin UI/transport | only backend-supported `api_type` is explicit; other values infer without write-back; redirect default is denied and explicit provider opt-in is isolated from other HTTP paths | config/admin/transport tests + docs comparison | High | Gateway/product |
| SCN-10 | Supply chain/operability | Build wheel/Docker/manual release | pyproject, Makefile, Docker, CI, version checks | artifact comes from current source and release is manually gated | lint/test/build/contract/release checks | High | Release |
| SCN-11 | Agent safety | Agent or test invokes real API from repository | live harness/scripts/instructions | audit never calls real API; development live call requires explicit approval | harness/config/source review | High | Project owner |

## 9. Dependency and Invalidation Edges

| From node | To coverage/scenario | Why dependent | Change types that invalidate | Boundary/stop condition |
| --- | --- | --- | --- | --- |
| `gateway/auth.py` | SCN-01/02/08, mapping coverage | identity gates every stateful route | auth, headers, admin session, config credential changes | stop outside auth and principal scope |
| `GatewayConfig`/provider resolution/transport | SCN-03/09/10 | route, protocol inference, and credential egress depend on parsed config and redirect policy | config schema, provider/shim registry, admin writes, HTTP client behavior | stop at provider URL/auth boundary |
| `proxy.py` + `pipeline.py` | SCN-03/04/05/06 | conversion/stream/tool state path | converter, route, stream, tool adaptation, Codex contract changes | stop at unchanged provider-independent IR tests |
| `observability/persistence.py` + crypto | SCN-02/05/06/07 | durable state ownership, cleanup, replay | schema, key, retention, mapping/compaction changes | stop at unaffected metrics/request-log paths only when proven |
| Codex compatibility resources/docs | SCN-03/04/06/11 | client contract and catalog semantics | Codex source, model catalog, local-mode, compact/tool changes | stop at provider-agnostic converter surfaces |
| CI/Docker/release | SCN-10/11 | artifact and agent control-plane evidence | workflow, base image, dependency, Makefile, release script changes | stop at runtime behavior when artifact path unaffected |
| `tests/live_agent` and repo instructions | SCN-11 and verification evidence | controls whether real calls/tools occur | harness, credentials, permissions, instructions, model/runtime changes | no live call in audit; mark external evidence Unknown |

## 10. Known Map Gaps

| Gap | Risk | Required evidence/owner | Status |
| --- | --- | --- | --- |
| No deployed environment or production telemetry | runtime reliability, recovery, actual network exposure unknown | future operator deployment evidence | Open / Unknown |
| Real Codex/provider behavior unavailable by audit policy | stochastic/tool/stream/model behavior may diverge from fixtures | developer-approved live run outside audit | Open / Unknown |
| External GitHub settings, branch protections, action pinning state not locally observable | CI/release privilege assumptions may be incomplete | owner or GitHub review | Open / Unknown |
| No formal legal/privacy/retention contract | data lifecycle requirements may change | owner decision if deployment scope changes | Open / Unknown |
| Direct arbitrary custom HTTP(S) provider egress and explicit per-provider redirect opt-in are accepted only for local/LAN use | SSRF, internal-target access, and provider-key disclosure remain possible for the configured URL/proxy and any operator-enabled redirect target | project owner; revisit before any public deployment/security claim | Risk Accepted, default-deny and auxiliary isolation verified in `20260720-1554` |
| Rosetta-version migration compatibility is intentionally unsupported | old state/config is rejected rather than migrated; protocol compatibility remains explicit | owner-approved remediation wave and regression tests | Fresh (deterministic); reopen on new migration path |
