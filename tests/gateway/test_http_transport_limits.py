"""Bounded upstream HTTP response-body transport tests."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any, cast

import pytest

from codex_rosetta.gateway.transport import (
    ProviderInfo,
    UpstreamContentEncodingError,
    UpstreamProtocolError,
    UpstreamResponseTooLargeError,
    UpstreamSafetyError,
    UpstreamStreamLimitError,
)
from codex_rosetta.gateway.transport.http import transport as transport_module
from codex_rosetta.gateway.transport.http.transport import (
    HttpTransport,
    HttpUpstreamStream,
    request_bounded_response,
)
from codex_rosetta.gateway.web_search import TavilyHTTPClient, WebSearchSettings
import codex_rosetta.gateway.web_search as web_search_module


class _FakeStreamingResponse:
    def __init__(
        self,
        status_code: int,
        chunks: list[bytes],
        *,
        headers: dict[str, str] | None = None,
        lines: list[str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks
        self._lines = lines or []
        self.closed = False
        self.close_calls = 0
        self.iterated = False
        self.line_limit: int | None = None
        self.lines_yielded = 0

    async def aiter_bytes(self, chunk_size: int = 4096):
        del chunk_size
        self.iterated = True
        for chunk in self._chunks:
            await asyncio.sleep(0)
            yield chunk

    async def aiter_lines(self, max_line_bytes: int | None = None):
        self.line_limit = max_line_bytes
        for line in self._lines:
            await asyncio.sleep(0)
            self.lines_yielded += 1
            yield line

    async def aclose(self) -> None:
        self.close_calls += 1
        self.closed = True


class _FakeClient:
    def __init__(self, response: _FakeStreamingResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def post(self, _url: str, **kwargs: Any) -> _FakeStreamingResponse:
        self.calls.append(kwargs)
        return self.response


def _provider(base_url: str = "https://upstream.example/v1") -> ProviderInfo:
    return ProviderInfo(
        "test",
        api_key="provider-key",
        base_url=base_url,
        auth_header_fn=lambda key: {"Authorization": f"Bearer {key}"},
        url_template="{base_url}/chat/completions",
    )


def _transport(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeStreamingResponse,
) -> tuple[HttpTransport, _FakeClient]:
    monkeypatch.setattr(
        transport_module,
        "HttpStreamingResponse",
        _FakeStreamingResponse,
    )
    client = _FakeClient(response)
    transport = HttpTransport()
    transport._pool = cast(Any, SimpleNamespace(get=lambda _proxy=None: client))
    return transport, client


class _LocalUpstreamHandler(BaseHTTPRequestHandler):
    """Exercise the real vendored streaming client on loopback only."""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        del format, args

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length)) if length else {}
        server = cast(_LocalUpstreamServer, self.server)
        server.accept_encoding = self.headers.get("Accept-Encoding")
        case = body.get("case") or self.path.rstrip("/").rsplit("/", 1)[-1]
        if case == "content-length":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "6")
            self.end_headers()
            self.wfile.write(b"123456")
        elif case == "chunked-error":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for chunk in (b"123", b"456"):
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        elif case == "gzip":
            payload = gzip.compress(b'{"expanded":"payload"}')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif case == "eof":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"123456")
            self.close_connection = True
        elif case == "slow":
            server.slow_started.set()
            time.sleep(server.slow_delay)
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif case == "sse":
            payload = b'data: {"delta":"ok"}\n\ndata: [DONE]\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif case == "chunked-declared-huge":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"10000000\r\n" + b"x" * 64)
            self.wfile.flush()
        elif case == "sse-no-newline":
            payload = b"data: " + b"x" * 64
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif case == "sse-no-delimiter":
            payload = b"data: 12345\ndata: 12345\ndata: 12345\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif case == "sse-multiple":
            payload = (
                b'data: {"delta":"one"}\n\ndata: {"delta":"two"}\n\ndata: [DONE]\n\n'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif case == "response-headers":
            self.close_connection = True
            headers = b"".join(
                f"X-Test-{index}: value\r\n".encode() for index in range(101)
            )
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\n" + headers + b"Content-Length: 2\r\n\r\n{}"
            )
        elif case == "response-trailers":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            trailers = b"".join(
                f"X-Trailer-{index}: value\r\n".encode() for index in range(101)
            )
            self.wfile.write(b"2\r\n{}\r\n0\r\n" + trailers + b"\r\n")
        else:
            payload = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


class _LocalUpstreamServer(ThreadingHTTPServer):
    accept_encoding: str | None = None
    slow_started: threading.Event
    slow_delay: float


@pytest.fixture
def local_upstream() -> Iterator[tuple[_LocalUpstreamServer, str]]:
    server = _LocalUpstreamServer(("127.0.0.1", 0), _LocalUpstreamHandler)
    server.slow_started = threading.Event()
    server.slow_delay = 0.2
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_nonstream_success_reads_incrementally_and_forces_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeStreamingResponse(200, [b'{"ok":', b"true}"])
    transport, client = _transport(monkeypatch, response)

    result = asyncio.run(
        transport.send_request(
            _provider(),
            "openai_chat",
            {"model": "test"},
            "test",
            extra_headers={"accept-encoding": "gzip"},
        )
    )

    assert result.body == {"ok": True}
    assert result.raw_content == b'{"ok":true}'
    assert response.closed is True
    assert client.calls[0]["stream"] is True
    headers = client.calls[0]["headers"]
    assert headers["Accept-Encoding"] == "identity"
    assert "accept-encoding" not in headers


@pytest.mark.parametrize("case", ["response-headers", "response-trailers"])
def test_real_header_and_trailer_overflow_map_to_safety_error(
    local_upstream: tuple[_LocalUpstreamServer, str],
    case: str,
) -> None:
    _server, base_url = local_upstream

    async def _scenario() -> None:
        transport = HttpTransport(timeout=1)
        try:
            with pytest.raises(UpstreamSafetyError, match="header"):
                await transport.send_request(
                    _provider(base_url),
                    "openai_chat",
                    {"model": "test", "case": case},
                    "test",
                )
        finally:
            await transport.close()

    asyncio.run(_scenario())


def test_content_length_rejects_oversized_success_before_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport_module, "MAX_UPSTREAM_SUCCESS_BODY_BYTES", 5)
    response = _FakeStreamingResponse(
        200,
        [b"ignored"],
        headers={"content-length": "6"},
    )
    transport, _client = _transport(monkeypatch, response)

    with pytest.raises(UpstreamResponseTooLargeError, match="exceeds 5"):
        asyncio.run(
            transport.send_request(
                _provider(), "openai_chat", {"model": "test"}, "test"
            )
        )

    assert response.iterated is False
    assert response.closed is True


def test_unknown_or_chunked_body_is_bounded_incrementally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport_module, "MAX_UPSTREAM_ERROR_BODY_BYTES", 5)
    response = _FakeStreamingResponse(500, [b"123", b"456"])
    transport, _client = _transport(monkeypatch, response)

    with pytest.raises(UpstreamResponseTooLargeError, match="exceeds 5"):
        asyncio.run(
            transport.send_passthrough(
                _provider(),
                "https://upstream.example/v1/embeddings",
                {"model": "test"},
            )
        )

    assert response.iterated is True
    assert response.closed is True


def test_compressed_response_is_rejected_without_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeStreamingResponse(
        200,
        [b"tiny-gzip-bomb"],
        headers={"content-encoding": "gzip"},
    )
    transport, _client = _transport(monkeypatch, response)

    with pytest.raises(UpstreamContentEncodingError, match="identity required"):
        asyncio.run(
            transport.send_request(
                _provider(), "openai_chat", {"model": "test"}, "test"
            )
        )

    assert response.iterated is False
    assert response.closed is True


def test_stream_error_body_is_bounded_but_success_sse_remains_incremental(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport_module, "MAX_UPSTREAM_ERROR_BODY_BYTES", 5)
    error_response = _FakeStreamingResponse(429, [b"123", b"456"])
    error_transport, _client = _transport(monkeypatch, error_response)
    with pytest.raises(UpstreamResponseTooLargeError, match="exceeds 5"):
        asyncio.run(
            error_transport.send_streaming(
                _provider(), "openai_chat", {"model": "test"}, "test"
            )
        )
    assert error_response.closed is True

    success_response = _FakeStreamingResponse(
        200,
        [],
        lines=['data: {"delta":"ok"}', "", "data: [DONE]", ""],
    )
    success_transport, _client = _transport(monkeypatch, success_response)

    async def _collect() -> list[dict[str, Any]]:
        stream = await success_transport.send_streaming(
            _provider(), "openai_chat", {"model": "test"}, "test"
        )
        try:
            return [event async for event in stream]
        finally:
            await stream.close()

    assert asyncio.run(_collect()) == [{"delta": "ok"}]
    assert success_response.closed is True
    assert success_response.line_limit == transport_module.MAX_UPSTREAM_SSE_LINE_BYTES


def test_stream_http_error_body_and_outer_cleanup_close_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeStreamingResponse(429, [b'{"error":"limited"}'])
    transport, _client = _transport(monkeypatch, response)

    async def _read() -> str:
        stream = await transport.send_streaming(
            _provider(), "openai_chat", {"model": "test"}, "test"
        )
        error_text = await stream.read_error()
        await stream.close()
        await stream.close()
        return error_text

    assert asyncio.run(_read()) == '{"error":"limited"}'
    assert response.close_calls == 1


@pytest.mark.parametrize(
    "malformed_data",
    [
        "configured-token",
        "Bearer bearer-secret",
        "private prompt text",
        "password=hunter2",
    ],
)
def test_malformed_sse_fails_closed_without_logging_body(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    malformed_data: str,
) -> None:
    response = _FakeStreamingResponse(
        200,
        [],
        lines=[
            f"data: {malformed_data}",
            "",
            'data: {"after":"must-not-yield"}',
            "",
        ],
    )
    transport, _client = _transport(monkeypatch, response)

    async def _collect() -> None:
        stream = await transport.send_streaming(
            _provider(), "openai_chat", {"model": "test"}, "test"
        )
        with pytest.raises(
            UpstreamProtocolError,
            match="^Upstream SSE data is not valid JSON$",
        ):
            _ = [event async for event in stream]
        # Simulate the outer async-context cleanup after the inner transport
        # has already closed on the protocol failure.
        await stream.close()

    caplog.set_level(logging.DEBUG, logger="codex-rosetta-gateway")
    caplog.set_level(logging.DEBUG, logger="codex-rosetta-gateway.body")
    with caplog.at_level(logging.DEBUG):
        asyncio.run(_collect())

    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert malformed_data not in rendered_logs
    assert "must-not-yield" not in rendered_logs
    assert response.lines_yielded == 2
    assert response.close_calls == 1


def test_sse_comments_empty_data_done_and_json_keep_existing_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeStreamingResponse(
        200,
        [],
        lines=[
            ": keepalive",
            "",
            "data:",
            "",
            'data: {"delta":"ok"}',
            "",
            "data: [DONE]",
            "",
            'data: {"after":"done"}',
            "",
        ],
    )
    transport, _client = _transport(monkeypatch, response)

    async def _collect() -> list[dict[str, Any]]:
        stream = await transport.send_streaming(
            _provider(), "openai_chat", {"model": "test"}, "test"
        )
        async with stream:
            return [event async for event in stream]

    assert asyncio.run(_collect()) == [{"delta": "ok"}]
    assert response.close_calls == 1
    assert response.lines_yielded == 8


def test_raw_passthrough_preserves_malformed_json_without_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_chunk = b"data: password=hunter2\n\n"
    response = _FakeStreamingResponse(200, [raw_chunk])
    transport, _client = _transport(monkeypatch, response)

    async def _collect() -> bytes:
        stream = await transport.send_streaming(
            _provider(), "openai_responses", {"model": "test"}, "test"
        )
        async with stream:
            raw = stream.aiter_raw_bytes()
            assert raw is not None
            return b"".join([chunk async for chunk in raw])

    assert asyncio.run(_collect()) == raw_chunk
    assert response.close_calls == 1


def test_success_sse_event_limit_maps_to_domain_error_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport_module, "MAX_UPSTREAM_SSE_EVENT_BYTES", 10)
    response = _FakeStreamingResponse(
        200,
        [],
        lines=["data: 12345", "data: 12345", ""],
    )
    transport, _client = _transport(monkeypatch, response)

    async def _collect() -> None:
        stream = await transport.send_streaming(
            _provider(), "openai_chat", {"model": "test"}, "test"
        )
        _ = [event async for event in stream]

    with pytest.raises(UpstreamStreamLimitError, match="SSE event exceeds 10"):
        asyncio.run(_collect())
    assert response.closed is True


def test_cancelled_body_read_closes_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()

    class _BlockingResponse(_FakeStreamingResponse):
        async def aiter_bytes(self, chunk_size: int = 4096):
            del chunk_size
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

    response = _BlockingResponse(200, [])
    transport, _client = _transport(monkeypatch, response)

    async def _cancel() -> None:
        task = asyncio.create_task(
            transport.send_request(
                _provider(), "openai_chat", {"model": "test"}, "test"
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_cancel())
    assert response.closed is True


def test_cancelled_raw_passthrough_closes_stream() -> None:
    started = asyncio.Event()

    class _BlockingResponse(_FakeStreamingResponse):
        async def aiter_bytes(self, chunk_size: int = 4096):
            del chunk_size
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

    response = _BlockingResponse(200, [])
    stream = HttpUpstreamStream(cast(Any, response))

    async def _cancel() -> None:
        raw = stream.aiter_raw_bytes()

        async def _next_chunk() -> bytes:
            return await raw.__anext__()

        task = asyncio.create_task(_next_chunk())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_cancel())
    assert response.closed is True
    assert response.close_calls == 1


def test_cancelled_parsed_sse_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    class _BlockingResponse(_FakeStreamingResponse):
        async def aiter_lines(self, max_line_bytes: int | None = None):
            self.line_limit = max_line_bytes
            started.set()
            await asyncio.Event().wait()
            yield 'data: {"unreachable":true}'

    response = _BlockingResponse(200, [])
    transport, _client = _transport(monkeypatch, response)

    async def _cancel() -> None:
        stream = await transport.send_streaming(
            _provider(), "openai_chat", {"model": "test"}, "test"
        )

        async def _collect() -> list[dict[str, Any]]:
            return [event async for event in stream]

        task = asyncio.create_task(_collect())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.close()

    asyncio.run(_cancel())
    assert response.close_calls == 1


def test_real_client_rejects_oversized_content_length(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
) -> None:
    server, base_url = local_upstream
    monkeypatch.setattr(transport_module, "MAX_UPSTREAM_SUCCESS_BODY_BYTES", 5)
    transport = HttpTransport()
    try:
        with pytest.raises(UpstreamResponseTooLargeError, match="exceeds 5"):
            asyncio.run(
                transport.send_request(
                    _provider(base_url),
                    "openai_chat",
                    {"model": "test", "case": "content-length"},
                    "test",
                )
            )
        assert server.accept_encoding == "identity"
    finally:
        asyncio.run(transport.close())


def test_real_client_bounds_chunked_stream_error(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
) -> None:
    _server, base_url = local_upstream
    monkeypatch.setattr(transport_module, "MAX_UPSTREAM_ERROR_BODY_BYTES", 5)
    transport = HttpTransport()
    try:
        with pytest.raises(UpstreamResponseTooLargeError, match="exceeds 5"):
            asyncio.run(
                transport.send_streaming(
                    _provider(base_url),
                    "openai_chat",
                    {"model": "test", "case": "chunked-error"},
                    "test",
                )
            )
    finally:
        asyncio.run(transport.close())


def test_real_client_bounds_huge_declared_chunk_before_full_payload(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
) -> None:
    _server, base_url = local_upstream
    monkeypatch.setattr(transport_module, "MAX_UPSTREAM_SUCCESS_BODY_BYTES", 5)
    transport = HttpTransport()
    try:
        with pytest.raises(UpstreamResponseTooLargeError, match="exceeds 5"):
            asyncio.run(
                transport.send_request(
                    _provider(base_url),
                    "openai_chat",
                    {"model": "test", "case": "chunked-declared-huge"},
                    "test",
                )
            )
    finally:
        asyncio.run(transport.close())


@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_auxiliary_reader_enforces_explicit_four_mib_boundary_without_partial_result(
    monkeypatch: pytest.MonkeyPatch,
    extra_bytes: int,
) -> None:
    limit = 4 * 1024 * 1024
    chunks = [b"x" * limit]
    if extra_bytes:
        chunks.append(b"x")
    response = _FakeStreamingResponse(200, chunks)
    monkeypatch.setattr(
        transport_module,
        "HttpStreamingResponse",
        _FakeStreamingResponse,
    )

    class _AuxClient:
        async def request(self, *args: Any, **kwargs: Any) -> _FakeStreamingResponse:
            return response

    async def _read():
        return await request_bounded_response(
            _AuxClient(),
            "POST",
            "https://upstream.example/v1/test",
            max_success_bytes=limit,
            max_error_bytes=limit,
        )

    if extra_bytes:
        with pytest.raises(
            UpstreamResponseTooLargeError,
            match=f"exceeds {limit} bytes",
        ):
            asyncio.run(_read())
    else:
        result = asyncio.run(_read())
        assert result.content == b"x" * limit

    assert response.closed is True


@pytest.mark.parametrize(
    "case,limit_name,limit,error_match",
    [
        ("sse-no-newline", "MAX_UPSTREAM_SSE_LINE_BYTES", 16, "SSE line"),
        ("sse-no-delimiter", "MAX_UPSTREAM_SSE_EVENT_BYTES", 10, "SSE event"),
    ],
)
def test_real_client_maps_sse_limits_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
    case: str,
    limit_name: str,
    limit: int,
    error_match: str,
) -> None:
    _server, base_url = local_upstream
    monkeypatch.setattr(transport_module, limit_name, limit)
    transport = HttpTransport()
    captured_stream: HttpUpstreamStream | None = None

    async def _collect() -> None:
        nonlocal captured_stream
        captured_stream = await transport.send_streaming(
            _provider(base_url),
            "openai_chat",
            {"model": "test", "case": case},
            "test",
        )
        _ = [event async for event in captured_stream]

    try:
        with pytest.raises(UpstreamStreamLimitError, match=error_match):
            asyncio.run(_collect())
        assert captured_stream is not None
        assert captured_stream._resp._closed is True
    finally:
        asyncio.run(transport.close())


def test_real_raw_passthrough_preserves_bytes_and_enforces_event_limit(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
) -> None:
    _server, base_url = local_upstream
    transport = HttpTransport()

    async def _read(case: str) -> tuple[HttpUpstreamStream, bytes]:
        stream = await transport.send_streaming(
            _provider(base_url),
            "openai_responses",
            {"model": "test", "case": case},
            "test",
        )
        return stream, b"".join([chunk async for chunk in stream.aiter_raw_bytes()])

    try:
        _stream, raw = asyncio.run(_read("sse-multiple"))
        assert raw == (
            b'data: {"delta":"one"}\n\ndata: {"delta":"two"}\n\ndata: [DONE]\n\n'
        )

        monkeypatch.setattr(transport_module, "MAX_UPSTREAM_SSE_EVENT_BYTES", 10)
        limited_stream: HttpUpstreamStream | None = None

        async def _read_limited() -> None:
            nonlocal limited_stream
            limited_stream = await transport.send_streaming(
                _provider(base_url),
                "openai_responses",
                {"model": "test", "case": "sse-no-delimiter"},
                "test",
            )
            _ = [chunk async for chunk in limited_stream.aiter_raw_bytes()]

        with pytest.raises(UpstreamStreamLimitError, match="SSE event exceeds 10"):
            asyncio.run(_read_limited())
        assert limited_stream is not None
        assert limited_stream._resp._closed is True
    finally:
        asyncio.run(transport.close())


def test_real_client_rejects_gzip_and_preserves_normal_sse(
    local_upstream: tuple[_LocalUpstreamServer, str],
) -> None:
    _server, base_url = local_upstream
    transport = HttpTransport()
    try:
        with pytest.raises(UpstreamContentEncodingError, match="identity required"):
            asyncio.run(
                transport.send_request(
                    _provider(base_url),
                    "openai_chat",
                    {"model": "test", "case": "gzip"},
                    "test",
                )
            )

        async def _collect() -> list[dict[str, Any]]:
            stream = await transport.send_streaming(
                _provider(base_url),
                "openai_chat",
                {"model": "test", "case": "sse"},
                "test",
            )
            try:
                return [event async for event in stream]
            finally:
                await stream.close()

        assert asyncio.run(_collect()) == [{"delta": "ok"}]

        async def _collect_multiple() -> list[dict[str, Any]]:
            stream = await transport.send_streaming(
                _provider(base_url),
                "openai_chat",
                {"model": "test", "case": "sse-multiple"},
                "test",
            )
            try:
                return [event async for event in stream]
            finally:
                await stream.close()

        assert asyncio.run(_collect_multiple()) == [
            {"delta": "one"},
            {"delta": "two"},
        ]
    finally:
        asyncio.run(transport.close())


def test_tavily_real_loopback_normal_json_forces_identity(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
) -> None:
    server, base_url = local_upstream
    monkeypatch.setattr(web_search_module, "TAVILY_SEARCH_URL", f"{base_url}/normal")

    result = asyncio.run(
        TavilyHTTPClient("tvly-test").search(
            "query",
            settings=WebSearchSettings(),
        )
    )

    assert result == {"ok": True}
    assert server.accept_encoding == "identity"


@pytest.mark.parametrize(
    ("case", "limit_name", "error_match"),
    [
        ("content-length", "MAX_UPSTREAM_SUCCESS_BODY_BYTES", "exceeds 5"),
        ("chunked-error", "MAX_UPSTREAM_ERROR_BODY_BYTES", "exceeds 5"),
        ("eof", "MAX_UPSTREAM_SUCCESS_BODY_BYTES", "exceeds 5"),
    ],
)
def test_tavily_real_loopback_bounds_all_body_framings(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
    case: str,
    limit_name: str,
    error_match: str,
) -> None:
    _server, base_url = local_upstream
    monkeypatch.setattr(transport_module, limit_name, 5)
    monkeypatch.setattr(web_search_module, "TAVILY_SEARCH_URL", f"{base_url}/{case}")

    with pytest.raises(RuntimeError, match=error_match):
        asyncio.run(
            TavilyHTTPClient("tvly-test").search(
                "query",
                settings=WebSearchSettings(),
            )
        )


def test_tavily_real_loopback_rejects_compressed_response(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
) -> None:
    _server, base_url = local_upstream
    monkeypatch.setattr(web_search_module, "TAVILY_SEARCH_URL", f"{base_url}/gzip")

    with pytest.raises(RuntimeError, match="identity required"):
        asyncio.run(
            TavilyHTTPClient("tvly-test").search(
                "query",
                settings=WebSearchSettings(),
            )
        )


def test_tavily_real_loopback_timeout(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
) -> None:
    _server, base_url = local_upstream
    monkeypatch.setattr(web_search_module, "TAVILY_SEARCH_URL", f"{base_url}/slow")

    with pytest.raises(RuntimeError, match="Tavily request failed"):
        asyncio.run(
            TavilyHTTPClient("tvly-test", timeout=0.02).search(
                "query",
                settings=WebSearchSettings(),
            )
        )


def test_tavily_real_loopback_cancel(
    monkeypatch: pytest.MonkeyPatch,
    local_upstream: tuple[_LocalUpstreamServer, str],
) -> None:
    server, base_url = local_upstream
    monkeypatch.setattr(web_search_module, "TAVILY_SEARCH_URL", f"{base_url}/slow")
    server.slow_delay = 2

    async def _cancel() -> None:
        task = asyncio.create_task(
            TavilyHTTPClient("tvly-test", timeout=5).search(
                "query",
                settings=WebSearchSettings(),
            )
        )
        assert await asyncio.to_thread(server.slow_started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_cancel())
