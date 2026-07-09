"""Tests for direct OpenAI Responses passthrough in the gateway proxy."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from codex_rosetta.gateway.proxy import handle_non_streaming, handle_streaming
from codex_rosetta.gateway.transport._base import UpstreamResponse, UpstreamStream
from codex_rosetta.routing import ResolvedRoute


def _responses_route(*, tool_adaptation: dict[str, Any] | None = None) -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_responses",
        provider_name="test-provider",
        upstream_model="gpt-test",
        tool_adaptation=tool_adaptation,
    )


def _provider_info() -> MagicMock:
    info = MagicMock()
    info.base_url = "https://api.example.test"
    return info


def test_openai_responses_non_streaming_direct_passthrough():
    """Same-protocol Responses requests should not be decoded into IR."""
    captured_body: dict[str, Any] = {}
    upstream_body = {
        "id": "resp_123",
        "object": "response",
        "model": "gpt-test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "work"}],
            }
        ],
        "custom_passthrough_field": {"kept": True},
    }
    upstream_raw = json.dumps(upstream_body, separators=(",", ":")).encode()

    async def send_request(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_body.update(body)
        return UpstreamResponse(
            status_code=200,
            body=upstream_body,
            raw_content=upstream_raw,
        )

    transport = MagicMock()
    transport.send_request = AsyncMock(side_effect=send_request)
    body = {
        "model": "gpt-test",
        "input": [{"type": "message", "role": "user", "content": "hello"}],
        "tool_choice": {"mode": "auto", "tool_name": ""},
        "parallel_tool_calls": False,
        "phase": "not-a-real-top-level-field-but-preserved",
    }

    async def run():
        return await handle_non_streaming(
            _responses_route(),
            _provider_info(),
            body,
            transport=transport,
            extra_headers={"User-Agent": "codex-test"},
        )

    response, profile = asyncio.run(run())

    assert response.status_code == 200
    assert response.body == upstream_raw
    assert json.loads(response.body) == upstream_body
    assert captured_body == body
    assert profile["passthrough"] is True
    assert "request_conversion_ms" not in profile


def test_tool_adaptation_removes_image_generation_before_passthrough():
    """Configured models can hide the image_generation tool from upstream."""
    captured_body: dict[str, Any] = {}
    upstream_body = {
        "id": "resp_123",
        "object": "response",
        "model": "gpt-test",
        "status": "completed",
        "output": [],
    }

    async def send_request(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_body.update(body)
        return UpstreamResponse(
            status_code=200,
            body=upstream_body,
            raw_content=json.dumps(upstream_body).encode(),
        )

    transport = MagicMock()
    transport.send_request = AsyncMock(side_effect=send_request)
    body = {
        "model": "gpt-test",
        "input": "hello",
        "tools": [
            {"type": "web_search_preview"},
            {"type": "image_generation"},
            {
                "type": "function",
                "function": {"name": "image_generation", "parameters": {}},
            },
            {"type": "function", "name": "apply_patch", "parameters": {}},
        ],
        "tool_choice": {"mode": "tool", "tool_name": "image_generation"},
        "tool_config": {"disable_parallel": True},
    }

    async def run():
        return await handle_non_streaming(
            _responses_route(
                tool_adaptation={
                    "localize_code_editing_tools": False,
                    "remove_image_generation": True,
                }
            ),
            _provider_info(),
            body,
            transport=transport,
        )

    response, profile = asyncio.run(run())

    assert response.status_code == 200
    assert profile["passthrough"] is True
    assert captured_body["tools"] == [
        {"type": "web_search_preview"},
        {"type": "function", "name": "apply_patch", "parameters": {}},
    ]
    assert "tool_choice" not in captured_body
    assert captured_body["tool_config"] == {"disable_parallel": True}
    assert body["tools"][1] == {"type": "image_generation"}


def test_tool_adaptation_removes_responses_lite_image_generation():
    """Image generation is removed from Responses Lite embedded tools."""
    captured_body: dict[str, Any] = {}

    async def send_request(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_body.update(body)
        response_body = {"id": "resp_123", "status": "completed", "output": []}
        return UpstreamResponse(
            status_code=200,
            body=response_body,
            raw_content=json.dumps(response_body).encode(),
        )

    transport = MagicMock()
    transport.send_request = AsyncMock(side_effect=send_request)
    body = {
        "model": "gpt-test",
        "input": [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "image_gen",
                        "tools": [
                            {
                                "type": "function",
                                "name": "imagegen",
                                "parameters": {},
                            }
                        ],
                    },
                    {
                        "type": "function",
                        "name": "exec_command",
                        "parameters": {},
                    },
                    {
                        "type": "function",
                        "name": "image_gen__imagegen",
                        "parameters": {},
                    },
                ],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            },
        ],
        "tool_choice": {"type": "image_gen"},
        "tool_config": {"disable_parallel": True},
    }

    async def run():
        return await handle_non_streaming(
            _responses_route(
                tool_adaptation={
                    "localize_code_editing_tools": False,
                    "remove_image_generation": True,
                }
            ),
            _provider_info(),
            body,
            transport=transport,
        )

    response, _ = asyncio.run(run())

    assert response.status_code == 200
    assert captured_body["input"][0]["tools"] == [
        {"type": "function", "name": "exec_command", "parameters": {}}
    ]
    assert "tool_choice" not in captured_body
    assert captured_body["tool_config"] == {"disable_parallel": True}
    assert body["input"][0]["tools"][0]["name"] == "image_gen"


class _RawStream(UpstreamStream):
    def __init__(self, chunks: list[bytes], *, status_code: int = 200) -> None:
        self.status_code = status_code
        self._chunks = chunks
        self.closed = False

    async def read_error(self) -> str:
        return b"".join(self._chunks).decode()

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        raise AssertionError("Responses passthrough must not parse stream chunks")

    def aiter_raw_bytes(self) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for chunk in self._chunks:
                yield chunk

        return gen()

    async def close(self) -> None:
        self.closed = True


def test_openai_responses_streaming_direct_raw_passthrough():
    """Same-protocol Responses streams should forward filtered raw SSE bytes."""
    raw_chunks = [
        b'event: response.created\ndata: {"type":"response.created"}\n\n',
        b'event: response.output_item.added\ndata: {"type":"response.output_item.added","item":{"type":"message","phase":"commentary"}}\n\n',
    ]
    stream = _RawStream(raw_chunks)
    captured_body: dict[str, Any] = {}

    async def send_streaming(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_body.update(body)
        return stream

    transport = MagicMock()
    transport.send_streaming = AsyncMock(side_effect=send_streaming)
    body = {
        "model": "gpt-test",
        "input": [
            {
                "type": "additional_tools",
                "tools": [
                    {"type": "image_generation"},
                    {"type": "web_search_preview"},
                ],
            },
            {"type": "message", "role": "user", "content": "hello"},
        ],
        "stream": True,
    }

    async def run():
        response, profile = await handle_streaming(
            _responses_route(
                tool_adaptation={
                    "remove_image_generation": True,
                }
            ),
            _provider_info(),
            body,
            transport=transport,
            extra_headers={"x-request-id": "req-123"},
        )
        chunks: list[bytes] = []
        async for chunk in response._generator:
            chunks.append(chunk)
        return response, profile, chunks

    response, profile, chunks = asyncio.run(run())

    assert response.status_code == 200
    assert response.content_type == "text/event-stream"
    assert chunks == raw_chunks
    assert captured_body == {
        "model": "gpt-test",
        "input": [
            {
                "type": "additional_tools",
                "tools": [{"type": "web_search_preview"}],
            },
            {"type": "message", "role": "user", "content": "hello"},
        ],
        "stream": True,
    }
    assert body["input"][0]["tools"][0] == {"type": "image_generation"}
    assert profile["passthrough"] is True
    assert "request_conversion_ms" not in profile
