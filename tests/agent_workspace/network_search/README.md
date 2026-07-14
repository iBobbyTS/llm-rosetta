# Network Search Tool Test

This suite verifies that an agent selects the available network-search tool and
receives a usable result through Codex-Rosetta. It does not measure research
quality, citation style, or the quality of the final prose.

## Scenarios

- `01`: search for the official Python documentation and report a fixed marker
  only when a `docs.python.org` URL is present in the tool result.
- `02`: under the fixed OpenAI-identified, `gpt-5.6-sol`-equivalent scope,
  search for the Python documentation, open its stored search reference, and
  then call `find` and `click`. Local Rosetta currently supports `open` but
  deliberately returns Not Implemented for `find` and `click`; the scenario
  verifies both the successful page retrieval and those bounded errors.

## Required Codex configuration

Set `web_search = "live"` at the top level of the isolated Codex configuration
so the model-facing search surface is available. Do not add this flag to
unrelated suites.

For a normal custom-provider run, retain the Rosetta localhost `base_url` and
the provider identity requested by the matrix. Responses Lite models expose
the standalone `web.run` extension only when the provider display name is
`OpenAI`; use that test-only display-name override when the matrix is intended
to exercise `web.run`, and record the override in the result. Use a separate
timestamp run for every provider identity.

The task forbids shell commands and browser automation so the marker cannot be
obtained by bypassing the model-facing network-search surface. Follow
[`EVALUATION.md`](EVALUATION.md) and write `artifacts/evaluation.json` for every
run.

Task `02` must use a provider display name exactly equal to `OpenAI`, a model
catalog entry exactly equivalent to `gpt-5.6-sol`, and the bundled `Chat
Default` Profile. It is not valid evidence when Codex selects hosted
`web_search` instead of standalone `web.run`.

The final tool classification must come from Rosetta **Gateway Logs**, not from
the Codex CLI item label. Codex renders both standalone `web.run` and hosted
`web_search` activity as a `web_search` item, so that label cannot distinguish
the two paths. Report exactly one `search_surface` value:

- `web.run` when the Gateway Logs show the standalone namespace/nested call,
  normally followed by the Codex Search API at `POST /v1/alpha/search`;
- `web_search` when the Gateway Logs show a structured hosted `web_search`
  definition/call, including a Responses-to-Chat localization that Rosetta
  executes through Tavily;
- `none` when no model-facing search call occurred;
- `ambiguous` when the logs are missing or do not prove either path.

A successful Tavily bridge remains `search_surface: "web_search"`: Tavily is
the executor selected by Rosetta, while `web_search` is the surface selected by
the model. Text that merely mentions `web.run` or `web_search` is not tool-call
evidence.
