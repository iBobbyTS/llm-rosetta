"""Bounded Bing Search executor for the ``web-run`` sidecar."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlsplit

BING_SEARCH_ORIGIN = "https://www.bing.com"
_MAX_RESULTS = 10
_MAX_QUERY_CHARS = 4_000
_MAX_DOMAIN_CHARS = 253
_MAX_TITLE_CHARS = 500
_MAX_URL_CHARS = 8_192
_MAX_CONTENT_CHARS = 1_200
_SEARCH_TIMEOUT_MS = 30_000

_RSS_RESULT_EXTRACTION_SCRIPT = r"""
() => {
  const output = [];
  const childText = (item, name) => {
    const node = Array.from(item.children)
      .find(child => child.localName.toLowerCase() === name);
    return node ? (node.textContent || '').trim() : '';
  };
  for (const item of document.querySelectorAll('item')) {
    output.push({
      title: childText(item, 'title'),
      url: childText(item, 'link'),
      content: childText(item, 'description'),
    });
  }
  return output;
}
"""

_BROWSER_RESULT_EXTRACTION_SCRIPT = r"""
() => {
  const output = [];
  const seen = new Set();
  for (const anchor of document.querySelectorAll('#b_results li.b_algo h2 a')) {
    const href = anchor.href || '';
    if (!href.startsWith('http://') && !href.startsWith('https://')) continue;
    if (seen.has(href)) continue;
    seen.add(href);

    const container = anchor.closest('li.b_algo');
    const snippet = container?.querySelector('.b_caption p, .b_paractl, p');
    output.push({
      title: (anchor.innerText || anchor.textContent || '').trim(),
      url: href,
      content: snippet
        ? (snippet.innerText || snippet.textContent || '').trim()
        : '',
    });
  }
  return output;
}
"""


class BingSearchError(RuntimeError):
    """Stable failure returned by the self-hosted Bing executor."""


def build_bing_search_url(
    query: str,
    *,
    max_results: int,
    include_domains: tuple[str, ...] = (),
) -> str:
    """Build one bounded Bing result URL with optional site restrictions."""
    effective_query = _effective_query(query, max_results, include_domains)
    return (
        f"{BING_SEARCH_ORIGIN}/search?format=rss&count={max(10, max_results)}"
        f"&setlang=en-US&q={quote_plus(effective_query)}"
    )


def build_bing_browser_search_url(
    query: str,
    *,
    max_results: int,
    include_domains: tuple[str, ...] = (),
) -> str:
    """Build a normal interactive Bing result-page URL."""
    effective_query = _effective_query(query, max_results, include_domains)
    return (
        f"{BING_SEARCH_ORIGIN}/search?count={max(10, max_results)}"
        f"&setlang=en-US&q={quote_plus(effective_query)}"
    )


async def execute_bing_search(
    browser: Any,
    *,
    query: str,
    max_results: int,
    include_domains: tuple[str, ...],
    route_handler: Callable[[Any, Any], Awaitable[None]],
) -> dict[str, Any]:
    """Search Bing RSS in an isolated browser context and normalize results."""
    return await _execute_bing_result_page(
        browser,
        search_url=build_bing_search_url(
            query,
            max_results=max_results,
            include_domains=include_domains,
        ),
        extraction_script=_RSS_RESULT_EXTRACTION_SCRIPT,
        max_results=max_results,
        include_domains=include_domains,
        route_handler=route_handler,
        blocked_message=(
            "Bing Search blocked the automated request; retry later or use Tavily"
        ),
    )


async def execute_bing_browser_search(
    browser: Any,
    *,
    query: str,
    max_results: int,
    include_domains: tuple[str, ...],
    route_handler: Callable[[Any, Any], Awaitable[None]],
) -> dict[str, Any]:
    """Search Bing's interactive result page in an isolated browser context."""
    return await _execute_bing_result_page(
        browser,
        search_url=build_bing_browser_search_url(
            query,
            max_results=max_results,
            include_domains=include_domains,
        ),
        extraction_script=_BROWSER_RESULT_EXTRACTION_SCRIPT,
        max_results=max_results,
        include_domains=include_domains,
        route_handler=route_handler,
        blocked_message=(
            "Bing Search blocked the browser request; retry later or use Bing RSS "
            "or Tavily"
        ),
    )


async def _execute_bing_result_page(
    browser: Any,
    *,
    search_url: str,
    extraction_script: str,
    max_results: int,
    include_domains: tuple[str, ...],
    route_handler: Callable[[Any, Any], Awaitable[None]],
    blocked_message: str,
) -> dict[str, Any]:
    allowed_domains = tuple(_normalize_domain(value) for value in include_domains)
    context = await browser.new_context(
        accept_downloads=False,
        service_workers="block",
        locale="en-US",
    )
    try:
        await context.route("**/*", route_handler)
        page = await context.new_page()
        response = await page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=_SEARCH_TIMEOUT_MS,
        )
        if response is not None and response.status >= 400:
            raise BingSearchError(f"Bing Search returned HTTP {response.status}")
        try:
            await page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass
        raw_results = await page.evaluate(extraction_script)
        if not isinstance(raw_results, list):
            raise BingSearchError("Bing Search returned an invalid result page")
        results = _normalize_results(
            raw_results,
            max_results=max_results,
            include_domains=allowed_domains,
        )
        if not results:
            body_text = str(
                await page.evaluate(
                    "() => document.documentElement.innerText || "
                    "document.documentElement.textContent || ''"
                )
            ).casefold()
            if any(
                marker in body_text
                for marker in (
                    "verify you are a human",
                    "our systems have detected unusual traffic",
                    "one last step",
                    "captcha",
                )
            ):
                raise BingSearchError(blocked_message)
        return {"results": results}
    except asyncio.CancelledError:
        raise
    except BingSearchError:
        raise
    except Exception as exc:
        message = str(exc).replace("\n", " ")[:500]
        raise BingSearchError(f"Bing Search request failed: {message}") from exc
    finally:
        await context.close()


def _normalize_results(
    raw_results: list[Any],
    *,
    max_results: int,
    include_domains: tuple[str, ...],
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = _bounded_text(item.get("title"), _MAX_TITLE_CHARS)
        url = _bounded_text(_unwrap_bing_url(item.get("url")), _MAX_URL_CHARS)
        content = _bounded_text(item.get("content"), _MAX_CONTENT_CHARS)
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not hostname or not title:
            continue
        if hostname == "bing.com" or hostname.endswith(".bing.com"):
            continue
        if include_domains and not any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in include_domains
        ):
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append({"title": title, "url": url, "content": content})
        if len(results) >= max_results:
            break
    return results


def _unwrap_bing_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not (hostname == "bing.com" or hostname.endswith(".bing.com")):
        return value
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if encoded.startswith("a1"):
        payload = encoded[2:]
        try:
            padding = "=" * (-len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        except ValueError, UnicodeDecodeError:
            return value
        if decoded.startswith(("http://", "https://")):
            return decoded
    if encoded.startswith(("http://", "https://")):
        return encoded
    return value


def _effective_query(
    query: str,
    max_results: int,
    include_domains: tuple[str, ...],
) -> str:
    normalized_query = query.strip()
    if not normalized_query or len(normalized_query) > _MAX_QUERY_CHARS:
        raise ValueError(f"query must contain 1-{_MAX_QUERY_CHARS} characters")
    if isinstance(max_results, bool) or not 1 <= max_results <= _MAX_RESULTS:
        raise ValueError(f"max_results must be between 1 and {_MAX_RESULTS}")

    domains = tuple(_normalize_domain(value) for value in include_domains)
    if not domains:
        return normalized_query
    sites = " OR ".join(f"site:{domain}" for domain in domains)
    return f"({normalized_query}) ({sites})"


def _normalize_domain(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("include_domains entries must be strings")
    domain = value.strip().lower().rstrip(".")
    labels = domain.split(".")
    if (
        not domain
        or len(domain) > _MAX_DOMAIN_CHARS
        or "://" in domain
        or "/" in domain
        or "@" in domain
        or any(not part or len(part) > 63 for part in labels)
        or any(part.startswith("-") or part.endswith("-") for part in labels)
        or any(
            not all(char.isalnum() or char == "-" for char in part) for part in labels
        )
    ):
        raise ValueError(f"invalid include_domains entry: {value!r}")
    return domain


def _bounded_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


__all__ = [
    "BingSearchError",
    "build_bing_browser_search_url",
    "build_bing_search_url",
    "execute_bing_browser_search",
    "execute_bing_search",
]
