# Codex Upgrade Reports

Each Codex source inspection or upgrade must save an independent report in this
directory. The report must state whether it is a routine release review, a full
inventory review, or a documentation-coverage-only review. Use a concise,
descriptive file name without a date or Codex version, for example:

```text
range-coverage-review.md
```

Immediately after the title, record the date and the current report-label
version on separate lines:

```text
Date: YYYY-MM-DD
Codex version: 0.144.0
```

The report body must still record the exact CLI/source identities actually
inspected. The label version is intentionally temporary until a later re-test
standardizes the current version.

Before pulling, create a report containing the previous Codex CLI version, source commit, and Codex-Rosetta version and commit. After pulling, add the target Codex release and new source commit.

Each report contains at least:

1. Old/new Codex CLI version, source code commit, date and Codex-Rosetta version/commit;
2. Three types of contract-group output of `make check-codex-compat`;
3. An itemized classification of every stable `CP-*` point in `../compatibility-points.md`, with the same number of entries as the canonical registry;
4. The repair plan and results of each "changed" item;
5. All automated test commands and results;
6. Real API test models, routes, scenarios and results for each "may not change" or "change" item;
7. Unresolved limitations, whether upgrades are allowed, and final Codex-Rosetta package version.

A documentation-coverage-only report may record runtime automation and live
tests as not run, but it must say so explicitly and cannot approve a Codex
version, refresh the source-contract baseline, or advance the Rosetta package
version. A full inventory report must also record the reverse-map and scattered
documentation scan from `../rosetta-source-map.md`.

Use the itemized classification table:

| ID and compatibility point | Classification | Source code/contract evidence | Fix or review plan | Automation results | Real API results |
| --- | --- | --- | --- | --- | --- |
| `CP-XX — <copied canonical name>` | High-confidence unchanged / possibly unchanged / changed | `<evidence>` | `<plan>` | `<command and results>` | `<model, route and results>` |

High-confidence unchanged rows may record the live API result as "not triggered this time", but their live scenarios must remain defined in `../compatibility-points.md`. Possibly unchanged and changed rows require live API results; mocks or fixtures cannot replace them.

## Current documentation baseline

- [`upgrade-review.md`](upgrade-review.md): full
  source-first inventory and implementation review for `0.145.0-alpha.23`,
  including direct comparison with `0.142.0` and `0.144.6`, deterministic
  checks, and the complete available live-agent matrix. The adaptation is
  recorded, but failed/unavailable live gates keep adoption pending.
- [`live-evidence.md`](live-evidence.md): per-attempt route,
  thread, evaluation, and cache-continuation evidence for that review.
- [`range-coverage-review.md`](range-coverage-review.md): historical
  full code-to-document inventory and `0.142.0`–`0.144.6` coverage
  validation. It is documentation-only and does not approve `0.144.6`.
