"""Tests for Codex Search and Images auxiliary endpoints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codex_rosetta.gateway.auth import api_key_principal_var
from codex_rosetta.gateway.codex_auxiliary import handle_codex_auxiliary
from codex_rosetta.gateway.codex_page import OpenedPage
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.codex_search_references import CodexSearchReferenceStore
from codex_rosetta.gateway.stream_trace import StreamTraceConfig, StreamTraceState
from codex_rosetta.gateway.tool_profiles import tool_profile_contract
from codex_rosetta.gateway.transport import UpstreamConnectionError
from codex_rosetta.gateway.transport._base import UpstreamResponse
from codex_rosetta.gateway.web_search import WebSearchSettings


ENDPOINTS = ("alpha/search", "images/generations", "images/edits")


@pytest.fixture(autouse=True)
def _authenticated_principal() -> Any:
    token = api_key_principal_var.set("test-client")
    try:
        yield
    finally:
        api_key_principal_var.reset(token)


def _make_config(
    api_type: str = "responses",
    *,
    upstream_model: str | None = "gpt-image-2",
    tavily_api_key: str | None = None,
    search_provider: str = "tavily",
    tool_profile: str | None = None,
    image_state: str | None = None,
    image_base_url: str = "https://images.example/v1",
    image_token: str = "image-token",
    upstream_base_url: str = "https://upstream.example/v1",
) -> GatewayConfig:
    provider_by_api_type = {
        "responses": "openai",
        "chat": "openai",
        "anthropic": "anthropic",
        "google": "google",
    }
    model: dict[str, Any] = {}
    if upstream_model is not None:
        model["upstream_model"] = upstream_model
    tool_profiles: dict[str, Any] = {}
    explicit_web_mapping = tool_profile == "test-web-run-mapping"
    local_search = (tavily_api_key is not None or search_provider != "tavily") and (
        explicit_web_mapping or api_type != "responses"
    )
    explicit_pass_through = tool_profile == "test-pass-through" or (
        tool_profile is None and api_type == "responses"
    )
    if (
        image_state is not None
        or local_search
        or explicit_pass_through
        or explicit_web_mapping
    ):
        base_profile = tool_profile_contract()["readonly"]["builtin"]
        tools = dict(base_profile["tools"])
        inputs = {
            item_id: dict(values) for item_id, values in base_profile["inputs"].items()
        }
        if explicit_pass_through:
            tools = {
                item_id: (
                    "disabled" if item_id.startswith("injection.") else "passthrough"
                )
                for item_id in tools
            }
        if local_search or explicit_web_mapping:
            tools["namespace.web.run"] = "modified"
        if image_state is not None:
            tools["namespace.image_gen.imagegen"] = image_state
            inputs["namespace.image_gen.imagegen"] = {
                "base_url": image_base_url,
                "token": image_token,
            }
        tool_profile = "test-profile"
        tool_profiles[tool_profile] = {"tools": tools, "inputs": inputs}
    return GatewayConfig(
        {
            "providers": {
                "upstream": {
                    "provider": provider_by_api_type[api_type],
                    "api_type": api_type,
                    "api_key": "upstream-key",
                    "base_url": upstream_base_url,
                }
            },
            "tool_profiles": tool_profiles,
            "model_groups": {
                "codex": {
                    "provider": "upstream",
                    "type": "llm",
                    **(
                        {"tool_profile": tool_profile}
                        if tool_profile is not None
                        else {}
                    ),
                    "models": {"gateway-model": model},
                }
            },
            "server": {
                "admin_password": "test-admin-password",
                "api_keys": [
                    {
                        "id": "test-client",
                        "label": "Test client",
                        "key": "gateway-key",
                    }
                ],
                "web_search": {
                    "provider": search_provider,
                    "tavily_api_key": tavily_api_key or "",
                },
            },
        }
    )


def _make_request(body: Any) -> MagicMock:
    request = MagicMock()
    request.json.return_value = body
    request.headers = {"user-agent": "codex-cli/test", "x-request-id": "req-1"}
    request.app = MagicMock()
    request.app.metrics = None
    request.app.request_log = None
    request.app.codex_search_reference_store = CodexSearchReferenceStore()
    request.app.transport.send_passthrough = AsyncMock(
        return_value=UpstreamResponse(
            status_code=202,
            body={"accepted": True},
            raw_content=b'{"accepted":true}',
        )
    )
    return request


@pytest.mark.parametrize("upstream_path", ENDPOINTS)
def test_responses_direct_transport_forwards_each_endpoint(upstream_path: str) -> None:
    config = _make_config()
    body = {"model": "gateway-model", "prompt": "draw a fox"}
    request = _make_request(body)

    response = asyncio.run(handle_codex_auxiliary(request, config, upstream_path))

    assert response.status_code == 202
    assert response.body == b'{"accepted":true}'
    provider_info, url, forwarded_body = (
        request.app.transport.send_passthrough.call_args.args
    )
    assert provider_info.base_url == "https://upstream.example/v1"
    assert url == f"https://upstream.example/v1/{upstream_path}"
    assert forwarded_body == {
        "model": "gpt-image-2",
        "prompt": "draw a fox",
    }
    extra_headers = request.app.transport.send_passthrough.call_args.kwargs[
        "extra_headers"
    ]
    assert extra_headers == {
        "x-request-id": "req-1",
        "User-Agent": "codex-cli/test",
    }


@pytest.mark.parametrize("status_code", [200, 400])
def test_auxiliary_provider_return_redacts_success_and_http_error(
    status_code: int,
) -> None:
    token = "upstream-key"
    config = _make_config()
    request = _make_request({"model": "gateway-model", "query": "test"})
    payload = {"nested": {"message": f"before {token} after"}}
    request.app.transport.send_passthrough.return_value = UpstreamResponse(
        status_code=status_code,
        body=payload if status_code < 400 else None,
        raw_content=json.dumps(payload, separators=(",", ":")).encode(),
    )

    response = asyncio.run(handle_codex_auxiliary(request, config, "alpha/search"))

    assert response.status_code == status_code
    assert token.encode() not in response.body
    assert b"before [REDACTED] after" in response.body


def test_auxiliary_transport_failure_is_redacted_before_metrics() -> None:
    token = "upstream-key"
    config = _make_config()
    request = _make_request({"model": "gateway-model", "query": "test"})
    metrics = MagicMock()
    request.app.metrics = metrics
    try:
        raise ValueError(f"cause contains {token}")
    except ValueError as cause:
        request.app.transport.send_passthrough.side_effect = UpstreamConnectionError(
            f"request failed with {token}"
        )
        request.app.transport.send_passthrough.side_effect.__cause__ = cause

    response = asyncio.run(handle_codex_auxiliary(request, config, "alpha/search"))

    assert response.status_code == 502
    assert token.encode() not in response.body
    error_detail = metrics.record_request.call_args.kwargs["error_detail"]
    assert token not in error_detail


@pytest.mark.parametrize("api_type", ["chat", "anthropic", "google"])
@pytest.mark.parametrize("upstream_path", ENDPOINTS)
def test_non_passthrough_modes_return_not_implemented(
    api_type: str, upstream_path: str
) -> None:
    config = _make_config(api_type, image_state="disabled")
    request = _make_request({"model": "gateway-model", "prompt": "test"})

    response = asyncio.run(handle_codex_auxiliary(request, config, upstream_path))

    assert response.status_code == 501
    payload = json.loads(response.body)
    assert payload["error"]["type"] == "invalid_request_error"
    if upstream_path in {"images/generations", "images/edits"}:
        assert "image_gen.imagegen is disabled" in payload["error"]["message"]
    else:
        assert (
            "only implemented for OpenAI Responses providers"
            in payload["error"]["message"]
        )
    assert payload["error"]["message"].endswith('Consider "Browser Use" skill')
    request.app.transport.send_passthrough.assert_not_awaited()


@pytest.mark.parametrize("invalid_body", [[], "text", 1, True])
def test_auxiliary_endpoint_rejects_non_object_json(invalid_body: Any) -> None:
    request = _make_request(invalid_body)

    response = asyncio.run(
        handle_codex_auxiliary(request, _make_config(), "alpha/search")
    )

    assert response.status_code == 400
    assert json.loads(response.body)["error"]["message"] == (
        "JSON body must be an object"
    )
    request.app.transport.send_passthrough.assert_not_awaited()


def test_auxiliary_endpoint_returns_model_not_found() -> None:
    request = _make_request({"model": "missing"})

    response = asyncio.run(
        handle_codex_auxiliary(request, _make_config(), "alpha/search")
    )

    assert response.status_code == 404
    assert json.loads(response.body)["error"]["type"] == "model_not_found"
    request.app.transport.send_passthrough.assert_not_awaited()


def test_auxiliary_endpoint_maps_upstream_connection_error() -> None:
    request = _make_request({"model": "gateway-model", "q": "latest news"})
    request.app.transport.send_passthrough.side_effect = UpstreamConnectionError(
        "connection refused"
    )

    response = asyncio.run(
        handle_codex_auxiliary(request, _make_config(), "alpha/search")
    )

    assert response.status_code == 502
    payload = json.loads(response.body)
    assert payload["error"]["message"] == (
        "Upstream request failed: connection refused"
    )


class _FakeTavilyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, WebSearchSettings]] = []

    async def search(
        self, query: str, *, settings: WebSearchSettings
    ) -> dict[str, Any]:
        self.calls.append((query, settings))
        return {
            "results": [
                {
                    "title": "Python documentation",
                    "url": "https://docs.python.org/3/",
                    "content": "Official Python documentation.",
                }
            ]
        }


class _FakePageClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def open(self, url: str) -> OpenedPage:
        self.calls.append(url)
        return OpenedPage(
            url=url,
            title="Python 3 Documentation",
            lines=("Python 3 documentation", "Tutorial", "Library Reference"),
        )


def _search_body(commands: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "search-session",
        "model": "gateway-model",
        "commands": commands,
        "settings": {
            "allowed_callers": ["direct"],
            "external_web_access": True,
        },
    }


def test_web_run_mapping_profile_intercepts_tool_mapping_only_search() -> None:
    config = _make_config(
        tavily_api_key="tvly-test",
        upstream_model="real-model",
        tool_profile="test-web-run-mapping",
    )
    request = _make_request(
        _search_body({"search_query": [{"q": "Python documentation"}]})
    )
    client = _FakeTavilyClient()

    response = asyncio.run(
        handle_codex_auxiliary(
            request,
            config,
            "alpha/search",
            search_client=client,
        )
    )

    assert response.status_code == 200
    assert "https://docs.python.org/3/" in json.loads(response.body)["output"]
    assert client.calls == [("Python documentation", WebSearchSettings())]
    request.app.transport.send_passthrough.assert_not_awaited()


class _FakeSelfHostedGoogleClient:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, WebSearchSettings]] = []

    async def search(
        self,
        query: str,
        *,
        settings: WebSearchSettings,
    ) -> dict[str, Any]:
        self.search_calls.append((query, settings))
        return {
            "results": [
                {
                    "title": "Python documentation",
                    "url": "https://docs.python.org/3/",
                    "content": "Python documentation from Google Search.",
                }
            ]
        }

    async def execute(self, **kwargs: Any) -> str:
        raise AssertionError(f"unexpected browser operation: {kwargs}")


def test_self_hosted_google_preserves_codex_search_response_contract() -> None:
    config = _make_config(
        search_provider="self_hosted_google",
        tool_profile="test-web-run-mapping",
    )
    request = _make_request(
        _search_body({"search_query": [{"q": "Python documentation"}]})
    )
    client = _FakeSelfHostedGoogleClient()

    response = asyncio.run(
        handle_codex_auxiliary(
            request,
            config,
            "alpha/search",
            browser_client=client,
        )
    )

    assert response.status_code == 200
    body = json.loads(response.body)
    assert "https://docs.python.org/3/" in body["output"]
    assert body["results"] == [
        {
            "type": "text_result",
            "title": "Python documentation",
            "url": "https://docs.python.org/3/",
            "content": "Python documentation from Google Search.",
            "ref_id": "turn0search0",
        }
    ]
    assert client.search_calls == [("Python documentation", WebSearchSettings())]
    request.app.transport.send_passthrough.assert_not_awaited()


def test_web_run_mapping_requires_global_api_key_for_search_query() -> None:
    config = _make_config(tool_profile="test-web-run-mapping")
    request = _make_request(
        _search_body({"search_query": [{"q": "Python documentation"}]})
    )

    response = asyncio.run(handle_codex_auxiliary(request, config, "alpha/search"))

    assert response.status_code == 501
    payload = json.loads(response.body)
    assert "Admin > Web Search" in payload["error"]["message"]
    request.app.transport.send_passthrough.assert_not_awaited()


def test_custom_pass_through_profile_keeps_search_native_with_tavily() -> None:
    config = _make_config(tavily_api_key="tvly-test", tool_profile="test-pass-through")
    request = _make_request(
        _search_body({"search_query": [{"q": "Python documentation"}]})
    )

    response = asyncio.run(
        handle_codex_auxiliary(
            request,
            config,
            "alpha/search",
            search_client=_FakeTavilyClient(),
        )
    )

    assert response.status_code == 202
    request.app.transport.send_passthrough.assert_awaited_once()
    assert request.app.transport.send_passthrough.await_args.args[1] == (
        "https://upstream.example/v1/alpha/search"
    )


def test_search_passthrough_does_not_force_v1_into_upstream_base_url() -> None:
    config = _make_config(
        upstream_base_url="https://upstream.example",
        tool_profile="test-pass-through",
    )
    request = _make_request(
        _search_body({"search_query": [{"q": "Python documentation"}]})
    )

    response = asyncio.run(handle_codex_auxiliary(request, config, "alpha/search"))

    assert response.status_code == 202
    assert request.app.transport.send_passthrough.await_args.args[1] == (
        "https://upstream.example/alpha/search"
    )


def test_local_search_records_gateway_log_stages(tmp_path: Path) -> None:
    trace_path = tmp_path / "search-trace.jsonl"
    config = _make_config(
        tavily_api_key="tvly-test", tool_profile="test-web-run-mapping"
    )
    request = _make_request(
        _search_body({"search_query": [{"q": "Python documentation"}]})
    )
    request.app.stream_trace_state = StreamTraceState(
        StreamTraceConfig(enabled=True, path=str(trace_path))
    )

    response = asyncio.run(
        handle_codex_auxiliary(
            request,
            config,
            "alpha/search",
            search_client=_FakeTavilyClient(),
        )
    )

    assert response.status_code == 200
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert [record["stage"] for record in records] == [
        "codex_search_request",
        "codex_search_response",
    ]
    assert records[0]["data"]["command_types"] == ["search_query"]
    assert records[1]["data"]["executor"] == "tavily_python"


def test_non_passthrough_search_uses_local_tavily_bridge() -> None:
    config = _make_config(
        "chat", tavily_api_key="tvly-test", upstream_model="deepseek-v4-flash"
    )
    request = _make_request(
        _search_body({"search_query": [{"q": "Python documentation"}]})
    )

    response = asyncio.run(
        handle_codex_auxiliary(
            request,
            config,
            "alpha/search",
            search_client=_FakeTavilyClient(),
        )
    )

    assert response.status_code == 200
    assert "docs.python.org" in json.loads(response.body)["output"]
    request.app.transport.send_passthrough.assert_not_awaited()


def test_local_search_open_returns_static_page_content() -> None:
    config = _make_config(
        tavily_api_key="tvly-test", tool_profile="test-web-run-mapping"
    )
    request = _make_request(
        _search_body({"open": [{"ref_id": "https://docs.python.org/3/"}]})
    )
    page_client = _FakePageClient()

    response = asyncio.run(
        handle_codex_auxiliary(
            request,
            config,
            "alpha/search",
            page_client=page_client,
        )
    )

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert "Python 3 Documentation" in payload["output"]
    assert page_client.calls == ["https://docs.python.org/3/"]
    request.app.transport.send_passthrough.assert_not_awaited()


def test_stored_reference_open_uses_the_app_owned_search_store() -> None:
    config = _make_config(
        tavily_api_key="tvly-test", tool_profile="test-web-run-mapping"
    )
    search_request = _make_request(
        _search_body({"search_query": [{"q": "Python documentation"}]})
    )
    store = search_request.app.codex_search_reference_store
    search_response = asyncio.run(
        handle_codex_auxiliary(
            search_request,
            config,
            "alpha/search",
            search_client=_FakeTavilyClient(),
        )
    )
    request = _make_request(_search_body({"open": [{"ref_id": "turn0search0"}]}))
    request.app.codex_search_reference_store = store
    page_client = _FakePageClient()
    response = asyncio.run(
        handle_codex_auxiliary(
            request,
            config,
            "alpha/search",
            page_client=page_client,
        )
    )

    assert search_response.status_code == 200
    assert "turn0search0" in json.loads(search_response.body)["output"]
    assert response.status_code == 200
    assert "Python 3 Documentation" in json.loads(response.body)["output"]
    assert page_client.calls == ["https://docs.python.org/3/"]
    request.app.transport.send_passthrough.assert_not_awaited()


def test_tavily_configuration_does_not_intercept_image_endpoints() -> None:
    config = _make_config(tavily_api_key="tvly-test")
    request = _make_request({"model": "gateway-model", "prompt": "draw a fox"})

    response = asyncio.run(
        handle_codex_auxiliary(request, config, "images/generations")
    )

    assert response.status_code == 202
    request.app.transport.send_passthrough.assert_awaited_once()


@pytest.mark.parametrize(
    "api_type",
    ["responses", "chat", "anthropic", "google"],
)
@pytest.mark.parametrize("upstream_path", ["images/generations", "images/edits"])
def test_modified_imagegen_uses_profile_openai_images_api(
    api_type: str,
    upstream_path: str,
) -> None:
    config = _make_config(
        api_type,
        image_state="modified",
        upstream_model="gpt-image-2",
    )
    body = {
        "model": "gateway-model",
        "prompt": "draw a fox",
        "images": [{"image_url": "data:image/png;base64,AAAA"}],
    }
    request = _make_request(body)

    response = asyncio.run(handle_codex_auxiliary(request, config, upstream_path))

    assert response.status_code == 202
    provider_info, url, forwarded_body = (
        request.app.transport.send_passthrough.call_args.args
    )
    assert provider_info.base_url == "https://images.example/v1"
    assert provider_info.auth_headers() == {"Authorization": "Bearer image-token"}
    assert url == f"https://images.example/v1/{upstream_path}"
    assert forwarded_body == {
        "model": "gpt-image-2",
        "prompt": "draw a fox",
        "images": [{"image_url": "data:image/png;base64,AAAA"}],
    }


@pytest.mark.parametrize(
    ("base_url", "token", "expected"),
    [
        ("", "image-token", "requires a Base URL"),
        ("https://images.example/v1", "", "requires a Token"),
        ("ftp://images.example/v1", "image-token", "must start with http://"),
    ],
)
def test_modified_imagegen_rejects_invalid_profile_configuration(
    base_url: str,
    token: str,
    expected: str,
) -> None:
    config = _make_config(
        "chat",
        image_state="modified",
        image_base_url=base_url,
        image_token=token,
    )
    request = _make_request({"model": "gateway-model", "prompt": "draw a fox"})

    response = asyncio.run(
        handle_codex_auxiliary(request, config, "images/generations")
    )

    assert response.status_code == 400
    assert expected in json.loads(response.body)["error"]["message"]
    request.app.transport.send_passthrough.assert_not_awaited()


def test_modified_imagegen_records_secret_free_gateway_log_stages(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "image-trace.jsonl"
    config = _make_config("chat", image_state="modified")
    request = _make_request({"model": "gateway-model", "prompt": "draw a fox"})
    request.app.stream_trace_state = StreamTraceState(
        StreamTraceConfig(enabled=True, path=str(trace_path))
    )

    response = asyncio.run(
        handle_codex_auxiliary(request, config, "images/generations")
    )

    assert response.status_code == 202
    trace_text = trace_path.read_text()
    assert "image-token" not in trace_text
    records = [json.loads(line) for line in trace_text.splitlines()]
    assert [record["stage"] for record in records] == [
        "codex_image_request",
        "codex_image_response",
    ]
    assert records[0]["data"] == {
        "base_url": "https://images.example/v1",
        "endpoint": "images/generations",
        "executor": "openai_images_api",
    }
    assert records[1]["data"] == {"status_code": 202}
