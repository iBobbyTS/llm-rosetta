# Audit Scope

Run: `20260721-1232`
Mode: Targeted remediation re-audit
Repository: `04efc74e0425c42bb906581b61c0c0be6976841` plus current working tree
Authorized remediation: Yes, limited to AUD-022/AUD-023/AUD-024
Real API calls: prohibited; none executed

## Selected slices

| Slice | Reason | Depth |
| --- | --- | --- |
| Responses credential semantic gate | Verify AUD-022 whitespace normalization at the shared transport boundary | raw and parsed SSE regression |
| Chat parallel tool identity | Verify AUD-023 stable wire-index mapping and fail-closed conflict behavior | adversarial order and identity-state regression |
| Responses computer-tool result dispatch | Verify the recorded explicit-rejection decision for AUD-024 | request conversion plus retained positive call round trip |
| Affected-cone validation | Detect regressions in the shared converter/transport contracts | focused, full deterministic, lint |

## Exclusions

Real provider/Codex behavior, browser/LAN deployment, external sinks, persistence,
release provenance, full fuzzing, and complete native computer-output support remain
outside this targeted re-audit. They are not claims of safety or availability.

## Acceptance criteria

- [x] AUD-022 blocks whitespace-padded completed JSON before release.
- [x] AUD-023 resolves every known Chat index to one stable identity and fails closed on missing/conflicting mappings.
- [x] AUD-024 rejects `computer_call_output` explicitly; no generic computer-control scope is added.
- [x] Focused and full deterministic tests plus lint pass.
- [x] No real API/provider/Codex call or deployment occurs.
