"""Tests for optional stream trace JSONL diagnostics."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from llm_rosetta.gateway.proxy import (
    _raw_stream_event_generator,
    _stream_event_generator,
    handle_streaming,
)
from llm_rosetta.gateway.stream_trace import (
    DEFAULT_TRACE_PATH,
    StreamTraceConfig,
    StreamTraceLogger,
    StreamTraceState,
)
from llm_rosetta.gateway.transport._base import UpstreamStream
from llm_rosetta.routing import ResolvedRoute


class _FakeStream:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.status_code = 200

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class _RawStream(UpstreamStream):
    def __init__(self, chunks: list[bytes], *, status_code: int = 200) -> None:
        self.status_code = status_code
        self._chunks = chunks
        self.closed = False

    async def read_error(self) -> str:
        return b"".join(self._chunks).decode()

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        raise AssertionError("raw passthrough should not parse stream chunks")

    def aiter_raw_bytes(self) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for chunk in self._chunks:
                yield chunk

        return gen()

    async def close(self) -> None:
        self.closed = True


class _FakeProcessor:
    def process_chunk(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "response.output_text.delta", "delta": chunk["delta"]}]


def test_stream_trace_writes_jsonl_for_stream_events(tmp_path):
    """Trace logger records upstream chunks and downstream SSE side by side."""
    trace_path = tmp_path / "stream-trace.jsonl"
    trace = StreamTraceLogger(
        path=trace_path,
        request_id="req-123",
        request_log_id="log-123",
        model="glm-5.2",
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="Opencode Go",
    )

    async def collect() -> list[str]:
        events: list[str] = []
        async for event in _stream_event_generator(
            source_provider="openai_responses",
            stream=_FakeStream([{"delta": "hello"}]),
            processor=_FakeProcessor(),
            model="glm-5.2",
            format_sse=lambda event: f"data: {json.dumps(event)}\n\n",
            trace=trace,
        ):
            events.append(event)
        return events

    events = asyncio.run(collect())

    assert events == [
        'data: {"type": "response.output_text.delta", "delta": "hello"}\n\n'
    ]
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    stages = [record["stage"] for record in records]
    assert stages == [
        "upstream_chunk",
        "source_event",
        "downstream_sse",
        "stream_complete",
    ]
    assert records[0]["data"] == {"delta": "hello"}
    assert records[1]["data"]["type"] == "response.output_text.delta"
    assert records[2]["data"].startswith("data: ")
    assert records[0]["model"] == "glm-5.2"
    assert records[0]["request_id"] == "req-123"


def test_stream_trace_state_respects_config_filter():
    """Trace is enabled only when config is on and the filter matches."""
    state = StreamTraceState(
        StreamTraceConfig(
            enabled=True,
            filter="glm,opencode",
        )
    )

    logger = state.create_logger(
        request_id=None,
        request_log_id=None,
        model="glm-5.2",
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="Opencode Go",
    )
    assert logger is not None
    assert logger.path == Path(DEFAULT_TRACE_PATH).expanduser()
    assert (
        state.create_logger(
            request_id=None,
            request_log_id=None,
            model="gpt-5.5",
            source_provider="openai_responses",
            target_provider="openai_responses",
            provider_name="Pixel",
        )
        is None
    )


def test_stream_trace_state_uses_configured_path(tmp_path):
    """Trace logger writes to configured path when one is supplied."""
    trace_path = tmp_path / "custom-stream-trace.jsonl"
    state = StreamTraceState(
        StreamTraceConfig(
            enabled=True,
            path=str(trace_path),
        )
    )

    logger = state.create_logger(
        request_id=None,
        request_log_id=None,
        model="glm-5.2",
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="Opencode Go",
    )

    assert logger is not None
    assert logger.path == trace_path


def test_stream_trace_state_does_not_force_logger_when_disabled(tmp_path):
    """Disabled trace settings must prevent all stream trace files."""
    trace_path = tmp_path / "forced-stream-trace.jsonl"
    state = StreamTraceState(
        StreamTraceConfig(
            enabled=False,
            filter="does-not-match",
            path=str(trace_path),
        )
    )

    logger = state.create_logger(
        request_id=None,
        request_log_id=None,
        model="glm-5.2",
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="Opencode Go",
        force=True,
    )

    assert logger is None


def test_raw_stream_trace_records_passthrough_chunk_once(tmp_path):
    """Responses passthrough traces should not duplicate identical raw bytes."""
    trace_path = tmp_path / "raw-stream-trace.jsonl"
    trace = StreamTraceLogger(
        path=trace_path,
        request_id="req-raw",
        request_log_id="log-raw",
        model="gpt-test",
        source_provider="openai_responses",
        target_provider="openai_responses",
        provider_name="Pixel",
    )
    raw_chunks = [
        b'event: response.created\ndata: {"type":"response.created"}\n\n',
        b'event: response.completed\ndata: {"type":"response.completed"}\n\n',
    ]

    async def collect() -> list[bytes]:
        chunks: list[bytes] = []
        async for chunk in _raw_stream_event_generator(
            stream=_RawStream(raw_chunks),
            model="gpt-test",
            trace=trace,
        ):
            chunks.append(chunk)
        return chunks

    assert asyncio.run(collect()) == raw_chunks
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert [record["stage"] for record in records] == [
        "raw_passthrough_chunk",
        "raw_passthrough_chunk",
        "stream_complete",
    ]
    assert records[0]["data"].startswith("b'event: response.created")


def test_responses_chat_streaming_trace_respects_disabled_config(tmp_path):
    """Responses/Chat conversion must not trace when stream trace is disabled."""
    trace_path = tmp_path / "disabled-stream-trace.jsonl"
    state = StreamTraceState(StreamTraceConfig(enabled=False, path=str(trace_path)))
    route = ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="Opencode Go",
        upstream_model="glm-5.2",
    )
    provider_info = MagicMock()
    provider_info.base_url = "https://api.example.test"

    async def send_streaming(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        assert target_provider == "openai_chat"
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        return _FakeStream(
            [
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "created": 123,
                    "model": "glm-5.2",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "hi"},
                            "finish_reason": None,
                        }
                    ],
                }
            ]
        )

    transport = MagicMock()
    transport.send_streaming = AsyncMock(side_effect=send_streaming)
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    async def run() -> list[str]:
        response, profile = await handle_streaming(
            route,
            provider_info,
            body,
            transport=transport,
            extra_headers={"x-request-id": "req-conv"},
            entry_id="log-conv",
            stream_trace_state=state,
        )
        assert response.status_code == 200
        assert "request_conversion_ms" in profile
        chunks: list[str] = []
        async for chunk in response._generator:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())

    assert any("response.output_text.delta" in chunk for chunk in chunks)
    assert not trace_path.exists()


def test_responses_chat_streaming_trace_records_when_enabled(tmp_path):
    """Responses/Chat conversion traces record inbound and outbound payloads."""
    trace_path = tmp_path / "enabled-stream-trace.jsonl"
    state = StreamTraceState(StreamTraceConfig(enabled=True, path=str(trace_path)))
    route = ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="Opencode Go",
        upstream_model="glm-5.2",
    )
    provider_info = MagicMock()
    provider_info.base_url = "https://api.example.test"

    async def send_streaming(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        assert target_provider == "openai_chat"
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        return _FakeStream(
            [
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion.chunk",
                    "created": 123,
                    "model": "glm-5.2",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "hi"},
                            "finish_reason": None,
                        }
                    ],
                }
            ]
        )

    transport = MagicMock()
    transport.send_streaming = AsyncMock(side_effect=send_streaming)
    body = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    async def run() -> list[str]:
        response, profile = await handle_streaming(
            route,
            provider_info,
            body,
            transport=transport,
            extra_headers={"x-request-id": "req-conv"},
            entry_id="log-conv",
            stream_trace_state=state,
        )
        assert response.status_code == 200
        assert "request_conversion_ms" in profile
        chunks: list[str] = []
        async for chunk in response._generator:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())

    assert any("response.output_text.delta" in chunk for chunk in chunks)
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    stages = [record["stage"] for record in records]
    assert stages[:5] == [
        "stream_start",
        "source_request",
        "target_request",
        "upstream_chunk",
        "ir_event",
    ]
    assert "source_event" in stages
    assert "downstream_sse" in stages
    assert stages[-1] == "stream_complete"
    assert records[1]["data"] == body
    assert records[2]["data"]["messages"] == [{"role": "user", "content": "hello"}]
