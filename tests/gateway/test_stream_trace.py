"""Tests for optional stream trace JSONL diagnostics."""

from __future__ import annotations

import asyncio
import json
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from codex_rosetta._vendor.httpserver import StreamingResponse
from codex_rosetta.gateway.proxy import (
    _raw_stream_event_generator,
    _stream_event_generator,
    _web_search_stream_event_generator,
    handle_streaming,
)
from codex_rosetta.gateway.stream_trace import (
    DEFAULT_TRACE_PATH,
    StreamTraceConfig,
    StreamTraceLogger,
    StreamTraceState,
)
from codex_rosetta.gateway.transport._base import (
    UpstreamConnectionError,
    UpstreamProtocolError,
    UpstreamStream,
)
from codex_rosetta.routing import ResolvedRoute


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

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def gen() -> AsyncIterator[dict[str, Any]]:
            raise AssertionError("raw passthrough should not parse stream chunks")
            yield {}

        return gen()

    def aiter_raw_bytes(self) -> AsyncIterator[bytes]:
        async def gen() -> AsyncIterator[bytes]:
            for chunk in self._chunks:
                yield chunk

        return gen()

    async def close(self) -> None:
        self.closed = True


class _ProtocolFailureStream:
    """Converted stream that exposes only a stable protocol failure."""

    status_code = 200

    def __init__(self) -> None:
        self.close_calls = 0
        self.untrusted_body = "configured-token Bearer bearer-secret password=hunter2"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def __aiter__(self):
        raise UpstreamProtocolError("Upstream SSE data is not valid JSON")
        yield {}

    async def close(self) -> None:
        self.close_calls += 1


class _FakeProcessor:
    def process_chunk(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "response.output_text.delta", "delta": chunk["delta"]}]


def _direct_responses_route() -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_responses",
        provider_name="OpenAI passthrough",
        upstream_model="gpt-test",
    )


def _direct_responses_body() -> dict[str, Any]:
    return {
        "model": "gpt-test",
        "input": [{"type": "message", "role": "user", "content": "hello"}],
        "stream": True,
    }


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
    assert stat.S_IMODE(trace_path.stat().st_mode) == 0o600


def test_direct_responses_trace_records_upstream_http_error(tmp_path):
    """A passthrough HTTP error is traced before any stream chunk exists."""
    trace_path = tmp_path / "passthrough-http-error.jsonl"
    state = StreamTraceState(StreamTraceConfig(enabled=True, path=str(trace_path)))
    stream = _RawStream(
        [b'{"error":{"message":"compact request rejected"}}'],
        status_code=502,
    )
    transport = MagicMock()
    transport.send_streaming = AsyncMock(return_value=stream)
    provider_info = MagicMock(base_url="https://api.example.test/v1")

    response, _profile = asyncio.run(
        handle_streaming(
            _direct_responses_route(),
            provider_info,
            _direct_responses_body(),
            transport=transport,
            extra_headers={"x-request-id": "req-http-error"},
            entry_id="log-http-error",
            stream_trace_state=state,
        )
    )

    assert response.status_code == 502
    assert stream.closed is True
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert [record["stage"] for record in records] == [
        "stream_start",
        "raw_passthrough_request",
        "upstream_error",
        "stream_complete",
    ]
    assert records[2]["data"] == {
        "status_code": 502,
        "error": '{"error":{"message":"compact request rejected"}}',
        "error_phase": "stream_header",
        "upstream_url": "https://api.example.test/v1",
    }
    assert records[3]["data"]["stream_outcome"] == "error"
    assert records[3]["data"]["chunk_count"] == 0


def test_direct_responses_trace_records_upstream_connection_error(tmp_path):
    """A passthrough connection failure keeps its diagnostic in the trace."""
    trace_path = tmp_path / "passthrough-connection-error.jsonl"
    state = StreamTraceState(StreamTraceConfig(enabled=True, path=str(trace_path)))
    transport = MagicMock()
    transport.send_streaming = AsyncMock(
        side_effect=UpstreamConnectionError("upstream retries exhausted")
    )
    provider_info = MagicMock(base_url="https://api.example.test/v1")

    response, _profile = asyncio.run(
        handle_streaming(
            _direct_responses_route(),
            provider_info,
            _direct_responses_body(),
            transport=transport,
            extra_headers={"x-request-id": "req-connect-error"},
            entry_id="log-connect-error",
            stream_trace_state=state,
        )
    )

    assert response.status_code == 502
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert [record["stage"] for record in records] == [
        "stream_start",
        "raw_passthrough_request",
        "upstream_connection_error",
        "stream_complete",
    ]
    assert records[2]["data"] == {
        "status_code": 502,
        "error": "upstream retries exhausted",
        "error_phase": "stream_header",
        "upstream_url": "https://api.example.test/v1",
    }
    assert records[3]["data"]["stream_error"] == "upstream retries exhausted"


def test_stream_trace_redacts_known_and_bearer_tokens(tmp_path):
    trace_path = tmp_path / "private" / "stream-trace.jsonl"
    state = StreamTraceState(
        StreamTraceConfig(enabled=True, path=str(trace_path)),
        token_values={"provider-secret"},
    )
    trace = state.create_logger(
        request_id="request",
        request_log_id=None,
        model="model",
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="provider",
    )
    assert trace is not None
    trace.log(
        "target_request",
        {
            "prompt": "keep alice@example.com",
            "api_key": "field-secret",
            "header": "Bearer bearer-secret",
            "echo": "provider-secret",
        },
    )

    record = json.loads(trace_path.read_text())
    assert record["data"]["prompt"] == "keep alice@example.com"
    assert record["data"]["api_key"] == "[REDACTED]"
    assert record["data"]["header"] == "Bearer [REDACTED]"
    assert record["data"]["echo"] == "[REDACTED]"
    assert stat.S_IMODE(trace_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(trace_path.stat().st_mode) == 0o600


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


def _assert_cancelled_terminal_record(trace_path: Path) -> None:
    terminal = json.loads(trace_path.read_text().splitlines()[-1])
    assert terminal["stage"] == "stream_complete"
    assert terminal["data"]["stream_complete"] is False
    assert terminal["data"]["stream_outcome"] == "cancelled"
    assert terminal["data"]["stream_error"] == "Stream closed before completion"


def test_converted_stream_trace_records_early_close_as_cancelled(tmp_path):
    trace_path = tmp_path / "converted-cancel.jsonl"
    trace = StreamTraceLogger(
        path=trace_path,
        request_id="req-converted-cancel",
        request_log_id=None,
        model="model",
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="provider",
    )

    async def scenario() -> None:
        generator = _stream_event_generator(
            source_provider="openai_responses",
            stream=_FakeStream([{"delta": "first"}, {"delta": "second"}]),
            processor=_FakeProcessor(),
            model="model",
            format_sse=lambda event: f"data: {json.dumps(event)}\n\n",
            trace=trace,
        )
        assert "first" in await generator.__anext__()
        await cast(Any, generator).aclose()

    asyncio.run(scenario())
    _assert_cancelled_terminal_record(trace_path)


def test_raw_stream_trace_records_early_close_as_cancelled(tmp_path):
    trace_path = tmp_path / "raw-cancel.jsonl"
    trace = StreamTraceLogger(
        path=trace_path,
        request_id="req-raw-cancel",
        request_log_id=None,
        model="model",
        source_provider="openai_responses",
        target_provider="openai_responses",
        provider_name="provider",
    )

    async def scenario() -> None:
        generator = _raw_stream_event_generator(
            stream=_RawStream([b"first", b"second"]),
            model="model",
            trace=trace,
        )
        assert await generator.__anext__() == b"first"
        await cast(Any, generator).aclose()

    asyncio.run(scenario())
    _assert_cancelled_terminal_record(trace_path)


def test_web_search_stream_trace_records_early_close_as_cancelled(tmp_path):
    trace_path = tmp_path / "web-search-cancel.jsonl"
    trace = StreamTraceLogger(
        path=trace_path,
        request_id="req-search-cancel",
        request_log_id=None,
        model="model",
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="provider",
    )

    async def scenario() -> None:
        generator = _web_search_stream_event_generator(
            source_provider="openai_responses",
            initial_stream=_FakeStream([{"delta": "first"}, {"delta": "second"}]),
            processor_factory=_FakeProcessor,
            model="model",
            format_sse=lambda event: f"data: {json.dumps(event)}\n\n",
            transport=MagicMock(),
            provider_info=MagicMock(),
            target_provider="openai_chat",
            target_body={},
            web_search_runtime=MagicMock(),
            trace=trace,
        )
        assert "first" in await generator.__anext__()
        await cast(Any, generator).aclose()

    asyncio.run(scenario())
    _assert_cancelled_terminal_record(trace_path)


@pytest.mark.parametrize("path_kind", ["converted", "web_search"])
def test_protocol_failure_has_one_safe_trace_terminal(tmp_path, path_kind):
    trace_path = tmp_path / f"{path_kind}-protocol-error.jsonl"
    trace = StreamTraceLogger(
        path=trace_path,
        request_id=f"req-{path_kind}",
        request_log_id=None,
        model="model",
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="provider",
    )
    stream = _ProtocolFailureStream()

    async def scenario() -> None:
        if path_kind == "converted":
            generator = _stream_event_generator(
                source_provider="openai_responses",
                stream=stream,
                processor=_FakeProcessor(),
                model="model",
                format_sse=lambda event: f"data: {json.dumps(event)}\n\n",
                trace=trace,
            )
        else:
            generator = _web_search_stream_event_generator(
                source_provider="openai_responses",
                initial_stream=stream,
                processor_factory=_FakeProcessor,
                model="model",
                format_sse=lambda event: f"data: {json.dumps(event)}\n\n",
                transport=MagicMock(),
                provider_info=MagicMock(),
                target_provider="openai_chat",
                target_body={},
                web_search_runtime=MagicMock(),
                trace=trace,
            )
        with pytest.raises(
            UpstreamProtocolError,
            match="^Upstream SSE data is not valid JSON$",
        ):
            await generator.__anext__()
        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()

    asyncio.run(scenario())

    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    terminals = [record for record in records if record["stage"] == "stream_complete"]
    assert len(terminals) == 1
    assert terminals[0]["data"]["stream_complete"] is False
    assert terminals[0]["data"]["stream_outcome"] == "error"
    assert terminals[0]["data"]["stream_error"] == (
        "Upstream SSE data is not valid JSON"
    )
    assert stream.untrusted_body not in trace_path.read_text()
    assert stream.close_calls == 1


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
        assert isinstance(response, StreamingResponse)
        assert "request_conversion_ms" in profile
        chunks: list[str] = []
        async for chunk in response._generator:
            assert isinstance(chunk, str)
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
        assert isinstance(response, StreamingResponse)
        assert "request_conversion_ms" in profile
        chunks: list[str] = []
        async for chunk in response._generator:
            assert isinstance(chunk, str)
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
