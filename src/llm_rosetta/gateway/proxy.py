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

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from llm_rosetta._vendor.httpserver import JSONResponse, Response, StreamingResponse

from llm_rosetta.auto_detect import ProviderType
from llm_rosetta.pipeline import ConversionError, ConversionPipeline
from llm_rosetta.routing import ResolvedRoute

from llm_rosetta.observability.error_dump import dump_error

from .logging import (
    get_logger,
    log_converted_request,
    log_original_request,
    log_response,
    log_stream_summary,
    log_upstream_error,
)
from .stream_trace import StreamTraceLogger, StreamTraceState
from .tool_adaptation import (
    CodexToolLocalizationStore,
    LOCALIZATION_CAPABILITIES_KEY,
    READ_OUTPUT_CACHE_KEY,
    LocalizedToolMapping,
    LocalizedToolCallStreamTransformer,
    NativeToolCapabilities,
    ReadOutputCache,
    localized_mapping_from_tool_calls,
    localize_code_editing_chat_request,
    should_localize_code_tools,
    tool_call_cache_ttl_hours,
    translate_localized_ir_response,
)
from .transport import (
    ProviderInfo,
    UpstreamConnectionError,
    UpstreamTransport,
)
from .transport.sse_format import SSE_FORMATTERS, format_sse_done

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


def _remove_tool_definition(body: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Remove one named tool definition from a provider request body."""
    tools = body.get("tools")
    if not isinstance(tools, list):
        return body

    filtered_tools = [tool for tool in tools if _tool_identifier(tool) != tool_name]
    if len(filtered_tools) == len(tools):
        return body

    adapted = dict(body)
    if filtered_tools:
        adapted["tools"] = filtered_tools
    else:
        adapted.pop("tools", None)
        adapted.pop("tool_config", None)

    if (
        _tool_choice_identifier(adapted.get("tool_choice")) == tool_name
        or not filtered_tools
    ):
        adapted.pop("tool_choice", None)
    return adapted


def _apply_tool_adaptation(
    body: dict[str, Any], route: ResolvedRoute
) -> dict[str, Any]:
    """Apply per-model tool adaptation before passthrough or conversion."""
    tool_adaptation = route.tool_adaptation or {}
    if tool_adaptation.get("remove_image_generation"):
        return _remove_tool_definition(body, "image_generation")
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


_default_metadata_store = ProviderMetadataStore()
_default_codex_tool_store = CodexToolLocalizationStore()


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
    persistent_mappings: list[LocalizedToolMapping] = []
    used_mapping_call_ids: set[str] = set()
    profile: dict[str, Any] = {}
    # model was already injected into body by app.py
    model = body.get("model", "")
    body = _apply_tool_adaptation(body, route)
    source_tool_capabilities = NativeToolCapabilities.from_chat_tools(body.get("tools"))

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
        reasoning_config_override=route.reasoning_override,
    )

    # Phase 1+2: Source → IR → Target
    try:
        target_body = pipeline.convert_request(
            body, on_ir_ready=store.inject_into_request
        )
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
                    if trace is not None:
                        trace.log("source_event", source_event, chunk_index=chunk_count)
                    sse_event = format_sse(source_event)
                    if trace is not None:
                        trace.log("downstream_sse", sse_event, chunk_index=chunk_count)
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
        # Write back stream profile to request log entry
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
    stream_trace_state: StreamTraceState | None = None,
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
    persistent_mappings: list[LocalizedToolMapping] = []
    used_mapping_call_ids: set[str] = set()
    profile: dict[str, Any] = {}
    # model was already injected into body by app.py
    model = body.get("model", "")
    body = _apply_tool_adaptation(body, route)
    source_tool_capabilities = NativeToolCapabilities.from_chat_tools(body.get("tools"))

    if _is_openai_responses_direct(route):
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

        profile["stream_connect_ms"] = round(
            (time.perf_counter() - t_connect) * 1000, 2
        )
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

    pipeline = ConversionPipeline(
        route.source_provider,
        route.target_provider,
        route.shim_name,
        upstream_model=model,
        model_capabilities=route.model_capabilities,
        reasoning_config_override=route.reasoning_override,
    )

    # Phase 1+2: Source → IR → Target
    try:
        target_body = pipeline.convert_request(
            body, on_ir_ready=store.inject_into_request
        )
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
        )
    else:
        stream_transformer = None
    processor = pipeline.create_stream_processor(
        on_ir_event=_on_ir_event,
        transform_ir_event=stream_transformer.transform
        if stream_transformer is not None
        else None,
    )
    format_sse = SSE_FORMATTERS[route.source_provider]

    if trace is not None:
        trace.log(
            "stream_start",
            {
                "model": model,
                "source_provider": route.source_provider,
                "target_provider": route.target_provider,
                "provider_name": route.provider_name,
                "entry_id": entry_id,
            },
        )
        trace.log("source_request", body)
        trace.log("target_request", target_body)

    return (
        StreamingResponse(
            _stream_event_generator(
                source_provider=route.source_provider,
                stream=stream,
                processor=processor,
                model=model,
                format_sse=format_sse,
                entry_id=entry_id,
                request_log=request_log,
                trace=trace,
            ),
            content_type="text/event-stream",
        ),
        profile,
    )
