# Persistent Audit Findings and Debt

Last updated: 2026-07-21
Repository head: `04efc74e0425c42bb906581b61c0c0be6976841` + current remediation working tree; targeted re-audit `20260721-1232`
Profile: `docs/audit-profile.md` (Approved)

## Conclusion ownership

This section separates the current conclusions by who may authorize the next
step. The baseline recorded `Authorized remediation: No`; the owner later
authorized scoped remediation waves. The targeted re-audit in
`docs/audit/runs/20260721-1232/` verifies that authorized remediation; no real
provider/API call or deployment was authorized.

### Logic/control issues I can repair directly

| ID | Conclusion | Direct repair boundary |
| --- | --- | --- |
| AUD-002 | Compaction replacement persistence has no aggregate row/byte quota | Add bounded, transactional persistence controls and regression tests; exact limits can be proposed within the approved local/LAN risk profile. |
| AUD-001 | Rosetta-version migration and legacy paths conflict with the no-migration boundary | Reject incompatible legacy config/state and remove the active migration/alias paths; current protocol compatibility remains explicit. |
| AUD-003 | Real-call runners lack a fail-closed developer-approval gate | Add an explicit opt-in gate and deterministic tests proving no external-call subprocess/client starts without it. |
| AUD-006 | Live-call approval gate omitted executable examples and a live SSE development script | Gate each real-call entry point before dotenv/credentials and enforce convention-based inventory coverage. |
| AUD-007 | Admin tool-profile default still depends on stripped provider metadata | Derive the runtime profile from `api_type + base_url` using the same URL-authoritative preset rules, without persisting UI options. |
| AUD-008 | Audit coverage ledger contradicts current finding/closure status | Reconcile current coverage, control status, and rotation queue after reopening affected findings. |
| AUD-010 | SQLite index validation omitted uniqueness/origin/partial attributes | Validate the complete required index shape before startup. |
| AUD-012 | Redirect policy was not enforced at every HTTP boundary | Deny provider redirects by default, isolate explicit provider opt-in, and force non-provider auxiliary requests to deny redirects. |
| AUD-014 | Tavily responses or exceptions could reflect the configured API key | Remove the configured key from success, error, and transport-exception data before model/client/diagnostic exposure. |
| AUD-015 | Provider and web-run sidecar return paths can reflect configured credentials | Enforce configured-token redaction at every credential-bearing outbound return boundary while preserving non-secret response/error semantics. |
| AUD-016 | Rotated provider wire keys are absent from the exact-value redaction inventory | Parse provider credentials once and register every actual trimmed wire key, plus the raw configured value where useful, for all runtime redactors. |
| AUD-018 | Admin model discovery trusts syntactically valid upstream JSON without validating its schema | Validate the root object, collection fields, members, and model identifiers before normalization; return a stable non-sensitive Admin error for every mismatch. |
| AUD-019 | **Open:** the executable inventory is correct for the known consumers, but leading-whitespace parsing and out-of-order Chat identity still bypass the bounded stream gate (AUD-022/AUD-023) | Reopen after the two stream-gate counterexamples are fixed and re-audited. |
| AUD-021 | **Open:** canonical `computer_call` itself round-trips, but `computer_call_output` is silently discarded (AUD-024) | Reopen after the owner selects explicit rejection or complete native output support and the contract is verified. |
| AUD-022 | Responses stream argument semantic gate can skip a completed JSON value with leading whitespace | Normalize JSON whitespace before semantic inspection and add raw/parsed SSE regressions; no business decision is required. |
| AUD-023 | Chat stream tool identity uses arrival order instead of the wire `index` | Use a stable index-to-call mapping and fail closed on conflicts; no business decision is required. |

### Business/semantic decisions requiring owner authority

| ID | Decision / current state | Why it cannot be inferred safely |
| --- | --- | --- |
| AUD-005 | **Recorded:** provider vendor/variant is derived from URL; unmatched URLs use custom and remain allowed | URL-authoritative custom endpoint semantics are product policy; the implementation boundary must follow the owner decision. |
| AUD-004 | Whether to adopt stronger artifact-integrity controls such as digest pinning, SBOM, provenance, and signing before a public release or stronger security claim | Manual release and the current pre-release risk acceptance are explicit product policy; stronger guarantees require an owner decision. |
| AUD-009 | **Recorded:** only exact backend-supported `api_type` values count as present; every other value is inferred from exact preset URL support order, custom defaults to Responses, and no write-back occurs | Protocol selection changes routing behavior, so its fallback order requires owner authority. |
| AUD-011 | **Recorded:** arbitrary HTTP(S) custom URLs may receive upstream API keys within local/LAN scope; redirects default off but may be explicitly enabled per provider | The egress/key-disclosure boundary and opt-in redirect expansion require owner authority; policy enforcement remains a repairable transport control. |
| AUD-017 | **Recorded:** configured credentials have no minimum-length requirement; Rosetta still requires a configured Gateway API key and has no unauthenticated mode; ambiguous return collisions must fail closed rather than leak credentials or silently emit corrupted SSE/JSON | Identical bytes cannot always be classified as reflection versus legitimate content, so the owner selected controlled failure while retaining both the no-leak and protocol-integrity requirements. |
| AUD-020 | **Recorded:** every untrusted return boundary protects only credentials configured for the active outbound provider or auxiliary client; global diagnostics continue to use `GatewayConfig.token_values` | The owner selected the narrower local/LAN product boundary and explicitly accepted cross-provider/client configured-secret reflection outside the active return gate. |
| AUD-024 | **Recorded:** explicitly reject `computer_call_output` with a controlled unsupported-item error; do not expand generic computer-control support | Full support expands the computer-control protocol surface; explicit rejection keeps the current Responses-only, non-streaming scope. |

The remaining `No Action`, deterministic-only, and excluded-runtime statements
are evidence status or explicit scope limits, not additional remediation
findings. They must not be presented as live-production or provider-quality
claims.

## Findings Status

| ID | Severity | Decision class | Status | Root cause | Affected scenarios/areas | Owner/decision | Due/revisit trigger |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AUD-019 | Must Fix | Agent-Fixable | Closed | Shared Responses consumer inventory now normalizes completed JSON whitespace and resolves Chat tool identities by explicit wire index | PROVIDER-01/TOOL-01/SCN-03/SCN-04/SCN-05/CTRL-03 | Gateway transport/security owner | Reopen on a new embedded-JSON consumer, stream identity, or parser boundary |
| AUD-021 | Must Fix | Decision Recorded | Closed | Canonical `computer_call` remains supported for Responses non-streaming; `computer_call_output` is now rejected explicitly under the recorded scope decision | TOOL-01/SCN-03/SCN-05/IF-05 | Project owner decision recorded: explicit rejection; no generic computer-control expansion | Reopen only if complete computer-output support is authorized |
| AUD-022 | Must Fix | Agent-Fixable | Closed | The bounded argument gate strips both leading and trailing JSON whitespace before semantic credential inspection | PROVIDER-01/SCN-03/SCN-04/CTRL-03; raw and parsed SSE | Gateway transport/security owner | Reopen if embedded JSON inventory, parser, or stream framing changes |
| AUD-023 | Must Fix | Agent-Fixable | Closed | Chat tool fragments use bounded index-to-call mappings, detect remaps/conflicts, and fail closed on missing identity | PROVIDER-01/SCN-03/SCN-04/CTRL-03; Chat SSE | Gateway transport/security owner | Reopen on Chat wire-schema, identity, or state-bound changes |
| AUD-024 | Must Fix | Decision Recorded | Closed | `computer_call_output` is rejected with a stable `NotImplementedError` before unknown-item handling can drop it | TOOL-01/SCN-03/SCN-05/IF-05; computer-use history | Project owner decision recorded: explicit rejection; Responses-only non-streaming scope retained | Reopen if native result support is authorized |
| AUD-020 | Must Fix | Decision Recorded | Closed | Active-provider/client credential inventory is the authoritative return-gate domain; global configured-token inventory remains diagnostic-only | PROVIDER-01/SIDE-01/SCN-03/CTRL-03; provider and auxiliary return-domain ownership | Project owner decision recorded in profile | Reopen if deployment boundary or credential-domain ownership changes |
| AUD-017 | Must Fix | Agent-Fixable | Closed | Credential-bearing return boundaries now preserve credential-free values byte-for-byte and fail closed on exact collisions; raw passthrough releases only complete safe SSE events and terminates from a valid event boundary | PROVIDER-01/SIDE-01/SCN-03/SCN-04/CTRL-03; provider, sidecar, search, SSE, and JSON return boundaries | Owner decision recorded; Gateway transport/security owner | Reopen if credential syntax, return clients, parsing, or stream framing changes |
| AUD-018 | Should Plan | Agent-Fixable | Closed | Admin model discovery validates the provider-specific root, collection, member, and identifier schema before normalization and returns a stable controlled error on mismatch | AUTH-02/SCN-08/SCN-09; Admin provider/model operation | Gateway/Admin owner | Reopen if model-list schema, shim ID ownership, or Admin error handling changes |
| AUD-002 | Must Fix | Agent-Fixable | Closed | Transactional compaction replacement row/byte/replacement-size quotas now bound supported local/LAN persistence | SCN-06, SCN-07; persistence/observability | Project owner / Gateway persistence owner | Reopen if limits or storage path change |
| AUD-001 | Should Plan | Agent-Fixable | Closed | Rosetta-version config/state/API migration and legacy compatibility paths were rejected or removed under the prelaunch no-migration boundary | SCN-08, SCN-06, DATA-03; config/local mode/admin/persistence/core API | Project owner / core and gateway owners | Reopen if a migration path is added |
| AUD-003 | Should Plan | Agent-Fixable | Closed | Shared exact-marker gate now covers every enumerated real-call entry point | SCN-11; scripts/live-agent/integration/agent control plane | Project owner / test-harness owner | Reopen on any new ungated live entry point |
| AUD-005 | Should Plan | Decision Recorded | Closed | URL-authoritative runtime resolution and Admin profile derivation now agree without persisting options | SCN-09; provider/config/Admin UI | Project owner decision recorded in profile | Reopen if provider options become persisted or URL semantics change |
| AUD-006 | Should Plan | Agent-Fixable | Closed | Integration/agent launchers, all 24 executable examples, and the live SSE development script fail closed before credentials/external work | SCN-11; integration, examples, dev scripts, and agent launch scripts | Project owner / test-harness owner | Reopen on any new real-call entry point |
| AUD-007 | Should Plan | Agent-Fixable | Closed | Admin profile is derived from runtime API fields and URL/protocol rules | SCN-09; Admin UI/config route | Gateway/Admin owner | Reopen if API response loses required derived fields |
| AUD-008 | Should Plan | Agent-Fixable | Closed | Findings, coverage, system map, run evidence, and rotation queue reconciled to the third omission baseline | audit control plane | Audit owner | Reopen when a remediation run leaves contradictory ledger state |
| AUD-010 | Should Plan | Agent-Fixable | Closed | SQLite validator checks columns, constraints, primary keys, required index columns, uniqueness, origin, and partial flag | DATA-01/DATA-03; persistence startup/write path | Persistence owner | Reopen on schema/table/index change without updated contract |
| AUD-012 | Must Fix | Agent-Fixable | Closed | Provider redirects are denied by default and isolated by policy; auxiliary HTTP requests force no-follow; provider opt-in is explicit | PROVIDER-01/SCN-09; transport boundary | Gateway transport owner | Reopen if redirect behavior or HTTP client changes |
| AUD-014 | Must Fix | Agent-Fixable | Closed | Tavily credential collisions are blocked before success/error data exposure; detached transport exceptions remain exactly redacted and cause-free | SIDE-01/CTRL-03; search/diagnostic boundary | Gateway search owner | Reopen if Tavily client or redaction boundary changes |
| AUD-015 | Must Fix | Agent-Fixable | Closed | Provider, sidecar, Admin model-discovery, parsed-object, raw-byte, stream, and exception return boundaries prevent credentials of the active provider/client from reaching downstream consumers; AUD-017 defines collision-safe semantics and AUD-020 defines the inventory domain | PROVIDER-01/SIDE-01/SCN-03/CTRL-03; downstream, model, trace, and diagnostic boundaries | Gateway transport and search owners | Reopen on any credential-bearing client, return path, dict-key handling, stream framing, exception propagation, or credential-domain change |
| AUD-016 | Must Fix | Agent-Fixable | Closed | `ProviderInfo` exposes the canonical `KeyRing` rotation sequence and `GatewayConfig` registers both the raw CSV and every selectable trimmed key with all runtime redactors | PROVIDER-01/DATA-01/CTRL-03; logs, traces, metrics, persistence, and response redaction | Gateway config, transport, and observability owners | Reopen on credential syntax, parsing, selection, startup/hot-reload propagation, or redactor-consumer changes |
| AUD-009 | Should Plan | Decision Recorded | Closed | Only exact backend-supported `api_type` strings are present; all other values infer in memory using `responses`, `chat`, `anthropic`, `google` order; custom defaults to Responses; warning emitted | PROVIDER-01; config/Admin | Project owner decision recorded in profile | Reopen if support list, fallback order, or persistence semantics change |
| AUD-011 | Should Plan | Decision Recorded | Risk Accepted | Direct arbitrary HTTP(S) custom egress and key delivery are accepted within local/LAN scope; provider redirect expansion requires explicit opt-in | PROVIDER-01/SCN-09; transport boundary | Project owner | Reopen if deployment boundary, direct-egress policy, or redirect policy changes |

## Historical Closure Evidence

Rows here preserve the evidence that closed a finding at that time. The current
status table above remains authoritative when a later audit reopens an ID.

| ID | Closed in run/head | Closure evidence | Residual risk | Reopen trigger |
| --- | --- | --- | --- | --- |
| AUD-001 | 20260719-1712 / working tree | Legacy config/state/API migration and deprecated retention aliases rejected or removed; deterministic suite green | Current Codex/provider protocol compatibility remains explicit; old user data is not migrated | Reopen if any Rosetta-version migration path is added |
| AUD-002 | 20260719-1712 / working tree | Single-row, per-principal and global compaction row/byte quotas enforced transactionally with focused tests | Chosen limits are local/LAN operational limits; no disk-recovery guarantee | Reopen if compaction storage semantics change |
| AUD-003 | 20260720-1107 / `e7f72bf` | Exact opt-in gate tested fail-closed for enumerated live/integration runners | Approved development live runs remain outside audit evidence | Reopen if a runner bypasses the shared gate |
| AUD-005 | 20260720-1107 / `e7f72bf` | URL-authoritative preset/custom resolution, runtime profile derivation, and no-option persistence tests | Custom egress remains owner-accepted local/LAN risk; no public claim | Reopen if provider options become persisted or matching diverges |
| AUD-006 | 20260720-1554 / `5d95668` | Live SSE development script gates before `.env`; `dev_scripts/*live*.py` joins the dynamic contract inventory | No real-call trajectory was run | Reopen on new runner/example/dev script |
| AUD-007 | 20260720-1107 / `e7f72bf` | Admin provider/model-group profile and validation rendering use runtime-derived fields | No browser deployment evidence | Reopen if Admin API/UI contract changes |
| AUD-008 | 20260720-1554 / working tree | Profile, findings, coverage, system map, README, and immutable run evidence reconciled to `de9c96b` | Ledger validity is repository-local | Reopen on contradictory status |
| AUD-009 | 20260720-1554 / `3f2d044` | Unknown strings, empty values, booleans, numbers, and containers all infer at runtime; Admin rendering and no-write-back tests pass | A wrong custom URL can still select Responses until operator corrects it | Reopen if support list, fallback order, or persistence changes |
| AUD-010 | 20260720-1239 / `ec8419b` | SQLite schema fingerprint includes index uniqueness/origin/partial attributes with focused tests | No restore/long-run stress claim | Reopen on schema change |
| AUD-011 | 20260720-1554 / `3e327c8` | Profile retains accepted direct custom egress and explicit provider redirect opt-in while auxiliary requests remain no-follow | Direct custom URL and enabled redirect-target SSRF/account-security risk remain accepted within local/LAN boundary | Reopen if scope or policy changes |
| AUD-012 | 20260720-1554 / `3e327c8` | Loopback regressions prove default provider and auxiliary redirects do not reach their target; explicit provider opt-in is separately tested | DNS/proxy behavior was not live-tested; opt-in can forward credentials | Reopen if redirect/client behavior changes |
| AUD-014 | 20260720-1554 / `b7542d2`, `de9c96b` | Success, HTTP error, and transport-exception reflection tests prove the configured Tavily key is absent and the original exception cause is detached | No real Tavily response was exercised | Reopen if Tavily response/error handling changes |
| AUD-015 | 20260720-1606 / working tree | Shared provider-return decorator, sidecar and Admin model-discovery redaction, cause detachment, arbitrary raw-SSE split coverage, and adversarial dict-key tests; independent focused/lint/full verification green | Deterministic fake-only evidence; no real provider, sidecar, external sink, or production runtime exercised | Reopen on a new credential-bearing client/return path, raw-stream implementation, dict-key serialization, exception propagation, or diagnostic sink |
| AUD-016 | 20260720-1606 / working tree | Canonical `KeyRing` values feed rotation and `GatewayConfig.token_values`; startup/hot-reload/rollback tests cover every rotated dummy key and runtime redactor; independent focused/lint/full verification green | Comma remains the credential-list syntax; no live rotation or external log/persistence sink exercised | Reopen on credential syntax/parser, provider construction, selection order, config activation/rollback, or redactor propagation changes |
| AUD-017 | 20260720-1859 / working tree | Provider/auxiliary/Tavily/sidecar/Admin boundaries block collisions; parsed JSON is not rewritten; raw SSE is held by complete event and tested across arbitrary splits, short/common tokens, rotation and framing; focused `187 passed`, full `3576 passed, 5 skipped`, lint and compatibility checks green | Exact matching does not detect encoded, hashed, or covert exfiltration; short/common credentials can intentionally make responses fail closed; no real upstream was exercised | Reopen on credential syntax, client inventory, return parsing, SSE framing, or collision policy change |
| AUD-018 | 20260720-1859 / working tree | Provider-specific model-list normalization rejects list/scalar/null roots, missing/wrong collections, invalid members/IDs, and preserves OpenAI/Anthropic/Google/custom-ID success cases; focused `187 passed`, full `3576 passed, 5 skipped` | Browser/LAN UX and real provider pagination remain unverified | Reopen on provider model-list schema, shim `model_id_field`, or Admin error contract change |
| AUD-019 | 20260720-2103 remediation / working tree | Shared JSON-semantic collision checks cover values and keys, Unicode and solidus escapes, surrogate pairs, raw success/error bodies, complete SSE events across arbitrary chunk splits, Tavily, and web-run sidecar responses; focused `105 passed`, full `3591 passed, 5 skipped`, lint and compatibility checks green | Invalid/non-JSON content retains exact wire matching only; arbitrary encoded, hashed, or covert exfiltration is outside the exact-match guarantee | Reopen on JSON/SSE parsing, return clients, wire framing, or credential matching changes |
| AUD-019 | 20260720-2255 remediation / working tree | Duplicate-preserving outer JSON parsing and bounded Responses/Chat argument semantics aligned to actual call/item/index consumers; safe duplicate/BOM/unknown-string/identity-change/resource-limit regressions; focused `322 passed`, full `3604 passed, 5 skipped`, lint green | Real provider/Codex timing remains unverified; unknown provider-specific nested schemas remain outside coverage until explicitly registered | Reopen on argument schema, identity resolution, parser, stream framing, or state-bound change |
| AUD-019 | 20260721-0906 remediation / working tree | Shared executable inventory covers all five current Responses embedded-JSON fields; converter-inventory contract plus adversarial non-streaming/custom-SSE tests; focused `326 passed`, full `3621 passed, 5 skipped`, lint and compatibility green | Real provider/Codex timing and future unregistered consumer fields remain Unknown | Reopen on converter consumer, schema, identity, parser, stream framing, or state-bound change |
| AUD-021 | 20260721-0906 remediation / working tree | Local SDK-backed canonical `computer_call`, IR `computer_use`, exact non-streaming Responses round trip, and explicit Chat/Anthropic/Google/stream rejection; focused/full/lint/compatibility green | Cross-format and converted-stream computer control remains deliberately unsupported; no live provider evidence | Reopen if computer-control support, wire fields, stream mapping, or target-format semantics change |
| AUD-020 | 20260720-2103 decision closure / working tree | Approved profile defines active outbound provider/client credentials as the return-gate domain; a deterministic transport contract proves an unrelated configured provider credential is returned unchanged while existing active-provider collision tests remain fail-closed | Cross-provider/client credential reflection is accepted within the local/LAN-only boundary; global diagnostics still redact the complete configured-token inventory | Reopen if public deployment, global no-configured-token return semantics, or credential ownership changes |

## AUD-019 — Consumer-semantic JSON reconstruction bypasses return credential checks

- Severity: Must Fix
- Decision class: Agent-Fixable
- Status: Closed
- Confidence: High
- First detected run: `20260720-2103`
- Last updated run: `20260721-1232`
- Owner: Gateway transport/security owner

### Failure and impact

The `20260720-2347` audit proved that the prior manual registry omitted
`custom_tool_call.input`, `shell_call.arguments`, and
`code_interpreter_call.arguments`. The `20260721-0906` remediation replaced
that drift-prone boundary with an executable inventory shared by Responses
message routing and the semantic gate, then verified every declared field
against the actual converter consumer. Custom-tool input delta/done events now
use the same bounded semantic accumulator as function arguments.

The `20260721-1148` independent omission audit disproved two remaining closure
assumptions: leading JSON whitespace skips the completion predicate, and Chat
wire indices are resolved through arrival order rather than a stable index map.
AUD-022 and AUD-023 record these sub-findings and reopen AUD-019.

### Frozen acceptance criteria

- [x] Raw-preserving JSON return channels detect credentials in every duplicate member before ordinary dict collapse can erase evidence.
- [x] Every current supported JSON-string field parsed again by Responses/Chat consumers is checked using schema-aware bounded semantics.
- [x] Stateful Responses and Chat argument-delta reconstruction is checked across arbitrary SSE event and HTTP chunk boundaries before the completing event is released.
- [x] Safe credential-free wire bytes remain byte-identical; invalid/unrelated strings are not recursively interpreted without a documented consumer contract.
- [x] The current-provider-only domain from AUD-020 remains unchanged, and short/common credential collisions retain AUD-017 fail-closed protocol integrity.
- [x] Focused parser/state tests, full deterministic checks, compatibility ledgers, and a phase-separated re-audit restore only the affected cone after the consumer inventory is complete.
- [ ] Leading-whitespace completed JSON and out-of-order Chat indices are blocked before release on both raw and parsed stream paths.

### Evidence and residual risk

- Closure evidence: `docs/audit/runs/20260721-0906/EVIDENCE.md` UNIT-001/003; pre-fix `9 failed, 218 passed`, post-fix focused `326 passed`, full `3621 passed, 5 skipped`, lint and Codex compatibility green.
- Residual risk boundary: arbitrary encrypted, compressed, hashed, or covert exfiltration remains outside the exact-reflection guarantee. The finding is limited to documented/supported downstream parser and accumulation semantics.

### Remediation history

| Wave/head | Changes | Verification | Result | Coverage invalidated |
| --- | --- | --- | --- | --- |
| `20260720-2103` / `73afaeb` | added exact-wire plus one-layer parsed JSON/SSE semantic collision checks | focused `105 passed`; full `3591 passed, 5 skipped`; lint/compatibility green | Closed at that evidence depth | None at that time |
| `20260720-2215` / `353a795` | independent omission audit only; no implementation change | duplicate-member, nested-second-parse, and cross-event delta probes reproduce; focused `266 passed`; full `3592 passed, 5 skipped`; lint green | Reopened | PROVIDER-01, TOOL-01, SCN-03, SCN-04, SCN-05, CTRL-03, GP-003 |
| `20260720-2255` / working tree | duplicate-preserving JSON primitive plus bounded provider-schema semantic gate aligned to real Responses/Chat consumer identities; BOM handling and active-provider scope retained | independent focused `322 passed`; lint green; full `3604 passed, 5 skipped, 11 warnings`; CodeGraph synchronized | Closed | affected cone restored deterministically; live behavior remains Unknown |
| `20260720-2347` / current working tree | periodic discovery only; no implementation change | gate allows escaped active credential through custom/shell/code-interpreter fields; converter reconstructs plaintext; focused `249 passed`; full `3604 passed, 5 skipped`; lint and compatibility green | Reopened | PROVIDER-01, TOOL-01, SCN-03, SCN-05, CTRL-03, GP-003 |
| `20260721-0906` / current working tree | executable embedded-JSON inventory shared by converter routing and semantic gate; custom input delta/done accumulation added | focused `326 passed`; lint green; full `3621 passed, 5 skipped`; compatibility green | Closed | affected cone restored deterministically; live behavior remains Unknown |
| `20260721-1148` / current working tree | independent omission audit only; no implementation change | leading-whitespace Responses and out-of-order Chat probes reproduce; existing focused suite `242 passed` | Reopened via AUD-022/AUD-023 | PROVIDER-01, STREAM-01, TOOL-01, SCN-03/04/05, CTRL-03 |
| `20260721-1232` / remediation commits `f30d167`, `6bd24b4` | normalized JSON whitespace and replaced arrival-order Chat identity with bounded index mappings and fail-closed conflicts | focused affected-cone `248 passed`; full `3624 passed, 5 skipped`; `make lint` passed | Closed at deterministic evidence depth | live provider timing and future converter consumers remain Unknown |

### Closure/reopen

- Historical closure evidence: `docs/audit/runs/20260720-2255/EVIDENCE.md` records implementation review, consumer-identity alignment, focused/lint/full verification, and safe-byte regressions for the schemas known in that run.
- Historical closure evidence: `docs/audit/runs/20260721-0906/EVIDENCE.md` proves the consumer inventory entries are real converter consumers, but it does not cover the `20260721-1148` whitespace/index counterexamples.
- The generic redactor gained only duplicate-preserving outer JSON parsing; provider protocol knowledge and bounded live state remain owned by the transport credential boundary.
- AUD-020 remains unchanged: only credentials of the active provider/client are inspected at the return gate.

## AUD-021 — Responses computer-tool wire and IR contracts disagree

- Severity: Must Fix
- Decision class: Decision Recorded
- Status: Closed
- Confidence: High
- First detected run: `20260720-2347`
- Last updated run: `20260721-1232`
- Owner: Core converter/IR owner

### Failure and impact

The `20260720-2347` audit found public types using `computer_tool_call`, while
the converter used `computer_call` and produced an IR value rejected by
validation. Local OpenAI SDK `2.45.0` confirms `computer_call` as canonical.
The public type, message dispatch, IR, and converter now agree; the complete
native item is preserved for a non-streaming Responses round trip. Unsupported
target formats and generic stream conversion raise explicitly.

The `20260721-1148` audit found that the matching client result item,
`computer_call_output`, is outside the result dispatcher and silently disappears.
AUD-024 records the owner decision needed for that adjacent contract and reopens
AUD-021 without invalidating the already-proven call-item behavior.

### Frozen acceptance criteria

- [x] One canonical Responses computer-tool wire type is shared by response types, dispatch, stream handling, and tests.
- [x] The IR owns an explicit `computer_use` representation with native Responses item preservation.
- [x] The canonical item produces a non-empty validated IR choice and does not silently fall through.
- [x] Unsupported target and generic stream conversions fail explicitly; they are not silently discarded.
- [x] Provider-to-IR, IR-to-provider, streaming rejection, and cross-format rejection tests cover structure and validation behavior.
- [x] Focused converter/type tests and the full deterministic suite pass in a phase-separated re-audit.
- [x] `computer_call_output` is rejected explicitly without losing history; complete native result support remains out of scope.

### Evidence and residual risk

- `docs/audit/runs/20260721-0906/EVIDENCE.md` UNIT-002/003 records SDK authority, exact same-format round trip, explicit unsupported paths, and full verification.
- `docs/audit/runs/20260721-1148/EVIDENCE.md` records the deterministic output-loss probe that opened the adjacent result contract as AUD-024; `20260721-1232` records the explicit-rejection closure.
- Cross-format and converted-stream computer-control support remains deliberately unsupported.
- No real provider/Codex behavior was exercised, and no Codex computer-call capability claim was added.

## AUD-020 — Return credential inventory is active-provider local

- Severity: Must Fix
- Decision class: Decision Recorded
- Status: Closed
- Confidence: High
- First detected run: `20260720-2103`
- Owner: Project owner; Gateway transport/security owner maintains the contract

### Observed boundary and impact

`CredentialRedactingTransport` builds its redactor from the active `ProviderInfo.credential_values`. The process separately owns the complete atomic configured-token inventory in `GatewayConfig.token_values` for diagnostics, but that inventory is intentionally not supplied to provider return gates. A Provider A request may therefore return a configured Provider B key unchanged in parsed and raw bodies.

### Human decision

- Option A considered: every untrusted return gate uses the complete atomic runtime configured-token inventory and fails closed on any collision. This can make an unrelated short/common configured token block otherwise legitimate output.
- Option B selected: protect only credentials configured for the active outbound provider/client. This avoids cross-route false blocking and explicitly accepts cross-provider or cross-client configured-secret reflection.
- Decision: protect only credentials configured for the active outbound provider or auxiliary client. Do not seed return gates from unrelated providers. Global observability/diagnostic redactors continue to use the complete configured-token inventory.
- Authority/date: Project owner / 2026-07-20.
- Accepted residual risk: a provider/client may return a credential configured only for another provider/client; this is outside the supported return-gate guarantee under the local/LAN-only profile.

### Frozen acceptance criteria after decision

- [x] The authoritative active-provider/client credential domain is documented in the profile and compatibility/security ledgers.
- [x] Existing startup and atomic hot reload/rollback continue to propagate each provider/client's own canonical inventory without introducing a global return-gate dependency.
- [x] Provider, auxiliary, Tavily, sidecar, Admin, raw/parsed/stream/error paths retain their current active-client domain.
- [x] Cross-provider reflection is explicitly accepted and covered by a regression; active short/common-token collisions remain fail-closed.
- [x] Phase-separated verification restores PROVIDER-01, SIDE-01, SCN-03, and CTRL-03 within the approved domain.

### Closure evidence

- The approved profile and Codex compatibility ledger now distinguish active-client return gates from global diagnostic redaction.
- `test_non_streaming_ignores_credentials_outside_active_provider` freezes the selected cross-provider behavior, while the existing collision matrix continues to prove active-provider credentials fail closed; focused `19 passed`, full `3592 passed, 5 skipped`, lint and compatibility checks are green.
- No runtime inventory expansion or real API call was introduced.

## Omission-remediation re-audit — `20260720-1239`

The second omission pass reopened incomplete controls, identified redirect credential exposure, and replaced the earlier AUD-009 decision. Details and current evidence are in [`docs/audit/runs/20260720-1239/REPORT.md`](runs/20260720-1239/REPORT.md) and [`EVIDENCE.md`](runs/20260720-1239/EVIDENCE.md). Prior runs remain historical evidence only.

### Classification at that run

- Agent-fixable and closed at that run: AUD-003, AUD-006 (including executable examples), AUD-007, AUD-008, AUD-010 (complete index attributes), AUD-012 (the then-current redirect prohibition).
- Business semantics recorded: AUD-005 (URL-authoritative custom behavior), AUD-009 (runtime protocol inference and warning), AUD-011 (direct arbitrary custom HTTP(S) egress accepted within local/LAN scope).
- No live/provider/deployment claim: this run remains static/deterministic only.

## Third omission-remediation re-audit — `20260720-1554`

The third omission pass reopened AUD-006, AUD-009, and AUD-012, reconciled AUD-008, and opened AUD-014 for Tavily credential reflection. Owner decisions permit explicit per-provider redirects and require every backend-unrecognized `api_type` value to behave as missing. Details and current evidence are in [`docs/audit/runs/20260720-1554/REPORT.md`](runs/20260720-1554/REPORT.md) and [`EVIDENCE.md`](runs/20260720-1554/EVIDENCE.md).

### Classification at that run

- Agent-fixable and closed: AUD-006 (including the live SSE dev script), AUD-008, AUD-012 (default-deny plus auxiliary isolation), and AUD-014 (Tavily reflected-token boundary).
- Business semantics recorded: AUD-005 (URL-authoritative custom behavior), AUD-009 (only backend-recognized values are explicit; all others infer), AUD-011 (direct custom egress and explicit provider redirect opt-in accepted within local/LAN scope), and rejected candidate AUD-013.
- No additional owner decision is required. No live/provider/deployment claim is made; this run remains static/deterministic only.

## Fourth independent omission audit - `20260720-1606`

This pass independently challenged the credential-return, redirect, and live-runner inventories at current HEAD `26b7558`. It found two previously omitted credential exposures while finding no current redirect or live-approval bypass. Details are in [`docs/audit/runs/20260720-1606/REPORT.md`](runs/20260720-1606/REPORT.md) and [`EVIDENCE.md`](runs/20260720-1606/EVIDENCE.md).

### Current classification

- Closed after authorized remediation and phase-separated verification: AUD-015 (provider, sidecar, Admin model-discovery, stream, exception, and dict-key reflected credentials) and AUD-016 (canonical rotated wire-key inventory and atomic runtime propagation).
- Business semantics remain unchanged: the approved profile already requires configured-token redaction and does not tolerate credential leakage, so neither finding requires a new owner decision.
- No Action at current HEAD: semantic live-runner inventory and redirect/direct-HTTP falsification found no reachable bypass. No real API or agent call was made.

## Fifth independent omission audit - `20260720-1859`

This pass challenged the fourth audit's non-secret preservation oracle and the Admin model-discovery response boundary at current HEAD `6d1bc7a`. Details are in [`docs/audit/runs/20260720-1859/REPORT.md`](runs/20260720-1859/REPORT.md) and [`EVIDENCE.md`](runs/20260720-1859/EVIDENCE.md).

### Current classification

- Closed after authorized remediation and phase-separated deterministic verification: AUD-017 and AUD-018.
- Credentials retain no minimum-length requirement, Rosetta remains Gateway-API-key authenticated, and exact return collisions fail closed without rewriting legitimate JSON or emitting a partial SSE event.
- Admin model discovery rejects syntactically valid but schema-invalid provider JSON with one stable controlled error while preserving supported OpenAI/Anthropic/Google/custom-ID normalization.
- Historical AUD-015/AUD-016 closure evidence remains preserved. No real API/agent call occurred; browser/LAN and external-provider behavior remain unverified.

## Seventh independent omission audit - `20260720-2215`

This pass independently challenged the just-closed credential parser oracle at current HEAD `353a795`. A fresh-context subagent received only the repository path and audit skill, then identified the deeper root cause before platform safety filtering interrupted report finalization; the main agent independently reproduced and recorded the evidence. Details are in [`docs/audit/runs/20260720-2215/REPORT.md`](runs/20260720-2215/REPORT.md) and [`EVIDENCE.md`](runs/20260720-2215/EVIDENCE.md).

### Current classification

- Reopened: AUD-019 (`Must Fix / Agent-Fixable`) because one-layer JSON checks do not cover duplicate-member preservation, nested tool-argument parsing, or cross-event delta accumulation.
- Remains closed: AUD-020; every probe uses the current provider credential and does not challenge the active-provider-only owner decision.
- No implementation remediation or real API call occurred. Focused `266 passed`, full `3592 passed, 5 skipped`, and lint are green but do not contain the failing consumer-semantic oracle.

## AUD-019 remediation re-audit - `20260720-2255`

The same subagent that independently found the omission implemented the frozen
repair boundary. The main agent then independently challenged safe duplicate
members and Responses/Chat identity changes before running focused, lint, and
full deterministic verification. Details are in
[`docs/audit/runs/20260720-2255/REPORT.md`](runs/20260720-2255/REPORT.md) and
[`EVIDENCE.md`](runs/20260720-2255/EVIDENCE.md).

### Current classification

- Closed: AUD-019 (`Must Fix / Agent-Fixable`).
- Retained: AUD-020 active-provider-only credential domain and AUD-017 collision/protocol semantics.
- Deterministic evidence only: focused `322 passed`, full `3604 passed, 5 skipped`, lint green; no real API/provider/Codex call occurred.

## Eighth independent omission audit - `20260720-2347`

This bounded periodic pass re-enumerated the actual Responses second-parse consumers and the computer-tool response contract at current HEAD `353a795` plus the current remediation working tree. Details are in [`docs/audit/runs/20260720-2347/REPORT.md`](runs/20260720-2347/REPORT.md) and [`EVIDENCE.md`](runs/20260720-2347/EVIDENCE.md).

### Current classification

- Reopened: AUD-019 (`Must Fix / Agent-Fixable`) because three current Responses fields decoded with `json.loads()` are absent from the semantic gate registry.
- Opened: AUD-021 (`Must Fix / Agent-Fixable`) because canonical `computer_tool_call` is silently dropped and fallback `computer_call` violates the IR tool-type contract.
- Retained: AUD-020 active-provider-only credential domain, AUD-017 collision/protocol semantics, and historical function/Chat state-bound evidence.
- No implementation remediation or real API call occurred. Focused `249 passed`, full `3604 passed, 5 skipped`, lint and Codex compatibility are green but lack the failing oracles.

## AUD-019 / AUD-021 targeted remediation - `20260721-0906`

This authorized repair froze the eighth-pass repros, added failing security and
protocol oracles, implemented the smallest shared ownership changes, then ran a
phase-separated targeted re-audit. Details are in
[`REPORT.md`](runs/20260721-0906/REPORT.md) and
[`EVIDENCE.md`](runs/20260721-0906/EVIDENCE.md).

### Current classification

- Closed: AUD-019 (`Must Fix / Agent-Fixable`).
- Closed: AUD-021 (`Must Fix / Agent-Fixable`).
- Retained: AUD-020 active-provider-only credential domain and AUD-017 collision/protocol semantics.
- Deterministic evidence only: focused `326 passed`, full `3621 passed, 5 skipped`, lint and Codex compatibility green; no real API/provider/Codex call occurred.

## Ninth independent omission audit - `20260721-1148`

This read-only pass used a new independent subagent, then revalidated each report
against current source and deterministic in-process probes. Details are in
[`REPORT.md`](runs/20260721-1148/REPORT.md) and
[`EVIDENCE.md`](runs/20260721-1148/EVIDENCE.md).

### Current classification

- Reopened: AUD-019 through new sub-findings AUD-022/AUD-023 (`Must Fix /
  Agent-Fixable`); both are closed by the targeted remediation below.
- Reopened: AUD-021 through new sub-finding AUD-024 (`Must Fix / Decision
  Required`); the owner recorded explicit rejection and it is closed below.
- Retained: AUD-020 active-provider-only credential domain, AUD-017 collision
  semantics, local/LAN deployment scope, manual release, and no migration layer.
- Historical baseline: existing focused suite `242 passed`; all three deterministic
  counterexamples reproduced. Remediation and verification are recorded in the
  targeted re-audit `20260721-1232`.

## Targeted remediation re-audit - `20260721-1232`

The owner authorized the three scoped fixes and explicitly selected rejection of
`computer_call_output`. The remediation commits are `f30d167`, `6bd24b4`, and
`04efc74`. The focused affected-cone suite passed (`248 passed`), the full
deterministic suite passed (`3624 passed, 5 skipped`), `make lint` passed, and no
real provider/API/Codex call or deployment occurred. AUD-019, AUD-021, AUD-022,
AUD-023, and AUD-024 are closed at deterministic evidence depth; runtime/provider
timing and external-sink behavior remain outside the profile.

## Accepted Debt and Risk

| ID | Owner | Why acceptable now | Safety ceiling | Mitigations/monitoring | Revisit trigger/date | Expected resolution |
| --- | --- | --- | --- | --- | --- | --- |
| AUD-004 | Project owner | Project is permanently scoped to local/trusted-LAN deployment and makes no public or artifact-integrity guarantee | No public deployment/security claim; no automated package/image publication | manual tag/version gate; local build from current checkout; CI Docker secret checks; disabled push targets | If the deployment boundary or release claim changes | Pin/verify build inputs and define provenance/SBOM/signing only if the owner later expands the boundary |

## Golden-Principle Candidates

| GP ID | Recurring issue/invariant | Evidence occurrences | Proposed enforcement | False-positive/maintenance risk | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- |
| GP-001 | Real provider/Codex calls require explicit human approval and are never part of audit/default deterministic checks | live runners now share a fail-closed exact-marker gate; deterministic suite excludes real calls | keep the shared gate mandatory for every new runner | Approved live runs remain explicit and out of audit evidence | Project owner | Enforced |
| GP-002 | Every durable agent/gateway state store needs an explicit owner scope and aggregate byte/row/TTL bound | tool mappings and compaction mappings now have scope, TTL and transactional row/byte limits | require quota contract tests for each new durable store | Limits are local/LAN policy values and may need owner tuning | Gateway persistence owner | Enforced |
| GP-003 | Every credential-bearing outbound client must register the credentials actually sent on the wire and block untrusted return collisions without silently corrupting the supported wire/application protocol | Tavily required AUD-014; provider/sidecar siblings required AUD-015; CSV key rotation required AUD-016; AUD-017 established collision-safe fail-closed semantics; AUD-019 now shares an executable Responses consumer inventory with converter routing and contract tests | keep the schema-aware inventory and bounded accumulators executable against every current converter parser and stream consumer across success/error/stream/exception paths | arbitrary recursive parsing would create false positives and unbounded state; enforcement must remain schema-aware and bounded | Gateway transport/security owner | Enforced |

## Candidate Disposition

| Candidate | Run/area | Disposition | Evidence/reason |
| --- | --- | --- | --- |
| Reuse old audit `FULL.md` status | UNIT-001 | Rejected | old head/profile and missing durable ledgers invalidate freshness |
| Treat no deployment as no security scope | UNIT-001/002 | Rejected | local/LAN auth, secrets, principal isolation and untrusted provider content remain in scope |
| Treat all `legacy` strings as one defect | UNIT-004 | Rejected | current Codex/provider protocol compatibility is distinct from Rosetta-version migration; inventory must separate them |
| AUD-013: reject model groups that reference missing/disabled providers | 20260720-1239 / config routing | Rejected by owner | Current silent-skip behavior is proportionate to this Gateway's scale; no new validation/error-propagation state machine is introduced. Revisit only if routing scale or operability requirements change. |

---

## AUD-017 — Exact credential replacement can corrupt legitimate protocols and content

- Severity: Must Fix
- Decision class: Agent-Fixable after owner decision
- Status: Closed
- Confidence: High
- First detected run: `20260720-1859`
- Last updated run: `20260720-1859`
- Owner: Gateway transport/security owner; project-owner semantics recorded 2026-07-20

### Quality attributes and profile requirements

- Affected attributes: Correctness, security, interoperability, reliability, operability.
- Profile/control requirement: configured credentials must not be returned, while Codex Responses/SSE semantics, framing, ordering, schemas, and non-secret content remain compatible.
- Violated invariant/outcome: every currently accepted credential value is treated as a context-free substring, so a legitimate protocol/content occurrence is indistinguishable from reflection and may be silently rewritten.

### Failure, abuse, or structural path

```text
Stimulus/trigger: Configure a custom/local provider credential equal to a short or common
                  protocol/content string such as "data", "id", or "a".
Environment/preconditions: Supported local/LAN Gateway; provider configuration is otherwise valid;
                           passthrough/converted response or stream crosses the credential redactor.
Path/components: GatewayConfig/build_provider_info -> KeyRing.credential_values -> SecretRedactor ->
                 CredentialRedactingTransport parsed/raw return -> downstream Codex/Admin/model consumer.
Expected response: The configured credential is not disclosed and legitimate response schema/framing/content is preserved.
Observed failure: Unconditional substring replacement rewrites SSE field names, event text, JSON keys,
                  scalar values, or raw JSON syntax even when the occurrence is legitimate content.
```

### Impact and risk basis

- User/business/mission impact: core Codex response streams can become unparsable or semantically wrong under a configuration the Gateway currently accepts.
- Security/privacy/data/reliability impact: the security control creates silent protocol corruption; attempts to avoid corruption without a credential contract can reintroduce credential disclosure.
- Likelihood/exploitability: requires a credential that overlaps legitimate output. Custom/local endpoints often use operator-chosen development tokens, and the code accepts every non-empty segment.
- Blast radius: every return using the affected provider/sidecar/search credential, including success, error, parsed, and raw-stream paths.
- Reversibility/recovery: operator can change the credential or disable the client; existing responses cannot be recovered after transformation.
- Systemic reach: shared `SecretRedactor` and credential-bearing client matrix.

### Scope and occurrences

| Component/path/symbol/workflow | Evidence | Why affected |
| --- | --- | --- |
| `gateway/transport/provider_info.py:35-50,87,94-103` | `KeyRing` accepts every non-empty comma-delimited segment | no minimum length, entropy, or safe-alphabet contract bounds exact matching |
| `observability/redaction.py:69-89,153-229` | credentials become raw/JSON-escaped byte regex alternatives and string replacements | replacement is context-free across keys, values, syntax, and stream framing |
| `gateway/transport/credential_redaction.py:90-126,237-247` | exact replacement is applied to parsed events, raw SSE, and raw response bodies | critical downstream boundary consumes the unsafe transform |
| Tavily and web-run sidecar clients | both reuse `SecretRedactor` with unconstrained configured tokens | parsed result/error/model text can be altered for the same root cause |

### Evidence

- Code/configuration evidence: current accepted credential domain and shared exact replacement path above.
- Test/scanner evidence: related focused suite reports `22 passed`, but raw-stream tests use a long unique token and define compatibility as unconditional `payload.replace(token, b"[REDACTED]")`.
- Runtime/operations/incident evidence: deterministic probe with a real `GatewayConfig` and `api_key="data"` changes standard `data:` framing to `[REDACTED]:`; `framing_preserved=False`. Additional probes show `a` rewriting event/payload text and `id` rewriting a JSON key.
- Architecture/history evidence: AUD-015 required status, schema, framing, ordering, and non-secret content compatibility; AUD-017 is a distinct root cause that invalidates that conclusion for the unconstrained credential domain without deleting the historical closure evidence.
- Contradicting evidence considered: arbitrary chunk-split tests prove match detection, not legitimate-content preservation; long production-like keys reduce likelihood but are not a current contract.
- Gaps/assumptions: no real provider was called. Encoded, hashed, or covert malicious-provider exfiltration is not solved by exact-value redaction and remains outside any deterministic literal-reflection claim.

### Recommended direction

- Smallest credible remediation/control: retain arbitrary configured credential lengths, make raw/parsed redaction protocol/schema-aware, and return a controlled fail-closed error whenever a credential collision cannot be removed without risking disclosure or malformed SSE/JSON.
- Rollout/migration/rollback implications: no credential-length migration or unauthenticated mode is introduced. Some short/common credentials may cause a request or stream to fail closed when safe output is impossible; diagnostics must identify the configuration problem without echoing the credential.
- Suggested priority: before release compatibility or credential-return closure is claimed.

### Frozen acceptance criteria

- [x] The project owner records that credentials have no minimum-length requirement, Rosetta remains API-key authenticated, and ambiguous collisions must fail closed without credential disclosure or silent protocol corruption; the profile and config contract agree.
- [x] Every credential accepted by the resulting contract has a defined, testable collision behavior that cannot silently break SSE field names/events, JSON syntax/schema, status/error structure, or non-secret scalar content.
- [x] Provider passthrough/converted, parsed/raw, streaming/non-streaming, Admin model discovery, Tavily, web-run sidecar, and image/auxiliary provider paths use the same canonical contract.
- [x] Regression tests cover one-character/common/numeric tokens, JSON keys/values, SSE `data`/`event` fields, arbitrary chunk splits, rotations, prefixes/overlap, and the existing startup/hot-reload credential inventory.
- [x] The original literal-reflection cases from AUD-014/AUD-015/AUD-016 remain secret-free under the approved fail-closed semantics.
- [x] Targeted tests, full deterministic suite, lint/type checks, compatibility checks, audit ledgers, and CodeGraph are updated; no live-call result is required for deterministic closure.

### Human decision or risk acceptance

- Decision required: resolved.
- Options/consequences: the owner rejected a minimum credential length and rejected unauthenticated Rosetta access. The remaining approved control is protocol-aware removal with controlled failure when a collision is ambiguous; neither credential leakage nor silently corrupted output is accepted.
- Decision: configured credentials have no minimum-length requirement; Rosetta always requires a configured Gateway API key; ambiguous collisions fail closed.
- Authority/date: Project owner / 2026-07-20.
- Residual-risk owner: Project owner and Gateway transport/security owner.

### Remediation history

| Wave/head | Changes | Verification | Result | Coverage invalidated |
| --- | --- | --- | --- | --- |
| `20260720-1859` / `6d1bc7a` | owner decision recorded; no implementation change | source trace, direct GatewayConfig/redactor probes, related 22-test focused suite | Open / agent-fixable | PROVIDER-01, SIDE-01, SCN-03, SCN-04, CTRL-03, GP-003 |
| `20260720-1859` / working tree | replaced semantic rewriting with canonical collision detection; added complete-event raw SSE gating and source-compatible terminal errors; applied the same fail-closed contract to provider, auxiliary, Tavily, sidecar, and Admin return paths | focused `187 passed`; full `3576 passed, 5 skipped, 11 warnings`; lint and compatibility checks green; no live calls | Closed | None; deterministic coverage refreshed |

### Closure/reopen

- Closure evidence: canonical collision checks preserve credential-free objects/bytes unchanged; complete-event raw SSE gating prevents partial risk-event emission; every credential-bearing client path returns a controlled non-sensitive failure; focused/full/lint/compatibility evidence is green.
- Residual risk: exact-value detection cannot prevent arbitrary reversible/covert encoding by a malicious credential-bearing upstream. Very short/common credentials may intentionally cause supported responses to fail closed.
- Reopen trigger after future closure: credential syntax, parser, exact matching, replacement marker, SSE/raw handling, parsed-key handling, client inventory, or profile no-leak semantics change.

## AUD-018 — Admin model discovery trusts unvalidated upstream JSON shapes

- Severity: Should Plan
- Decision class: Agent-Fixable
- Status: Closed
- Confidence: High
- First detected run: `20260720-1859`
- Last updated run: `20260720-1859`
- Owner: Gateway/Admin owner

### Quality attributes and profile requirements

- Affected attributes: Reliability, security, operability, correctness.
- Profile/control requirement: provider content is untrusted; Admin provider/model operations are always-on critical surfaces and malformed upstream behavior must fail with bounded, non-sensitive errors.
- Violated invariant/outcome: syntactically valid JSON is treated as a valid provider model-list schema without checking its root, collection, member, or identifier types.

### Failure, abuse, or structural path

```text
Stimulus/trigger: Configured provider returns HTTP 200 with a syntactically valid JSON list,
                  scalar, object with non-list data/models, or a list containing non-object members.
Environment/preconditions: Authenticated Admin invokes upstream model discovery.
Path/components: fetch_upstream_models -> response.json -> redact_exact -> body.get -> m.get -> sort.
Expected response: Return a stable, non-sensitive Admin error and perform no partial mutation.
Observed failure: Type assumptions raise uncaught AttributeError/TypeError instead of the route's controlled error response.
```

### Impact and risk basis

- User/business/mission impact: Admin cannot complete model discovery and receives an internal failure for an ordinary untrusted-provider contract violation.
- Security/privacy/data/reliability impact: no secret exposure or persistent mutation was demonstrated; the primary risk is reliability and trust-boundary erosion.
- Likelihood/exploitability: malformed/custom provider responses are realistic; a malicious configured provider can trigger the path deterministically.
- Blast radius: one Admin request/provider discovery operation.
- Reversibility/recovery: retry after provider/config correction; no durable corruption shown.
- Systemic reach: bounded to Admin model-list normalization, with provider-specific OpenAI/Anthropic/Google branches.

### Scope and occurrences

| Component/path/symbol/workflow | Evidence | Why affected |
| --- | --- | --- |
| `gateway/admin/routes/config.py:975-1004` | catches JSON parse failure, then directly calls `body.get()` and `m.get()` | wrong-but-valid JSON escapes the intended error boundary |
| `tests/gateway/test_admin_model_discovery_cleanup.py` | covers invalid JSON, connection/cancellation, redirect, lifecycle, and credential reflection | no root/member/schema negative cases exist |

### Evidence

- Code/configuration evidence: no explicit mapping/list/string checks before normalization and sorting.
- Test/scanner evidence: related focused suite passes `22` tests while omitting valid-JSON/wrong-schema cases.
- Runtime/operations/incident evidence: monkeypatched bounded response returning `[{'id': 'm'}]` causes `AttributeError: 'list' object has no attribute 'get'` from `fetch_upstream_models`.
- Architecture/history evidence: IF-03 provider responses are untrusted; IF-04 Admin APIs should bound errors.
- Contradicting evidence considered: server middleware may convert the exception to a generic 500, but that does not satisfy the route-level controlled provider-error contract or give actionable Admin feedback.
- Gaps/assumptions: no browser/LAN or real provider exercise.

### Recommended direction

- Smallest credible remediation/control: validate the root mapping, provider-specific collection list, each member mapping, selected ID field string, and final homogeneous model-ID list; return one stable non-sensitive schema error for mismatches.
- Rollout/migration/rollback implications: none; valid provider responses retain current output. Avoid exposing raw malformed provider content in the error.
- Suggested priority: before relying on custom-provider Admin model discovery.

### Frozen acceptance criteria

- [x] Root JSON must be an object; otherwise return a stable non-sensitive Admin error without raising.
- [x] `data`/`models` must be arrays; every consumed member must be an object; missing/wrong types return the same bounded error class.
- [x] Provider-specific and shim-specific ID fields must resolve to strings; invalid/mixed values cannot reach `.startswith()`, sorting, or response serialization unchecked.
- [x] OpenAI-compatible, Anthropic, Google, and custom `model_id_field` success cases retain normalized output.
- [x] Tests cover list/scalar/null roots, missing/wrong collection types, null/scalar members, wrong ID types, ignored untrusted extra keys, and credential-reflecting payloads.
- [x] Connection, cancellation, redirect, size-limit, credential handling, client cleanup, and no-partial-mutation behavior remain intact; targeted and full deterministic checks pass.

### Human decision or risk acceptance

- Decision required: None.
- Options/consequences: N/A; existing trust-boundary policy supplies the intended outcome.
- Decision: Agent-fixable after remediation authorization.
- Authority/date: Existing approved profile / 2026-07-20.
- Residual-risk owner: Gateway/Admin owner.

### Remediation history

| Wave/head | Changes | Verification | Result | Coverage invalidated |
| --- | --- | --- | --- | --- |
| `20260720-1859` / `6d1bc7a` | discovery only; no implementation change | source trace, fake root-list probe, related 22-test focused suite | Open | AUTH-02, SCN-08, SCN-09 model-discovery slice |
| `20260720-1859` / working tree | added one provider-schema normalizer with stable mismatch response; validated object root, expected collection, object members, string fallback/shim IDs, and collision handling before Admin rendering | focused `187 passed`; full `3576 passed, 5 skipped, 11 warnings`; lint and compatibility checks green | Closed | None; deterministic Admin slice refreshed |

### Closure/reopen

- Closure evidence: negative-schema matrix returns the stable Admin error without exceptions, while supported provider/shim schemas remain normalized and sorted; full deterministic checks are green.
- Residual risk: browser/LAN UX, real provider pagination, and live external payloads remain unverified.
- Reopen trigger after future closure: provider model-list schema, shim `model_id_field`, Admin error contract, or model-discovery transport changes.

## AUD-002 — Compaction replacement persistence has no aggregate quota

- Severity: Must Fix
- Decision class: Agent-Fixable
- Status: Closed in remediation wave `20260719-1712`; confirmed in targeted re-audit `20260720-1107`
- Current state: `store_codex_compaction_mapping` now enforces replacement-size, per-principal and global row/byte quotas transactionally; the original failure description is retained as historical baseline evidence.
- Confidence: High
- First detected run: 20260719-1542
- Last updated run: 20260720-1107
- Owner: Gateway persistence owner / project owner

### Quality attributes and profile requirements

- Affected attributes: Security, reliability, privacy, cost, operability.
- Profile/control requirement: Durable state must be bounded for supported local/LAN use; do not tolerate unbounded supported-path state growth. Prompt/summary content may be retained only within explicit current policy bounds.
- Violated invariant/outcome: A valid authenticated client can cause multiple Rosetta compaction summaries to be retained for the rolling seven-day TTL without a configured aggregate row/byte ceiling.

### Failure, abuse, or structural path

```text
Stimulus/trigger: Repeated valid Codex Remote Compaction V2 triggers routed through Rosetta mode.
Environment/preconditions: Supported local/LAN Gateway; valid API key; compaction summary succeeds.
Path/components: codex_compaction.create_compaction_mapping -> PersistenceManager.store_codex_compaction_mapping -> codex_compaction_mappings.
Expected response: Persist only within an explicit per-principal/global row and byte budget, or fail closed with a bounded error.
Observed or supported failure: The storage method accepts arbitrary replacement_text, computes replacement_bytes, inserts and commits; only a rolling seven-day TTL is applied. No aggregate quota or max replacement length is enforced in this path.
```

### Impact and risk basis

- User/business/mission impact: local/LAN Gateway disk and database growth can degrade or stop the only supported service; compaction may retain large prompt-derived summaries.
- Security/privacy/data/reliability impact: prompt/source-sensitive plaintext is retained; a valid key or buggy loop can create repeated state and denial-of-wallet/storage pressure.
- Likelihood/exploitability: Medium; reachable only through valid routed compaction flow, but repeated loops are plausible.
- Blast radius: one principal can affect the shared SQLite data directory; aggregate impact persists until TTL cleanup or manual deletion.
- Reversibility/recovery: manual deletion is possible, but no backup/restore guarantee exists and disk exhaustion can impair normal cleanup.
- Systemic reach: persistence owner and all compaction routes; distinct from already-bounded encrypted tool mappings.

### Scope and occurrences

| Component/path/symbol/workflow | Evidence | Why affected |
| --- | --- | --- |
| `src/codex_rosetta/gateway/codex_compaction.py:create_compaction_mapping` | lines 304-326 | stores full summary replacement and uses only seven-day TTL |
| `src/codex_rosetta/observability/persistence.py:store_codex_compaction_mapping` | lines 1271-1303 | accepts arbitrary text and commits without quota validation |
| `src/codex_rosetta/observability/persistence.py:codex_compaction_mappings` | schema lines 401-414 | has expiry index but no row/byte budget columns/limits |
| `src/codex_rosetta/observability/persistence.py:tool_call_mappings` | schema/capacity methods lines 326-419, 1000-1259 | sibling store demonstrates stronger boundedness controls, making the gap concrete |

### Evidence

- Code/configuration evidence: `EVIDENCE.md` UNIT-004; current source lines above.
- Test/scanner evidence: `tests/gateway/test_codex_compaction.py` and `tests/gateway/test_persistence_sqlite.py` pass, but no aggregate compaction-cap test exists in the reviewed scope.
- Runtime/operations/incident evidence: no deployed environment or production data; no live API call made.
- Architecture/history evidence: the remediation wave aligned compaction mapping storage with the bounded encrypted tool-mapping pattern while preserving TTL cleanup.
- Contradicting evidence considered: seven-day expiry and request/upstream limits may bound individual summaries; they do not establish an aggregate row/byte ceiling for the table.
- Gaps/assumptions: exact upstream response size limit and operator filesystem capacity were not live-exercised.

### Recommended direction

- Smallest credible remediation/control: add explicit maximum replacement bytes and per-principal/global row/byte quotas using the same fail-closed pattern as encrypted tool mappings; ensure cleanup and quota accounting are transactional.
- Rollout/migration/rollback implications: no migration compatibility layer is promised; new limits can reject new mappings while allowing existing TTL cleanup. Add tests for per-principal/global overage and oversized one-row input.
- Suggested priority: Must Fix before first supported internal deployment/live compaction use.

### Frozen acceptance criteria

- [x] A single compaction replacement has a hard, tested byte limit.
- [x] Per-principal and global row/byte quotas are enforced transactionally.
- [x] Existing TTL cleanup remains fail-closed and does not bypass quotas.
- [x] Original compaction scenario succeeds below limits and returns a bounded error above limits.
- [x] Different API-key principals cannot consume or read each other’s mappings.
- [x] Focused persistence/compaction tests and full deterministic suite pass.
- [x] Persistent coverage and retention policy document the new bounds.

### Human decision or risk acceptance

- Decision required: None for the control shape; exact limits can be selected by the persistence owner within the profile.
- Options/consequences: add quota controls now, or explicitly accept local/LAN disk-growth risk until before first internal deployment.
- Decision: Owner authorized remediation after the baseline.
- Authority/date: 2026-07-19
- Residual-risk owner: Project owner

### Remediation history

| Wave/head | Changes | Verification | Result | Coverage invalidated |
| --- | --- | --- | --- | --- |
| 20260719-1712 | Added transactional single-row, per-principal and global row/byte quotas with deterministic regression tests | `make test`; focused persistence tests | Closed | No baseline rows/data migration is promised |

### Closure/reopen

- Closure evidence: `docs/audit/runs/20260719-1712/EVIDENCE.md`; persistence/compaction tests; full deterministic suite.
- Residual risk: limits are local/LAN policy limits; no backup/restore or long-run disk guarantee is claimed.
- Reopen trigger: any change that bypasses transactional quota accounting or introduces another durable compaction store.

---

## AUD-001 — Internal migration and legacy compatibility paths conflict with the prelaunch boundary

- Severity: Should Plan
- Decision class: Agent-Fixable
- Status: Closed in targeted re-audit `20260720-1107` (original gate added in `20260719-1712`)
- Current state: Rosetta-version config/state/internal API migration and deprecated retention aliases now fail closed or are removed; current protocol compatibility remains explicit.
- Confidence: High
- First detected run: 20260719-1542
- Last updated run: 20260720-1107
- Owner: Project owner with core/gateway owners

### Quality attributes and profile requirements

- Affected attributes: Modifiability, correctness, security, operability.
- Profile/control requirement: No project-version migration layer for old Rosetta config, persistence, or internal APIs is promised; current Codex/provider wire compatibility remains in scope.
- Violated invariant/outcome: The implementation still contains active Rosetta-version migration/legacy paths that preserve old config/state/API behavior without an approved supported boundary.

### Failure, abuse, or structural path

```text
Stimulus/trigger: New prelaunch code/config/schema is changed while legacy input/state remains accepted.
Environment/preconditions: Old config key, legacy JSONL/JSON data, old mapping schema, or deprecated Python alias is present.
Path/components: GatewayConfig/local_mode/Admin key routes/PersistenceManager/pipeline/converter aliases.
Expected response: Unsupported old Rosetta-version state/config/API is rejected or removed, while explicit Codex/provider protocol compatibility remains tested and documented.
Observed or supported failure: Multiple paths synthesize, migrate, backfill, or alias legacy behavior; scope and removal trigger are not centralized.
```

### Impact and risk basis

- User/business/mission impact: prelaunch changes carry avoidable compatibility branches and state transitions; future Codex/provider fixes can touch more paths.
- Security/privacy/data/reliability impact: migration code handles secrets, logs, and executable tool history; hidden fallback semantics can preserve stale or lossy state.
- Likelihood/exploitability: High for maintainers/agents encountering old artifacts; not an anonymous attack path under the supported boundary.
- Blast radius: config startup/admin mutation, persistence initialization, local mode, core public API and converters.
- Reversibility/recovery: removal is easier before first supported deployment/data set; after deployment, migration semantics become harder to change.
- Systemic reach: broad recurring pattern, not one local branch.

### Scope and occurrences

| Component/path/symbol/workflow | Evidence | Why affected |
| --- | --- | --- |
| `gateway/config.py` | lines 756-767 | accepts legacy `server.api_key` by synthesizing `api_keys` |
| `gateway/local_mode.py:ensure_codex_api_key` | lines 789-812 | migrates legacy single key into array |
| `gateway/admin/routes/keys.py` | lines 34-43 and 81-86 | exposes and migrates legacy key entries |
| `observability/persistence.py` | `_migrate_legacy`, column/schema migration, legacy mapping migration | imports old files/schema and discards/rewrites old mapping state |
| `pipeline.py` and converter modules | deprecated aliases and old API compatibility methods | keeps old internal/public call shapes alive |
| `gateway/admin`/catalog/docs | legacy field/type fallbacks | preserves old config and target-client shapes; some may be Codex protocol compatibility and require classification |

### Evidence

- Code/configuration evidence: `EVIDENCE.md` UNIT-004 and `rg -n -i 'legacy|migration|backward compat'` inventory.
- Test/scanner evidence: full deterministic suite passes, including legacy migration tests; passing tests prove current behavior, not that the behavior is still approved.
- Runtime/operations/incident evidence: no deployed data set exists.
- Architecture/history evidence: current project has an IR/provider compatibility mission, while the user explicitly removed Rosetta-version migration obligations.
- Contradicting evidence considered: some legacy fields are required to preserve current Codex 0.144.x/alpha.23 protocol behavior; those must not be removed without classification.
- Gaps/assumptions: exact complete inventory and canonical allowlist of protocol compatibility aliases are not yet finalized.

### Recommended direction

- Smallest credible remediation/control: build a one-time inventory and classification table: `current Codex/provider protocol compatibility`, `required current config`, or `Rosetta-version migration/legacy`. Remove or reject only the last class; add a test/CI guard against new unapproved internal migration paths.
- Rollout/migration/rollback implications: prelaunch/no deployed data makes removal low-risk; preserve no old-data migration guarantee as profile states.
- Suggested priority: schedule before first supported internal deployment.

### Frozen acceptance criteria

- [x] Every active `legacy`/`migration` path in the affected slices is inventoried with owner and classification.
- [x] Current Codex/provider protocol compatibility remains explicit and tested.
- [x] Rosetta-version config/persistence/internal-API migration paths are removed or fail closed.
- [x] No new compatibility migration layer can be added without an explicit profile decision.
- [x] Full deterministic suite and targeted compatibility checks pass after the inventory/removal wave.

### Human decision or risk acceptance

- Decision required: None for the approved boundary; protocol-vs-internal classification must be documented during remediation.
- Options/consequences: remove internal migration now, or accept growing prelaunch complexity and future removal cost.
- Decision: Owner authorized removal/rejection of Rosetta-version migration paths; current protocol compatibility remains in scope.
- Authority/date: 2026-07-19
- Residual-risk owner: Project owner

### Remediation history

| Wave/head | Changes | Verification | Result | Coverage invalidated |
| --- | --- | --- | --- | --- |
| 20260719-1712 | Rejected legacy single-key/config/type/schema/file paths, removed deprecated pipeline aliases and request-log retention alias, and updated tests | `make test`; config/persistence/profiling tests | Closed | Existing old state is rejected, not migrated |

### Closure/reopen

- Closure evidence: `docs/audit/runs/20260719-1712/EVIDENCE.md`; config/local-mode/admin/persistence/core changes; full deterministic suite.
- Residual risk: protocol compatibility remains intentionally supported; old Rosetta state/config is not recoverable by migration. A pair of private, unreachable historical mapping-migration definitions remains as cleanup debt and is not invoked by startup or runtime paths.
- Reopen trigger: any new Rosetta-version migration/compatibility path without an explicit profile decision.

---

## AUD-003 — Real-call runners lack a fail-closed developer-approval gate

- Severity: Should Plan
- Decision class: Agent-Fixable
- Status: Closed in targeted re-audit `20260720-1107` (original gate added in `20260719-1712`)
- Current state: every enumerated integration, relay, agentabi, and repository live-agent runner requires the shared exact approval marker before external work starts.
- Confidence: High
- First detected run: 20260719-1542
- Last updated run: 20260720-1107
- Owner: Project owner / live-test harness owner

### Quality attributes and profile requirements

- Affected attributes: Security, cost, operability, verification integrity.
- Profile/control requirement: Real Provider/Codex API calls are normal development behavior only after explicit developer approval; audit runs must never make real calls.
- Violated invariant/outcome: historical omission was that several live runners bypassed the approval boundary; the current entry-point inventory now fails closed before credentials and external endpoints.

### Failure, abuse, or structural path

```text
Stimulus/trigger: Agent or developer invokes a live/integration runner.
Environment/preconditions: Credentials/configuration are present; runner is reachable.
Path/components: scripts/run_gateway_integration.sh or tests/live_agent/*/run_live.py -> Codex/provider process/API.
Expected response: runner refuses unless an explicit one-shot opt-in/approval marker is present; deterministic tests remain network-free.
Observed or supported failure: scripts are directly executable; one live config sets approval_policy="never" and sandbox_mode="danger-full-access" for the isolated run; no in-harness human confirmation or fail-closed external-call gate exists.
```

### Impact and risk basis

- User/business/mission impact: accidental or autonomous runs can consume paid provider quota and produce unapproved real transcripts/tool side effects.
- Security/privacy/data/reliability impact: credentials and prompt/tool data cross the local harness boundary; evaluator evidence can be contaminated by unintended live state.
- Likelihood/exploitability: Medium; requires a runner invocation and credentials, but agent autonomy makes accidental invocation plausible.
- Blast radius: selected provider/Codex account and local run artifacts; no release secret path observed.
- Reversibility/recovery: API spend/transcript exposure cannot be fully undone.
- Systemic reach: all live/integration runners, not just one scenario.

### Scope and occurrences

| Component/path/symbol/workflow | Evidence | Why affected |
| --- | --- | --- |
| `scripts/run_gateway_integration.sh` | lines 21-40, 70-82 | default matrix and child scripts can invoke a running Gateway/upstream |
| `tests/live_agent/context_compaction/run_live.py` | lines 23-30, 72-90 | reads configured sources and builds a real Codex config |
| `tests/live_agent/deferred_tool_search/prepare_run.py` | lines 36-59, 68-96 | invokes `codex` in isolated run root and writes credentials/config |
| `Makefile` | `test` excludes integration but `test-integration`/`test-gateway` are callable | separation exists but approval is procedural, not mechanical |

### Evidence

- Code/configuration evidence: current scripts and `docs/dev/agent-tool-testing.md`.
- Test/scanner evidence: deterministic live configuration/fixture tests passed; no live run executed.
- Runtime/operations/incident evidence: explicitly unavailable by user policy.
- Contradicting evidence considered: `make test` excludes integration and current audit used only deterministic tests; this prevents accidental calls in this run but does not protect direct runner invocation.
- Gaps/assumptions: exact credential availability and developer workflow are intentionally not inspected/used.

### Recommended direction

- Smallest credible remediation/control: add a mandatory explicit opt-in variable/CLI flag with a clear approval value and fail closed in every runner; add deterministic tests that absent opt-in cannot spawn Codex/provider subprocesses or network clients. Keep audit profile commands on the no-live path.
- Rollout/migration/rollback implications: no impact to deterministic tests; developers must opt in per run/provider.
- Suggested priority: before autonomous agent execution or adding more live suites.

### Frozen acceptance criteria

- [x] Every audited real-call runner fails closed without explicit opt-in.
- [x] Approval marker is scoped to one run and does not expose secret values.
- [x] Deterministic tests prove the no-opt-in path performs no external call.
- [x] Live artifacts remain credential-free and ignored by Git.

### Human decision or risk acceptance

- Decision required: None; the user already approved the gate requirement.
- Options/consequences: enforce in runner code, or retain a procedural-only gate with residual spend/privacy risk.
- Decision: Owner authorized a mandatory fail-closed developer approval marker for live tests.
- Authority/date: 2026-07-19
- Residual-risk owner: Project owner

### Remediation history

| Wave/head | Changes | Verification | Result | Coverage invalidated |
| --- | --- | --- | --- | --- |
| 20260719-1712 | Added shared exact-marker gate before credentials/run roots/subprocesses and deterministic fail-closed tests | live configuration contract tests; `make test` | Closed | No real-call trajectory was run |
| 20260720-1107 | Extended the gate to every integration, relay, agentabi, and agent-launch entry point and re-audited ordering | full deterministic suite; live-call contract tests | Closed | No real-call trajectory was run |

### Closure/reopen

- Closure evidence: `docs/audit/runs/20260719-1712/EVIDENCE.md`; `gateway/live_gate.py`; live runner contract tests.
- Residual risk: approved development runs can still incur real API cost or side effects; audit continues to exclude them.
- Reopen trigger: any new external-call runner without the shared gate.

---

## AUD-005 — URL-authoritative provider option semantics were not enforced

- Severity: Should Plan
- Decision class: Needs Decision (decision now recorded)
- Status: Closed in targeted re-audit `20260720-1107` (original URL semantics fixed in `20260719-1712`)
- Current state: Admin saves only URL/protocol/key data; runtime derives the preset/custom presentation and shim from the URL, with unmatched URLs allowed as `custom`.
- Confidence: High
- First detected run: 20260719-1542
- Last updated run: 20260720-1107
- Owner: Project owner / Gateway product owner

### Quality attributes and profile requirements

- Affected attributes: Security, modifiability, operability, correctness.
- Profile/control requirement: provider vendor/variant is presentation-only; exact preset URL matches render the preset, unmatched URLs render `custom` and remain allowed within the local/LAN operator boundary.
- Decision boundary: resolved by owner on 2026-07-19; `api_type` remains the persisted transport protocol while URL is the provider-option authority.

### Failure, abuse, or structural path

```text
Stimulus/trigger: Admin/config supplies a custom or unknown provider entry/base URL.
Environment/preconditions: Admin-authenticated mutation or local config file.
Path/components: Admin provider UI -> GatewayConfig provider resolution -> build_provider_info unknown fallback -> ProviderInfo URL/auth.
Expected response: behavior matches an explicit preset-only policy: reject unsupported provider identity/endpoint, or document and test the override as supported.
Observed or supported failure: UI exposes `Custom`; config accepts unknown types; provider factory falls back to Bearer auth and generic `{base_url}/` URL template.
```

### Impact and risk basis

- User/business/mission impact: supported surface is broader than profile, making compatibility/test and support claims ambiguous.
- Security/privacy/data/reliability impact: arbitrary endpoint selection can send configured provider credentials and prompt traffic directly to the selected URL; automatic HTTP redirects are now prohibited. This is an Admin/misconfiguration path, not anonymous SSRF under the current boundary.
- Likelihood/exploitability: Medium for operator error or compromised Admin; low for unauthenticated clients.
- Blast radius: provider credential and prompt traffic for the configured route.
- Reversibility/recovery: config can be changed, but accidental egress may already expose data/credentials.
- Systemic reach: registry, config parser, Admin UI, docs, and tests.

### Scope and occurrences

| Component/path/symbol/workflow | Evidence | Why affected |
| --- | --- | --- |
| `gateway/providers.py:build_provider_info` | lines 130-144 | unknown/custom provider gets generic Bearer/URL fallback |
| `gateway/admin/admin.html` | lines 1310-1323 | explicit Custom vendor and custom variants are user-visible |
| `gateway/config.py` | lines 402-419 | provider/API type/shim fallback resolution accepts name/type forms |

### Evidence

- Code/configuration evidence: current provider factory/config/admin UI.
- Test/scanner evidence: provider/config/admin tests pass and therefore confirm the behavior is intentional/current.
- Runtime/operations/incident evidence: no external call was made; no deployment exists.
- Contradicting evidence considered: the old baseline exposed a broader Custom surface without a documented source of truth; the remediation now documents and tests that custom URLs are intentionally allowed.
- Gaps/assumptions: no external egress/provider-quality test was run; those remain outside audit scope.

### Recommended direction

- Smallest credible remediation/control: use URL/protocol matching as the single runtime source of truth, keep exact preset matches selectable, and render unmatched URLs as `custom` without persisting vendor/variant metadata.
- Rollout/migration/rollback implications: prelaunch/no deployed data makes boundary tightening low-risk; no old config migration promise.
- Suggested priority: reopen only if the owner changes the custom endpoint policy or provider metadata becomes persisted.

### Frozen acceptance criteria

- [x] Product decision records that exact preset URL matches render preset options and unmatched URLs render allowed `custom`.
- [x] Provider `type`/`shim` metadata is not persisted as the source of routing truth; URL/protocol resolution is canonical.
- [x] Admin UI, docs, config handling, and tests use the same URL-authoritative semantics.
- [x] No external egress claim is made by this deterministic audit.

### Human decision or risk acceptance

- Decision required: No further decision for the approved semantics.
- Options/consequences: exact preset URLs render preset options; unmatched URLs render `custom` and remain allowed. This preserves operator flexibility while keeping URL authoritative.
- Decision: URL is authoritative; `api_type` remains the transport protocol field because one URL may serve Chat or Responses.
- Authority/date: 2026-07-19
- Residual-risk owner: Project owner

### Remediation history

| Wave/head | Changes | Verification | Result | Coverage invalidated |
| --- | --- | --- | --- | --- |
| 20260719-1712 | Removed persisted vendor/variant writes, derived shims from exact URL/protocol matches, and added custom URL tests | config/Admin tests; full deterministic suite | Closed | No external egress or provider-quality claim |
| 20260720-1107 | Aligned Admin profile derivation and recorded the custom HTTP(S) egress decision in the profile | full deterministic suite; provider/Admin contract tests | Closed | Custom egress remains owner-accepted local/LAN risk |

### Closure/reopen

- Closure evidence: `docs/audit/runs/20260719-1712/EVIDENCE.md`; `gateway/config.py`; Admin config route/UI tests.
- Residual risk: custom URLs are intentionally allowed within local/LAN operator scope; no public SSRF/account-security claim is made.
- Reopen trigger: owner changes custom endpoint policy or any code persists provider option metadata.

---

## AUD-006 — Live-call approval gate did not cover every real-call entry point

- Severity: Should Plan
- Decision class: Agent-Fixable
- Status: Closed in third omission-remediation re-audit `20260720-1554`
- First detected run: `20260719-1802`
- Last updated run: `20260720-1554`
- Owner: Project owner / live-test harness owner

### Failure and closure

The omission audits found SDK/REST E2E, agentabi, relay, agent-launch paths, all 24 executable examples, and later `dev_scripts/test_roundtrip_live.py` could reach credentials, subprocesses, or network clients without the shared gate. The remediation added the exact-marker fail-closed gate before sensitive work. Example directories and `dev_scripts/*live*.py` are dynamically inventoried so the established live-script convention cannot silently fall outside a static list.

- Closure evidence: `35521ab`, `5d95668`; `scripts/require_live_call_approval.sh`; `src/codex_rosetta/gateway/live_gate.py`; live/integration/example/dev entry points; `tests/live_agent/test_live_agent_configuration_contract.py`.
- Residual risk: an approved development run can still incur real API cost or side effects; audit runs remain deterministic-only.
- Reopen trigger: any new real-call runner without the shared gate.

## AUD-007 — Admin profile derivation depended on stripped provider metadata

- Severity: Should Plan
- Decision class: Agent-Fixable
- Status: Closed in targeted re-audit `20260720-1107`
- First detected run: `20260719-1802`
- Last updated run: `20260720-1107`
- Owner: Gateway/Admin owner

### Failure and closure

The Admin API strips presentation-only provider metadata, while the UI still attempted to read it when selecting a tool profile. The API now returns runtime-derived `default_tool_profile` and validation state; the UI renders those fields and uses URL/protocol semantics without writing provider options back to config.

- Closure evidence: `gateway/admin/routes/config.py`, `gateway/admin/admin.html`, i18n labels, and Admin/config contract tests.
- Residual risk: browser and deployed LAN behavior remain unexercised in this audit.
- Reopen trigger: changing the Admin response contract or persisting provider presentation options.

## AUD-008 — Audit ledgers contained contradictory closure and rotation state

- Severity: Should Plan
- Decision class: Agent-Fixable
- Status: Closed in third omission-remediation re-audit `20260720-1554`
- First detected run: `20260719-1802`
- Last updated run: `20260720-1554`
- Owner: Audit owner

### Failure and closure

`FINDINGS.md`, `COVERAGE.md`, the approved profile, README, and prior run text disagreed with current code and owner decisions. This run reconciles status tables, evidence links, coverage freshness, control baseline, system-map notes, and the rotation queue against exact code head `de9c96b`.

- Closure evidence: this ledger set and `docs/audit/runs/20260720-1554/`.
- Residual risk: ledger correctness is repository-local and depends on future runs preserving the same reconciliation step.
- Reopen trigger: a future remediation run leaves contradictory persistent statuses.

## AUD-009 — Missing or backend-unrecognized `api_type` runtime fallback semantics

- Severity: Should Plan
- Decision class: Decision Recorded
- Status: Closed in third omission-remediation re-audit `20260720-1554`
- First detected run: `20260719-1802`
- Last updated run: `20260720-1554`
- Owner: Project owner / Gateway config owner

### Decision and closure

Owner decision superseding the `20260720-1107` conclusion: only an exact string present in the backend support map counts as `api_type`. Missing, empty, non-string, and backend-unrecognized values are all treated as absent. Runtime compares the authoritative URL against preset protocol URLs and selects the first supported protocol in `responses`, `chat`, `anthropic`, `google` order; an unmatched custom URL defaults to `responses`. Each active provider emits a terminal warning once per config load; Admin renders the inferred value. Neither the input dictionary nor the config file is written back.

- Closure evidence: `3f2d044`; `gateway/config.py`; Admin config route; config/Admin tests covering exact supported values, unrecognized/falsy/non-string values, preset/custom selection, warning count, Tool Profile derivation, and no write-back.
- Residual risk: an unmatched or mistyped custom URL defaults to Responses until the operator corrects the URL or declares `api_type`; no compatibility migration layer is introduced.
- Reopen trigger: changing the backend support map, protocol order, URL-authority, warning behavior, or inferred-value persistence.

## AUD-010 — SQLite schema validation omitted complete index attributes

- Severity: Should Plan
- Decision class: Agent-Fixable
- Status: Closed in omission-remediation re-audit `20260720-1239`
- First detected run: `20260719-1802`
- Last updated run: `20260720-1239`
- Owner: Persistence owner

### Failure and closure

Startup validation checks expected column names/types, NOT NULL flags, primary-key positions, and required index names, column order, uniqueness, SQLite origin, and partial-index flag. Incompatible existing schemas fail closed before runtime writes; focused tests cover missing primary key, wrong columns, unique indexes, and partial indexes.

- Closure evidence: `ec8419b`; `observability/persistence.py`; `tests/gateway/test_persistence_sqlite.py`.
- Residual risk: no restore, deployment, or long-run disk stress evidence.
- Reopen trigger: schema/table/index changes without updated validation and tests.

## AUD-011 — Arbitrary HTTP(S) custom URL egress requires an owner boundary decision

- Severity: Should Plan
- Decision class: Decision Recorded
- Status: Direct-egress and explicit provider-redirect risk accepted in `20260720-1554`
- First detected run: `20260719-1802`
- Last updated run: `20260720-1554`
- Owner: Project owner

### Decision and residual risk

Owner decision: arbitrary unmatched HTTP(S) custom URLs are allowed and may receive configured upstream API keys. Redirects are denied by default, but an operator may explicitly enable them per provider; the same policy applies to that provider's model discovery. This is accepted only for the declared local/trusted-LAN deployment boundary. The profile and system map state that direct custom egress and enabled redirect targets can expand SSRF/key-disclosure exposure; no public deployment, account-security, or preset-only egress guarantee is claimed.

- Decision evidence: `docs/audit-profile.md`, `docs/audit/SYSTEM-MAP.md`, and `docs/audit/runs/20260720-1554/REPORT.md`.
- Reopen trigger: any public deployment/security claim or change to custom URL or redirect policy.

## AUD-012 — Upstream redirects could expand credential egress

- Severity: Must Fix
- Decision class: Agent-Fixable
- Status: Closed in third omission-remediation re-audit `20260720-1554`
- First detected run: `20260720-1239`
- Last updated run: `20260720-1554`
- Owner: Gateway transport owner

### Failure and closure

The first repair set the primary upstream pool to reject redirects, but the shared auxiliary request helper still inherited the vendored client's redirect allowance. The final policy now has explicit ownership: provider request/stream/passthrough clients default to `max_redirects=0`, separate pool entries isolate an explicit per-provider opt-in, model discovery inherits that provider policy, and all non-provider auxiliary calls force `max_redirects=0`. Loopback tests verify default provider and auxiliary redirect targets receive no request; a separate test proves the operator opt-in follows the redirect.

- Closure evidence: `3e327c8`; `gateway/transport/http/client_pool.py`; `gateway/transport/http/transport.py`; provider/Admin config paths; transport and model-discovery tests.
- Residual risk: explicit `allow_redirects` may forward provider authorization to a redirect target; the operator-approved direct custom URL and configured proxy also receive the credential. DNS rebinding and proxy behavior were not live-tested.
- Reopen trigger: any client/transport/config change that weakens default denial, auxiliary isolation, or explicit opt-in ownership.

## AUD-014 — Tavily response or exception content could reflect the API key

- Severity: Must Fix
- Decision class: Agent-Fixable
- Status: Closed in third omission-remediation re-audit `20260720-1554`
- First detected run: `20260720-1554`
- Last updated run: `20260720-1554`
- Owner: Gateway search owner

### Failure and closure

Tavily uses the configured API key only as Bearer request authentication, but untrusted success/error content or a transport exception could reflect that value into model results or diagnostics. `TavilyHTTPClient` now applies the shared recursive `SecretRedactor` to parsed success data, bounded HTTP error text, and transport exception messages. The re-audit also removed the original exception cause so a full traceback cannot reveal a redacted transport message's underlying secret.

- Closure evidence: `b7542d2`, `de9c96b`; `gateway/web_search.py`; transport-limit tests covering nested success fields, HTTP errors, and detached transport exceptions.
- Residual risk: no real Tavily call was made; correctness is deterministic against documented response fields and adversarial local fixtures.
- Reopen trigger: Tavily authentication/response handling, redaction logic, or diagnostic exception propagation changes.

## AUD-015 - Provider and web-run sidecar return paths can reflect configured credentials

- Severity: Must Fix
- Decision class: Agent-Fixable
- Status: Closed in targeted remediation re-audit `20260720-1606`
- Current state: every credential-bearing provider, sidecar, auxiliary, and Admin model-discovery return path in scope removes configured credential values from values and object keys before client/model/diagnostic use; secret-bearing causes are detached.
- Confidence: High
- First detected run: `20260720-1606`
- Last updated run: `20260720-1606`
- Owner: Gateway transport and search owners

### Quality attributes and profile requirements

- Affected attributes: Security, privacy, correctness, operability.
- Profile/control requirement: provider API keys and optional sidecar tokens are crown jewels; configured token values must be redacted; provider/tool output is untrusted; credential leakage is not tolerated on the supported local/LAN path (`docs/audit-profile.md:21`, `:31`, `:34`, `:36`, `:56`, `:67`, `:99`).
- Violated invariant/outcome: a credential placed on an outbound request must not re-enter downstream/model output, errors, traces, logs, persistence, or exception chains through untrusted return content.

### Failure, abuse, or structural path

```text
Stimulus/trigger: A configured provider or web-run sidecar reflects the credential
                  it received into a success body, error body, SSE chunk, or exception.
Environment/preconditions: Supported local/LAN Gateway; attacker controls or compromises
                           the configured upstream, or an upstream/proxy emits reflected data.
Path/components: ProviderInfo/sidecar bearer header -> untrusted upstream return ->
                 proxy or Codex Search bridge -> downstream client/model/diagnostic state.
Expected response: Remove the exact configured wire credential at the client/transport return
                   boundary while preserving non-secret status, schema, and content.
Observed failure: Provider raw passthrough and sidecar success/error/exception paths return the
                  reflected value unchanged; the sidecar retains the original exception cause.
```

### Evidence and reachability

- Provider authentication selects and emits a wire key at `src/codex_rosetta/gateway/transport/provider_info.py:91-93`.
- Non-streaming Responses passthrough returns raw success and error bodies at `src/codex_rosetta/gateway/proxy.py:1495-1535`; converted errors return the same raw content at `:1623-1648`.
- Same-protocol streaming yields upstream bytes at `src/codex_rosetta/gateway/proxy.py:2138-2165`, and stream-header errors are returned at `:2315-2367`. These paths have diagnostic redactors but no downstream return redactor.
- The sidecar sends its bearer and returns success data or propagates reflected error/exception data at `src/codex_rosetta/gateway/web_run_sidecar.py:82-120`, `:135-184`.
- Codex Search exposes sidecar results and mapped errors through `src/codex_rosetta/gateway/codex_auxiliary.py:313-341` and `src/codex_rosetta/gateway/codex_search.py:100-104`, `:354-364`, `:527-544`, `:870-897`.
- Deterministic fake results at current HEAD:

```text
sidecar_success_reflects_token True
sidecar_error_reflects_token True
sidecar_exception_chain_reflects_token True cause_retained True
provider_200_reflects_token True
provider_401_reflects_token True
```

- `tests/gateway/test_web_run_sidecar.py:18-120` and `tests/gateway/test_responses_passthrough.py:59-114` verify ordinary behavior/raw preservation but contain no reflected-configured-token oracle.

### Impact and risk basis

- A malicious or compromised configured upstream can disclose its bearer/API key to any authenticated downstream client and can inject it into subsequent model/tool context.
- The same reflected data can reach traces, request/error persistence, or exception diagnostics, increasing retention and secondary disclosure risk.
- The supported boundary is local/trusted LAN rather than public, but the profile explicitly includes compromised/mistaken LAN clients and malicious provider content and classifies confirmed credential exposure as `Must Fix`.

### Existing controls and why they are insufficient

- `UpstreamErrorLogState`, body logs, persistence, metrics, and trace state apply `SecretRedactor` to selected diagnostic copies. They do not transform the downstream response returned by proxy handlers.
- Tavily now redacts success/error/transport content inside `TavilyHTTPClient`, proving the intended pattern, but provider transport and web-run sidecar clients are sibling omissions.
- HTTP size limits and redirect denial bound transport behavior but do not remove a credential reflected by the configured destination itself.

### Acceptance criteria

1. Every credential-bearing provider and web-run sidecar return path removes every configured credential actually sent on the request from success bodies, HTTP errors, stream data, transport errors, and retained exception/cause data before any downstream/model/diagnostic exposure.
2. Both same-protocol passthrough and converted provider paths are covered for non-streaming and streaming behavior. Streaming protection must handle a credential split across arbitrary upstream chunks, not only a token contained in one chunk.
3. Sidecar `execute` and `search` success objects, structured errors, plain errors, invalid payload diagnostics, and transport exceptions are covered; raw secret-bearing causes are detached or sanitized recursively.
4. Status codes, provider error schema, SSE ordering/framing, and all non-secret content remain compatible. Raw passthrough may change only where needed to satisfy the configured-token invariant.
5. Client responses/model context, body/error logs, traces, metrics, and persisted error/request data are all asserted secret-free with nested and embedded reflection fixtures.
6. Regression tests cover ordinary single keys and all rotated wire keys from AUD-016 without making real calls.

### Verification required for closure

- Focused unit tests for sidecar success/error/exception and provider success/error/stream paths, including split-token SSE chunks and exception tracebacks.
- Existing passthrough, conversion, streaming, logging, persistence, and redaction suites.
- Full deterministic suite, lint/type checks, and CodeGraph sync if implementation ownership crosses indexed modules.
- No real provider/sidecar call is required for deterministic closure; live behavior remains a separate approved-development evidence gap.

### Residual risk and invalidation

- Exact-value redaction intentionally changes otherwise legitimate content equal to a configured credential; this follows the approved no-leak policy.
- Reopen on any new credential-bearing client, authentication scheme, raw passthrough path, stream framing implementation, or exception propagation change.

### Closure evidence

- `CredentialRedactingTransport` applies exact configured-credential removal to provider passthrough and converted responses, parsed streams, raw SSE bytes across arbitrary chunk splits, HTTP errors, and detached transport exceptions before proxy consumers observe them.
- `WebRunSidecarHTTPClient`, Codex auxiliary provider calls, and Admin `fetch_upstream_models` apply the same configured-value invariant to success objects, structured/plain/invalid errors, model IDs, logging, and transport failures. Admin discovery derives its redactor from `pinfo.credential_values`, including the key actually selected on the wire.
- `SecretRedactor.redact()` and `redact_exact()` redact configured values in string and bytes dictionary keys as well as values. If multiple source keys redact to the same key, normal deterministic dictionary semantics apply: the later source item wins, and no original secret key is retained.
- Regression tests cover success and HTTP errors, passthrough and converted paths, streaming and non-streaming paths, arbitrary cross-chunk raw SSE, nested values, adversarial object keys, every rotated dummy key, traces/logs/metrics/persistence redactors, sidecar execute/search, Admin model discovery, and cause-free exceptions without real calls.
- Independent phase-separated verification: focused `158 passed`; `conda run -n llm-rosetta make lint` passed; `conda run -n llm-rosetta make test` reported `3542 passed, 5 skipped, 11 warnings`.

All six frozen acceptance criteria are satisfied by current code plus deterministic verification. Status/error schemas and non-secret response/SSE bytes remain covered; only exact configured-credential occurrences are replaced.

## AUD-016 - Rotated provider wire keys are absent from the exact-value redaction inventory

- Severity: Must Fix
- Decision class: Agent-Fixable
- Status: Closed in targeted remediation re-audit `20260720-1606`
- Current state: provider rotation and redaction consume the same canonical ordered `KeyRing` values, while the raw CSV and every selectable key reach all runtime redactors atomically.
- Confidence: High
- First detected run: `20260720-1606`
- Last updated run: `20260720-1606`
- Owner: Gateway config, transport, and observability owners

### Quality attributes and profile requirements

- Affected attributes: Security, privacy, correctness, modifiability, operability.
- Profile/control requirement: every configured provider token value used on the supported path must be redacted from diagnostics and untrusted return data; key rotation is an explicitly audited background/runtime behavior.
- Violated invariant/outcome: the redaction inventory must contain the same canonical credential values the transport can select and send on the wire.

### Failure, abuse, or structural path

```text
Stimulus/trigger: A provider is configured with "key-A, key-B" and reflects the active key
                  in an ordinary message or payload field.
Environment/preconditions: Provider key rotation enabled through the documented comma-delimited
                           api_key value; logging, tracing, persistence, or response sanitization active.
Path/components: collect_token_values stores one raw CSV string -> KeyRing independently splits it ->
                 provider sends key-A -> exact-value redactor knows only "key-A, key-B".
Expected response: Every trimmed credential selectable by KeyRing is registered with every runtime redactor.
Observed failure: The active individual key survives sanitization in ordinary fields.
```

### Evidence and reachability

- `_add_token` and `collect_token_values` retain the provider `api_key` as one exact string (`src/codex_rosetta/observability/redaction.py:43-60`).
- `SecretRedactor` replaces only registered exact values (`src/codex_rosetta/observability/redaction.py:69-94`).
- `KeyRing` independently splits commas and trims individual wire keys (`src/codex_rosetta/gateway/transport/provider_info.py:28-45`); `ProviderInfo.auth_headers()` selects one of them (`:91-93`).
- `GatewayConfig.token_values` feeds error/body logs at startup and trace, persistence, metrics, and all redactors during hot reload (`src/codex_rosetta/gateway/config.py:716-717`; `src/codex_rosetta/gateway/app.py:1092-1097`; `src/codex_rosetta/gateway/admin/routes/_shared.py:145-215`).
- Deterministic current-HEAD probe:

```text
registered_exact_individual_keys False False
registered_raw_csv True
active_rotated_key rotate-secret-A
diagnostic_reflects_active_key True
```

- `tests/observability/test_redaction.py:12-28` covers a single provider key; no test joins key-ring parsing to runtime redactor registration.

### Impact and risk basis

- Any rotated provider key can survive configured-token redaction when reflected in a normal error/message field, allowing it into logs, traces, metrics, persistence, and the return boundaries addressed by AUD-015.
- The mismatch affects every redaction consumer because they all receive the same incomplete `GatewayConfig.token_values` set.
- Fixing only one logger or only AUD-015 would leave the root parser divergence intact.

### Existing controls and why they are insufficient

- Token-shaped fields and `Bearer ...` strings are redacted structurally, but a malicious reflection can place the active key in any ordinary string field.
- Sorting registered tokens longest-first prevents overlap corruption only for values actually present in the set; it cannot infer the keys hidden inside the CSV string.
- Atomic hot reload propagates the token set consistently, but it consistently propagates the incomplete set.

### Acceptance criteria

1. One canonical parser/contract defines provider credential values for both rotation and redaction; it trims whitespace, ignores empty entries, preserves rotation order, and exposes every value that can be sent on the wire to the redaction inventory.
2. The raw configured CSV value may remain registered, but every individual selectable key must also be registered. Single-key behavior and existing provider configuration semantics remain unchanged.
3. Startup and atomic hot reload update error logs, body logs, stream traces, metrics, persistence/error dumps, and AUD-015 return-boundary redactors with the complete new set before the new provider state becomes active.
4. Tests exercise every rotation position, surrounding whitespace, empty segments, duplicate/overlapping key values, ordinary nested reflection fields, and removal of old keys after successful hot reload without exposing secrets in test failure output.
5. No logging, Admin response, or persistence artifact reveals the canonical parsed key list.

### Verification required for closure

- Unit tests that bind config parsing, `KeyRing`, `ProviderInfo.auth_headers`, and each prepared runtime redactor to the same dummy rotated credentials.
- Hot-reload atomicity/rollback tests and the focused/full deterministic validation required for AUD-015.
- No live provider call is required; real key-rotation behavior remains an explicitly separate development evidence gap.

### Residual risk and invalidation

- Providers may support future credential formats that legitimately contain commas; such a format would require an owner-visible configuration contract change rather than an implicit parser exception.
- Reopen on provider credential syntax, key-ring selection, config substitution, hot-reload activation, or redaction-consumer changes.

### Closure evidence

- `KeyRing` parses the configured CSV once into an ordered tuple, trimming whitespace, ignoring empty entries, and preserving duplicates and selection order. `ProviderInfo.credential_values` exposes that same tuple; there is no second rotation parser.
- `GatewayConfig` retains the raw configured CSV through the existing token collector and additionally registers every `ProviderInfo.credential_values` entry. Environment-resolved provider credentials therefore follow the same source of truth.
- Startup and Admin config prepare/activate/rollback tests prove the complete new set reaches stream trace, upstream/body logs, metrics, persistence, and provider-return redactors atomically; rollback retains the old state and successful activation removes old keys.
- Tests cover every rotation position, whitespace, empty entries, duplicates, prefix overlap, nested value and object-key reflection, raw CSV retention, environment fallback, and absence of an externally exposed parsed-key list.
- Independent phase-separated verification: focused `158 passed`; `conda run -n llm-rosetta make lint` passed; `conda run -n llm-rosetta make test` reported `3542 passed, 5 skipped, 11 warnings`.

All five frozen acceptance criteria are satisfied deterministically. No live provider rotation, external log sink, or production persistence artifact was exercised.

## AUD-004 — Mutable build inputs and missing artifact provenance are accepted release debt

- Severity: Track as Debt
- Decision class: Needs Decision
- Status: Risk Accepted
- Confidence: High
- First detected run: 20260719-1542
- Last updated run: 20260719-1542
- Owner: Project owner

### Quality attributes and profile requirements

- Affected attributes: Security, operability, supply chain, modifiability.
- Profile/control requirement: manual GitHub Release only; no current signing/SBOM/provenance guarantee; revisit before any stronger public release/security claim.
- Violated invariant/outcome: none under the current explicitly limited pre-release commitment; artifact integrity is weaker than a mature release baseline.

### Failure, abuse, or structural path

```text
Stimulus/trigger: Build or manual release resolves mutable external action/base/dependency inputs.
Environment/preconditions: CI or local release build.
Path/components: Actions major tags, pip latest/unlocked optional dependencies, Docker base tag, manual artifact handling.
Expected response: future stronger release baseline pins/verifies inputs and records provenance.
Observed or supported failure: current controls do not provide immutable digest pinning, lockfile, SBOM, signature, or attestation.
```

### Impact and risk basis

- User/business/mission impact: a compromised/mutated build input could alter a manually released artifact.
- Security/privacy/data/reliability impact: supply-chain compromise can affect credentials and all gateway users; no automated publish path reduces immediate blast radius.
- Likelihood/exploitability: Low-to-medium; depends on external tag/index compromise.
- Blast radius: CI artifacts/manual release output.
- Reversibility/recovery: manual release withdrawal/rebuild; no signing claim.
- Systemic reach: CI, Docker, pyproject optional dependencies and release process.

### Evidence

- `.github/workflows/ci.yml` uses `actions/checkout@v6` and `actions/setup-python@v6`; SDK monitor upgrades SDKs to latest and uses `actions/github-script@v9` with `issues: write`.
- `docker/Dockerfile` uses `python:3.14.6-alpine` tag and resolves gateway/profiling dependencies at build time.
- `pyproject.toml` has no lockfile/provenance/signing/SBOM control; Makefile disables automated package/Docker push.
- Local lint/test/build/contract/release checks pass.

### Recommended direction

- Smallest credible remediation/control: define a release integrity baseline (digest-pinned Actions/base, dependency lock or verified constraints, SBOM, provenance/signing and review ownership) before claiming it.
- Rollout/migration/rollback implications: changes release process only; no runtime migration.
- Suggested priority: before first public release or external security claim.

### Frozen acceptance criteria

- [ ] Manual release remains the only publication path unless explicitly changed.
- [ ] External build inputs have an owner and immutable verification policy.
- [ ] Artifact provenance/signing/SBOM requirement is either implemented or explicitly risk-accepted with a review trigger.

### Human decision or risk acceptance

- Decision required: none for current pre-release operation; stronger release controls are deferred by profile.
- Decision: Risk accepted until first public/stronger release claim.
- Authority/date: Project owner / 2026-07-19
- Residual-risk owner: Project owner

### Remediation history

| Wave/head | Changes | Verification | Result | Coverage invalidated |
| --- | --- | --- | --- | --- |
| None | No remediation authorized | local deterministic gates pass | Risk Accepted | — |

### Closure/reopen

- Closure evidence: not applicable; this is accepted debt, not a resolved finding.
- Residual risk: mutable supply-chain inputs and absent provenance remain.
- Reopen trigger: public release, production deployment, signing/SBOM promise, or CI permission expansion.

## AUD-022 — Leading whitespace bypasses the Responses stream JSON semantic gate

- Severity: Must Fix
- Decision class: Agent-Fixable
- Status: Closed
- First detected run: `20260721-1148`
- Owner: Gateway transport/security owner

### Evidence and failure path

`src/codex_rosetta/gateway/transport/credential_semantics.py:132-141` calls
`rstrip()` on the bounded argument buffer, then checks `startswith("{")` on the
untrimmed text. A legal JSON value beginning with spaces therefore skips the
semantic `SecretRedactor.contains_json_semantic()` call. A deterministic probe
with fragments `  {"value":"\\u00` and `73ecret"}` leaves a complete
credential-bearing JSON object in the buffer without raising
`SecretCollisionError`.

### Impact and invariant

An active provider can reflect its configured credential through a Responses
function/custom argument stream using Unicode escapes and leading JSON whitespace.
This violates the profile invariant that an active-provider credential must not
cross the return boundary, including raw or parsed SSE.

### Acceptance criteria

1. JSON whitespace is normalized consistently before the completion/semantic gate;
   incomplete fragments remain bounded and are not prematurely parsed.
2. The raw-byte SSE gate and parsed-event stream path both block the leading-space
   counterexample before any downstream consumer sees it.
3. Existing byte-transparent safe SSE, BOM, duplicate-member, and active-provider
   collision behavior remains unchanged.
4. Focused security tests and the deterministic suite pass; no real API call is
   required.

### Recommended repair and residual risk

Use a single whitespace-normalization predicate (or parse the completed buffer
directly) rather than relying on `rstrip()` plus an untrimmed prefix check. Reopen
if the embedded JSON consumer inventory, parser, or stream framing changes.

### Closure evidence (`20260721-1232`)

`_append()` now uses `buffer.text.strip()` for both completion detection and
semantic inspection. The raw SSE regression
`test_raw_sse_blocks_whitespace_padded_argument_reconstruction` passes, as do
the parsed-event and full transport suites. Commit: `f30d167`.

## AUD-023 — Chat parallel tool stream identity is arrival-order based

- Severity: Must Fix
- Decision class: Agent-Fixable
- Status: Closed
- First detected run: `20260721-1148`
- Owner: Gateway transport/security owner

### Evidence and failure path

`src/codex_rosetta/gateway/transport/credential_semantics.py:247-257`
maintains `_chat_tool_order` as an append-only list of IDs. A later event with a
wire `index` indexes that arrival list, not a stable `index -> call_id` map. With
parallel calls arriving as index 1 then index 0, a subsequent index-1 fragment is
stored under the index-0 call. The active credential is consequently split across
buffers and the semantic gate releases it.

### Impact and invariant

Besides corrupting tool argument association, the defect permits an upstream to
evade the active-provider credential reconstruction check by choosing a non-sequential
arrival order. It violates both the tool identity/history invariant and the
credential return-boundary invariant.

### Acceptance criteria

1. Every wire index resolves to one stable call identity independent of arrival
   order; explicit IDs and indices must agree.
2. Missing, conflicting, reused, or remapped identities clear state and fail closed
   within the existing identity/fragment bounds.
3. Sequential and parallel Chat streams, arbitrary chunk splits, and safe unrelated
   arguments retain current behavior.
4. Focused transport tests and deterministic checks pass without real calls.

### Recommended repair and residual risk

Replace the arrival-order list with a bounded index map plus explicit conflict
handling. Reopen on Chat wire-schema, stream identity, or state-bound changes.

### Closure evidence (`20260721-1232`)

Chat fragments now use bounded `index -> call_id` and reverse mappings, reject
conflicts or missing identities, and clear state before failing closed. The
out-of-order parallel-index regression and full transport suite pass. Commit:
`6bd24b4`.

## AUD-024 — `computer_call_output` is silently discarded

- Severity: Must Fix
- Decision class: Decision Recorded
- Status: Closed
- First detected run: `20260721-1148`
- Owner: Project owner and core converter owner

### Evidence and failure path

`src/codex_rosetta/converters/openai_responses/message_ops.py:332-402`
recognizes only three tool-result types and has no reject branch for unknown items.
`computer_call_output` therefore disappears. `tool_ops.py:783-813` emits only a
generic `function_call_output`, and the current IR has no native screenshot/result
part. A local converter probe with a `computer_call` plus a screenshot-bearing
`computer_call_output` returned only the assistant `computer_use` call.

### Impact and invariant

The computer-use call/result history is no longer lossless. A downstream or a later
turn can receive a call without its screenshot/result, causing protocol rejection,
repeated actions, or incorrect replay. Silent data loss violates the profile's
no-silent-Codex/protocol-corruption invariant.

### Decision required

The owner must choose one of:

1. **Explicit rejection (recommended):** reject `computer_call_output` with a
   stable, observable unsupported-item error. This preserves the current scope of
   validated non-streaming `computer_call`, no generic computer-control mapping,
   and no stream support.
2. **Complete support:** define an IR/native metadata representation for output,
   screenshots and correlation, implement Responses request/response/stream
   round trips, and authorize a new cross-format audit.

Regardless of the choice, the interim behavior must stop silently dropping the
   item; no compatibility migration layer or public-deployment promise is implied.

### Recorded owner decision and closure evidence (`20260721-1232`)

The owner selected explicit rejection. `p_messages_to_ir()` now rejects
`computer_call_output` with a controlled `NotImplementedError` before unknown
items can be silently discarded. This preserves the current Responses-only,
non-streaming `computer_call` scope and does not add generic computer-control
support. The negative converter regression and full deterministic suite pass.
Commit: `04efc74`.

### Reopen/closure criteria

Closure is satisfied by the recorded explicit-rejection decision, the fail-closed
negative regression, retained positive `computer_call` round-trip coverage, and
focused/full deterministic verification. Real provider behavior remains outside
the audit unless separately approved.
