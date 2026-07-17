"""Route-level lifecycle tests for streaming telemetry finalization."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

import codex_rosetta.gateway.app as app_module
from codex_rosetta._vendor.httpserver import StreamingResponse
from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.gateway.auth import api_key_principal_var
from codex_rosetta.gateway.proxy import ProviderMetadataStore
from codex_rosetta.gateway.tool_adaptation import (
    CodexToolLocalizationStore,
    LocalizedToolMapping,
)
from codex_rosetta.gateway.transport import UpstreamNetworkError, UpstreamProtocolError
from codex_rosetta.observability import MetricsCollector, RequestLog
from codex_rosetta.routing import ResolvedRoute


class _Config:
    models = {"gpt-test": "test-provider"}
    web_search: dict[str, Any] = {}

    def resolve(self, source_provider: ProviderType, model: str):
        return (
            ResolvedRoute(
                source_provider=source_provider,
                target_provider="openai_chat",
                provider_name="test-provider",
            ),
            MagicMock(),
        )


def _request() -> SimpleNamespace:
    app = SimpleNamespace(
        metadata_store=ProviderMetadataStore(),
        codex_tool_store=CodexToolLocalizationStore(),
        transport=MagicMock(),
        metrics=MetricsCollector(),
        request_log=RequestLog(),
        persistence=None,
        profiler_state=None,
        stream_trace_state=None,
        gateway_config=_Config(),
    )
    return SimpleNamespace(
        headers={},
        json=lambda: {"model": "gpt-test", "input": [], "stream": True},
        client_addr=("127.0.0.1", 12345),
        app=app,
    )


async def _open_stream(
    monkeypatch: pytest.MonkeyPatch,
    generator: Any,
) -> tuple[StreamingResponse, SimpleNamespace]:
    async def _fake_handle_streaming(*args: Any, **kwargs: Any):
        scope = kwargs["state_scope"]
        kwargs["metadata_store"].scoped(scope).cache_from_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {
                                    "type": "tool_call",
                                    "tool_call_id": "call-stream",
                                    "provider_metadata": {"signature": "secret"},
                                }
                            ]
                        }
                    }
                ]
            }
        )
        kwargs["codex_tool_store"].scoped(scope).remember(
            LocalizedToolMapping("call-stream", "Read", {}, "exec_command", {})
        )
        return StreamingResponse(generator(), content_type="text/event-stream"), {
            "stream_connect_ms": 1.0
        }

    monkeypatch.setattr(app_module, "handle_streaming", _fake_handle_streaming)
    request = _request()
    token = api_key_principal_var.set("test-client")
    try:
        response = await app_module._proxy_handler(request, "openai_responses")
    finally:
        api_key_principal_var.reset(token)
    assert isinstance(response, StreamingResponse)
    return response, request


def _assert_request_state_empty(request: SimpleNamespace) -> None:
    assert len(request.app.metadata_store) == 0
    assert len(request.app.codex_tool_store) == 0


def test_stream_metrics_remain_open_until_normal_generator_completion(monkeypatch):
    async def _stream():
        yield "first"
        yield "second"

    async def _scenario():
        response, request = await _open_stream(monkeypatch, _stream)
        metrics = request.app.metrics
        request_log = request.app.request_log

        assert metrics.active_streams == 1
        assert metrics.total_requests == 0
        entries, total = request_log.get_entries()
        assert total == 1
        assert entries[0]["status_code"] == 200

        assert await response._generator.__anext__() == "first"
        assert metrics.active_streams == 1
        assert metrics.total_requests == 0

        remaining = [chunk async for chunk in response._generator]
        assert remaining == ["second"]
        assert metrics.active_streams == 0
        assert metrics.total_requests == 1
        assert metrics.total_errors == 0
        assert metrics.total_streams == 1
        assert metrics.by_status_code == {200: 1}
        final_entries, _ = request_log.get_entries()
        assert final_entries[0]["status_code"] == 200
        assert final_entries[0]["profile"]["stream_complete"] is True
        _assert_request_state_empty(request)

    asyncio.run(_scenario())


def test_stream_generator_failure_records_502_and_provider_error(monkeypatch):
    async def _stream():
        yield "first"
        raise RuntimeError("upstream stream exploded")

    async def _scenario():
        response, request = await _open_stream(monkeypatch, _stream)
        assert await response._generator.__anext__() == "first"

        with pytest.raises(RuntimeError, match="upstream stream exploded"):
            await response._generator.__anext__()

        metrics = request.app.metrics
        assert metrics.active_streams == 0
        assert metrics.total_requests == 1
        assert metrics.total_errors == 1
        assert metrics.by_status_code == {502: 1}
        health = metrics.provider_health_snapshot()["test-provider"]
        assert health["success_rate"] == 0.0
        assert health["last_error"] == "upstream stream exploded"

        entries, total = request.app.request_log.get_entries()
        assert total == 1
        assert entries[0]["status_code"] == 502
        assert entries[0]["error_detail"] == "upstream stream exploded"
        assert entries[0]["profile"]["stream_complete"] is False
        _assert_request_state_empty(request)

    asyncio.run(_scenario())


def test_stream_protocol_failure_records_stable_502_exactly_once(monkeypatch):
    async def _stream():
        raise UpstreamProtocolError("Upstream SSE data is not valid JSON")
        yield "unreachable"

    async def _scenario():
        response, request = await _open_stream(monkeypatch, _stream)

        with pytest.raises(
            UpstreamProtocolError,
            match="^Upstream SSE data is not valid JSON$",
        ):
            await response._generator.__anext__()
        with pytest.raises(StopAsyncIteration):
            await response._generator.__anext__()

        metrics = request.app.metrics
        assert metrics.active_streams == 0
        assert metrics.total_requests == 1
        assert metrics.total_errors == 1
        assert metrics.by_status_code == {502: 1}

        entries, total = request.app.request_log.get_entries()
        assert total == 1
        assert entries[0]["status_code"] == 502
        assert entries[0]["error_detail"] == "Upstream SSE data is not valid JSON"
        assert entries[0]["profile"]["stream_complete"] is False
        assert entries[0]["profile"]["stream_error"] == (
            "Upstream SSE data is not valid JSON"
        )
        _assert_request_state_empty(request)

    asyncio.run(_scenario())


def test_stream_network_failure_logs_one_error_without_escaping(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _stream():
        raise UpstreamNetworkError("Streaming read timed out for upstream")
        yield "unreachable"

    async def _scenario():
        response, request = await _open_stream(monkeypatch, _stream)

        with pytest.raises(StopAsyncIteration):
            await response._generator.__anext__()

        metrics = request.app.metrics
        assert metrics.total_requests == 1
        assert metrics.total_errors == 1
        assert metrics.by_status_code == {502: 1}
        entries, total = request.app.request_log.get_entries()
        assert total == 1
        assert entries[0]["status_code"] == 502
        assert entries[0]["error_detail"] == "Streaming read timed out for upstream"
        _assert_request_state_empty(request)

    caplog.set_level("ERROR", logger="codex-rosetta-gateway")
    asyncio.run(_scenario())

    error_records = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(error_records) == 1
    assert error_records[0].getMessage() == (
        "Upstream stream disconnected: Streaming read timed out for upstream"
    )
    assert error_records[0].exc_info is None


def test_stream_aclose_records_499_once_without_double_finalize(monkeypatch):
    async def _stream():
        yield "first"
        await asyncio.Event().wait()

    async def _scenario():
        response, request = await _open_stream(monkeypatch, _stream)
        assert await response._generator.__anext__() == "first"

        instrumented = cast(Any, response._generator)
        await instrumented.aclose()
        await instrumented.aclose()

        metrics = request.app.metrics
        assert metrics.active_streams == 0
        assert metrics.total_requests == 1
        assert metrics.total_errors == 1
        assert metrics.total_streams == 1
        assert metrics.by_status_code == {499: 1}
        entries, total = request.app.request_log.get_entries()
        assert total == 1
        assert entries[0]["status_code"] == 499
        assert entries[0]["error_detail"] == "Stream closed before completion"
        assert entries[0]["profile"]["stream_complete"] is False
        _assert_request_state_empty(request)

    asyncio.run(_scenario())


def test_stream_task_cancellation_records_499(monkeypatch):
    async def _stream():
        yield "first"
        await asyncio.Event().wait()

    async def _scenario():
        response, request = await _open_stream(monkeypatch, _stream)
        assert await response._generator.__anext__() == "first"

        async def _next_chunk():
            return await response._generator.__anext__()

        pending = asyncio.create_task(_next_chunk())
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

        metrics = request.app.metrics
        assert metrics.active_streams == 0
        assert metrics.total_requests == 1
        assert metrics.by_status_code == {499: 1}
        entries, total = request.app.request_log.get_entries()
        assert total == 1
        assert entries[0]["status_code"] == 499
        assert entries[0]["error_detail"] == ("Stream cancelled or client disconnected")
        _assert_request_state_empty(request)

    asyncio.run(_scenario())
