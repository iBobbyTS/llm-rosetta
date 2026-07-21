# Audit Run Evidence

Run: `20260720-2103`
Repository head/environment: `99218427824047a416030675c19c9ba4908925ac`; macOS, Python 3.14.6 via `llm-rosetta`; no real API calls

## Evidence Index

| Unit | Status | Severity | Coverage IDs | Finding IDs | Evidence summary | Gaps |
| --- | --- | --- | --- | --- | --- | --- |
| UNIT-001 | Re-audited | Closed | PROVIDER-01, SIDE-01, SCN-03, SCN-04, CTRL-03 | AUD-019 | shared exact-wire plus parsed JSON/SSE semantic checks block equivalent encodings across provider, Tavily, and sidecar return paths | no real upstream; non-JSON covert encodings are outside this finding |
| UNIT-002 | Re-audited | Decision Recorded / Closed | PROVIDER-01, SIDE-01, SCN-03, CTRL-03 | AUD-020 | return redaction intentionally uses only the active provider/client credentials; cross-provider reflection is accepted and regression-tested, while diagnostics remain global | no live upstream; public deployment remains unsupported |
| UNIT-003 | Reviewed / No Action | N/A | AGENT-01, SCN-11, CTRL-06 | none | all reachable repository-local real-call runners found in the semantic inventory use the shared approval gate | no live trajectory was executed; atypically named future runners remain an invalidation trigger |
| UNIT-004 | Sampled / No Action | N/A | converter rotation | none | Anthropic and Google deterministic converter suites passed in the focused run | not a full converter matrix and no provider semantics were exercised live |
| UNIT-005 | Verified | N/A | repository checks | AUD-019, AUD-020 | focused tests, lint, and full non-integration suite passed; direct probes still reproduce both omissions | deterministic/local evidence only |

---

## UNIT-001 — Semantically exact JSON credentials bypass raw wire matching

- Scope reason: changed/high-churn security boundary; invalidated prior credential-return closure
- Status: Reviewed
- Outcome: Must Fix
- Coverage IDs: PROVIDER-01, SIDE-01, SCN-03, SCN-04, CTRL-03
- Finding IDs: AUD-019

### Failure path

`SecretRedactor.update()` registers the raw UTF-8 credential and only the two canonical JSON spellings produced by `json.dumps(..., ensure_ascii=False/True)`. `contains_wire_bytes()` then performs byte-substring matching. Valid JSON permits other spellings that decode to the same string, including per-character Unicode escapes and escaped solidus. Raw SSE and raw error gates inspect those bytes before returning them, but do not parse and compare the decoded JSON value.

### Source evidence

- `src/codex_rosetta/observability/redaction.py:69-89,122-124`
- `src/codex_rosetta/gateway/transport/credential_redaction.py:55-101,139-153,310-324`
- `src/codex_rosetta/gateway/web_search.py:104-128`
- `src/codex_rosetta/gateway/web_run_sidecar.py:109-137,177-205`

### Deterministic probes

| Probe | Result | Interpretation |
| --- | --- | --- |
| Active token `secret`; wrapped raw SSE event `data: {"delta":"\\u0073ecret"}\n\n` | event passed unchanged; downstream `json.loads` returned `secret` | a semantically exact configured credential crosses the public wrapped raw-stream boundary |
| `SecretRedactor("secret").contains_wire_bytes(b'{"error":"\\u0073ecret"}')` | `False` | Unicode-escaped equivalent is not registered |
| `SecretRedactor("a/b").contains_wire_bytes(b'{"error":"a\\/b"}')` | `False` | escaped-solidus equivalent is not registered |

### Scenario result

```text
Stimulus: a configured upstream reflects the active credential as a legal JSON string using a non-canonical escape spelling
Expected: no configured credential reaches downstream after normal JSON/SSE decoding
Observed: the raw wire gate reports no collision and releases bytes that decode to the credential
Result: Not Satisfied
```

### Coverage update

Phase-separated re-audit closes AUD-019 and restores SCN-04. PROVIDER-01, SIDE-01, SCN-03, and CTRL-03 remain `Invalidated` only because AUD-020's credential-domain decision is still open. AUD-017 remains historical closure of the replacement/protocol-corruption root cause.

---

## UNIT-002 — Return gates do not use the complete configured credential inventory

- Scope reason: cross-client completeness check for the shared credential-return policy
- Status: Reviewed
- Outcome: Must Fix / Needs Decision
- Coverage IDs: PROVIDER-01, SIDE-01, SCN-03, CTRL-03
- Finding IDs: AUD-020

### Failure path

`CredentialRedactingTransport` constructs its return redactor from `ProviderInfo.credential_values`, which contains only the active provider's `KeyRing`. The process already maintains `GatewayConfig.token_values` for configured secrets and supplies that global inventory to observability redactors, but the provider return boundary does not receive it. A configured credential belonging to another route/provider is therefore not considered a collision.

### Source evidence

- `src/codex_rosetta/gateway/transport/credential_redaction.py:23-27`
- `src/codex_rosetta/gateway/transport/provider_info.py:94-97`
- `src/codex_rosetta/gateway/config.py:717,840-848`
- `src/codex_rosetta/gateway/app.py:1093-1097`

### Deterministic probe

| Probe | Result | Interpretation |
| --- | --- | --- |
| Active provider key `provider-a-secret`; fake upstream returns configured Provider B key `provider-b-secret` through the public transport wrapper | parsed body `{'output': 'provider-b-secret'}` and raw body `{"output":"provider-b-secret"}` returned unchanged | return policy is active-provider-local rather than configured-secret-global |

### Scenario result

```text
Stimulus: one configured/custom upstream returns a credential configured for another provider/route
Expected under the owner-approved active-provider/client wording: unrelated provider credential remains outside this return gate
Observed: the credential is outside the active ProviderInfo inventory and is returned unchanged
Result: Satisfied after decision recording
```

### Decision closure

- Selected boundary: protect only credentials configured for the active outbound provider or auxiliary client. Do not inject unrelated providers' credentials into a return gate.
- Global observability, persistence, logging, trace, and metric diagnostics continue to use the complete configured-token inventory.
- Cross-provider/client reflection is an explicit accepted residual risk within the supported local/LAN-only deployment profile.

The owner selected this boundary on 2026-07-20. The profile, compatibility ledger, and `test_non_streaming_ignores_credentials_outside_active_provider` now freeze the contract without changing runtime inventory ownership.

---

## UNIT-003 — Live-call approval inventory

- Status: Reviewed / No Action
- Coverage IDs: AGENT-01, SCN-11, CTRL-06
- Finding IDs: none

The semantic inventory covered integration E2E runners, agentabi and GPT relay runners, 24 executable examples, the live SSE development script, and shell launchers. All reachable real-call entry points use the shared exact-marker gate. `scripts/rosetta-test-kilo.sh` prints instructions only and does not itself start an external call, so its absence from the executable shell gate matrix is No Action.

No real Codex, provider, sidecar, Tavily, browser, or agent call was made.

---

## UNIT-004 — Representative non-Codex converter rotation

- Status: Sampled / No Action
- Finding IDs: none

The focused deterministic run included `tests/converters/anthropic` and `tests/converters/google_genai`. No defect was identified in this sample. This is not a full converter inventory and makes no claim about live provider behavior.

---

## UNIT-005 — Verification

| Command/check | Result | Limitation |
| --- | --- | --- |
| Initial focused command including nonexistent `tests/gateway/test_web_search.py` | collection error; no tests ran | corrected immediately to the repository's actual `test_web_search_bridge.py` path |
| Corrected focused credential/admin/live-gate/Anthropic/Google suite | `723 passed, 2 warnings in 2.70s` | deterministic fakes/in-process only; existing tests omit AUD-019/AUD-020 cases |
| `make lint` via `llm-rosetta` | passed | static checks only |
| `make test` via `llm-rosetta` | `3576 passed, 5 skipped, 11 warnings in 22.63s` | integration tests ignored; no real API calls |
| Main-agent independent recheck of `SecretRedactor.contains_wire_bytes` for `"\\u0073ecret"` and `"a\\/b"` | both returned `False`; `json.loads` reconstructed `secret` and `a/b` | local deterministic semantic probe only |
| Main-agent closing `make lint` and `make test` | lint passed; `3576 passed, 5 skipped, 11 warnings in 17.43s` | integration tests ignored; no real API calls |
| AUD-019 focused remediation suite | `105 passed in 11.24s` | deterministic fakes/in-process only; covers shared redactor, provider raw/error/SSE, Tavily, and web-run sidecar |
| AUD-019 full remediation gates | lint passed; `3591 passed, 5 skipped, 11 warnings in 18.54s`; Codex compatibility contract has no blocking changes | integration ignored; no real API calls |
| AUD-020 decision-contract gates | focused `19 passed`; lint passed; full `3592 passed, 5 skipped, 11 warnings in 18.15s`; Codex compatibility contract has no blocking changes | deterministic contract only; integration ignored; no real API calls |

Passing suites do not close AUD-019 or AUD-020: both are reproduced by direct deterministic probes using the public transport wrapper.

## Remaining Gaps

- Real provider/Codex/Tavily/sidecar behavior, network chunk timing, browser/LAN UX, deployed sinks, production telemetry, recovery, and external GitHub settings remain `Unknown` or excluded.
- No full converter matrix or live provider compatibility matrix was run.
- Encoded/hashed/covert exfiltration beyond semantic JSON string equivalence remains outside the exact-match guarantee.
