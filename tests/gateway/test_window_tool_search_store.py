"""Window isolation tests for deferred Codex tool discovery."""

from __future__ import annotations

from typing import Any

from codex_rosetta.gateway.proxy import WindowToolSearchStore


def _namespace(name: str, tool_name: str, description: str) -> dict[str, Any]:
    return {
        "type": "namespace",
        "name": name,
        "description": f"Tools for {name}",
        "tools": [
            {
                "type": "function",
                "name": tool_name,
                "description": description,
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    }


def _search_body(query: str) -> dict[str, Any]:
    return {
        "input": [
            {
                "type": "tool_search_call",
                "id": "tsc_1",
                "call_id": "call_search_1",
                "execution": "client",
                "arguments": {"query": query, "limit": 8},
            },
            {
                "type": "tool_search_output",
                "call_id": "call_search_1",
                "tools": [],
            },
        ]
    }


def _result_tools(body: dict[str, Any]) -> list[dict[str, Any]]:
    return body["input"][1]["tools"]


def test_deferred_tool_search_is_isolated_between_codex_windows():
    store = WindowToolSearchStore()
    github = _namespace("github", "list_pull_requests", "List GitHub pull requests")
    gmail = _namespace("gmail", "search_mail", "Search Gmail messages")
    store.remember_deferred_tools("thread-a:0", [github])
    store.remember_deferred_tools("thread-b:0", [gmail])

    a_github = _search_body("github pull requests")
    a_gmail = _search_body("gmail messages")
    b_github = _search_body("github pull requests")
    b_gmail = _search_body("gmail messages")

    store.enrich_tool_search_outputs("thread-a:0", a_github)
    store.enrich_tool_search_outputs("thread-a:0", a_gmail)
    store.enrich_tool_search_outputs("thread-b:0", b_github)
    store.enrich_tool_search_outputs("thread-b:0", b_gmail)

    assert _result_tools(a_github)[0]["name"] == "github"
    assert _result_tools(a_gmail) == []
    assert _result_tools(b_github) == []
    assert _result_tools(b_gmail)[0]["name"] == "gmail"
