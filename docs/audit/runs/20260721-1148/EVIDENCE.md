# Audit Evidence

Run: `20260721-1148`
Repository base: `353a795a00fb42ecbe307653f12877900e831bf9` plus current working tree
Mode: independent omission audit; read-only
Real API calls: none

## Source evidence

| Finding | Current source path | Observable defect |
| --- | --- | --- |
| AUD-022 | `src/codex_rosetta/gateway/transport/credential_semantics.py:132-141` | `rstrip()` leaves leading whitespace; `startswith("{")` gates semantic parse |
| AUD-023 | `src/codex_rosetta/gateway/transport/credential_semantics.py:247-271` | append-only arrival list is indexed by wire `index` |
| AUD-024 | `src/codex_rosetta/converters/openai_responses/message_ops.py:330-402`; `tool_ops.py:783-847` | result type omitted; unknown item ignored; output fixed to function result |

## Deterministic probes

All probes used the synthetic credential `secret` and in-process classes only.

1. `ProviderCredentialSemanticGate(SecretRedactor({"secret"}),
   "openai_responses")` received deltas `  {"value":"\\u00` and
   `73ecret"}`. Both calls returned normally; the buffer ended as a complete
   credential-bearing JSON object. The same redactor reports
   `contains_json_semantic(...) == True` for that complete value when called
   directly.
2. `ProviderCredentialSemanticGate(..., "openai_chat")` received tool calls in
   order `(index=1,id=call-1)`, `(index=0,id=call-0)`, then `(index=1)` with the
   completing fragment. The final fragment was stored under `call-0`; no
   collision was raised and `call-1` remained incomplete.
3. `OpenAIResponsesConverter().request_from_provider()` received a
   `computer_call` followed by a `computer_call_output` containing a synthetic
   screenshot. Returned IR contained only the assistant `computer_use` part;
   the output item was absent.

## Regression baseline

Command:

```text
conda run --no-capture-output -n llm-rosetta pytest -q \
  tests/gateway/test_transport_credential_redaction.py \
  tests/converters/openai_responses/test_converter.py \
  tests/converters/openai_responses/test_stream.py \
  tests/converters/openai_responses/test_tool_ops.py
```

Result: `242 passed in 1.82s`. Existing tests do not include the three counterexamples.

`git diff --check` passed, and `codegraph sync` reported the repository index was
already up to date.

## Limits

No real upstream, Codex process, external sink, browser/LAN deployment, or production
runtime was exercised. No code, config, ledger status, or git history was rewritten by
the auditing subagent.
