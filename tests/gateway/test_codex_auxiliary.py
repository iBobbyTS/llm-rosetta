"""Tests for Codex Search and Images auxiliary endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codex_rosetta.gateway.codex_auxiliary import handle_codex_auxiliary
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.transport import UpstreamConnectionError
from codex_rosetta.gateway.transport._base import UpstreamResponse


ENDPOINTS = ("alpha/search", "images/generations", "images/edits")


def _make_config(
    api_type: str = "responses_passthrough",
    *,
    upstream_model: str | None = "gpt-image-2",
) -> GatewayConfig:
    provider_by_api_type = {
        "responses_passthrough": "openai",
        "responses_rosetta": "openai",
        "chat": "openai",
        "anthropic": "anthropic",
        "google": "google",
    }
    model: dict[str, Any] = {}
    if upstream_model is not None:
        model["upstream_model"] = upstream_model
    return GatewayConfig(
        {
            "providers": {
                "upstream": {
                    "provider": provider_by_api_type[api_type],
                    "api_type": api_type,
                    "api_key": "upstream-key",
                    "base_url": "https://upstream.example/v1",
                }
            },
            "model_groups": {
                "codex": {
                    "provider": "upstream",
                    "type": "llm",
                    "models": {"gateway-model": model},
                }
            },
            "server": {
                "admin_password": "test-admin-password",
                "api_keys": [
                    {
                        "id": "test-client",
                        "label": "Test client",
                        "key": "gateway-key",
                    }
                ],
            },
        }
    )


def _make_request(body: Any) -> MagicMock:
    request = MagicMock()
    request.json.return_value = body
    request.headers = {"user-agent": "codex-cli/test", "x-request-id": "req-1"}
    request.app = MagicMock()
    request.app.metrics = None
    request.app.request_log = None
    request.app.transport.send_passthrough = AsyncMock(
        return_value=UpstreamResponse(
            status_code=202,
            body={"accepted": True},
            raw_content=b'{"accepted":true}',
        )
    )
    return request


@pytest.mark.parametrize("upstream_path", ENDPOINTS)
def test_responses_passthrough_forwards_each_endpoint(upstream_path: str) -> None:
    config = _make_config()
    body = {"model": "gateway-model", "prompt": "draw a fox"}
    request = _make_request(body)

    response = asyncio.run(handle_codex_auxiliary(request, config, upstream_path))

    assert response.status_code == 202
    assert response.body == b'{"accepted":true}'
    provider_info, url, forwarded_body = (
        request.app.transport.send_passthrough.call_args.args
    )
    assert provider_info.base_url == "https://upstream.example/v1"
    assert url == f"https://upstream.example/v1/{upstream_path}"
    assert forwarded_body == {
        "model": "gpt-image-2",
        "prompt": "draw a fox",
    }
    extra_headers = request.app.transport.send_passthrough.call_args.kwargs[
        "extra_headers"
    ]
    assert extra_headers == {
        "x-request-id": "req-1",
        "User-Agent": "codex-cli/test",
    }


@pytest.mark.parametrize(
    "api_type", ["responses_rosetta", "chat", "anthropic", "google"]
)
@pytest.mark.parametrize("upstream_path", ENDPOINTS)
def test_non_passthrough_modes_return_not_implemented(
    api_type: str, upstream_path: str
) -> None:
    config = _make_config(api_type)
    request = _make_request({"model": "gateway-model", "prompt": "test"})

    response = asyncio.run(handle_codex_auxiliary(request, config, upstream_path))

    assert response.status_code == 501
    payload = json.loads(response.body)
    assert payload["error"]["type"] == "invalid_request_error"
    assert (
        "only implemented for OpenAI Responses (Pass through)"
        in payload["error"]["message"]
    )
    request.app.transport.send_passthrough.assert_not_awaited()


@pytest.mark.parametrize("invalid_body", [[], "text", 1, True])
def test_auxiliary_endpoint_rejects_non_object_json(invalid_body: Any) -> None:
    request = _make_request(invalid_body)

    response = asyncio.run(
        handle_codex_auxiliary(request, _make_config(), "alpha/search")
    )

    assert response.status_code == 400
    assert json.loads(response.body)["error"]["message"] == (
        "JSON body must be an object"
    )
    request.app.transport.send_passthrough.assert_not_awaited()


def test_auxiliary_endpoint_returns_model_not_found() -> None:
    request = _make_request({"model": "missing"})

    response = asyncio.run(
        handle_codex_auxiliary(request, _make_config(), "alpha/search")
    )

    assert response.status_code == 404
    assert json.loads(response.body)["error"]["type"] == "model_not_found"
    request.app.transport.send_passthrough.assert_not_awaited()


def test_auxiliary_endpoint_maps_upstream_connection_error() -> None:
    request = _make_request({"model": "gateway-model", "q": "latest news"})
    request.app.transport.send_passthrough.side_effect = UpstreamConnectionError(
        "connection refused"
    )

    response = asyncio.run(
        handle_codex_auxiliary(request, _make_config(), "alpha/search")
    )

    assert response.status_code == 502
    payload = json.loads(response.body)
    assert payload["error"]["message"] == (
        "Upstream request failed: connection refused"
    )
