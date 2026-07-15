# Network Search and `web.run` Evaluation

This file guides the outer evaluating agent. Do not include it in the tested
model's prompt.

## Required evidence

Use all three bounded sources, but assign them different roles:

1. `artifacts/codex.jsonl` proves the process result, thread id, visible search
   activity, and final marker.
2. The matching rollout under `codex_home/sessions` proves the native Codex
   call/result sequence and absence of prohibited shell, direct-HTTP, or
   external-browser fallbacks.
3. Rosetta **Gateway Logs** at the path recorded in
   `artifacts/gateway-log-root.txt` are authoritative for `search_surface`, the
   actual upstream model, model-facing arguments, local executor, operation
   counts, references, and terminal stream state.

Do not classify the surface from a Codex item whose type is `web_search`.
Codex uses that presentation type for both standalone `web.run` and hosted
`web_search`. Text in prompts, tool descriptions, responses, or errors is not
proof of a tool call.

## Search-surface classification

Set `search_surface` using Gateway Logs:

### `web.run`

Require an executed namespace/nested call, not instructional prose. Accepted
evidence includes a model output call to `web.run`, or a projected Function
call reconstructed into an `exec` custom-tool invocation of `tools.web__run`.
When route telemetry is available, also record the subsequent
`POST /v1/alpha/search` request.

A Rosetta-local execution is proven by `codex_search_request` followed by
`codex_search_response`. Record the bounded executor and count fields from the
response summary. Expected executor summaries are `tavily_python` for search,
static open, or time, and `tavily_python_web_run_sidecar` when a browser/PDF
operation ran. The first name does not imply that Tavily was contacted: use
`search_count` and `tavily_result_count` to distinguish pure Python operations.

### `web_search`

Require a structured hosted tool definition or call. Accepted evidence
includes:

- a Responses request tool with `type: "web_search"` or
  `type: "web_search_preview"`, followed by a `web_search_call`; or
- a Responses-to-Chat trace where `source_request` contains the hosted search
  tool, `target_request` contains Rosetta's localized `web_search` Function,
  and the trace contains `web_search_request`/`web_search_response` stages.

If Rosetta executes that localized call through Tavily, keep
`search_surface: "web_search"` and record Tavily separately as the executor.

### `none` and `ambiguous`

Use `none` when Gateway Logs are present and prove that no model-facing search
call occurred. Use `ambiguous` when the trace is absent, truncated, filtered to
the wrong request/model, or contains only textual mentions. An `ambiguous` run
cannot pass this suite.

## Common success rules

Every run passes only when all of the following are true:

- the exact success marker is present and the failure marker is absent;
- the required `search_surface` is proven by Gateway Logs;
- every required operation was a separate model-facing call unless the task
  explicitly requires multiple array entries in one call;
- every target result is non-error and satisfies its bounded content check;
- the actual upstream model and expected model-facing alias are proven;
- no shell, direct HTTP, external-browser, or local-tool substitute was used;
- the Rosetta stream completed successfully.

The optional `web-run` sidecar is the implementation behind valid `web.run`
calls in tasks `03` and `04`; it is not an external-browser fallback. A correct
final marker without the required call sequence is a failure. A correctly
selected operation whose backend cannot reach the public fixture is an
end-to-end failure, but record it as a backend/network failure rather than a
model argument error when the call was valid.

Small unrelated model deviations do not fail the run when the core sequence
and results hold. Never repair a target operation through another tool.

## Per-scenario decisions

### `01` / basic search surface

Require at least one proven `web.run` or hosted `web_search` call and a
non-error result containing a `docs.python.org` URL. Tavily may be the local
executor, but the surface remains whichever tool the model selected.

### `02` / Tavily search reference and static open

Require exactly these ordered, separate `web.run` calls:

1. `search_query` with `domains` exactly `["docs.python.org"]` and
   `response_length: "short"`; its result must contain a `docs.python.org` URL
   and a `turnXsearchY` reference;
2. `open` with that exact reference and `lineno: 1`; its result must contain
   readable Python documentation, a line window starting at 1, and no `L0:`
   line.

The isolated Gateway must have no sidecar configured. Gateway Logs must report
one Tavily search, one stored-reference open, zero browser opens, and a
`tavily_python` executor summary. Opening the resolved URL directly does not
satisfy the reference requirement.

### `03` / sidecar HTML navigation

Require exactly these ordered, separate `web.run` calls:

1. direct `open` of `https://example.com/`, returning `Example Domain`, link
   id `1`, and a `turnXfetchY` reference;
2. `find` for `Example Domain` on that exact page reference, returning at least
   one match;
3. `click` with id `1` on that same reference, returning a different
   `turnXfetchY` reference without error.

Gateway Logs must report one browser open, one find, one click, zero Tavily
results, and executor `tavily_python_web_run_sidecar`. A fresh direct open in
place of either reference-based operation is a failure.

### `04` / sidecar PDF

Require exactly these ordered, separate `web.run` calls:

1. direct `open` of the exact W3C PDF URL, returning `Pages: 1`,
   `Dummy PDF file`, and a `turnXviewY` reference;
2. `find` for `Dummy PDF file` on that exact PDF reference, returning at least
   one match;
3. `screenshot` for page `0` on that same reference, returning `Rendered size`,
   `Text source`, and `Dummy PDF file`.

Gateway Logs must report one browser open, one find, one screenshot, zero
Tavily results, and executor `tavily_python_web_run_sidecar`. The result is
text and render metadata; do not require an image content item. Record the
observed text source as `embedded text`, `Tesseract OCR`, or `unknown`.

### `05` / Python fixed-offset time

Require exactly one `web.run` call whose `time` array contains exactly
`+03:00` and `-05:30` in that order. Require two non-error ISO 8601 timestamps
with the matching labels. Gateway Logs must report `time_count: 2`, zero
search/open/browser/PDF operation counts, zero Tavily results, and executor
`tavily_python`. Any `clock`, shell, local-language runtime, or prose substitute
is a failure.

## Required result file

Write `artifacts/evaluation.json` with this shape:

```json
{
  "classification": "success | success with deviations | failure",
  "failure_owner": "none | codex | rosetta | upstream_model | external_backend | ambiguous",
  "task_id": "01 through 05",
  "target_scope": "basic_search | reference_open | browser_navigation | pdf | fixed_offset_time",
  "model": "Codex-facing model alias",
  "model_catalog_equivalent_to": "gpt-5.6-sol or null for an explicit task 01 comparison",
  "provider_identity": "isolated provider id",
  "provider_display_name": "provider display name",
  "upstream_model": "model proven by Gateway Logs",
  "thread_id": "Codex thread id",
  "rollout_path": "isolated rollout path",
  "process_exit_code": 0,
  "success_marker_observed": true,
  "search_surface": "web.run | web_search | none | ambiguous",
  "local_executor": "tavily_python | tavily_python_web_run_sidecar | upstream_responses | none | unknown",
  "model_facing_operations": ["observed operation names in order"],
  "operation_results": {
    "operation name": "success | failed | not_called"
  },
  "references": {
    "search": "bounded turnXsearchY value or null",
    "page": ["bounded turnXfetchY values"],
    "pdf": "bounded turnXviewY value or null"
  },
  "successful_target_results": true,
  "text_source": "embedded text | Tesseract OCR | not_applicable | unknown",
  "prohibited_fallback_calls": 0,
  "gateway_log_evidence": [
    {
      "stage": "bounded Gateway Logs stage",
      "request_id": "request id when available",
      "observation": "short credential-free structural observation"
    }
  ],
  "stream_completed": true,
  "warning": null
}
```

Use `failure_owner` only to classify an observed failed or deviating run; it is
not a substitute for structural evidence. Keep evidence bounded and
credential-free. Never copy full prompts, response bodies, API keys,
authorization headers, page contents, PDF bytes, or entire trace records into
the result.
