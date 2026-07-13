"""Tests for upstream header forwarding from gateway handlers."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import MagicMock

import codex_rosetta.gateway.app as app_module
import pytest
from codex_rosetta._vendor.httpserver import JSONResponse, Request, StreamingResponse
from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.gateway.auth import api_key_principal_var
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.admin.routes.config import reload_config
from codex_rosetta.gateway.headers import (
    MAX_REQUEST_ID_BYTES,
    build_upstream_extra_headers,
    resolve_request_id,
)
from codex_rosetta.routing import ResolvedRoute


def _gateway_config(*, admin_cors_origins: list[str] | None = None) -> dict[str, Any]:
    return {
        "providers": {
            "test-provider": {
                "api_key": "sk-test",
                "base_url": "https://api.example.test/v1",
                "type": "openai",
            }
        },
        "model_groups": {
            "test": {
                "provider": "test-provider",
                "type": "llm",
                "models": {"gpt-test": {}},
            }
        },
        "server": {
            "admin_password": "test-admin-password",
            "api_keys": [
                {
                    "id": "test-client",
                    "label": "Test client",
                    "key": "test-gateway-key",
                }
            ],
            "admin_cors_origins": admin_cors_origins or [],
        },
    }


def _app_request(
    app: Any,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
) -> Request:
    return Request(
        method=method,
        path=path,
        query_string="",
        headers=headers,
        body=b"",
        client_addr=("198.51.100.10", 12345),
        app=app,
    )


def test_create_app_stores_resolved_codex_home(tmp_path):
    codex_home = tmp_path / "codex-home"

    app: Any = app_module.create_app(
        GatewayConfig(_gateway_config()), codex_home=str(codex_home)
    )

    assert app.codex_home == str(codex_home)


@pytest.fixture(autouse=True)
def _authenticated_principal():
    token = api_key_principal_var.set("test-client")
    yield
    api_key_principal_var.reset(token)


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


def test_request_id_boundary_accepts_exact_limit_and_generates_missing_id():
    exact = "r" * MAX_REQUEST_ID_BYTES
    generated = resolve_request_id(None)

    assert resolve_request_id(exact) == exact
    assert str(uuid.UUID(generated)) == generated


@pytest.mark.parametrize(
    "request_id",
    ["", " ", "req\x1b[2J", "req\x7f", "请求", "r" * (MAX_REQUEST_ID_BYTES + 1)],
)
def test_request_id_boundary_rejects_unsafe_external_values(request_id: str):
    with pytest.raises(ValueError, match="x-request-id"):
        resolve_request_id(request_id)


@pytest.mark.parametrize("mode", ["normal", "error", "stream"])
@pytest.mark.parametrize(
    "request_id",
    ["req\x1b[2J", "r" * (MAX_REQUEST_ID_BYTES + 1)],
)
def test_invalid_request_id_is_rejected_before_gateway_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    request_id: str,
) -> None:
    gateway_logger = MagicMock()
    stats = MagicMock()
    monkeypatch.setattr(app_module, "logger", gateway_logger)
    monkeypatch.setattr(app_module, "record_request_stat", stats)

    request = MagicMock()
    request.headers = {"x-request-id": request_id}
    request.json.return_value = {
        "model": "gpt-test",
        "messages": [],
        "stream": mode == "stream",
    }
    if mode == "error":
        request.json.side_effect = ValueError("body must remain unread")
    request.app.gateway_config = MagicMock()
    request.app.persistence = MagicMock()
    request.app.stream_trace_state = MagicMock()
    request.app.metadata_store = MagicMock()
    request.app.codex_tool_store = MagicMock()
    request.app.window_tool_search_store = MagicMock()

    response = asyncio.run(app_module._proxy_handler(request, "openai_responses"))

    assert response.status_code == 400
    assert not isinstance(response, StreamingResponse)
    assert request_id not in response.headers.values()
    uuid.UUID(response.headers["x-request-id"])
    request.json.assert_not_called()
    request.app.gateway_config.resolve.assert_not_called()
    assert request.app.persistence.mock_calls == []
    assert request.app.stream_trace_state.mock_calls == []
    assert request.app.metadata_store.mock_calls == []
    assert request.app.codex_tool_store.mock_calls == []
    assert request.app.window_tool_search_store.mock_calls == []
    assert gateway_logger.mock_calls == []
    stats.assert_not_called()


def test_request_log_client_ip_ignores_untrusted_forwarded_headers():
    request = MagicMock()
    request.headers = {
        "x-forwarded-for": "203.0.113.99, 192.0.2.10",
        "x-real-ip": "203.0.113.100",
    }
    request.client_addr = ("198.51.100.10", 12345)

    assert app_module._extract_client_ip(request) == "198.51.100.10"


def test_admin_cors_preflight_and_actual_request_require_correct_boundaries(tmp_path):
    config_data = _gateway_config(admin_cors_origins=["https://admin.example"])
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config_data), encoding="utf-8")
    app = app_module.create_app(
        GatewayConfig(config_data), config_path=str(config_path)
    )
    try:
        allowed = asyncio.run(
            app._dispatch(
                _app_request(
                    app,
                    method="OPTIONS",
                    path="/admin/api/config",
                    headers={"origin": "https://admin.example"},
                )
            )
        )
        assert allowed.status_code == 204
        assert allowed.headers["Access-Control-Allow-Origin"] == (
            "https://admin.example"
        )

        denied = asyncio.run(
            app._dispatch(
                _app_request(
                    app,
                    method="OPTIONS",
                    path="/admin/api/config",
                    headers={"origin": "https://attacker.example"},
                )
            )
        )
        assert denied.status_code == 403
        assert "Access-Control-Allow-Origin" not in denied.headers

        substring = asyncio.run(
            app._dispatch(
                _app_request(
                    app,
                    method="OPTIONS",
                    path="/admin/api/config",
                    headers={"origin": "https://admin"},
                )
            )
        )
        assert substring.status_code == 403
        assert "Access-Control-Allow-Origin" not in substring.headers

        unauthenticated = asyncio.run(
            app._dispatch(
                _app_request(
                    app,
                    method="GET",
                    path="/admin/api/config",
                    headers={"origin": "https://admin.example"},
                )
            )
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["Access-Control-Allow-Origin"] == (
            "https://admin.example"
        )
        assert unauthenticated.headers["Vary"] == "Origin"

        denied_actual = asyncio.run(
            app._dispatch(
                _app_request(
                    app,
                    method="GET",
                    path="/admin/api/config",
                    headers={"origin": "https://attacker.example"},
                )
            )
        )
        assert denied_actual.status_code == 401
        assert "Access-Control-Allow-Origin" not in denied_actual.headers

        authenticated = asyncio.run(
            app._dispatch(
                _app_request(
                    app,
                    method="GET",
                    path="/admin/api/config",
                    headers={
                        "origin": "https://admin.example",
                        "x-admin-token": getattr(app, "auth_state").admin_token,
                    },
                )
            )
        )
        assert authenticated.status_code == 200
        assert authenticated.headers["Access-Control-Allow-Origin"] == (
            "https://admin.example"
        )
    finally:
        persistence = getattr(app, "persistence", None)
        if persistence is not None:
            persistence.close()


@pytest.mark.parametrize("path", ["/v1/responses", "/v1/embeddings", "/v1/models"])
def test_protected_v1_preflight_reaches_public_cors_route(path: str):
    app = app_module.create_app(GatewayConfig(_gateway_config()))

    response = asyncio.run(
        app._dispatch(
            _app_request(
                app,
                method="OPTIONS",
                path=path,
                headers={
                    "origin": "https://browser.example",
                    "access-control-request-method": "POST",
                    "access-control-request-headers": "authorization",
                },
            )
        )
    )

    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Access-Control-Allow-Headers"] == "*"


def test_protected_v1_auth_failure_remains_browser_readable():
    app = app_module.create_app(GatewayConfig(_gateway_config()))

    response = asyncio.run(
        app._dispatch(
            _app_request(
                app,
                method="POST",
                path="/v1/responses",
                headers={
                    "origin": "https://browser.example",
                    "authorization": "Bearer wrong-key",
                },
            )
        )
    )

    assert response.status_code == 401
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_dynamically_registered_v1_route_fails_closed():
    app = app_module.create_app(GatewayConfig(_gateway_config()))

    @app.post("/v1/dynamic")
    async def dynamic_route(request: Any) -> JSONResponse:
        return JSONResponse({"reached": True})

    unauthenticated = asyncio.run(
        app._dispatch(_app_request(app, method="POST", path="/v1/dynamic", headers={}))
    )
    authenticated = asyncio.run(
        app._dispatch(
            _app_request(
                app,
                method="POST",
                path="/v1/dynamic",
                headers={"authorization": "Bearer test-gateway-key"},
            )
        )
    )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200


def test_unknown_v1_path_requires_key_then_reaches_router_error():
    app = app_module.create_app(GatewayConfig(_gateway_config()))

    unauthenticated = asyncio.run(
        app._dispatch(_app_request(app, method="GET", path="/v1/unknown", headers={}))
    )
    authenticated = asyncio.run(
        app._dispatch(
            _app_request(
                app,
                method="GET",
                path="/v1/unknown",
                headers={"authorization": "Bearer test-gateway-key"},
            )
        )
    )

    assert unauthenticated.status_code == 401
    # The wildcard OPTIONS route makes the current router report 405 for an
    # unknown non-OPTIONS method. Authentication must run before that result.
    assert authenticated.status_code == 405


def test_unknown_v1_preflight_remains_public():
    app = app_module.create_app(GatewayConfig(_gateway_config()))

    response = asyncio.run(
        app._dispatch(
            _app_request(
                app,
                method="OPTIONS",
                path="/v1/unknown",
                headers={"origin": "https://browser.example"},
            )
        )
    )

    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_admin_cors_preflight_uses_hot_reloaded_allowlist(tmp_path):
    config_path = tmp_path / "config.jsonc"
    initial_data = _gateway_config(admin_cors_origins=["https://old-admin.example"])
    config_path.write_text(json.dumps(initial_data), encoding="utf-8")
    app = app_module.create_app(
        GatewayConfig(initial_data), config_path=str(config_path)
    )
    try:
        updated_data = _gateway_config(admin_cors_origins=["https://new-admin.example"])
        config_path.write_text(json.dumps(updated_data), encoding="utf-8")

        reload_response = asyncio.run(reload_config(MagicMock(app=app)))
        assert reload_response.status_code == 200

        old_origin = asyncio.run(
            app._dispatch(
                _app_request(
                    app,
                    method="OPTIONS",
                    path="/admin/api/config",
                    headers={"origin": "https://old-admin.example"},
                )
            )
        )
        new_origin = asyncio.run(
            app._dispatch(
                _app_request(
                    app,
                    method="OPTIONS",
                    path="/admin/api/config",
                    headers={"origin": "https://new-admin.example"},
                )
            )
        )

        assert old_origin.status_code == 403
        assert new_origin.status_code == 204
        assert new_origin.headers["Access-Control-Allow-Origin"] == (
            "https://new-admin.example"
        )
    finally:
        persistence = getattr(app, "persistence", None)
        if persistence is not None:
            persistence.close()


def test_proxy_handler_forwards_user_agent_to_non_streaming_proxy(monkeypatch):
    """The main proxy handler should pass client User-Agent to the upstream call."""
    captured_headers: dict[str, str] = {}

    class _Config:
        models = {"gpt-test": "test-provider"}

        def resolve(self, source_provider: ProviderType, model: str):
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
    request.app.gateway_config = _Config()

    response = asyncio.run(app_module._proxy_handler(request, "openai_chat"))

    assert response.status_code == 200
    assert captured_headers["User-Agent"] == "codex-cli/1.2.3"
    assert "x-request-id" in captured_headers


def test_proxy_stats_use_original_upstream_model_name(monkeypatch):
    """Stats must not expose the public model alias as the counter key."""
    recorded_models: list[str] = []

    class _Config:
        models = {"public-alias": "test-provider"}

        def resolve(self, source_provider: ProviderType, model: str):
            return (
                ResolvedRoute(
                    source_provider=source_provider,
                    target_provider="openai_chat",
                    provider_name="test-provider",
                    upstream_model="provider-original-model",
                ),
                MagicMock(),
            )

    async def _fake_handle_non_streaming(*args: Any, **kwargs: Any):
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_handle_non_streaming)
    monkeypatch.setattr(app_module, "record_request_stat", recorded_models.append)

    request = MagicMock()
    request.headers = {}
    request.json.return_value = {"model": "public-alias", "messages": []}
    request.app.metadata_store = MagicMock()
    request.app.metrics = None
    request.app.request_log = None
    request.app.persistence = None
    request.app.profiler_state = None
    request.app.gateway_config = _Config()

    response = asyncio.run(app_module._proxy_handler(request, "openai_chat"))

    assert response.status_code == 200
    assert recorded_models == ["provider-original-model"]


def test_proxy_success_survives_request_log_persistence_failure(monkeypatch):
    """Observability storage is best-effort and cannot replace a proxy response."""

    class _Config:
        models = {"gpt-test": "test-provider"}

        def resolve(self, source_provider: ProviderType, model: str):
            return (
                ResolvedRoute(
                    source_provider=source_provider,
                    target_provider="openai_chat",
                    provider_name="test-provider",
                ),
                MagicMock(),
            )

    async def _fake_handle_non_streaming(*args: Any, **kwargs: Any):
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_handle_non_streaming)

    request = MagicMock()
    request.headers = {}
    request.json.return_value = {"model": "gpt-test", "messages": []}
    request.app.metadata_store = MagicMock()
    request.app.codex_tool_store = MagicMock()
    request.app.window_tool_search_store = MagicMock()
    request.app.transport = MagicMock()
    request.app.metrics = None
    request.app.request_log.add.side_effect = RuntimeError("sqlite unavailable")
    request.app.persistence = None
    request.app.profiler_state = None
    request.app.gateway_config = _Config()

    response = asyncio.run(app_module._proxy_handler(request, "openai_chat"))

    assert response.status_code == 200
    assert isinstance(response, JSONResponse)
    assert json.loads(response.body) == {"ok": True}


def test_proxy_handler_passes_codex_window_id_to_streaming_proxy(monkeypatch):
    """Codex window id scopes stream-only final-answer phase decisions."""
    captured_kwargs: dict[str, Any] = {}

    class _Config:
        models = {"glm-5.2": "test-provider"}
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

    async def _fake_handle_streaming(*args: Any, **kwargs: Any):
        captured_kwargs.update(kwargs)

        async def _empty_stream():
            if False:
                yield ""

        return StreamingResponse(_empty_stream(), content_type="text/event-stream"), {}

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
    request.app.gateway_config = _Config()

    response = asyncio.run(app_module._proxy_handler(request, "openai_responses"))

    assert response.status_code == 200
    assert captured_kwargs["codex_window_id"] == "thread-abc:0"
    scope = captured_kwargs["state_scope"]
    assert scope.principal_id == "test-client"
    assert scope.provider_name == "test-provider"
    assert scope.model == "glm-5.2"
    assert scope.conversation_id == "thread-abc:0"
    assert scope.persistent is True
    assert "x-codex-window-id" not in captured_kwargs["extra_headers"]


def test_proxy_handler_passes_codex_window_id_to_non_streaming_proxy(monkeypatch):
    """Codex window id is available to non-streaming request conversion."""
    captured_kwargs: dict[str, Any] = {}

    class _Config:
        models = {"glm-5.2": "test-provider"}

        def resolve(self, source_provider: ProviderType, model: str):
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
    request.app.gateway_config = _Config()

    response = asyncio.run(app_module._proxy_handler(request, "openai_responses"))

    assert response.status_code == 200
    assert captured_kwargs["codex_window_id"] == "thread-abc:0"
    scope = captured_kwargs["state_scope"]
    assert scope.principal_id == "test-client"
    assert scope.provider_name == "test-provider"
    assert scope.model == "glm-5.2"
    assert scope.conversation_id == "thread-abc:0"
    assert scope.persistent is True
    assert "x-codex-window-id" not in captured_kwargs["extra_headers"]


@pytest.mark.parametrize(
    "window_id",
    [
        "x" * 129,
        "é" * 65,
    ],
)
def test_proxy_handler_rejects_oversized_window_id_before_state(
    monkeypatch, window_id: str
):
    called = False

    class _Config:
        models = {"glm-5.2": "test-provider"}

        def resolve(self, source_provider: ProviderType, model: str):
            raise AssertionError("oversized window id reached routing")

    async def _fake_handle_non_streaming(*args: Any, **kwargs: Any):
        nonlocal called
        called = True
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_handle_non_streaming)
    request = MagicMock()
    request.headers = {"x-codex-window-id": window_id}
    request.json.return_value = {"model": "glm-5.2", "input": []}
    request.app.gateway_config = _Config()

    response = asyncio.run(app_module._proxy_handler(request, "openai_responses"))

    assert response.status_code == 400
    assert not isinstance(response, StreamingResponse)
    assert json.loads(response.body)["error"]["message"] == (
        "'x-codex-window-id' must be at most 128 UTF-8 bytes"
    )
    assert called is False


def test_proxy_handler_accepts_exact_window_id_byte_limit(monkeypatch):
    captured_kwargs: dict[str, Any] = {}

    class _Config:
        models = {"glm-5.2": "test-provider"}

        def resolve(self, source_provider: ProviderType, model: str):
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

    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_handle_non_streaming)
    window_id = "é" * 64
    request = MagicMock()
    request.headers = {"x-codex-window-id": window_id}
    request.json.return_value = {"model": "glm-5.2", "input": []}
    request.app.metadata_store = MagicMock()
    request.app.codex_tool_store = MagicMock()
    request.app.window_tool_search_store = MagicMock()
    request.app.metrics = None
    request.app.request_log = None
    request.app.persistence = None
    request.app.profiler_state = None
    request.app.transport = MagicMock()
    request.app.gateway_config = _Config()

    response = asyncio.run(app_module._proxy_handler(request, "openai_responses"))

    assert response.status_code == 200
    assert captured_kwargs["codex_window_id"] == window_id
    assert captured_kwargs["state_scope"].conversation_id == window_id
