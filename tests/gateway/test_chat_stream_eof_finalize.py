"""Tests for Chat upstream streams that end after finish_reason."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from llm_rosetta.gateway.proxy import handle_streaming
from llm_rosetta.gateway.transport._base import UpstreamStream
from llm_rosetta.routing import ResolvedRoute


class _ChatStream(UpstreamStream):
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.status_code = 200
        self._chunks = chunks
        self.closed = False

    async def read_error(self) -> str:
        return ""

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for chunk in self._chunks:
            yield chunk

    def aiter_raw_bytes(self):
        return None

    async def close(self) -> None:
        self.closed = True


def _route() -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="test-provider",
        upstream_model="glm-5.2",
    )


def _provider_info() -> MagicMock:
    info = MagicMock()
    info.base_url = "https://api.example.test"
    return info


def _chunk(
    *,
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
    empty_choices: bool = False,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 123,
        "model": "glm-5.2",
    }
    if empty_choices:
        chunk["choices"] = []
    else:
        delta: dict[str, Any] = {}
        if content is not None:
            delta["content"] = content
        chunk["choices"] = [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ]
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def _responses_completed_events(chunks: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for chunk in chunks:
        event_type: str | None = None
        data: dict[str, Any] | None = None
        for line in chunk.splitlines():
            if line.startswith("event: "):
                event_type = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if event_type == "response.completed" and data is not None:
            events.append(data)
    return events


def _run_chat_stream(chunks: list[dict[str, Any]]) -> list[str]:
    async def send_streaming(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        assert target_provider == "openai_chat"
        return _ChatStream(chunks)

    transport = MagicMock()
    transport.send_streaming = AsyncMock(side_effect=send_streaming)
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    async def run() -> list[str]:
        response, profile = await handle_streaming(
            _route(),
            _provider_info(),
            body,
            transport=transport,
        )
        assert response.status_code == 200
        assert "request_conversion_ms" in profile
        emitted: list[str] = []
        async for event in response._generator:
            emitted.append(event)
        return emitted

    return asyncio.run(run())


def test_chat_finish_then_eof_synthesizes_responses_completed():
    chunks = [
        _chunk(content="hello"),
        _chunk(finish_reason="stop"),
    ]

    emitted = _run_chat_stream(chunks)

    completed = _responses_completed_events(emitted)
    assert len(completed) == 1
    response = completed[0]["response"]
    assert response["status"] == "completed"
    assert response["output"][0]["content"][0]["text"] == "hello"
    assert "usage" not in response or response["usage"] is None


def test_chat_finish_with_usage_keeps_single_completed():
    chunks = [
        _chunk(content="hello"),
        _chunk(
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
    ]

    emitted = _run_chat_stream(chunks)

    completed = _responses_completed_events(emitted)
    assert len(completed) == 1
    assert completed[0]["response"]["usage"]["total_tokens"] == 15


def test_chat_finish_then_empty_choices_usage_keeps_single_completed():
    chunks = [
        _chunk(content="hello"),
        _chunk(finish_reason="stop"),
        _chunk(
            empty_choices=True,
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
    ]

    emitted = _run_chat_stream(chunks)

    completed = _responses_completed_events(emitted)
    assert len(completed) == 1
    assert completed[0]["response"]["usage"]["total_tokens"] == 15


def test_chat_eof_without_finish_does_not_synthesize_completed():
    emitted = _run_chat_stream([_chunk(content="partial")])

    assert _responses_completed_events(emitted) == []
