"""Tests for upstream header forwarding from gateway handlers."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import llm_rosetta.gateway.app as app_module
from llm_rosetta._vendor.httpserver import JSONResponse, StreamingResponse
from llm_rosetta.gateway.headers import build_upstream_extra_headers
from llm_rosetta.routing import ResolvedRoute


def test_build_upstream_extra_headers_preserves_user_agent_and_responses_version():
    """Only explicitly supported client headers should be forwarded upstream."""
    request = MagicMock()
    request.headers = {
        "user-agent": "codex-cli/1.2.3",
        "openresponses-version": "2025-06-18",
        "authorization": "Bearer client-key",
    }

    headers = build_upstream_extra_headers(request, "req-123")

    assert headers == {
        "x-request-id": "req-123",
        "User-Agent": "codex-cli/1.2.3",
        "OpenResponses-Version": "2025-06-18",
    }


def test_proxy_handler_forwards_user_agent_to_non_streaming_proxy(monkeypatch):
    """The main proxy handler should pass client User-Agent to the upstream call."""
    captured_headers: dict[str, str] = {}

    class _Config:
        models = {"gpt-test": "test-provider"}

        def resolve(self, source_provider: str, model: str):
            return (
                ResolvedRoute(
                    source_provider=source_provider,
                    target_provider="openai_chat",
                    provider_name="test-provider",
                ),
                MagicMock(),
            )

    async def _fake_handle_non_streaming(*args: Any, **kwargs: Any):
        captured_headers.update(kwargs["extra_headers"])
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "_config", _Config())
    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_handle_non_streaming)

    request = MagicMock()
    request.headers = {"user-agent": "codex-cli/1.2.3"}
    request.json.return_value = {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hello"}],
    }
    request.app.metadata_store = MagicMock()
    request.app.metrics = None
    request.app.request_log = None
    request.app.persistence = None
    request.app.profiler_state = None

    response = asyncio.run(app_module._proxy_handler(request, "openai_chat"))

    assert response.status_code == 200
    assert captured_headers["User-Agent"] == "codex-cli/1.2.3"
    assert "x-request-id" in captured_headers


def test_proxy_handler_passes_codex_window_id_to_streaming_proxy(monkeypatch):
    """Codex window id scopes stream-only final-answer phase decisions."""
    captured_kwargs: dict[str, Any] = {}

    class _Config:
        models = {"glm-5.2": "test-provider"}
        web_search: dict[str, Any] = {}

        def resolve(self, source_provider: str, model: str):
            return (
                ResolvedRoute(
                    source_provider=source_provider,
                    target_provider="openai_chat",
                    provider_name="test-provider",
                ),
                MagicMock(),
            )

    async def _fake_handle_streaming(*args: Any, **kwargs: Any):
        captured_kwargs.update(kwargs)

        async def _empty_stream():
            if False:
                yield ""

        return StreamingResponse(_empty_stream(), content_type="text/event-stream"), {}

    monkeypatch.setattr(app_module, "_config", _Config())
    monkeypatch.setattr(app_module, "handle_streaming", _fake_handle_streaming)

    request = MagicMock()
    request.headers = {
        "user-agent": "codex-cli/1.2.3",
        "x-codex-window-id": "thread-abc:0",
    }
    request.json.return_value = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    request.app.metadata_store = MagicMock()
    request.app.metrics = None
    request.app.request_log = None
    request.app.persistence = None
    request.app.profiler_state = None
    request.app.stream_trace_state = None
    request.app.transport = MagicMock()

    response = asyncio.run(app_module._proxy_handler(request, "openai_responses"))

    assert response.status_code == 200
    assert captured_kwargs["codex_window_id"] == "thread-abc:0"
    assert captured_kwargs["tool_cache_session_id"] == "thread-abc:0"
    assert "x-codex-window-id" not in captured_kwargs["extra_headers"]


def test_proxy_handler_passes_codex_window_id_to_non_streaming_proxy(monkeypatch):
    """Codex window id is available to non-streaming request conversion."""
    captured_kwargs: dict[str, Any] = {}

    class _Config:
        models = {"glm-5.2": "test-provider"}

        def resolve(self, source_provider: str, model: str):
            return (
                ResolvedRoute(
                    source_provider=source_provider,
                    target_provider="openai_chat",
                    provider_name="test-provider",
                ),
                MagicMock(),
            )

    async def _fake_handle_non_streaming(*args: Any, **kwargs: Any):
        captured_kwargs.update(kwargs)
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "_config", _Config())
    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_handle_non_streaming)

    request = MagicMock()
    request.headers = {"x-codex-window-id": "thread-abc:0"}
    request.json.return_value = {
        "model": "glm-5.2",
        "input": [{"role": "user", "content": "hello"}],
    }
    request.app.metadata_store = MagicMock()
    request.app.metrics = None
    request.app.request_log = None
    request.app.persistence = None
    request.app.profiler_state = None
    request.app.transport = MagicMock()

    response = asyncio.run(app_module._proxy_handler(request, "openai_responses"))

    assert response.status_code == 200
    assert captured_kwargs["codex_window_id"] == "thread-abc:0"
    assert captured_kwargs["tool_cache_session_id"] == "thread-abc:0"
    assert "x-codex-window-id" not in captured_kwargs["extra_headers"]
