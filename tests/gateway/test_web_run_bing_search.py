"""Tests for the self-hosted Bing executor packaged in ``web-run``."""

from __future__ import annotations

import asyncio
import base64

import pytest

from codex_rosetta.gateway.resources.web_run.bing_search import (
    build_bing_browser_search_url,
    build_bing_search_url,
    execute_bing_browser_search,
    execute_bing_search,
)


def test_bing_search_url_applies_site_filters_without_changing_input() -> None:
    url = build_bing_search_url(
        "official Python docs",
        max_results=5,
        include_domains=("docs.python.org", "python.org"),
    )

    assert url.startswith("https://www.bing.com/search?")
    assert "format=rss" in url
    assert "official+Python+docs" in url
    assert "site%3Adocs.python.org" in url
    assert "site%3Apython.org" in url


def test_bing_browser_search_url_uses_interactive_html_results() -> None:
    url = build_bing_browser_search_url(
        "official Python docs",
        max_results=5,
        include_domains=("docs.python.org",),
    )

    assert url.startswith("https://www.bing.com/search?")
    assert "format=rss" not in url
    assert "official+Python+docs" in url
    assert "site%3Adocs.python.org" in url


@pytest.mark.parametrize(
    "domain",
    [
        "https://python.org",
        "python.org/path",
        "user@example.com",
        "bad..domain",
        "-bad.example",
        "bad-.example",
        f"{'x' * 64}.example",
    ],
)
def test_bing_search_url_rejects_non_domain_filters(domain: str) -> None:
    with pytest.raises(ValueError, match="invalid include_domains"):
        build_bing_search_url("python", max_results=5, include_domains=(domain,))


class _FakePage:
    async def goto(self, *args, **kwargs):
        return type("Response", (), {"status": 200})()

    async def wait_for_load_state(self, *args, **kwargs):
        return None

    async def evaluate(self, script):
        del script
        target = "https://www.python.org/"
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        return [
            {
                "title": "Python",
                "url": f"https://www.bing.com/ck/a?u=a1{encoded}&ntb=1",
                "content": "Welcome to Python.org",
            },
            {
                "title": "Filtered",
                "url": "https://example.com/",
                "content": "Must not escape the domain filter",
            },
        ]


class _FakeContext:
    def __init__(self) -> None:
        self.closed = False
        self.routes = []

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    async def new_page(self):
        return _FakePage()

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.context = _FakeContext()

    async def new_context(self, **kwargs):
        return self.context


def test_bing_search_unwraps_redirects_filters_results_and_closes_context() -> None:
    browser = _FakeBrowser()

    async def route_handler(route, request):
        del route, request

    result = asyncio.run(
        execute_bing_search(
            browser,
            query="official Python",
            max_results=5,
            include_domains=("python.org",),
            route_handler=route_handler,
        )
    )

    assert result == {
        "results": [
            {
                "title": "Python",
                "url": "https://www.python.org/",
                "content": "Welcome to Python.org",
            }
        ]
    }
    assert browser.context.closed is True


def test_bing_browser_search_uses_same_bounds_and_domain_filter() -> None:
    browser = _FakeBrowser()

    async def route_handler(route, request):
        del route, request

    result = asyncio.run(
        execute_bing_browser_search(
            browser,
            query="official Python",
            max_results=5,
            include_domains=("python.org",),
            route_handler=route_handler,
        )
    )

    assert result == {
        "results": [
            {
                "title": "Python",
                "url": "https://www.python.org/",
                "content": "Welcome to Python.org",
            }
        ]
    }
    assert browser.context.closed is True
