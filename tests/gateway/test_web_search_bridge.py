"""Tests for the Codex web_search bridge on Responses-to-Chat routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

from codex_rosetta._vendor.httpserver import StreamingResponse
from codex_rosetta.gateway.proxy import handle_streaming
from codex_rosetta.gateway.transport._base import UpstreamStream
from codex_rosetta.gateway.web_search import (
    WEB_SEARCH_PROFILE_ITEM_ID,
    WebSearchSettings,
    profile_search_config,
)
from codex_rosetta.routing import ResolvedRoute


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

    async def close(self) -> None:
        self.closed = True


class _FakeTavilyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, WebSearchSettings]] = []

    async def search(
        self,
        query: str,
        *,
        settings: WebSearchSettings,
    ) -> dict[str, Any]:
        self.calls.append((query, settings))
        return {
            "answer": "Codex web search is enabled through a Responses web_search tool.",
            "request_id": "tvly-test",
            "response_time": 0.12,
            "results": [
                {
                    "title": "Codex Web Search Docs",
                    "url": "https://example.com/codex-web-search",
                    "content": "Codex can display native web search activity.",
                    "score": 0.91,
                }
            ],
        }


def _route(*, search_token: str = "tvly-test") -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="test-provider",
        upstream_model="deepseek-v4-flash",
        tool_profile_inputs={
            "hosted.web_search": {
                "provider": "tavily",
                "token": search_token,
            }
        },
    )


def _provider_info() -> MagicMock:
    info = MagicMock()
    info.base_url = "https://api.example.test"
    return info


def test_web_search_runtime_config_comes_from_profile_card() -> None:
    assert profile_search_config(_route(), WEB_SEARCH_PROFILE_ITEM_ID) == {
        "provider": "tavily",
        "tavily_api_key": "tvly-test",
    }
    assert profile_search_config(
        _route(search_token=""), WEB_SEARCH_PROFILE_ITEM_ID
    ) == {"provider": "tavily", "tavily_api_key": ""}


def _tool_call_chunk() -> dict[str, Any]:
    return {
        "id": "chatcmpl-search",
        "object": "chat.completion.chunk",
        "created": 123,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_web_search",
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": json.dumps(
                                    {"query": "Codex native web search UX"}
                                ),
                            },
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
    }


def _finish_chunk(reason: str = "tool_calls") -> dict[str, Any]:
    return {
        "id": "chatcmpl-search",
        "object": "chat.completion.chunk",
        "created": 123,
        "model": "deepseek-v4-flash",
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
    }


def _answer_chunk(text: str, *, finish_reason: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-answer",
        "object": "chat.completion.chunk",
        "created": 124,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "delta": {"content": text} if text else {},
                "finish_reason": finish_reason,
            }
        ],
    }


def _events(chunks: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for chunk in chunks:
        data: dict[str, Any] | None = None
        for line in chunk.splitlines():
            if line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if data is not None:
            events.append(data)
    return events


def test_responses_chat_web_search_executes_tavily_and_continues_chat_stream():
    captured_bodies: list[dict[str, Any]] = []
    streams = [
        _ChatStream([_tool_call_chunk(), _finish_chunk()]),
        _ChatStream(
            [
                _answer_chunk("The search result says native UX is available."),
                _answer_chunk("", finish_reason="stop"),
            ]
        ),
    ]

    async def send_streaming(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        assert target_provider == "openai_chat"
        captured_bodies.append(body)
        return streams.pop(0)

    transport = MagicMock()
    transport.send_streaming.side_effect = send_streaming
    fake_tavily = _FakeTavilyClient()
    body = {
        "model": "deepseek-v4-flash",
        "input": [{"role": "user", "content": "Search for Codex web search UX."}],
        "stream": True,
        "tools": [
            {
                "type": "web_search",
                "external_web_access": True,
                "search_context_size": "high",
            }
        ],
    }

    async def run() -> list[str]:
        response, profile = await handle_streaming(
            _route(),
            _provider_info(),
            body,
            transport=transport,
            web_search_client=fake_tavily,
        )
        assert response.status_code == 200
        assert isinstance(response, StreamingResponse)
        assert "request_conversion_ms" in profile
        emitted: list[str] = []
        async for chunk in response._generator:
            assert isinstance(chunk, str)
            emitted.append(chunk)
        return emitted

    emitted = asyncio.run(run())
    events = _events(emitted)

    assert len(captured_bodies) == 2
    web_tool = captured_bodies[0]["tools"][0]["function"]
    assert web_tool["name"] == "web_search"
    assert web_tool["parameters"]["required"] == ["query"]
    assert fake_tavily.calls == [
        (
            "Codex native web search UX",
            WebSearchSettings(max_results=8, search_depth="advanced"),
        )
    ]

    followup_messages = captured_bodies[1]["messages"]
    assert followup_messages[-2]["role"] == "assistant"
    assert followup_messages[-2]["tool_calls"][0]["id"] == "call_web_search"
    assert followup_messages[-1]["role"] == "tool"
    assert followup_messages[-1]["tool_call_id"] == "call_web_search"
    assert "Codex Web Search Docs" in followup_messages[-1]["content"]
    assert "https://example.com/codex-web-search" in followup_messages[-1]["content"]

    web_added = [
        event
        for event in events
        if event.get("type") == "response.output_item.added"
        and event.get("item", {}).get("type") == "web_search_call"
    ]
    web_done = [
        event
        for event in events
        if event.get("type") == "response.output_item.done"
        and event.get("item", {}).get("type") == "web_search_call"
    ]
    completed = [event for event in events if event.get("type") == "response.completed"]

    assert len(web_added) == 1
    assert len(web_done) == 1
    assert web_done[0]["item"]["status"] == "completed"
    assert web_done[0]["item"]["action"] == {
        "type": "search",
        "query": "Codex native web search UX",
    }
    assert len(completed) == 1
    output = completed[0]["response"]["output"]
    assert output[0]["type"] == "web_search_call"
    assert output[1]["type"] == "message"
    assert output[1]["content"][0]["text"] == (
        "The search result says native UX is available."
    )


def test_responses_chat_without_tavily_key_does_not_expose_web_search_tool():
    captured_bodies: list[dict[str, Any]] = []
    stream = _ChatStream(
        [
            _answer_chunk("Search is unavailable."),
            _answer_chunk("", finish_reason="stop"),
        ]
    )

    async def send_streaming(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        assert target_provider == "openai_chat"
        captured_bodies.append(body)
        return stream

    transport = MagicMock()
    transport.send_streaming.side_effect = send_streaming
    body = {
        "model": "deepseek-v4-flash",
        "input": [{"role": "user", "content": "Search for Codex web search UX."}],
        "stream": True,
        "tools": [{"type": "web_search", "external_web_access": True}],
        "tool_choice": "web_search",
    }

    async def run() -> list[str]:
        response, _profile = await handle_streaming(
            _route(search_token=""),
            _provider_info(),
            body,
            transport=transport,
        )
        assert isinstance(response, StreamingResponse)
        emitted: list[str] = []
        async for chunk in response._generator:
            assert isinstance(chunk, str)
            emitted.append(chunk)
        return emitted

    emitted = asyncio.run(run())
    events = _events(emitted)

    assert len(captured_bodies) == 1
    assert captured_bodies[0].get("tools") in (None, [])
    assert captured_bodies[0].get("tool_choice") != "web_search"
    assert not [
        event
        for event in events
        if event.get("type") == "response.output_item.added"
        and event.get("item", {}).get("type") == "web_search_call"
    ]
    completed = [event for event in events if event.get("type") == "response.completed"]
    assert completed[0]["response"]["output"][0]["content"][0]["text"] == (
        "Search is unavailable."
    )
