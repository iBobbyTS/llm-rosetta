"""Tests for optional stream trace JSONL diagnostics."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from llm_rosetta.gateway.proxy import _stream_event_generator
from llm_rosetta.gateway.stream_trace import (
    DEFAULT_TRACE_PATH,
    StreamTraceConfig,
    StreamTraceLogger,
    StreamTraceState,
)


class _FakeStream:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


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
