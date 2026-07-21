"""Resource-lifecycle tests for Admin upstream model discovery."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from codex_rosetta._vendor.httpclient import (
    CaseInsensitiveDict,
    Response as HttpResponse,
)
from codex_rosetta.gateway.admin.routes import config as config_routes
from codex_rosetta.gateway.config import GatewayConfig


def _request(
    *,
    allow_redirects: bool = False,
    api_key: str = "sk-test",
) -> SimpleNamespace:
    config = GatewayConfig(
        {
            "providers": {
                "test-provider": {
                    "api_key": api_key,
                    "base_url": "https://api.example.test/v1",
                    "api_type": "chat",
                    "allow_redirects": allow_redirects,
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
            },
        }
    )
    return SimpleNamespace(
        app=SimpleNamespace(gateway_config=config),
        path_params={"name": "test-provider"},
    )


def _response(content: bytes) -> HttpResponse:
    return HttpResponse(
        200,
        CaseInsensitiveDict({"content-type": "application/json"}),
        content,
        "https://api.example.test/v1/models",
    )


@pytest.mark.parametrize(
    ("outcome", "expected_error"),
    [
        ("success", None),
        ("connection_error", "boom"),
        ("parse_error", "non-JSON"),
        ("cancelled", None),
    ],
)
def test_model_discovery_closes_client_on_every_exit_path(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    expected_error: str | None,
):
    instances: list[Any] = []

    class _FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            self.enter_count = 0
            self.exit_count = 0
            instances.append(self)

        async def __aenter__(self):
            self.enter_count += 1
            return self

        async def __aexit__(self, *args: Any) -> None:
            self.exit_count += 1

        async def get(self, url: str, **kwargs: Any) -> HttpResponse:
            if outcome == "connection_error":
                raise RuntimeError("boom")
            if outcome == "cancelled":
                raise asyncio.CancelledError
            if outcome == "parse_error":
                return _response(b"not json")
            return _response(b'{"data":[{"id":"gpt-upstream"}]}')

    monkeypatch.setattr(config_routes, "AsyncClient", _FakeAsyncClient)

    async def _fake_bounded_request(client, method, url, **kwargs):
        assert method == "GET"
        assert kwargs.pop("allow_redirects") is False
        return await client.get(url, **kwargs)

    monkeypatch.setattr(
        config_routes,
        "request_bounded_response",
        _fake_bounded_request,
    )

    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(config_routes.fetch_upstream_models(_request()))
    else:
        response = asyncio.run(config_routes.fetch_upstream_models(_request()))
        body = json.loads(response.body)
        if expected_error is None:
            assert body["models"] == ["gpt-upstream"]
        else:
            assert expected_error in body["error"]

    assert len(instances) == 1
    assert instances[0].enter_count == 1
    assert instances[0].exit_count == 1


def test_model_discovery_uses_provider_redirect_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class _FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    async def _fake_bounded_request(client, method, url, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {"data": []})

    monkeypatch.setattr(config_routes, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        config_routes,
        "request_bounded_response",
        _fake_bounded_request,
    )

    response = asyncio.run(
        config_routes.fetch_upstream_models(_request(allow_redirects=True))
    )

    assert response.status_code == 200
    assert observed["allow_redirects"] is True


@pytest.mark.parametrize("outcome", ["success", "connection_error"])
def test_model_discovery_redacts_rotated_wire_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: str,
) -> None:
    first_key = "admin-model-first-secret"
    wire_key = "admin-model-wire-secret"
    request = _request(api_key=f" {first_key}, , {wire_key} ")
    pinfo = request.app.gateway_config.providers["test-provider"]
    assert pinfo.auth_headers()["Authorization"] == f"Bearer {first_key}"
    observed_headers: dict[str, str] = {}

    class _FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    async def _fake_bounded_request(client, method, url, **kwargs):
        observed_headers.update(kwargs["headers"])
        if outcome == "connection_error":
            raise RuntimeError(f"connection rejected credential={wire_key}")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"data": [{"id": f"model-{wire_key}"}]},
        )

    monkeypatch.setattr(config_routes, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        config_routes,
        "request_bounded_response",
        _fake_bounded_request,
    )

    response = asyncio.run(config_routes.fetch_upstream_models(request))

    assert observed_headers["Authorization"] == f"Bearer {wire_key}"
    response_text = response.body.decode("utf-8")
    assert first_key not in response_text
    assert wire_key not in response_text
    if outcome == "success":
        assert json.loads(response.body)["models"] == ["model-[REDACTED]"]
    else:
        assert "connection rejected credential=[REDACTED]" in response_text
        assert wire_key not in caplog.text
        assert "connection rejected credential=[REDACTED]" in caplog.text
