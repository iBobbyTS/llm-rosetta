"""Tests for the Gateway client of the optional web-run sidecar."""

from __future__ import annotations

import asyncio

import pytest

import codex_rosetta.gateway.web_run_sidecar as sidecar_module
from codex_rosetta.gateway.transport.http.transport import BoundedHttpResponse
from codex_rosetta.gateway.web_run_sidecar import (
    WebRunSidecarError,
    WebRunSidecarHTTPClient,
    WebRunSidecarInvalidRequest,
)
from codex_rosetta.gateway.web_search import WebSearchSettings


def test_sidecar_client_sends_scoped_bearer_authenticated_operation(
    monkeypatch,
) -> None:
    captured = {}

    async def fake_request(client, method, url, **kwargs):
        del client
        captured.update(method=method, url=url, **kwargs)
        return BoundedHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=b'{"output":"Opened page"}',
        )

    monkeypatch.setattr(sidecar_module, "request_bounded_response", fake_request)
    client = WebRunSidecarHTTPClient("http://web-run:8080", "sidecar-secret", timeout=9)

    output = asyncio.run(
        client.execute(
            session_id="a" * 64,
            operation="open",
            arguments={"ref_id": "https://example.com"},
        )
    )

    assert output == "Opened page"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://web-run:8080/v1/execute"
    assert captured["headers"]["Authorization"] == "Bearer sidecar-secret"
    assert captured["json"] == {
        "session_id": "a" * 64,
        "operation": "open",
        "arguments": {"ref_id": "https://example.com"},
    }
    assert captured["max_success_bytes"] == 1_000_000


def test_sidecar_client_maps_client_errors(monkeypatch) -> None:
    async def fake_request(client, method, url, **kwargs):
        del client, method, url, kwargs
        return BoundedHttpResponse(
            status_code=404,
            headers={"content-type": "application/json"},
            content=b'{"detail":"Unknown page reference"}',
        )

    monkeypatch.setattr(sidecar_module, "request_bounded_response", fake_request)

    with pytest.raises(WebRunSidecarInvalidRequest, match="Unknown page reference"):
        asyncio.run(
            WebRunSidecarHTTPClient("http://web-run:8080", "secret").execute(
                session_id="a" * 64,
                operation="click",
                arguments={"ref_id": "turn0fetch0", "id": 1},
            )
        )


@pytest.mark.parametrize(
    "provider",
    ["self_hosted_google", "self_hosted_bing", "self_hosted_bing_browser"],
)
def test_sidecar_client_sends_bounded_self_hosted_search(
    monkeypatch, provider: str
) -> None:
    captured = {}

    async def fake_request(client, method, url, **kwargs):
        del client
        captured.update(method=method, url=url, **kwargs)
        return BoundedHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=(
                b'{"results":[{"title":"Python","url":"https://python.org",'
                b'"content":"Welcome"}]}'
            ),
        )

    monkeypatch.setattr(sidecar_module, "request_bounded_response", fake_request)
    client = WebRunSidecarHTTPClient(
        "http://web-run:8080", "sidecar-secret", search_provider=provider
    )

    result = asyncio.run(
        client.search(
            "official Python",
            settings=WebSearchSettings(
                max_results=8,
                search_depth="advanced",
                include_domains=("python.org",),
            ),
        )
    )

    assert result["results"][0]["title"] == "Python"
    assert captured["url"] == "http://web-run:8080/v1/search"
    assert captured["json"] == {
        "provider": provider,
        "query": "official Python",
        "max_results": 8,
        "include_domains": ["python.org"],
    }
    assert captured["max_success_bytes"] == 1_000_000


@pytest.mark.parametrize("status_code", [200, 400, 500])
def test_sidecar_execute_redacts_credential_from_success_and_http_errors(
    monkeypatch,
    status_code: int,
) -> None:
    token = "sidecar-reflected-secret"
    if status_code == 200:
        content = f'{{"output":"before {token} after"}}'.encode()
    elif status_code == 400:
        content = f'{{"detail":{{"message":"bad {token}"}}}}'.encode()
    else:
        content = f'{{"error":"failed {token}"}}'.encode()

    async def fake_request(*args, **kwargs):
        del args, kwargs
        return BoundedHttpResponse(status_code, {}, content)

    monkeypatch.setattr(sidecar_module, "request_bounded_response", fake_request)
    client = WebRunSidecarHTTPClient("http://web-run:8080", token)

    if status_code == 200:
        output = asyncio.run(
            client.execute(session_id="a" * 64, operation="open", arguments={})
        )
        assert output == "before [REDACTED] after"
    else:
        error_type = (
            WebRunSidecarInvalidRequest if status_code == 400 else WebRunSidecarError
        )
        with pytest.raises(error_type) as caught:
            asyncio.run(
                client.execute(session_id="a" * 64, operation="open", arguments={})
            )
        assert token not in str(caught.value)


def test_sidecar_search_redacts_nested_success_payload(monkeypatch) -> None:
    token = "sidecar-search-secret"

    async def fake_request(*args, **kwargs):
        del args, kwargs
        return BoundedHttpResponse(
            200,
            {},
            (
                f'{{"results":[{{"title":"stable","content":"before {token} after"}}]}}'
            ).encode(),
        )

    monkeypatch.setattr(sidecar_module, "request_bounded_response", fake_request)
    result = asyncio.run(
        WebRunSidecarHTTPClient("http://web-run:8080", token).search(
            "query",
            settings=WebSearchSettings(
                max_results=3,
                search_depth="basic",
                include_domains=(),
            ),
        )
    )

    assert result["results"][0]["content"] == "before [REDACTED] after"


@pytest.mark.parametrize("method", ["execute", "search"])
def test_sidecar_transport_exception_is_redacted_and_cause_free(
    monkeypatch,
    method: str,
) -> None:
    token = "sidecar-transport-secret"

    async def fake_request(*args, **kwargs):
        del args, kwargs
        try:
            raise ValueError(f"cause contains {token}")
        except ValueError as cause:
            raise RuntimeError(f"transport failed with {token}") from cause

    monkeypatch.setattr(sidecar_module, "request_bounded_response", fake_request)
    client = WebRunSidecarHTTPClient("http://web-run:8080", token)

    with pytest.raises(WebRunSidecarError) as caught:
        if method == "execute":
            asyncio.run(
                client.execute(session_id="a" * 64, operation="open", arguments={})
            )
        else:
            asyncio.run(
                client.search(
                    "query",
                    settings=WebSearchSettings(
                        max_results=3,
                        search_depth="basic",
                        include_domains=(),
                    ),
                )
            )

    assert token not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_sidecar_invalid_payload_diagnostic_drops_sensitive_json_cause(
    monkeypatch,
) -> None:
    token = "sidecar-invalid-json-secret"

    async def fake_request(*args, **kwargs):
        del args, kwargs
        return BoundedHttpResponse(200, {}, f"not-json {token}".encode())

    monkeypatch.setattr(sidecar_module, "request_bounded_response", fake_request)

    with pytest.raises(WebRunSidecarError) as caught:
        asyncio.run(
            WebRunSidecarHTTPClient("http://web-run:8080", token).execute(
                session_id="a" * 64,
                operation="open",
                arguments={},
            )
        )

    assert str(caught.value) == "web-run sidecar returned invalid JSON"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
