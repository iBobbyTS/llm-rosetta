# Context Compaction Protocol Test

This suite verifies Codex Remote Compaction V2 routing, wire shape, persistence,
and replay through Codex-Rosetta. It does not score summary quality; use
`context_compaction_summary_quality` for the controlled fact-retention matrix.

## Scenarios

- `01`: `deepseek-v4-flash` context-limit compaction through Rosetta mode.
- `02`: `gpt-5.6-sol` context-limit native passthrough through
  `Pixel (K12)`.
- `03`: `gpt-5.6-sol` through `Pixel (K12)` to
  `deepseek-v4-flash`; require `comp_hash_changed` and Rosetta mode.
- `04`: reverse `03`; require `comp_hash_changed` and Rosetta mode.
- `05`: `deepseek-v4-flash` post-compaction exactly-once behavior. It uses the
  same large deterministic scenario as `01` but scores model command
  discipline separately from the Remote V2 protocol.

Every cell uses a separate timestamped run root, Codex Home, copied gateway
configuration, port, gateway process, and Gateway Logs trace.

## Automated native smoke run

`run_live.py` automates the isolated setup, dual-auth validation, gateway
lifecycle, a real Codex app-server session, and bounded native-compaction
checks. It defaults to task `02` with `gpt-5.6-terra`, which is an intentional
model substitution for a lower-cost native wire smoke test. The default
`--trigger manual` path creates a seed turn, invokes the official
`thread/compact/start` API, and runs a follow-up turn, reproducing the same
`user_requested` path as the interactive `/compact` command:

```bash
conda run -n llm-rosetta python tests/live_agent/context_compaction/run_live.py
```

The command prints the timestamped run root and writes a credential-free
`artifacts/automation-result.json`. Success requires exactly one in-band
trigger, `wire_passthrough=true`, no trigger-request upstream error, a later
installed `compaction` input, one native `user_requested` request profile, the
final marker, and a zero Codex exit status. Use `--model gpt-5.6-sol` to run the
task's canonical model instead. Use `--trigger context-limit` to run the
original deterministic auto-threshold scenario. The runner never reuses or
stops the user's main gateway.

Each run copies `/Users/ibobby/.codex-multi-2/auth.json` into its ignored,
timestamped `codex_home` with mode `0600`, points `CODEX_HOME` at that directory,
and verifies ChatGPT OAuth before starting Codex. On macOS, the manual path uses
the installed Codex Desktop DeviceCheck module to answer app-server
`attestation/generate` requests. Attestation values stay in process memory and
are never written to the protocol artifact or stream trace.

## Provider routing

In the copied config only, route every `gpt-5.6-sol` cell to the existing
provider named exactly `Pixel (K12)`. Keep `deepseek-v4-flash` on its existing
sole provider. Verify both provider names and actual upstream models from the
trace; model aliases alone are not evidence.

For context-limit tasks `01`, `02`, and `05`, set:

```toml
model_provider = "codex_rosetta"
model_auto_compact_token_limit = 19000
```

The deterministic command emits more than 100,000 characters of neutral filler.
The tested model must configure both the outer Code Mode `exec` cell and its
nested command call to retain at least 20,000 output tokens; otherwise either
layer can truncate the result and keep the retained payload below the required
60,000 characters. Record the baseline
and post-compaction Codex token counts, selected command output-token cap, and
retained command-output character count. Require both token counts below
19,000, a cap of at least 20,000 tokens, and at least 60,000 retained
characters. Tasks `01` and `02` pass their protocol scope after at least one
complete trigger/result/install/replay chain. Later model restarts or additional
complete compactions are recorded as deviations and do not reverse the protocol
result. Task `05` separately requires exactly one command start, one compaction,
one Rosetta mapping, and the exactly-once marker. A run without the measured
context-limit shape is invalid and must be reported rather than silently
accepted.

For model-switch tasks `03` and `04`, use the normal token limit. Retain the
first execution's thread id and run:

```bash
codex exec resume -m TARGET <thread-id> \
  "Proceed with the resume phase of the existing task."
```

This explicit target selection is limited to the model-switch protocol cells;
ordinary cells use the isolated config default without `-m`. Non-interactive
resume requires the new prompt. The first phase's fixed code is
not repeated in that prompt, so the target marker proves context continuation.

## Result interpretation

Follow [`EVALUATION.md`](EVALUATION.md). Keep protocol and model-behavior
classifications independent. Protocol tasks require a genuine trigger item,
exact reason/mode, at least one canonical compaction output, an installed
follow-up input, the expected mapping lower bound or native zero count, and the
protocol marker. Task `05` additionally requires exact cardinality. Do not
count strings that merely appear inside prompts, source listings, tool output,
or errors.

Classify each compact-related request from the strongest bounded evidence
available. Prefer the Gateway request-log profile plus the HTTP path; use the
wire body when the request reaches stream tracing. Rosetta may answer a Remote
V2 trigger early, before creating a normal stream-trace request record, so an
absent `raw_passthrough_request` is not evidence that compaction did not occur:

- `legacy_remote_compact`: `POST /v1/responses/compact`;
- `remote_v2_in_band`: `POST /v1/responses` whose final input item is the sole
  `compaction_trigger`;
- `post_compaction_followup`: `POST /v1/responses` carrying an installed
  `compaction` or accepted `compaction_summary` item without a new trigger;
- `local_internal`: a rollout `compacted`/`context_compacted` event with none
  of the wire shapes above;
- `ordinary_response`: none of the compact request/event evidence above.

Also record the `x-codex-turn-metadata` reason, model, route, prompt-cache key,
response item type, and the next request's installed item. This combination
distinguishes Remote V2 and proves the full trigger/result/replay chain even
when the current stream trace does not record the early-response request. A
Gateway request-log profile with `compaction_mode` and `compaction_reason` is
created only after a valid in-band trigger is recognized; combine it with the
registered `/v1/responses` route and mapping/install evidence. Do not classify
a legacy compact request unless a bounded access log or capture proves
`/v1/responses/compact`.
