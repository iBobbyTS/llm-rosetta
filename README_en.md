# Codex-Rosetta

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

[English Version](README_en.md) | [中文版](README_zh.md)

**Codex-Rosetta** is an LLM gateway based on LLM-Rosetta. It focuses on connecting third-party LLM APIs to Codex while improving tool calling and plugin behavior.

## Fork Focus

This project is forked from [Oaklight/llm-rosetta](https://github.com/Oaklight/llm-rosetta). This fork focuses on converting Chat Completions-compatible APIs to the Responses API, adapting tool-call semantics so open models work better in Codex, and aggregating multiple providers behind one gateway. The agent-facing generation API only exposes OpenAI Responses; Chat Completions, Anthropic Messages, and Google GenAI formats are retained as upstream target formats, not downstream client surfaces.

## Installation

Clone the repository, enter it, and install the package:

```bash
git clone https://github.com/iBobbyTS/codex-rosetta.git
cd codex-rosetta
python -m pip install -U '.[gateway]'
```

The `gateway` extra installs the audited AEAD dependency used for exact,
restart-safe tool-history persistence. Core conversion-only consumers may
still install `.` without gateway dependencies.

## Usage

Initialize the gateway configuration once:

```bash
codex-rosetta-gateway init
```

Initialization generates a mandatory Admin password and gateway access key in
the owner-only config file. Store both securely; protected `/v1` requests must
send the generated access key as a Bearer token. See
[Gateway security and authentication](docs/en/gateway-security.md).

Start the local gateway each time you use it:

```bash
codex-rosetta-gateway --host 127.0.0.1 --log-level warning
```

The default `warning` level shows only warnings and errors. Use
`--log-level stats` to maintain request counts by original upstream model name
on one terminal line, `info` to print request summaries, or `error` to show only
errors. In stats mode, warnings and errors start on a new line; the counters
resume on the next request. Use the WebUI **Request Log** for complete request
history and **Gateway Logs** for streaming trace diagnostics.

### Local Mode

Local mode is enabled by default. It uses the model presets bundled with this
project, automatically matches models configured in the gateway, and injects
them into Codex by maintaining `<codex-home>/model_catalog.json` and the
`model_catalog_json` setting in `<codex-home>/config.toml`. Model changes made
in the WebUI are synchronized to both the gateway configuration and this Codex
model catalog. When no models are configured, the catalog contains the eight
bundled Codex models; once any models are configured, it contains only the
configured models.
Restart Codex after changing models so it reloads the catalog.

At each confirmed local-mode startup, the gateway also ensures that
`server.api_keys` contains a key named `codex` and reuses its existing value
without rotating it. Codex `config.toml` is updated to select
`model_provider = "codex_rosetta"` and to replace the managed
`[model_providers.codex_rosetta]` table with an OpenAI-named Responses provider
pointing to `http://127.0.0.1:<effective-port>/v1`. Other provider tables and
their parameters are preserved. The effective port includes a CLI `--port`
override. Local mode also ensures the `[desktop]` table's
`enabled-reasoning-efforts` setting exposes `low`, `medium`, `high`, `xhigh`,
`max`, and `ultra`; an existing line containing all six values is left intact.

The first time local mode is enabled, the gateway asks before replacing an
existing `model_catalog_json` setting. To enable it persistently from the CLI,
including in an interactive environment, run:

```bash
codex-rosetta-gateway --local-mode
```

For unattended startup, explicitly approve catalog replacement:

```bash
codex-rosetta-gateway --confirm-clear-existing-catalog
```

`--confirm-clear-existing-catalog` records consent but does not enable a
previously disabled local mode. Combine it with `--local-mode` when both actions
are required.

Once `--local-mode` has been used, the enabled state is stored in the gateway
configuration and remains on for later starts without that option. To disable
local mode persistently without modifying Codex Home, run:

```bash
codex-rosetta-gateway --no-local-mode
```

`--local-mode` and `--no-local-mode` are mutually exclusive. To disable local
mode and also remove Rosetta's managed catalog, catalog assignment, selected
provider, and managed provider table from Codex `config.toml`, run:

```bash
codex-rosetta-gateway local-mode clear
```

The generated `codex` gateway key is retained so re-enabling local mode can
reuse it without rotating credentials.

The target Codex Home comes from `--codex-home`, then `CODEX_HOME`, and defaults
to `~/.codex`. When local mode is enabled with a non-loopback gateway host, the
gateway warns that remote clients must configure their own `config.toml` and
`model_catalog_json` manually; local mode only updates the selected Codex Home
on the gateway machine.

## Codex Model Names and Built-in Roles

Avoid exposing third-party model names such as `deepseek-v4-pro` or `glm-5.2`
directly to Codex. Unknown names receive fallback model metadata, which can
change the tools, Responses request shape, reasoning controls, context limits,
and multi-agent behavior that Codex enables. Instead, configure a Rosetta model
group whose public model name is one of Codex's built-in names and whose
`upstream_model` is the provider's real model ID.

Recommended public model names are:

- `gpt-5.6-sol`
- `gpt-5.6-terra`
- `gpt-5.5`
- `gpt-5.4`
- `gpt-5.4-mini`
- `gpt-5.2`

The public name controls the model metadata and tool surface selected by Codex;
Rosetta replaces it with `upstream_model` before sending the request to the
provider. Choose a built-in name whose advertised context window, modalities,
reasoning behavior, and tool mode are compatible with the actual upstream
model. A name mapping does not make the upstream model acquire capabilities it
does not support.

The following built-in names have special roles:

- `gpt-5.6-luna` uses the legacy multi-agent v1 tool surface. Subagent behavior
  may be unreliable, and Rosetta's built-in **Chat Default** Tool Profile
  disables the `multi_agent_v1` namespace.
- `gpt-5.4` is the default model for memory consolidation. Override it with
  `memories.consolidation_model` in Codex `config.toml` when the provider uses a
  different public model name.
- `gpt-5.4-mini` is the default model for extracting memories from historical
  threads. Override it with `memories.extract_model`.
- `codex-auto-review` is the default automatic approval-review model, including
  command-execution approval review. Override it through the active model's
  `auto_review_model_override` field in a Codex model catalog; this is model
  metadata, not a top-level `config.toml` option.

For example, memory models can be overridden in Codex `config.toml`:

```toml
[memories]
consolidation_model = "your-consolidation-model"
extract_model = "your-extraction-model"
```

### Enable v2 Collaboration for Models Without a Fixed Version

`gpt-5.6-sol` and `gpt-5.6-terra` already select v2 `collaboration` through
their built-in model metadata. `gpt-5.6-luna` explicitly selects legacy v1, so
the feature setting below does not upgrade Luna. For built-in models whose
catalog entry does not specify a multi-agent version, such as `gpt-5.5`,
`gpt-5.4`, `gpt-5.4-mini`, and `gpt-5.2`, enable v2 in Codex `config.toml`:

```toml
[features]
multi_agent_v2 = true
```

Start a new Codex task after changing this setting. The selected multi-agent
version is retained by an existing task, so switching models or changing the
feature during that task may not replace its current tool surface. The stable
`multi_agent` feature (also accepted through its legacy `collab` alias) selects
the legacy v1 tools when v2 is not enabled; new configurations should prefer
`multi_agent_v2` when the target model can use the `collaboration` namespace.

## Full Documentation

- [English user documentation](docs/en/README.md)
- [Gateway security and authentication](docs/en/gateway-security.md)
- [Developer documentation](docs/dev/README.md)

## Problems This Project Addresses

Using third-party models in Codex usually runs into several issues:

- Providers may only expose a Chat Completions API.
- Models may not know how to edit files with `apply_patch`, so they fall back to `sed`, Python scripts, or other shell commands.
- Built-in Codex flows such as Goal and subagents may behave incorrectly.
- Models may not proactively call plugins.
- Some models do not support multimodal image understanding.
- Computer use and browser use may be unreliable.
- Models may not match the intended reasoning depth.

This project aims to improve those behaviors so strong models such as DeepSeek V4 Pro, GLM-5.x, and Qwen3.7 can run smoothly in Codex, with lower cost while still using Codex's advanced agent capabilities.

Currently solved, but not yet heavily production-tested:

- Responses API conversion: [Oaklight/llm-rosetta](https://github.com/Oaklight/llm-rosetta) provides the project base, core protocol conversion, and a simple web UI.
- Code editing tool translation: because these models often recommend Claude Code as their preferred coding agent, this project references Claude Code-style tool definitions. Models can emit familiar tool calls, and Rosetta converts them back to `apply_patch` or other Codex-native calls.
- Input-cache preservation: because the gateway intercepts and rewrites tool calls, the provider-side cache and Codex's local session history can otherwise diverge. Rosetta rewrites historical tool calls in outgoing requests so provider input caches can still match.
- Goal, TODO, Plan, and Subagent flows have been tested successfully.
- Work-process folding, with the trade-off that streaming is lost, but the behavior can now be toggled on or off.
- Model reasoning-depth mapping.

## Supported Providers

- Downstream clients should call `/v1/responses` for generation. `/v1/chat/completions`, `/v1/messages`, and Google GenAI generation endpoints are not exposed as client-facing generation routes.
- DeepSeek, Opencode Go, and other services that expose an OpenAI Chat Completions-compatible upstream API. Rosetta performs protocol conversion and tool-layer translation.
- OpenAI, API relay services, and other services that expose an OpenAI Responses-compatible upstream API. Rosetta directly passes through these requests without decoding and re-encoding them.
- Anthropic Messages and Google GenAI upstream providers remain available through the conversion pipeline.

## Citation

[![LLM-Rosetta: A Hub-and-Spoke Intermediate Representation for Cross-Provider LLM API Translation (arXiv)](https://img.shields.io/badge/arXiv-2604.09360-b31b1b.svg)](https://arxiv.org/abs/2604.09360)

## Contributing

Contributions are welcome. Visit the [GitHub repository](https://github.com/iBobbyTS/codex-rosetta) to get started.

## License

This project keeps the MIT license. See [LICENSE](LICENSE) for details.
