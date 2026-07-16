"""Local implementation of the reliable subset of Codex ``web.run``."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from .config import SELF_HOSTED_WEB_SEARCH_PROVIDERS
from .codex_page import (
    PageOpenExecutionError,
    PageOpenInvalidRequest,
    PageOpenNotImplemented,
    StaticPageClient,
    StaticPageHTTPClient,
)
from .codex_search_references import (
    SEARCH_REFERENCE_RE,
    CodexSearchReferenceScope,
    CodexSearchReferenceStore,
    SearchQueryDraft,
    SearchResultDraft,
    StoredSearchBatch,
)
from .web_search import (
    TavilyHTTPClient,
    WebSearchClient,
    WebSearchSettings,
    format_web_search_result_for_model,
)
from .web_run_capabilities import (
    WEB_RUN_KNOWN_COMMANDS,
    web_run_supported_command_fields,
)
from .web_run_sidecar import (
    WebRunBrowserClient,
    WebRunSidecarError,
    WebRunSidecarInvalidRequest,
    WebRunSidecarNotImplemented,
)

_SUPPORTED_SETTINGS = frozenset(
    {
        "search_context_size",
        "filters",
        "allowed_callers",
        "external_web_access",
    }
)
_MAX_SEARCH_QUERIES = 4
_MAX_TIME_QUERIES = 16
_MAX_QUERY_CHARS = 4_000
_MAX_OUTPUT_CHARS = 100_000


class CodexSearchError(ValueError):
    """Base error for the local Codex search bridge."""


class CodexSearchInvalidRequest(CodexSearchError):
    """The request does not satisfy the public Codex Search contract."""


class CodexSearchNotImplemented(CodexSearchError):
    """The request requires semantics the local bridge cannot provide."""


class CodexSearchExecutionError(RuntimeError):
    """A supported operation failed in its external executor."""


@dataclass(frozen=True)
class CodexSearchBridgeResult:
    """Successful bridge output and bounded telemetry metadata."""

    output: str
    search_count: int
    open_count: int
    time_count: int
    search_result_count: int
    search_provider: str
    stored_reference_open_count: int = 0
    search_reference_count: int = 0
    search_cache_hit: bool = False
    click_count: int = 0
    find_count: int = 0
    screenshot_count: int = 0
    browser_open_count: int = 0

    @property
    def tavily_result_count(self) -> int:
        """Return Tavily-only count for backward-compatible trace consumers."""
        return self.search_result_count if self.search_provider == "tavily" else 0

    def response_body(self) -> dict[str, str]:
        return {"output": self.output}

    def trace_summary(self) -> dict[str, Any]:
        used_browser = bool(
            self.browser_open_count
            or self.click_count
            or self.find_count
            or self.screenshot_count
        )
        if (
            self.search_provider in SELF_HOSTED_WEB_SEARCH_PROVIDERS
            and self.search_count
        ):
            engine = self.search_provider.removeprefix("self_hosted_")
            executor = f"{engine}_web_run_sidecar"
        elif used_browser:
            executor = "tavily_python_web_run_sidecar"
        else:
            executor = "tavily_python"
        return {
            "executor": executor,
            "search_count": self.search_count,
            "open_count": self.open_count,
            "time_count": self.time_count,
            "search_provider": self.search_provider,
            "search_result_count": self.search_result_count,
            "tavily_result_count": self.tavily_result_count,
            "stored_reference_open_count": self.stored_reference_open_count,
            "search_reference_count": self.search_reference_count,
            "search_cache_hit": self.search_cache_hit,
            "click_count": self.click_count,
            "find_count": self.find_count,
            "screenshot_count": self.screenshot_count,
            "browser_open_count": self.browser_open_count,
            "output_chars": len(self.output),
        }


@dataclass(frozen=True)
class _SearchExecution:
    sections: tuple[str, ...]
    result_count: int
    reference_count: int
    cache_hit: bool


def should_use_local_codex_search(
    body: dict[str, Any],
    web_search_config: dict[str, Any] | None,
    *,
    native_passthrough_available: bool,
    browser_available: bool = False,
) -> bool:
    """Choose the local bridge without taking native pass-through away by default."""
    config = web_search_config if isinstance(web_search_config, dict) else {}
    provider = str(config.get("provider") or "tavily")
    if str(config.get("tavily_api_key") or "").strip() or (
        provider in SELF_HOSTED_WEB_SEARCH_PROVIDERS and browser_available
    ):
        return True
    commands = body.get("commands")
    if not native_passthrough_available:
        return isinstance(commands, dict)
    locally_executed = {"open", "time"}
    if browser_available:
        locally_executed.update({"click", "find", "screenshot"})
    return isinstance(commands, dict) and any(
        _has_value(commands.get(name)) for name in locally_executed
    )


async def execute_local_codex_search(
    body: dict[str, Any],
    web_search_config: dict[str, Any] | None,
    *,
    client: WebSearchClient | None = None,
    page_client: StaticPageClient | None = None,
    browser_client: WebRunBrowserClient | None = None,
    now: Callable[[], datetime] | None = None,
    reference_store: CodexSearchReferenceStore | None = None,
    principal_id: str | None = None,
) -> CodexSearchBridgeResult:
    """Execute the deterministic local subset of Codex ``SearchRequest``."""
    _validate_request_identity(body)
    commands = body.get("commands")
    if not isinstance(commands, dict):
        raise CodexSearchInvalidRequest("'commands' must be an object")

    supported_fields = web_run_supported_command_fields(
        search_available=True,
        browser_available=browser_client is not None,
    )
    unsupported = _unsupported_features(
        commands, body.get("settings"), supported_fields=supported_fields
    )
    if unsupported:
        joined = ", ".join(sorted(unsupported))
        raise CodexSearchNotImplemented(
            f"Codex search feature not implemented by the local bridge: {joined}"
        )

    base_settings = _resolve_settings(commands, body.get("settings"))
    queries = _parse_search_queries(commands.get("search_query"), base_settings)
    open_operations = _parse_open_operations(commands.get("open"))
    click_operations = _parse_click_operations(commands.get("click"))
    find_operations = _parse_find_operations(commands.get("find"))
    screenshot_operations = _parse_screenshot_operations(commands.get("screenshot"))
    time_offsets = _parse_time_queries(commands.get("time"))
    if not any(
        (
            queries,
            open_operations,
            click_operations,
            find_operations,
            screenshot_operations,
            time_offsets,
        )
    ):
        raise CodexSearchInvalidRequest(
            "'commands' must contain at least one supported operation"
        )

    config = web_search_config if isinstance(web_search_config, dict) else {}
    provider = str(config.get("provider") or "tavily")
    api_key = str(config.get("tavily_api_key") or "").strip()
    search_client = client
    if queries and search_client is None:
        if provider == "tavily":
            if not api_key:
                raise CodexSearchNotImplemented(
                    "Codex search_query requires a Tavily API key in Admin > Web Search"
                )
            search_client = TavilyHTTPClient(api_key)
        elif provider in SELF_HOSTED_WEB_SEARCH_PROVIDERS:
            if browser_client is None:
                label = _search_provider_label(provider)
                raise CodexSearchNotImplemented(
                    f"{label} search requires a healthy web-run sidecar"
                )
            search_client = cast(WebSearchClient, browser_client)
        else:
            raise CodexSearchNotImplemented(
                f"Unsupported local web search provider: {provider}"
            )
    resolved_page_client = page_client or (
        StaticPageHTTPClient() if open_operations and browser_client is None else None
    )
    scope = _reference_scope(body, principal_id, reference_store)
    browser_session_id = _browser_session_id(body, principal_id)

    search_execution = await _execute_search_queries(
        queries,
        body=body,
        search_client=search_client,
        search_provider=provider,
        reference_store=reference_store,
        scope=scope,
    )
    sections = list(search_execution.sections)
    open_sections, stored_reference_open_count = await _execute_open_operations(
        open_operations,
        page_client=resolved_page_client,
        browser_client=browser_client,
        browser_session_id=browser_session_id,
        reference_store=reference_store,
        scope=scope,
    )
    sections.extend(open_sections)
    sections.extend(
        await _execute_browser_operations(
            click_operations,
            operation="click",
            browser_client=browser_client,
            browser_session_id=browser_session_id,
            reference_store=reference_store,
            scope=scope,
        )
    )
    sections.extend(
        await _execute_browser_operations(
            find_operations,
            operation="find",
            browser_client=browser_client,
            browser_session_id=browser_session_id,
            reference_store=reference_store,
            scope=scope,
        )
    )
    sections.extend(
        await _execute_browser_operations(
            screenshot_operations,
            operation="screenshot",
            browser_client=browser_client,
            browser_session_id=browser_session_id,
            reference_store=reference_store,
            scope=scope,
        )
    )

    if time_offsets:
        clock = now or (lambda: datetime.now(timezone.utc))
        current = clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        lines = ["Times:"]
        for label, zone in time_offsets:
            lines.append(
                f"{label}: {current.astimezone(zone).isoformat(timespec='seconds')}"
            )
        sections.append("\n".join(lines))

    output = "\n\n".join(section for section in sections if section).strip()
    output = _apply_output_budget(output, body.get("max_output_tokens"))
    return CodexSearchBridgeResult(
        output=output,
        search_count=len(queries),
        open_count=len(open_operations),
        time_count=len(time_offsets),
        search_result_count=search_execution.result_count,
        search_provider=provider,
        stored_reference_open_count=stored_reference_open_count,
        search_reference_count=search_execution.reference_count,
        search_cache_hit=search_execution.cache_hit,
        click_count=len(click_operations),
        find_count=len(find_operations),
        screenshot_count=len(screenshot_operations),
        browser_open_count=len(open_operations) if browser_client is not None else 0,
    )


async def _execute_search_queries(
    queries: list[tuple[str, WebSearchSettings]],
    *,
    body: dict[str, Any],
    search_client: WebSearchClient | None,
    search_provider: str,
    reference_store: CodexSearchReferenceStore | None,
    scope: CodexSearchReferenceScope | None,
) -> _SearchExecution:
    if not queries:
        return _SearchExecution((), 0, 0, False)

    fingerprint = _search_request_fingerprint(body)
    if reference_store is not None and scope is not None:
        cached = reference_store.get_search(scope, fingerprint)
        if cached is not None:
            return _stored_search_execution(cached, cache_hit=True)

    query_drafts: list[SearchQueryDraft] = []
    for query, settings in queries:
        assert search_client is not None
        try:
            raw = await search_client.search(query, settings=settings)
        except Exception as exc:
            raise CodexSearchExecutionError(
                f"{_search_provider_label(search_provider)} search failed for "
                f"query {query!r}: {exc}"
            ) from exc
        query_drafts.append(_search_query_draft(query, raw))

    if reference_store is not None and scope is not None:
        batch, cache_hit = reference_store.remember_search(
            scope,
            fingerprint,
            tuple(query_drafts),
        )
        return _stored_search_execution(batch, cache_hit=cache_hit)

    return _SearchExecution(
        sections=tuple(
            format_web_search_result_for_model(
                draft.query,
                _draft_as_tavily_result(draft),
            )
            for draft in query_drafts
        ),
        result_count=sum(draft.source_result_count for draft in query_drafts),
        reference_count=0,
        cache_hit=False,
    )


def _search_provider_label(provider: str) -> str:
    if provider == "self_hosted_google":
        return "Self-hosted Google"
    if provider == "self_hosted_bing":
        return "Self-hosted Bing RSS"
    if provider == "self_hosted_bing_browser":
        return "Self-hosted Bing Browser"
    if provider == "tavily":
        return "Tavily"
    return provider or "Web"


def _stored_search_execution(
    batch: StoredSearchBatch,
    *,
    cache_hit: bool,
) -> _SearchExecution:
    return _SearchExecution(
        sections=tuple(_format_stored_search_batch(batch)),
        result_count=sum(query.source_result_count for query in batch.queries),
        reference_count=sum(
            result.ref_id is not None
            for query in batch.queries
            for result in query.results
        ),
        cache_hit=cache_hit,
    )


async def _execute_open_operations(
    open_operations: list[tuple[str, int | None]],
    *,
    page_client: StaticPageClient | None,
    browser_client: WebRunBrowserClient | None,
    browser_session_id: str,
    reference_store: CodexSearchReferenceStore | None,
    scope: CodexSearchReferenceScope | None,
) -> tuple[list[str], int]:
    sections: list[str] = []
    stored_reference_count = 0
    for ref_id, lineno in open_operations:
        url = _resolve_browser_reference(
            ref_id, scope=scope, reference_store=reference_store
        )
        stored_reference_count += url != ref_id
        if browser_client is not None:
            arguments: dict[str, Any] = {"ref_id": url}
            if lineno is not None:
                arguments["lineno"] = lineno
            sections.append(
                await _execute_browser_operation(
                    browser_client,
                    browser_session_id,
                    "open",
                    arguments,
                )
            )
            continue
        assert page_client is not None
        try:
            page = await page_client.open(url)
            sections.append(page.format_for_model(lineno=lineno))
        except PageOpenInvalidRequest as exc:
            raise CodexSearchInvalidRequest(str(exc)) from exc
        except PageOpenNotImplemented as exc:
            raise CodexSearchNotImplemented(str(exc)) from exc
        except PageOpenExecutionError as exc:
            raise CodexSearchExecutionError(str(exc)) from exc
    return sections, stored_reference_count


async def _execute_browser_operations(
    operations: list[dict[str, Any]],
    *,
    operation: str,
    browser_client: WebRunBrowserClient | None,
    browser_session_id: str,
    reference_store: CodexSearchReferenceStore | None,
    scope: CodexSearchReferenceScope | None,
) -> list[str]:
    if not operations:
        return []
    if browser_client is None:
        raise CodexSearchNotImplemented(
            f"Codex search feature requires the optional web-run sidecar: commands.{operation}"
        )
    sections: list[str] = []
    for item in operations:
        arguments = dict(item)
        arguments["ref_id"] = _resolve_browser_reference(
            str(item["ref_id"]), scope=scope, reference_store=reference_store
        )
        sections.append(
            await _execute_browser_operation(
                browser_client,
                browser_session_id,
                operation,
                arguments,
            )
        )
    return sections


async def _execute_browser_operation(
    client: WebRunBrowserClient,
    session_id: str,
    operation: str,
    arguments: dict[str, Any],
) -> str:
    try:
        return await client.execute(
            session_id=session_id,
            operation=operation,
            arguments=arguments,
        )
    except WebRunSidecarInvalidRequest as exc:
        raise CodexSearchInvalidRequest(str(exc)) from exc
    except WebRunSidecarNotImplemented as exc:
        raise CodexSearchNotImplemented(str(exc)) from exc
    except WebRunSidecarError as exc:
        raise CodexSearchExecutionError(str(exc)) from exc


def codex_search_request_summary(body: dict[str, Any]) -> dict[str, Any]:
    """Return a prompt-free, credential-free Gateway Logs summary."""
    commands = body.get("commands")
    settings = body.get("settings")
    return {
        "search_session_id_present": isinstance(body.get("id"), str)
        and bool(body.get("id")),
        "command_types": sorted(
            key
            for key, value in commands.items()
            if isinstance(commands, dict) and _has_value(value)
        )
        if isinstance(commands, dict)
        else [],
        "setting_types": sorted(settings) if isinstance(settings, dict) else [],
    }


def _validate_request_identity(body: dict[str, Any]) -> None:
    session_id = body.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise CodexSearchInvalidRequest("Missing or invalid search request 'id'")
    if len(session_id) > 256:
        raise CodexSearchInvalidRequest("Search request 'id' exceeds 256 characters")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise CodexSearchInvalidRequest("Missing or invalid search request 'model'")


def _unsupported_features(
    commands: dict[str, Any],
    settings: Any,
    *,
    supported_fields: dict[str, frozenset[str] | None],
) -> set[str]:
    return _unsupported_command_features(
        commands, supported_fields=supported_fields
    ) | _unsupported_setting_features(settings)


def _unsupported_command_features(
    commands: dict[str, Any],
    *,
    supported_fields: dict[str, frozenset[str] | None],
) -> set[str]:
    supported_commands = frozenset(supported_fields)
    unsupported = {
        f"commands.{key}"
        for key, value in commands.items()
        if key not in supported_commands and _has_value(value)
    }
    for key in WEB_RUN_KNOWN_COMMANDS - supported_commands:
        if _has_value(commands.get(key)):
            unsupported.add(f"commands.{key}")

    for command in supported_commands:
        unsupported.update(
            _unsupported_array_item_features(
                command,
                commands.get(command),
                supported_fields=supported_fields,
            )
        )
    return unsupported


def _unsupported_array_item_features(
    command: str,
    items: Any,
    *,
    supported_fields: dict[str, frozenset[str] | None],
) -> set[str]:
    unsupported: set[str] = set()
    if not isinstance(items, list):
        return unsupported
    allowed = supported_fields.get(command)
    if not isinstance(allowed, frozenset):
        return {f"commands.{command}"} if _has_value(items) else set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key not in allowed and _has_value(value):
                unsupported.add(f"commands.{command}[].{key}")
    return unsupported


def _unsupported_setting_features(settings: Any) -> set[str]:
    unsupported: set[str] = set()
    if settings is None:
        return unsupported
    if not isinstance(settings, dict):
        return unsupported
    for key, value in settings.items():
        if key not in _SUPPORTED_SETTINGS and _has_value(value):
            unsupported.add(f"settings.{key}")

    filters = settings.get("filters")
    if isinstance(filters, dict):
        if _has_value(filters.get("blocked_domains")):
            unsupported.add("settings.filters.blocked_domains")
        for key, value in filters.items():
            if key not in {"allowed_domains", "blocked_domains"} and _has_value(value):
                unsupported.add(f"settings.filters.{key}")

    external_access = settings.get("external_web_access")
    if external_access not in (None, True, "live"):
        unsupported.add("settings.external_web_access")
    return unsupported


def _resolve_settings(commands: dict[str, Any], settings: Any) -> WebSearchSettings:
    if settings is not None and not isinstance(settings, dict):
        raise CodexSearchInvalidRequest("'settings' must be an object")
    settings = settings or {}
    context_size = settings.get("search_context_size")
    presets = {
        None: WebSearchSettings(),
        "low": WebSearchSettings(max_results=3, search_depth="basic"),
        "medium": WebSearchSettings(max_results=5, search_depth="basic"),
        "high": WebSearchSettings(max_results=8, search_depth="advanced"),
    }
    if context_size not in presets:
        raise CodexSearchInvalidRequest(
            "'settings.search_context_size' must be low, medium, or high"
        )
    resolved = presets[context_size]

    response_length = commands.get("response_length")
    response_presets = {
        None: None,
        "short": (3, "basic"),
        "medium": (5, "basic"),
        "long": (8, "advanced"),
    }
    if response_length not in response_presets:
        raise CodexSearchInvalidRequest(
            "'commands.response_length' must be short, medium, or long"
        )
    response_preset = response_presets[response_length]
    if response_preset is not None:
        resolved = replace(
            resolved,
            max_results=response_preset[0],
            search_depth=response_preset[1],
        )

    filters = settings.get("filters")
    allowed_domains: tuple[str, ...] = ()
    if filters is not None:
        if not isinstance(filters, dict):
            raise CodexSearchInvalidRequest("'settings.filters' must be an object")
        allowed_domains = _parse_domains(
            filters.get("allowed_domains"), "settings.filters.allowed_domains"
        )
    return replace(resolved, include_domains=allowed_domains)


def _parse_search_queries(
    value: Any,
    base_settings: WebSearchSettings,
) -> list[tuple[str, WebSearchSettings]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CodexSearchInvalidRequest("'commands.search_query' must be an array")
    if len(value) > _MAX_SEARCH_QUERIES:
        raise CodexSearchInvalidRequest(
            f"'commands.search_query' supports at most {_MAX_SEARCH_QUERIES} entries"
        )
    parsed: list[tuple[str, WebSearchSettings]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise CodexSearchInvalidRequest(
                f"'commands.search_query[{index}]' must be an object"
            )
        query = item.get("q")
        if not isinstance(query, str) or not query.strip():
            raise CodexSearchInvalidRequest(
                f"'commands.search_query[{index}].q' must be a non-empty string"
            )
        query = query.strip()
        if len(query) > _MAX_QUERY_CHARS:
            raise CodexSearchInvalidRequest(
                f"'commands.search_query[{index}].q' exceeds {_MAX_QUERY_CHARS} characters"
            )
        query_domains = _parse_domains(
            item.get("domains"), f"commands.search_query[{index}].domains"
        )
        domains = _intersect_domains(base_settings.include_domains, query_domains)
        parsed.append((query, replace(base_settings, include_domains=domains)))
    return parsed


def _parse_open_operations(value: Any) -> list[tuple[str, int | None]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CodexSearchInvalidRequest("'commands.open' must be an array")
    if len(value) > 4:
        raise CodexSearchInvalidRequest("'commands.open' supports at most 4 entries")
    parsed: list[tuple[str, int | None]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise CodexSearchInvalidRequest(
                f"'commands.open[{index}]' must be an object"
            )
        ref_id = item.get("ref_id")
        if not isinstance(ref_id, str) or not ref_id.strip():
            raise CodexSearchInvalidRequest(
                f"'commands.open[{index}].ref_id' must be a non-empty string"
            )
        lineno = item.get("lineno")
        if lineno is not None and (
            isinstance(lineno, bool) or not isinstance(lineno, int) or lineno < 0
        ):
            raise CodexSearchInvalidRequest(
                f"'commands.open[{index}].lineno' must be a non-negative integer"
            )
        parsed.append((ref_id.strip(), lineno))
    return parsed


def _parse_click_operations(value: Any) -> list[dict[str, Any]]:
    return _parse_integer_reference_operations("click", value, value_field="id")


def _parse_find_operations(value: Any) -> list[dict[str, Any]]:
    items = _reference_operation_items("find", value)
    parsed: list[dict[str, Any]] = []
    for index, item, ref_id in items:
        pattern = item.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise CodexSearchInvalidRequest(
                f"'commands.find[{index}].pattern' must be a non-empty string"
            )
        parsed.append({"ref_id": ref_id, "pattern": pattern})
    return parsed


def _parse_screenshot_operations(value: Any) -> list[dict[str, Any]]:
    return _parse_integer_reference_operations(
        "screenshot", value, value_field="pageno"
    )


def _parse_integer_reference_operations(
    command: str,
    value: Any,
    *,
    value_field: str,
) -> list[dict[str, Any]]:
    items = _reference_operation_items(command, value)
    parsed: list[dict[str, Any]] = []
    for index, item, ref_id in items:
        field_value = item.get(value_field)
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or field_value < 0
        ):
            raise CodexSearchInvalidRequest(
                f"'commands.{command}[{index}].{value_field}' must be a "
                "non-negative integer"
            )
        parsed.append({"ref_id": ref_id, value_field: field_value})
    return parsed


def _reference_operation_items(
    command: str,
    value: Any,
) -> list[tuple[int, dict[str, Any], str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CodexSearchInvalidRequest(f"'commands.{command}' must be an array")
    if len(value) > 4:
        raise CodexSearchInvalidRequest(
            f"'commands.{command}' supports at most 4 entries"
        )
    parsed: list[tuple[int, dict[str, Any], str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise CodexSearchInvalidRequest(
                f"'commands.{command}[{index}]' must be an object"
            )
        ref_id = item.get("ref_id")
        if not isinstance(ref_id, str) or not ref_id.strip():
            raise CodexSearchInvalidRequest(
                f"'commands.{command}[{index}].ref_id' must be a non-empty string"
            )
        parsed.append((index, cast(dict[str, Any], item), ref_id.strip()))
    return parsed


def _reference_scope(
    body: dict[str, Any],
    principal_id: str | None,
    reference_store: CodexSearchReferenceStore | None,
) -> CodexSearchReferenceScope | None:
    if reference_store is None:
        return None
    if not isinstance(principal_id, str) or not principal_id:
        raise CodexSearchInvalidRequest(
            "Authenticated principal is required for stored search references"
        )
    return CodexSearchReferenceScope(
        principal_id=principal_id,
        session_id=str(body["id"]),
    )


def _search_request_fingerprint(body: dict[str, Any]) -> str:
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _search_query_draft(query: str, raw: dict[str, Any]) -> SearchQueryDraft:
    answer = raw.get("answer")
    answer = (
        _trim_search_text(answer.strip(), 4_000)
        if isinstance(answer, str) and answer.strip()
        else None
    )
    raw_results = raw.get("results")
    source_result_count = len(raw_results) if isinstance(raw_results, list) else 0
    results: list[SearchResultDraft] = []
    if isinstance(raw_results, list):
        for result in raw_results[:10]:
            if not isinstance(result, dict):
                continue
            score = result.get("score")
            results.append(
                SearchResultDraft(
                    title=_trim_search_text(
                        str(result.get("title") or "Untitled").strip(), 500
                    ),
                    url=_trim_search_text(str(result.get("url") or "").strip(), 8_192),
                    content=_trim_search_text(
                        str(result.get("content") or "").strip(), 1_200
                    ),
                    score=score if isinstance(score, int | float) else None,
                )
            )
    return SearchQueryDraft(query, answer, tuple(results), source_result_count)


def _draft_as_tavily_result(draft: SearchQueryDraft) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "results": [
            {
                "title": result.title,
                "url": result.url,
                "content": result.content,
                **({"score": result.score} if result.score is not None else {}),
            }
            for result in draft.results
        ]
    }
    if draft.answer is not None:
        raw["answer"] = draft.answer
    return raw


def _format_stored_search_batch(batch: StoredSearchBatch) -> list[str]:
    sections: list[str] = []
    for query in batch.queries:
        lines = [f"Web search query: {query.query}"]
        if query.answer:
            lines.extend(["", f"Answer summary: {query.answer}"])
        if not query.results:
            lines.extend(["", "No web search results were returned."])
            sections.append("\n".join(lines))
            continue
        lines.extend(["", "Sources:"])
        for index, result in enumerate(query.results, start=1):
            label = result.ref_id or str(index)
            lines.append(f"[{label}] {result.title}")
            if result.url:
                lines.append(f"URL: {result.url}")
            if result.content:
                lines.append(f"Content: {_trim_search_text(result.content, 1200)}")
            if result.score is not None:
                lines.append(f"Score: {result.score}")
            lines.append("")
        sections.append("\n".join(lines).rstrip())
    return sections


def _resolve_browser_reference(
    ref_id: str,
    *,
    scope: CodexSearchReferenceScope | None,
    reference_store: CodexSearchReferenceStore | None,
) -> str:
    if ref_id.lower().startswith(("http://", "https://")):
        return ref_id
    if SEARCH_REFERENCE_RE.fullmatch(ref_id) is None:
        return ref_id
    url = (
        reference_store.resolve(scope, ref_id)
        if reference_store is not None and scope is not None
        else None
    )
    if url is None:
        raise CodexSearchInvalidRequest(f"Unknown search reference: {ref_id}")
    return url


def _browser_session_id(body: dict[str, Any], principal_id: str | None) -> str:
    principal = principal_id if isinstance(principal_id, str) else ""
    material = f"{principal}\0{body['id']}".encode()
    return hashlib.sha256(material).hexdigest()


def _trim_search_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _parse_domains(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CodexSearchInvalidRequest(f"'{field}' must be an array")
    domains: list[str] = []
    for index, domain in enumerate(value):
        if not isinstance(domain, str) or not domain.strip():
            raise CodexSearchInvalidRequest(
                f"'{field}[{index}]' must be a non-empty string"
            )
        normalized = (
            domain.strip().lower().removeprefix("https://").removeprefix("http://")
        )
        normalized = normalized.split("/", 1)[0].lstrip(".")
        if not normalized or any(char.isspace() for char in normalized):
            raise CodexSearchInvalidRequest(f"'{field}[{index}]' is not a domain")
        if normalized not in domains:
            domains.append(normalized)
    return tuple(domains)


def _intersect_domains(
    global_domains: tuple[str, ...],
    query_domains: tuple[str, ...],
) -> tuple[str, ...]:
    if not global_domains:
        return query_domains
    if not query_domains:
        return global_domains
    intersection = tuple(
        domain for domain in query_domains if domain in set(global_domains)
    )
    if not intersection:
        raise CodexSearchInvalidRequest(
            "Query domains do not overlap settings.filters.allowed_domains"
        )
    return intersection


def _parse_time_queries(value: Any) -> list[tuple[str, timezone]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CodexSearchInvalidRequest("'commands.time' must be an array")
    if len(value) > _MAX_TIME_QUERIES:
        raise CodexSearchInvalidRequest(
            f"'commands.time' supports at most {_MAX_TIME_QUERIES} entries"
        )
    parsed: list[tuple[str, timezone]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise CodexSearchInvalidRequest(
                f"'commands.time[{index}]' must be an object"
            )
        offset = item.get("utc_offset")
        if not isinstance(offset, str) or len(offset) != 6:
            raise CodexSearchInvalidRequest(
                f"'commands.time[{index}].utc_offset' must match +HH:MM or -HH:MM"
            )
        sign = 1 if offset[0] == "+" else -1 if offset[0] == "-" else 0
        try:
            hours = int(offset[1:3])
            minutes = int(offset[4:6])
        except ValueError:
            sign = 0
            hours = minutes = 0
        if offset[3] != ":" or not sign or minutes > 59 or hours > 14:
            raise CodexSearchInvalidRequest(
                f"'commands.time[{index}].utc_offset' must be a valid UTC offset"
            )
        if hours == 14 and minutes:
            raise CodexSearchInvalidRequest(
                f"'commands.time[{index}].utc_offset' exceeds the supported UTC range"
            )
        delta = timedelta(hours=hours, minutes=minutes) * sign
        parsed.append((offset, timezone(delta, name=f"UTC{offset}")))
    return parsed


def _apply_output_budget(output: str, value: Any) -> str:
    if value is None:
        return output[:_MAX_OUTPUT_CHARS]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CodexSearchInvalidRequest(
            "'max_output_tokens' must be a positive integer"
        )
    # One Unicode code point per requested token is deliberately conservative;
    # this is a hard output cap, not a claim to reproduce Codex's tokenizer.
    max_chars = min(_MAX_OUTPUT_CHARS, value)
    if len(output) <= max_chars:
        return output
    marker = "\n...[output truncated by Rosetta]"
    if max_chars <= len(marker):
        return output[:max_chars]
    return output[: max_chars - len(marker)].rstrip() + marker


def _has_value(value: Any) -> bool:
    return value not in (None, [], {}, "")
