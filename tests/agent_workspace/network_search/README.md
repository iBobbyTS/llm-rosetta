# Network Search and `web.run` Tool Tests

This suite verifies that an agent selects the available network-search surface
and correctly calls the subset of `web.run` implemented by Codex-Rosetta. It
does not measure research quality, citation style, browser comprehension, or
the quality of the final prose.

## Scenarios

- `01`: perform one basic search for the official Python documentation. This
  baseline permits either standalone `web.run` or hosted `web_search`.
- `02`: use local `web.run` with a domain-filtered Tavily query and short
  response length, then open the returned `turnXsearchY` reference through the
  bounded static Python page reader with a non-zero `lineno`.
- `03`: use the optional sidecar to open `https://example.com/`, retain its
  `turnXfetchY` page reference, find text on that page, and click its sole
  numbered link.
- `04`: use the optional sidecar to open a stable public PDF, retain its
  `turnXviewY` reference, find embedded text, and render page zero through
  `screenshot`.
- `05`: request two fixed-offset times in one `web.run` call. This is a local
  Python operation and must not contact Tavily or the sidecar.

Every numbered task is independent. Run one task per isolated timestamp root;
references from one task must never be reused by another.

## Fixed Codex scope

Set `web_search = "live"` at the top level of the isolated Codex configuration
so the model-facing search surface is available. Do not add this flag to
unrelated suites.

Tasks `02` through `05` must use all of the following:

- a provider display name exactly equal to `OpenAI`;
- a model catalog entry exactly equivalent to `gpt-5.6-sol`;
- the bundled `Chat Default` Tool Profile with `web__run` set to Modified;
- the Rosetta localhost `base_url` generated for the isolated run.

These tasks are not valid evidence when Codex selects hosted `web_search`
instead of standalone `web.run`. Task `01` deliberately remains the
surface-comparison baseline and permits either surface.

## Executor matrix

| Task | Tavily token | `web-run` sidecar | Expected local path |
|---|---|---|---|
| `01` | required for a Rosetta-local search | disabled | Tavily or upstream hosted search |
| `02` | required | disabled | Tavily plus bounded static Python open |
| `03` | not required | required | Playwright page open/find/click |
| `04` | not required | required | Playwright/PyMuPDF PDF handling |
| `05` | not required | disabled | Python fixed-offset time |

For a sidecar-required task, configure matching authenticated
`server.web_run.base_url` and `server.web_run.token` values in the copied
Gateway configuration before starting the isolated Gateway. The endpoint must
refer to the separately built `web-run` container and must be reachable from
that Gateway. For a sidecar-disabled task, remove both values instead of
leaving a stale or unreachable endpoint. Gateway startup capability detection
must make `click`, `find`, and `screenshot` model-visible only in the
sidecar-required runs.

The task prompts prohibit shell commands, direct HTTP clients, and external
browser tools so the result cannot be obtained by bypassing the target search
surface. The browser implementation behind a valid `web.run` call is the
subject under test and is not a prohibited fallback.

## Evidence and classification

Follow [`EVALUATION.md`](EVALUATION.md) and write
`artifacts/evaluation.json` for every run. The final tool classification must
come from Rosetta **Gateway Logs**, not from the Codex CLI item label. Codex
renders both standalone `web.run` and hosted `web_search` activity as a
`web_search` item, so that label cannot distinguish the two paths. Report
exactly one `search_surface` value:

- `web.run` when Gateway Logs show the standalone namespace/nested call,
  normally followed by `POST /v1/alpha/search`;
- `web_search` when Gateway Logs show a structured hosted `web_search`
  definition/call, including Responses-to-Chat localization executed through
  Tavily;
- `none` when the logs prove no model-facing search call occurred;
- `ambiguous` when the logs are missing or inconclusive.

A successful Tavily bridge remains `search_surface: "web_search"` when the
model selected hosted search: Tavily is the executor, while `web_search` is the
surface. Text that merely mentions `web.run` or `web_search` is not tool-call
evidence.

The PDF screenshot scenario verifies the model-facing operation and Rosetta's
text result, including rendered dimensions and the embedded-text/OCR source.
It does not claim that pixels were injected into the model. The deterministic
public fixture contains embedded text; Tesseract fallback remains covered by
container tests rather than by this network-dependent agent scenario.
