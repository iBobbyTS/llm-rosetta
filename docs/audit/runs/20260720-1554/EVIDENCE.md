# Third Omission-Remediation Re-audit Evidence

## Code evidence

| Finding | Commit | Evidence |
| --- | --- | --- |
| AUD-012 | `3e327c8` | `HttpClientPool` is keyed by proxy and redirect policy; provider request/stream/passthrough paths default to zero redirects, explicit opt-in is isolated, model discovery inherits provider policy, and `request_bounded_response` overrides caller defaults for auxiliary no-follow. Loopback tests prove blocked targets are untouched. |
| AUD-014 | `b7542d2`, `de9c96b` | `TavilyHTTPClient` recursively redacts the configured key from parsed success data, bounded error text, and transport messages; the original transport exception cause is detached to protect full tracebacks. |
| AUD-006 | `5d95668` | `dev_scripts/test_roundtrip_live.py` requires the shared exact approval marker before `.env`; the contract test dynamically discovers `dev_scripts/*live*.py`. |
| AUD-009 | `3f2d044` | Only exact strings in the backend API-type map return as explicit. Unknown strings, whitespace variants, empty/falsy values, booleans, numbers, lists, and objects infer from the authoritative URL, warn once, render through Admin, and remain unchanged on disk. |
| AUD-008/011 | this ledger commit | Profile and ledgers record default-deny redirects, explicit provider opt-in, auxiliary isolation, accepted local/LAN custom/redirect egress risk, and the exact code baseline. |

The [official Tavily Search API reference](https://docs.tavily.com/documentation/api-reference/endpoint/search) defines the API key as Bearer request authentication and documents success fields such as `query`, `answer`, `results`, `response_time`, `usage`, and `request_id`. The remediation therefore keeps request authentication intact and removes any reflected configured key from untrusted response/error content rather than treating a response token as part of the contract.

## Phase-separated re-audit

- CodeGraph traced `request_bounded_response`, `HttpClientPool`, and `HttpTransport` callers after remediation.
- Repository search confirmed no non-provider caller passes `allow_redirects=True`; only provider model discovery inherits the provider setting.
- Direct HTTP implementations outside the shared helper were checked: the web-run resource explicitly disables redirects, static page fetch uses a no-redirect handler, and Google image fetch owns a separate validated redirect policy without provider authorization.
- Real-call candidate inventory confirmed the newly gated live SSE script and retained the previously gated examples, integration entries, and launch scripts.
- The Tavily error path was re-reviewed after initial tests; this found and closed the retained-exception-cause leak in `de9c96b` before baseline freeze.

## Verification

| Check | Result | Scope/limitation |
| --- | --- | --- |
| `tests/gateway/test_http_transport_limits.py` | 52 passed | Local fakes/loopback; covers redirect and Tavily boundaries without external network. |
| Live-call configuration contract | 52 passed | Static/source contract plus local gate execution; no real call. |
| Config and Admin route suites | 204 passed | Local/fake behavior; no upstream request. |
| `conda run -n llm-rosetta make lint` | passed | Ruff, format, ty, complexity ratchet; 349 files formatted. |
| `conda run -n llm-rosetta make test` | 3505 passed, 5 skipped, 11 warnings | `tests/integration` excluded by Makefile; no real API call. |
| `git diff --check` | passed before ledger edits | Code baseline clean; repeated after ledger creation. |
| `codegraph sync` | passed | Index synchronized after cross-boundary code changes. |

## Negative evidence and residual limits

- No real API key was used and no external provider, Codex, or Tavily request was sent.
- Direct requests to an operator-configured arbitrary HTTP(S) custom URL or proxy can receive the provider credential. Explicit `allow_redirects` can additionally forward credentials to redirect targets. Both are accepted only for local/trusted-LAN deployments.
- No availability, data-loss recovery, HA/SLO/RTO/RPO, public deployment, provider quality, DNS rebinding, or proxy-behavior conclusion is made.
- AUD-013 remains rejected: missing/disabled provider model groups retain the existing silent-skip behavior by explicit owner decision.
