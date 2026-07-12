"""Gateway-local web search bridge for Responses-to-Chat Codex traffic."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Protocol

from codex_rosetta._vendor.httpclient import AsyncClient

from .transport.http.transport import request_bounded_response

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
WEB_SEARCH_TOOL_NAMES = {"web_search", "web_search_preview"}
WEB_SEARCH_PROFILE_ITEM_ID = "hosted.web_search"
WEB_RUN_PROFILE_ITEM_ID = "namespace.web.run"


def profile_search_config(route: Any, item_id: str) -> dict[str, str]:
    """Resolve one search tool's provider settings from its selected Profile."""
    values = route.tool_profile_inputs.get(item_id, {})
    provider = str(values.get("provider") or "").strip()
    token = str(values.get("token") or "").strip()
    if provider != "tavily":
        return {}
    return {"provider": provider, "tavily_api_key": token}


@dataclass(frozen=True)
class WebSearchSettings:
    """Resolved settings from the Responses web_search tool definition."""

    max_results: int = 5
    search_depth: str = "basic"
    include_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingWebSearchCall:
    """A Chat tool call that Rosetta must satisfy through Tavily."""

    call_id: str
    query: str
    item: dict[str, Any]


@dataclass(frozen=True)
class WebSearchExecutionResult:
    """A completed web search result ready to feed back to Chat."""

    call: PendingWebSearchCall
    model_text: str
    status: str = "completed"
    raw: dict[str, Any] | None = None
    error: str | None = None


class TavilySearchClient(Protocol):
    """Minimal protocol used by the gateway web-search runtime."""

    async def search(
        self,
        query: str,
        *,
        settings: WebSearchSettings,
    ) -> dict[str, Any]:
        """Run a Tavily search and return the parsed JSON body."""


class TavilyHTTPClient:
    """Small async Tavily REST client using the vendored HTTP transport."""

    def __init__(self, api_key: str, *, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def search(
        self,
        query: str,
        *,
        settings: WebSearchSettings,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "search_depth": settings.search_depth,
            "max_results": settings.max_results,
            "include_answer": True,
            "include_raw_content": False,
            "include_images": False,
        }
        if settings.include_domains:
            payload["include_domains"] = list(settings.include_domains)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with AsyncClient(timeout=self.timeout) as client:
            try:
                response = await request_bounded_response(
                    client,
                    "POST",
                    TAVILY_SEARCH_URL,
                    json=payload,
                    headers=headers,
                )
            except Exception as exc:
                raise RuntimeError(f"Tavily request failed: {exc}") from exc

        if response.status_code >= 400:
            body = response.text[:500]
            raise RuntimeError(f"Tavily returned HTTP {response.status_code}: {body}")
        try:
            parsed = response.json()
        except Exception as exc:
            raise RuntimeError("Tavily returned invalid JSON") from exc
        return parsed if isinstance(parsed, dict) else {"result": parsed}


class WebSearchRuntime:
    """Executes web searches and appends tool results to Chat requests."""

    def __init__(
        self,
        *,
        client: TavilySearchClient,
        settings: WebSearchSettings,
    ) -> None:
        self.client = client
        self.settings = settings

    async def execute(self, call: PendingWebSearchCall) -> WebSearchExecutionResult:
        try:
            raw = await self.client.search(call.query, settings=self.settings)
        except Exception as exc:
            error = str(exc)
            return WebSearchExecutionResult(
                call=call,
                status="failed",
                error=error,
                model_text=(
                    f"Web search failed for query {call.query!r}: {error}. "
                    "Explain that the search could not be completed if this "
                    "prevents answering."
                ),
            )
        return WebSearchExecutionResult(
            call=call,
            raw=raw,
            model_text=format_tavily_result_for_model(call.query, raw),
        )

    async def execute_many(
        self, calls: list[PendingWebSearchCall]
    ) -> list[WebSearchExecutionResult]:
        results: list[WebSearchExecutionResult] = []
        for call in calls:
            results.append(await self.execute(call))
        return results

    @staticmethod
    def append_tool_results(
        chat_body: dict[str, Any],
        results: list[WebSearchExecutionResult],
    ) -> dict[str, Any]:
        """Return a Chat request body with web-search tool results appended."""
        updated = copy.deepcopy(chat_body)
        messages = list(updated.get("messages") or [])
        tool_calls: list[dict[str, Any]] = []
        for result in results:
            arguments = json.dumps(
                {"query": result.call.query},
                ensure_ascii=False,
            )
            tool_calls.append(
                {
                    "id": result.call.call_id,
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": arguments,
                    },
                }
            )
        if tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                }
            )
            for result in results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.call.call_id,
                        "content": result.model_text,
                    }
                )
        updated["messages"] = messages
        if _tool_choice_requests_web_search(updated.get("tool_choice")):
            updated["tool_choice"] = "auto"
        return updated


class WebSearchStreamController:
    """Tracks synthetic web_search_call events during a multi-request stream."""

    def __init__(self) -> None:
        self.response_id: str | None = None
        self.completed_items: list[dict[str, Any]] = []
        self._pending_calls: list[PendingWebSearchCall] = []
        self._item_output_indexes: dict[str, int] = {}

    def process_source_event(
        self,
        event: dict[str, Any],
        *,
        round_index: int,
    ) -> list[dict[str, Any]]:
        event = copy.deepcopy(event)
        event_type = event.get("type")

        if event_type == "response.created":
            response = event.get("response")
            if self.response_id is None and isinstance(response, dict):
                response_id = response.get("id")
                if isinstance(response_id, str) and response_id:
                    self.response_id = response_id
                return [event]
            return []

        item = event.get("item") if isinstance(event, dict) else None
        if event_type == "response.output_item.added" and _is_web_search_item(item):
            self._set_web_search_output_index(event, item)
            return [event]

        if event_type == "response.output_item.done" and _is_web_search_item(item):
            assert isinstance(item, dict)
            self._set_web_search_output_index(event, item)
            query = _web_search_item_query(item)
            item_id = str(item.get("id") or f"web_search_{len(self.completed_items)}")
            self._pending_calls.append(
                PendingWebSearchCall(call_id=item_id, query=query, item=dict(item))
            )
            return []

        if event_type == "response.completed":
            if self._pending_calls:
                return []
            self._patch_completed_response(event)
            return [event]

        if round_index > 0:
            self._shift_output_index(event)
        return [event]

    def pop_pending_calls(self) -> list[PendingWebSearchCall]:
        calls = self._pending_calls
        self._pending_calls = []
        return calls

    def complete_search_results(
        self,
        results: list[WebSearchExecutionResult],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for result in results:
            item = dict(result.call.item)
            item["status"] = result.status
            if result.call.query:
                item["action"] = {"type": "search", "query": result.call.query}
            self.completed_items.append(item)
            events.append(
                {
                    "type": "response.output_item.done",
                    "output_index": self._item_output_indexes.get(
                        result.call.call_id,
                        len(self.completed_items) - 1,
                    ),
                    "item": item,
                }
            )
        return events

    def _set_web_search_output_index(self, event: dict[str, Any], item: Any) -> None:
        if not isinstance(item, dict):
            return
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            return
        output_index = self._item_output_indexes.setdefault(
            item_id,
            len(self.completed_items) + len(self._pending_calls),
        )
        event["output_index"] = output_index

    def _shift_output_index(self, event: dict[str, Any]) -> None:
        offset = len(self.completed_items)
        if not offset:
            return
        output_index = event.get("output_index")
        if isinstance(output_index, int):
            event["output_index"] = output_index + offset

    def _patch_completed_response(self, event: dict[str, Any]) -> None:
        response = event.get("response")
        if not isinstance(response, dict):
            return
        if self.response_id:
            response["id"] = self.response_id
        if not self.completed_items:
            return
        output = response.get("output")
        if not isinstance(output, list):
            response["output"] = [dict(item) for item in self.completed_items]
            return
        existing_ids = {
            item.get("id")
            for item in output
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        prefix = [
            dict(item)
            for item in self.completed_items
            if item.get("id") not in existing_ids
        ]
        if prefix:
            response["output"] = prefix + output


def build_web_search_runtime(
    source_body: dict[str, Any],
    config: dict[str, Any] | None,
    *,
    client: TavilySearchClient | None = None,
) -> WebSearchRuntime | None:
    """Build a runtime when the request exposes Codex web_search and Tavily is set."""
    if not has_responses_web_search_tool(source_body):
        return None
    config = config if isinstance(config, dict) else {}
    api_key = str(config.get("tavily_api_key") or "").strip()
    if client is None and not api_key:
        return None
    return WebSearchRuntime(
        client=client or TavilyHTTPClient(api_key),
        settings=extract_web_search_settings(source_body),
    )


def strip_responses_web_search_tools(body: dict[str, Any]) -> dict[str, Any]:
    """Return a request body without Responses hosted web search tools."""
    tools = body.get("tools")
    if not isinstance(tools, list) or not any(
        _is_responses_web_search_tool(tool) for tool in tools
    ):
        return body

    updated = copy.deepcopy(body)
    next_tools = [
        tool
        for tool in updated.get("tools", [])
        if not _is_responses_web_search_tool(tool)
    ]
    if next_tools:
        updated["tools"] = next_tools
    else:
        updated.pop("tools", None)

    if _tool_choice_requests_web_search(updated.get("tool_choice")):
        updated.pop("tool_choice", None)
    return updated


def has_responses_web_search_tool(body: dict[str, Any]) -> bool:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return False
    return any(_is_responses_web_search_tool(tool) for tool in tools)


def extract_web_search_settings(body: dict[str, Any]) -> WebSearchSettings:
    tool = _first_web_search_tool(body)
    if not isinstance(tool, dict):
        return WebSearchSettings()

    context_size = str(tool.get("search_context_size") or "").lower()
    if context_size == "high":
        max_results = 8
        search_depth = "advanced"
    elif context_size == "low":
        max_results = 3
        search_depth = "basic"
    else:
        max_results = 5
        search_depth = "basic"

    filters = tool.get("filters")
    allowed_domains = ()
    if isinstance(filters, dict):
        domains = filters.get("allowed_domains")
        if isinstance(domains, list):
            allowed_domains = tuple(
                domain
                for domain in domains
                if isinstance(domain, str) and domain.strip()
            )

    return WebSearchSettings(
        max_results=max_results,
        search_depth=search_depth,
        include_domains=allowed_domains,
    )


def format_tavily_result_for_model(query: str, raw: dict[str, Any]) -> str:
    """Render Tavily JSON into concise model-visible tool output."""
    lines = [f"Web search query: {query}"]
    answer = raw.get("answer")
    if isinstance(answer, str) and answer.strip():
        lines.extend(["", f"Answer summary: {answer.strip()}"])

    results = raw.get("results")
    if not isinstance(results, list) or not results:
        lines.extend(["", "No web search results were returned."])
        return "\n".join(lines)

    lines.append("")
    lines.append("Sources:")
    for index, result in enumerate(results[:10], start=1):
        if not isinstance(result, dict):
            continue
        title = str(result.get("title") or "Untitled").strip()
        url = str(result.get("url") or "").strip()
        content = str(result.get("content") or "").strip()
        lines.append(f"[{index}] {title}")
        if url:
            lines.append(f"URL: {url}")
        if content:
            lines.append(f"Content: {_trim_text(content, 1200)}")
        score = result.get("score")
        if isinstance(score, int | float):
            lines.append(f"Score: {score}")
        lines.append("")
    return "\n".join(lines).rstrip()


def web_search_trace_summary(result: WebSearchExecutionResult) -> dict[str, Any]:
    """Return a trace-safe summary of one web search execution."""
    raw = result.raw or {}
    results = raw.get("results")
    return {
        "query": result.call.query,
        "status": result.status,
        "error": result.error,
        "answer_present": isinstance(raw.get("answer"), str)
        and bool(raw.get("answer")),
        "result_count": len(results) if isinstance(results, list) else 0,
        "request_id": raw.get("request_id"),
        "response_time": raw.get("response_time"),
    }


def _first_web_search_tool(body: dict[str, Any]) -> dict[str, Any] | None:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if _is_responses_web_search_tool(tool):
            return tool
    return None


def _is_responses_web_search_tool(tool: Any) -> bool:
    return isinstance(tool, dict) and tool.get("type") in WEB_SEARCH_TOOL_NAMES


def _is_web_search_item(item: Any) -> bool:
    return isinstance(item, dict) and item.get("type") == "web_search_call"


def _web_search_item_query(item: dict[str, Any]) -> str:
    action = item.get("action")
    if isinstance(action, dict):
        query = action.get("query")
        if isinstance(query, str):
            return query
        queries = action.get("queries")
        if isinstance(queries, list):
            first = next((q for q in queries if isinstance(q, str) and q), "")
            if first:
                return first
    return ""


def _tool_choice_requests_web_search(tool_choice: Any) -> bool:
    if isinstance(tool_choice, str):
        return tool_choice in WEB_SEARCH_TOOL_NAMES
    if not isinstance(tool_choice, dict):
        return False
    if tool_choice.get("name") in WEB_SEARCH_TOOL_NAMES:
        return True
    function = tool_choice.get("function")
    return isinstance(function, dict) and function.get("name") in WEB_SEARCH_TOOL_NAMES


def _trim_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."
