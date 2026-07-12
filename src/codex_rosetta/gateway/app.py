"""codex-rosetta Gateway — HTTP application and route handlers."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any

from codex_rosetta._vendor.httpserver import (
    App,
    JSONResponse,
    Response,
    StreamingResponse,
)
from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.observability.error_dump import dump_error

from .auth import (
    AuthState,
    api_key_label_var,
    api_key_principal_var,
    create_auth_hook,
)
from .config import GatewayConfig
from .codex_auxiliary import handle_codex_auxiliary as _handle_codex_auxiliary
from .cors import apply_cors_headers, is_admin_origin_allowed, is_admin_path
from .embeddings import handle_embeddings as _handle_embeddings
from .headers import (
    build_upstream_extra_headers,
    generate_request_id,
    resolve_request_id,
)
from .health import build_health_payload, build_readiness_payload
from .image_workers import ImageFetchWorkerPool
from .logging import (
    BodyLogState,
    UpstreamErrorLogState,
    get_logger,
    record_request_stat,
)

from .proxy import (
    ProviderMetadataCapacityError,
    ProviderMetadataStore,
    ToolSearchCapacityError,
    WindowToolSearchStore,
    close_resources,
    detect_stream_request,
    error_response_for_source,
    extract_model,
    handle_non_streaming,
    handle_streaming,
    normalize_codex_window_id,
    validate_model_id,
)
from .state_scope import GatewayStateScope
from .tool_adaptation import CodexToolLocalizationStore

logger = get_logger()

_TOOL_CALL_CACHE_CLEANUP_INTERVAL = 3600
_INBOUND_REQUEST_LINE_TIMEOUT_SECONDS = 5.0
_INBOUND_HEADER_TIMEOUT_SECONDS = 10.0
_INBOUND_BODY_TIMEOUT_SECONDS = 30.0
_INBOUND_MAX_CONCURRENT_REQUEST_PARSES = 64


def _record_request_log_entry(
    request: Any,
    *,
    model: str,
    source_provider: ProviderType,
    target_provider: ProviderType,
    provider_name: str,
    is_stream: bool,
    status_code: int,
    duration_ms: float,
    error_detail: str | None,
    profile: dict[str, Any] | None = None,
    entry_id_override: str | None = None,
) -> str | None:
    """Safely add one request-log entry without affecting proxy delivery."""
    request_log = getattr(request.app, "request_log", None)
    if request_log is None:
        return None
    try:
        from dataclasses import replace as _dc_replace

        from codex_rosetta.observability import RequestLogEntry

        entry = RequestLogEntry.create(
            model=model,
            source_provider=source_provider,
            target_provider=target_provider,
            target_provider_name=provider_name,
            is_stream=is_stream,
            status_code=status_code,
            duration_ms=duration_ms,
            error_detail=error_detail,
            api_key_label=api_key_label_var.get(),
            client_ip=_extract_client_ip(request),
            profile=profile,
        )
        if entry_id_override:
            entry = _dc_replace(entry, id=entry_id_override)
        request_log.add(entry)
        return entry.id
    except Exception as exc:
        logger.warning("Failed to record request log entry: %s", exc)
        return None


def _record_telemetry(
    request: Any,
    *,
    model: str,
    source_provider: ProviderType,
    target_provider: ProviderType,
    provider_name: str,
    is_stream: bool,
    status_code: int,
    duration_ms: float,
    error_detail: str | None,
    profile: dict[str, Any] | None = None,
    entry_id_override: str | None = None,
) -> str | None:
    """Record metrics and request log entry after a proxy call completes.

    Args:
        entry_id_override: Pre-generated entry ID for streaming requests.
            When provided, the entry is created with this ID so the
            stream generator can write back profile data by ID.

    Returns:
        The request log entry ID, or ``None`` if no request log is
        configured.
    """
    metrics = getattr(request.app, "metrics", None)
    if metrics:
        try:
            if is_stream:
                metrics.active_streams -= 1
            metrics.record_request(
                model=model,
                source=source_provider,
                target=target_provider,
                status_code=status_code,
                duration_ms=duration_ms,
                is_stream=is_stream,
                provider_name=provider_name,
                error_detail=error_detail,
            )
        except Exception as exc:
            logger.warning("Failed to record request metrics: %s", exc)

    return _record_request_log_entry(
        request,
        model=model,
        source_provider=source_provider,
        target_provider=target_provider,
        provider_name=provider_name,
        is_stream=is_stream,
        status_code=status_code,
        duration_ms=duration_ms,
        error_detail=error_detail,
        profile=profile,
        entry_id_override=entry_id_override,
    )


def _finalize_stream_telemetry(
    request: Any,
    *,
    entry_id: str,
    model: str,
    source_provider: ProviderType,
    target_provider: ProviderType,
    provider_name: str,
    status_code: int,
    duration_ms: float,
    error_detail: str | None,
) -> None:
    """Safely record the one terminal outcome of an open stream."""
    metrics = getattr(request.app, "metrics", None)
    if metrics:
        try:
            metrics.active_streams -= 1
            metrics.record_request(
                model=model,
                source=source_provider,
                target=target_provider,
                status_code=status_code,
                duration_ms=duration_ms,
                is_stream=True,
                provider_name=provider_name,
                error_detail=error_detail,
            )
        except Exception as exc:
            logger.warning("Failed to finalize stream metrics: %s", exc)

    request_log = getattr(request.app, "request_log", None)
    if request_log is not None:
        profile_update: dict[str, Any] = {
            "stream_complete": status_code < 400,
        }
        if error_detail is not None:
            profile_update["stream_error"] = error_detail[:500]
        try:
            request_log.update_result(
                entry_id,
                status_code=status_code,
                duration_ms=duration_ms,
                error_detail=error_detail,
                profile_update=profile_update,
            )
        except Exception as exc:
            logger.warning("Failed to finalize stream request log: %s", exc)


class _InstrumentedStream:
    """Async iterator that finalizes stream telemetry exactly once."""

    def __init__(
        self,
        source: AsyncIterator[bytes | str],
        *,
        success_status: int,
        finalize: Callable[[int, str | None], None],
    ) -> None:
        self._source = source
        self._iterator = source.__aiter__()
        self._success_status = success_status
        self._finalize_callback = finalize
        self._finished = False
        self._source_closed = False

    def __aiter__(self) -> _InstrumentedStream:
        return self

    async def __anext__(self) -> bytes | str:
        try:
            return await self._iterator.__anext__()
        except StopAsyncIteration:
            self._source_closed = True
            self._finish(self._success_status, None)
            raise
        except asyncio.CancelledError:
            try:
                await self._close_source()
            except BaseException:
                logger.debug("Failed to close cancelled stream", exc_info=True)
            finally:
                self._finish(499, "Stream cancelled or client disconnected")
            raise
        except Exception as exc:
            try:
                await self._close_source()
            except BaseException:
                logger.debug("Failed to close errored stream", exc_info=True)
            finally:
                self._finish(502, str(exc))
            raise

    async def aclose(self) -> None:
        """Close an incomplete stream and record a client-disconnect outcome."""
        if self._finished:
            return
        status_code = 499
        error_detail = "Stream closed before completion"
        try:
            await self._close_source()
        except asyncio.CancelledError:
            error_detail = "Stream cancelled or client disconnected"
            raise
        except Exception as exc:
            status_code = 502
            error_detail = str(exc)
            logger.debug("Failed to close stream source", exc_info=True)
        finally:
            self._finish(status_code, error_detail)

    async def _close_source(self) -> None:
        if self._source_closed:
            return
        self._source_closed = True
        aclose = getattr(self._iterator, "aclose", None)
        if aclose is None and self._iterator is not self._source:
            aclose = getattr(self._source, "aclose", None)
        if aclose is not None:
            await aclose()

    def _finish(self, status_code: int, error_detail: str | None) -> None:
        if self._finished:
            return
        self._finished = True
        self._finalize_callback(status_code, error_detail)


def _response_error_detail(response: Response | StreamingResponse) -> str | None:
    """Decode a non-streaming error response body for telemetry."""
    if response.status_code < 400 or not hasattr(response, "body"):
        return None
    body = response.body
    return body.decode("utf-8", errors="replace") if isinstance(body, bytes) else None


def _instrument_stream_response(
    request: Any,
    response: StreamingResponse,
    *,
    entry_id: str,
    request_id: str,
    model: str,
    source_provider: ProviderType,
    target_provider: ProviderType,
    provider_name: str,
    profile: dict[str, Any] | None,
    profiler: Any,
    started_at: float,
    on_finish: Callable[[], None] | None = None,
) -> None:
    """Attach request logging and terminal telemetry to an open stream."""
    _record_request_log_entry(
        request,
        model=model,
        source_provider=source_provider,
        target_provider=target_provider,
        provider_name=provider_name,
        is_stream=True,
        status_code=response.status_code,
        duration_ms=(time.monotonic() - started_at) * 1000,
        error_detail=None,
        profile=profile,
        entry_id_override=entry_id,
    )

    def _finalize_stream(status: int, stream_error: str | None) -> None:
        try:
            duration_ms = (time.monotonic() - started_at) * 1000
            _try_stop_profiler(
                profiler,
                request.app,
                request_id=request_id,
                model=model,
                source=source_provider,
                target=target_provider,
                is_stream=True,
                duration_ms=duration_ms,
            )
            _finalize_stream_telemetry(
                request,
                entry_id=entry_id,
                model=model,
                source_provider=source_provider,
                target_provider=target_provider,
                provider_name=provider_name,
                status_code=status,
                duration_ms=duration_ms,
                error_detail=stream_error,
            )
            logger.info("[%s] stream finalized status=%s", request_id, status)
        finally:
            if on_finish is not None:
                on_finish()

    response._generator = _InstrumentedStream(
        response._generator,
        success_status=response.status_code,
        finalize=_finalize_stream,
    )


def _clear_request_local_state(
    scope: GatewayStateScope,
    *,
    metadata_store: ProviderMetadataStore,
    codex_tool_store: CodexToolLocalizationStore,
    window_tool_search_store: WindowToolSearchStore,
) -> None:
    """Clear every in-memory store owned by one non-persistent request scope."""
    if scope.persistent:
        return
    for name, store in (
        ("provider metadata", metadata_store),
        ("tool localization", codex_tool_store),
        ("deferred tool search", window_tool_search_store),
    ):
        try:
            store.scoped(scope).clear()
        except Exception:
            logger.warning(
                "Failed to clear request-local %s state", name, exc_info=True
            )


def _mark_stream_active(request: Any, *, is_stream: bool) -> None:
    """Increment the active-stream gauge for an accepted streaming request."""
    if not is_stream:
        return
    metrics = getattr(request.app, "metrics", None)
    if metrics:
        metrics.active_streams += 1


def _extract_client_ip(request: Any) -> str | None:
    """Return the direct TCP peer address for request attribution.

    Forwarded client-IP headers remain untrusted until the gateway exposes an
    explicit trusted-proxy allowlist.
    """
    addr = getattr(request, "client_addr", None)
    if addr and isinstance(addr, (tuple, list)) and addr[0]:
        return str(addr[0])
    return None


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _try_start_profiler(app: Any) -> Any | None:
    """Start a per-request deep profiler if profiling is enabled.

    Returns a started DeepProfiler instance, or ``None`` if profiling
    is disabled or pyinstrument is not installed.
    """
    state = getattr(app, "profiler_state", None)
    if state is None or not state.should_profile():
        return None
    try:
        profiler = state.create_profiler()
        profiler.start()
        return profiler
    except RuntimeError:
        # pyinstrument not installed — restore the consumed slot
        state.remaining += 1
        if not state.enabled:
            state.enabled = True
        return None


def _try_stop_profiler(
    profiler: Any,
    app: Any,
    *,
    request_id: str,
    model: str,
    source: str,
    target: str,
    is_stream: bool,
    duration_ms: float,
) -> None:
    """Stop a running deep profiler and store the result."""
    if profiler is None:
        return
    try:
        profiler.stop()
        state = getattr(app, "profiler_state", None)
        if state is not None:
            state.store_result(
                profiler,
                request_id=request_id,
                model=model,
                source=source,
                target=target,
                is_stream=is_stream,
                duration_ms=duration_ms,
            )
    except Exception:
        logger.debug("Failed to store profiling result")


def _proxy_request_id_or_error(
    request: Any, source_provider: ProviderType
) -> str | Response:
    """Resolve a safe request ID or return a source-shaped ingress error."""

    try:
        return resolve_request_id(request.headers.get("x-request-id"))
    except ValueError as exc:
        response = error_response_for_source(source_provider, 400, str(exc))
        response.headers["x-request-id"] = generate_request_id()
        return response


def _apply_route_model_alias(
    body: dict[str, Any], model: str, upstream_model: str | None
) -> tuple[str, str]:
    """Apply an upstream alias and return log/stat model labels."""

    if upstream_model:
        body["model"] = upstream_model
        return f"{model} (upstream={upstream_model})", upstream_model
    return model, model


async def _proxy_handler(
    request: Any,
    source_provider: ProviderType,
    model_override: str | None = None,
    force_stream: bool = False,
) -> Response | StreamingResponse:
    """Shared handler for all proxy endpoints."""
    config: GatewayConfig = request.app.gateway_config

    # Reject untrusted correlation metadata before it can reach logs, traces,
    # persistence, state, response headers, or the upstream transport.
    request_id = _proxy_request_id_or_error(request, source_provider)
    if isinstance(request_id, Response):
        return request_id

    try:
        body: dict[str, Any] = request.json()
    except Exception:
        resp = error_response_for_source(source_provider, 400, "Invalid JSON body")
        resp.headers["x-request-id"] = request_id
        return resp
    if not isinstance(body, dict):
        resp = error_response_for_source(
            source_provider, 400, "JSON body must be an object"
        )
        resp.headers["x-request-id"] = request_id
        return resp

    # Determine model
    try:
        model = (
            validate_model_id(model_override)
            if model_override
            else extract_model(source_provider, body)
        )
        codex_window_id = normalize_codex_window_id(
            request.headers.get("x-codex-window-id")
        )
    except ValueError as exc:
        resp = error_response_for_source(source_provider, 400, str(exc))
        resp.headers["x-request-id"] = request_id
        return resp
    if not model:
        resp = error_response_for_source(
            source_provider, 400, "Missing 'model' in request body"
        )
        resp.headers["x-request-id"] = request_id
        return resp

    # If model came from URL (Google), inject it into body for the converter
    if model_override and "model" not in body:
        body["model"] = model_override

    # Resolve target provider via unified routing
    try:
        route, provider_info = config.resolve(source_provider, model)
    except KeyError:
        configured = ", ".join(sorted(config.models.keys()))
        resp = error_response_for_source(
            source_provider,
            404,
            f"Unknown model: '{model}'. Configured models: {configured}",
        )
        resp.headers["x-request-id"] = request_id
        return resp

    # Model aliases are applied before converter/upstream use while logging
    # preserves both public and upstream identities.
    model_label, stats_model = _apply_route_model_alias(
        body, model, route.upstream_model
    )

    # Determine streaming
    is_stream = force_stream or detect_stream_request(source_provider, body)
    principal_id = api_key_principal_var.get()
    if principal_id is None:
        resp = error_response_for_source(
            source_provider, 401, "Authenticated principal is unavailable"
        )
        resp.headers["x-request-id"] = request_id
        return resp
    state_scope = GatewayStateScope.for_request(
        principal_id=principal_id,
        provider_name=route.provider_name,
        model=model,
        window_id=codex_window_id,
    )

    record_request_stat(stats_model)
    logger.info(
        "[%s] %s -> %s | model=%s stream=%s",
        request_id,
        source_provider,
        route.target_provider,
        model_label,
        is_stream,
    )

    store: ProviderMetadataStore = request.app.metadata_store
    codex_tool_store: CodexToolLocalizationStore = request.app.codex_tool_store
    window_tool_search_store: WindowToolSearchStore = (
        request.app.window_tool_search_store
    )

    # Forward only explicitly supported client headers to upstream.
    extra_headers = build_upstream_extra_headers(request, request_id)

    # --- Metrics instrumentation ---
    _mark_stream_active(request, is_stream=is_stream)

    t0 = time.monotonic()
    status_code = 500
    error_detail: str | None = None
    profile: dict[str, Any] | None = None
    request_log = getattr(request.app, "request_log", None)

    persistence = getattr(request.app, "persistence", None)
    deep_profiler = _try_start_profiler(request.app)
    pre_entry_id: str | None = None
    stream_telemetry_deferred = False
    request_state_cleanup_deferred = False

    try:
        if is_stream:
            # For streaming, we pre-generate the entry_id so the stream
            # generator can write back stream-phase profile after completion.
            pre_entry_id = uuid.uuid4().hex

            response, profile = await handle_streaming(
                route,
                provider_info,
                body,
                transport=request.app.transport,
                metadata_store=store,
                codex_tool_store=codex_tool_store,
                extra_headers=extra_headers,
                entry_id=pre_entry_id,
                request_log=request_log,
                persistence=persistence,
                state_scope=state_scope,
                codex_window_id=codex_window_id,
                window_tool_search_store=window_tool_search_store,
                web_search_config=config.web_search,
                stream_trace_state=getattr(request.app, "stream_trace_state", None),
                upstream_error_log_state=getattr(
                    request.app, "upstream_error_log_state", None
                ),
                body_log_state=getattr(request.app, "body_log_state", None),
                image_fetch_workers=getattr(request.app, "image_fetch_workers", None),
            )
        else:
            pre_entry_id = None
            response, profile = await handle_non_streaming(
                route,
                provider_info,
                body,
                transport=request.app.transport,
                metadata_store=store,
                codex_tool_store=codex_tool_store,
                extra_headers=extra_headers,
                persistence=persistence,
                state_scope=state_scope,
                codex_window_id=codex_window_id,
                window_tool_search_store=window_tool_search_store,
                upstream_error_log_state=getattr(
                    request.app, "upstream_error_log_state", None
                ),
                body_log_state=getattr(request.app, "body_log_state", None),
                image_fetch_workers=getattr(request.app, "image_fetch_workers", None),
            )
        status_code = response.status_code
        error_detail = _response_error_detail(response)
        if isinstance(response, StreamingResponse):
            assert is_stream
            assert pre_entry_id is not None
            _instrument_stream_response(
                request,
                response,
                entry_id=pre_entry_id,
                request_id=request_id,
                model=model,
                source_provider=source_provider,
                target_provider=route.target_provider,
                provider_name=route.provider_name,
                profile=profile,
                profiler=deep_profiler,
                started_at=t0,
                on_finish=lambda: _clear_request_local_state(
                    state_scope,
                    metadata_store=store,
                    codex_tool_store=codex_tool_store,
                    window_tool_search_store=window_tool_search_store,
                ),
            )
            stream_telemetry_deferred = True
            request_state_cleanup_deferred = True
        response.headers["x-request-id"] = request_id
        logger.info("[%s] response status=%s", request_id, status_code)
        return response
    except (ToolSearchCapacityError, ProviderMetadataCapacityError) as exc:
        error_detail = str(exc)
        status_code = 413
        pre_entry_id = None
        logger.warning("[%s] deferred tool-search capacity rejected", request_id)
        resp = error_response_for_source(source_provider, 413, str(exc))
        resp.headers["x-request-id"] = request_id
        return resp
    except Exception as exc:
        error_detail = str(exc)
        logger.exception("[%s] unhandled error in proxy handler", request_id)
        status_code = 500
        pre_entry_id = None
        dump_error(
            persistence,
            request_body=body,
            response_text=error_detail,
            model=model,
            source_provider=source_provider,
            target_provider=route.target_provider,
            provider_name=route.provider_name,
            status_code=500,
            error_phase="conversion",
        )
        resp = error_response_for_source(
            source_provider, 500, f"Internal server error: {exc}"
        )
        resp.headers["x-request-id"] = request_id
        return resp
    finally:
        duration_ms = (time.monotonic() - t0) * 1000
        if not request_state_cleanup_deferred:
            _clear_request_local_state(
                state_scope,
                metadata_store=store,
                codex_tool_store=codex_tool_store,
                window_tool_search_store=window_tool_search_store,
            )
        if not stream_telemetry_deferred:
            _try_stop_profiler(
                deep_profiler,
                request.app,
                request_id=request_id,
                model=model,
                source=source_provider,
                target=route.target_provider,
                is_stream=is_stream,
                duration_ms=duration_ms,
            )

            _record_telemetry(
                request,
                model=model,
                source_provider=source_provider,
                target_provider=route.target_provider,
                provider_name=route.provider_name,
                is_stream=is_stream,
                status_code=status_code,
                duration_ms=duration_ms,
                error_detail=error_detail,
                profile=profile,
                entry_id_override=pre_entry_id,
            )


# --- Endpoint handlers ---


async def handle_openai_chat(request: Any) -> Response | StreamingResponse:
    return await _proxy_handler(request, source_provider="openai_chat")


async def handle_embeddings(request: Any) -> Response:
    config: GatewayConfig = request.app.gateway_config
    return await _handle_embeddings(request, config)


async def handle_codex_search(request: Any) -> Response:
    config: GatewayConfig = request.app.gateway_config
    return await _handle_codex_auxiliary(request, config, "alpha/search")


async def handle_image_generation(request: Any) -> Response:
    config: GatewayConfig = request.app.gateway_config
    return await _handle_codex_auxiliary(request, config, "images/generations")


async def handle_image_edit(request: Any) -> Response:
    config: GatewayConfig = request.app.gateway_config
    return await _handle_codex_auxiliary(request, config, "images/edits")


async def handle_anthropic(request: Any) -> Response | StreamingResponse:
    return await _proxy_handler(request, source_provider="anthropic")


async def handle_openai_responses(request: Any) -> Response | StreamingResponse:
    return await _proxy_handler(request, source_provider="openai_responses")


async def handle_google_genai(
    request: Any, model_path: str = ""
) -> Response | StreamingResponse:
    if model_path.endswith(":streamGenerateContent"):
        model = model_path.removesuffix(":streamGenerateContent")
        return await _proxy_handler(
            request,
            source_provider="google",
            model_override=model,
            force_stream=True,
        )
    elif model_path.endswith(":generateContent"):
        model = model_path.removesuffix(":generateContent")
        return await _proxy_handler(
            request, source_provider="google", model_override=model
        )
    else:
        return Response(
            body='{"error": "Unknown Google GenAI method"}',
            status_code=404,
            content_type="application/json",
        )


async def handle_list_models(request: Any) -> Response:
    """List configured models in a format compatible with OpenAI and Anthropic SDKs."""
    config: GatewayConfig = request.app.gateway_config
    models = sorted(config.models.keys())
    data = []
    for name in models:
        provider_name = config.models[name]
        api_standard = config.provider_types.get(provider_name, "unknown")
        capabilities = config.model_capabilities.get(name, ["text"])
        data.append(
            {
                "id": name,
                "object": "model",
                "created": 0,
                "owned_by": provider_name,
                "api_standard": api_standard,
                "capabilities": capabilities,
                "type": "model",
                "display_name": name,
                "created_at": "1970-01-01T00:00:00Z",
            }
        )
    return JSONResponse(
        {
            "object": "list",
            "data": data,
            "has_more": False,
            "first_id": models[0] if models else None,
            "last_id": models[-1] if models else None,
        }
    )


async def handle_list_models_google(request: Any) -> Response:
    """List configured models in Google GenAI SDK format."""
    config: GatewayConfig = request.app.gateway_config
    models_list = [
        {
            "name": f"models/{name}",
            "displayName": name,
            "supportedGenerationMethods": [
                "generateContent",
                "streamGenerateContent",
            ],
        }
        for name in sorted(config.models.keys())
    ]
    return JSONResponse({"models": models_list})


async def handle_health(request: Any) -> Response:
    """Return operational metrics and per-provider health status.

    Always returns HTTP 200. Use ``status: "degraded"`` in the payload
    to signal provider issues without breaking existing monitors.
    For a 503-on-unhealthy probe use ``/health/ready``.
    """
    metrics = getattr(request.app, "metrics", None)
    if metrics is None:
        return JSONResponse({"status": "ok"})

    return JSONResponse(build_health_payload(metrics), status_code=200)


async def handle_health_live(request: Any) -> Response:
    """Kubernetes liveness probe — always 200 while the process is up."""
    return JSONResponse({"status": "ok"})


async def handle_health_ready(request: Any) -> Response:
    """Kubernetes readiness probe — 200 if all providers are operational, 503 if not."""
    metrics = getattr(request.app, "metrics", None)
    if metrics is None:
        return JSONResponse({"status": "ok"})

    payload, status_code = build_readiness_payload(metrics)
    return JSONResponse(payload, status_code=status_code)


# ---------------------------------------------------------------------------
# Persistence flush helpers
# ---------------------------------------------------------------------------

_FLUSH_METRICS_INTERVAL = 30  # seconds


async def _periodic_flush(app: App) -> None:
    """Periodically flush metrics counters to disk."""
    while True:
        await asyncio.sleep(_FLUSH_METRICS_INTERVAL)
        persistence = getattr(app, "persistence", None)
        if persistence is None:
            continue
        metrics = getattr(app, "metrics", None)
        if metrics is not None:
            try:
                persistence.save_metrics(metrics.export_counters())
            except Exception as exc:
                logger.warning("Failed to flush metrics: %s", exc)


async def _periodic_tool_call_mapping_cleanup(app: App) -> None:
    """Periodically delete expired persistent tool-call mappings."""
    while True:
        await asyncio.sleep(_TOOL_CALL_CACHE_CLEANUP_INTERVAL)
        persistence = getattr(app, "persistence", None)
        if persistence is None:
            continue
        try:
            now = datetime.now(timezone.utc).isoformat()
            persistence.cleanup_expired_tool_call_mappings(now)
        except Exception as exc:
            logger.warning("Failed to clean up tool-call mapping cache: %s", exc)


def _flush_now(app: App) -> None:
    """Final synchronous flush on shutdown."""
    persistence = getattr(app, "persistence", None)
    if persistence is None:
        return

    metrics = getattr(app, "metrics", None)
    if metrics is not None:
        try:
            persistence.save_metrics(metrics.export_counters())
        except Exception as exc:
            logger.warning("Shutdown: failed to flush metrics: %s", exc)

    persistence.close()
    logger.info("Persistence flushed and closed on shutdown")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(config: GatewayConfig, config_path: str | None = None) -> App:
    """Create the httpserver application."""
    from .transport import HttpTransport

    metadata_store = ProviderMetadataStore()
    codex_tool_store = CodexToolLocalizationStore()
    window_tool_search_store = WindowToolSearchStore()
    image_fetch_workers = ImageFetchWorkerPool()
    transport = HttpTransport()

    app = App(
        max_body_size=config.request_body_limit_bytes,
        request_line_timeout=_INBOUND_REQUEST_LINE_TIMEOUT_SECONDS,
        header_timeout=_INBOUND_HEADER_TIMEOUT_SECONDS,
        body_timeout=_INBOUND_BODY_TIMEOUT_SECONDS,
        max_concurrent_request_parses=_INBOUND_MAX_CONCURRENT_REQUEST_PARSES,
    )
    setattr(app, "gateway_config", config)
    app.admin_cors_origins = tuple(config.admin_cors_origins)  # type: ignore

    # --- Routes ---
    app.route("/v1/alpha/search", methods=["POST"])(handle_codex_search)
    app.route("/v1/images/generations", methods=["POST"])(handle_image_generation)
    app.route("/v1/images/edits", methods=["POST"])(handle_image_edit)
    app.route("/v1/embeddings", methods=["POST"])(handle_embeddings)
    app.route("/v1/responses", methods=["POST"])(handle_openai_responses)
    app.route("/v1/models", methods=["GET"])(handle_list_models)
    app.route("/health", methods=["GET"])(handle_health)
    app.route("/health/live", methods=["GET"])(handle_health_live)
    app.route("/health/ready", methods=["GET"])(handle_health_ready)

    # --- Auth ---
    import secrets

    internal_token = f"rsk-internal-{secrets.token_hex(16)}"
    auth_state = AuthState(
        config.api_key_principals,
        config.api_key_labels,
        internal_token,
        admin_password=config.admin_password,
    )
    upstream_error_log_state = UpstreamErrorLogState(
        {*config.token_values, internal_token}
    )
    body_log_state = BodyLogState(
        enabled=config.log_bodies,
        token_values={*config.token_values, internal_token},
    )
    auth_hook = create_auth_hook(auth_state)
    app.before_body(auth_hook)
    app.before_request(auth_hook)

    # Decode Codex's optional request compression only after authentication.
    # The HTTP parser has already applied app.max_body_size to compressed bytes;
    # this hook applies the same live limit to decoded bytes before JSON parsing.
    from .inbound_content_encoding import decode_inbound_zstd

    app.before_request(decode_inbound_zstd)

    # --- CORS ---
    # Admin API endpoints are restricted to same-origin by default.
    # /v1/* proxy endpoints remain open (Access-Control-Allow-Origin: *).
    # The list of allowed origins for admin can be overridden via
    # server.admin_cors_origins in config (default [] = same-origin only).
    @app.after_request
    async def add_cors_headers(request: Any, response: Any) -> Any:
        apply_cors_headers(request, response)
        if is_admin_path(request.path):
            # Restricted CORS for admin endpoints: same-origin only by default,
            # or explicit allow-list via server.admin_cors_origins.
            # Prevent reverse-proxy caching of admin API responses (e.g. Caddy/Souin).
            # Uses the full directive set that Souin recognises as NO-STORE-DIRECTIVE.
            if request.path.startswith("/admin/api/"):
                response.headers.setdefault(
                    "Cache-Control", "no-cache, no-store, must-revalidate"
                )
        return response

    @app.route("/<path:_path>", methods=["OPTIONS"])
    async def cors_preflight(request: Any, _path: str = "") -> Response:
        if is_admin_path(request.path) and not is_admin_origin_allowed(request):
            return JSONResponse(
                {"error": "Admin CORS origin is not allowed"}, status_code=403
            )
        resp = Response(body=b"", status_code=204)
        return apply_cors_headers(request, resp)

    @app.errorhandler(404)
    async def handle_404(request: Any, exc: Any) -> Response:
        resp = JSONResponse({"error": "Not Found"}, status_code=404)
        return apply_cors_headers(request, resp)

    @app.errorhandler(405)
    async def handle_405(request: Any, exc: Any) -> Response:
        resp = JSONResponse({"error": "Method Not Allowed"}, status_code=405)
        return apply_cors_headers(request, resp)

    # --- Admin routes ---
    from .admin import setup_admin
    from .admin.routes import register_admin_routes

    register_admin_routes(app)

    # --- App-level state ---
    app.transport = transport  # type: ignore
    app.metadata_store = metadata_store  # type: ignore
    app.codex_tool_store = codex_tool_store  # type: ignore
    app.window_tool_search_store = window_tool_search_store  # type: ignore
    app.image_fetch_workers = image_fetch_workers  # type: ignore
    app.internal_token = internal_token  # type: ignore
    app.auth_state = auth_state  # type: ignore
    app.upstream_error_log_state = upstream_error_log_state  # type: ignore
    app.body_log_state = body_log_state  # type: ignore

    setup_admin(app, config, config_path)

    return app


async def run_gateway(
    app: App, host: str, port: int, *, socket: str | None = None
) -> None:
    """Start the gateway with lifecycle management."""
    # Expose bind address so admin test tasks can self-call.
    setattr(app, "_bind_host", host)
    setattr(app, "_bind_port", port)
    flush_task = asyncio.create_task(_periodic_flush(app))
    tool_cache_cleanup_task = asyncio.create_task(
        _periodic_tool_call_mapping_cleanup(app)
    )
    try:
        await app._serve(host, port, socket=socket)
    finally:
        for task in (flush_task, tool_cache_cleanup_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        admin_runtime_state = getattr(app, "admin_runtime_state", None)
        if admin_runtime_state is not None:
            await admin_runtime_state.aclose()
        _flush_now(app)
        await close_resources(
            transport=app.transport,  # type: ignore
            metadata_store=app.metadata_store,  # type: ignore
            codex_tool_store=app.codex_tool_store,  # type: ignore
            window_tool_search_store=app.window_tool_search_store,  # type: ignore
            image_fetch_workers=app.image_fetch_workers,  # type: ignore
        )
