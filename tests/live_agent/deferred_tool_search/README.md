# Isolated Capability Exposure And Discovery Test

This suite verifies that Codex-Rosetta preserves the model-visible information
needed to use standalone skills, standalone MCP tools, plugin skills, and plugin
MCP tools. It pairs explicit controls with natural-language discovery tasks so
installation/exposure failures are not confused with active-discovery failures.

It intentionally uses only local deterministic fixtures. It does not copy real
user skills, install third-party plugins, use Browser/apps, or reuse the user's
normal `CODEX_HOME`.

## Candidate catalog

Every task receives exactly three clearly unrelated candidates in stable order:

1. archive record proof (the target);
2. integer addition;
3. color-label normalization.

Implicit prompts mention only the archive-record goal and record id. They do
not contain a skill/plugin/tool name, plugin URI, private body marker, or tool
result prefix. This is a narrow exposure regression, not a model-selection
benchmark.

## Task matrix

| Task | Mode | Surface | Required behavior |
|---|---|---|---|
| `01` | explicit | plugin MCP | structured plugin mention, plugin guidance, `ALL_TOOLS`, call |
| `02` | explicit | standalone MCP | named MCP class, `ALL_TOOLS`, call |
| `03` | explicit | standalone skill | `$skill` mention and host-injected `<skill>` body |
| `04` | implicit | standalone skill | catalog match, selected `SKILL.md` read, body marker |
| `05` | implicit | standalone MCP | semantic `ALL_TOOLS` match and call |
| `06` | implicit | plugin skill | prefixed skill metadata, selected body read, no explicit guidance |
| `07` | implicit | plugin MCP | plugin provenance in deferred tool metadata and call, no mention |

The explicit controls are paired with implicit tasks as `03/04`, `02/05`, and
`01/06/07`. If a control passes but its implicit counterpart fails, classify
the matrix result as `active_discovery_failed`; do not report an installation or
Rosetta conversion failure without evidence for that earlier stage.

## Isolated provisioning

Copy `common/` and exactly one selected task into a fresh timestamp-only run
root. After copying the user's gateway configuration, prepare only that isolated
copy and Codex home:

```bash
conda run -n llm-rosetta python "$SUITE/prepare_run.py" \
  --run-root "$RUN_ROOT" \
  --gateway-log-root "$GATEWAY_LOG_ROOT" \
  --port 18765 \
  --model gpt-5.6-terra \
  --task-id 01
```

`prepare_run.py` installs only the surfaces required by the selected task:

- plugin MCP tasks remove plugin skills from the copied worktree;
- the plugin skill task removes plugin MCP declarations;
- standalone skill tasks copy all three simple skills;
- standalone MCP tasks register one server exposing all three tools.

The three plugins share `fixtures/deterministic_mcp_server.py`. Placeholder
paths in copied plugin MCP manifests are resolved only inside the disposable
worktree before installation. Installation/list outputs stay under `artifacts/`
as evidence, not as proof that a model saw or used a capability.

For third-party aliases the script writes a gateway-derived
`model_catalog.json`; an unknown-model fallback is a setup failure.

## Model order and stop gate

Run tasks `01` through `07` with `gpt-5.6-terra` first, using a separate run
root, gateway, Codex home, and trace for every cell. Stop and repair the first
failing Terra task. Do not start `deepseek-v4-flash` until every Terra task
passes.

| Model | Expected route |
|---|---|
| `gpt-5.6-terra` | direct OpenAI Responses Lite/code-mode baseline |
| `deepseek-v4-flash` | Responses-to-Chat with generated 0.144.4 model catalog |

For the direct Responses baseline, deferred MCP discovery remains `exec ->
runtime ALL_TOOLS -> nested tool`. On the Responses-to-Chat route, Rosetta must
add an ordinary `tool_search` Function from the live deferred guidance; its call
must return to Codex as custom `exec` JavaScript, after which the selected
runtime tool is invoked through raw `exec`. Codex places candidate metadata only
in the V8 runtime, and the fixture's compact catalog output captures it without
Gateway discovery state. Follow
[`EVALUATION.md`](EVALUATION.md) for structural evidence and result fields.
