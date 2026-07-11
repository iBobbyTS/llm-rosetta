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

Use `--log-level info` (the default) to print request summaries, `warning` to
show only warnings and errors, or `error` to show only errors. Use the WebUI
**Request Log** for complete request history and **Gateway Logs** for streaming
trace diagnostics.

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
