# Audit Remediation Evidence

Run: `20260720-2255`
Repository base/environment: `353a795a00fb42ecbe307653f12877900e831bf9`; remediation working tree; macOS; Python 3.14.6 via `llm-rosetta`
Real API calls: none

## Evidence Index

| Unit | Status | Coverage IDs | Finding | Summary |
| --- | --- | --- | --- | --- |
| UNIT-001 | Verified | PROVIDER-01, TOOL-01, SCN-03/04/05, CTRL-03 | AUD-019 | duplicate-preserving outer JSON plus bounded provider-schema argument semantics |
| UNIT-002 | Verified | SCN-04/05, GP-003 | AUD-019 | Responses and Chat state keys aligned to actual converter consumer identities |
| UNIT-003 | Verified | REL-01, CTRL-05 | AUD-019 | focused, lint, full deterministic checks, diff check, and CodeGraph synchronization |

## UNIT-001 — Implementation boundary

Changed implementation:

- `src/codex_rosetta/observability/redaction.py`
  - adds a duplicate-preserving outer JSON representation;
  - `contains_json_semantic()` checks every object member without recursively parsing arbitrary strings.
- `src/codex_rosetta/gateway/transport/credential_semantics.py`
  - owns documented Responses/Chat argument schemas and live reconstruction state;
  - parses only known function/tool argument JSON strings;
  - bounds live state at 1 MiB, 4096 fragments, and 4096 identities;
  - clears mappings/buffers on argument done, output-item done, response completed, or stream finish.
- `src/codex_rosetta/gateway/transport/credential_redaction.py`
  - invokes the semantic gate for parsed and raw return paths;
  - keeps the existing complete-event hold and active-provider credential inventory;
  - aligns initial UTF-8 BOM handling with the SSE parser.

Tests cover duplicate member collisions, known nested argument JSON, unknown-string non-recursion, safe duplicate byte identity, Responses/Chat cross-event reconstruction, BOM, resource bounds, and AUD-020 active-provider-only behavior.

## UNIT-002 — Consumer identity re-audit

The first implementation draft was independently challenged before full verification:

1. Safe duplicate routing/identity members initially failed closed merely because they were duplicated. The repair now uses last-member-wins for protocol routing while the duplicate-preserving redactor still inspects every member for credentials.
2. Responses initially keyed state by `item_id` before `call_id`, which disagreed with `resolve_call_id()`. The repair registers a bounded `item_id -> call_id` map from output-item events and prefers direct `call_id`.
3. Chat initially could split an id-only first chunk from an index-only continuation. The repair mirrors the converter's registered tool-call order.

Regression tests exercise each identity-shape transition and prove the completing event is blocked before downstream reconstruction can obtain the active credential.

## UNIT-003 — Verification

| Command/check | Result | Notes |
| --- | --- | --- |
| focused redaction/transport/provider-return/HTTP-limit/Responses+Chat stream and tool suite | `322 passed in 11.47s` | independent main-agent run |
| direct `make lint` outside the project environment | failed before checks because `ruff` was not on PATH | environment invocation error only |
| `conda run -n llm-rosetta make lint` | passed | Ruff check/format, ty, complexity ratchet |
| `conda run -n llm-rosetta make test` | `3604 passed, 5 skipped, 11 warnings in 18.82s` | integration excluded; no real API calls |
| `git diff --check` | passed | no whitespace errors |
| `codegraph sync` | passed | index synchronized after source changes |

## Residual Risk

- Real provider/Codex parsers, timing, and deployed sinks remain Unknown by audit policy.
- New provider-specific fields that downstream consumers parse as JSON require explicit schema registration and tests.
- The new bounds intentionally fail closed for unusually large or fragmented tool arguments; availability for those payloads is not promised by the approved profile.
- Encoded, compressed, hashed, or covert exfiltration outside documented parser semantics remains outside the exact-reflection guarantee.
