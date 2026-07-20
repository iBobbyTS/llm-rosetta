# Third Omission-Remediation Re-audit Scope

- Run: `20260720-1554`
- Mode: targeted repair and phase-separated re-audit after the third omission pass
- Code baseline: `de9c96b8b346b5a338e81ea7fa66ba1a0c590d7b`
- In-scope findings: AUD-006, AUD-008, AUD-009, AUD-011, AUD-012, AUD-014; prior candidate AUD-013 disposition
- Deployment boundary: local process and trusted internal network only; no public deployment or account-security guarantee
- Live-call boundary: no real Codex/provider/API call is permitted in this audit

## In scope

1. Deny redirects by default for provider traffic, isolate explicit provider opt-in, inherit that policy for model discovery, and force all other auxiliary HTTP requests to remain no-follow.
2. Remove any reflected configured Tavily API key from success, HTTP-error, and transport-exception data before it reaches models, clients, diagnostics, or tracebacks.
3. Gate the real-provider SSE development script before `.env` and cover the established live-script naming convention dynamically.
4. Treat only exact backend-supported `api_type` strings as present; infer every other value with a warning, render the runtime value, and avoid config write-back.
5. Reconcile the approved profile and all persistent ledgers to the exact code baseline after phase-separated verification.

## Excluded

- Real upstream/provider/Codex/Tavily/agentabi calls, credential reads for live execution, browser/LAN deployment, Docker/Compose smoke, backup/restore, long-run disk stress, DNS/proxy adversarial tests, GitHub settings, and provider-quality claims.
- AUD-013 implementation: the owner previously rejected added missing/disabled-provider group validation as disproportionate to the current Gateway scale.

## Acceptance evidence

- Focused tests prove default redirect denial, explicit opt-in, pool-policy isolation, auxiliary no-follow, model-discovery inheritance, Tavily credential removal including traceback causes, live-script gate ordering, exact backend protocol recognition, warning behavior, Admin rendering, and no write-back.
- `make lint` and the full deterministic non-integration suite pass after all code commits.
- Profile, findings, coverage, system map, README, and this immutable run agree on decisions, residual risks, and exact commit identities.
