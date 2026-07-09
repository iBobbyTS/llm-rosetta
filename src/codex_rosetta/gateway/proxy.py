"""Proxy engine — response conversion, metadata caching, and pipeline handlers.

This module contains the core proxy logic:
- Provider metadata caching (e.g. Google ``thought_signature``)
- Shim transform resolution
- Non-streaming and streaming request handlers
- Error response helpers
- Request body helpers

Transport-level concerns (HTTP client, SSE parsing, upstream request assembly)
are delegated to the :class:`~transport.UpstreamTransport` interface.
Downstream SSE formatting lives in :mod:`transport.sse_format`.
"""

from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from codex_rosetta._vendor.httpserver import JSONResponse, Response, StreamingResponse

from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.pipeline import ConversionError, ConversionPipeline
from codex_rosetta.routing import ResolvedRoute

from codex_rosetta.observability.error_dump import dump_error

from .logging import (
    get_logger,
    log_converted_request,
    log_original_request,
    log_response,
    log_stream_summary,
    log_upstream_error,
)
from .stream_phase_buffer import ResponsesPhaseBuffer
from .stream_trace import StreamTraceLogger, StreamTraceState
from .tool_adaptation import (
    CodexToolLocalizationStore,
    LOCALIZATION_CAPABILITIES_KEY,
    READ_OUTPUT_CACHE_KEY,
    LocalizedToolMapping,
    LocalizedToolCallStreamTransformer,
    NativeToolCapabilities,
    ReadOutputCache,
    enable_phase_detection,
    enable_tool_description_optimization,
    localized_mapping_from_tool_calls,
    localize_code_editing_chat_request,
    should_localize_code_tools,
    tool_call_cache_ttl_hours,
    translate_localized_ir_response,
    use_apply_patch_for_code_edits,
)
from .transport import (
    ProviderInfo,
    UpstreamConnectionError,
    UpstreamTransport,
)
from .transport.sse_format import SSE_FORMATTERS, format_sse_done
from .web_search import (
    TavilySearchClient,
    WebSearchRuntime,
    WebSearchStreamController,
    build_web_search_runtime,
    strip_responses_web_search_tools,
    web_search_trace_summary,
)

logger = get_logger()


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def error_response_for_source(
    source_provider: ProviderType, status_code: int, message: str
) -> Response:
    """Return an error response formatted for the source provider's envelope."""
    if source_provider == "openai_chat":
        body = {
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "code": None,
            }
        }
    elif source_provider in ("openai_responses", "open_responses"):
        body = {
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "code": None,
            }
        }
    elif source_provider == "anthropic":
        body = {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": message},
        }
    elif source_provider == "google":
        body = {
            "error": {
                "code": status_code,
                "message": message,
                "status": "INVALID_ARGUMENT",
            }
        }
    else:
        body = {"error": {"message": message}}

    return JSONResponse(body, status_code=status_code)


# ---------------------------------------------------------------------------
# Request body helpers
# ---------------------------------------------------------------------------


def detect_stream_request(source_provider: ProviderType, body: dict[str, Any]) -> bool:
    """Detect if the incoming request asks for streaming."""
    if source_provider in (
        "openai_chat",
        "openai_responses",
        "open_responses",
        "anthropic",
    ):
        return bool(body.get("stream", False))
    # Google streaming is determined by the endpoint path, not the body
    return False


def extract_model(source_provider: ProviderType, body: dict[str, Any]) -> str | None:
    """Extract the model name from a source-format request body."""
    return body.get("model")


def _is_openai_responses_direct(route: ResolvedRoute) -> bool:
    """Return true for same-protocol Responses requests that can pass through."""
    return route.source_provider in (
        "openai_responses",
        "open_responses",
    ) and route.target_provider in ("openai_responses", "open_responses")


def _uses_responses_chat_bridge(route: ResolvedRoute) -> bool:
    """Return true for Responses client requests bridged to Chat upstream."""
    return (
        route.source_provider
        in (
            "openai_responses",
            "open_responses",
        )
        and route.target_provider == "openai_chat"
    )


_DIRECT_RESPONSES_NAMESPACE_TOOLS = {
    "codex_app",
    "multi_agent_v1",
}

_GITHUB_NAMESPACE_NAME = "mcp__codex_apps__github"
_GITHUB_OWNER_HINT = (
    "Do not guess. If the owner is not explicitly provided, inspect the local "
    "git remote first, for example by running git remote -v."
)
_GITHUB_REPO_HINT = (
    "Do not guess. If the repository name is not explicitly provided, derive "
    "it from the user request or inspect the local git remote first, for example "
    "by running git remote -v."
)
_TOOL_SEARCH_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _is_responses_tool_search_definition(tool: Any) -> bool:
    """Return true when a Responses tool definition exposes native tool search."""
    return isinstance(tool, dict) and tool.get("type") == "tool_search"


def _should_defer_responses_namespace_tool(tool: Any) -> bool:
    """Return true for namespace tools that should be discovered via tool_search."""
    if not isinstance(tool, dict) or tool.get("type") != "namespace":
        return False
    name = tool.get("name")
    return isinstance(name, str) and name not in _DIRECT_RESPONSES_NAMESPACE_TOOLS


def _append_schema_description_hint(schema: dict[str, Any], hint: str) -> None:
    description = schema.get("description")
    if isinstance(description, str) and description.strip():
        if hint not in description:
            schema["description"] = f"{description.rstrip()} {hint}"
    else:
        schema["description"] = hint


def _patch_github_namespace_tool_schema_for_chat(
    tool: dict[str, Any],
) -> dict[str, Any]:
    """Add Chat-only GitHub owner/repo hints to loadable namespace tools."""
    metadata = tool.get("metadata")
    if not isinstance(metadata, dict):
        return tool
    if metadata.get("responses_namespace") != _GITHUB_NAMESPACE_NAME:
        return tool

    parameters = tool.get("parameters")
    if not isinstance(parameters, dict):
        return tool
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return tool

    patched = copy.deepcopy(tool)
    patched_parameters = patched.get("parameters")
    if not isinstance(patched_parameters, dict):
        return patched
    patched_properties = patched_parameters.get("properties")
    if not isinstance(patched_properties, dict):
        return patched

    owner_schema = patched_properties.get("owner")
    if isinstance(owner_schema, dict):
        _append_schema_description_hint(owner_schema, _GITHUB_OWNER_HINT)

    repo_schema = patched_properties.get("repo")
    if isinstance(repo_schema, dict):
        _append_schema_description_hint(repo_schema, _GITHUB_REPO_HINT)

    return patched


def _synthetic_responses_tool_search_definition(
    deferred_tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a minimal native Responses tool_search definition for Chat bridges."""
    source_lines: list[str] = []
    for tool in deferred_tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        description = tool.get("description")
        if isinstance(description, str) and description:
            normalized = " ".join(description.split())
            if len(normalized) > 240:
                normalized = normalized[:237].rstrip() + "..."
            source_lines.append(f"- {name}: {normalized}")
        else:
            source_lines.append(f"- {name}")

    source_hint = (
        "\n\nAvailable deferred tool sources:\n" + "\n".join(source_lines)
        if source_lines
        else ""
    )
    return {
        "type": "tool_search",
        "execution": "client",
        "description": (
            "Search deferred Codex tools by capability or provider name and expose "
            "the matching tools for a later model request. The query is for tool "
            "discovery only: include generic capability/source terms, not task data. "
            "Do not include repository names, PR/issue numbers, URLs, file paths, "
            "user text, or other runtime arguments; pass those later to the loaded "
            "tool."
            f"{source_hint}"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Tool-discovery query using generic capability/source terms "
                        "only. Do not include task-specific values such as repo names, "
                        "PR numbers, issue IDs, URLs, file paths, or user content."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Use 8 unless previous search didn't give enough tools.",
                },
            },
            "required": ["query"],
        },
    }


def _minimal_responses_tool_search_definition() -> dict[str, Any]:
    """Build a minimal native Responses tool_search definition for Chat bridges."""
    return {
        "type": "tool_search",
        "execution": "client",
        "description": "Search deferred Codex tools.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    }


def _responses_tool_containers(
    body: dict[str, Any],
) -> list[tuple[dict[str, Any], list[Any]]]:
    """Return top-level and Responses Lite embedded tool containers."""
    containers: list[tuple[dict[str, Any], list[Any]]] = []
    tools = body.get("tools")
    if isinstance(tools, list):
        containers.append((body, tools))

    input_items = body.get("input")
    if not isinstance(input_items, list):
        return containers
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "additional_tools":
            continue
        embedded_tools = item.get("tools")
        if isinstance(embedded_tools, list):
            containers.append((item, embedded_tools))
    return containers


def _flatten_responses_tools(body: dict[str, Any]) -> list[Any]:
    """Flatten all Responses tool containers without modifying the request."""
    return [
        tool for _container, tools in _responses_tool_containers(body) for tool in tools
    ]


def _defer_responses_namespace_tools_for_chat(
    body: dict[str, Any],
    *,
    optimize_tool_descriptions: bool = True,
) -> list[dict[str, Any]]:
    """Hide deferred Responses namespaces from Chat until tool_search loads them."""
    containers = _responses_tool_containers(body)
    if not containers:
        return []

    deferred_tools: list[dict[str, Any]] = []
    has_tool_search = False
    filtered_containers: list[tuple[dict[str, Any], list[Any]]] = []
    for container, tools in containers:
        filtered_tools: list[Any] = []
        for tool in tools:
            if _is_responses_tool_search_definition(tool):
                has_tool_search = True
                filtered_tools.append(tool)
            elif _should_defer_responses_namespace_tool(tool):
                deferred_tools.append(tool)
            else:
                filtered_tools.append(tool)
        filtered_containers.append((container, filtered_tools))

    if not deferred_tools:
        return []

    if not has_tool_search:
        if optimize_tool_descriptions:
            filtered_containers[0][1].append(
                _synthetic_responses_tool_search_definition(deferred_tools)
            )
        else:
            filtered_containers[0][1].append(
                _minimal_responses_tool_search_definition()
            )

    for container, filtered_tools in filtered_containers:
        container["tools"] = filtered_tools
    return deferred_tools


def _tool_identifier(tool: Any) -> str | None:
    """Return the provider-facing name/type used to identify a tool definition."""
    if not isinstance(tool, dict):
        return None

    tool_type = tool.get("type")
    if tool_type == "function":
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return function["name"]
        if tool.get("name"):
            return tool["name"]
    return tool.get("name") or tool_type


def _tool_choice_identifier(tool_choice: Any) -> str | None:
    """Return the explicitly selected tool name from a tool_choice value."""
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None

    if tool_choice.get("tool_name"):
        return tool_choice["tool_name"]
    if tool_choice.get("name"):
        return tool_choice["name"]

    function = tool_choice.get("function")
    if isinstance(function, dict) and function.get("name"):
        return function["name"]

    choice_type = tool_choice.get("type")
    if choice_type not in (None, "auto", "none", "required", "function", "tool"):
        return choice_type
    return None


def _remove_tool_definition(
    body: dict[str, Any],
    tool_name: str,
    *,
    aliases: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Remove named tools from top-level and Responses Lite containers."""
    removed_names = {tool_name, *aliases}
    changed = False
    adapted = dict(body)

    tools = body.get("tools")
    if isinstance(tools, list):
        filtered_tools = [
            tool for tool in tools if _tool_identifier(tool) not in removed_names
        ]
        if len(filtered_tools) != len(tools):
            changed = True
            if filtered_tools:
                adapted["tools"] = filtered_tools
            else:
                adapted.pop("tools", None)

    input_items = body.get("input")
    if isinstance(input_items, list):
        filtered_input: list[Any] = []
        for item in input_items:
            if not isinstance(item, dict) or item.get("type") != "additional_tools":
                filtered_input.append(item)
                continue
            embedded_tools = item.get("tools")
            if not isinstance(embedded_tools, list):
                filtered_input.append(item)
                continue
            filtered_embedded = [
                tool
                for tool in embedded_tools
                if _tool_identifier(tool) not in removed_names
            ]
            if len(filtered_embedded) == len(embedded_tools):
                filtered_input.append(item)
                continue
            changed = True
            if filtered_embedded:
                filtered_item = dict(item)
                filtered_item["tools"] = filtered_embedded
                filtered_input.append(filtered_item)
        if changed:
            adapted["input"] = filtered_input

    if not changed:
        return body

    remaining_tools = bool(_flatten_responses_tools(adapted))
    if not remaining_tools:
        adapted.pop("tool_config", None)

    if (
        _tool_choice_identifier(adapted.get("tool_choice")) in removed_names
        or not remaining_tools
    ):
        adapted.pop("tool_choice", None)
    return adapted


def _apply_tool_adaptation(
    body: dict[str, Any], route: ResolvedRoute
) -> dict[str, Any]:
    """Apply per-model tool adaptation before passthrough or conversion."""
    tool_adaptation = route.tool_adaptation or {}
    if tool_adaptation.get("remove_image_generation"):
        return _remove_tool_definition(
            body,
            "image_generation",
            aliases=frozenset({"image_gen", "imagegen", "image_gen__imagegen"}),
        )
    return body


def _apply_converted_request_tool_adaptation(
    body: dict[str, Any],
    route: ResolvedRoute,
    *,
    codex_tool_store: CodexToolLocalizationStore | None = None,
    persistent_mappings: list[LocalizedToolMapping] | None = None,
    used_mapping_call_ids: set[str] | None = None,
    capabilities: NativeToolCapabilities | None = None,
) -> dict[str, Any]:
    """Apply tool adaptation after source request has been converted."""
    if should_localize_code_tools(route):
        return localize_code_editing_chat_request(
            body,
            store=codex_tool_store,
            mappings=persistent_mappings,
            used_call_ids=used_mapping_call_ids,
            capabilities=capabilities,
        )
    return body


def _pop_tool_localization_capabilities(
    body: dict[str, Any],
) -> NativeToolCapabilities:
    """Remove and return internal tool localization metadata from a request."""
    return NativeToolCapabilities.from_metadata(
        body.pop(LOCALIZATION_CAPABILITIES_KEY, None)
    )


def _pop_read_output_cache(body: dict[str, Any]) -> ReadOutputCache | None:
    """Remove and return internal Read output cache metadata from a request."""
    value = body.pop(READ_OUTPUT_CACHE_KEY, None)
    return value if isinstance(value, ReadOutputCache) else None


def _load_persistent_tool_mappings(
    persistence: Any | None,
    *,
    session_id: str | None,
) -> list[LocalizedToolMapping]:
    if persistence is None or not session_id:
        return []
    try:
        rows = persistence.query_tool_call_mappings(
            session_id=session_id,
            now=datetime.now(UTC).isoformat(),
        )
    except Exception:
        logger.debug("Failed to load persistent tool-call mappings", exc_info=True)
        return []

    mappings: list[LocalizedToolMapping] = []
    for row in rows:
        mapping = localized_mapping_from_tool_calls(
            row.get("original_tool_call") or {},
            row.get("codex_tool_call") or {},
        )
        if mapping is not None:
            mappings.append(mapping)
    return mappings


def _delete_unused_persistent_tool_mappings(
    persistence: Any | None,
    *,
    session_id: str | None,
    loaded_mappings: list[LocalizedToolMapping],
    used_call_ids: set[str],
) -> None:
    if persistence is None or not session_id or not loaded_mappings:
        return
    unused = [
        mapping.call_id
        for mapping in loaded_mappings
        if mapping.call_id not in used_call_ids
    ]
    if not unused:
        return
    try:
        persistence.delete_tool_call_mappings(
            session_id=session_id,
            tool_call_ids=unused,
        )
    except Exception:
        logger.debug("Failed to delete unused tool-call mappings", exc_info=True)


def _persist_tool_mapping(
    persistence: Any | None,
    *,
    session_id: str | None,
    ttl_hours: float,
    mapping: LocalizedToolMapping,
) -> None:
    if persistence is None or not session_id or not mapping.call_id:
        return
    now = datetime.now(UTC)
    try:
        persistence.upsert_tool_call_mapping(
            session_id=session_id,
            tool_call_id=mapping.call_id,
            original_tool_call=mapping.original_tool_call(),
            codex_tool_call=mapping.codex_tool_call(),
            expire_at=(now + timedelta(hours=ttl_hours)).isoformat(),
            timestamp=now.isoformat(),
        )
    except Exception:
        logger.debug("Failed to persist tool-call mapping", exc_info=True)


def _persist_localized_response_mappings(
    ir_response: dict[str, Any],
    *,
    tool_store: CodexToolLocalizationStore,
    persistence: Any | None,
    session_id: str | None,
    ttl_hours: float,
) -> None:
    for choice in ir_response.get("choices", []):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        for part in message.get("content", []):
            if not isinstance(part, dict) or part.get("type") != "tool_call":
                continue
            mapping = tool_store.get(part.get("tool_call_id", ""))
            if mapping is None:
                continue
            _persist_tool_mapping(
                persistence,
                session_id=session_id,
                ttl_hours=ttl_hours,
                mapping=mapping,
            )


def _translate_and_persist_localized_response_tools(
    ir_response: dict[str, Any],
    route: ResolvedRoute,
    *,
    tool_store: CodexToolLocalizationStore,
    persistence: Any | None,
    session_id: str | None,
    capabilities: NativeToolCapabilities | None = None,
    read_cache: ReadOutputCache | None = None,
) -> None:
    if not should_localize_code_tools(route):
        return
    translate_localized_ir_response(
        ir_response,
        store=tool_store,
        capabilities=capabilities,
        read_cache=read_cache,
        use_apply_patch=use_apply_patch_for_code_edits(route.tool_adaptation),
    )
    _persist_localized_response_mappings(
        ir_response,
        tool_store=tool_store,
        persistence=persistence,
        session_id=session_id,
        ttl_hours=tool_call_cache_ttl_hours(route.tool_adaptation),
    )


def _create_stream_trace_logger(
    stream_trace_state: StreamTraceState | None,
    *,
    request_id: str | None,
    request_log_id: str | None,
    model: str,
    route: ResolvedRoute,
) -> StreamTraceLogger | None:
    """Create a stream trace logger when runtime stream tracing is enabled."""
    state = stream_trace_state or StreamTraceState()
    return state.create_logger(
        request_id=request_id,
        request_log_id=request_log_id,
        model=model,
        source_provider=route.source_provider,
        target_provider=route.target_provider,
        provider_name=route.provider_name,
    )


# ---------------------------------------------------------------------------
# Resource cleanup
# ---------------------------------------------------------------------------


async def close_resources(
    *,
    transport: UpstreamTransport | None = None,
    metadata_store: ProviderMetadataStore | None = None,
    codex_tool_store: CodexToolLocalizationStore | None = None,
) -> None:
    """Close transport and clear metadata store (called on app shutdown)."""
    if transport is not None:
        await transport.close()
    store = metadata_store or _default_metadata_store
    store.clear()
    tools = (
        codex_tool_store if codex_tool_store is not None else _default_codex_tool_store
    )
    tools.clear()


# ---------------------------------------------------------------------------
# Provider metadata store (e.g. Google thought_signature)
# ---------------------------------------------------------------------------
# Bridges provider_metadata across HTTP request boundaries.  Request 1's
# response may contain a ``thought_signature`` that must be injected into
# Request 2's tool result.  Entries are keyed by ``tool_call_id`` and are
# kept alive (``get``, not ``pop``) because clients resend the full
# conversation history on every request.


@dataclass
class _CacheEntry:
    """A single cached provider_metadata entry with creation timestamp."""

    data: dict[str, Any]
    created: float = field(default_factory=time.monotonic)


class ProviderMetadataStore:
    """Stores provider_metadata across request boundaries with TTL and bounds.

    Args:
        ttl: Time-to-live in seconds for each entry.  Defaults to 30 minutes.
        max_size: Maximum number of entries.  Oldest is evicted on overflow.
    """

    def __init__(self, *, ttl: float = 1800.0, max_size: int = 10_000) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._ttl = ttl
        self._max_size = max_size

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, e in self._store.items() if now - e.created > self._ttl]
        for k in expired:
            del self._store[k]

    def _evict_oldest(self) -> None:
        if len(self._store) >= self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k].created)
            del self._store[oldest_key]

    def cache_from_response(self, ir_response: dict[str, Any]) -> None:
        """Extract and cache provider_metadata from tool calls in an IR response."""
        self._evict_expired()
        for choice in ir_response.get("choices", []):
            msg = choice.get("message", {})
            for part in msg.get("content", []):
                if part.get("type") == "tool_call" and "provider_metadata" in part:
                    tool_call_id = part.get("tool_call_id")
                    if tool_call_id:
                        self._evict_oldest()
                        self._store[tool_call_id] = _CacheEntry(
                            data=part["provider_metadata"],
                        )
                        logger.debug(
                            "Cached provider_metadata for tool_call %s", tool_call_id
                        )

    def cache_from_stream_event(self, ir_event: dict[str, Any]) -> None:
        """Cache provider_metadata from a tool_call_start stream event."""
        if (
            ir_event.get("type") == "tool_call_start"
            and "provider_metadata" in ir_event
        ):
            self._evict_expired()
            self._evict_oldest()
            self._store[ir_event["tool_call_id"]] = _CacheEntry(
                data=ir_event["provider_metadata"],
            )

    def inject_into_request(self, ir_request: dict[str, Any]) -> None:
        """Inject cached provider_metadata into tool call parts in an IR request.

        Clients send the full conversation history on every request, so the
        same tool_call_id may appear in multiple requests.  Entries are kept
        alive (not popped) for subsequent turns.
        """
        self._evict_expired()
        logger.debug(
            "inject: store has %d entries: %s",
            len(self._store),
            list(self._store.keys()),
        )
        for msg in ir_request.get("messages", []):
            for part in msg.get("content", []):
                if part.get("type") == "tool_call":
                    tool_call_id = part.get("tool_call_id")
                    if tool_call_id and tool_call_id in self._store:
                        part["provider_metadata"] = self._store[tool_call_id].data

    def clear(self) -> None:
        """Remove all entries."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


class WindowToolSearchStore:
    """Stores loadable Responses tools discovered within one Codex window."""

    def __init__(self, *, ttl: float = 1800.0, max_size: int = 1_000) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._deferred_store: dict[str, _CacheEntry] = {}
        self._ttl = ttl
        self._max_size = max_size

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, e in self._store.items() if now - e.created > self._ttl]
        for k in expired:
            del self._store[k]
        deferred_expired = [
            k for k, e in self._deferred_store.items() if now - e.created > self._ttl
        ]
        for k in deferred_expired:
            del self._deferred_store[k]

    def _evict_oldest(self) -> None:
        if len(self._store) >= self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k].created)
            del self._store[oldest_key]
        if len(self._deferred_store) >= self._max_size:
            oldest_key = min(
                self._deferred_store, key=lambda k: self._deferred_store[k].created
            )
            del self._deferred_store[oldest_key]

    @staticmethod
    def _tool_key(tool: Any) -> str | None:
        if not isinstance(tool, dict):
            return None
        tool_type = tool.get("type")
        tool_name = tool.get("name")
        if tool_type == "namespace" and tool_name:
            return f"namespace:{tool_name}"
        if tool_type == "function" and tool_name:
            return f"function:{tool_name}"
        if tool_name:
            return f"{tool_type or 'tool'}:{tool_name}"
        if tool_type:
            return f"type:{tool_type}"
        return None

    @staticmethod
    def _extract_tool_search_tools(body: dict[str, Any]) -> list[dict[str, Any]]:
        input_items = body.get("input")
        if not isinstance(input_items, list):
            return []
        tools: list[dict[str, Any]] = []
        for item in input_items:
            if not isinstance(item, dict) or item.get("type") != "tool_search_output":
                continue
            item_tools = item.get("tools")
            if not isinstance(item_tools, list):
                continue
            tools.extend(tool for tool in item_tools if isinstance(tool, dict))
        return tools

    @staticmethod
    def _tool_search_calls_by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
        input_items = body.get("input")
        if not isinstance(input_items, list):
            return {}
        calls: dict[str, dict[str, Any]] = {}
        for item in input_items:
            if not isinstance(item, dict) or item.get("type") != "tool_search_call":
                continue
            call_id = item.get("call_id")
            if isinstance(call_id, str) and call_id:
                calls[call_id] = item
        return calls

    @staticmethod
    def _tool_search_query(call: dict[str, Any] | None) -> str:
        if not isinstance(call, dict):
            return ""
        arguments = call.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                return arguments
        if not isinstance(arguments, dict):
            return ""
        query = arguments.get("query")
        return query if isinstance(query, str) else ""

    @staticmethod
    def _tool_search_limit(call: dict[str, Any] | None) -> int:
        if not isinstance(call, dict):
            return 8
        arguments = call.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                return 8
        if not isinstance(arguments, dict):
            return 8
        limit = arguments.get("limit")
        if isinstance(limit, int):
            return max(1, min(limit, 50))
        return 8

    @staticmethod
    def _search_tokens(value: str) -> list[str]:
        return [
            token for token in _TOOL_SEARCH_TOKEN_RE.findall(value.lower()) if token
        ]

    @classmethod
    def _tool_search_text(cls, tool: dict[str, Any]) -> str:
        values: list[str] = []
        for key in ("name", "description"):
            value = tool.get(key)
            if isinstance(value, str):
                values.append(value)
        children = tool.get("tools")
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                for key in ("name", "description"):
                    value = child.get(key)
                    if isinstance(value, str):
                        values.append(value)
                parameters = child.get("parameters")
                if isinstance(parameters, dict):
                    values.extend(cls._schema_search_text(parameters))
        return " ".join(values)

    @classmethod
    def _schema_search_text(cls, schema: dict[str, Any]) -> list[str]:
        values: list[str] = []
        title = schema.get("title")
        description = schema.get("description")
        if isinstance(title, str):
            values.append(title)
        if isinstance(description, str):
            values.append(description)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for prop_name, prop_schema in properties.items():
                if isinstance(prop_name, str):
                    values.append(prop_name)
                if isinstance(prop_schema, dict):
                    values.extend(cls._schema_search_text(prop_schema))
        items = schema.get("items")
        if isinstance(items, dict):
            values.extend(cls._schema_search_text(items))
        elif isinstance(items, list):
            for item_schema in items:
                if isinstance(item_schema, dict):
                    values.extend(cls._schema_search_text(item_schema))
        for keyword in ("anyOf", "oneOf", "allOf"):
            variants = schema.get(keyword)
            if not isinstance(variants, list):
                continue
            for variant in variants:
                if isinstance(variant, dict):
                    values.extend(cls._schema_search_text(variant))
        return values

    @classmethod
    def _score_tool_for_query(cls, tool: dict[str, Any], query: str) -> int:
        query_tokens = set(cls._search_tokens(query))
        if not query_tokens:
            return 0
        tool_text = cls._tool_search_text(tool)
        tool_tokens = set(cls._search_tokens(tool_text))
        if not tool_tokens:
            return 0
        score = 0
        namespace = tool.get("name")
        namespace_tokens = (
            set(cls._search_tokens(namespace)) if isinstance(namespace, str) else set()
        )
        for token in query_tokens:
            if token in namespace_tokens:
                score += 8
            elif token in tool_tokens:
                score += 3
            elif any(tool_token.startswith(token) for tool_token in tool_tokens):
                score += 1
        return score

    @classmethod
    def _score_namespace_child_for_query(
        cls, namespace: dict[str, Any], child: dict[str, Any], query: str
    ) -> int:
        query_tokens = set(cls._expanded_query_tokens(query))
        if not query_tokens:
            return 0

        namespace_text = " ".join(
            value
            for key in ("name", "description")
            if isinstance((value := namespace.get(key)), str)
        )
        child_name = child.get("name") if isinstance(child.get("name"), str) else ""
        child_description = (
            child.get("description")
            if isinstance(child.get("description"), str)
            else ""
        )
        schema_text = " ".join(
            cls._schema_search_text(child.get("parameters", {}))
            if isinstance(child.get("parameters"), dict)
            else []
        )

        namespace_tokens = set(cls._search_tokens(namespace_text))
        child_name_tokens = set(cls._search_tokens(child_name.replace("_", " ")))
        description_tokens = set(cls._search_tokens(child_description))
        schema_tokens = set(cls._search_tokens(schema_text))
        all_tokens = (
            namespace_tokens | child_name_tokens | description_tokens | schema_tokens
        )
        if not all_tokens:
            return 0

        score = 0
        for token in query_tokens:
            if token in child_name_tokens:
                score += 12
            elif any(tool_token.startswith(token) for tool_token in child_name_tokens):
                score += 5
            if token in description_tokens:
                score += 5
            if token in schema_tokens:
                score += 3
            if token in namespace_tokens:
                score += 3

        normalized_query = " ".join(cls._search_tokens(query))
        normalized_description = " ".join(cls._search_tokens(child_description))
        if normalized_query and normalized_query in normalized_description:
            score += 10
        return score

    @classmethod
    def _expanded_query_tokens(cls, query: str) -> list[str]:
        tokens = cls._search_tokens(query)
        expanded = list(tokens)
        token_set = set(tokens)
        if {"pull", "request"}.issubset(token_set):
            expanded.extend(["pr", "prs"])
        return expanded

    def _remember_tools(
        self,
        store: dict[str, _CacheEntry],
        window_id: str,
        tools: list[dict[str, Any]],
    ) -> None:
        if not tools:
            return
        self._evict_expired()
        window_tools = dict(store.get(window_id, _CacheEntry(data={})).data)
        for tool in tools:
            key = self._tool_key(tool)
            if not key:
                continue
            window_tools[key] = self._merge_loadable_tool(window_tools.get(key), tool)
        self._evict_oldest()
        store[window_id] = _CacheEntry(data=window_tools)

    @staticmethod
    def _merge_loadable_tool(existing: Any, incoming: dict[str, Any]) -> dict[str, Any]:
        if (
            not isinstance(existing, dict)
            or existing.get("type") != "namespace"
            or incoming.get("type") != "namespace"
        ):
            return copy.deepcopy(incoming)

        merged = copy.deepcopy(existing)
        existing_children = merged.setdefault("tools", [])
        incoming_children = incoming.get("tools")
        if not isinstance(existing_children, list) or not isinstance(
            incoming_children, list
        ):
            return copy.deepcopy(incoming)

        existing_names = {
            child.get("name")
            for child in existing_children
            if isinstance(child, dict) and isinstance(child.get("name"), str)
        }
        for child in incoming_children:
            if not isinstance(child, dict):
                continue
            child_name = child.get("name")
            if isinstance(child_name, str) and child_name in existing_names:
                continue
            existing_children.append(copy.deepcopy(child))
            if isinstance(child_name, str):
                existing_names.add(child_name)
        return merged

    def remember_deferred_tools(
        self, window_id: str | None, tools: list[dict[str, Any]]
    ) -> None:
        """Remember Responses namespace tools hidden from Chat for this window."""
        if not window_id:
            return
        self._remember_tools(self._deferred_store, window_id, tools)

    def remember_from_request(
        self, window_id: str | None, body: dict[str, Any]
    ) -> None:
        """Remember loadable tools from tool_search_output items in this request."""
        if not window_id:
            return
        discovered_tools = self._extract_tool_search_tools(body)
        if not discovered_tools:
            return
        self._remember_tools(self._store, window_id, discovered_tools)

    def enrich_tool_search_outputs(
        self, window_id: str | None, body: dict[str, Any]
    ) -> None:
        """Fill empty tool_search_output tools from Rosetta-hidden namespaces."""
        if not window_id:
            return
        input_items = body.get("input")
        if not isinstance(input_items, list):
            return
        self._evict_expired()
        entry = self._deferred_store.get(window_id)
        if entry is None or not isinstance(entry.data, dict) or not entry.data:
            return

        calls_by_id = self._tool_search_calls_by_id(body)
        deferred_tools = [
            tool for tool in entry.data.values() if isinstance(tool, dict)
        ]
        if not deferred_tools:
            return

        for item in input_items:
            if not isinstance(item, dict) or item.get("type") != "tool_search_output":
                continue
            tools = item.get("tools")
            if not isinstance(tools, list) or tools:
                continue
            call = calls_by_id.get(item.get("call_id"))
            query = self._tool_search_query(call)
            limit = self._tool_search_limit(call)
            matches = self._match_deferred_tools(deferred_tools, query, limit)
            if not matches:
                continue
            item["tools"] = [copy.deepcopy(tool) for tool in matches]

    @classmethod
    def _match_deferred_tools(
        cls, deferred_tools: list[dict[str, Any]], query: str, limit: int
    ) -> list[dict[str, Any]]:
        scored: list[tuple[int, int, int, dict[str, Any]]] = []
        for tool_index, tool in enumerate(deferred_tools):
            if tool.get("type") == "namespace" and isinstance(tool.get("tools"), list):
                for child_index, child in enumerate(tool["tools"]):
                    if not isinstance(child, dict):
                        continue
                    if child.get("type", "function") != "function":
                        continue
                    score = cls._score_namespace_child_for_query(tool, child, query)
                    if score <= 0:
                        continue
                    namespace_match = {
                        "type": "namespace",
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "tools": [copy.deepcopy(child)],
                    }
                    scored.append((score, tool_index, child_index, namespace_match))
                continue

            score = cls._score_tool_for_query(tool, query)
            if score <= 0:
                continue
            scored.append((score, tool_index, -1, copy.deepcopy(tool)))

        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return cls._coalesce_loadable_tools([tool for _, _, _, tool in scored[:limit]])

    @classmethod
    def _coalesce_loadable_tools(
        cls, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        coalesced: list[dict[str, Any]] = []
        namespace_indexes: dict[str, int] = {}
        for tool in tools:
            if tool.get("type") != "namespace":
                coalesced.append(copy.deepcopy(tool))
                continue
            namespace_name = tool.get("name")
            if not isinstance(namespace_name, str):
                coalesced.append(copy.deepcopy(tool))
                continue
            if namespace_name not in namespace_indexes:
                namespace_indexes[namespace_name] = len(coalesced)
                coalesced.append(copy.deepcopy(tool))
                continue
            existing = coalesced[namespace_indexes[namespace_name]]
            coalesced[namespace_indexes[namespace_name]] = cls._merge_loadable_tool(
                existing, tool
            )
        return coalesced

    def inject_into_request(
        self,
        window_id: str | None,
        ir_request: dict[str, Any],
        pipeline: ConversionPipeline,
        *,
        optimize_tool_descriptions: bool = True,
    ) -> None:
        """Append remembered loadable tools to an IR request for the same window."""
        if not window_id:
            return
        self._evict_expired()
        entry = self._store.get(window_id)
        if entry is None:
            return
        tools_by_key = entry.data
        if not isinstance(tools_by_key, dict) or not tools_by_key:
            return

        response_converter = pipeline._source_converter
        converted: list[Any] = []
        for tool in tools_by_key.values():
            converted_tool = response_converter.tool_ops.p_tool_definition_to_ir(tool)
            if isinstance(converted_tool, list):
                converted.extend(converted_tool)
            else:
                converted.append(converted_tool)
        if not converted:
            return

        existing_tools = ir_request.setdefault("tools", [])
        existing_names = {
            tool.get("name")
            for tool in existing_tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }
        added: list[Any] = []
        for tool in converted:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not isinstance(name, str) or name in existing_names:
                continue
            if optimize_tool_descriptions:
                tool = _patch_github_namespace_tool_schema_for_chat(tool)
            existing_tools.append(tool)
            existing_names.add(name)
            added.append(tool)

        if added and hasattr(response_converter, "_store_namespace_tool_map"):
            response_converter._store_namespace_tool_map(added, pipeline.context)
        if added and hasattr(response_converter, "_store_native_tool_type_map"):
            response_converter._store_native_tool_type_map(added, pipeline.context)

    def clear(self) -> None:
        self._store.clear()
        self._deferred_store.clear()

    def __len__(self) -> int:
        return len(self._store)


_default_metadata_store = ProviderMetadataStore()
_default_codex_tool_store = CodexToolLocalizationStore()
_default_window_tool_search_store = WindowToolSearchStore()


def _prepare_window_tool_search_request(
    *,
    route: ResolvedRoute,
    codex_window_id: str | None,
    body: dict[str, Any],
    window_tools: WindowToolSearchStore,
) -> bool:
    """Update per-window loadable-tool state and return whether injection applies."""
    if not (_uses_responses_chat_bridge(route) and codex_window_id):
        return False
    deferred_tools = _defer_responses_namespace_tools_for_chat(
        body,
        optimize_tool_descriptions=enable_tool_description_optimization(
            route.tool_adaptation
        ),
    )
    window_tools.remember_deferred_tools(codex_window_id, deferred_tools)
    window_tools.enrich_tool_search_outputs(codex_window_id, body)
    window_tools.remember_from_request(codex_window_id, body)
    return True


# ---------------------------------------------------------------------------
# Core proxy handlers
# ---------------------------------------------------------------------------


async def handle_non_streaming(
    route: ResolvedRoute,
    provider_info: ProviderInfo,
    body: dict[str, Any],
    *,
    transport: UpstreamTransport,
    metadata_store: ProviderMetadataStore | None = None,
    codex_tool_store: CodexToolLocalizationStore | None = None,
    extra_headers: dict[str, str] | None = None,
    persistence: Any | None = None,
    tool_cache_session_id: str | None = None,
    codex_window_id: str | None = None,
    window_tool_search_store: WindowToolSearchStore | None = None,
) -> tuple[Response, dict[str, Any]]:
    """Non-streaming proxy: convert -> forward -> convert back -> respond.

    Returns:
        A ``(response, profile)`` tuple.  The profile dict contains
        per-phase timing data merged from the conversion pipeline and
        gateway-level measurements (upstream latency).
    """
    store = metadata_store or _default_metadata_store
    tool_store = (
        codex_tool_store if codex_tool_store is not None else _default_codex_tool_store
    )
    window_tools = (
        window_tool_search_store
        if window_tool_search_store is not None
        else _default_window_tool_search_store
    )
    persistent_mappings: list[LocalizedToolMapping] = []
    used_mapping_call_ids: set[str] = set()
    profile: dict[str, Any] = {}
    # model was already injected into body by app.py
    model = body.get("model", "")
    body = _apply_tool_adaptation(body, route)
    source_tool_capabilities = NativeToolCapabilities.from_chat_tools(
        _flatten_responses_tools(body)
    )
    use_window_tool_search = _prepare_window_tool_search_request(
        route=route,
        codex_window_id=codex_window_id,
        body=body,
        window_tools=window_tools,
    )

    if _is_openai_responses_direct(route):
        log_original_request(body)
        t_upstream = time.perf_counter()
        try:
            resp = await transport.send_request(
                provider_info,
                route.target_provider,
                body,
                model,
                extra_headers=extra_headers,
            )
        except UpstreamConnectionError as exc:
            profile["upstream_ms"] = round((time.perf_counter() - t_upstream) * 1000, 2)
            return (
                error_response_for_source(
                    route.source_provider, 502, f"Upstream request failed: {exc}"
                ),
                profile,
            )
        profile["upstream_ms"] = round((time.perf_counter() - t_upstream) * 1000, 2)
        profile["passthrough"] = True

        if resp.is_error:
            log_upstream_error(
                resp.status_code,
                resp.error_text,
                endpoint=str(route.target_provider),
            )
            dump_error(
                persistence,
                request_body=body,
                response_text=resp.error_text,
                converted_body=body,
                model=model,
                source_provider=route.source_provider,
                target_provider=route.target_provider,
                provider_name=route.provider_name,
                status_code=resp.status_code,
                error_phase="upstream",
                upstream_url=str(provider_info.base_url),
            )
            return (
                Response(
                    body=resp.raw_content,
                    status_code=resp.status_code,
                    content_type="application/json",
                ),
                profile,
            )

        if resp.body is not None:
            log_response(resp.body, label="UPSTREAM RESPONSE")
        return (
            Response(
                body=resp.raw_content,
                status_code=resp.status_code,
                content_type="application/json",
            ),
            profile,
        )

    pipeline = ConversionPipeline(
        route.source_provider,
        route.target_provider,
        route.shim_name,
        upstream_model=model,
        model_capabilities=route.model_capabilities,
        reasoning_mapping=route.reasoning_mapping,
        provider_name=route.provider_name,
        conversion_options={
            "enable_tool_description_optimization": (
                enable_tool_description_optimization(route.tool_adaptation)
            )
        },
    )

    # Phase 1+2: Source → IR → Target
    def _on_request_ir_ready(ir_request: dict[str, Any]) -> None:
        store.inject_into_request(ir_request)
        if use_window_tool_search:
            window_tools.inject_into_request(
                codex_window_id,
                ir_request,
                pipeline,
                optimize_tool_descriptions=enable_tool_description_optimization(
                    route.tool_adaptation
                ),
            )

    try:
        target_body = pipeline.convert_request(body, on_ir_ready=_on_request_ir_ready)
    except ConversionError as exc:
        return error_response_for_source(route.source_provider, 400, str(exc)), profile
    if should_localize_code_tools(route):
        persistent_mappings = _load_persistent_tool_mappings(
            persistence,
            session_id=tool_cache_session_id,
        )
    target_body = _apply_converted_request_tool_adaptation(
        target_body,
        route,
        codex_tool_store=tool_store,
        persistent_mappings=persistent_mappings,
        used_mapping_call_ids=used_mapping_call_ids,
        capabilities=source_tool_capabilities,
    )
    tool_capabilities = _pop_tool_localization_capabilities(target_body)
    read_cache = _pop_read_output_cache(target_body)

    profile.update(pipeline.profile)

    log_original_request(pipeline.ir_request)
    if pipeline.warnings:
        logger.warning("Conversion warnings: %s", pipeline.warnings)
    log_converted_request(target_body)

    # Phase 3: Forward to upstream via transport
    t_upstream = time.perf_counter()
    try:
        resp = await transport.send_request(
            provider_info,
            route.target_provider,
            target_body,
            model,
            extra_headers=extra_headers,
        )
    except UpstreamConnectionError as exc:
        profile["upstream_ms"] = round((time.perf_counter() - t_upstream) * 1000, 2)
        return (
            error_response_for_source(
                route.source_provider, 502, f"Upstream request failed: {exc}"
            ),
            profile,
        )
    _delete_unused_persistent_tool_mappings(
        persistence,
        session_id=tool_cache_session_id,
        loaded_mappings=persistent_mappings,
        used_call_ids=used_mapping_call_ids,
    )
    profile["upstream_ms"] = round((time.perf_counter() - t_upstream) * 1000, 2)

    if resp.is_error:
        log_upstream_error(
            resp.status_code,
            resp.error_text,
            endpoint=str(route.target_provider),
        )
        dump_error(
            persistence,
            request_body=body,
            response_text=resp.error_text,
            converted_body=target_body,
            model=model,
            source_provider=route.source_provider,
            target_provider=route.target_provider,
            provider_name=route.provider_name,
            status_code=resp.status_code,
            error_phase="upstream",
            upstream_url=str(provider_info.base_url),
        )
        return (
            Response(
                body=resp.raw_content,
                status_code=resp.status_code,
                content_type="application/json",
            ),
            profile,
        )

    # Phase 4: Target response → Source response
    assert resp.body is not None
    log_response(resp.body, label="UPSTREAM RESPONSE")

    def _on_response_ir_ready(ir_response: dict[str, Any]) -> None:
        _translate_and_persist_localized_response_tools(
            ir_response,
            route,
            tool_store=tool_store,
            persistence=persistence,
            session_id=tool_cache_session_id,
            capabilities=tool_capabilities,
            read_cache=read_cache,
        )
        store.cache_from_response(ir_response)

    try:
        source_response = pipeline.convert_response(
            resp.body, on_ir_ready=_on_response_ir_ready
        )
    except ConversionError as exc:
        profile.update(pipeline.profile)
        return error_response_for_source(route.source_provider, 502, str(exc)), profile

    # Merge response-phase timings from pipeline
    profile.update(pipeline.profile)
    return JSONResponse(source_response), profile


async def _stream_event_generator(
    *,
    source_provider: ProviderType,
    stream: Any,
    processor: Any,
    model: str,
    format_sse: Any,
    event_buffer: ResponsesPhaseBuffer | None = None,
    entry_id: str | None = None,
    request_log: Any | None = None,
    trace: StreamTraceLogger | None = None,
) -> AsyncIterator[str]:
    """Stream SSE events from an already-opened upstream stream.

    The caller (``handle_streaming``) is responsible for opening the upstream
    connection and checking for immediate errors *before* constructing the
    ``StreamingResponse``.  This ensures the HTTP status code sent to the
    client reflects the upstream status (e.g. 400 for token-limit errors)
    rather than always being 200.
    """
    chunk_count = 0
    t0 = time.monotonic()
    stream_error: str | None = None
    ttfb_ms: float | None = None
    t_stream_open = time.perf_counter()

    try:
        async with stream:
            async for chunk in stream:
                if chunk_count == 0:
                    ttfb_ms = round((time.perf_counter() - t_stream_open) * 1000, 2)
                chunk_count += 1
                if trace is not None:
                    trace.log("upstream_chunk", chunk, chunk_index=chunk_count)
                for source_event in processor.process_chunk(chunk):
                    for sse_event in _format_source_event_sse(
                        source_event,
                        event_buffer=event_buffer,
                        format_sse=format_sse,
                        trace=trace,
                        chunk_count=chunk_count,
                    ):
                        yield sse_event

        finalize_stream = getattr(processor, "finalize_stream", None)
        if finalize_stream is not None:
            for source_event in finalize_stream():
                for sse_event in _format_source_event_sse(
                    source_event,
                    event_buffer=event_buffer,
                    format_sse=format_sse,
                    trace=trace,
                    chunk_count=chunk_count,
                ):
                    yield sse_event

        if event_buffer is not None:
            for sse_event in _format_buffered_events_sse(
                event_buffer.flush(),
                format_sse=format_sse,
                trace=trace,
                chunk_count=chunk_count,
            ):
                yield sse_event

        if source_provider == "openai_chat":
            done_event = format_sse_done()
            if trace is not None:
                trace.log("downstream_sse_done", done_event, chunk_index=chunk_count)
            yield done_event

        log_stream_summary(
            model=model,
            duration_s=time.monotonic() - t0,
            chunk_count=chunk_count,
        )
    except Exception as exc:
        stream_error = str(exc)
        raise
    finally:
        _finalize_stream_profile(
            entry_id=entry_id,
            request_log=request_log,
            trace=trace,
            t0=t0,
            chunk_count=chunk_count,
            stream_error=stream_error,
            ttfb_ms=ttfb_ms,
        )


async def _web_search_stream_event_generator(  # noqa: C901
    *,
    source_provider: ProviderType,
    initial_stream: Any,
    processor_factory: Any,
    model: str,
    format_sse: Any,
    transport: UpstreamTransport,
    provider_info: ProviderInfo,
    target_provider: ProviderType,
    target_body: dict[str, Any],
    web_search_runtime: WebSearchRuntime,
    extra_headers: dict[str, str] | None = None,
    event_buffer: ResponsesPhaseBuffer | None = None,
    entry_id: str | None = None,
    request_log: Any | None = None,
    trace: StreamTraceLogger | None = None,
    max_rounds: int = 5,
) -> AsyncIterator[str]:
    """Stream Chat upstream output, executing synthetic web_search calls inline."""
    chunk_count = 0
    t0 = time.monotonic()
    stream_error: str | None = None
    ttfb_ms: float | None = None
    t_stream_open = time.perf_counter()
    controller = WebSearchStreamController()
    current_stream = initial_stream
    current_body = target_body
    round_index = 0

    try:
        while True:
            processor = processor_factory()
            if trace is not None and round_index > 0:
                trace.log(
                    "web_search_target_request",
                    {"round": round_index, "body": current_body},
                    chunk_index=chunk_count,
                )

            async with current_stream:
                async for chunk in current_stream:
                    if chunk_count == 0:
                        ttfb_ms = round(
                            (time.perf_counter() - t_stream_open) * 1000,
                            2,
                        )
                    chunk_count += 1
                    if trace is not None:
                        stage = (
                            "upstream_chunk"
                            if round_index == 0
                            else "web_search_upstream_chunk"
                        )
                        trace.log(stage, chunk, chunk_index=chunk_count)
                    for source_event in processor.process_chunk(chunk):
                        for sse_event in _format_web_search_source_event_sse(
                            source_event,
                            controller=controller,
                            round_index=round_index,
                            event_buffer=event_buffer,
                            format_sse=format_sse,
                            trace=trace,
                            chunk_count=chunk_count,
                        ):
                            yield sse_event

            finalize_stream = getattr(processor, "finalize_stream", None)
            if finalize_stream is not None:
                for source_event in finalize_stream():
                    for sse_event in _format_web_search_source_event_sse(
                        source_event,
                        controller=controller,
                        round_index=round_index,
                        event_buffer=event_buffer,
                        format_sse=format_sse,
                        trace=trace,
                        chunk_count=chunk_count,
                    ):
                        yield sse_event

            calls = controller.pop_pending_calls()
            if not calls:
                break
            if round_index >= max_rounds:
                raise RuntimeError("web_search loop exceeded maximum rounds")

            for call in calls:
                if trace is not None:
                    trace.log(
                        "web_search_request",
                        {
                            "round": round_index,
                            "query": call.query,
                            "call_id": call.call_id,
                            "settings": web_search_runtime.settings,
                        },
                        chunk_index=chunk_count,
                    )
            results = await web_search_runtime.execute_many(calls)
            if trace is not None:
                trace.log(
                    "web_search_response",
                    [web_search_trace_summary(result) for result in results],
                    chunk_index=chunk_count,
                )
            for source_event in controller.complete_search_results(results):
                for sse_event in _format_source_event_sse(
                    source_event,
                    event_buffer=event_buffer,
                    format_sse=format_sse,
                    trace=trace,
                    chunk_count=chunk_count,
                ):
                    yield sse_event

            current_body = web_search_runtime.append_tool_results(current_body, results)
            current_stream = await transport.send_streaming(
                provider_info,
                target_provider,
                current_body,
                model,
                extra_headers=extra_headers,
            )
            if current_stream.is_error:
                error_text = await current_stream.read_error()
                await current_stream.close()
                raise RuntimeError(
                    f"Upstream request after web_search failed: {error_text}"
                )
            round_index += 1

        if event_buffer is not None:
            for sse_event in _format_buffered_events_sse(
                event_buffer.flush(),
                format_sse=format_sse,
                trace=trace,
                chunk_count=chunk_count,
            ):
                yield sse_event

        if source_provider == "openai_chat":
            done_event = format_sse_done()
            if trace is not None:
                trace.log("downstream_sse_done", done_event, chunk_index=chunk_count)
            yield done_event

        log_stream_summary(
            model=model,
            duration_s=time.monotonic() - t0,
            chunk_count=chunk_count,
        )
    except Exception as exc:
        stream_error = str(exc)
        raise
    finally:
        _finalize_stream_profile(
            entry_id=entry_id,
            request_log=request_log,
            trace=trace,
            t0=t0,
            chunk_count=chunk_count,
            stream_error=stream_error,
            ttfb_ms=ttfb_ms,
        )


def _format_web_search_source_event_sse(
    source_event: dict[str, Any],
    *,
    controller: WebSearchStreamController,
    round_index: int,
    event_buffer: ResponsesPhaseBuffer | None,
    format_sse: Any,
    trace: StreamTraceLogger | None,
    chunk_count: int,
) -> list[str]:
    if trace is not None:
        trace.log("source_event", source_event, chunk_index=chunk_count)
    events = controller.process_source_event(source_event, round_index=round_index)
    return _format_buffered_events_sse(
        [
            buffered
            for event in events
            for buffered in (
                event_buffer.process(event) if event_buffer is not None else [event]
            )
        ],
        format_sse=format_sse,
        trace=trace,
        chunk_count=chunk_count,
    )


def _format_source_event_sse(
    source_event: dict[str, Any],
    *,
    event_buffer: ResponsesPhaseBuffer | None,
    format_sse: Any,
    trace: StreamTraceLogger | None,
    chunk_count: int,
) -> list[str]:
    if trace is not None:
        trace.log("source_event", source_event, chunk_index=chunk_count)
    buffered_events = (
        event_buffer.process(source_event)
        if event_buffer is not None
        else [source_event]
    )
    return _format_buffered_events_sse(
        buffered_events,
        format_sse=format_sse,
        trace=trace,
        chunk_count=chunk_count,
    )


def _format_buffered_events_sse(
    events: list[dict[str, Any]],
    *,
    format_sse: Any,
    trace: StreamTraceLogger | None,
    chunk_count: int,
) -> list[str]:
    sse_events: list[str] = []
    for event in events:
        sse_event = format_sse(event)
        if trace is not None:
            trace.log("downstream_sse", sse_event, chunk_index=chunk_count)
        sse_events.append(sse_event)
    return sse_events


def _converted_stream_response_generator(
    *,
    source_provider: ProviderType,
    stream: Any,
    processor: Any,
    processor_factory: Any,
    model: str,
    format_sse: Any,
    event_buffer: ResponsesPhaseBuffer | None,
    entry_id: str | None,
    request_log: Any | None,
    trace: StreamTraceLogger | None,
    web_search_runtime: WebSearchRuntime | None,
    transport: UpstreamTransport,
    provider_info: ProviderInfo,
    target_provider: ProviderType,
    target_body: dict[str, Any],
    extra_headers: dict[str, str] | None,
) -> AsyncIterator[str]:
    if web_search_runtime is None:
        return _stream_event_generator(
            source_provider=source_provider,
            stream=stream,
            processor=processor,
            model=model,
            format_sse=format_sse,
            event_buffer=event_buffer,
            entry_id=entry_id,
            request_log=request_log,
            trace=trace,
        )
    return _web_search_stream_event_generator(
        source_provider=source_provider,
        initial_stream=stream,
        processor_factory=processor_factory,
        model=model,
        format_sse=format_sse,
        transport=transport,
        provider_info=provider_info,
        target_provider=target_provider,
        target_body=target_body,
        web_search_runtime=web_search_runtime,
        extra_headers=extra_headers,
        event_buffer=event_buffer,
        entry_id=entry_id,
        request_log=request_log,
        trace=trace,
    )


def _prepare_web_search_runtime_and_body(
    *,
    route: ResolvedRoute,
    body: dict[str, Any],
    web_search_config: dict[str, Any] | None,
    web_search_client: TavilySearchClient | None,
) -> tuple[dict[str, Any], WebSearchRuntime | None]:
    if not _uses_responses_chat_bridge(route):
        return body, None

    runtime = build_web_search_runtime(
        body,
        web_search_config,
        client=web_search_client,
    )
    if runtime is None:
        return strip_responses_web_search_tools(body), None
    return body, runtime


def _finalize_stream_profile(
    *,
    entry_id: str | None,
    request_log: Any | None,
    trace: StreamTraceLogger | None,
    t0: float,
    chunk_count: int,
    stream_error: str | None,
    ttfb_ms: float | None,
) -> None:
    if entry_id and request_log is not None:
        stream_profile: dict[str, Any] = {
            "stream_duration_ms": round((time.monotonic() - t0) * 1000, 2),
            "stream_chunks": chunk_count,
            "stream_complete": stream_error is None,
        }
        if ttfb_ms is not None:
            stream_profile["stream_ttfb_ms"] = ttfb_ms
        if stream_error is not None:
            stream_profile["stream_error"] = stream_error[:500]
        try:
            request_log.update_profile(entry_id, stream_profile)
        except Exception:
            logger.debug("Failed to write stream profile for %s", entry_id)
    if trace is not None:
        trace.log(
            "stream_complete",
            {
                "chunk_count": chunk_count,
                "stream_complete": stream_error is None,
                "stream_error": stream_error,
                "ttfb_ms": ttfb_ms,
            },
        )


async def _raw_stream_event_generator(
    *,
    stream: Any,
    model: str,
    entry_id: str | None = None,
    request_log: Any | None = None,
    trace: StreamTraceLogger | None = None,
) -> AsyncIterator[bytes]:
    """Pass raw upstream stream bytes to the client without event conversion."""
    chunk_count = 0
    t0 = time.monotonic()
    stream_error: str | None = None
    ttfb_ms: float | None = None
    t_stream_open = time.perf_counter()

    try:
        async with stream:
            raw_iter = stream.aiter_raw_bytes()
            if raw_iter is None:
                raise RuntimeError("Upstream stream does not support raw passthrough")
            async for chunk in raw_iter:
                if chunk_count == 0:
                    ttfb_ms = round((time.perf_counter() - t_stream_open) * 1000, 2)
                chunk_count += 1
                if trace is not None:
                    trace.log("raw_passthrough_chunk", chunk, chunk_index=chunk_count)
                yield chunk

        log_stream_summary(
            model=model,
            duration_s=time.monotonic() - t0,
            chunk_count=chunk_count,
        )
    except Exception as exc:
        stream_error = str(exc)
        raise
    finally:
        if entry_id and request_log is not None:
            stream_profile: dict[str, Any] = {
                "stream_duration_ms": round((time.monotonic() - t0) * 1000, 2),
                "stream_chunks": chunk_count,
                "stream_complete": stream_error is None,
                "stream_passthrough": True,
            }
            if ttfb_ms is not None:
                stream_profile["stream_ttfb_ms"] = ttfb_ms
            if stream_error is not None:
                stream_profile["stream_error"] = stream_error[:500]
            try:
                request_log.update_profile(entry_id, stream_profile)
            except Exception:
                logger.debug("Failed to write stream profile for %s", entry_id)
        if trace is not None:
            trace.log(
                "stream_complete",
                {
                    "chunk_count": chunk_count,
                    "stream_complete": stream_error is None,
                    "stream_error": stream_error,
                    "ttfb_ms": ttfb_ms,
                    "passthrough": True,
                },
            )


async def _handle_direct_responses_streaming(
    route: ResolvedRoute,
    provider_info: ProviderInfo,
    body: dict[str, Any],
    *,
    transport: UpstreamTransport,
    model: str,
    extra_headers: dict[str, str] | None,
    entry_id: str | None,
    request_log: Any | None,
    persistence: Any | None,
    stream_trace_state: StreamTraceState | None,
) -> tuple[Response | StreamingResponse, dict[str, Any]]:
    """Handle same-protocol Responses streaming passthrough."""
    profile: dict[str, Any] = {}
    log_original_request(body)
    t_connect = time.perf_counter()
    try:
        stream = await transport.send_streaming(
            provider_info,
            route.target_provider,
            body,
            model,
            extra_headers=extra_headers,
        )
    except UpstreamConnectionError as exc:
        profile["stream_connect_ms"] = round(
            (time.perf_counter() - t_connect) * 1000, 2
        )
        error_msg = str(exc)
        dump_error(
            persistence,
            request_body=body,
            response_text=error_msg,
            converted_body=body,
            model=model,
            source_provider=route.source_provider,
            target_provider=route.target_provider,
            provider_name=route.provider_name,
            status_code=502,
            error_phase="stream_header",
            upstream_url=str(provider_info.base_url),
            request_log_id=entry_id,
        )
        return (
            error_response_for_source(
                route.source_provider, 502, f"Upstream request failed: {exc}"
            ),
            profile,
        )

    profile["stream_connect_ms"] = round((time.perf_counter() - t_connect) * 1000, 2)
    profile["passthrough"] = True

    if stream.is_error:
        error_text = await stream.read_error()
        await stream.close()
        log_upstream_error(
            stream.status_code,
            error_text,
            endpoint=str(route.target_provider),
            is_streaming=True,
        )
        dump_error(
            persistence,
            request_body=body,
            response_text=error_text,
            converted_body=body,
            model=model,
            source_provider=route.source_provider,
            target_provider=route.target_provider,
            provider_name=route.provider_name,
            status_code=stream.status_code,
            error_phase="stream_header",
            upstream_url=str(provider_info.base_url),
            request_log_id=entry_id,
        )
        return (
            Response(
                body=error_text.encode("utf-8")
                if isinstance(error_text, str)
                else error_text,
                status_code=stream.status_code,
                content_type="application/json",
            ),
            profile,
        )

    request_id = extra_headers.get("x-request-id") if extra_headers else None
    trace = _create_stream_trace_logger(
        stream_trace_state,
        request_id=request_id,
        request_log_id=entry_id,
        model=model,
        route=route,
    )
    if trace is not None:
        trace.log(
            "stream_start",
            {
                "model": model,
                "source_provider": route.source_provider,
                "target_provider": route.target_provider,
                "provider_name": route.provider_name,
                "entry_id": entry_id,
                "passthrough": True,
            },
        )
        trace.log("raw_passthrough_request", body)

    return (
        StreamingResponse(
            _raw_stream_event_generator(
                stream=stream,
                model=model,
                entry_id=entry_id,
                request_log=request_log,
                trace=trace,
            ),
            content_type="text/event-stream",
        ),
        profile,
    )


async def handle_streaming(
    route: ResolvedRoute,
    provider_info: ProviderInfo,
    body: dict[str, Any],
    *,
    transport: UpstreamTransport,
    metadata_store: ProviderMetadataStore | None = None,
    codex_tool_store: CodexToolLocalizationStore | None = None,
    extra_headers: dict[str, str] | None = None,
    entry_id: str | None = None,
    request_log: Any | None = None,
    persistence: Any | None = None,
    tool_cache_session_id: str | None = None,
    codex_window_id: str | None = None,
    window_tool_search_store: WindowToolSearchStore | None = None,
    stream_trace_state: StreamTraceState | None = None,
    web_search_config: dict[str, Any] | None = None,
    web_search_client: TavilySearchClient | None = None,
) -> tuple[Response | StreamingResponse, dict[str, Any]]:
    """Streaming proxy: convert -> forward -> stream-convert back -> SSE.

    Opens the upstream connection *before* constructing the
    ``StreamingResponse`` so that immediate errors (4xx/5xx from the
    upstream) are returned with the correct HTTP status code instead of
    being buried inside an SSE event on an HTTP 200 response.

    Returns:
        A ``(response, profile)`` tuple.  The profile dict contains
        request-phase timing data.  Stream-phase metrics (TTFB,
        duration, chunks) are written back to the request log entry
        after the stream completes.
    """
    store = metadata_store or _default_metadata_store
    tool_store = (
        codex_tool_store if codex_tool_store is not None else _default_codex_tool_store
    )
    window_tools = (
        window_tool_search_store
        if window_tool_search_store is not None
        else _default_window_tool_search_store
    )
    persistent_mappings: list[LocalizedToolMapping] = []
    used_mapping_call_ids: set[str] = set()
    profile: dict[str, Any] = {}
    # model was already injected into body by app.py
    model = body.get("model", "")
    body = _apply_tool_adaptation(body, route)
    body, web_search_runtime = _prepare_web_search_runtime_and_body(
        route=route,
        body=body,
        web_search_config=web_search_config,
        web_search_client=web_search_client,
    )
    source_tool_capabilities = NativeToolCapabilities.from_chat_tools(
        _flatten_responses_tools(body)
    )
    use_window_tool_search = _prepare_window_tool_search_request(
        route=route,
        codex_window_id=codex_window_id,
        body=body,
        window_tools=window_tools,
    )

    if _is_openai_responses_direct(route):
        return await _handle_direct_responses_streaming(
            route,
            provider_info,
            body,
            transport=transport,
            model=model,
            extra_headers=extra_headers,
            entry_id=entry_id,
            request_log=request_log,
            persistence=persistence,
            stream_trace_state=stream_trace_state,
        )

    pipeline = ConversionPipeline(
        route.source_provider,
        route.target_provider,
        route.shim_name,
        upstream_model=model,
        model_capabilities=route.model_capabilities,
        reasoning_mapping=route.reasoning_mapping,
        provider_name=route.provider_name,
        conversion_options={
            "enable_tool_description_optimization": (
                enable_tool_description_optimization(route.tool_adaptation)
            )
        },
    )

    # Phase 1+2: Source → IR → Target
    def _on_request_ir_ready(ir_request: dict[str, Any]) -> None:
        store.inject_into_request(ir_request)
        if use_window_tool_search:
            window_tools.inject_into_request(
                codex_window_id,
                ir_request,
                pipeline,
                optimize_tool_descriptions=enable_tool_description_optimization(
                    route.tool_adaptation
                ),
            )

    try:
        target_body = pipeline.convert_request(body, on_ir_ready=_on_request_ir_ready)
    except ConversionError as exc:
        return error_response_for_source(route.source_provider, 400, str(exc)), profile
    if should_localize_code_tools(route):
        persistent_mappings = _load_persistent_tool_mappings(
            persistence,
            session_id=tool_cache_session_id,
        )
    target_body = _apply_converted_request_tool_adaptation(
        target_body,
        route,
        codex_tool_store=tool_store,
        persistent_mappings=persistent_mappings,
        used_mapping_call_ids=used_mapping_call_ids,
        capabilities=source_tool_capabilities,
    )
    tool_capabilities = _pop_tool_localization_capabilities(target_body)
    read_cache = _pop_read_output_cache(target_body)

    profile.update(pipeline.profile)

    log_original_request(pipeline.ir_request)
    if pipeline.warnings:
        logger.warning("Conversion warnings: %s", pipeline.warnings)

    log_converted_request(target_body)

    # Phase 3: Open upstream connection and check for immediate errors
    # *before* committing to a 200 StreamingResponse.
    t_connect = time.perf_counter()
    try:
        stream = await transport.send_streaming(
            provider_info,
            route.target_provider,
            target_body,
            model,
            extra_headers=extra_headers,
        )
    except UpstreamConnectionError as exc:
        profile["stream_connect_ms"] = round(
            (time.perf_counter() - t_connect) * 1000, 2
        )
        # Connection-level failure — no upstream HTTP response exists, so
        # the gateway synthesizes an error message and returns 502.
        error_msg = str(exc)
        dump_error(
            persistence,
            request_body=body,
            response_text=error_msg,
            converted_body=target_body,
            model=model,
            source_provider=route.source_provider,
            target_provider=route.target_provider,
            provider_name=route.provider_name,
            status_code=502,
            error_phase="stream_header",
            upstream_url=str(provider_info.base_url),
            request_log_id=entry_id,
        )
        return (
            error_response_for_source(
                route.source_provider, 502, f"Upstream request failed: {exc}"
            ),
            profile,
        )
    _delete_unused_persistent_tool_mappings(
        persistence,
        session_id=tool_cache_session_id,
        loaded_mappings=persistent_mappings,
        used_call_ids=used_mapping_call_ids,
    )

    profile["stream_connect_ms"] = round((time.perf_counter() - t_connect) * 1000, 2)

    # Application-level error — upstream returned a valid HTTP response with
    # a 4xx/5xx status.  Pass the original body through as-is so the client
    # SDK can parse the real error (e.g. "context_length_exceeded").
    if stream.is_error:
        error_text = await stream.read_error()
        await stream.close()
        log_upstream_error(
            stream.status_code,
            error_text,
            endpoint=str(route.target_provider),
            is_streaming=True,
        )
        dump_error(
            persistence,
            request_body=body,
            response_text=error_text,
            converted_body=target_body,
            model=model,
            source_provider=route.source_provider,
            target_provider=route.target_provider,
            provider_name=route.provider_name,
            status_code=stream.status_code,
            error_phase="stream_header",
            upstream_url=str(provider_info.base_url),
            request_log_id=entry_id,
        )
        return (
            Response(
                body=error_text.encode("utf-8")
                if isinstance(error_text, str)
                else error_text,
                status_code=stream.status_code,
                content_type="application/json",
            ),
            profile,
        )

    # Phase 4: No error — create stream processor and return SSE response
    request_id = extra_headers.get("x-request-id") if extra_headers else None
    trace = _create_stream_trace_logger(
        stream_trace_state,
        request_id=request_id,
        request_log_id=entry_id,
        model=model,
        route=route,
    )

    def _on_ir_event(ir_event: dict[str, Any]) -> None:
        store.cache_from_stream_event(ir_event)
        if trace is not None:
            trace.log("ir_event", ir_event)

    if should_localize_code_tools(route):
        ttl_hours = tool_call_cache_ttl_hours(route.tool_adaptation)

        def _persist_stream_mapping(mapping: LocalizedToolMapping) -> None:
            _persist_tool_mapping(
                persistence,
                session_id=tool_cache_session_id,
                ttl_hours=ttl_hours,
                mapping=mapping,
            )

        stream_transformer = LocalizedToolCallStreamTransformer(
            store=tool_store,
            on_mapping=_persist_stream_mapping,
            capabilities=tool_capabilities,
            read_cache=read_cache,
            use_apply_patch=use_apply_patch_for_code_edits(route.tool_adaptation),
        )
    else:
        stream_transformer = None

    def _create_processor():
        return pipeline.create_stream_processor(
            on_ir_event=_on_ir_event,
            transform_ir_event=stream_transformer.transform
            if stream_transformer is not None
            else None,
            finalize_on_finish_eof=route.source_provider
            in ("openai_responses", "open_responses")
            and route.target_provider == "openai_chat",
        )

    processor = _create_processor()
    format_sse = SSE_FORMATTERS[route.source_provider]
    event_buffer = (
        ResponsesPhaseBuffer(window_id=codex_window_id)
        if route.source_provider in ("openai_responses", "open_responses")
        and route.target_provider == "openai_chat"
        and codex_window_id
        and enable_phase_detection(route.tool_adaptation)
        else None
    )

    if trace is not None:
        trace.log(
            "stream_start",
            {
                "model": model,
                "source_provider": route.source_provider,
                "target_provider": route.target_provider,
                "provider_name": route.provider_name,
                "entry_id": entry_id,
                "codex_window_id": codex_window_id,
                "phase_buffer_enabled": event_buffer is not None,
                "web_search_bridge_enabled": web_search_runtime is not None,
            },
        )
        trace.log("source_request", body)
        trace.log("target_request", target_body)

    return (
        StreamingResponse(
            _converted_stream_response_generator(
                source_provider=route.source_provider,
                stream=stream,
                processor=processor,
                processor_factory=_create_processor,
                model=model,
                format_sse=format_sse,
                event_buffer=event_buffer,
                entry_id=entry_id,
                request_log=request_log,
                trace=trace,
                web_search_runtime=web_search_runtime,
                transport=transport,
                provider_info=provider_info,
                target_provider=route.target_provider,
                target_body=target_body,
                extra_headers=extra_headers,
            ),
            content_type="text/event-stream",
        ),
        profile,
    )
