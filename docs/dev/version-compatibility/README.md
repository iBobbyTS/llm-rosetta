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

## Current alpha.23 adaptation baseline

Inspection date: 2026-07-18

| Project | Current Value | Description |
| --- | --- | --- |
| Local Codex CLI | `codex-cli 0.144.6` | From `codex --version`; this inspection does not adopt it as the Rosetta package target |
| Codex source branch | detached `rust-v0.145.0-alpha.23` | Exact release reference in `../openai-codex-src` |
| Codex source commit | `655224ffae098a85efeddf8289171ff3bd2624d1` | Exact peeled commit for `rust-v0.145.0-alpha.23` |
| Codex source timestamp | `2026-07-17T14:53:36-07:00` | Release commit timestamp |
| Codex-Rosetta package version | `0.144.0.r0` | First Rosetta patch for Codex `0.144.0` |
| Codex-Rosetta review snapshot | HEAD `5dd45e7e60f8b5dacea321002b0a55a85b01bf17` plus the uncommitted adaptation worktree | The implementation and evidence are not a clean release revision; no commit was requested |

This is a full-inventory, source-first adaptation, not a clean reproducible
release revision or a compatibility approval. It validates the code-derived
ledger against Codex 0.142.0, 0.144.6, and 0.145.0-alpha.23, updates the
Rosetta runtime owners and packaged assets, and runs the available automated and
live gates. The package version remains unchanged because required live cells
still fail or lack their required external runner/backend. The Codex CLI release version, Codex source
commit, Codex-Rosetta source version, and Codex-Rosetta commit remain
independent identifiers. See the [alpha.23 upgrade report](reports/upgrade-review.md)
for the source findings and the [range-coverage-review report](reports/range-coverage-review.md)
for the historical 0.142.0–0.144.6 documentation review.

## Recorded documentation verification

| Check | Results |
| --- | --- |
| Codex source contract check | Extractor updated for alpha.23 and reviewed against exact commit `655224ff…`: 22 high-confidence unchanged groups, 11 possibly unchanged groups, and no changed group against the reviewed target snapshot |
| Code-to-document reverse map | Rebuilt from current Rosetta code and deterministic tests in [`rosetta-source-map.md`](rosetta-source-map.md); the stable ledger now contains 23 points |
| Ledger integrity | Compatibility overview and test matrix contain the same 23 names exactly once; the previously unmatched Skill and Bing rows and deferred-tool naming were reconciled |
| `0.142.0` … `0.145.0-alpha.23` source coverage | The prior 0.142.0–0.144.6 map was rechecked directly against the alpha.23 source and every CP-01…CP-23 point is classified in the alpha.23 report |
| Runtime adaptation and real Codex/API | Implemented and exercised with the locally built `0.145.0-alpha.23` client. Most CLI-compatible cells pass; image generation, one DeepSeek process-continuation cell, exact Terra context-limit passthrough, GUI Browser, orchestrator Skills, sidecar search, and agentabi remain failed or unavailable. Alpha.23 is therefore not approved |

## Files

- [`../../en/codex-model-catalog.md`](../../en/codex-model-catalog.md): Complete Codex model catalog field reference and Rosetta third-party adaptation guidance, mirrored under `docs/zh-cn`.
- [`compatibility-points.md`](compatibility-points.md): Codex-specific compatibility points, ownership boundaries, evidence paths, and known limitations.
- [`rosetta-source-map.md`](rosetta-source-map.md): Code-derived Rosetta owner and deterministic-test map used by routine release reviews.
- [`upgrade-checklist.md`](upgrade-checklist.md): Source-review and test checklist for Codex upgrades, separating the automation backlog from live Codex/model gates.
- [`codex-source-contract.json`](codex-source-contract.json): A machine-comparable contract baseline extracted from the current Codex source code and saved after human review.
- [`evidence/`](evidence/README.md): Historical source research, runtime observations, and supporting protocol notes. These are evidence, not current compatibility claims.
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

- Current Rosetta code and deterministic tests are implementation facts. The
  compatibility ledger is the intended contract, and
  [`rosetta-source-map.md`](rosetta-source-map.md) is the maintained index
  between them. Correct the documentation whenever the three disagree.
- The developer selects either a routine release review or a full inventory
  review; semantic version numbers do not select the mode automatically. A
  routine review may use the documented source map, but an unmapped Codex diff,
  an unmapped Rosetta owner, or conflicting extractor/source evidence must be
  escalated for an explicit review-mode decision.
- Stable `CP-*` IDs, rather than free-form row names, identify compatibility
  points in reports. The overview and test matrix must each contain every
  registered point exactly once.
- Store version-specific source research, runtime observations, and upgrade
  decisions only under this directory's `evidence/` and `reports/`. User-facing
  references and general test guides may link here but must not maintain a
  second compatibility baseline or upgrade checklist.
- When any Codex-specific adaptation is discovered or added during daily development, the corresponding compatibility points must be added or updated in `compatibility-points.md`, and the checks that can be automatically completed, when the real Codex/API test must be connected, and the recommended actual test scenarios must be written clearly.
- When updating the Codex CLI or `../openai-codex-src`, an upgrade checklist must be performed.
- The upgrade must first record the old commit, fetch tags and remote references,
  and resolve the peeled target release commit. Prefer fast-forward-only branch
  updates; when a requested release tag is a divergent backport snapshot, a
  clean exact-tag detach is allowed and must be recorded. Never compare only an
  unfetched local `origin/main`.
- When `make check-codex-compat` reports a commit or contract drift, the semantic review must be completed first; the baseline must not be refreshed directly to make the check green.
- The three-category contract-group output of `make check-codex-compat` is evidence input only. The final report must provide "high confidence no change", "possibly no change" or "change" for each compatibility point in `compatibility-points.md` item by item, and compatibility points not covered by the extractor must not be missed.
- Run all automated tests. Every compatibility point classified as "possibly unchanged" or "changed" requires a real Codex/API test. Only after all repairs and gates pass may the Codex-Rosetta package version advance to `{codex_version}.r{patch_number}`; record the exact source commit at the same time.
- When adding, modifying or deleting Codex-specific adaptations, the compatibility point list must be updated in the same task.
- Review the single Responses wire protocol's direct Tool Mapping and Responses→Chat paths separately. Direct transport applies only the selected Tool Profile before forwarding and naturally retains unknown non-tool fields; the IR path requires explicit review for every new wire shape. Removed split protocol values are rejected during configuration loading and are not separate Admin protocol choices or wire protocols.
- "Unused" does not equal "compatible". Capabilities such as WebSocket Responses, Responses Lite, remote compact or dynamic Codex model catalog can only be marked as supported after actual testing.
- Compatibility claims must retain test evidence; when credentials or real upstreams are missing, they are clearly marked as "unverified" and unit tests cannot be used to replace end-to-end conclusions.
