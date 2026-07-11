# Gateway Security and Authentication

Codex-Rosetta fails closed: every gateway configuration must contain a
non-empty Admin password and at least one gateway access key. The default bind
address is `127.0.0.1`, and API credential reveal is disabled by default.

## Initialize a local gateway

```bash
codex-rosetta-gateway init
codex-rosetta-gateway --host 127.0.0.1
```

`init` generates a strong random `server.admin_password` and one
`server.api_keys` entry in the config file. The config, lock, and backup files
are written with owner-only permissions. Store the generated credentials in a
password manager before distributing them to clients.

Every `/v1` request uses the gateway access key, not an upstream provider key.
Authentication runs before routing, so unknown, removed, and dynamically
registered `/v1` paths fail closed as well. Browser `OPTIONS` preflight remains
public and the subsequent request still requires authentication:

```http
Authorization: Bearer rsk-...
```

Inbound parsing also has fixed process-level resource limits. A request line
must complete within one 5-second monotonic deadline; each header or chunked
trailer section must complete within 10 seconds and may contain at most 100
fields or 64 KiB including framing; and the complete request body must arrive
within one 30-second monotonic deadline. At most 64 connections may occupy the
request parser at once. A 65th connection receives HTTP 503 immediately rather
than waiting for capacity.

For protected `/v1` and Admin API requests, credentials are checked after the
bounded headers but before body bytes are consumed. Invalid credentials are
therefore rejected without buffering a declared large body. Valid requests
default to a 128 MiB body limit. The Admin Server Settings page can change
`server.request_body_limit_mb` at runtime to `64`, `128`, `256`, `512`, `1024`,
or `"unlimited"`; a config reload applies the same setting without restarting
the gateway. Public Admin login/auth-check endpoints and browser `OPTIONS`
preflight remain intentionally unauthenticated; any body they carry is still
covered by the same body deadline, configured size limit, and parser capacity.
The unlimited setting removes Rosetta's practical body-size ceiling but still
buffers each body in memory, so use it only on a trusted, memory-controlled
deployment.

Each access-key entry needs a stable, unique `id`. Rosetta uses that ID as the
authenticated principal for cross-turn state isolation; changing a label does
not change identity. The Admin UI rejects deletion of the final access key.

Gateway model identifiers are limited to 256 UTF-8 bytes. The Codex
`x-codex-window-id` header is limited to 128 UTF-8 bytes; current Codex window
IDs use `{UUID}:{window_number}` and are normally about 40 bytes. Requests over
either semantic limit receive a format-appropriate HTTP 400 before routing or
state allocation. These limits prevent request-error reflection and state-key
memory from bypassing the larger body/header and cache-value byte budgets.

Cross-turn in-memory state has principal-fair hard limits. Provider continuation
metadata is limited to 1 MiB per entry, 8 MiB per scope, 1,024 entries and
16 MiB per principal, and 10,000 entries and 64 MiB for the app. Deferred tool
discovery is limited to 1,024 nested tools and 16 MiB per scope, 256 unique
scopes per principal across the loaded and deferred maps, 1,000 retained scopes
per map, and 64 MiB for the app. A scope present in both tool maps counts once
toward the principal limit. Reaching a principal limit rejects the new state.
When a global count map is full, Rosetta may replace only the inserting
principal's oldest entry or scope; it never evicts another principal's state.
Capacity failures are returned as HTTP 413 before partial cache mutation.

## Environment-backed example config

The versioned example uses these required environment variables:

```bash
export CODEX_ROSETTA_ADMIN_PASSWORD='replace-with-a-strong-secret'
export CODEX_ROSETTA_API_KEY='rsk-replace-with-a-strong-secret'
```

Startup fails if either value is empty or unresolved. Provider API keys remain
separate and use their provider-specific environment variables.

## Docker and remote access

The container listens on `0.0.0.0` so Docker can publish the gateway. This does
not relax authentication: keep the generated Admin password and gateway access
key, restrict the published port with host/network firewall rules, and place
TLS in front of any non-loopback deployment. `server.credential_visible`
controls raw Gateway/provider API credential reveal in the Admin UI and API;
do not enable it unless that reveal is explicitly needed in a trusted Admin
session. It does not mask userinfo embedded in `server.proxy` or a provider
`proxy` URL. Those connection URLs remain visible to an authenticated Admin,
so keep proxy passwords out of URLs when possible and protect Admin access.

The repository does not publish a Docker image. From the repository root, use
`make compose-up`; it rebuilds the current checkout's wheel and passes that
exact wheel into the versioned Compose build. A plain Compose invocation must
also provide `LOCAL_WHEEL` and must not rely on the old registry image name.

The Admin login limiter keys attempts by the direct peer address. Forwarded
client-IP headers are ignored because the gateway does not yet expose a
trusted-proxy allowlist. A reverse proxy therefore shares one limiter bucket;
configure its own rate limiting as an additional control. Request-log client
attribution follows the same rule and records only the direct TCP peer; it does
not treat `X-Forwarded-For` or `X-Real-IP` as authoritative.

Every `create_app()` instance owns its Admin login limiter and model-test task
registry. Login failures, task IDs, capacity, cancellation, expiry cleanup, and
shutdown therefore affect only that app. Polling or cancelling an ID through a
different app returns the same HTTP 404 as an unknown ID and does not reveal
whether the other app owns it.

Admin model tests allow at most four running tasks and retain at most 128 task
records per app. Their self-call response body has a dedicated 4 MiB incremental
read limit for both success and error responses; overflow is rejected before
full-body JSON decoding and is recorded as a stable 502-class diagnostic with
no partial body. Completed results remain compact JSON bytes until polling.
Each retained record, including its metadata, is limited to 4 MiB, and all
completed records in one app share a 32 MiB budget. Capacity enforcement evicts
only that app's oldest completed results, never active work. Running tasks count
toward the 128-record limit but not the completed-byte budget. App shutdown
cancels and awaits its own active tests and clears its own completed results.

## Outbound network and response limits

When a request is converted to Google GenAI, public HTTP(S) image URLs are
downloaded under one 30-second monotonic deadline covering DNS, connect,
redirects, response headers, and body reads. Redirect targets and every direct
DNS answer are revalidated, private/non-routable addresses are rejected, at
most three redirects are followed, and each image body is limited to 10 MiB.
The gateway runs this blocking work in a four-worker pool owned by each app;
queue waits, request cancellation, and shutdown do not release capacity until
the underlying worker has actually exited.

Gateway upstream HTTP requests force `Accept-Encoding: identity`. A response
that still declares gzip, deflate, Brotli, or another content encoding is
closed and rejected instead of being decompressed. This makes observable wire
payload bytes and decoded payload bytes identical; HTTP chunk framing is not
counted as payload. Non-streaming success bodies are limited to 50,000,000
bytes, and non-streaming error bodies plus streaming HTTP error bodies are
limited to 1,000,000 bytes. `Content-Length` is checked before reading, while
chunked and unknown-length bodies are counted incrementally. A peer-declared
HTTP chunk is consumed in fixed bounded payload subchunks (at most 64 KiB in
Gateway body reads), so the peer's declared chunk size is never materialized
before the Gateway budget is checked. Oversized or non-identity responses are
closed and surfaced as a stable gateway upstream error.

Successful SSE streams remain incremental and do not acquire a total
stream-size or duration cap. Each SSE line is limited to 1 MiB and each event's
accumulated `data:` payload is limited to 8 MiB; the event counter resets after
every delimiter. The same limits apply to converted SSE and byte-preserving
Responses passthrough. Overflow closes the upstream and surfaces a stable
Gateway safety error.

Converted provider streams accept JSON `data:` events, the explicit `[DONE]`
marker, empty `data:` keepalives, and normal SSE comments. A non-empty event
that is neither JSON nor `[DONE]` is an upstream protocol failure: Rosetta
closes it once and terminates the converted stream with a stable 502-class
error. The malformed event body is never included in the client-visible error
or ordinary/body logs. Same-protocol Responses streaming remains a
byte-preserving passthrough; it enforces the wire-size limits above without
parsing provider event JSON.

## Diagnostic data retention

Error diagnostics may contain prompts, source code, and tool payloads. Rosetta
redacts configured Gateway/provider API tokens, Bearer/Authorization tokens,
explicit token/API-key fields, and values that match a configured API token.
It deliberately preserves all other request, converted-body, response, prompt,
password, secret, client-secret, proxy-password, and personal data. Restrict
access to the data directory accordingly.

Live upstream-error log lines use the same current app/config token set even
when request-body logging is disabled. Error text is token-redacted first,
control characters and line separators are escaped onto one line, and the
final value is capped at 4,096 characters. This logging boundary preserves
prompts, personal data, ordinary `password`, `secret`, and `client_secret`
values; it is not a general-purpose privacy scrubber.

Request/response body logging is a separate opt-in controlled by
`debug.log_bodies` or `CODEX_ROSETTA_LOG_BODIES`. It uses the dedicated
`codex-rosetta-gateway.body` logger at DEBUG: enabling it does not enable other
Gateway DEBUG noise. Each app keeps its own live body-log policy and token set,
including after Admin config reloads. Original, intermediate, converted, and
upstream bodies are recursively token-redacted before JSON serialization, then
escaped onto one line and capped at 20,000 characters. Serialization failures
emit only a constant placeholder; they never fall back to the raw object or
exception text.

Body logs preserve prompts, source code, personal data, and ordinary
`password`, `secret`, `client_secret`, and proxy-password values. Treat them as
sensitive diagnostics and restrict console/file log access. Configured exact
Gateway/provider tokens, Bearer/Authorization values, explicit token/API-key
fields, and those fields inside JSON-encoded function arguments are redacted.

Request-log success and error caps are validated during both startup and Admin
hot reload. `server.request_log.success_max`, `error_max`, legacy
`max_entries`, and the `REQUEST_LOG_SUCCESS_MAX` / `REQUEST_LOG_ERROR_MAX`
environment overrides must be integers from 0 through 1,000,000; booleans,
negative values, and larger values are rejected. A cap of `0` retains no rows
of that request class and converges immediately when activated. These request
log caps do not change the independent 10,000-entry error-dump contract.

Each request or converted body is limited to 10 MiB before storage. Retained
error diagnostics are pruned only by the established 10,000-entry count limit;
there is no automatic age or total-size deletion policy. Count pruning and
manual clearing also delete unreferenced body blobs.

## Executable tool-history storage

When code-tool localization is enabled, the native/localized call mapping is
executable replay state rather than diagnostic data. Rosetta stores the exact
mapping in SQLite using AES-256-GCM with a unique nonce and authenticated scope
for every row. The SQLite columns contain ciphertext, not a redacted
`[REDACTED]` projection. Request logs, traces, error dumps, APIs, and the Admin
UI remain separate diagnostic surfaces and continue to apply the token-only
redaction policy above.

By default the first persisted mapping atomically creates
`data/tool-mapping.key` next to `gateway.db`. The data directory is mode `0700`
and the key is mode `0600`; concurrent gateway starts converge on the same
fully-written key. A deployment-managed durable secret may instead set
`CODEX_ROSETTA_TOOL_MAPPING_KEY` to one base64-encoded 32-byte key. The
environment value is not copied into SQLite or included in errors.

Treat the database and key as one backup unit. Stop writes or use a consistent
SQLite backup, and back up `gateway.db` together with `tool-mapping.key`; when
using the environment override, back up the external secret through its secret
manager. Restore both from the same backup generation. Key rotation is not
implemented: do not replace either key source while encrypted rows remain.

If encrypted rows exist and the key is missing, malformed, mismatched, or a row
fails authentication, gateway startup fails closed instead of regenerating a
key or replaying lossy history. Legacy plaintext or `[REDACTED]` mapping rows
cannot be recovered; the schema migration emits a warning and removes only
those legacy mapping rows. Expired and unused encrypted mappings retain the
configured TTL cleanup behavior.

Encrypted mapping storage also has fixed hard budgets. Ciphertext plus ownership
metadata is limited to 16 MiB per row; each session is limited to 2,048 rows or
64 MiB, each principal to 8,192 rows or 256 MiB, and the database to 32,768 rows
or 512 MiB. Upsert cleanup, replacement-aware row/byte accounting, validation,
and the final write run in one `BEGIN IMMEDIATE` transaction, so a rejected or
failed replacement preserves the previous mapping. Startup validates accounting
and all hierarchical budgets before decrypting rows; session replay performs the
same accounting check before loading ciphertext. Existing encrypted-v1 tables
gain and backfill the `mapping_bytes` column without decrypting or deleting valid
history. Capacity or inconsistent-accounting failures are fail-closed.
