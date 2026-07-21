# Fourth Omission Audit Scope

Run: `20260720-1606`
Mode: Periodic
Repository range/head: independent omission review at `26b7558b1b54160c201ed9cedb1e80a1aa188d95`; no code delta after the third omission-remediation run
Profile and status: `docs/audit-profile.md` (Approved)
Resource/budget constraints: local source, CodeGraph, deterministic tests, and loopback/fake services only; no real provider/API calls
Authorized remediation: No

## 1. Scope Selection Summary

| Scope item | Reason | Criticality | Quality attributes | Scenarios | Expected evidence | Planned depth |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Credential-bearing outbound request return paths | always-on critical, rotating, omission challenge | Critical | Security, privacy, correctness | SCN-03, SCN-09 | source/call-path trace plus malicious fake response/error probes | Deep across provider, Tavily, web-run sidecar, model discovery, and image/search siblings |
| Redirect/destination policy ownership across HTTP clients | always-on critical, finding follow-up | High | Security, reliability, operability | SCN-03, SCN-09 | caller inventory, policy-keyed pooling, no-follow/opt-in tests | Deep at every shared and direct HTTP boundary |
| Real-call and agent-launch approval inventory | always-on critical, agent-control rotation | High | Security, cost, operability | SCN-11 | convention-independent source inventory and fail-closed contract evidence | Deep across Python, shell, examples, integration, dev scripts, and launchers |
| Audit ledger and oracle consistency | finding/debt follow-up | High | Maintainability, evidence integrity | SCN-09, SCN-11 | current-head reconciliation and comparison of claims with executable controls | Targeted |

## 2. Changed and Invalidated Surface

| Change/component | Semantic class | Dependent coverage/scenarios | Invalidation result | Rationale |
| --- | --- | --- | --- | --- |
| No code change after `de9c96b`; audit ledger finalized at `26b7558` | documentation/source-of-truth | GOV-01, MAP-01, affected control claims | Freshness challenged, not automatically invalidated | This independent pass tests whether the third run omitted sibling credential or runner paths. |
| Redirect, protocol inference, live gate, and Tavily remediation in the six commits ahead of `origin/main` | security/config/agent harness | PROVIDER-01, SIDE-01, AGENT-01, CTRL-03/06/09/10, SCN-03/09/11 | Re-evidence dependent sibling paths | The repaired controls are cross-cutting and prior omission rounds repeatedly found incomplete inventories. |

## 3. Always-On Critical Scenarios

| Scenario | Why required now | Evidence target |
| --- | --- | --- |
| Credential-bearing provider or auxiliary request receives malicious success/error/exception content | Provider, Tavily, and sidecar tokens are crown-jewel values and response content is untrusted | Prove configured credentials cannot be reflected into model/client/diagnostic output, or report a reachable path. |
| New or atypically named real-call runner starts without explicit developer approval | GP-001 and CTRL-06 claim every real-call entry point is gated | Inventory actual credential/network/subprocess entry points independently of current naming-based contract tests. |

## 4. Rotating Deep Slices

| Area | Last reviewed | Why selected | Planned boundary |
| --- | --- | --- | --- |
| Web-run sidecar bearer client and model-visible return data | 2026-07-20, partial sibling review | Prior run focused Tavily token reflection and only recorded sidecar redirect behavior | Configured token through request, response/error/exception, caller/model exposure, tests |
| Provider/model-discovery error and response redaction | 2026-07-20, redirect-focused | Credential egress changed but reflection handling was not the stated oracle | ProviderInfo through transport/proxy/Admin model discovery and logs/errors |
| Gate inventory implementation and executable scripts | 2026-07-20 | Repeated omissions show static/convention inventories require independent falsification | All source-controlled executable launch paths that can read credentials or start external clients |

## 5. Incident, Finding, and Debt Follow-up

| Item | Trigger/evidence | Planned verification |
| --- | --- | --- |
| AUD-006 / GP-001 | Two prior omission rounds expanded the live-call inventory | Compare dynamic contract discovery with a semantic repository-wide network/credential inventory. |
| AUD-012 / AUD-014 | Redirect and Tavily reflection fixes share outbound HTTP trust boundaries | Trace sibling clients for equivalent redirect and reflected-secret behavior. |
| AUD-008 | Prior ledgers repeatedly contradicted current code/head | Reconcile baseline identities and update only if current evidence differs. |

## 6. Exclusions

| Area | Reason excluded | Residual risk | Next review trigger |
| --- | --- | --- | --- |
| Real Codex/provider/Tavily/sidecar/agentabi calls | Explicit profile prohibition for audits | Provider-specific behavior and external retention remain Unknown | Developer-authorized non-audit live run |
| Public deployment, DNS rebinding, proxy adversarial behavior, production telemetry | Unsupported deployment boundary or unavailable environment | Public/LAN infrastructure risk is not proven safe | Deployment/security claim expansion |
| Unchanged converter semantics, persistence recovery, supply-chain provenance | This run is an omission slice, not a baseline reset | Existing deterministic/Unknown statuses remain | Scheduled rotation or relevant change |

## 7. Material Assumptions and Decisions Needed

- The approved requirement that configured token values must not appear in model/client/diagnostic data applies equally to provider, Tavily, and web-run sidecar bearer credentials. If the owner intends a narrower exception for sidecar/provider response content, that is a business/security-policy decision rather than an implementation inference.
- Existing local/LAN acceptance of direct custom provider egress and explicit provider redirects remains unchanged.

## 8. Stop Criteria for This Run

- [x] Every scoped item has evidence and an outcome.
- [x] Required scenarios are exercised with deterministic fakes or explicitly blocked.
- [x] Findings are deduplicated by root cause and partitioned by decision class.
- [x] Persistent ledgers are reconciled to current HEAD.
- [x] The Chinese report states gaps, exclusions, and next priorities.
