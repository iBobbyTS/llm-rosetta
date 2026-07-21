# Fourth Omission Audit Evidence

Run: `20260720-1606`
Repository head/environment: `26b7558b1b54160c201ed9cedb1e80a1aa188d95`; local macOS, Python environment `llm-rosetta`; no real provider/API calls

## Evidence Index

| Unit | Status | Severity | Coverage IDs | Finding IDs | Evidence summary | Gaps |
| --- | --- | --- | --- | --- | --- | --- |
| UNIT-001 | Closed after remediation | Must Fix | PROVIDER-01, SIDE-01, SCN-03, CTRL-03 | AUD-015 | Original provider/sidecar reflection was confirmed, then closed by provider-return, sidecar, Admin model-discovery, stream, exception, and dictionary-key controls verified with adversarial fakes. | Deterministic only; no real provider, sidecar, external sink, or deployment call. |
| UNIT-002 | Closed after remediation | Must Fix | PROVIDER-01, DATA-01, CTRL-03 | AUD-016 | Original CSV/rotation divergence was confirmed, then closed by canonical `KeyRing` values registered with every runtime redactor across startup and atomic hot reload/rollback. | No live rotation, external log sink, or production persistence exercise. |
| UNIT-003 | No Action | N/A | AGENT-01, SCN-11, CTRL-06 | None | A semantic inventory of credential reads, external clients, subprocess launchers, examples, integration scripts, and executable shell scripts found every current real-call entry point gated before sensitive work. The executable Kilo helper only prints IDE instructions. | The Python contract still combines dynamic directory discovery with a static integration list; any new atypically named runner remains an invalidation trigger. |
| UNIT-004 | No Action | N/A | PROVIDER-01, SIDE-01, SCN-09, CTRL-09 | None | Shared bounded HTTP requests remain no-follow by default, provider pool entries are keyed by redirect policy, model discovery alone inherits explicit provider opt-in, and direct auxiliary callers do not enable redirects. Focused loopback tests passed. | DNS rebinding, configured proxies, and operator-enabled redirects were not exercised; those remain profile-accepted or Unknown. |
| UNIT-005 | Pass | N/A | REL-01, CTRL-05 | None | `125 passed` across sidecar, Responses passthrough, redaction, live-gate contract, and HTTP transport-limit tests. All adversarial probes used fake transports and dummy credentials. | This was a focused audit suite, not the full deterministic suite or a build. |
| UNIT-006 | Closed / phase-separated verification | Must Fix | PROVIDER-01, SIDE-01, DATA-01, SCN-03, CTRL-03 | AUD-015, AUD-016 | Authorized remediation plus independent targeted review covered all frozen scenarios, including Admin model discovery and malicious JSON object keys; focused, lint/type, and full deterministic suites passed. | Deterministic-only closure; no build, real call, external sink, or deployed runtime evidence. |

## UNIT-001 - Credential Reflection at Outbound Return Boundaries

### Requirement and source trace

- The approved profile classifies provider credentials as crown jewels, treats provider/tool output as untrusted, requires configured token values to be redacted, and classifies confirmed credential exposure as `Must Fix` (`docs/audit-profile.md:21`, `:31`, `:34`, `:36`, `:67`, `:99`).
- `ProviderInfo.auth_headers()` advances the key ring and places the selected wire key into authentication headers (`src/codex_rosetta/gateway/transport/provider_info.py:91`).
- Non-streaming Responses passthrough returns `resp.raw_content` for both success and error without a configured-token redaction step (`src/codex_rosetta/gateway/proxy.py:1495`, `:1515`, `:1524`, `:1530`). The converted error path does the same (`src/codex_rosetta/gateway/proxy.py:1623`, `:1643`).
- Same-protocol streaming returns an upstream error body directly and yields raw upstream SSE bytes directly (`src/codex_rosetta/gateway/proxy.py:2138`, `:2156`, `:2165`, `:2315`, `:2360`). Logging has a redactor, but downstream responses do not (`src/codex_rosetta/gateway/logging.py:262`, `:284`).
- `WebRunSidecarHTTPClient` sends its configured bearer at `web_run_sidecar.py:82` and `:135`, then returns untrusted success strings/objects unchanged (`:115`, `:168`) or embeds untrusted errors/exceptions into retained exception chains (`:97`, `:108`, `:150`, `:161`, `:176`).
- The local Codex Search bridge propagates sidecar output into response/model data and propagates exception text into 4xx/5xx responses and trace events (`src/codex_rosetta/gateway/codex_auxiliary.py:290`, `:313`, `:333`, `:339`; `src/codex_rosetta/gateway/codex_search.py:100`, `:354`, `:527`, `:870`).
- `GatewayConfig` already registers the sidecar token as a configured token, so the intended security policy is not ambiguous (`src/codex_rosetta/gateway/config.py:762`, `:765`, `:769`).

### Deterministic reachability evidence

The sidecar helper was replaced in process with success, HTTP-error, and transport-exception fakes. No socket request occurred:

```text
sidecar_success_reflects_token True
sidecar_error_reflects_token True
sidecar_exception_chain_reflects_token True cause_retained True
```

An actual `ProviderInfo` generated its authorization header and a fake transport reflected that selected value in a normal Responses body and a 401 body. `handle_non_streaming` returned both to the downstream caller:

```text
provider_200_reflects_token True
provider_401_reflects_token True
```

The error logger independently changed the diagnostic copy to `[REDACTED]`, proving that log sanitization does not protect the downstream response boundary.

### Existing-oracle gap

`tests/gateway/test_web_run_sidecar.py:18` verifies bearer delivery and ordinary mapping, while `tests/gateway/test_responses_passthrough.py:59` explicitly requires raw response preservation. Neither suite asserts that a configured credential reflected in ordinary success/error content is removed before downstream/model exposure. Tavily has this exact boundary protection in `gateway/web_search.py:100-125`; the provider and sidecar siblings do not.

### Initial outcome before authorized remediation

This discovery opened `AUD-015`. UNIT-006 below records the later authorized repair and phase-separated deterministic closure.

## UNIT-002 - Rotated Wire Keys Are Missing from the Redaction Inventory

### Requirement and source trace

- `collect_token_values()` adds an `api_key` string as one exact value (`src/codex_rosetta/observability/redaction.py:43`, `:49`, `:54`). `SecretRedactor` performs exact substring replacement over that inventory (`:69`, `:87`, `:92`).
- `KeyRing`, independently, splits the same configured string on commas, trims it, and selects one individual key for each request (`src/codex_rosetta/gateway/transport/provider_info.py:28`, `:35`, `:39`, `:91`).
- `GatewayConfig` collects tokens before provider construction (`src/codex_rosetta/gateway/config.py:716`), and the resulting set is propagated into error logs/body logs at startup (`src/codex_rosetta/gateway/app.py:1092`) and into trace, logging, persistence, and metrics during atomic config activation (`src/codex_rosetta/gateway/admin/routes/_shared.py:145`, `:164`, `:175`, `:183`, `:194`, `:209`).

### Deterministic reachability evidence

A real `GatewayConfig` with two dummy comma-delimited provider keys produced an actual `ProviderInfo`, selected the first wire key, and sanitized a reflected diagnostic through the configured `UpstreamErrorLogState`:

```text
registered_exact_individual_keys False False
registered_raw_csv True
active_rotated_key rotate-secret-A
diagnostic_reflects_active_key True
```

The same incomplete token set is shared by body logging, stream trace, persistence/error dumps, and metrics, so this is not limited to one logger.

### Existing-oracle gap

`tests/observability/test_redaction.py:12` covers one provider token but no comma-delimited rotation value. Current provider tests exercise rotation behavior separately from the exact-value redaction inventory, allowing the two parsers to diverge.

### Initial outcome before authorized remediation

This discovery opened `AUD-016`. UNIT-006 below records the later canonical-parser repair and phase-separated deterministic closure.

## UNIT-003 - Real-Call and Agent-Launch Approval Inventory

- Dynamic contract discovery covers both live example directories and `dev_scripts/*live*.py`; static rows cover integration/agent and shell launchers (`tests/live_agent/test_live_agent_configuration_contract.py:22`, `:29`, `:48`, `:78`, `:89`, `:104`).
- Independent semantic searches covered credential environment reads, dotenv, SDK/httpx clients, `agentabi`, `urllib`, and `subprocess.run/Popen`, including names outside the dynamic globs.
- The two nested live-agent launchers gate at the start of `main()` before parsing run paths, copying credentials, or launching subprocesses (`tests/live_agent/context_compaction/run_live.py:511`; `tests/live_agent/deferred_tool_search/prepare_run.py:209`).
- `scripts/rosetta-test-kilo.sh` is executable but only emits IDE selection instructions and makes no credential, network, or subprocess call.
- Result: No Action at current HEAD. AGENT-01/SCN-11/CTRL-06 remain fresh deterministically. No live runner was executed.

## UNIT-004 - Redirect and Direct HTTP Boundary Falsification

- `request_bounded_response()` defaults to `allow_redirects=False` and forces zero redirects unless explicitly enabled (`src/codex_rosetta/gateway/transport/http/transport.py:242`, `:250`, `:266`).
- Provider clients are pooled by both proxy and redirect policy, preventing an opt-in client from being reused by a default-deny provider (`src/codex_rosetta/gateway/transport/http/client_pool.py:28`).
- Provider request, stream, and passthrough paths use `ProviderInfo.allow_redirects`; Admin model discovery is the direct sibling that deliberately inherits the same provider setting (`src/codex_rosetta/gateway/transport/http/transport.py:518`, `:577`, `:641`; `src/codex_rosetta/gateway/admin/routes/config.py:939`).
- Web-run health, sidecar, Tavily, internal Admin tasks, and observability requests use the helper without enabling redirects. The web-run resource client also explicitly sets `follow_redirects=False`.
- Result: No new redirect-policy finding. The focused loopback transport suite passed, while external DNS/proxy behavior remains unclaimed.

## UNIT-005 - Command and Test Record

All commands were local source inspection, mock/fake probes, or deterministic tests. No command loaded real credentials, started a provider/agent runner, or made a real API call.

```text
conda run -n llm-rosetta pytest -q \
  tests/gateway/test_web_run_sidecar.py \
  tests/gateway/test_responses_passthrough.py \
  tests/observability/test_redaction.py \
  tests/live_agent/test_live_agent_configuration_contract.py \
  tests/gateway/test_http_transport_limits.py

125 passed in 11.89s
```

## UNIT-006 - Authorized Remediation and Targeted Re-Audit

### Current control boundary

- `KeyRing` parses provider credential CSV once into the ordered selectable tuple. `ProviderInfo.credential_values` exposes that same tuple, and `GatewayConfig` adds every selectable value while retaining the raw configured CSV in the exact-token inventory.
- `CredentialRedactingTransport` sanitizes provider parsed bodies, raw bodies, parsed streams, raw SSE bytes, HTTP errors, and transport exceptions before proxy consumers receive them. Its streaming redactor covers configured values split at every byte boundary while preserving all non-secret bytes and SSE order/framing.
- `WebRunSidecarHTTPClient`, Codex auxiliary provider calls, and Admin `fetch_upstream_models` apply configured-value redaction to their own credential-bearing return paths. Admin discovery derives the boundary from `pinfo.credential_values`, and regression tests advance the ring to prove the second dummy key is the one actually sent on the wire.
- Transport and sidecar exception messages are sanitized and raised/returned without retaining a secret-bearing cause or context.
- `SecretRedactor.redact()` and `redact_exact()` sanitize configured values in both dictionary values and string/bytes keys. Redacted-key collisions use deterministic ordinary-dict semantics: the later source item wins, and no original secret key remains.
- Admin hot-reload tests exercise prepare, activate, and rollback across stream trace, upstream/body logs, metrics, persistence, and provider-return redactors; successful activation removes old keys and failed activation restores the prior complete state.

### Adversarial deterministic coverage

- Provider passthrough and converted success/HTTP-error responses, streaming and non-streaming paths, parsed events, raw SSE with every token split and bytewise chunks, stream read/iteration/close failures, traces, metrics, body/error logs, and persistence redactors.
- Sidecar execute/search nested success, structured/plain HTTP errors, invalid payloads, transport exceptions, and detached causes.
- Admin model discovery success model IDs and connection errors containing the actual rotated wire key.
- Canonical credential parsing with whitespace, empty entries, duplicates, prefix overlap, all rotation positions, raw CSV retention, environment fallback, startup, successful hot reload, old-key removal, and rollback.
- Malicious upstream JSON objects using configured credentials as object keys, including string/bytes direct-redactor cases and a provider passthrough integration regression.

### Independent verification record

The parent audit flow performed phase-separated verification against the completed working tree. No real provider, Codex, Tavily, sidecar, agent, or external API call was made.

```text
focused remediation and adversarial re-audit: 158 passed
conda run -n llm-rosetta make lint: passed
conda run -n llm-rosetta make test: 3542 passed, 5 skipped, 11 warnings
```

### Closure assessment

- AUD-015: all six frozen acceptance criteria satisfied by current code and deterministic tests.
- AUD-016: all five frozen acceptance criteria satisfied by current code and deterministic tests.
- Residual limits: no real upstream reflection, real chunk timing, external logging/persistence sink, proxy/DNS behavior, container runtime, or production deployment was exercised. Closure is explicitly deterministic-only.
- Reopen AUD-015 on any new credential-bearing client/return path, authentication scheme, raw/parsed stream implementation, dictionary-key serialization behavior, exception propagation, or diagnostic sink.
- Reopen AUD-016 on provider credential syntax/parser, environment substitution, `KeyRing` selection semantics, provider construction, startup/hot-reload activation or rollback, or runtime redactor-consumer changes.
