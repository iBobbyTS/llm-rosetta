# Codex Version Compatibility

This directory is the source of truth for compatibility between Codex-Rosetta and the Codex CLI/source checkout. It records the contracts, current limitations, and boundaries that this project actively maintains for Codex client behavior—not generalized OpenAI Responses API support.

## Codex source location

The local Codex source checkout used by this project is located at:

```text
../openai-codex-src
```

Paths are relative to the Codex-Rosetta repository root. The source repository's own version field may be `0.0.0`/`0.0.0-dev`, so every compatibility baseline must record all of the following:

1. The installed Codex CLI version;
2. The full `../openai-codex-src` commit;
3. The Codex-Rosetta version and commit;
4. Tests that passed and capabilities that remain unverified.

Compatibility cannot be declared just because the version numbers are the same.

## Package version naming

Codex-Rosetta source versions use `{codex_version}.r{patch_number}`. The first three segments match the target Codex CLI release, while `rN` is the Rosetta patch number for that Codex release. Each newly adopted Codex release starts at `r0`; only subsequent Rosetta fixes increment `rN`. Source versions retain the literal `rN`, while Python package metadata normalizes it to the equivalent PEP 440 `.postN` form. Manual GitHub Release tags retain the repository's historical `v` prefix, so source `0.144.0.r0` maps to tag `v0.144.0.r0`.

## Current pending inspection baseline

Inspection date: 2026-07-14

| Project | Current Value | Description |
| --- | --- | --- |
| Local Codex CLI | `codex-cli 0.144.4` | From `codex --version`; newer than the `0.144.0.r0` compatibility target |
| Codex source branch | detached `rust-v0.144.4` | Exact release reference in `../openai-codex-src` |
| Codex source commit | `8c68d4c87dc54d38861f5114e920c3de2efa5876` | Exact peeled commit for `rust-v0.144.4` |
| Codex source timestamp | `2026-07-13T21:20:37-07:00` | Release commit timestamp |
| Codex-Rosetta package version | `0.144.0.r0` | First Rosetta patch for Codex `0.144.0` |
| Codex-Rosetta review snapshot | HEAD `47157ee` plus the 0.144.4 compatibility worktree | Exact release compatibility remains pending until the triggered live gates pass |

This is a dirty inspection snapshot, not a clean reproducible release revision. The `0.144.4` reference review and the earlier `0.144.0` compatibility decision are **pending / not approved** until every triggered live gate passes against an exact clean Codex-Rosetta commit. The Codex CLI release version, Codex source commit, Codex-Rosetta source version, and Codex-Rosetta commit remain independent compatibility identifiers. See [`reports/20260714-codex-v0.144.4.md`](reports/20260714-codex-v0.144.4.md) for the scoped source review and remaining gates; the 0.144.1 report remains historical evidence.

## Recorded verification results (partial)

| Check | Results |
| --- | --- |
| Codex source contract check | Baseline reviewed and updated for `8c68d4c87dc…`; final worktree verification is recorded in the 0.144.4 report |
| Codex-specific targeted regression | `404 passed`; extended regression `425 passed, 6 warnings`; Responses converter `356 passed` |
| `make lint` | Passed; both Ruff check and format check passed |
| `make test` | `2533 passed, 4 skipped, 9 warnings` |
| Real Codex/API | **Partial:** `deepseek-v4-flash` completed controlled Lite/code-mode, file reading, multi-turn tools, `ultra`, and `exec` through an isolated gateway. Native GPT, compact/resume/fork, plugin/MCP/deferred tools, web search, UI phase, Desktop tools, changed error paths, and multi-agent remain unverified/not triggered; WebSocket Responses, incremental history, and remote compact remain unsupported |

The controlled alias is evidence only for the third-party route it actually exercised. It must not be expanded into native GPT evidence or treated as satisfying the complete live matrix. See [`reports/20260709-codex-v0.144.0.md`](reports/20260709-codex-v0.144.0.md) for the itemized `tested`, `unverified / not triggered`, and `unsupported` status.

The earlier intermittent `TestPipelineProfile::test_profile_populated_after_convert_request` failure was traced to test arithmetic, not conversion state leakage: each phase and the total are independently rounded to a 0.01 ms reporting quantum, so adding rounded sub-millisecond phases can exceed the separately rounded total. The request/response assertions now use tight absolute tolerances derived from that quantization; focused tests, a 50,000-iteration stress run, and the full suite pass. See `.agent-work/debug/resolved/20260709-pipeline-profile-rounding-flake.md` for the evidence chain.

## Files

- [`../../en/codex-model-catalog.md`](../../en/codex-model-catalog.md): Complete Codex model catalog field reference and Rosetta third-party adaptation guidance, mirrored under `docs/zh-cn`.
- [`compatibility-points.md`](compatibility-points.md): Codex-specific compatibility points, ownership boundaries, evidence paths, and known limitations.
- [`upgrade-checklist.md`](upgrade-checklist.md): Source-review and test checklist for Codex upgrades, separating the automation backlog from live Codex/model gates.
- [`codex-source-contract.json`](codex-source-contract.json): A machine-comparable contract baseline extracted from the current Codex source code and saved after human review.
- [`reports/`](reports/README.md): Old/new versions, itemized classifications, fixes, automation results, real API results and final version decisions for each Codex upgrade.

Primary automation entry point:

```bash
make check-codex-compat
```

This command strictly compares the source commit and extracted contract. A missing `../openai-codex-src`, a missing extraction anchor, or contract drift causes a failure. Run `make update-codex-compat-baseline` only after reviewing the source changes. Ordinary `make test` tests the extractor itself and does not fail merely because CI lacks the sibling Codex checkout.

Each check outputs three fixed parts:

1. **High confidence that there is no change**: the source code commit is the same, or the constants, wire mapping, event name, endpoint, metadata key, `apply_patch` grammar hash, etc. of the complete comparison are completely consistent;
2. **Possibly unchanged**: The current extractor can only prove that the field name or enum member set is consistent, and has not yet fully compared the field type, serde strategy, default value or runtime semantics, and must continue manual review;
3. **Changes**: Source code commit, any extracted contract group or extracted structure changes, with detailed unified diff attached.

Category three will be displayed even if it is empty. By default "changed" causes the check to return exit code 1; failed extraction or missing path returns exit code 2. `--ignore-source-commit` only allows commit changes that do not affect the exit code. The report will still list this fact in "changed" and indicate that it has been ignored.

## Maintenance rules

- When any Codex-specific adaptation is discovered or added during daily development, the corresponding compatibility points must be added or updated in `compatibility-points.md`, and the checks that can be automatically completed, when the real Codex/API test must be connected, and the recommended actual test scenarios must be written clearly.
- When updating the Codex CLI or `../openai-codex-src`, an upgrade checklist must be performed.
- The upgrade must first record the old commit, and then fast-forward `../openai-codex-src` to the latest remote version; you cannot just compare the local `origin/main` that has not been fetched.
- When `make check-codex-compat` reports a commit or contract drift, the semantic review must be completed first; the baseline must not be refreshed directly to make the check green.
- The three-category contract-group output of `make check-codex-compat` is evidence input only. The final report must provide "high confidence no change", "possibly no change" or "change" for each compatibility point in `compatibility-points.md` item by item, and compatibility points not covered by the extractor must not be missed.
- Run all automated tests. Every compatibility point classified as "possibly unchanged" or "changed" requires a real Codex/API test. Only after all repairs and gates pass may the Codex-Rosetta package version advance to `{codex_version}.r{patch_number}`; record the exact source commit at the same time.
- When adding, modifying or deleting Codex-specific adaptations, the compatibility point list must be updated in the same task.
- Review Responses Tool Mapping only, Responses Rosetta, and Responses→Chat separately. The first applies only the selected Tool Profile before its direct transport path and naturally retains unknown non-tool fields; the latter two use IR and require explicit review for every new wire shape. Tool Mapping only and Rosetta are internal handling modes, not distinct wire protocols.
- "Unused" does not equal "compatible". Capabilities such as WebSocket Responses, Responses Lite, remote compact or dynamic Codex model catalog can only be marked as supported after actual testing.
- Compatibility claims must retain test evidence; when credentials or real upstreams are missing, they are clearly marked as "unverified" and unit tests cannot be used to replace end-to-end conclusions.
