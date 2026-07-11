"""Codex Search and Images endpoint pass-through handlers."""

from __future__ import annotations

import time
from typing import Any

from codex_rosetta._vendor.httpserver import JSONResponse, Response
from codex_rosetta.routing import is_openai_responses_passthrough

from .config import GatewayConfig
from .headers import build_upstream_extra_headers, resolve_request_id
from .logging import record_request_stat
from .proxy import error_response_for_source, extract_model
from .transport import UpstreamConnectionError, UpstreamTransport


async def handle_codex_auxiliary(
    request: Any,
    config: GatewayConfig,
    upstream_path: str,
) -> Response:
    """Pass a Codex Search or Images request to a Responses provider."""

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

    if not is_openai_responses_passthrough(route):
        return error_response_for_source(
            "openai_responses",
            501,
            (
                f"POST /v1/{upstream_path} is only implemented for "
                "OpenAI Responses (Pass through) providers"
            ),
        )

    if route.upstream_model:
        body["model"] = route.upstream_model

    resolved_model = route.upstream_model or model
    record_request_stat(resolved_model)
    upstream_url = f"{provider_info.base_url}/{upstream_path}"
    transport: UpstreamTransport = request.app.transport
    extra_headers = build_upstream_extra_headers(request, request_id)
    started_at = time.monotonic()
    status_code = 500
    error_detail: str | None = None

    try:
        response = await transport.send_passthrough(
            provider_info,
            upstream_url,
            body,
            extra_headers=extra_headers,
        )
        status_code = response.status_code
        if response.is_error:
            error_detail = response.error_text
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
