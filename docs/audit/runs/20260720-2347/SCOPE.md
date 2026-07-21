# Audit Run Scope

Run: `20260720-2347`
Mode: `Periodic`
Repository range/head: `353a795a00fb42ecbe307653f12877900e831bf9` plus the current AUD-019 remediation working tree
Profile and status: `docs/audit-profile.md`, `Approved`
Resource/budget constraints: deterministic local evidence only; no real provider/Codex/API calls; no deployment
Authorized remediation: No

## 1. Scope Selection Summary

| Scope item | Reason | Criticality | Quality attributes | Scenarios | Expected evidence | Planned depth |
| --- | --- | --- | --- | --- | --- | --- |
| Active-provider credential semantic gate | changed/high-churn; always-on critical; finding follow-up | Critical | security, privacy, correctness, reliability | SCN-03/04/05, CTRL-03 | source-to-consumer trace, adversarial deterministic probes, focused tests | deep |
| Responses tool-call consumer schemas | invalidated; rotating converter slice | Critical | correctness, interoperability, security | SCN-03/05 | enumerate every field parsed or accumulated by `OpenAIResponsesMessageOps`, `OpenAIResponsesToolOps`, and stream handlers; compare with gate registrations | deep |
| IR tool-type contract at Responses boundary | rotating converter slice; candidate from direct probe | High | correctness, maintainability | SCN-03/05 | full conversion probes and type/validation evidence for each declared tool item | targeted |
| Persistent audit closure state | finding/debt follow-up | High | audit integrity, agent legibility | CTRL-03, audit control plane | reconcile current run/coverage/finding claims with current code and probes | targeted |

## 2. Changed and Invalidated Surface

| Change/component | Semantic class | Dependent coverage/scenarios | Invalidation result | Rationale |
| --- | --- | --- | --- | --- |
| `credential_semantics.py` and duplicate-preserving JSON decode | return-security state/schema boundary | PROVIDER-01, TOOL-01, SCN-03/04/05, CTRL-03 | review required | current closure claims coverage of documented Responses/Chat consumer semantics |
| Responses tool parsing and stream accumulation | converter/consumer contract | TOOL-01, CODEX-01, SCN-03/04/05 | review required | any parsed field omitted from the gate can recreate a credential after release |
| AUD-019 closure artifacts | audit control plane | coverage/findings/README | review required | closure is valid only if all current consumer schemas and identities are represented |

## 3. Always-On Critical Scenarios

| Scenario | Why required now | Evidence target |
| --- | --- | --- |
| Active provider returns an escaped credential in a supported Responses tool field | credential exposure is `Must Fix` under the profile | the gate blocks before converter/Codex-visible reconstruction |
| Active provider emits tool input across stream events | current remediation added bounded cross-event state | state keys and registered event families match the actual converter consumers |

## 4. Rotating Deep Slices

| Area | Last reviewed | Why selected | Planned boundary |
| --- | --- | --- | --- |
| Responses non-function tool items | not explicitly covered in `20260720-2255` | shared parser handles six item types but closure names only Responses/Chat function/tool arguments | `message_ops._TOOL_CALL_TYPES` through `tool_ops.p_tool_call_to_ir` and IR validation |

## 5. Incident, Finding, and Debt Follow-up

| Item | Trigger/evidence | Planned verification |
| --- | --- | --- |
| AUD-019 | current remediation closure | attempt to reconstruct active credential through every current nested parser and accumulator |
| GP-003 | consumer-semantic return matrix | verify schema inventory is derived from actual consumers rather than a manually incomplete allowlist |

## 6. Exclusions

| Area | Reason excluded | Residual risk | Next review trigger |
| --- | --- | --- | --- |
| Real providers, real Codex, browser/LAN deployment, external sinks | prohibited/unavailable in audit | runtime/parser/timing behavior remains Unknown | developer-approved non-audit live evidence |
| Public internet, HA, backup/restore, SLO/RTO/RPO | outside approved profile | no stronger operational claim | profile expansion |
| Unrelated converters and unchanged persistence/release paths | periodic sampling boundary | prior freshness remains bounded to recorded evidence | direct change, incident, or due rotation |

## 7. Material Assumptions and Decisions Needed

- The approved active-provider/client credential domain remains authoritative; this run does not revisit AUD-020.
- Any field that current converter code parses from provider-controlled text into structured tool input is part of the consumer-semantic return boundary, even when the wire specification describes the field as freeform text.
- No owner decision is required to reopen an implementation omission under the existing no-active-credential-return requirement.

## 8. Stop Criteria for This Run

- [x] Every scoped item has evidence and outcome.
- [x] Required scenarios are exercised or explicitly blocked.
- [x] Persistent ledgers are updated.
- [x] Report states gaps and next priorities.
