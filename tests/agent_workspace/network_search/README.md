# Network Search Tool Test

This suite verifies that an agent selects the available network-search tool and
receives a usable result through Codex-Rosetta. It does not measure research
quality, citation style, or the quality of the final prose.

## Scenarios

- `01`: search for the official Python documentation and report a fixed marker
  only when a `docs.python.org` URL is present in the tool result.

The task forbids shell commands and browser automation so the marker cannot be
obtained by bypassing the model-facing network-search surface. Evaluate both
the Codex rollout and Rosetta trace to identify whether the request used a
namespace function such as `web.run`, a hosted `web_search`, or a translated
bridge.
