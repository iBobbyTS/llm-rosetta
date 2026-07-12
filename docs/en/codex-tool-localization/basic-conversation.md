# Basic Conversation

Codex talks to models through the OpenAI Responses API surface. Many third-party providers only expose an OpenAI Chat Completions-compatible endpoint. Codex-Rosetta bridges that gap in different ways depending on the route:

- Responses to Responses providers configured as **OpenAI Responses (Tool Mapping only)** apply the selected Tool Profile, then pass the Responses payload through directly.
- Responses to Responses providers configured as **OpenAI Responses (Rosetta)** are decoded through Codex-Rosetta's IR and encoded back to the Responses format.
- Responses to Chat routes are converted through Codex-Rosetta's IR, then converted back to Responses events for Codex.

The goal is to preserve Codex runtime semantics, not just make the upstream request syntactically valid.

## The Two Responses Options Are Not New Protocols

**OpenAI Responses (Tool Mapping only)** and **OpenAI Responses (Rosetta)** use the same OpenAI Responses wire protocol and endpoint shape. They are separate Admin UI choices only because the gateway handles them differently internally:

- **Tool Mapping only** applies the selected Tool Profile without running the complete Responses body through IR, then forwards the response JSON and streaming SSE bytes directly. Use it for official OpenAI or GPT proxy services that preserve OpenAI's Responses behavior.
- **Rosetta** runs the request and response through the Responses → IR → Responses pipeline. Use it for other model providers that support the Responses protocol but need Rosetta's normalization, such as Qwen.

The configured `api_type` values are `responses_passthrough` and `responses_rosetta`. Both resolve to the internal `openai_responses` provider type; they do not add public gateway endpoints or API standards.

Only the direct transport used by Tool Mapping only and Responses-to-Chat conversion are currently guaranteed. Responses (Rosetta), Anthropic conversion, and Google conversion remain unguaranteed; this mode-selection work does not expand Responses field or event unpacking.

## Responses Tool Mapping Only

For same-protocol OpenAI Responses routes configured as **Tool Mapping only**, the gateway does not decode and re-encode the complete request through IR. It applies the selected Tool Profile, forwards the resulting request, and streams raw upstream SSE bytes back to Codex. The transport-level exception is an authenticated request with `Content-Encoding: zstd`: Rosetta decodes it under the configured pre/post-decompression size limits and removes the encoding header first.

This is important because Codex relies on fields that are not part of a minimal cross-provider IR, including:

- `phase` on message output items, used by Codex to fold work-process output.
- Reasoning items and encrypted reasoning payloads.
- Native Responses tool item structure.
- Provider-specific request fields such as `include`.

Tool Profiles are selectable for this mode. The bundled **Responses pass through** Profile leaves native tools unchanged and disables Rosetta-only injections. The bundled **Responses web.run mapping** Profile differs only by setting `web.run` to Modified: Codex still exposes and calls `tools.web__run`, while `/v1/alpha/search` is handled by Rosetta's reliable Tavily/Python subset. Other Responses fields and upstream response bytes remain on the direct path.

Codex's standalone Search and Images clients use three additional JSON endpoints:

- `POST /v1/alpha/search`
- `POST /v1/images/generations`
- `POST /v1/images/edits`

Images use the selected Profile's `image_gen.imagegen` state:

- **Passthrough** forwards the endpoint only when the request model resolves to
  an **OpenAI Responses (Tool Mapping only)** provider.
- **Modified** sends generation and edit requests to the OpenAI Images API Base
  URL configured on the Function card, using its Token as a Bearer credential.
  This path is available to Tool Mapping only, Responses Rosetta, Chat,
  Anthropic, and Google model groups.
- **Disabled** rejects the Images endpoint.

The request model must resolve to a model group selecting that Profile. The
configured upstream model alias is applied, while the remaining OpenAI Images
request and JSON response bypass IR conversion. Modified currently supports
only the OpenAI `images/generations` and `images/edits` wire API; Rosetta does
not translate vendor-specific image APIs.

Standalone Search has an additional local bridge. When the selected Profile
marks `web.run` as Modified, `/v1/alpha/search` executes the reliable subset
locally: `search_query` uses the Provider and Token configured beneath the
`web.run` card in that Profile. Tavily is currently the only provider,
direct-URL `open` fetches public static HTML or plain text, and `time` uses
Python fixed-UTC-offset calculation. Open validates every redirect target,
rejects credentials and non-public addresses, permits at most five redirects,
and applies a 15-second and 2 MiB response limit. It returns normalized,
line-addressable text and supports `lineno`; stored references such as
`turn0search0`, JavaScript rendering, compressed pages, and non-text content
remain unsupported. Supported search options are query/domain filters, search
context size, response length, and a conservative output budget. Requests
containing `click`, `find`, `image_query`, `screenshot`, finance, weather,
sports, recency, blocked-domain, location, or non-live access semantics return
HTTP `501` with `code: "not_implemented"` before any partial operation runs.
Every `501` message from these auxiliary endpoints also ends with
`Consider "Browser Use" skill` so Codex can choose the browser fallback.
With the **Responses pass through** Profile, `/alpha/search` remains native
upstream pass-through even when a Tavily Token is configured. With **Responses web.run
mapping**, supported commands use the local executor; search queries require a
Tavily Token on the `web.run` card, while direct-URL `open` and time-only requests use Python.

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
