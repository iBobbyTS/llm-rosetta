# Audit Evidence

Run: `20260721-1232`
Repository head: `04efc74e0425c42bb906581b61c0c0be6976841`
Mode: targeted remediation re-audit
Real API calls: none

## Remediation evidence

| Finding | Code/test evidence | Result |
| --- | --- | --- |
| AUD-022 | `_append()` trims both sides of the bounded argument buffer before the completion and semantic checks; `test_raw_sse_blocks_whitespace_padded_argument_reconstruction` covers the leading/trailing whitespace counterexample | blocked; no downstream bytes released |
| AUD-023 | Chat gate stores bounded `index -> call_id` and reverse mappings, rejects remaps/conflicts/missing identities, and clears state before failing closed; out-of-order index regression covers index 1 then 0 arrival | blocked; stable identity preserved |
| AUD-024 | `p_messages_to_ir()` rejects `computer_call_output` with `NotImplementedError`; positive `computer_call` round-trip remains covered | explicit fail-closed rejection; no silent drop |

## Verification commands

```text
conda run -n llm-rosetta pytest -q \
  tests/gateway/test_transport_credential_redaction.py \
  tests/converters/openai_responses/test_converter.py \
  tests/converters/openai_responses/test_stream.py \
  tests/converters/openai_responses/test_tool_ops.py \
  tests/converters/test_computer_tool_contract.py
```

Result: `248 passed`.

```text
conda run -n llm-rosetta make lint
```

Result: passed (`ruff`, `ruff format --check`, `ty`, and complexity ratchet).

```text
conda run -n llm-rosetta make test
```

Result: `3624 passed, 5 skipped, 11 warnings`.

No real provider/API/Codex call, deployment, or external sink was used. The
remaining runtime/provider timing and external-sink gaps are unchanged.
