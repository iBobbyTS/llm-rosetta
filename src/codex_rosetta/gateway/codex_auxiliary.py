"""Codex Search and Images endpoint pass-through handlers."""

from __future__ import annotations

import time
from typing import Any

from codex_rosetta._vendor.httpserver import JSONResponse, Response
from codex_rosetta.routing import is_responses_passthrough

from .auth import api_key_principal_var
from .codex_images import (
    IMAGE_ENDPOINTS,
    IMAGEGEN_PROFILE_ITEM_ID,
    CodexImageConfigurationError,
    image_trace_summary,
    profile_image_provider,
)
from .codex_page import StaticPageClient
from .codex_search import (
    CodexSearchExecutionError,
    CodexSearchInvalidRequest,
    CodexSearchNotImplemented,
    codex_search_request_summary,
    execute_local_codex_search,
    should_use_local_codex_search,
)
from .codex_search_references import CodexSearchReferenceStore
from .config import GatewayConfig
from .headers import build_upstream_extra_headers, resolve_request_id
from .logging import record_request_stat
from .proxy import error_response_for_source, extract_model
from .stream_trace import StreamTraceLogger, StreamTraceState
from .tool_profiles import route_tool_state
from .transport import UpstreamConnectionError, UpstreamTransport
from .web_run_sidecar import WebRunBrowserClient, WebRunSidecarHTTPClient
from .web_search import TavilySearchClient

_BROWSER_USE_HINT = 'Consider "Browser Use" skill'


def _native_auxiliary_endpoint_available(
    *,
    native_passthrough: bool,
    upstream_path: str,
    web_run_state: str,
    image_tool_state: str,
) -> bool:
    if upstream_path in IMAGE_ENDPOINTS:
        return native_passthrough and image_tool_state == "passthrough"
    return native_passthrough and (
        upstream_path != "alpha/search" or web_run_state == "passthrough"
    )


def _unavailable_auxiliary_message(
    upstream_path: str,
    *,
    web_run_state: str,
    image_tool_state: str,
) -> str:
    if upstream_path == "alpha/search" and web_run_state == "disabled":
        return "web.run is disabled by the selected Tool Profile"
    if upstream_path in IMAGE_ENDPOINTS and image_tool_state == "disabled":
        return "image_gen.imagegen is disabled by the selected Tool Profile"
    return (
        f"POST /v1/{upstream_path} is only implemented for OpenAI Responses providers"
    )


def _log_profile_image_request(
    trace: StreamTraceLogger | None,
    *,
    enabled: bool,
    upstream_path: str,
    provider_info: Any,
) -> None:
    if enabled and trace is not None:
        trace.log(
            "codex_image_request",
            image_trace_summary(upstream_path, provider_info),
        )


def _log_profile_image_response(
    trace: StreamTraceLogger | None,
    *,
    enabled: bool,
    status_code: int,
) -> None:
    if enabled and trace is not None:
        trace.log("codex_image_response", {"status_code": status_code})


async def handle_codex_auxiliary(
    request: Any,
    config: GatewayConfig,
    upstream_path: str,
    *,
    search_client: TavilySearchClient | None = None,
    page_client: StaticPageClient | None = None,
    browser_client: WebRunBrowserClient | None = None,
) -> Response:
    """Handle Codex Search locally when configured, or pass auxiliaries through."""

    try:
        request_id = resolve_request_id(request.headers.get("x-request-id"))
    except ValueError as exc:
        return error_response_for_source("openai_responses", 400, str(exc))

    try:
        body: dict[str, Any] = request.json()
    except Exception:
        return error_response_for_source("openai_responses", 400, "Invalid JSON body")
    if not isinstance(body, dict):
        return error_response_for_source(
            "openai_responses", 400, "JSON body must be an object"
        )

    try:
        model = extract_model("openai_responses", body)
    except ValueError as exc:
        return error_response_for_source("openai_responses", 400, str(exc))
    if not model:
        return error_response_for_source(
            "openai_responses", 400, "Missing 'model' in request body"
        )

    try:
        route, provider_info = config.resolve("openai_responses", model)
    except KeyError:
        configured = ", ".join(sorted(config.models.keys()))
        return JSONResponse(
            {
                "error": {
                    "message": (
                        f"Unknown model: '{model}'. Configured models: {configured}"
                    ),
                    "type": "model_not_found",
                    "code": None,
                }
            },
            status_code=404,
        )

    native_passthrough = is_responses_passthrough(route)
    web_run_state = route_tool_state(route, "namespace.web.run", "modified")
    image_tool_state = route_tool_state(route, IMAGEGEN_PROFILE_ITEM_ID, "disabled")
    web_run_mapping = web_run_state == "modified"
    web_run_config = config.web_search
    resolved_browser_client = browser_client or _configured_browser_client(config)
    use_profile_images = (
        upstream_path in IMAGE_ENDPOINTS and image_tool_state == "modified"
    )
    use_local_search = (
        upstream_path == "alpha/search"
        and web_run_mapping
        and should_use_local_codex_search(
            body,
            web_run_config,
            native_passthrough_available=False,
            browser_available=resolved_browser_client is not None,
        )
    )
    native_endpoint_available = _native_auxiliary_endpoint_available(
        native_passthrough=native_passthrough,
        upstream_path=upstream_path,
        web_run_state=web_run_state,
        image_tool_state=image_tool_state,
    )
    if (
        not native_endpoint_available
        and not use_local_search
        and not use_profile_images
    ):
        message = _unavailable_auxiliary_message(
            upstream_path,
            web_run_state=web_run_state,
            image_tool_state=image_tool_state,
        )
        return error_response_for_source(
            "openai_responses",
            501,
            _with_browser_use_hint(message),
        )

    active_provider_info = provider_info
    if use_profile_images:
        try:
            active_provider_info = profile_image_provider(
                route,
                proxy_url=config.proxy,
            )
        except CodexImageConfigurationError as exc:
            return error_response_for_source("openai_responses", 400, str(exc))
    if route.upstream_model:
        body["model"] = route.upstream_model

    resolved_model = str(body.get("model") or route.upstream_model or model)
    record_request_stat(resolved_model)
    upstream_url = f"{active_provider_info.base_url}/{upstream_path}"
    transport: UpstreamTransport = request.app.transport
    extra_headers = build_upstream_extra_headers(request, request_id)
    trace = _create_auxiliary_trace(
        request,
        request_id=request_id,
        model=model,
        route=route,
    )
    started_at = time.monotonic()
    status_code = 500
    error_detail: str | None = None

    try:
        if use_local_search:
            reference_store, principal_id = _search_reference_context(request)
            response, status_code, error_detail = await _handle_local_search(
                trace,
                body,
                web_run_config,
                search_client,
                page_client,
                resolved_browser_client,
                reference_store,
                principal_id,
            )
            return response

        _log_profile_image_request(
            trace,
            enabled=use_profile_images,
            upstream_path=upstream_path,
            provider_info=active_provider_info,
        )

        response = await transport.send_passthrough(
            active_provider_info,
            upstream_url,
            body,
            extra_headers=extra_headers,
        )
        status_code = response.status_code
        if response.is_error:
            error_detail = response.error_text
        _log_profile_image_response(
            trace,
            enabled=use_profile_images,
            status_code=response.status_code,
        )
        return Response(
            body=response.raw_content,
            status_code=response.status_code,
            content_type="application/json",
        )
    except UpstreamConnectionError as exc:
        error_detail = str(exc)
        status_code = 502
        return error_response_for_source(
            "openai_responses", 502, f"Upstream request failed: {exc}"
        )
    except Exception as exc:
        error_detail = str(exc)
        raise
    finally:
        from .app import _record_telemetry

        _record_telemetry(
            request,
            model=model,
            source_provider="openai_responses",
            target_provider=route.target_provider,
            provider_name=route.provider_name,
            is_stream=False,
            status_code=status_code,
            duration_ms=(time.monotonic() - started_at) * 1000,
            error_detail=error_detail,
        )


def _search_reference_context(
    request: Any,
) -> tuple[CodexSearchReferenceStore | None, str | None]:
    store = getattr(request.app, "codex_search_reference_store", None)
    return (
        store if isinstance(store, CodexSearchReferenceStore) else None,
        api_key_principal_var.get(),
    )


def _configured_browser_client(config: GatewayConfig) -> WebRunBrowserClient | None:
    if not config.web_run_sidecar_url or not config.web_run_sidecar_token:
        return None
    return WebRunSidecarHTTPClient(
        config.web_run_sidecar_url,
        config.web_run_sidecar_token,
        timeout=config.web_run_sidecar_timeout,
        search_provider=config.web_search["provider"],
    )


async def _handle_local_search(
    trace: StreamTraceLogger | None,
    body: dict[str, Any],
    web_search_config: dict[str, Any],
    search_client: TavilySearchClient | None,
    page_client: StaticPageClient | None,
    browser_client: WebRunBrowserClient | None,
    reference_store: CodexSearchReferenceStore | None,
    principal_id: str | None,
) -> tuple[Response, int, str | None]:
    if trace is not None:
        trace.log("codex_search_request", codex_search_request_summary(body))
    try:
        result = await execute_local_codex_search(
            body,
            web_search_config,
            client=search_client,
            page_client=page_client,
            browser_client=browser_client,
            reference_store=reference_store,
            principal_id=principal_id,
        )
    except CodexSearchNotImplemented as exc:
        error = str(exc)
        if trace is not None:
            trace.log("codex_search_not_implemented", {"error": error})
        return _not_implemented_response(error), 501, error
    except CodexSearchInvalidRequest as exc:
        error = str(exc)
        if trace is not None:
            trace.log("codex_search_invalid_request", {"error": error})
        return error_response_for_source("openai_responses", 400, error), 400, error
    except CodexSearchExecutionError as exc:
        error = str(exc)
        if trace is not None:
            trace.log("codex_search_execution_error", {"error": error})
        return error_response_for_source("openai_responses", 502, error), 502, error

    if trace is not None:
        trace.log("codex_search_response", result.trace_summary())
    return JSONResponse(result.response_body()), 200, None


def _not_implemented_response(message: str) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": _with_browser_use_hint(message),
                "type": "not_implemented_error",
                "code": "not_implemented",
            }
        },
        status_code=501,
    )


def _with_browser_use_hint(message: str) -> str:
    return f"{message.rstrip('.')}. {_BROWSER_USE_HINT}"


def _create_auxiliary_trace(
    request: Any,
    *,
    request_id: str,
    model: str,
    route: Any,
) -> StreamTraceLogger | None:
    state = getattr(request.app, "stream_trace_state", None)
    if not isinstance(state, StreamTraceState):
        return None
    return state.create_logger(
        request_id=request_id,
        request_log_id=None,
        model=model,
        source_provider=route.source_provider,
        target_provider=route.target_provider,
        provider_name=route.provider_name,
    )
