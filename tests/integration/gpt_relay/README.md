# GPT Relay Compatibility Test

This suite measures one real OpenAI Responses-compatible relay under two Codex
provider identities: `name = "OpenAI"` and a non-OpenAI name. It is provider
neutral; TURNING is only one representative runtime parameter.

The core question is whether the same real relay completes requests after the
provider identity changes the Codex wire request. Mock responses are used only
by infrastructure unit tests and never count as relay compatibility evidence.

## Scenarios

| ID | Identity/auth | Real-service assertion |
| --- | --- | --- |
| C0 | direct API key | Baseline Responses SSE completes. |
| C1 | relay name/API key | A non-OpenAI Codex agent loop completes. |
| C2 | `OpenAI`/API key | The same loop completes with OpenAI-only sequential-cutoff and item-metadata behavior observable; API-key auth remains uncompressed. |
| C3 | `OpenAI`/synthetic Codex-backend auth | Codex emits Zstd and the real relay accepts the request after the local proxy replaces only authentication. |
| C4 | `OpenAI`/synthetic Codex-backend auth | An injected old-model compact `InvalidRequest` is followed by a current-model compact and normal follow-up completed by the real relay. To isolate fallback from C3, the proxy records Codex's Zstd input and normalizes it before upstream. |
| C5 | non-OpenAI/synthetic Codex-backend auth | The real relay completes the negative control without OpenAI-only compression, sequential cutoff, or model fallback. |

Synthetic Codex-backend auth activates the same local source gates as a Codex
backend login. The proxy removes that synthetic credential, installs the
selected relay API key, and forwards the request to the real service. Results
always label these cells with `synthetic_codex_backend_auth: true`; they prove
wire compatibility, not native relay support for ChatGPT login.

C3 is the authoritative unmodified Zstd acceptance test. C4 deliberately sets
`transport_adapted: true`: its proxy decompresses the already-recorded Codex
request before real upstream forwarding so a relay that fails C3 can still be
tested for remote-compact and model-fallback semantics. C4 therefore proves
fallback compatibility behind such a middle-layer adaptation, not direct Zstd
support.

## Run

Use the repository's Python 3.14 environment. Run one scenario per minute so
every cell receives its own `tmp/agent_testing_workspace/YYYYMMDDHHMM` root:

```bash
conda run -n llm-rosetta python -m tests.integration.gpt_relay.run \
  --scenario C2 \
  --provider-name TURNING \
  --gateway-config ~/.config/codex-rosetta-gateway/config.jsonc \
  --model gpt-5.6-terra
```

Run C0 through C5 separately. C1 and C2 copy
`tests/agent_workspace/command_execution/01`, adding a small real agent/tool
loop without scoring general model ability. C3-C5 use the path-pinned Codex
source harness in `codex_harness/`.

The harness follows the sibling Codex source toolchain and currently invokes
the installed Rust `1.95.0` toolchain explicitly.

The source Gateway configuration is copied but never edited. Capture records
request keys, input item types, gated fields, encoding, requested/forwarded
model, status, and SSE event names. It does not record prompt text, request
bodies, authorization headers, cookies, or API keys. All artifacts remain in
the run root; this suite does not enable Web Admin Gateway Logs and therefore
does not create a RAM Disk trace.

The authoritative result is `artifacts/evaluation.json`. Process exit zero is
insufficient: success also requires a real 2xx upstream response, a completed
Responses stream, and the scenario-specific request shape.

`success_with_deviation` is used when the core sequence and final completion
succeed after a bounded recoverable deviation, such as one `response.failed`
followed by Codex's successful retry. The failed intermediate stream remains in
the result and is not silently treated as a clean success.

Recorded, sanitized real-service results live under `results/`; raw run
artifacts and copied credentials remain only under the ignored `tmp/` root.
