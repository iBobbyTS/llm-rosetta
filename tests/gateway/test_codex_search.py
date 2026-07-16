"""Contract tests for the local Codex standalone-search bridge."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from codex_rosetta.gateway.codex_page import OpenedPage
from codex_rosetta.gateway.codex_search import (
    CodexSearchInvalidRequest,
    CodexSearchNotImplemented,
    execute_local_codex_search,
    should_use_local_codex_search,
)
from codex_rosetta.gateway.codex_search_references import CodexSearchReferenceStore
from codex_rosetta.gateway.web_run_sidecar import WebRunSidecarInvalidRequest
from codex_rosetta.gateway.web_search import WebSearchSettings


class _FakeTavilyClient:
    def __init__(self, *, url: str = "https://docs.python.org/3/") -> None:
        self.calls: list[tuple[str, WebSearchSettings]] = []
        self.url = url

    async def search(
        self,
        query: str,
        *,
        settings: WebSearchSettings,
    ) -> dict[str, Any]:
        self.calls.append((query, settings))
        return {
            "answer": f"Answer for {query}",
            "results": [
                {
                    "title": "Python documentation",
                    "url": self.url,
                    "content": "The official Python 3 documentation.",
                    "score": 0.99,
                }
            ],
        }


class _FakePageClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def open(self, url: str) -> OpenedPage:
        self.calls.append(url)
        return OpenedPage(
            url=url,
            title="Python 3 Documentation",
            lines=("Overview", "What's new", "Tutorial", "Library Reference"),
        )


class _FakeBrowserClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def execute(
        self,
        *,
        session_id: str,
        operation: str,
        arguments: dict[str, Any],
    ) -> str:
        self.calls.append((session_id, operation, arguments))
        return f"browser:{operation}:{arguments['ref_id']}"


class _FakeSelfHostedGoogleClient(_FakeBrowserClient):
    def __init__(self) -> None:
        super().__init__()
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
                    "title": "Python",
                    "url": "https://www.python.org/",
                    "content": "The Python programming language.",
                }
            ]
        }


def _body(commands: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "id": "search-session",
        "model": "gateway-model",
        "commands": commands,
        **extra,
    }


def test_search_query_uses_tavily_with_supported_filters() -> None:
    client = _FakeTavilyClient()
    result = asyncio.run(
        execute_local_codex_search(
            _body(
                {
                    "search_query": [
                        {
                            "q": "official Python documentation",
                            "domains": ["docs.python.org", "python.org"],
                        }
                    ],
                    "response_length": "long",
                },
                settings={
                    "search_context_size": "low",
                    "filters": {"allowed_domains": ["docs.python.org"]},
                    "allowed_callers": ["direct"],
                    "external_web_access": True,
                },
            ),
            {"tavily_api_key": "tvly-test"},
            client=client,
        )
    )

    assert client.calls == [
        (
            "official Python documentation",
            WebSearchSettings(
                max_results=8,
                search_depth="advanced",
                include_domains=("docs.python.org",),
            ),
        )
    ]
    assert result.search_count == 1
    assert result.open_count == 0
    assert result.time_count == 0
    assert result.tavily_result_count == 1
    assert "https://docs.python.org/3/" in result.output
    assert result.response_body() == {"output": result.output}


def test_time_uses_python_without_tavily() -> None:
    result = asyncio.run(
        execute_local_codex_search(
            _body(
                {
                    "time": [
                        {"utc_offset": "+03:00"},
                        {"utc_offset": "-05:30"},
                    ]
                }
            ),
            {},
            now=lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert result.search_count == 0
    assert result.open_count == 0
    assert result.time_count == 2
    assert "+03:00: 2026-07-12T15:00:00+03:00" in result.output
    assert "-05:30: 2026-07-12T06:30:00-05:30" in result.output


def test_open_direct_url_returns_line_addressable_static_page() -> None:
    page_client = _FakePageClient()
    result = asyncio.run(
        execute_local_codex_search(
            _body(
                {
                    "open": [
                        {
                            "ref_id": "https://docs.python.org/3/",
                            "lineno": 2,
                        }
                    ]
                }
            ),
            {},
            page_client=page_client,
        )
    )

    assert page_client.calls == ["https://docs.python.org/3/"]
    assert result.search_count == 0
    assert result.open_count == 1
    assert result.time_count == 0
    assert "Title: Python 3 Documentation" in result.output
    assert "L2: Tutorial" in result.output
    assert "L0: Overview" not in result.output


def test_search_result_reference_can_be_opened_in_the_same_session() -> None:
    store = CodexSearchReferenceStore()
    search_result = asyncio.run(
        execute_local_codex_search(
            _body({"search_query": [{"q": "python"}]}),
            {"tavily_api_key": "tvly-test"},
            client=_FakeTavilyClient(),
            reference_store=store,
            principal_id="client-a",
        )
    )
    page_client = _FakePageClient()
    open_result = asyncio.run(
        execute_local_codex_search(
            _body({"open": [{"ref_id": "turn0search0"}]}),
            {},
            page_client=page_client,
            reference_store=store,
            principal_id="client-a",
        )
    )

    assert "[turn0search0] Python documentation" in search_result.output
    assert page_client.calls == ["https://docs.python.org/3/"]
    assert "Title: Python 3 Documentation" in open_result.output
    assert open_result.stored_reference_open_count == 1


def test_sidecar_executes_open_click_find_and_pdf_screenshot() -> None:
    store = CodexSearchReferenceStore()
    asyncio.run(
        execute_local_codex_search(
            _body({"search_query": [{"q": "python"}]}),
            {"tavily_api_key": "tvly-test"},
            client=_FakeTavilyClient(),
            reference_store=store,
            principal_id="client-a",
        )
    )
    browser = _FakeBrowserClient()

    result = asyncio.run(
        execute_local_codex_search(
            _body(
                {
                    "open": [{"ref_id": "turn0search0", "lineno": 2}],
                    "click": [{"ref_id": "turn1fetch0", "id": 7}],
                    "find": [{"ref_id": "turn1fetch0", "pattern": "Python"}],
                    "screenshot": [
                        {"ref_id": "https://example.com/file.pdf", "pageno": 3}
                    ],
                }
            ),
            {},
            browser_client=browser,
            reference_store=store,
            principal_id="client-a",
        )
    )

    session_ids = {call[0] for call in browser.calls}
    assert len(session_ids) == 1
    assert len(next(iter(session_ids))) == 64
    assert browser.calls[0][1:] == (
        "open",
        {"ref_id": "https://docs.python.org/3/", "lineno": 2},
    )
    assert browser.calls[1][1:] == (
        "click",
        {"ref_id": "turn1fetch0", "id": 7},
    )
    assert browser.calls[2][1:] == (
        "find",
        {"ref_id": "turn1fetch0", "pattern": "Python"},
    )
    assert browser.calls[3][1:] == (
        "screenshot",
        {"ref_id": "https://example.com/file.pdf", "pageno": 3},
    )
    assert result.open_count == 1
    assert result.browser_open_count == 1
    assert result.click_count == 1
    assert result.find_count == 1
    assert result.screenshot_count == 1
    assert result.trace_summary()["executor"] == "tavily_python_web_run_sidecar"


def test_sidecar_invalid_reference_maps_to_codex_invalid_request() -> None:
    class InvalidBrowser:
        async def execute(self, **kwargs: Any) -> str:
            del kwargs
            raise WebRunSidecarInvalidRequest("Unknown or expired page reference")

    with pytest.raises(CodexSearchInvalidRequest, match="Unknown or expired"):
        asyncio.run(
            execute_local_codex_search(
                _body({"click": [{"ref_id": "turn9fetch0", "id": 1}]}),
                {},
                browser_client=InvalidBrowser(),
            )
        )


@pytest.mark.parametrize(
    ("principal_id", "session_id"),
    [("client-b", "search-session"), ("client-a", "other-session")],
)
def test_stored_reference_fails_closed_outside_its_owner_session(
    principal_id: str,
    session_id: str,
) -> None:
    store = CodexSearchReferenceStore()
    asyncio.run(
        execute_local_codex_search(
            _body({"search_query": [{"q": "python"}]}),
            {"tavily_api_key": "tvly-test"},
            client=_FakeTavilyClient(),
            reference_store=store,
            principal_id="client-a",
        )
    )

    body = _body({"open": [{"ref_id": "turn0search0"}]})
    body["id"] = session_id
    with pytest.raises(CodexSearchInvalidRequest, match="Unknown search reference"):
        asyncio.run(
            execute_local_codex_search(
                body,
                {},
                page_client=_FakePageClient(),
                reference_store=store,
                principal_id=principal_id,
            )
        )


def test_retried_search_reuses_results_and_reference_ids() -> None:
    store = CodexSearchReferenceStore()
    client = _FakeTavilyClient()
    body = _body({"search_query": [{"q": "python"}]})

    first = asyncio.run(
        execute_local_codex_search(
            body,
            {"tavily_api_key": "tvly-test"},
            client=client,
            reference_store=store,
            principal_id="client-a",
        )
    )
    second = asyncio.run(
        execute_local_codex_search(
            body,
            {"tavily_api_key": "tvly-test"},
            client=client,
            reference_store=store,
            principal_id="client-a",
        )
    )

    assert first.output == second.output
    assert first.search_cache_hit is False
    assert second.search_cache_hit is True
    assert len(client.calls) == 1


def test_parallel_searches_allocate_distinct_turn_references() -> None:
    store = CodexSearchReferenceStore()

    async def run() -> list[str]:
        results = await asyncio.gather(
            execute_local_codex_search(
                _body({"search_query": [{"q": "python one"}]}),
                {"tavily_api_key": "tvly-test"},
                client=_FakeTavilyClient(url="https://docs.python.org/3/"),
                reference_store=store,
                principal_id="client-a",
            ),
            execute_local_codex_search(
                _body({"search_query": [{"q": "python two"}]}),
                {"tavily_api_key": "tvly-test"},
                client=_FakeTavilyClient(url="https://docs.python.org/3/tutorial/"),
                reference_store=store,
                principal_id="client-a",
            ),
        )
        return [result.output for result in results]

    outputs = asyncio.run(run())
    assert any("[turn0search0]" in output for output in outputs)
    assert any("[turn1search0]" in output for output in outputs)


@pytest.mark.parametrize(
    ("commands", "settings", "feature"),
    [
        ({"click": [{"ref_id": "turn0fetch0", "id": 1}]}, None, "commands.click"),
        (
            {"find": [{"ref_id": "turn0fetch0", "pattern": "Python"}]},
            None,
            "commands.find",
        ),
        ({"image_query": [{"q": "python"}]}, None, "commands.image_query"),
        (
            {"screenshot": [{"ref_id": "turn0view0", "pageno": 0}]},
            None,
            "commands.screenshot",
        ),
        ({"finance": [{"ticker": "AMD", "type": "equity"}]}, None, "commands.finance"),
        ({"weather": [{"location": "Paris"}]}, None, "commands.weather"),
        ({"sports": [{"fn": "standings", "league": "nfl"}]}, None, "commands.sports"),
        (
            {"search_query": [{"q": "python", "recency": 7}]},
            None,
            "commands.search_query[].recency",
        ),
        (
            {"search_query": [{"q": "python"}]},
            {"user_location": {"type": "approximate", "country": "US"}},
            "settings.user_location",
        ),
        (
            {"search_query": [{"q": "python"}]},
            {"filters": {"blocked_domains": ["example.com"]}},
            "settings.filters.blocked_domains",
        ),
        (
            {"search_query": [{"q": "python"}]},
            {"external_web_access": "cached"},
            "settings.external_web_access",
        ),
    ],
)
def test_unsupported_features_fail_before_tavily(
    commands: dict[str, Any],
    settings: dict[str, Any] | None,
    feature: str,
) -> None:
    client = _FakeTavilyClient()
    body = _body(commands)
    if settings is not None:
        body["settings"] = settings

    with pytest.raises(CodexSearchNotImplemented, match=feature.replace("[", r"\[")):
        asyncio.run(
            execute_local_codex_search(
                body,
                {"tavily_api_key": "tvly-test"},
                client=client,
            )
        )

    assert client.calls == []


def test_mixed_supported_and_unsupported_request_is_atomic() -> None:
    client = _FakeTavilyClient()
    page_client = _FakePageClient()
    with pytest.raises(CodexSearchNotImplemented, match="commands.click"):
        asyncio.run(
            execute_local_codex_search(
                _body(
                    {
                        "search_query": [{"q": "python"}],
                        "open": [{"ref_id": "https://docs.python.org/3/"}],
                        "click": [{"ref_id": "turn0fetch0", "id": 1}],
                    }
                ),
                {"tavily_api_key": "tvly-test"},
                client=client,
                page_client=page_client,
            )
        )
    assert client.calls == []
    assert page_client.calls == []


@pytest.mark.parametrize(
    ("commands", "message"),
    [
        ({}, "at least one"),
        ({"search_query": [{"q": ""}]}, "non-empty string"),
        ({"search_query": [{"q": "python"}] * 5}, "at most 4"),
        ({"open": [{"ref_id": "https://example.com", "lineno": -1}]}, "non-negative"),
        ({"time": [{"utc_offset": "+14:30"}]}, "exceeds"),
        ({"time": [{"utc_offset": "UTC"}]}, "must match"),
    ],
)
def test_invalid_requests_are_rejected(commands: dict[str, Any], message: str) -> None:
    with pytest.raises(CodexSearchInvalidRequest, match=message):
        asyncio.run(execute_local_codex_search(_body(commands), {}))


def test_search_without_tavily_key_is_not_implemented() -> None:
    with pytest.raises(CodexSearchNotImplemented, match="Admin > Web Search"):
        asyncio.run(
            execute_local_codex_search(
                _body({"search_query": [{"q": "python"}]}),
                {},
            )
        )


@pytest.mark.parametrize(
    ("provider", "executor"),
    [
        ("self_hosted_google", "google_web_run_sidecar"),
        ("self_hosted_bing", "bing_web_run_sidecar"),
        ("self_hosted_bing_browser", "bing_browser_web_run_sidecar"),
    ],
)
def test_self_hosted_search_uses_web_run_sidecar(provider: str, executor: str) -> None:
    client = _FakeSelfHostedGoogleClient()

    result = asyncio.run(
        execute_local_codex_search(
            _body(
                {"search_query": [{"q": "official Python", "domains": ["python.org"]}]}
            ),
            {"provider": provider, "tavily_api_key": ""},
            browser_client=client,
        )
    )

    assert client.search_calls == [
        (
            "official Python",
            WebSearchSettings(include_domains=("python.org",)),
        )
    ]
    assert "https://www.python.org/" in result.output
    assert result.search_count == 1
    assert result.trace_summary()["executor"] == executor
    assert result.trace_summary()["search_result_count"] == 1
    assert result.trace_summary()["tavily_result_count"] == 0


@pytest.mark.parametrize(
    "provider",
    ["self_hosted_google", "self_hosted_bing", "self_hosted_bing_browser"],
)
def test_self_hosted_search_requires_sidecar(provider: str) -> None:
    with pytest.raises(CodexSearchNotImplemented, match="healthy web-run sidecar"):
        asyncio.run(
            execute_local_codex_search(
                _body({"search_query": [{"q": "python"}]}),
                {"provider": provider, "tavily_api_key": ""},
            )
        )


def test_max_output_tokens_applies_conservative_character_cap() -> None:
    result = asyncio.run(
        execute_local_codex_search(
            _body(
                {"time": [{"utc_offset": "+00:00"}]},
                max_output_tokens=20,
            ),
            {},
            now=lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert len(result.output) == 20


def test_local_bridge_selection_preserves_native_passthrough_without_tavily() -> None:
    search = _body({"search_query": [{"q": "python"}]})
    page = _body({"open": [{"ref_id": "https://docs.python.org/3/"}]})
    clock = _body({"time": [{"utc_offset": "+00:00"}]})

    assert not should_use_local_codex_search(
        search, {}, native_passthrough_available=True
    )
    assert should_use_local_codex_search(
        search,
        {"tavily_api_key": "tvly-test"},
        native_passthrough_available=True,
    )
    assert not should_use_local_codex_search(
        search,
        {"provider": "self_hosted_google"},
        native_passthrough_available=True,
    )
    assert should_use_local_codex_search(
        search,
        {"provider": "self_hosted_google"},
        native_passthrough_available=True,
        browser_available=True,
    )
    assert should_use_local_codex_search(
        search,
        {"provider": "self_hosted_bing"},
        native_passthrough_available=True,
        browser_available=True,
    )
    assert should_use_local_codex_search(
        search,
        {"provider": "self_hosted_bing_browser"},
        native_passthrough_available=True,
        browser_available=True,
    )
    assert should_use_local_codex_search(search, {}, native_passthrough_available=False)
    assert should_use_local_codex_search(page, {}, native_passthrough_available=True)
    assert should_use_local_codex_search(clock, {}, native_passthrough_available=True)
