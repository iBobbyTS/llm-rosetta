"""Request-local identity, isolation, cleanup, and window continuity tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import codex_rosetta.gateway.app as app_module
from codex_rosetta._vendor.httpserver import JSONResponse
from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.gateway.auth import api_key_principal_var
from codex_rosetta.gateway.proxy import (
    ProviderMetadataCapacityError,
    ProviderMetadataStore,
)
from codex_rosetta.gateway.tool_adaptation import (
    CodexToolLocalizationStore,
    LocalizedToolMapping,
)
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


def _request(*, marker: str, window_id: str | None = None) -> SimpleNamespace:
    headers = {"x-request-id": "reused-correlation-id"}
    if window_id is not None:
        headers["x-codex-window-id"] = window_id
    app = SimpleNamespace(
        metadata_store=ProviderMetadataStore(),
        codex_tool_store=CodexToolLocalizationStore(),
        transport=MagicMock(),
        metrics=None,
        request_log=None,
        persistence=None,
        profiler_state=None,
        stream_trace_state=None,
        upstream_error_log_state=None,
        image_fetch_workers=None,
        gateway_config=_Config(),
    )
    return SimpleNamespace(
        headers=headers,
        json=lambda: {"model": "gpt-test", "marker": marker},
        client_addr=("127.0.0.1", 12345),
        app=app,
    )


def _metadata_request() -> dict[str, Any]:
    return {
        "messages": [
            {"content": [{"type": "tool_call", "tool_call_id": "same-call-id"}]}
        ]
    }


def _metadata_response(signature: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": [
                        {
                            "type": "tool_call",
                            "tool_call_id": "same-call-id",
                            "provider_metadata": {"signature": signature},
                        }
                    ]
                }
            }
        ]
    }


def _remember_all_state(kwargs: dict[str, Any], marker: str) -> str | None:
    scope = kwargs["state_scope"]
    metadata = kwargs["metadata_store"].scoped(scope)
    localization = kwargs["codex_tool_store"].scoped(scope)
    request_body = _metadata_request()
    metadata.inject_into_request(request_body)
    signature = (
        request_body["messages"][0]["content"][0]
        .get("provider_metadata", {})
        .get("signature")
    )
    metadata.cache_from_response(_metadata_response(marker))
    localization.remember(
        LocalizedToolMapping("same-call-id", "Read", {}, "exec_command", {})
    )
    return signature


def _assert_request_state_empty(request: SimpleNamespace) -> None:
    assert len(request.app.metadata_store) == 0
    assert len(request.app.codex_tool_store) == 0


def test_reused_request_id_is_sequentially_isolated_and_cleaned(monkeypatch):
    observed: list[tuple[Any, str | None]] = []

    async def _fake_handle(*args: Any, **kwargs: Any):
        marker = args[2]["marker"]
        observed.append((kwargs["state_scope"], _remember_all_state(kwargs, marker)))
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_handle)
    request = _request(marker="first")
    token = api_key_principal_var.set("shared-principal")
    try:
        first = asyncio.run(app_module._proxy_handler(request, "openai_responses"))
        request.json = lambda: {"model": "gpt-test", "marker": "second"}
        second = asyncio.run(app_module._proxy_handler(request, "openai_responses"))
    finally:
        api_key_principal_var.reset(token)

    assert first.headers["x-request-id"] == "reused-correlation-id"
    assert second.headers["x-request-id"] == "reused-correlation-id"
    assert observed[0][0] != observed[1][0]
    assert [signature for _scope, signature in observed] == [None, None]
    _assert_request_state_empty(request)


def test_reused_request_id_is_concurrently_isolated_and_cleaned(monkeypatch):
    ready = 0
    release = asyncio.Event()
    observed: list[tuple[Any, str | None]] = []

    async def _fake_handle(*args: Any, **kwargs: Any):
        nonlocal ready
        marker = args[2]["marker"]
        _remember_all_state(kwargs, marker)
        ready += 1
        if ready == 2:
            release.set()
        await release.wait()
        request_body = _metadata_request()
        kwargs["metadata_store"].scoped(kwargs["state_scope"]).inject_into_request(
            request_body
        )
        signature = request_body["messages"][0]["content"][0]["provider_metadata"][
            "signature"
        ]
        observed.append((kwargs["state_scope"], signature))
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_handle)
    request_a = _request(marker="a")
    request_b = _request(marker="b")
    request_b.app = request_a.app

    async def _run_both():
        return await asyncio.gather(
            app_module._proxy_handler(request_a, "openai_responses"),
            app_module._proxy_handler(request_b, "openai_responses"),
        )

    token = api_key_principal_var.set("shared-principal")
    try:
        asyncio.run(_run_both())
    finally:
        api_key_principal_var.reset(token)

    assert len({scope for scope, _signature in observed}) == 2
    assert {signature for _scope, signature in observed} == {"a", "b"}
    _assert_request_state_empty(request_a)


def test_real_window_scope_retains_continuity(monkeypatch):
    observed: list[tuple[Any, str | None]] = []

    async def _fake_handle(*args: Any, **kwargs: Any):
        marker = args[2]["marker"]
        observed.append((kwargs["state_scope"], _remember_all_state(kwargs, marker)))
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_handle)
    request = _request(marker="first", window_id="thread-1:0")
    token = api_key_principal_var.set("shared-principal")
    try:
        asyncio.run(app_module._proxy_handler(request, "openai_responses"))
        request.json = lambda: {"model": "gpt-test", "marker": "second"}
        asyncio.run(app_module._proxy_handler(request, "openai_responses"))
    finally:
        api_key_principal_var.reset(token)

    assert observed[0][0] == observed[1][0]
    assert observed[0][0].persistent is True
    assert [signature for _scope, signature in observed] == [None, "first"]
    assert len(request.app.metadata_store) == 1


def test_provider_metadata_capacity_error_maps_to_stable_413(monkeypatch):
    async def _reject(*args: Any, **kwargs: Any):
        raise ProviderMetadataCapacityError(
            "provider_metadata scope exceeds 8388608 bytes"
        )

    monkeypatch.setattr(app_module, "handle_non_streaming", _reject)
    request = _request(marker="rejected")
    token = api_key_principal_var.set("shared-principal")
    try:
        response = asyncio.run(app_module._proxy_handler(request, "openai_responses"))
    finally:
        api_key_principal_var.reset(token)

    assert response.status_code == 413
    assert isinstance(response, JSONResponse)
    assert b"provider_metadata scope exceeds 8388608 bytes" in response.body
    assert response.headers["x-request-id"] == "reused-correlation-id"
    _assert_request_state_empty(request)
