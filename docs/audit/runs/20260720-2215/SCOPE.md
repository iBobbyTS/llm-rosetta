# Audit Run Scope

Run: `20260720-2215`
Mode: `Periodic`
Repository range/head: `353a795a00fb42ecbe307653f12877900e831bf9`; follow-up to `20260720-2103`
Profile and status: `docs/audit-profile.md`, `Approved`
Resource/budget constraints: deterministic local evidence only; no real Codex/provider/API calls, deployment, browser, external GitHub settings, or production evidence
Authorized remediation: No

## 1. Scope Selection Summary

| Scope item | Reason | Criticality | Quality attributes | Scenarios | Expected evidence | Planned depth |
| --- | --- | --- | --- | --- | --- | --- |
| Active-provider credential return boundary after AUD-019/AUD-020 closure | changed/high-churn, invalidated, always-on critical | Critical | Security, correctness, interoperability | SCN-03, SCN-04 | fresh independent hypotheses, current source/call paths, deterministic consumer-semantic probes | Deep |
| OpenAI Responses function/tool argument reconstruction | rotating, scenario dependency | Critical | Security, correctness | SCN-03, SCN-04, SCN-05 | nested JSON parsing and stream-accumulation paths, focused converter/transport tests | Deep |
| Audit independence and evidence integrity | agent/harness/tooling | High | Evidence integrity, maintainability | CTRL-05, CTRL-06 | fresh-context subagent discovery followed by main-agent reproduction; no remediation | Targeted |

## 2. Changed and Invalidated Surface

| Change/component | Semantic class | Dependent coverage/scenarios | Invalidation result | Rationale |
| --- | --- | --- | --- | --- |
| `SecretRedactor.contains_json_semantic()` and complete-event SSE gate introduced by `73afaeb` | security boundary/parser semantics | PROVIDER-01, SCN-03, SCN-04, CTRL-03 | challenged in this run | prior proof assumed one outer `json.loads` captured downstream semantic equivalence |
| Active-provider-only decision contract in `353a795` | credential ownership | PROVIDER-01, CTRL-03 | retained | all probes use the current provider credential; no global-inventory issue is asserted |
| Responses function argument strings and streaming deltas | nested/accumulated application protocol | TOOL-01, SCN-03, SCN-04, SCN-05 | challenged in this run | supported consumers parse or concatenate content after the return gate releases it |

## 3. Always-On Critical Scenarios

| Scenario | Why required now | Evidence target |
| --- | --- | --- |
| Active provider returns JSON/Responses data that later consumer stages decode again | direct credential confidentiality claim | prove whether a credential can be reconstructed after every gate check succeeds |
| Active provider splits tool argument JSON across complete SSE events | streaming and tool-call critical path | prove whether complete-event holding covers cross-event application reconstruction |

## 4. Rotating Deep Slices

| Area | Last reviewed | Why selected | Planned boundary |
| --- | --- | --- | --- |
| Responses nested tool arguments and delta accumulation | prior audits checked outer JSON and HTTP chunks, not consumer reconstruction | direct dependency of Codex tool execution | return gate through outer event parse, delta accumulation, and nested JSON parse |
| Duplicate-key JSON parser equivalence | not previously recorded | outer `json.loads` collapses information while raw bytes are preserved downstream | active-provider raw JSON/SSE only |

## 5. Incident, Finding, and Debt Follow-up

| Item | Trigger/evidence | Planned verification |
| --- | --- | --- |
| AUD-019 closure | fresh subagent independently found deeper same-root semantic encodings | reopen the same ID if current deterministic probes reproduce |
| GP-003 | repeated sibling escapes at the credential return boundary | reassess whether the enforced test matrix models downstream consumer semantics |

## 6. Exclusions

| Area | Reason excluded | Residual risk | Next review trigger |
| --- | --- | --- | --- |
| Real provider/Codex/Tavily/sidecar calls | explicitly prohibited by profile | external parser and timing behavior remain Unknown | developer-authorized development validation |
| Public deployment, HA, backup/restore, production operations | outside approved commitment | no public/availability/recovery claim | profile expansion |
| Unrelated converters, Admin UI, persistence, release/supply chain | no new dependency edge from the challenged parser assumption | prior freshness/debt remains | normal rotation or direct change |

## 7. Material Assumptions and Decisions Needed

- The active-provider/client credential domain recorded by AUD-020 remains authoritative.
- Supported downstream reconstruction includes documented JSON-string tool arguments and Responses argument-delta accumulation. Arbitrary encrypted, compressed, hashed, or covert encodings remain outside exact-reflection guarantees.
- No new business decision is required to reopen AUD-019; the approved profile already requires active-provider credentials not to reach downstream consumers.

## 8. Stop Criteria for This Run

- [x] Every scoped item has evidence and outcome.
- [x] Required scenarios are exercised or explicitly blocked.
- [x] Persistent ledgers are updated.
- [x] Report states gaps and next priorities.
