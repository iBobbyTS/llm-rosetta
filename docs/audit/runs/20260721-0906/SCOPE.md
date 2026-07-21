# Audit Remediation Scope

Run: `20260721-0906`
Mode: `Targeted Re-audit`
Repository range/head: `353a795a00fb42ecbe307653f12877900e831bf9` plus the current remediation working tree
Profile and status: `docs/audit-profile.md`, `Approved`
Resource/budget constraints: deterministic local evidence only; no real provider/Codex/API calls; no deployment
Authorized remediation: Yes, limited to `AUD-019` and `AUD-021`

## 1. Scope Selection Summary

| Scope item | Reason | Criticality | Quality attributes | Scenarios | Expected evidence | Planned depth |
| --- | --- | --- | --- | --- | --- | --- |
| Responses credential consumer schema | reopened finding; invalidated critical boundary | Critical | security, privacy, correctness | SCN-03/04/05, CTRL-03 | failing gate-to-converter tests, bounded non-streaming and streaming repair | deep |
| Responses computer-tool contract | open finding; converter/type/IR drift | Critical | correctness, interoperability, maintainability | SCN-03/05 | authoritative local SDK evidence, validated same-format round trip, explicit cross-format rejection | deep |
| Persistent audit and Codex compatibility ledgers | remediation follow-up | High | audit integrity, agent legibility | TOOL-01, PROVIDER-01 | code-visible closure evidence and current deterministic results | targeted |

## 2. Changed and Invalidated Surface

| Change/component | Semantic class | Dependent coverage/scenarios | Invalidation result | Rationale |
| --- | --- | --- | --- | --- |
| `credential_semantics.py` and Responses converter schema constants | credential return boundary | PROVIDER-01, TOOL-01, SCN-03/04/05, CTRL-03 | remains Invalidated until re-audit | embedded JSON fields and event families must match real consumers |
| Responses tool types, tool ops, message/stream conversion, IR | public wire and cross-format contract | TOOL-01, SCN-03/05, CP-05/08/17 | remains Invalidated until re-audit | canonical computer call currently fails validation or disappears |

## 3. Always-On Critical Scenarios

| Scenario | Why required now | Evidence target |
| --- | --- | --- |
| Active provider returns a credential in every supported embedded JSON-string field | AUD-019 is a repeated security-boundary omission | no consumer can reconstruct the credential after the gate releases bytes |
| Active provider splits custom-tool input across SSE events | converter accumulates this field across events | the completing event is blocked before downstream reconstruction |
| Responses computer call crosses the central IR | AUD-021 violates the hub-and-spoke contract | same-format round trip is validated; unsupported cross-format conversion fails explicitly |

## 4. Exclusions

| Area | Reason excluded | Residual risk | Next review trigger |
| --- | --- | --- | --- |
| Real providers, Codex, browser/LAN deployment, external sinks | prohibited/unavailable in audit | runtime timing and external parser behavior remain Unknown | developer-approved non-audit evidence |
| New generalized computer-control feature set | no business authorization or Codex owner | providers other than Responses cannot consume computer actions | explicit product scope expansion |
| Unrelated converters, persistence, Admin, release paths | bounded targeted re-audit | prior evidence remains unchanged | direct dependency change or incident |

## 5. Material Assumptions and Decisions Needed

- The active-provider-only credential domain from AUD-020 remains authoritative.
- `computer_call` is the canonical local OpenAI SDK wire spelling; no Codex compatibility claim is added.
- Same-format Responses preserves the native computer item. Other target formats reject `computer_use` explicitly rather than guessing a function representation.
- No additional owner decision is required.

## 6. Stop Criteria

- [x] Original AUD-019 and AUD-021 repros fail before implementation and pass after it.
- [x] Sibling non-streaming and streaming paths have meaningful regression oracles.
- [x] Focused, lint, full deterministic, and compatibility checks pass.
- [x] A phase-separated targeted re-audit verifies frozen acceptance criteria.
- [x] Persistent ledgers and compatibility docs reflect current behavior and residual gaps.
