"""Tests for cached browser readiness used by model-facing web.run projection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from codex_rosetta.gateway import web_run_health
from codex_rosetta.gateway.app import _resolve_request_tool_runtime_capabilities
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.web_run_capabilities import (
    WEB_RUN_BASIC_SEARCH_CAPABILITY,
    WEB_RUN_SIDECAR_CAPABILITY,
)
from codex_rosetta.gateway.web_run_health import WebRunHealthState
from codex_rosetta.routing import ResolvedRoute


class _FakeAsyncClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


def test_unconfigured_status_does_not_probe(monkeypatch):
    async def unexpected_request(*args, **kwargs):
        raise AssertionError("unconfigured health must not issue a request")

    monkeypatch.setattr(web_run_health, "request_bounded_response", unexpected_request)

    status = asyncio.run(WebRunHealthState().status(None))

    assert status.as_dict() == {
        "configured": False,
        "service_online": False,
        "browser_ready": None,
    }


def test_health_probe_is_bounded_and_reports_browser_readiness(monkeypatch):
    calls = []

    async def fake_request(client, method, url, **kwargs):
        calls.append((client.kwargs, method, url, kwargs))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "ok", "browser_ready": True},
        )

    monkeypatch.setattr(web_run_health, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(web_run_health, "request_bounded_response", fake_request)

    status = asyncio.run(WebRunHealthState().status("http://web-run:8080/"))

    assert status.as_dict() == {
        "configured": True,
        "service_online": True,
        "browser_ready": True,
    }
    assert calls == [
        (
            {"timeout": 2.0},
            "GET",
            "http://web-run:8080/health",
            {
                "max_success_bytes": 64 * 1024,
                "max_error_bytes": 64 * 1024,
            },
        )
    ]


def test_probe_failure_is_credential_free_and_fail_closed(monkeypatch):
    async def fail_request(*args, **kwargs):
        raise RuntimeError("sensitive upstream detail")

    monkeypatch.setattr(web_run_health, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(web_run_health, "request_bounded_response", fail_request)

    status = asyncio.run(WebRunHealthState().status("http://web-run:8080"))

    assert status.as_dict() == {
        "configured": True,
        "service_online": False,
        "browser_ready": None,
    }


def test_health_cache_coalesces_concurrent_refreshes(monkeypatch):
    calls = 0

    async def fake_request(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "ok", "browser_ready": True},
        )

    monkeypatch.setattr(web_run_health, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(web_run_health, "request_bounded_response", fake_request)

    async def run():
        state = WebRunHealthState()
        return await asyncio.gather(
            state.status("http://web-run:8080"),
            state.status("http://web-run:8080"),
            state.status("http://web-run:8080"),
        )

    statuses = asyncio.run(run())

    assert calls == 1
    assert all(status.browser_ready is True for status in statuses)


def test_health_cache_refreshes_after_ttl_and_invalidation(monkeypatch):
    now = [10.0]
    readiness = [False, True, False]
    calls = 0

    async def fake_request(*args, **kwargs):
        nonlocal calls
        value = readiness[calls]
        calls += 1
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "ok", "browser_ready": value},
        )

    monkeypatch.setattr(web_run_health, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(web_run_health, "request_bounded_response", fake_request)

    async def run():
        state = WebRunHealthState(monotonic=lambda: now[0])
        first = await state.status("http://web-run:8080")
        cached = await state.status("http://web-run:8080")
        now[0] += 5.0
        expired = await state.status("http://web-run:8080")
        state.invalidate()
        invalidated = await state.status("http://web-run:8080")
        return first, cached, expired, invalidated

    first, cached, expired, invalidated = asyncio.run(run())

    assert calls == 3
    assert [
        first.browser_ready,
        cached.browser_ready,
        expired.browser_ready,
        invalidated.browser_ready,
    ] == [False, False, True, False]


def test_health_cache_changes_key_when_sidecar_url_changes(monkeypatch):
    calls = []

    async def fake_request(client, method, url, **kwargs):
        calls.append(url)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "ok", "browser_ready": True},
        )

    monkeypatch.setattr(web_run_health, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(web_run_health, "request_bounded_response", fake_request)

    async def run():
        state = WebRunHealthState()
        await state.status("http://web-run-a:8080")
        await state.status("http://web-run-b:8080")

    asyncio.run(run())

    assert calls == [
        "http://web-run-a:8080/health",
        "http://web-run-b:8080/health",
    ]


def test_request_route_adds_browser_capability_only_when_ready(monkeypatch):
    readiness = [False, True]

    async def fake_request(*args, **kwargs):
        value = readiness.pop(0)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "ok", "browser_ready": value},
        )

    monkeypatch.setattr(web_run_health, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(web_run_health, "request_bounded_response", fake_request)

    async def run():
        state = WebRunHealthState(ttl_seconds=0)
        app = SimpleNamespace(web_run_health_state=state)
        config = cast(
            GatewayConfig,
            SimpleNamespace(
                web_run_sidecar_url="http://web-run:8080",
                web_run_sidecar_token="sidecar-token",
                web_search={"provider": "tavily", "tavily_api_key": ""},
            ),
        )
        route = ResolvedRoute(
            source_provider="openai_responses",
            target_provider="openai_responses",
            provider_name="test",
            tool_profile={"namespace.web.run": "modified"},
        )
        unavailable = await _resolve_request_tool_runtime_capabilities(
            app,
            config,
            route,
            {
                "tools": [
                    {
                        "type": "custom",
                        "name": "exec",
                        "description": "### `web__run`",
                    }
                ]
            },
        )
        available = await _resolve_request_tool_runtime_capabilities(
            app,
            config,
            route,
            {"tools": [{"type": "function", "name": "web__run"}]},
        )
        return route, unavailable, available

    original, unavailable, available = asyncio.run(run())

    assert unavailable is original
    assert WEB_RUN_SIDECAR_CAPABILITY not in unavailable.tool_runtime_capabilities
    assert WEB_RUN_SIDECAR_CAPABILITY in available.tool_runtime_capabilities


@pytest.mark.parametrize(
    "provider",
    ["self_hosted_google", "self_hosted_bing", "self_hosted_bing_browser"],
)
def test_ready_sidecar_adds_self_hosted_search_capability(monkeypatch, provider):
    async def fake_request(*args, **kwargs):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "ok", "browser_ready": True},
        )

    monkeypatch.setattr(web_run_health, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(web_run_health, "request_bounded_response", fake_request)

    config = cast(
        GatewayConfig,
        SimpleNamespace(
            web_run_sidecar_url="http://web-run:8080",
            web_run_sidecar_token="sidecar-token",
            web_search={"provider": provider, "tavily_api_key": ""},
        ),
    )
    route = ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_responses",
        provider_name="test",
        tool_profile={"namespace.web.run": "modified"},
    )

    resolved = asyncio.run(
        _resolve_request_tool_runtime_capabilities(
            SimpleNamespace(web_run_health_state=WebRunHealthState()),
            config,
            route,
            {"tools": [{"type": "function", "name": "web__run"}]},
        )
    )

    assert resolved.tool_runtime_capabilities == frozenset(
        {WEB_RUN_BASIC_SEARCH_CAPABILITY, WEB_RUN_SIDECAR_CAPABILITY}
    )


def test_request_route_skips_health_for_passthrough_profile(monkeypatch):
    async def unexpected_request(*args, **kwargs):
        raise AssertionError("passthrough must not probe sidecar readiness")

    monkeypatch.setattr(web_run_health, "request_bounded_response", unexpected_request)

    async def run():
        app = SimpleNamespace(web_run_health_state=WebRunHealthState())
        config = cast(
            GatewayConfig,
            SimpleNamespace(
                web_run_sidecar_url="http://web-run:8080",
                web_run_sidecar_token="sidecar-token",
                web_search={"provider": "tavily", "tavily_api_key": ""},
            ),
        )
        route = ResolvedRoute(
            source_provider="openai_responses",
            target_provider="openai_responses",
            provider_name="test",
            tool_profile={"namespace.web.run": "passthrough"},
        )
        return route, await _resolve_request_tool_runtime_capabilities(
            app,
            config,
            route,
            {"tools": [{"type": "function", "name": "web__run"}]},
        )

    original, resolved = asyncio.run(run())

    assert resolved is original
