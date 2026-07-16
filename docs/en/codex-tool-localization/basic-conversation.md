# Basic Conversation

Codex talks to models through the OpenAI Responses API surface. Many third-party providers only expose an OpenAI Chat Completions-compatible endpoint. Codex-Rosetta bridges that gap in different ways depending on the route:

- The Admin UI exposes one **OpenAI Responses** protocol. Every Provider uses the direct Responses path; Provider selection changes only the default Tool Profile.
- Responses to Chat routes are converted through Codex-Rosetta's IR, then converted back to Responses events for Codex.

The goal is to preserve Codex runtime semantics, not just make the upstream request syntactically valid.

## One Responses Protocol, Provider-Aware Defaults

Provider configuration stores `api_type: "responses"`. The Provider choice selects the model-group default Profile, while protocol handling remains direct:

- OpenAI Official selects **透传（适用于OpenAI官方API）** and keeps the request, tool declarations, response JSON, and SSE bytes on the direct path.
- OpenAI Custom and Custom + Custom select **web.run 注入（适用于尚未支持/alpha/search端点的中转站）**. This keeps every original tool shape except `web.run`, which is Modified and handled by Rosetta.
- A listed third-party provider with Responses selects **工具映射（适用于第三方模型提供的Responses接口）** while keeping direct Responses transport.
- Any Chat protocol selects **Chat Default（适用于第三方仅提供chat api的模型）**. Other protocols currently use the same fallback through a separate branch reserved for future protocol defaults.

The only supported Responses protocol value is `responses`; the former `responses_passthrough` and `responses_rosetta` values are no longer accepted and must be replaced before loading the configuration.

## Direct Responses Transport

For every same-protocol Responses route, the gateway does not decode and re-encode the complete request through IR. It applies the selected Tool Profile, forwards the resulting request, and streams raw upstream SSE bytes back to Codex. Model-switch compaction is the deliberate semantic exception: Rosetta asks the previous model for a plaintext summary, stores its replacement for seven days, and rehydrates it before the next Provider request. The transport-level exception is an authenticated request with `Content-Encoding: zstd`: Rosetta decodes it under the configured pre/post-decompression size limits and removes the encoding header first.

This is important because Codex relies on fields that are not part of a minimal cross-provider IR, including:

- `phase` on message output items, used by Codex to fold work-process output.
- Reasoning items and encrypted reasoning payloads.
- Native Responses tool item structure.
- Provider-specific request fields such as `include`.

The bundled **透传** Profile leaves every native function, custom, hosted, and Namespace tool Passthrough. Its only Disabled entries are synthetic Rosetta injections, so it cannot add a tool Codex did not send. The bundled **web.run 注入** Profile differs in exactly one state: `web.run` is Modified. Rosetta then rewrites only the live `web__run` section nested in the custom `exec` description; other Responses fields and upstream response bytes remain on the direct path. **工具映射** inherits the established Chat Default mapping policy for listed third-party Responses implementations.

Codex's standalone Search and Images clients use three additional JSON endpoints:

- `POST /v1/alpha/search`
- `POST /v1/images/generations`
- `POST /v1/images/edits`

Images use the selected Profile's `image_gen.imagegen` state:

- **Passthrough** forwards the endpoint only when the request model resolves to
  a direct OpenAI Responses provider.
- **Modified** sends generation and edit requests to the OpenAI Images API Base
  URL configured on the Function card, using its Token as a Bearer credential.
  This path is available to Responses, Chat,
  Anthropic, and Google model groups.
- **Disabled** rejects the Images endpoint.

The request model must resolve to a model group selecting that Profile. The
configured upstream model alias is applied, while the remaining OpenAI Images
request and JSON response bypass IR conversion. Modified currently supports
only the OpenAI `images/generations` and `images/edits` wire API; Rosetta does
not translate vendor-specific image APIs.

Standalone Search has an additional local bridge. When the selected Profile
marks `web.run` as Modified, `/v1/alpha/search` executes the reliable subset
locally: `search_query` uses the global Provider configured under Admin
**Web Search** (`server.web_search`). Tavily uses the configured API Key;
**Self-hosted (Google)**, **Self-hosted (Bing RSS)**, and
**Self-hosted (Bing Browser)** run in the existing `web-run` container and
therefore require that sidecar to be healthy. Bing RSS reads the XML result
representation, while Bing Browser loads and parses the interactive HTML result
page. All providers are normalized to the same Codex-visible source format. Direct-URL
`open` fetches public static HTML or plain text, and `time` uses
Python fixed-UTC-offset calculation. Open validates every redirect target,
rejects credentials and non-public addresses, permits at most five redirects,
and applies a 15-second and 2 MiB response limit. It returns normalized,
line-addressable text and supports `lineno`; stored `turnXsearchY` references
resolve to their scoped search-result URL.

An optional, separately built `web-run` Docker sidecar provides self-hosted
Google or Bing basic search and adds JavaScript-rendered `open`, session-scoped
`turnXfetchY` page references, numbered-link `click`,
case-insensitive `find`, and PDF `turnXviewY` references. PDF `open` and `find`
use PyMuPDF embedded-text extraction. PDF `screenshot` renders the requested
page with PyMuPDF and uses Tesseract when the rendered page has no embedded
text. The Codex Search endpoint returns text rather than a multimodal image
item, so screenshot results contain rendered dimensions and extracted/OCR text;
they do not inject PDF pixels into the model conversation. Without the sidecar,
the model-facing Modified schema omits `click`, `find`, and `screenshot`, and
static `open` remains the bounded Python implementation. Configuring the
sidecar is not enough to advertise browser commands: its bounded health status
must report both an online service and `browser_ready=true`.

Supported search options are query/domain filters, search context size,
response length, and a conservative output budget. Requests containing
`image_query`, finance, weather, sports, recency, blocked-domain, location, or
non-live access semantics return HTTP `501` with `code: "not_implemented"`
before any partial operation runs. Every `501` message from these auxiliary
endpoints also ends with `Consider "Browser Use" skill` so Codex can choose the
browser fallback. When a selected Profile sets `web.run` to Passthrough,
`/v1/alpha/search` remains native upstream pass-through even when Tavily or the
sidecar is configured. When it sets `web.run` to Modified, supported commands
use Rosetta's search service. The model-visible definition always retains
direct-URL `open`, fixed-offset `time`, and `response_length`; it adds
`search_query` when either a global Tavily API Key is configured or
either self-hosted provider is selected and the sidecar reports ready, and adds
`click`, `find`, and `screenshot` only while the optional sidecar reports
`browser_ready=true`. The Hosted `web_search` tool remains independent:
its Provider, Token, and guidance continue to belong to the selected Profile.

`/v1/alpha/search` above is the Codex-facing Gateway route. Passthrough does not
assume that an upstream Base URL omitted `/v1`: it appends the relative
`alpha/search` path to the configured Base URL. A Base URL ending in `/v1`
therefore receives `/v1/alpha/search`, while a versionless Base URL receives
`/alpha/search`.

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
