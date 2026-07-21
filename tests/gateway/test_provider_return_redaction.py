"""Gateway provider-return credential redaction regression tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from codex_rosetta._vendor.httpserver import Response, StreamingResponse
from codex_rosetta.gateway.proxy import handle_non_streaming, handle_streaming
from codex_rosetta.gateway.stream_trace import StreamTraceConfig, StreamTraceState
from codex_rosetta.gateway.transport._base import (
    UpstreamConnectionError,
    UpstreamResponse,
    UpstreamStream,
)
from codex_rosetta.gateway.transport.provider_info import ProviderInfo, openai_auth
from codex_rosetta.routing import ResolvedRoute


def _provider(token: str) -> ProviderInfo:
    return ProviderInfo(
        "test-provider",
        api_key=token,
        base_url="https://upstream.example/v1",
        auth_header_fn=openai_auth,
        url_template="{base_url}/responses",
    )


def _passthrough_route() -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_responses",
        provider_name="test-provider",
        upstream_model="test-model",
    )


def _converted_route() -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="test-provider",
        upstream_model="test-model",
    )


class _StaticTransport:
    def __init__(
        self,
        *,
        response: UpstreamResponse | None = None,
        stream: UpstreamStream | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.response = response
        self.stream = stream
        self.failure = failure

    async def send_request(self, *args: Any, **kwargs: Any) -> UpstreamResponse:
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response

    async def send_streaming(self, *args: Any, **kwargs: Any) -> UpstreamStream:
        if self.failure is not None:
            raise self.failure
        assert self.stream is not None
        return self.stream

    async def send_passthrough(
        self,
        provider_info: ProviderInfo,
        url: str,
        body: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> UpstreamResponse:
        del provider_info, url, body, extra_headers
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response

    async def close(self) -> None:
        return None


class _StaticStream(UpstreamStream):
    def __init__(
        self,
        *,
        status_code: int = 200,
        events: list[dict[str, Any]] | None = None,
        raw_chunks: list[bytes] | None = None,
        error: str = "",
    ) -> None:
        self.status_code = status_code
        self.events = events or []
        self.raw_chunks = raw_chunks
        self.error = error
        self.closed = False

    async def read_error(self) -> str:
        return self.error

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for event in self.events:
            yield event

    def aiter_raw_bytes(self) -> AsyncIterator[bytes] | None:
        if self.raw_chunks is None:
            return None

        async def chunks() -> AsyncIterator[bytes]:
            for chunk in self.raw_chunks or []:
                yield chunk

        return chunks()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("status_code", [200, 401])
def test_responses_passthrough_redacts_success_and_http_error(status_code: int) -> None:
    token = "passthrough-provider-secret"
    payload = {
        "id": "resp_test",
        "object": "response",
        "status": "completed" if status_code == 200 else "failed",
        "output": [],
        "nested": {
            token: "ordinary-value-under-secret-key",
            "message": f"before {token} after",
        },
    }
    response, profile = asyncio.run(
        handle_non_streaming(
            _passthrough_route(),
            _provider(token),
            {"model": "test-model", "input": "hello"},
            transport=_StaticTransport(
                response=UpstreamResponse(
                    status_code=status_code,
                    body=payload if status_code < 400 else None,
                    raw_content=json.dumps(payload, separators=(",", ":")).encode(),
                )
            ),
        )
    )

    assert response.status_code == status_code
    assert token.encode() not in response.body
    assert b"before [REDACTED] after" in response.body
    assert profile["passthrough"] is True


@pytest.mark.parametrize("status_code", [200, 429])
def test_converted_response_redacts_success_and_http_error(status_code: int) -> None:
    token = "converted-provider-secret"
    upstream = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 123,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"before {token} after",
                },
                "finish_reason": "stop",
            }
        ],
    }
    if status_code >= 400:
        upstream = {"error": {"message": f"failed with {token}"}}

    response, _profile = asyncio.run(
        handle_non_streaming(
            _converted_route(),
            _provider(token),
            {"model": "test-model", "input": "hello"},
            transport=_StaticTransport(
                response=UpstreamResponse(
                    status_code=status_code,
                    body=upstream if status_code < 400 else None,
                    raw_content=json.dumps(upstream, separators=(",", ":")).encode(),
                )
            ),
        )
    )

    assert response.status_code == status_code
    assert token.encode() not in response.body
    assert b"[REDACTED]" in response.body


def test_passthrough_raw_stream_redacts_cross_chunk_and_trace(tmp_path) -> None:
    token = "raw-passthrough-secret"
    payload = (
        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta",'
        b'"delta":"before ' + token.encode() + b' after"}\n\n'
    )
    start = payload.index(token.encode()) + 5
    trace_path = tmp_path / "raw-trace.jsonl"
    trace_state = StreamTraceState(
        StreamTraceConfig(enabled=True, path=str(trace_path)), token_values=()
    )

    async def run() -> bytes:
        response, _profile = await handle_streaming(
            _passthrough_route(),
            _provider(token),
            {"model": "test-model", "input": "hello", "stream": True},
            transport=_StaticTransport(
                stream=_StaticStream(raw_chunks=[payload[:start], payload[start:]])
            ),
            extra_headers={"x-request-id": "req-redaction"},
            entry_id="log-redaction",
            stream_trace_state=trace_state,
        )
        assert isinstance(response, StreamingResponse)
        chunks: list[bytes] = []
        async for chunk in response._generator:
            assert isinstance(chunk, bytes)
            chunks.append(chunk)
        return b"".join(chunks)

    emitted = asyncio.run(run())

    assert emitted == payload.replace(token.encode(), b"[REDACTED]")
    assert token not in trace_path.read_text(encoding="utf-8")
    assert "stable" not in trace_path.read_text(encoding="utf-8")


def test_converted_stream_redacts_model_output_before_sse_and_trace(tmp_path) -> None:
    token = "converted-stream-secret"
    trace_path = tmp_path / "converted-trace.jsonl"
    trace_state = StreamTraceState(
        StreamTraceConfig(enabled=True, path=str(trace_path)), token_values=()
    )
    events = [
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 123,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": f"before {token} after",
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 123,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]

    async def run() -> str:
        response, _profile = await handle_streaming(
            _converted_route(),
            _provider(token),
            {"model": "test-model", "input": "hello", "stream": True},
            transport=_StaticTransport(stream=_StaticStream(events=events)),
            extra_headers={"x-request-id": "req-converted-redaction"},
            entry_id="log-converted-redaction",
            stream_trace_state=trace_state,
        )
        assert isinstance(response, StreamingResponse)
        chunks: list[str] = []
        async for chunk in response._generator:
            assert isinstance(chunk, str)
            chunks.append(chunk)
        return "".join(chunks)

    emitted = asyncio.run(run())

    assert token not in emitted
    assert "before [REDACTED] after" in emitted
    assert token not in trace_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("converted", [False, True])
def test_streaming_http_error_is_redacted_for_passthrough_and_conversion(
    tmp_path,
    converted: bool,
) -> None:
    token = "streaming-http-error-secret"
    trace_path = tmp_path / f"http-error-{converted}.jsonl"
    trace_state = StreamTraceState(
        StreamTraceConfig(enabled=True, path=str(trace_path)), token_values=()
    )
    route = _converted_route() if converted else _passthrough_route()

    response, _profile = asyncio.run(
        handle_streaming(
            route,
            _provider(token),
            {"model": "test-model", "input": "hello", "stream": True},
            transport=_StaticTransport(
                stream=_StaticStream(
                    status_code=401,
                    error=f'{{"error":{{"message":"failed with {token}"}}}}',
                )
            ),
            extra_headers={"x-request-id": f"req-http-error-{converted}"},
            entry_id=f"log-http-error-{converted}",
            stream_trace_state=trace_state,
        )
    )

    assert isinstance(response, Response)
    assert response.status_code == 401
    assert token.encode() not in response.body
    assert b"failed with [REDACTED]" in response.body
    if trace_path.exists():
        assert token not in trace_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("streaming", [False, True])
def test_proxy_redacts_transport_failure_before_client_and_diagnostics(
    tmp_path,
    streaming: bool,
) -> None:
    token = "provider-transport-exception-secret"
    failure = UpstreamConnectionError(f"connection reflected {token}")
    trace_path = tmp_path / f"failure-{streaming}.jsonl"
    trace_state = StreamTraceState(
        StreamTraceConfig(enabled=True, path=str(trace_path)), token_values=()
    )

    if streaming:
        response, _profile = asyncio.run(
            handle_streaming(
                _passthrough_route(),
                _provider(token),
                {"model": "test-model", "input": "hello", "stream": True},
                transport=_StaticTransport(failure=failure),
                extra_headers={"x-request-id": "req-failure"},
                entry_id="log-failure",
                stream_trace_state=trace_state,
            )
        )
    else:
        response, _profile = asyncio.run(
            handle_non_streaming(
                _passthrough_route(),
                _provider(token),
                {"model": "test-model", "input": "hello"},
                transport=_StaticTransport(failure=failure),
            )
        )

    assert isinstance(response, Response)
    assert response.status_code == 502
    assert token.encode() not in response.body
    if trace_path.exists():
        assert token not in trace_path.read_text(encoding="utf-8")
