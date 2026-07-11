"""Tests for the gateway's agent-facing downstream API surface."""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from codex_rosetta._vendor.httpserver import Request, StreamingResponse
from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.gateway.app import _proxy_handler, create_app, handle_google_genai
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.headers import MAX_REQUEST_ID_BYTES
from codex_rosetta.gateway.proxy import MAX_MODEL_ID_BYTES


def _make_app():
    config = GatewayConfig(
        {
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
            },
        }
    )
    return create_app(config)


def _request(
    app,
    path: str,
    *,
    method: str = "POST",
    body: bytes = b"not json",
    headers: dict[str, str] | None = None,
) -> Request:
    return Request(
        method=method,
        path=path,
        query_string="",
        headers={
            "authorization": "Bearer test-gateway-key",
            **(headers or {}),
        },
        body=body,
        client_addr=("127.0.0.1", 12345),
        app=app,
    )


def test_responses_endpoint_remains_agent_facing_generation_route():
    app = _make_app()

    response = asyncio.run(app._dispatch(_request(app, "/v1/responses")))

    assert response.status_code == 400
    assert b"Invalid JSON body" in response.body


@pytest.mark.parametrize(
    "path",
    [
        "/v1/alpha/search",
        "/v1/images/generations",
        "/v1/images/edits",
    ],
)
def test_codex_auxiliary_endpoints_are_post_only(path: str):
    app = _make_app()

    response = asyncio.run(app._dispatch(_request(app, path, method="GET")))

    assert response.status_code == 405


@pytest.mark.parametrize(
    "path",
    [
        "/v1/responses",
        "/v1/embeddings",
        "/v1/alpha/search",
        "/v1/images/generations",
        "/v1/images/edits",
    ],
)
@pytest.mark.parametrize("value", [[], None, "text", 1, True, 1.25])
def test_public_post_endpoints_reject_non_object_json(path: str, value: object):
    app = _make_app()

    response = asyncio.run(
        app._dispatch(_request(app, path, body=json.dumps(value).encode()))
    )

    assert response.status_code == 400
    assert b"JSON body must be an object" in response.body


@pytest.mark.parametrize(
    "path",
    [
        "/v1/responses",
        "/v1/embeddings",
        "/v1/alpha/search",
        "/v1/images/generations",
        "/v1/images/edits",
    ],
)
@pytest.mark.parametrize("model", [[], {}, 42, 0, True, False, "", " \t\n"])
def test_public_post_endpoints_reject_invalid_model_types(path: str, model: object):
    app = _make_app()

    response = asyncio.run(
        app._dispatch(
            _request(
                app,
                path,
                body=json.dumps({"model": model, "input": []}).encode(),
            )
        )
    )

    assert response.status_code == 400
    payload = json.loads(response.body)
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["message"] == "'model' must be a non-empty string"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/responses",
        "/v1/embeddings",
        "/v1/alpha/search",
        "/v1/images/generations",
        "/v1/images/edits",
    ],
)
def test_public_post_endpoints_keep_missing_model_error(path: str):
    app = _make_app()

    response = asyncio.run(
        app._dispatch(_request(app, path, body=json.dumps({"input": []}).encode()))
    )

    assert response.status_code == 400
    payload = json.loads(response.body)
    assert payload["error"]["message"] == "Missing 'model' in request body"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/responses",
        "/v1/embeddings",
        "/v1/alpha/search",
        "/v1/images/generations",
        "/v1/images/edits",
    ],
)
@pytest.mark.parametrize(
    ("model", "expected_status"),
    [
        ("x" * MAX_MODEL_ID_BYTES, 404),
        ("é" * (MAX_MODEL_ID_BYTES // 2), 404),
        ("x" * (MAX_MODEL_ID_BYTES + 1), 400),
        ("é" * (MAX_MODEL_ID_BYTES // 2 + 1), 400),
    ],
)
def test_public_post_endpoints_bound_model_utf8_bytes(
    path: str, model: str, expected_status: int
):
    app = _make_app()

    response = asyncio.run(
        app._dispatch(
            _request(
                app,
                path,
                body=json.dumps({"model": model, "input": []}).encode(),
            )
        )
    )

    assert response.status_code == expected_status
    if expected_status == 400:
        payload = json.loads(response.body)
        assert payload["error"]["message"] == (
            f"'model' must be at most {MAX_MODEL_ID_BYTES} UTF-8 bytes"
        )


@pytest.mark.parametrize(
    "source_provider",
    ["openai_chat", "openai_responses", "anthropic", "google"],
)
def test_all_proxy_source_formats_share_model_byte_limit(
    source_provider: ProviderType,
):
    app = _make_app()
    request = _request(
        app,
        "/unused",
        body=json.dumps(
            {"model": "x" * (MAX_MODEL_ID_BYTES + 1), "input": []}
        ).encode(),
    )

    response = asyncio.run(_proxy_handler(request, source_provider))

    assert response.status_code == 400
    assert not isinstance(response, StreamingResponse)
    payload = json.loads(response.body)
    error = payload["error"]
    assert error["message"] == (
        f"'model' must be at most {MAX_MODEL_ID_BYTES} UTF-8 bytes"
    )


@pytest.mark.parametrize(
    "source_provider",
    ["openai_chat", "openai_responses", "open_responses", "anthropic", "google"],
)
@pytest.mark.parametrize(
    ("request_id", "expected_message"),
    [
        ("", "'x-request-id' must be a non-empty visible ASCII string"),
        (" ", "'x-request-id' must be a non-empty visible ASCII string"),
        ("req\x1b[2J", "'x-request-id' must be a non-empty visible ASCII string"),
        ("请求", "'x-request-id' must be a non-empty visible ASCII string"),
        (
            "r" * (MAX_REQUEST_ID_BYTES + 1),
            f"'x-request-id' must be at most {MAX_REQUEST_ID_BYTES} ASCII bytes",
        ),
    ],
)
def test_all_proxy_source_formats_reject_invalid_request_id_at_ingress(
    source_provider: ProviderType,
    request_id: str,
    expected_message: str,
) -> None:
    app = _make_app()
    request = _request(
        app,
        "/unused",
        body=json.dumps({"model": "gpt-test", "input": []}).encode(),
        headers={"x-request-id": request_id},
    )

    response = asyncio.run(_proxy_handler(request, source_provider))

    assert response.status_code == 400
    assert not isinstance(response, StreamingResponse)
    payload = json.loads(response.body)
    assert payload["error"]["message"] == expected_message
    assert request_id not in response.headers.values()
    uuid.UUID(response.headers["x-request-id"])


@pytest.mark.parametrize(
    "source_provider",
    ["openai_chat", "openai_responses", "open_responses", "anthropic", "google"],
)
def test_all_proxy_source_formats_accept_exact_request_id_limit(
    source_provider: ProviderType,
) -> None:
    app = _make_app()
    request_id = "r" * MAX_REQUEST_ID_BYTES
    request = _request(
        app,
        "/unused",
        body=json.dumps({"model": "unknown-model", "input": []}).encode(),
        headers={"x-request-id": request_id},
    )

    response = asyncio.run(_proxy_handler(request, source_provider))

    assert response.status_code == 404
    assert response.headers["x-request-id"] == request_id


@pytest.mark.parametrize(
    ("model_size", "expected_status"),
    [(MAX_MODEL_ID_BYTES, 404), (MAX_MODEL_ID_BYTES + 1, 400)],
)
def test_google_path_model_override_uses_shared_byte_limit(
    model_size: int, expected_status: int
):
    app = _make_app()
    request = _request(app, "/unused", body=b"{}")

    response = asyncio.run(
        handle_google_genai(
            request,
            model_path=f"{'x' * model_size}:generateContent",
        )
    )

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    "path",
    [
        "/v1/chat/completions",
        "/v1/messages",
        "/v1beta/models",
        "/v1beta/models/gemini:generateContent",
        "/v1beta/models/gemini:streamGenerateContent",
    ],
)
def test_removed_downstream_generation_routes_are_not_registered(path: str):
    app = _make_app()

    response = asyncio.run(app._dispatch(_request(app, path)))

    assert response.status_code in {404, 405}
