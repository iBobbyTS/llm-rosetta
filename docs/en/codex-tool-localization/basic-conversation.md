# Basic Conversation

Codex talks to models through the OpenAI Responses API surface. Many third-party providers only expose an OpenAI Chat Completions-compatible endpoint. Codex-Rosetta bridges that gap in different ways depending on the route:

- Responses to Responses providers configured as **OpenAI Responses (Pass through)** are passed through directly.
- Responses to Responses providers configured as **OpenAI Responses (Rosetta)** are decoded through Codex-Rosetta's IR and encoded back to the Responses format.
- Responses to Chat routes are converted through Codex-Rosetta's IR, then converted back to Responses events for Codex.

The goal is to preserve Codex runtime semantics, not just make the upstream request syntactically valid.

## The Two Responses Options Are Not New Protocols

**OpenAI Responses (Pass through)** and **OpenAI Responses (Rosetta)** use the same OpenAI Responses wire protocol, endpoint shape, and converter. They are separate Admin UI choices only because the gateway handles them differently internally:

- **Pass through** forwards the request, response JSON, and streaming SSE bytes with minimal intervention. Use it for official OpenAI or GPT proxy services that preserve OpenAI's Responses behavior.
- **Rosetta** runs the request and response through the Responses → IR → Responses pipeline. Use it for other model providers that support the Responses protocol but need Rosetta's normalization, such as Qwen.

The configured `api_type` values are `responses_passthrough` and `responses_rosetta`. Both resolve to the internal `openai_responses` provider type; they do not add public gateway endpoints or API standards.

Only Responses pass-through and Responses-to-Chat conversion are currently guaranteed. Responses (Rosetta), Anthropic conversion, and Google conversion remain unguaranteed; this mode-selection work does not expand Responses field or event unpacking.

## Responses Pass-Through

For same-protocol OpenAI Responses routes configured as **Pass through**, the gateway normally does not decode and re-encode the request body. It forwards the original request and streams raw upstream SSE bytes back to Codex. The transport-level exception is an authenticated request with `Content-Encoding: zstd`: Rosetta decodes it under the configured pre/post-decompression size limits, removes the encoding header, and then preserves the decoded JSON fields through pass-through handling.

This is important because Codex relies on fields that are not part of a minimal cross-provider IR, including:

- `phase` on message output items, used by Codex to fold work-process output.
- Reasoning items and encrypted reasoning payloads.
- Native Responses tool item structure.
- Provider-specific request fields such as `include`.

Tool Profiles do not apply to Pass-through providers. The gateway forwards their tool definitions and choices without Profile filtering, modification, namespace expansion, or Rosetta tool injection. Tool Profiles remain selectable for **OpenAI Responses (Rosetta)**, Chat, Anthropic, and Google providers.

Codex's standalone Search and Images clients use three additional JSON endpoints:

- `POST /v1/alpha/search`
- `POST /v1/images/generations`
- `POST /v1/images/edits`

The gateway forwards these endpoints only when the request model resolves to an **OpenAI Responses (Pass through)** provider. The configured upstream model alias is applied, but the payload and JSON response otherwise bypass IR conversion. Responses (Rosetta), Chat, Anthropic, and Google routes return `501 Not Implemented` for these endpoints.

## Responses To Chat Conversion

For Chat-only providers, Codex-Rosetta converts the incoming Responses request into IR, then into a Chat Completions request. The converted Chat request keeps the conversation, tools, tool choice, reasoning configuration, and stream configuration as much as the target API allows.

When the Chat response returns, Rosetta converts it back into Responses-compatible output so Codex can keep driving the agent loop.

The gateway also preserves selected runtime state across the request and response phases:

- Responses namespace tool mappings are stored in the conversion context.
- Provider metadata such as reasoning or tool-call metadata can be cached by tool call ID and reinjected into later requests.
- User-Agent and OpenResponses-Version headers are forwarded through an explicit header allowlist.

## Streaming Shape

For converted streaming responses, Rosetta rebuilds Responses SSE events from upstream Chat chunks. It emits the same broad categories Codex expects:

- `response.output_item.added`
- text deltas and text done events
- tool call start, argument delta, and done events
- reasoning events when available
- `response.completed`

The converter preserves message `phase` metadata on both added and done message items so Codex can fold commentary/work output when the final answer is produced.

## Reasoning

For Chat providers that emit reasoning in a provider-specific field, Rosetta keeps that reasoning separate from normal assistant output when the upstream provides it separately. DeepSeek-style `reasoning_content` is preserved through tool loops so the next request can satisfy providers that require the reasoning content to be echoed back.

If a model emits reasoning inside ordinary text, for example as `<think>...</think>`, Rosetta treats that as normal output because the provider did not separate it semantically.

## Context Handling

Codex currently sends full conversation context in repeated requests instead of relying only on `previous_response_id`. Rosetta therefore treats the incoming request body as the source of truth for the current turn. It does not implement server-side Responses conversation storage.

This matters for Chat conversion: if Codex resends historical assistant tool calls, Rosetta must keep those historical calls coherent with what the upstream model originally saw. Code editing localization uses a persistent mapping cache for that reason.
