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

import asyncio
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from codex_rosetta._vendor.httpserver import JSONResponse, Response, StreamingResponse

from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.converters.google_genai.image_fetch import (
    ImageFetchCancellation,
    ImageFetchPolicy,
    ImageFetchTimeoutError,
)
from codex_rosetta.pipeline import ConversionError, ConversionPipeline
from codex_rosetta.routing import ResolvedRoute, is_responses_passthrough

from codex_rosetta.observability.error_dump import dump_error

from .codex_search_references import CodexSearchReferenceStore
from .codex_compaction import (
    COMPACT_PROMPT_SHA256,
    InvalidCodexCompactionRequest,
    InvalidCompactionSummary,
    build_compaction_response,
    create_compaction_mapping,
    extract_assistant_summary,
    prepare_codex_compaction,
)
from .code_mode_projection import (
    ExecToolProjection,
    exec_tool_projections_for_route,
    project_modified_exec_web_run_description,
)
from .logging import (
    BodyLogState,
    UpstreamErrorLogState,
    get_logger,
    log_converted_request,
    log_ir_request,
    log_original_request,
    log_response,
    log_stream_summary,
    log_upstream_error,
)
from .image_workers import (
    ImageFetchWorkerPool,
    ImageWorkerCapacityError,
    ImageWorkerTimeoutError,
)
from .inbound_content_encoding import InboundWireRequest
from .stream_phase_buffer import ResponsesPhaseBuffer
from .state_scope import GatewayStateScope
from .stream_trace import StreamTraceLogger, StreamTraceState
from .tool_adaptation import (
    CodexToolLocalizationStore,
    DEFAULT_TOOL_CALL_CACHE_TTL_HOURS,
    EXEC_PROJECTIONS_KEY,
    LOCALIZATION_CAPABILITIES_KEY,
    READ_OUTPUT_CACHE_KEY,
    LocalizedToolMapping,
    LocalizedToolCallStreamTransformer,
    NativeToolCapabilities,
    ReadOutputCache,
    injected_local_tool_names,
    localized_mapping_from_tool_calls,
    localized_native_tool_names,
    localize_code_editing_chat_request,
    should_localize_code_tools,
    translate_localized_ir_response,
)
from .tool_profiles import (
    apply_profile_tool_mutations,
    is_internal_container_when_disabled,
    route_tool_state,
    tool_catalog_lookups,
)
from .transport import (
    ProviderInfo,
    UpstreamConnectionError,
    UpstreamTransport,
)
from .transport.sse_format import SSE_FORMATTERS, format_sse_done
from .web_run_capabilities import (
    WEB_RUN_PROFILE_ITEM_ID,
    project_modified_web_run_function,
    web_run_model_availability,
)
from .web_search import (
    TavilySearchClient,
    WEB_SEARCH_PROFILE_ITEM_ID,
    WebSearchRuntime,
    WebSearchStreamController,
    build_web_search_runtime,
    profile_search_config,
    strip_responses_web_search_tools,
    web_search_trace_summary,
)

logger = get_logger()

# Provider model IDs are compact identifiers, not request payloads.  The
# 256-byte ceiling leaves ample room for namespaced/versioned IDs while
# preventing routing errors from reflecting an entire large request field.
MAX_MODEL_ID_BYTES = 256

# Codex currently sends ``{UUID}:{window_number}`` (about 40 bytes).  Keep a
# generous forward-compatible envelope while preventing state-map keys from
# bypassing the stores' value-byte accounting.
MAX_CODEX_WINDOW_ID_BYTES = 128


async def _convert_request(
    pipeline: ConversionPipeline,
    route: ResolvedRoute,
    body: dict[str, Any],
    on_ir_ready: Callable[[dict[str, Any]], None],
    *,
    image_fetch_workers: ImageFetchWorkerPool | None = None,
    image_fetch_policy: ImageFetchPolicy | None = None,
) -> dict[str, Any]:
    """Keep potentially blocking Google image retrieval off the event loop."""
    if route.target_provider == "google":
        policy = image_fetch_policy or ImageFetchPolicy(
            cancellation=ImageFetchCancellation()
        )
        cancellation = policy.cancellation or ImageFetchCancellation()
        owner = image_fetch_workers or ImageFetchWorkerPool(max_workers=1)
        close_owner = image_fetch_workers is None
        try:
            return await owner.run(
                lambda: pipeline.convert_request(
                    body,
                    on_ir_ready=on_ir_ready,
                ),
                cancellation=cancellation,
                timeout_seconds=policy.timeout_seconds,
            )
        finally:
            if close_owner:
                await owner.close()
    return pipeline.convert_request(body, on_ir_ready=on_ir_ready)


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


def _conversion_failure_response(
    source_provider: ProviderType,
    exc: ImageWorkerCapacityError | ImageWorkerTimeoutError | ConversionError,
) -> Response:
    """Map conversion scheduling/fetch failures to stable Gateway statuses."""
    if isinstance(exc, ImageWorkerCapacityError):
        return error_response_for_source(source_provider, 503, str(exc))
    if isinstance(exc, ImageWorkerTimeoutError) or isinstance(
        exc.__cause__, ImageFetchTimeoutError
    ):
        return error_response_for_source(source_provider, 504, str(exc))
    return error_response_for_source(source_provider, 400, str(exc))


async def _run_rosetta_compaction(
    *,
    route: ResolvedRoute,
    provider_info: ProviderInfo,
    preparation: Any,
    transport: UpstreamTransport,
    metadata_store: ProviderMetadataStore | None,
    codex_tool_store: CodexToolLocalizationStore | None,
    extra_headers: dict[str, str] | None,
    persistence: Any | None,
    state_scope: GatewayStateScope,
    codex_window_id: str | None,
    image_fetch_workers: ImageFetchWorkerPool | None,
    stream: bool,
) -> tuple[Response | StreamingResponse, dict[str, Any]]:
    """Execute the internal no-tools summary call and return a V2 item."""
    if persistence is None:
        return (
            error_response_for_source(
                route.source_provider,
                503,
                "Rosetta remote compaction requires gateway persistence",
            ),
            {"compaction_mode": "rosetta", "compaction_reason": preparation.reason},
        )
    assert preparation.summary_request is not None
    summary_response, summary_profile = await handle_non_streaming(
        route,
        provider_info,
        preparation.summary_request,
        transport=transport,
        metadata_store=metadata_store,
        codex_tool_store=codex_tool_store,
        extra_headers=extra_headers,
        persistence=persistence,
        state_scope=state_scope,
        codex_window_id=codex_window_id,
        upstream_error_log_state=None,
        body_log_state=None,
        image_fetch_workers=image_fetch_workers,
        skip_codex_compaction=True,
        disable_error_dump=True,
    )
    profile: dict[str, Any] = {
        "compaction_mode": "rosetta",
        "compaction_reason": preparation.reason,
        "compaction_rehydrated_count": preparation.rehydrated_count,
        "compaction_dropped_rosetta_count": preparation.dropped_rosetta_count,
        "compaction_dropped_native_count": preparation.dropped_native_count,
        "compaction_prompt_sha256": COMPACT_PROMPT_SHA256,
    }
    profile.update(
        {f"compaction_summary_{key}": value for key, value in summary_profile.items()}
    )
    if summary_response.status_code >= 400:
        return summary_response, profile
    try:
        summary_payload = json.loads(summary_response.body)
        if not isinstance(summary_payload, dict):
            raise InvalidCompactionSummary("internal compaction response is not JSON")
        summary = extract_assistant_summary(summary_payload)
    except (InvalidCompactionSummary, ValueError, TypeError) as exc:
        logger.warning(
            "Rosetta compaction summary failed (reason=%s, prompt_sha256=%s): %s",
            preparation.reason,
            COMPACT_PROMPT_SHA256,
            exc,
        )
        return error_response_for_source(route.source_provider, 502, str(exc)), profile
    try:
        mapping = create_compaction_mapping(
            persistence,
            principal_id=state_scope.principal_id,
            source_model=str(preparation.body.get("model", "")),
            reason=preparation.reason,
            summary=summary,
        )
    except Exception as exc:
        logger.warning(
            "Rosetta compaction persistence failed (reason=%s, prompt_sha256=%s): %s",
            preparation.reason,
            COMPACT_PROMPT_SHA256,
            exc,
        )
        return error_response_for_source(route.source_provider, 503, str(exc)), profile
    return (
        build_compaction_response(
            model=str(preparation.body.get("model", "")),
            token=mapping.token,
            stream=stream,
        ),
        profile,
    )


async def _prepare_codex_compaction_request(
    *,
    route: ResolvedRoute,
    provider_info: ProviderInfo,
    body: dict[str, Any],
    transport: UpstreamTransport,
    metadata_store: ProviderMetadataStore | None,
    codex_tool_store: CodexToolLocalizationStore | None,
    extra_headers: dict[str, str] | None,
    persistence: Any | None,
    state_scope: GatewayStateScope,
    codex_window_id: str | None,
    image_fetch_workers: ImageFetchWorkerPool | None,
    stream: bool,
    enabled: bool = True,
) -> tuple[dict[str, Any], Response | StreamingResponse | None, dict[str, Any]]:
    """Apply V2 replay/policy, returning an early response only when required."""
    if not enabled:
        return body, None, {}
    try:
        preparation = prepare_codex_compaction(
            body,
            route=route,
            persistence=persistence,
            principal_id=state_scope.principal_id,
        )
    except InvalidCodexCompactionRequest as exc:
        return (
            body,
            error_response_for_source(route.source_provider, 400, str(exc)),
            {},
        )
    profile = {
        "compaction_mode": preparation.mode,
        "compaction_reason": preparation.reason,
        "compaction_rehydrated_count": preparation.rehydrated_count,
        "compaction_dropped_rosetta_count": preparation.dropped_rosetta_count,
        "compaction_dropped_native_count": preparation.dropped_native_count,
    }
    if preparation.mode != "rosetta":
        return preparation.body, None, profile if preparation.mode else {}
    response, compaction_profile = await _run_rosetta_compaction(
        route=route,
        provider_info=provider_info,
        preparation=preparation,
        transport=transport,
        metadata_store=metadata_store,
        codex_tool_store=codex_tool_store,
        extra_headers=extra_headers,
        persistence=persistence,
        state_scope=state_scope,
        codex_window_id=codex_window_id,
        image_fetch_workers=image_fetch_workers,
        stream=stream,
    )
    return preparation.body, response, compaction_profile


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


def validate_model_id(model: Any) -> str | None:
    """Return a valid bounded model identifier or raise a stable input error."""
    if model is None:
        return None
    if not isinstance(model, str) or not model.strip():
        raise ValueError("'model' must be a non-empty string")
    if len(model.encode("utf-8")) > MAX_MODEL_ID_BYTES:
        raise ValueError(f"'model' must be at most {MAX_MODEL_ID_BYTES} UTF-8 bytes")
    return model


def extract_model(source_provider: ProviderType, body: dict[str, Any]) -> str | None:
    """Extract and validate the model name from a source-format request body."""
    return validate_model_id(body.get("model"))


def normalize_codex_window_id(value: Any) -> str | None:
    """Normalize an optional bounded Codex window identity header."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("'x-codex-window-id' must be a string")
    if len(value.encode("utf-8")) > MAX_CODEX_WINDOW_ID_BYTES:
        raise ValueError(
            "'x-codex-window-id' must be at most "
            f"{MAX_CODEX_WINDOW_ID_BYTES} UTF-8 bytes"
        )
    return value


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
    """Apply the selected profile before passthrough or conversion."""
    adapted = body
    if route.target_provider == "openai_chat":
        adapted = _remove_tool_definition(adapted, "tool_search")
    if getattr(route, "tool_profile", None):
        return _apply_tool_profile_to_request(adapted, route)
    if route.target_provider == "openai_chat":
        return _remove_tool_definition(
            adapted,
            "image_generation",
            aliases=frozenset({"image_gen", "imagegen", "image_gen__imagegen"}),
        )
    return adapted


def _profile_item_id(tool: Any, *, namespace: str | None = None) -> str | None:
    """Resolve one Responses tool definition to its bundled catalog ID."""
    if not isinstance(tool, dict):
        return None
    name = _tool_identifier(tool)
    if not isinstance(name, str):
        return None
    lookups = tool_catalog_lookups()
    if namespace is not None:
        return lookups["namespace_children"].get((namespace, name))
    tool_type = tool.get("type", "function")
    return lookups["by_type_name"].get((tool_type, name))


def _filter_profile_namespace_children(
    tool: dict[str, Any],
    namespace_item_id: str,
    route: ResolvedRoute,
) -> tuple[dict[str, Any] | None, set[str]]:
    """Filter disabled children from one enabled namespace definition."""
    namespace = tool.get("name")
    children = tool.get("tools")
    if not isinstance(namespace, str) or not isinstance(children, list):
        return tool, set()
    kept: list[Any] = []
    removed: set[str] = set()
    for child in children:
        child_id = _profile_item_id(child, namespace=namespace)
        if child_id is not None and route_tool_state(route, child_id) == "disabled":
            child_name = _tool_identifier(child)
            if isinstance(child_name, str):
                removed.add(child_name)
        else:
            adapted_child = child
            if child_id is not None:
                adapted_child = apply_profile_tool_mutations(
                    adapted_child, child_id, route
                )
            kept.append(
                apply_profile_tool_mutations(adapted_child, namespace_item_id, route)
            )
    if not kept:
        return None, removed
    if kept == children:
        return tool, removed
    adapted = dict(tool)
    adapted["tools"] = kept
    return adapted, removed


def _filter_profile_tool(
    tool: Any, route: ResolvedRoute
) -> tuple[Any | None, set[str]]:
    """Apply one profile entry to one Responses tool definition."""
    item_id = _profile_item_id(tool)
    if item_id is None:
        return tool, set()
    name = _tool_identifier(tool)
    state = route_tool_state(route, item_id)
    if state == "disabled" and not (
        route.target_provider == "openai_chat"
        and is_internal_container_when_disabled(route, item_id)
    ):
        return None, {name} if isinstance(name, str) else set()
    if isinstance(tool, dict) and tool.get("type") == "namespace":
        adapted, removed = _filter_profile_namespace_children(tool, item_id, route)
        if adapted is None and isinstance(name, str):
            removed.add(name)
        return adapted, removed
    adapted = apply_profile_tool_mutations(tool, item_id, route)
    if (
        item_id == "custom.exec"
        and route_tool_state(route, WEB_RUN_PROFILE_ITEM_ID) == "modified"
        and isinstance(adapted, dict)
    ):
        description = adapted.get("description")
        if isinstance(description, str):
            projected_description = project_modified_exec_web_run_description(
                description,
                route,
            )
            if projected_description != description:
                adapted = dict(adapted)
                adapted["description"] = projected_description
    if (
        item_id == WEB_RUN_PROFILE_ITEM_ID
        and state == "modified"
        and isinstance(adapted, dict)
    ):
        search_available, browser_available = web_run_model_availability(route)
        projected = project_modified_web_run_function(
            adapted,
            search_available=search_available,
            browser_available=browser_available,
        )
        if projected is None:
            return None, {name} if isinstance(name, str) else set()
        return projected, set()
    return adapted, set()


def _filter_profile_tools(
    tools: list[Any], route: ResolvedRoute
) -> tuple[list[Any], set[str]]:
    """Filter one Responses tool list using small per-entry decisions."""
    filtered: list[Any] = []
    removed: set[str] = set()
    for tool in tools:
        adapted, item_removed = _filter_profile_tool(tool, route)
        removed.update(item_removed)
        if adapted is not None:
            filtered.append(adapted)
    return filtered, removed


def _filter_profile_lite_input(
    input_items: list[Any], route: ResolvedRoute
) -> tuple[list[Any], set[str]]:
    """Filter tools embedded in Responses Lite additional_tools items."""
    result: list[Any] = []
    removed: set[str] = set()
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "additional_tools":
            result.append(item)
            continue
        embedded = item.get("tools")
        if not isinstance(embedded, list):
            result.append(item)
            continue
        filtered, item_removed = _filter_profile_tools(embedded, route)
        removed.update(item_removed)
        if filtered == embedded:
            result.append(item)
        elif filtered:
            next_item = dict(item)
            next_item["tools"] = filtered
            result.append(next_item)
    return result, removed


def _apply_tool_profile_to_request(
    body: dict[str, Any], route: ResolvedRoute
) -> dict[str, Any]:
    """Apply catalog states to top-level and Responses Lite tool containers."""
    if route.source_provider not in ("openai_responses", "open_responses"):
        return body
    changed = False
    removed_names: set[str] = set()
    adapted = dict(body)

    tools = body.get("tools")
    if isinstance(tools, list):
        filtered, removed = _filter_profile_tools(tools, route)
        removed_names.update(removed)
        if filtered != tools:
            changed = True
            if filtered:
                adapted["tools"] = filtered
            else:
                adapted.pop("tools", None)

    input_items = body.get("input")
    if isinstance(input_items, list):
        next_input, removed = _filter_profile_lite_input(input_items, route)
        removed_names.update(removed)
        if next_input != input_items:
            changed = True
            adapted["input"] = next_input

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
            native_tool_names=localized_native_tool_names(route),
            injected_tool_names=injected_local_tool_names(route),
            exec_projections=exec_tool_projections_for_route(route),
            profile_route=route,
            hide_exec_container=is_internal_container_when_disabled(
                route, "custom.exec"
            ),
        )
    return body


def _source_tool_capabilities_after_profile(
    original_body: dict[str, Any],
    adapted_body: dict[str, Any],
    route: ResolvedRoute,
) -> NativeToolCapabilities:
    """Preserve internal apply_patch capability when only direct exposure is off."""
    capabilities = NativeToolCapabilities.from_chat_tools(
        _flatten_responses_tools(adapted_body)
    )
    if route_tool_state(route, "custom.apply_patch") != "disabled":
        return capabilities
    original = NativeToolCapabilities.from_chat_tools(
        _flatten_responses_tools(original_body)
    )
    if not original.has_custom_apply_patch:
        return capabilities
    return NativeToolCapabilities(
        has_exec_command=capabilities.has_exec_command,
        has_shell_command=capabilities.has_shell_command,
        has_custom_apply_patch=True,
        has_custom_exec=capabilities.has_custom_exec,
    )


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


def _pop_exec_tool_projections(
    body: dict[str, Any],
) -> dict[str, ExecToolProjection]:
    """Remove and return request-local exec projection metadata."""
    value = body.pop(EXEC_PROJECTIONS_KEY, None)
    if not isinstance(value, dict):
        return {}
    return {
        name: projection
        for name, projection in value.items()
        if isinstance(name, str) and isinstance(projection, ExecToolProjection)
    }


def _load_persistent_tool_mappings(
    persistence: Any | None,
    *,
    state_scope: GatewayStateScope,
) -> list[LocalizedToolMapping]:
    if not state_scope.persistent:
        return []
    if persistence is None:
        raise RuntimeError(
            "Persistent tool-history storage is unavailable; refusing lossy replay"
        )
    now = datetime.now(timezone.utc)
    try:
        rows = persistence.query_tool_call_mappings(
            principal_id=state_scope.principal_id,
            provider_name=state_scope.provider_name,
            model=state_scope.model,
            session_id=state_scope.conversation_id,
            now=now.isoformat(),
            renew_expire_at=(
                now + timedelta(hours=DEFAULT_TOOL_CALL_CACHE_TTL_HOURS)
            ).isoformat(),
            renewed_at=now.isoformat(),
        )
    except Exception as exc:
        logger.error("Failed to load persistent tool-call mappings", exc_info=True)
        raise RuntimeError(
            "Persistent tool history could not be authenticated; refusing lossy replay"
        ) from exc

    mappings: list[LocalizedToolMapping] = []
    for row in rows:
        mapping = localized_mapping_from_tool_calls(
            row.get("original_tool_call") or {},
            row.get("codex_tool_call") or {},
        )
        if mapping is None:
            raise RuntimeError(
                "Authenticated persistent tool history contains an invalid mapping"
            )
        mappings.append(mapping)
    return mappings


def _delete_unused_persistent_tool_mappings(
    persistence: Any | None,
    *,
    state_scope: GatewayStateScope,
    loaded_mappings: list[LocalizedToolMapping],
    used_call_ids: set[str],
) -> None:
    if persistence is None or not state_scope.persistent or not loaded_mappings:
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
            principal_id=state_scope.principal_id,
            provider_name=state_scope.provider_name,
            model=state_scope.model,
            session_id=state_scope.conversation_id,
            tool_call_ids=unused,
        )
    except Exception:
        logger.debug("Failed to delete unused tool-call mappings", exc_info=True)


def _persist_tool_mapping(
    persistence: Any | None,
    *,
    state_scope: GatewayStateScope,
    ttl_hours: float,
    mapping: LocalizedToolMapping,
) -> None:
    if not state_scope.persistent or not mapping.call_id:
        return
    if persistence is None:
        raise RuntimeError(
            "Persistent tool-history storage is unavailable; refusing volatile mapping"
        )
    now = datetime.now(timezone.utc)
    try:
        persistence.upsert_tool_call_mapping(
            principal_id=state_scope.principal_id,
            provider_name=state_scope.provider_name,
            model=state_scope.model,
            session_id=state_scope.conversation_id,
            tool_call_id=mapping.call_id,
            original_tool_call=mapping.original_tool_call(),
            codex_tool_call=mapping.codex_tool_call(),
            expire_at=(now + timedelta(hours=ttl_hours)).isoformat(),
            timestamp=now.isoformat(),
        )
    except Exception as exc:
        logger.error("Failed to persist tool-call mapping", exc_info=True)
        raise RuntimeError(
            "Tool history could not be durably protected; refusing volatile mapping"
        ) from exc


def _translate_and_persist_localized_response_tools(
    ir_response: dict[str, Any],
    route: ResolvedRoute,
    *,
    tool_store: CodexToolLocalizationStore,
    persistence: Any | None,
    state_scope: GatewayStateScope,
    capabilities: NativeToolCapabilities | None = None,
    read_cache: ReadOutputCache | None = None,
    exec_projections: dict[str, ExecToolProjection] | None = None,
) -> None:
    if not should_localize_code_tools(route):
        return
    ttl_hours = DEFAULT_TOOL_CALL_CACHE_TTL_HOURS

    def _remember_mapping(mapping: LocalizedToolMapping) -> None:
        _persist_tool_mapping(
            persistence,
            state_scope=state_scope,
            ttl_hours=ttl_hours,
            mapping=mapping,
        )

    translate_localized_ir_response(
        ir_response,
        store=tool_store if not state_scope.persistent else None,
        on_mapping=_remember_mapping if state_scope.persistent else None,
        capabilities=capabilities,
        read_cache=read_cache,
        use_apply_patch=True,
        exec_projections=exec_projections,
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
    codex_search_reference_store: CodexSearchReferenceStore | None = None,
    image_fetch_workers: ImageFetchWorkerPool | None = None,
) -> None:
    """Close transport and clear all app-owned cross-request state."""
    if image_fetch_workers is not None:
        await image_fetch_workers.close()
    if transport is not None:
        await transport.close()
    store = metadata_store if metadata_store is not None else _default_metadata_store
    store.clear_all()
    tools = (
        codex_tool_store if codex_tool_store is not None else _default_codex_tool_store
    )
    tools.clear_all()
    if codex_search_reference_store is not None:
        codex_search_reference_store.clear_all()


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


MAX_PROVIDER_METADATA_ENTRY_BYTES = 1 * 1024 * 1024
MAX_PROVIDER_METADATA_SCOPE_BYTES = 8 * 1024 * 1024
MAX_PROVIDER_METADATA_ENTRIES_PER_PRINCIPAL = 1_024
MAX_PROVIDER_METADATA_PRINCIPAL_BYTES = 16 * 1024 * 1024
MAX_PROVIDER_METADATA_GLOBAL_BYTES = 64 * 1024 * 1024


class ProviderMetadataCapacityError(RuntimeError):
    """Raised before provider metadata would exceed a configured budget."""


@dataclass
class _ProviderMetadataEntry:
    serialized: bytes
    byte_size: int
    created: float = field(default_factory=time.monotonic)


@dataclass
class _ProviderMetadataState:
    store: dict[tuple[GatewayStateScope, str], _ProviderMetadataEntry] = field(
        default_factory=dict
    )
    scope_bytes: dict[GatewayStateScope, int] = field(default_factory=dict)
    principal_entries: dict[str, int] = field(default_factory=dict)
    principal_bytes: dict[str, int] = field(default_factory=dict)
    global_bytes: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)


class ProviderMetadataStore:
    """Stores provider_metadata across request boundaries with TTL and bounds.

    Args:
        ttl: Time-to-live in seconds for each entry.  Defaults to 30 minutes.
        max_size: Maximum number of entries.  Oldest is evicted on overflow.
    """

    def __init__(
        self,
        *,
        ttl: float = 1800.0,
        max_size: int = 10_000,
        max_entry_bytes: int = MAX_PROVIDER_METADATA_ENTRY_BYTES,
        max_bytes_per_scope: int = MAX_PROVIDER_METADATA_SCOPE_BYTES,
        max_entries_per_principal: int = MAX_PROVIDER_METADATA_ENTRIES_PER_PRINCIPAL,
        max_bytes_per_principal: int = MAX_PROVIDER_METADATA_PRINCIPAL_BYTES,
        max_bytes_global: int = MAX_PROVIDER_METADATA_GLOBAL_BYTES,
        _scope: GatewayStateScope | None = None,
        _state: _ProviderMetadataState | None = None,
    ) -> None:
        self._is_root = _state is None
        self._state = _state or _ProviderMetadataState()
        self._store = self._state.store
        self._ttl = ttl
        self._max_size = max_size
        self._max_entry_bytes = max_entry_bytes
        self._max_bytes_per_scope = max_bytes_per_scope
        self._max_entries_per_principal = max_entries_per_principal
        self._max_bytes_per_principal = max_bytes_per_principal
        self._max_bytes_global = max_bytes_global
        self._scope = _scope or GatewayStateScope(
            principal_id="__standalone_store__",
            provider_name="",
            model="",
            conversation_id=f"request:{uuid.uuid4().hex}",
            persistent=False,
        )

    def scoped(self, scope: GatewayStateScope) -> ProviderMetadataStore:
        """Return a view whose call IDs are namespaced to *scope*."""
        return ProviderMetadataStore(
            ttl=self._ttl,
            max_size=self._max_size,
            max_entry_bytes=self._max_entry_bytes,
            max_bytes_per_scope=self._max_bytes_per_scope,
            max_entries_per_principal=self._max_entries_per_principal,
            max_bytes_per_principal=self._max_bytes_per_principal,
            max_bytes_global=self._max_bytes_global,
            _scope=scope,
            _state=self._state,
        )

    def _key(self, call_id: str) -> tuple[GatewayStateScope, str]:
        return self._scope, call_id

    @staticmethod
    def _canonicalize(value: Any) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def _remove_key_locked(self, key: tuple[GatewayStateScope, str]) -> None:
        entry = self._store.pop(key, None)
        if entry is None:
            return
        scope = key[0]
        principal_id = scope.principal_id
        principal_entries = self._state.principal_entries.get(principal_id, 0) - 1
        scope_bytes = self._state.scope_bytes.get(scope, 0) - entry.byte_size
        principal_bytes = (
            self._state.principal_bytes.get(principal_id, 0) - entry.byte_size
        )
        if scope_bytes > 0:
            self._state.scope_bytes[scope] = scope_bytes
        else:
            self._state.scope_bytes.pop(scope, None)
        if principal_entries > 0:
            self._state.principal_entries[principal_id] = principal_entries
        else:
            self._state.principal_entries.pop(principal_id, None)
        if principal_bytes > 0:
            self._state.principal_bytes[principal_id] = principal_bytes
        else:
            self._state.principal_bytes.pop(principal_id, None)
        self._state.global_bytes -= entry.byte_size

    def _evict_expired_locked(self, now: float) -> None:
        expired = [
            key for key, entry in self._store.items() if now - entry.created > self._ttl
        ]
        for key in expired:
            self._remove_key_locked(key)

    def _put_many(self, values: list[tuple[str, Any]]) -> None:
        prepared: dict[tuple[GatewayStateScope, str], tuple[bytes, int]] = {}
        for call_id, value in values:
            serialized = self._canonicalize(value)
            byte_size = len(serialized)
            if byte_size > self._max_entry_bytes:
                raise ProviderMetadataCapacityError(
                    f"provider_metadata entry exceeds {self._max_entry_bytes} bytes"
                )
            prepared[self._key(call_id)] = (serialized, byte_size)
        if not prepared:
            return

        now = time.monotonic()
        principal_id = self._scope.principal_id
        with self._state.lock:
            self._evict_expired_locked(now)
            new_keys = [key for key in prepared if key not in self._store]
            projected_principal_entries = self._state.principal_entries.get(
                principal_id, 0
            ) + len(new_keys)
            if projected_principal_entries > self._max_entries_per_principal:
                raise ProviderMetadataCapacityError(
                    "provider_metadata principal entry count exceeds "
                    f"{self._max_entries_per_principal}"
                )
            overflow = len(self._store) + len(new_keys) - self._max_size
            evictions: list[tuple[GatewayStateScope, str]] = []
            if overflow > 0:
                candidates = sorted(
                    (
                        key
                        for key in self._store
                        if key[0].principal_id == principal_id and key not in prepared
                    ),
                    key=lambda key: self._store[key].created,
                )
                if len(candidates) < overflow:
                    raise ProviderMetadataCapacityError(
                        f"provider_metadata entry count exceeds {self._max_size}"
                    )
                evictions = candidates[:overflow]

            removed_scope_bytes = sum(
                self._store[key].byte_size for key in evictions if key[0] == self._scope
            )
            removed_principal_bytes = sum(
                self._store[key].byte_size for key in evictions
            )
            removed_global_bytes = removed_principal_bytes
            for key in prepared:
                old = self._store.get(key)
                if old is not None:
                    removed_scope_bytes += old.byte_size
                    removed_principal_bytes += old.byte_size
                    removed_global_bytes += old.byte_size

            added_bytes = sum(item[1] for item in prepared.values())
            scope_bytes = (
                self._state.scope_bytes.get(self._scope, 0)
                - removed_scope_bytes
                + added_bytes
            )
            principal_bytes = (
                self._state.principal_bytes.get(principal_id, 0)
                - removed_principal_bytes
                + added_bytes
            )
            global_bytes = self._state.global_bytes - removed_global_bytes + added_bytes
            if scope_bytes > self._max_bytes_per_scope:
                raise ProviderMetadataCapacityError(
                    f"provider_metadata scope exceeds {self._max_bytes_per_scope} bytes"
                )
            if principal_bytes > self._max_bytes_per_principal:
                raise ProviderMetadataCapacityError(
                    "provider_metadata principal exceeds "
                    f"{self._max_bytes_per_principal} bytes"
                )
            if global_bytes > self._max_bytes_global:
                raise ProviderMetadataCapacityError(
                    "provider_metadata application exceeds "
                    f"{self._max_bytes_global} bytes"
                )

            for key in evictions:
                self._remove_key_locked(key)
            for key, (serialized, byte_size) in prepared.items():
                self._remove_key_locked(key)
                self._store[key] = _ProviderMetadataEntry(
                    serialized=serialized,
                    byte_size=byte_size,
                    created=now,
                )
                scope = key[0]
                entry_principal = scope.principal_id
                self._state.scope_bytes[scope] = (
                    self._state.scope_bytes.get(scope, 0) + byte_size
                )
                self._state.principal_entries[entry_principal] = (
                    self._state.principal_entries.get(entry_principal, 0) + 1
                )
                self._state.principal_bytes[entry_principal] = (
                    self._state.principal_bytes.get(entry_principal, 0) + byte_size
                )
                self._state.global_bytes += byte_size

    def cache_from_response(self, ir_response: dict[str, Any]) -> None:
        """Extract and cache provider_metadata from tool calls in an IR response."""
        values: list[tuple[str, Any]] = []
        for choice in ir_response.get("choices", []):
            msg = choice.get("message", {})
            for part in msg.get("content", []):
                if part.get("type") == "tool_call" and "provider_metadata" in part:
                    tool_call_id = part.get("tool_call_id")
                    if tool_call_id:
                        values.append((tool_call_id, part["provider_metadata"]))
        self._put_many(values)
        for tool_call_id, _value in values:
            logger.debug("Cached provider_metadata for tool_call %s", tool_call_id)

    def cache_from_stream_event(self, ir_event: dict[str, Any]) -> None:
        """Cache provider_metadata from a tool_call_start stream event."""
        if (
            ir_event.get("type") == "tool_call_start"
            and "provider_metadata" in ir_event
        ):
            tool_call_id = ir_event.get("tool_call_id")
            if tool_call_id:
                self._put_many([(tool_call_id, ir_event["provider_metadata"])])

    def inject_into_request(self, ir_request: dict[str, Any]) -> None:
        """Inject cached provider_metadata into tool call parts in an IR request.

        Clients send the full conversation history on every request, so the
        same tool_call_id may appear in multiple requests.  Entries are kept
        alive (not popped) for subsequent turns.
        """
        injections: list[tuple[dict[str, Any], bytes]] = []
        with self._state.lock:
            self._evict_expired_locked(time.monotonic())
            logger.debug(
                "inject: store has %d entries: %s",
                len(self._store),
                list(self._store.keys()),
            )
            for msg in ir_request.get("messages", []):
                for part in msg.get("content", []):
                    if part.get("type") == "tool_call":
                        tool_call_id = part.get("tool_call_id")
                        key = self._key(tool_call_id) if tool_call_id else None
                        entry = self._store.get(key) if key is not None else None
                        if entry is not None:
                            injections.append((part, entry.serialized))
        for part, serialized in injections:
            part["provider_metadata"] = json.loads(serialized)

    def clear(self) -> None:
        """Remove entries owned by this store's scope."""
        with self._state.lock:
            keys = [key for key in self._store if key[0] == self._scope]
            for key in keys:
                self._remove_key_locked(key)

    def clear_all(self) -> None:
        """Remove all entries owned by this root store."""
        if not self._is_root:
            raise RuntimeError("clear_all() is only available on a root store")
        with self._state.lock:
            self._store.clear()
            self._state.scope_bytes.clear()
            self._state.principal_entries.clear()
            self._state.principal_bytes.clear()
            self._state.global_bytes = 0

    def __len__(self) -> int:
        with self._state.lock:
            self._evict_expired_locked(time.monotonic())
            if self._is_root:
                return len(self._store)
            return sum(1 for scope, _call_id in self._store if scope == self._scope)


_default_metadata_store = ProviderMetadataStore()
_default_codex_tool_store = CodexToolLocalizationStore()


# ---------------------------------------------------------------------------
# Core proxy handlers
# ---------------------------------------------------------------------------


def _resolve_state_stores(
    *,
    route: ResolvedRoute,
    model: str,
    state_scope: GatewayStateScope | None,
    metadata_store: ProviderMetadataStore | None,
    codex_tool_store: CodexToolLocalizationStore | None,
) -> tuple[
    GatewayStateScope,
    ProviderMetadataStore,
    CodexToolLocalizationStore,
]:
    """Resolve one ownership scope across all cross-request state stores."""
    scope = state_scope or GatewayStateScope.for_request(
        principal_id="__direct_request__",
        provider_name=route.provider_name,
        model=model,
        window_id=None,
    )
    store_root = (
        metadata_store if metadata_store is not None else _default_metadata_store
    )
    tool_store_root = (
        codex_tool_store if codex_tool_store is not None else _default_codex_tool_store
    )
    if state_scope is None:
        # Direct library callers have no authenticated request context. Keep
        # explicitly supplied stores compatible but never persist them.
        return scope, store_root, tool_store_root
    return (
        scope,
        store_root.scoped(scope),
        tool_store_root.scoped(scope),
    )


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
    state_scope: GatewayStateScope | None = None,
    codex_window_id: str | None = None,
    upstream_error_log_state: UpstreamErrorLogState | None = None,
    body_log_state: BodyLogState | None = None,
    image_fetch_workers: ImageFetchWorkerPool | None = None,
    skip_codex_compaction: bool = False,
    disable_error_dump: bool = False,
) -> tuple[Response, dict[str, Any]]:
    """Non-streaming proxy: convert -> forward -> convert back -> respond.

    Returns:
        A ``(response, profile)`` tuple.  The profile dict contains
        per-phase timing data merged from the conversion pipeline and
        gateway-level measurements (upstream latency).
    """
    model = body.get("model", "")
    scope, store, tool_store = _resolve_state_stores(
        route=route,
        model=model,
        state_scope=state_scope,
        metadata_store=metadata_store,
        codex_tool_store=codex_tool_store,
    )
    persistent_mappings: list[LocalizedToolMapping] = []
    used_mapping_call_ids: set[str] = set()
    profile: dict[str, Any] = {}
    error_dump_persistence = None if disable_error_dump else persistence
    (
        body,
        compaction_response,
        compaction_profile,
    ) = await _prepare_codex_compaction_request(
        route=route,
        provider_info=provider_info,
        body=body,
        transport=transport,
        metadata_store=metadata_store,
        codex_tool_store=codex_tool_store,
        extra_headers=extra_headers,
        persistence=persistence,
        state_scope=scope,
        codex_window_id=codex_window_id,
        image_fetch_workers=image_fetch_workers,
        stream=False,
        enabled=not skip_codex_compaction,
    )
    profile.update(compaction_profile)
    if compaction_response is not None:
        assert isinstance(compaction_response, Response)
        return compaction_response, profile
    # model was already injected into body by app.py
    original_body = body
    body = _apply_tool_adaptation(body, route)
    source_tool_capabilities = _source_tool_capabilities_after_profile(
        original_body, body, route
    )
    if is_responses_passthrough(route):
        log_original_request(body, state=body_log_state)
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
                state=upstream_error_log_state,
            )
            dump_error(
                error_dump_persistence,
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
            log_response(
                resp.body,
                label="UPSTREAM RESPONSE",
                state=body_log_state,
            )
        return (
            Response(
                body=resp.raw_content,
                status_code=resp.status_code,
                content_type="application/json",
            ),
            profile,
        )

    log_original_request(body, state=body_log_state)
    image_fetch_cancellation = ImageFetchCancellation()
    image_fetch_policy = ImageFetchPolicy(
        proxy_url=provider_info.proxy_url,
        cancellation=image_fetch_cancellation,
    )
    pipeline = ConversionPipeline(
        route.source_provider,
        route.target_provider,
        route.shim_name,
        upstream_model=model,
        input_modalities=route.input_modalities,
        reasoning_mapping=None,
        provider_name=route.provider_name,
        conversion_options={
            "image_fetch_policy": image_fetch_policy,
        },
    )

    # Phase 1+2: Source → IR → Target
    def _on_request_ir_ready(ir_request: dict[str, Any]) -> None:
        store.inject_into_request(ir_request)

    try:
        target_body = await _convert_request(
            pipeline,
            route,
            body,
            _on_request_ir_ready,
            image_fetch_workers=image_fetch_workers,
            image_fetch_policy=image_fetch_policy,
        )
    except (ImageWorkerCapacityError, ImageWorkerTimeoutError, ConversionError) as exc:
        return _conversion_failure_response(route.source_provider, exc), profile
    if should_localize_code_tools(route):
        persistent_mappings = _load_persistent_tool_mappings(
            persistence,
            state_scope=scope,
        )
    target_body = _apply_converted_request_tool_adaptation(
        target_body,
        route,
        codex_tool_store=tool_store if not scope.persistent else None,
        persistent_mappings=persistent_mappings,
        used_mapping_call_ids=used_mapping_call_ids,
        capabilities=source_tool_capabilities,
    )
    tool_capabilities = _pop_tool_localization_capabilities(target_body)
    read_cache = _pop_read_output_cache(target_body)
    exec_projections = _pop_exec_tool_projections(target_body)

    profile.update(pipeline.profile)

    log_ir_request(pipeline.ir_request, state=body_log_state)
    if pipeline.warnings:
        logger.warning("Conversion warnings: %s", pipeline.warnings)
    log_converted_request(target_body, state=body_log_state)

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
        state_scope=scope,
        loaded_mappings=persistent_mappings,
        used_call_ids=used_mapping_call_ids,
    )
    profile["upstream_ms"] = round((time.perf_counter() - t_upstream) * 1000, 2)

    if resp.is_error:
        log_upstream_error(
            resp.status_code,
            resp.error_text,
            endpoint=str(route.target_provider),
            state=upstream_error_log_state,
        )
        dump_error(
            error_dump_persistence,
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
    log_response(resp.body, label="UPSTREAM RESPONSE", state=body_log_state)

    def _on_response_ir_ready(ir_response: dict[str, Any]) -> None:
        _translate_and_persist_localized_response_tools(
            ir_response,
            route,
            tool_store=tool_store,
            persistence=persistence,
            state_scope=scope,
            capabilities=tool_capabilities,
            read_cache=read_cache,
            exec_projections=exec_projections,
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
    terminal_outcome = "cancelled"
    stream_error: str | None = "Stream closed before completion"
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
        terminal_outcome = "completed"
        stream_error = None
    except BaseException as exc:
        terminal_outcome, stream_error = _stream_terminal_failure(exc)
        raise
    finally:
        _finalize_stream_profile(
            entry_id=entry_id,
            request_log=request_log,
            trace=trace,
            t0=t0,
            chunk_count=chunk_count,
            terminal_outcome=terminal_outcome,
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
    terminal_outcome = "cancelled"
    stream_error: str | None = "Stream closed before completion"
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
        terminal_outcome = "completed"
        stream_error = None
    except BaseException as exc:
        terminal_outcome, stream_error = _stream_terminal_failure(exc)
        raise
    finally:
        _finalize_stream_profile(
            entry_id=entry_id,
            request_log=request_log,
            trace=trace,
            t0=t0,
            chunk_count=chunk_count,
            terminal_outcome=terminal_outcome,
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
    web_search_client: TavilySearchClient | None,
) -> tuple[dict[str, Any], WebSearchRuntime | None]:
    if not _uses_responses_chat_bridge(route):
        return body, None
    if route_tool_state(route, "hosted.web_search", "modified") != "modified":
        return body, None

    runtime = build_web_search_runtime(
        body,
        profile_search_config(route, WEB_SEARCH_PROFILE_ITEM_ID),
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
    terminal_outcome: str,
    stream_error: str | None,
    ttfb_ms: float | None,
    passthrough: bool = False,
) -> None:
    stream_complete = terminal_outcome == "completed"
    if entry_id and request_log is not None:
        stream_profile: dict[str, Any] = {
            "stream_duration_ms": round((time.monotonic() - t0) * 1000, 2),
            "stream_chunks": chunk_count,
            "stream_complete": stream_complete,
            "stream_outcome": terminal_outcome,
        }
        if passthrough:
            stream_profile["stream_passthrough"] = True
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
                "stream_complete": stream_complete,
                "stream_outcome": terminal_outcome,
                "stream_error": stream_error,
                "ttfb_ms": ttfb_ms,
                "passthrough": passthrough,
            },
        )


def _stream_terminal_failure(exc: BaseException) -> tuple[str, str]:
    """Classify early close/cancellation separately from provider failures."""
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled", "Stream cancelled or client disconnected"
    if isinstance(exc, GeneratorExit):
        return "cancelled", "Stream closed before completion"
    return "error", str(exc)


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
    terminal_outcome = "cancelled"
    stream_error: str | None = "Stream closed before completion"
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
        terminal_outcome = "completed"
        stream_error = None
    except BaseException as exc:
        terminal_outcome, stream_error = _stream_terminal_failure(exc)
        raise
    finally:
        _finalize_stream_profile(
            entry_id=entry_id,
            request_log=request_log,
            trace=trace,
            t0=t0,
            chunk_count=chunk_count,
            terminal_outcome=terminal_outcome,
            stream_error=stream_error,
            ttfb_ms=ttfb_ms,
            passthrough=True,
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
    upstream_error_log_state: UpstreamErrorLogState | None,
    body_log_state: BodyLogState | None,
    inbound_wire_request: InboundWireRequest | None,
) -> tuple[Response | StreamingResponse, dict[str, Any]]:
    """Handle same-protocol Responses streaming passthrough."""
    profile: dict[str, Any] = {}
    log_original_request(body, state=body_log_state)
    request_id = extra_headers.get("x-request-id") if extra_headers else None
    trace = _create_stream_trace_logger(
        stream_trace_state,
        request_id=request_id,
        request_log_id=entry_id,
        model=model,
        route=route,
    )
    trace_started_at = time.monotonic()
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

    t_connect = time.perf_counter()
    try:
        send_kwargs: dict[str, Any] = {"extra_headers": extra_headers}
        if (
            inbound_wire_request is not None
            and body.get("stream") is True
            and inbound_wire_request.matches(body)
        ):
            send_kwargs.update(
                wire_body=inbound_wire_request.body,
                wire_headers=inbound_wire_request.headers,
            )
            profile["wire_passthrough"] = True
        stream = await transport.send_streaming(
            provider_info,
            route.target_provider,
            body,
            model,
            **send_kwargs,
        )
    except UpstreamConnectionError as exc:
        profile["stream_connect_ms"] = round(
            (time.perf_counter() - t_connect) * 1000, 2
        )
        error_msg = str(exc)
        if trace is not None:
            trace.log(
                "upstream_connection_error",
                {
                    "status_code": 502,
                    "error": error_msg,
                    "error_phase": "stream_header",
                    "upstream_url": str(provider_info.base_url),
                },
            )
        _finalize_stream_profile(
            entry_id=entry_id,
            request_log=request_log,
            trace=trace,
            t0=trace_started_at,
            chunk_count=0,
            terminal_outcome="error",
            stream_error=error_msg,
            ttfb_ms=None,
            passthrough=True,
        )
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
        if trace is not None:
            trace.log(
                "upstream_error",
                {
                    "status_code": stream.status_code,
                    "error": error_text,
                    "error_phase": "stream_header",
                    "upstream_url": str(provider_info.base_url),
                },
            )
        _finalize_stream_profile(
            entry_id=entry_id,
            request_log=request_log,
            trace=trace,
            t0=trace_started_at,
            chunk_count=0,
            terminal_outcome="error",
            stream_error=error_text,
            ttfb_ms=None,
            passthrough=True,
        )
        log_upstream_error(
            stream.status_code,
            error_text,
            endpoint=str(route.target_provider),
            is_streaming=True,
            state=upstream_error_log_state,
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


async def handle_streaming(  # noqa: C901
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
    state_scope: GatewayStateScope | None = None,
    codex_window_id: str | None = None,
    stream_trace_state: StreamTraceState | None = None,
    upstream_error_log_state: UpstreamErrorLogState | None = None,
    body_log_state: BodyLogState | None = None,
    image_fetch_workers: ImageFetchWorkerPool | None = None,
    web_search_client: TavilySearchClient | None = None,
    inbound_wire_request: InboundWireRequest | None = None,
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
    model = body.get("model", "")
    scope, store, tool_store = _resolve_state_stores(
        route=route,
        model=model,
        state_scope=state_scope,
        metadata_store=metadata_store,
        codex_tool_store=codex_tool_store,
    )
    persistent_mappings: list[LocalizedToolMapping] = []
    used_mapping_call_ids: set[str] = set()
    profile: dict[str, Any] = {}
    (
        body,
        compaction_response,
        compaction_profile,
    ) = await _prepare_codex_compaction_request(
        route=route,
        provider_info=provider_info,
        body=body,
        transport=transport,
        metadata_store=metadata_store,
        codex_tool_store=codex_tool_store,
        extra_headers=extra_headers,
        persistence=persistence,
        state_scope=scope,
        codex_window_id=codex_window_id,
        image_fetch_workers=image_fetch_workers,
        stream=True,
    )
    profile.update(compaction_profile)
    if compaction_response is not None:
        return compaction_response, profile
    # model was already injected into body by app.py
    original_body = body
    body = _apply_tool_adaptation(body, route)
    body, web_search_runtime = _prepare_web_search_runtime_and_body(
        route=route,
        body=body,
        web_search_client=web_search_client,
    )
    source_tool_capabilities = _source_tool_capabilities_after_profile(
        original_body, body, route
    )
    if is_responses_passthrough(route):
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
            upstream_error_log_state=upstream_error_log_state,
            body_log_state=body_log_state,
            inbound_wire_request=inbound_wire_request,
        )

    log_original_request(body, state=body_log_state)
    image_fetch_cancellation = ImageFetchCancellation()
    image_fetch_policy = ImageFetchPolicy(
        proxy_url=provider_info.proxy_url,
        cancellation=image_fetch_cancellation,
    )
    pipeline = ConversionPipeline(
        route.source_provider,
        route.target_provider,
        route.shim_name,
        upstream_model=model,
        input_modalities=route.input_modalities,
        reasoning_mapping=None,
        provider_name=route.provider_name,
        conversion_options={
            "image_fetch_policy": image_fetch_policy,
        },
    )

    # Phase 1+2: Source → IR → Target
    def _on_request_ir_ready(ir_request: dict[str, Any]) -> None:
        store.inject_into_request(ir_request)

    try:
        target_body = await _convert_request(
            pipeline,
            route,
            body,
            _on_request_ir_ready,
            image_fetch_workers=image_fetch_workers,
            image_fetch_policy=image_fetch_policy,
        )
    except (ImageWorkerCapacityError, ImageWorkerTimeoutError, ConversionError) as exc:
        return _conversion_failure_response(route.source_provider, exc), profile
    if should_localize_code_tools(route):
        persistent_mappings = _load_persistent_tool_mappings(
            persistence,
            state_scope=scope,
        )
    target_body = _apply_converted_request_tool_adaptation(
        target_body,
        route,
        codex_tool_store=tool_store if not scope.persistent else None,
        persistent_mappings=persistent_mappings,
        used_mapping_call_ids=used_mapping_call_ids,
        capabilities=source_tool_capabilities,
    )
    tool_capabilities = _pop_tool_localization_capabilities(target_body)
    read_cache = _pop_read_output_cache(target_body)
    exec_projections = _pop_exec_tool_projections(target_body)

    profile.update(pipeline.profile)

    log_ir_request(pipeline.ir_request, state=body_log_state)
    if pipeline.warnings:
        logger.warning("Conversion warnings: %s", pipeline.warnings)

    log_converted_request(target_body, state=body_log_state)

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
        state_scope=scope,
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
            state=upstream_error_log_state,
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
        ttl_hours = DEFAULT_TOOL_CALL_CACHE_TTL_HOURS

        def _persist_stream_mapping(mapping: LocalizedToolMapping) -> None:
            _persist_tool_mapping(
                persistence,
                state_scope=scope,
                ttl_hours=ttl_hours,
                mapping=mapping,
            )

        stream_transformer = LocalizedToolCallStreamTransformer(
            store=tool_store if not scope.persistent else None,
            on_mapping=_persist_stream_mapping,
            capabilities=tool_capabilities,
            read_cache=read_cache,
            use_apply_patch=True,
            exec_projections=exec_projections,
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
