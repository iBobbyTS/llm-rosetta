"""Regression tests for app-owned gateway configuration."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import codex_rosetta.gateway.app as app_module
from codex_rosetta._vendor.httpserver import JSONResponse
from codex_rosetta.gateway.admin.routes import _shared
from codex_rosetta.gateway.admin.routes import config as config_routes
from codex_rosetta.gateway.admin.routes import testing as testing_routes
from codex_rosetta.gateway.auth import api_key_principal_var
from codex_rosetta.gateway.config import GatewayConfig


def _config(
    label: str,
    *,
    proxy: str | None = None,
    request_body_limit_mb: int | str = 128,
) -> GatewayConfig:
    return GatewayConfig(
        {
            "providers": {
                f"provider-{label}": {
                    "api_key": f"sk-{label}",
                    "base_url": f"https://{label}.example.test/v1",
                    "type": "openai",
                }
            },
            "model_groups": {
                f"group-{label}": {
                    "provider": f"provider-{label}",
                    "type": "llm",
                    "models": {f"model-{label}": {}},
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
                "proxy": proxy,
                "request_body_limit_mb": request_body_limit_mb,
            },
        }
    )


def _request(app: Any, body: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        app=app,
        headers={},
        json=lambda: body or {},
        client_addr=("127.0.0.1", 12345),
    )


def test_request_handlers_keep_their_own_config_after_second_app_creation(
    monkeypatch,
):
    config_a = _config("a")
    app_a = cast(Any, app_module.create_app(config_a))
    app_b = cast(Any, app_module.create_app(_config("b")))
    assert app_a.admin_runtime_state is not app_b.admin_runtime_state
    assert (
        app_a.admin_runtime_state.login_limiter
        is not app_b.admin_runtime_state.login_limiter
    )
    assert (
        app_a.admin_runtime_state.test_tasks is not app_b.admin_runtime_state.test_tasks
    )
    assert "sk-a" not in app_a.body_log_state.render({"value": "sk-a"})
    assert "sk-b" in app_a.body_log_state.render({"value": "sk-b"})
    assert "sk-b" not in app_b.body_log_state.render({"value": "sk-b"})
    assert "sk-a" in app_b.body_log_state.render({"value": "sk-a"})
    captured: dict[str, Any] = {}

    async def _fake_non_streaming(route: Any, provider: Any, *args: Any, **kwargs: Any):
        captured["route"] = route
        captured["provider"] = provider
        captured["body_log_state"] = kwargs["body_log_state"]
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "handle_non_streaming", _fake_non_streaming)
    token = api_key_principal_var.set("test-client")
    try:
        response = asyncio.run(
            app_module._proxy_handler(
                _request(app_a, {"model": "model-a", "input": []}),
                "openai_responses",
            )
        )
    finally:
        api_key_principal_var.reset(token)

    assert response.status_code == 200
    assert captured["route"].provider_name == "provider-a"
    assert captured["provider"].base_url == "https://a.example.test/v1"
    assert captured["provider"].auth_headers() == {"Authorization": "Bearer sk-a"}
    assert captured["body_log_state"] is app_a.body_log_state

    openai_models = asyncio.run(app_module.handle_list_models(_request(app_a)))
    assert [item["id"] for item in json.loads(openai_models.body)["data"]] == [
        "model-a"
    ]
    google_models = asyncio.run(app_module.handle_list_models_google(_request(app_a)))
    assert [item["name"] for item in json.loads(google_models.body)["models"]] == [
        "models/model-a"
    ]

    async def _fake_embeddings(request: Any, config: GatewayConfig):
        captured["embeddings_config"] = config
        return JSONResponse({"ok": True})

    monkeypatch.setattr(app_module, "_handle_embeddings", _fake_embeddings)
    asyncio.run(app_module.handle_embeddings(_request(app_a)))
    assert captured["embeddings_config"] is config_a

    async def _fake_streaming(*args: Any, **kwargs: Any):
        captured["stream_body_log_state"] = kwargs["body_log_state"]
        return JSONResponse({"ok": True}), {}

    monkeypatch.setattr(app_module, "handle_streaming", _fake_streaming)
    token = api_key_principal_var.set("test-client")
    try:
        stream_response = asyncio.run(
            app_module._proxy_handler(
                _request(
                    app_a,
                    {"model": "model-a", "input": [], "stream": True},
                ),
                "openai_responses",
            )
        )
    finally:
        api_key_principal_var.reset(token)

    assert stream_response.status_code == 200
    assert captured["stream_body_log_state"] is app_a.body_log_state
    assert app_b.gateway_config.models == {"model-b": "provider-b"}


def test_admin_config_helpers_and_activation_are_app_scoped():
    config_a = _config("a")
    config_b = _config("b", request_body_limit_mb=64)
    app_a = cast(Any, app_module.create_app(config_a))
    app_b = cast(Any, app_module.create_app(config_b))
    request_a = _request(app_a)
    runtime_a = app_a.admin_runtime_state

    assert config_routes._get_gateway_config(request_a) is config_a
    assert testing_routes._get_gateway_config(request_a) is config_a

    assert app_a.max_body_size == 128 * 1024 * 1024
    assert app_b.max_body_size == 64 * 1024 * 1024

    updated_a = _config("a-updated", request_body_limit_mb=256)
    _shared._activate_gateway_config(request_a, updated_a)

    assert app_a.gateway_config is updated_a
    assert config_routes._get_gateway_config(request_a) is updated_a
    assert testing_routes._get_gateway_config(request_a) is updated_a
    assert app_a.admin_runtime_state is runtime_a
    assert app_a.max_body_size == 256 * 1024 * 1024
    assert app_b.gateway_config is config_b
    assert app_b.max_body_size == 64 * 1024 * 1024


def test_create_and_hot_reload_do_not_mutate_process_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://parent-process:8000")
    monkeypatch.setenv("HTTPS_PROXY", "http://parent-process:8443")
    initial = _config("proxy-a", proxy="http://gateway-a:9000")
    app = cast(Any, app_module.create_app(initial))

    updated = _config("proxy-b", proxy="http://gateway-b:9001")
    _shared._activate_gateway_config(_request(app), updated)

    assert app.gateway_config.proxy == "http://gateway-b:9001"
    assert __import__("os").environ["HTTP_PROXY"] == "http://parent-process:8000"
    assert __import__("os").environ["HTTPS_PROXY"] == "http://parent-process:8443"
