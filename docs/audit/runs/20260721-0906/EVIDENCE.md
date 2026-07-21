# Audit Remediation Evidence

Run: `20260721-0906`
Repository base/environment: `353a795a00fb42ecbe307653f12877900e831bf9`; remediation working tree; macOS; Python 3.14.6 via `llm-rosetta`
Real API calls: none

## Evidence Index

| Unit | Status | Coverage IDs | Finding | Summary |
| --- | --- | --- | --- | --- |
| UNIT-001 | Verified | PROVIDER-01, TOOL-01, SCN-03/04/05, CTRL-03 | AUD-019 | converter-owned embedded-JSON inventory covers every current Responses second-parse field and custom-input SSE accumulation |
| UNIT-002 | Verified | TOOL-01, SCN-03/05, IF-05, CP-05/17 | AUD-021 | canonical `computer_call` validates and round-trips in Responses; unsupported target and stream conversions fail explicitly |
| UNIT-003 | Verified | REL-01, CODEX-01 | AUD-019, AUD-021 | focused, lint, full deterministic, compatibility, diff, and CodeGraph checks |

## UNIT-001 — Credential consumer boundary

- `RESPONSES_EMBEDDED_JSON_FIELDS` is the executable inventory shared by
  Responses message routing and `ProviderCredentialSemanticGate`.
- The inventory covers `function_call.arguments`, `mcp_call.arguments`,
  `custom_tool_call.input`, `shell_call.arguments`, and
  `code_interpreter_call.arguments`.
- Function-argument and custom-input delta/done events use the same bounded
  call identity and fragment accumulator.
- The converter contract test iterates the inventory and proves every declared
  field is actually decoded to the same semantic JSON object.
- Adversarial transport tests prove escaped active-provider credentials are
  blocked in every current non-streaming consumer and across custom-input SSE
  events before downstream reconstruction.

The gate still does not recursively parse unrelated strings. Existing active-
provider-only ownership, duplicate-member inspection, safe byte identity, and
1 MiB/4096-fragment/4096-identity bounds remain unchanged.

## UNIT-002 — Computer-call contract

- Local OpenAI SDK `2.45.0` identifies the canonical item as
  `type="computer_call"`; the public TypedDict now matches that wire spelling
  and current fields.
- IR explicitly admits `tool_type="computer_use"` and preserves the complete
  native Responses item in provider metadata.
- Non-streaming Responses conversion validates and restores the original item
  structure.
- OpenAI Chat, Anthropic, and Google target converters reject `computer_use`
  with `NotImplementedError` instead of inventing a function call.
- The generic Responses streaming bridge rejects `computer_call` on added,
  done, or completed input rather than silently dropping it. Direct Responses
  transport remains outside the converter and retains its byte-transparent
  behavior under the transport safety envelope.

The adjacent reviewed Codex alpha.23 source has no computer-call owner, so this
is recorded as generic Responses protocol compatibility, not a Codex feature
claim.

## UNIT-003 — Verification

| Command/check | Result | Notes |
| --- | --- | --- |
| pre-fix focused oracle | `9 failed, 218 passed` | failures matched the three missing fields, custom SSE accumulation, invalid/lost computer call, and three silent cross-format downgrades |
| post-fix focused suite | `326 passed` | security inventory, converter, stream, type, and cross-format contracts |
| direct `make lint` outside the project environment | failed before checks because `ruff` was not on PATH | environment invocation error only |
| `conda run -n llm-rosetta make lint` | passed | Ruff check/format, ty, and complexity ratchet |
| `conda run -n llm-rosetta make test` | `3621 passed, 5 skipped, 11 warnings in 21.26s` | integration excluded; no real API calls |
| `conda run -n llm-rosetta make check-codex-compat` | passed | source `655224ffae09`; no changed contract rows |
| `git diff --check` | passed | no whitespace errors |
| `codegraph sync` | passed | repository index synchronized after source changes |

## Phase-Separated Re-audit

After implementation and full verification, the frozen acceptance criteria were
rechecked independently from the edit sequence:

1. Every current converter second-parse field is present in the executable
   inventory and is exercised through both converter and return-gate tests.
2. Custom-tool input uses item/call identity mapping and is blocked on the
   completing event; safe unrelated strings remain unparsed.
3. Canonical computer items survive the validated non-streaming Responses
   round trip.
4. Every unsupported target or streaming bridge path raises explicitly.
5. No new Codex capability, generalized computer-control mapping, real-call,
   deployment, migration, availability, or recovery promise was introduced.

No additional Must Fix or owner decision was found inside the targeted cone.

## Residual Risk

- Real provider/Codex timing, external parsers, deployment, and sinks remain
  Unknown by audit policy.
- A future converter field that decodes embedded JSON must join the shared
  inventory and its executable contract test.
- Cross-format and converted-stream computer-control support remains explicitly
  unsupported until separately designed and authorized.
