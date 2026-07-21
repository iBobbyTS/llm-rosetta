# Audit Remediation Scope

Run: `20260720-2255`
Mode: `Remediation re-audit`
Repository base/head: `353a795a00fb42ecbe307653f12877900e831bf9`; implementation in the current working tree
Profile and status: `docs/audit-profile.md`, `Approved`
Authorized remediation: AUD-019 only; use the same subagent that found the omission
Constraints: no real Codex/provider/API calls, no deployment, no commit

## Scope

| Item | Required outcome | Evidence |
| --- | --- | --- |
| Duplicate JSON members | inspect every member for the active credential before ordinary dict collapse | redactor primitive and duplicate-member regressions |
| Known nested argument JSON | parse only documented Responses/Chat function and tool argument fields | non-streaming and parsed-stream regressions |
| Cross-event argument reconstruction | follow the same call/item/index identities as the real consumers with bounded state | Responses and Chat identity-change regressions |
| Protocol preservation | safe duplicate, unknown-string, ordinary SSE, and BOM frames remain byte-identical | raw SSE and non-streaming regressions |
| Resource ownership | fail closed at explicit byte, fragment, and identity bounds; clear state at done/completed/EOF | limit and lifecycle regressions |
| Credential domain | retain AUD-020 active-provider/client-only return scope | unrelated-provider regression and source review |

## Exclusions

- Real provider/Codex/Tavily/sidecar behavior and network timing.
- New provider schemas not consumed by the current Responses or Chat paths.
- Public deployment, availability, recovery, production telemetry, and external sinks.

## Stop Criteria

- [x] The same subagent implements the frozen repair boundary.
- [x] Main-agent review challenges consumer identity alignment and safe duplicate behavior.
- [x] Focused, lint, full deterministic tests, diff checks, and CodeGraph synchronization pass.
- [x] Current findings, coverage, system map, compatibility points, and run index are reconciled.
