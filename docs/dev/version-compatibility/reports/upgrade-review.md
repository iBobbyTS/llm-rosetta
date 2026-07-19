# Codex 0.145.0-alpha.23 Upgrade Review
Date: 2026-07-18
Codex version: 0.145.0-alpha.23

## Decision

- Review mode: **full inventory review, source-first**.
- Rosetta's source adaptation and deterministic checks are complete.
- Codex 0.145.0-alpha.23 compatibility is **not approved**: two runnable
  live-agent cells failed, two compaction cells failed, and several mandatory
  gates could not run with the supplied configuration/environment.
- The package remains `0.144.0.r0`. No release, commit, or source-compatibility
  claim was made.
- `codex-source-contract.json` is refreshed to the reviewed target source so
  future drift is detectable. This records the implemented source contract; it
  is not an adoption approval.

The prior `0.142.0` through `0.144.6` report was documentation-only. This work
repeated the complete inventory, compared Codex and Rosetta source directly,
implemented the gaps, and then exercised the exact target binary. Developer
documentation was used as an index, not as proof.

## Inspection identities

| Identity | Value |
| --- | --- |
| Inspection date | 2026-07-18 |
| Installed Codex CLI (not used as target evidence) | `codex-cli 0.144.6` |
| Target source tag | `rust-v0.145.0-alpha.23` |
| Target source commit | `655224ffae098a85efeddf8289171ff3bd2624d1` |
| Target debug binary | `../openai-codex-src/codex-rs/target/debug/codex`, reporting `codex-cli 0.145.0-alpha.23` |
| Range baseline | `rust-v0.142.0`, `3a76f3ac68c8949d1cac6ea769b6ec7b8953a415` |
| Previous source | `rust-v0.144.6`, `5d1fbf26c43abc65a203928b2e31561cb039e06d` |
| Rosetta starting commit | `5dd45e7e60f8b5dacea321002b0a55a85b01bf17` plus the uncommitted adaptation |
| Rosetta package version | `0.144.0.r0` |

The target checkout was clean and detached at the exact tag. The source
manifest, target binary, installed CLI, and Rosetta package version were kept as
separate identities.

## Inventory and reverse-map method

1. Read the centralized ledger, source map, upgrade checklist, prior reports,
   and paired English/Chinese references.
2. Used CodeGraph before local source inspection, then followed Rosetta request,
   response, stream, tool, search, catalog, and session owners.
3. Compared the complete Codex source ranges. The `0.142.0..0.144.6` range
   contains 1,420 changed files; `0.144.6..0.145.0-alpha.23` contains 1,204.
   These are scope checks, not compatibility-point counts.
4. Compared the target `models-manager/models.json`, protocol structs, API
   endpoints, Code Mode descriptions, tool specs, SSE usage, compaction, skills,
   and runtime/auth paths against their Rosetta owners.
5. Scanned outside `docs/dev/version-compatibility/`. No second authoritative
   upgrade procedure or compatibility ledger remains. Outside pages are
   user-facing references, module READMEs, or historical work records.

## Implemented adaptation

| Boundary | Source-derived change |
| --- | --- |
| Model contracts | Replaced obsolete `supports_reasoning_summaries` with default-true `supports_reasoning_summary_parameter`; added permissions and auto-review contract extraction; refreshed eight-entry official catalog byte-for-byte from the target and documented intentional third-party preset differences. |
| Response IDs | Prevented empty `msg_`/`fc_` identifiers and kept one valid stable ID across streaming events and replay. |
| Usage | Preserved alpha.23 `cache_write_input_tokens` through non-streaming and streaming IR conversion as cache-creation usage, including reverse conversion. |
| Search | Preserved omitted versus explicit-empty `results` and supported structured `text_result` payloads without narrowing unknown JSON. |
| Code Mode | Accounted for deferred-only MCP declarations when rendering Shared MCP Types; refreshed the reviewed 53-item static tool catalog binding to the exact target commit. |
| Tool localization | Corrected model-facing deferred names to hyphenated names while retaining native dotted names. |
| Documentation | Centralized alpha.23 ownership/evidence, fixed the Kimi K3 preset gap, and updated matching English/Chinese model and compatibility references. |
| Release validation | Added prerelease-plus-`rN` validation, including the PEP 440 normalization `0.145.0a23.post0`. |

## Deterministic gates

| Check | Result |
| --- | --- |
| Targeted converter/gateway/source-contract tests | 246 passed; the subsequent stream/tool-focused rerun passed 165 tests |
| `conda run -n llm-rosetta make test` | **3422 passed, 5 skipped**, 11 warnings |
| `conda run -n llm-rosetta make lint` | Passed Ruff check/format, ty, and complexipy |
| `python -m build` in `llm-rosetta` | Passed; built `codex_rosetta-0.144.0.post0` wheel and sdist |
| `make build` wrapper | Interrupted during its broad pre-clean because preserved live-run roots made `find .` traverse for minutes; the actual build command passed |
| `make check-codex-compat` | Passed against the exact target: 22 high-confidence unchanged, 11 possibly unchanged, 0 changed |
| `make check-release-version RELEASE_TAG=v0.144.0.r0` | Passed for the unchanged package version; prerelease target syntax is covered by unit tests |
| Official model catalog comparison | Byte-identical to target `models-manager/models.json` |
| Static tool catalog checks | 53 reviewed items, exact alpha.23 tag/commit binding, tests passed |
| EN/ZH relative-path parity | Passed: 0 English-only and 0 Chinese-only paths |
| Markdown/link and whitespace checks | Passed; the literal `tools[entry.name](...)` prose is not a Markdown link |

The direct `make test` attempt outside the configured Conda environment could
not find `pytest`; the required environment run above is the authoritative
result.

## Live-agent execution

All runnable cells used the exact alpha.23 binary, local Gateway mode, an
isolated copy of `~/.config/codex-rosetta-gateway` with only the port changed,
its configured keys, and `/Users/ibobby/.codex-multi-2/auth.json`. GPT cells used
`gpt-5.6-terra`, non-multimodal third-party cells used
`deepseek-v4-flash`, and multimodal cells used `mimo-v2.5`.

The per-attempt route, thread, marker, evaluation, and per-request cache usage
are in [live-evidence.md](live-evidence.md).

The 2026-07-19 new-key retest is recorded in the appendix. DeepSeek task 03
failed again, while Terra task 03 passed. The raw Chat arguments show that
DeepSeek generated a literal backslash-plus-`n` and restarted the process;
Rosetta preserved that value. Terra generated a JavaScript newline escape and
continued the original session successfully. This is a model-facing
tool-argument reliability failure, not a Rosetta serialization failure.

The first additional `deepseek-v4-pro` retest failed: it received
`INPUT:VALUE` but restarted the command three times without any `write_stdin`
call. A later retest after the Chat Default continuation example succeeded: it
issued one `exec_command`, reused the same session with one `write_stdin`, and
sent the required newline, returning `RESULT:INPUT_OK`. This confirms the
original issue was model-facing prompt/tool-use behavior rather than a
Rosetta session or converter defect. The additional MiMo
retest reached the Images endpoint with the refreshed key, but the endpoint
still did not expose `gpt-image-2`.

The `glm-5.2` control was rerun after adding Chat default profile guidance to
both `exec_command` and `write_stdin`. The upstream request still exposed both
as independent Chat functions. GLM kept one process session and called
`write_stdin`, but its raw function arguments contained an over-escaped
`chars: "rosetta\\\\n"`; Rosetta preserved that exact value, so the process
remained blocked waiting for a newline. This confirms that `write_stdin` is
expanded outside `exec` for Chat upstreams and that the remaining failure is
model-side JSON escaping rather than Rosetta session or serialization loss.
Adding an explicit `exec_command` → `write_stdin` example to the profile was
also tested; it improved the documented sequence but did not change GLM's
over-escaping behavior.

### Ordinary suites

| Suite | Result |
| --- | --- |
| Command execution | 7/8 original final cells passed. Terra task 03 passed in the 2026-07-19 new-key retest. DeepSeek-v4-pro first failed but passed after the Chat Default continuation example; GLM task 03 still fails on over-escaped newline arguments. The remaining GLM failure is model-facing, not a Rosetta conversion failure. |
| Deferred discovery | 14/14 passed across Terra and DeepSeek. |
| Built-in tools | 11/11 passed: Terra 5, DeepSeek 4, MiMo view transport and visual recognition 2. |
| Local skills and namespace tools | 4/4 passed; native dotted and model-facing hyphenated names were both observed. |
| Subagent tools | 12/12 passed across Terra and DeepSeek. |
| Runnable network search | 6/6 final cells passed for tasks 01, 02, and 05 across both text models. Terra task 01 needed one retry after an upstream 429. |
| Image generation | Failed. MiMo discovered the correct image tool and reached the Images endpoint with both tested keys; the endpoint returned `404 model_not_found` for Codex's alpha.23 `gpt-image-2`, and the new-key retest made no capability difference. |

This is 54 passing and 2 failing final cells among 56 runnable ordinary cells.
Network tasks 03/04 were not runnable because the supplied configuration has no
`server.web_run.base_url`/token sidecar. The configuration was not expanded
beyond the user's boundary.

Browser/Computer Use was not executed: its maintained suite requires a fresh
GUI main-executor task plus a separate judge, which cannot be validly created
inside this task. This is `invalid_execution`, not evidence that Browser is
unavailable. Orchestrator-skill cells were `runner_not_supported` because the
supplied config has no orchestrator provider. Formal `agentabi` was not run
because no `agentabi` Conda environment or importable package exists; nothing
was installed implicitly.

### Compaction suites

| Cell | Result |
| --- | --- |
| DeepSeek context-limit task 01 | Failed infrastructure contract: the model reran the one-shot scenario three times, producing three compactions/mappings instead of exactly one. |
| Terra official context-limit task 02 | Failed official evaluation: compact/resume markers passed, but raw-wire passthrough was false. |
| Terra manual app-server task 02 | Completed with one user-requested compaction, raw-wire passthrough, installed follow-up, and one native profile mapping. |
| Terra→DeepSeek task 03 | Completed; one changed compaction hash and one mapping on the same thread. |
| DeepSeek→Terra task 04 | Completed with the same invariants. |
| Terra and DeepSeek summary-quality cells | `not_scored`: their baseline contexts (15,270 and 17,423 tokens) exceeded the suite's 15,000-token precondition. Diagnostic output is retained but is not a pass/fail quality claim. |

Thus the scored/executable compaction evidence is three completed and two
failed cells, plus two explicitly unscored quality cells.

### Cache-continuation evidence

The original matrix appendix records 227 upstream requests and 154 adjacent
non-first deltas; the three follow-up cells add 18 source requests without
being folded into a new combined cache aggregate. No aggregate hit rate is claimed.
Sixty-one original deltas were within ±200 tokens.
Larger deltas were inspected and attributed to uncached conversation suffixes,
backend block alignment, subagent instruction changes, deliberate model/profile
switches, or three backend misses. Eight requests omitted usage: one 429, four
failed/timeout command requests, and three completed Terra subagent requests.
They remain missing rather than being synthesized as zero.

## Compatibility-point disposition

| ID and compatibility point | Classification | Source code/contract evidence and implemented disposition | Automation results | Real API results |
| --- | --- | --- | --- | --- |
| `CP-01 — Agent-facing API` | Changed | Audited alpha routes and kept Realtime explicitly outside Rosetta; refreshed endpoint/private-struct extraction. | Contract and full suite passed. | Direct and cross-format routes passed; Realtime remains unsupported. |
| `CP-02 — Responses transparent handling` | Changed | Audited include/session/reasoning and preserved new opaque fields on transparent paths. | Passthrough/contract tests passed. | Ordinary Responses passed; official compaction raw-wire cell failed, manual raw-wire cell passed. |
| `CP-03 — Codex Search and Images endpoints` | Changed | Added optional opaque `results`/`text_result` handling and reviewed image tool contract. | Search and image exposure tests passed. | Search runnable cells passed; Images endpoint rejected `gpt-image-2`; sidecar tasks unavailable. |
| `CP-04 — Request and window identity` | Changed | Extracted prompt-cache/session ownership and retained runtime identity headers. | Identity and full tests passed. | Isolated auth/session traces passed across runnable suites. |
| `CP-05 — Responses→Chat bridge` | Changed | Added valid stable message/function IDs and cache-write mapping. | Converter/stream tests passed. | DeepSeek bridge broadly passed; Flash task 03 failed in the original matrix and follow-up, and Pro failed the additional control retest. |
| `CP-06 — Responses Lite / additional_tools` | Changed | Extractor now understands renamed capability and typed item IDs; multi-round Lite bridge retained. | Source-contract and deferred tests passed. | Deferred discovery passed 14/14. |
| `CP-07 — Codex model catalog` | Changed | Official catalog is byte-identical to the target; presets use the alpha field and explicit safe differences. | Catalog/preset/local-mode tests passed. | All cells resolved the requested configured models in local mode. |
| `CP-08 — custom/freeform tool` | Changed | Rebased exec/apply-patch/freeform and image constraints on target source. | Tool projection and converter tests passed. | Command/builtin/deferred suites passed except DeepSeek stdin task. |
| `CP-09 — Code tool localization` | Changed | Corrected model-facing hyphenated names and native dotted names. | Catalog/namespace tests passed. | Namespace cells observed both forms and passed. |
| `CP-10 — Tool history consistency` | Changed | Valid/stable response IDs and replay paths tested. | ID/history/stream tests passed. | Multi-round ordinary history passed; DeepSeek compaction task repeated the one-shot scenario. |
| `CP-11 — Deferred tool discovery` | Changed | Shared MCP Types now include deferred-only MCP declarations with exact authorization. | Projection tests passed. | 14/14 deferred cells passed. |
| `CP-12 — Codex tool usage tips` | Changed | Refreshed reviewed static descriptions and target binding. | 53-item catalog tests passed. | Built-in tool suites passed. |
| `CP-13 — Skill delivery surfaces` | Changed | Local skill boundary retained; orchestrator remains provider-owned. | Fixture/full tests passed. | Local skill 2/2 passed; orchestrator runner unsupported. |
| `CP-14 — Live-agent runtime authentication` | Possibly unchanged | Kept OAuth and Gateway-key responsibilities separate. | Runtime-auth artifact validation passed for executed cells. | Runnable cells used both required auth sources; no auth mismatch found. |
| `CP-15 — Web search bridge` | Changed | Preserved opaque search results and reviewed native header forwarding. | Search tests passed. | Tasks 01/02/05 passed for both text models; tasks 03/04 lacked the configured sidecar. |
| `CP-16 — Self-hosted Bing search` | Possibly unchanged | Local executor remains separate, but its alpha result envelope is covered. | Bing/search unit coverage passed in full suite. | No sidecar/Bing live backend in supplied config; unresolved. |
| `CP-17 — Stream lifecycle` | Changed | Added cache-write usage in streaming and stable IDs across events. | Stream-focused rerun and full suite passed. | Runnable streaming cells terminated correctly; eight upstream events omitted usage as documented. |
| `CP-18 — Message phase` | Possibly unchanged | Phase ownership remains client-side; new protocol fields were inventoried. | Phase/tool tests passed. | Subagent and ordinary phase behavior passed; fresh GUI Browser phase was not validly runnable. |
| `CP-19 — Reasoning` | Changed | Adopted default-true summary parameter and preserved reasoning/include behavior. | Contract, preset, converter tests passed. | Reasoning-capable ordinary continuations passed; no separate formal C-matrix was run. |
| `CP-20 — Context compaction resilience` | Changed | Extracted retry/output-ID/cache-write changes and added stream/nonstream mappings. | Compaction contracts and full suite passed. | Three completed, two failed, two not scored; DeepSeek failure is model/session continuity, Terra raw-wire failure is CLI-attestation test design. |
| `CP-21 — GPT relay provider identity` | Changed | Audited provider/session/route identity and retained explicit profile selection. | Identity/profile tests passed. | Terra Pixel route was captured repeatedly; formal C0-C5 relay matrix was not run, so unresolved. |
| `CP-22 — Model-group tool profiles` | Changed | Added permissions/auto-review fields and reviewed third-party profile differences. | Preset/local-mode/tool tests passed. | Terra, DeepSeek, and MiMo selected expected routes/tools; image profile backend failed. |
| `CP-23 — Static tool catalog` | Changed | Refreshed 53 entries and metadata to exact alpha.23 tag/commit. | Catalog equality and projection tests passed. | Model-visible native/deferred names were exercised; Browser/orchestrator surfaces remain unverified. |

## Adoption blockers

1. DeepSeek-v4-flash and the first DeepSeek-v4-pro cells failed the stdin/session
   continuation contract; the latest DeepSeek-v4-pro cell passed after the
   Chat Default prompt/example revision. Keep the successful replay as the
   current compatibility evidence and continue monitoring model variance.
2. The Images endpoint must expose Codex alpha.23's `gpt-image-2`, or the
   deployment must provide an explicitly compatible image-model mapping.
3. DeepSeek's one-shot compaction replay is a model/session continuity failure;
   Terra's official raw-wire result is a CLI-runner/attestation gate mismatch.
   Split the once-only and protocol-only tests, and run the raw-wire gate via
   app-server or condition it on attestation before treating either as a
   Rosetta defect.
4. Summary-quality fixtures need a valid below-15k baseline before they can be
   scored.
5. Network sidecar/Bing, fresh-task Browser plus judge, orchestrator provider,
   formal agentabi, and the CP-21 C0-C5 relay matrix remain unavailable or
   unrunnable under the supplied environment.
6. After those gates pass, update the package to the approved alpha.23 `r0`
   version and rerun release validation. Until then, `0.144.0.r0` remains the
   only package claim.
