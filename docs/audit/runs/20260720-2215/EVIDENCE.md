# Audit Run Evidence

Run: `20260720-2215`
Repository head/environment: `353a795a00fb42ecbe307653f12877900e831bf9`; macOS; Python 3.14.6 via `llm-rosetta`; no real API calls

## Evidence Index

| Unit | Status | Severity | Coverage IDs | Finding IDs | Evidence summary | Gaps |
| --- | --- | --- | --- | --- | --- | --- |
| UNIT-001 | Reviewed | Must Fix / Open | PROVIDER-01, TOOL-01, SCN-03, SCN-04, SCN-05, CTRL-03 | AUD-019 reopened | one-layer JSON collision checks lose duplicate members and do not model supported nested/cross-event tool-argument reconstruction | no real Codex/provider parser or network timing |
| UNIT-002 | Verified | N/A | REL-01, CTRL-05 | AUD-019 | focused and full deterministic suites remain green despite direct failing probes | existing tests do not contain the new oracle |
| UNIT-003 | Partial | Evidence gap | AGENT-01 | none | a fresh-context subagent independently generated and reproduced the root cause before main-agent confirmation | the platform twice blocked the subagent during report finalization |

---

## UNIT-001 — Consumer-semantic credential reconstruction

- Scope reason: changed/high-churn security boundary and always-on critical provider/Responses path
- Status: Reviewed
- Outcome: Must Fix
- Coverage IDs: PROVIDER-01, TOOL-01, SCN-03, SCN-04, SCN-05, CTRL-03
- Finding IDs: AUD-019

### Failure path

`SecretRedactor.contains_json_semantic()` performs exact wire matching and then one ordinary `json.loads()`. The resulting dictionary/string tree is checked for the active credential. This is insufficient when the supported downstream consumer observes semantics not retained by that one parse:

1. ordinary `json.loads` keeps only the final duplicate member, while raw passthrough still releases every member byte;
2. Responses `function.arguments` and related fields are JSON strings that are parsed again by tool conversion/consumers;
3. `response.function_call_arguments.delta` strings are accumulated across complete SSE events before the completed argument string is parsed.

The complete-event gate retains one event for raw cross-frame substring matching, but checks JSON semantics independently per event. Syntax between frames prevents the literal credential bytes from becoming adjacent on the wire even though downstream delta concatenation reconstructs them.

### Source evidence

- `src/codex_rosetta/observability/redaction.py:104-135`
- `src/codex_rosetta/gateway/transport/credential_redaction.py:55-124`
- `src/codex_rosetta/converters/openai_responses/tool_ops.py:634-646`
- `src/codex_rosetta/converters/openai_responses/converter.py:1238-1259`
- `src/codex_rosetta/converters/base/context.py:255-297`

### Deterministic probes

| Probe | Gate result | Supported consumer result |
| --- | --- | --- |
| `{"value":"\\u0073ecret","value":"safe"}` | `contains_json_semantic=False` | `object_pairs_hook=list` preserves `('value', 'secret')` before the later safe member |
| outer JSON containing `function.arguments="{\"value\":\"\\u0073ecret\"}"` | `contains_json_semantic=False` | second `json.loads(arguments)` returns `{'value': 'secret'}` |
| two complete Responses SSE events whose `delta` values concatenate to `{"value":"\\u0073ecret"}` | every event released; `sse_emitted_all=True` | concatenate deltas then parse returns `{'value': 'secret'}` |

### Scenario result

```text
Stimulus: the active provider reflects its configured credential through a supported JSON/tool representation requiring duplicate preservation, nested parsing, or cross-event accumulation
Environment: local deterministic redactor/SSE gate at HEAD 353a795
Expected response: fail closed before downstream Codex/converter/tool consumers can reconstruct the credential
Observed response: all current return-gate checks pass and downstream reconstruction yields the exact active-provider credential
Result: Not Satisfied
```

### Finding and coverage update

- Reopen AUD-019; this is the same root cause as the previous semantic-equivalence finding, not a new ID.
- Invalidate PROVIDER-01, TOOL-01, SCN-03, SCN-04, SCN-05, and CTRL-03.
- Retain AUD-020 as Closed/Decision Recorded because all probes use the current provider credential inventory.
- Reopen GP-003 enforcement calibration: its executable matrix covers wire chunks and complete events but not supported downstream semantic reconstruction.

### Gaps and assumptions

- No real provider, Codex, sidecar, browser, or deployed sink was exercised.
- Arbitrary covert encoding remains outside scope; remediation should be schema/consumer-aware rather than recursively parsing every string as JSON.
- Duplicate-key handling may be bounded to raw-preserving return channels; parsed-and-normalized auxiliary clients need separate reachability analysis during remediation.

---

## UNIT-002 — Verification portfolio

| Command/check | Result | Limitation |
| --- | --- | --- |
| focused redaction/provider-return/Responses stream+tool suite | `266 passed in 11.61s` | no duplicate-key, nested-second-parse, or cross-event reconstruction oracle |
| `make lint` | passed | static/type/complexity only |
| `make test` | `3592 passed, 5 skipped, 11 warnings in 18.45s` | integration ignored; no real API calls |
| direct three-case Python probe | all gates returned safe/released; all consumer reconstructions returned `secret` | deterministic local semantics only |

Passing suites do not close the finding because the direct consumer-semantic probes contradict the closure assumption.

---

## UNIT-003 — Independent-agent evidence

A new fresh-context subagent received only the repository path and `code-audit` skill. It independently identified the one-layer parser limitation and reproduced duplicate-key, nested tool-argument, and cross-SSE-event variants before any prior finding was supplied. The platform then blocked that agent twice while it was finalizing security-audit artifacts. The main agent independently reproduced the exact cases and owns the durable ledger update. This platform interruption is an evidence-process gap, not proof for or against the finding.

## Remaining Gaps

- Real Codex/provider behavior, network chunk timing, sidecar/Tavily consumer schemas, browser/LAN deployment, production telemetry, external sinks, and GitHub settings remain `Unknown` or excluded.
- No remediation was authorized or attempted.
