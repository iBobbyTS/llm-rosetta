# Periodic Audit Evidence

Run: `20260720-2347`
Repository base/environment: `353a795a00fb42ecbe307653f12877900e831bf9`; current remediation working tree; macOS; Python 3.14.6 via `llm-rosetta`
Real API calls: none
Authorized remediation: no

## Evidence Index

| Unit | Status | Coverage IDs | Finding | Summary |
| --- | --- | --- | --- | --- |
| UNIT-001 | Confirmed | PROVIDER-01, TOOL-01, SCN-03/05, CTRL-03, GP-003 | AUD-019 | the semantic return gate omits three current Responses fields that the converter decodes as JSON |
| UNIT-002 | Confirmed | TOOL-01, SCN-03/05, IF-05 | AUD-021 | the authoritative computer-tool wire type is silently dropped while the converter's fallback spelling violates the IR tool-type contract |
| UNIT-003 | Verified with gap | REL-01, CTRL-05 | AUD-019/AUD-021 | focused, lint, full deterministic, Codex compatibility, and diff checks are green but lack the failing oracles |

## UNIT-001 — Responses consumer schemas omitted from the credential gate

### Source trace

- `src/codex_rosetta/gateway/transport/credential_semantics.py:15-16,92-104` registers only `function_call` and `mcp_call` and inspects only their `arguments` fields for non-streaming Responses documents.
- `src/codex_rosetta/converters/openai_responses/message_ops.py:329-338,363-380` routes six tool item names to `p_tool_call_to_ir()`.
- `src/codex_rosetta/converters/openai_responses/tool_ops.py:637-647,685-724` calls `json.loads()` for:
  - `shell_call.arguments`;
  - `code_interpreter_call.arguments`;
  - valid-JSON `custom_tool_call.input`.
- `src/codex_rosetta/converters/openai_responses/converter.py:390-434` feeds these decoded values into the validated IR response.
- `src/codex_rosetta/pipeline.py:413-440` then converts that IR into the downstream source-provider response.

The gate's manual schema registry therefore disagrees with the current consumer inventory. The historical AUD-019 closure remains valid for duplicate members, Responses/Chat function arguments, consumer identities, and state bounds, but it is not complete for the current Responses converter.

### Deterministic adversarial probe

The probe used active credential `secret` and wire text `{"value":"\u0073ecret"}`. The raw text does not contain the credential bytes; each current consumer's second JSON decode reconstructs the credential.

| Responses item | Gate result | Converter result |
| --- | --- | --- |
| `custom_tool_call.input` | allowed | `tool_type=custom`, `tool_input={'value': 'secret'}` |
| `shell_call.arguments` | allowed | `tool_type=code_interpreter`, `tool_input={'value': 'secret'}` |
| `code_interpreter_call.arguments` | allowed | `tool_type=code_interpreter`, `tool_input={'value': 'secret'}` |

A same-format Responses round trip makes the release concrete:

```text
gate=allowed
downstream={'type': 'custom_tool_call', 'call_id': 'call_1', 'name': 'exec', 'input': '{"value": "secret"}'}
```

This reopens `AUD-019 / Must Fix / Agent-Fixable`. The active-provider-only domain recorded by AUD-020 is unchanged because the probe uses the active provider credential.

The converter also supports `response.custom_tool_call_input.delta/done`, while the semantic gate currently handles only `response.function_call_arguments.delta/done`. This run records that stream schema as an unverified re-audit target rather than claiming a stream leak: the stream converter's custom input semantics differ from the confirmed non-streaming second-parse path.

## UNIT-002 — Responses computer-tool wire/IR contract drift

### Source trace

- `src/codex_rosetta/types/openai/responses/response_types.py:513-520` declares the response item type as `computer_tool_call`.
- `tests/test_types/openai_responses/test_type_compatibility.py:633-646` freezes the same spelling.
- `src/codex_rosetta/converters/openai_responses/message_ops.py:329-338` instead recognizes `computer_call` and does not recognize `computer_tool_call`.
- Unknown ordinary item types are skipped by `p_messages_to_ir()` at `message_ops.py:363-414`, so the authoritative spelling produces no assistant content.
- The fallback `computer_call` branch emits `tool_type="computer_use"` at `tool_ops.py:685-700`.
- `src/codex_rosetta/types/ir/parts.py:108-121` does not permit `computer_use` in `ToolCallPart.tool_type`.

### Deterministic full-converter probe

```text
computer_tool_call choices=[]
computer_call ValidationError: Expected one of
('function', 'mcp', 'custom', 'web_search', 'code_interpreter', 'file_search')
at 'choices[0].message.content[0].tool_type', got 'computer_use'
```

The canonical response item is silently discarded; the converter's alternate spelling is rejected during IR validation. This opens `AUD-021 / Must Fix / Agent-Fixable` because a supported typed tool response cannot traverse the central conversion boundary without protocol loss or failure.

## UNIT-003 — Deterministic verification

| Command/check | Result | Notes |
| --- | --- | --- |
| `conda run -n llm-rosetta python -m pytest tests/gateway/test_transport_credential_redaction.py tests/observability/test_redaction.py tests/converters/openai_responses/test_converter.py tests/converters/openai_responses/test_tool_ops.py tests/converters/openai_responses/test_stream.py -q` | `249 passed in 1.67s` | relevant suites are green but contain neither failing oracle |
| `conda run -n llm-rosetta make lint` | passed | Ruff check/format, ty, complexity ratchet |
| `conda run -n llm-rosetta make test` | `3604 passed, 5 skipped, 11 warnings in 18.64s` | integration excluded; no real API calls |
| `conda run -n llm-rosetta make check-codex-compat` | passed | source commit `655224ff...`; 11 rows remain possibly unchanged semantic checks |
| direct isolated probes above | reproduced | no network, provider, deployment, or durable state |

Passing tests do not close either finding because the required security and computer-tool contract outcomes are absent from the test portfolio.

## Residual Risk and Boundaries

- Real provider/Codex parsers, timing, external sinks, browser/LAN deployment, and production telemetry remain Unknown by audit policy.
- The non-streaming custom/shell/code-interpreter credential reconstruction is confirmed. Custom-tool streaming remains a next re-audit target, not a confirmed stream disclosure in this run.
- No product code was repaired, no deployment occurred, and no commit or real API call was made.
- Existing duplicate-preserving JSON, function/Chat state identity, resource-bound, safe-byte, collision, and active-provider-domain evidence remains historical evidence but cannot support a complete AUD-019 closure while consumer fields are omitted.
