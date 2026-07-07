"""Tests for the gateway's agent-facing downstream API surface."""

from __future__ import annotations

import asyncio

import pytest

from llm_rosetta._vendor.httpserver import Request
from llm_rosetta.gateway.app import create_app
from llm_rosetta.gateway.config import GatewayConfig


def _make_app():
    config = GatewayConfig(
        {
            "providers": {
                "test-provider": {
                    "api_key": "sk-test",
                    "base_url": "https://api.example.test/v1",
                    "type": "openai",
                }
            },
            "models": {"gpt-test": "test-provider"},
            "server": {},
        }
    )
    return create_app(config)


def _request(app, path: str, *, body: bytes = b"not json") -> Request:
    return Request(
        method="POST",
        path=path,
        query_string="",
        headers={},
        body=body,
        client_addr=("127.0.0.1", 12345),
        app=app,
    )


def test_responses_endpoint_remains_agent_facing_generation_route():
    app = _make_app()

    response = asyncio.run(app._dispatch(_request(app, "/v1/responses")))

    assert response.status_code == 400
    assert b"Invalid JSON body" in response.body


@pytest.mark.parametrize(
    "path",
    [
        "/v1/chat/completions",
        "/v1/messages",
        "/v1beta/models",
        "/v1beta/models/gemini:generateContent",
        "/v1beta/models/gemini:streamGenerateContent",
    ],
)
def test_removed_downstream_generation_routes_are_not_registered(path: str):
    app = _make_app()

    response = asyncio.run(app._dispatch(_request(app, path)))

    assert response.status_code in {404, 405}
