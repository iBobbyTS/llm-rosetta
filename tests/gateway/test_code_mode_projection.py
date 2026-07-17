"""Tests for projecting selected Code Mode exec tools into Chat functions."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from codex_rosetta.gateway.code_mode_projection import (
    ExecToolProjection,
    build_exec_script,
    exec_tool_section_names,
    exec_tool_projections_for_route,
    plan_exec_tool_definitions,
    prune_exec_tool_description,
    project_exec_tool_definitions,
    project_modified_exec_web_run_description,
)
from codex_rosetta.gateway.tool_adaptation import (
    CodexToolLocalizationStore,
    EXEC_PROJECTIONS_KEY,
    LocalizedToolCallStreamTransformer,
    NativeToolCapabilities,
    localize_code_editing_chat_request,
    localized_mapping_from_tool_calls,
    translate_localized_tool_call_part,
)
from codex_rosetta.gateway.proxy import ProviderMetadataStore, handle_non_streaming
from codex_rosetta.gateway.state_scope import GatewayStateScope
from codex_rosetta.gateway.transport._base import UpstreamResponse
from codex_rosetta.gateway.tool_profiles import tool_profile_contract
from codex_rosetta.gateway.web_run_capabilities import (
    WEB_RUN_BASIC_SEARCH_CAPABILITY,
    WEB_RUN_SIDECAR_CAPABILITY,
)
from codex_rosetta.observability.persistence import PersistenceManager
from codex_rosetta.routing import ResolvedRoute


def _route(
    *,
    tool_runtime_capabilities: frozenset[str] = frozenset(),
) -> ResolvedRoute:
    contract = tool_profile_contract()
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="test",
        upstream_model="deepseek-v4-flash",
        tool_profile_name="builtin",
        tool_profile=dict(contract["builtin"]),
        tool_profile_inputs={
            item_id: dict(values)
            for item_id, values in contract["readonly"]["builtin"]["inputs"].items()
        },
        tool_runtime_capabilities=tool_runtime_capabilities,
    )


def _section(name: str, input_name: str, input_type: str) -> str:
    return f"""### `{name}`
Description for {name}.

exec tool declaration:
```ts
declare const tools: {{ {name}({input_name}: {input_type}): Promise<unknown>; }};
```"""


def _web_run_section() -> str:
    return """### `web__run`
Tool for accessing the internet.
* `search_query`: Search the internet (and optionally with a domain or recency filter).
* `image_query`: Search for images.
* `open`: Open a result or URL.
* `click`: Open a numbered link.
* `find`: Find text in a page.
* `screenshot`: Capture a PDF page.
* `finance`: Look up market prices.
* `weather`: Look up forecasts.
* `sports`: Look up schedules.
* `time`: Look up the time.
* Combine {"finance": [...]} and {"find": [...]} when useful.
* If called accidentally, send an empty query.

## Decision boundary
Browse whenever current information is required.

exec tool declaration:
```ts
declare const tools: { web__run(args: {
  search_query?: Array<{ q: string; recency?: number; domains?: Array<string>; }>;
  image_query?: Array<{ q: string; recency?: number; domains?: Array<string>; }>;
  open?: Array<{ ref_id: string; lineno?: number; }>;
  click?: Array<{ ref_id: string; id: number; }>;
  find?: Array<{ ref_id: string; pattern: string; }>;
  screenshot?: Array<{ ref_id: string; pageno: number; }>;
  finance?: Array<{ ticker: string; type: string; market?: string; }>;
  weather?: Array<{ location: string; start?: string; duration?: number; }>;
  sports?: Array<{ fn: string; league: string; }>;
  time?: Array<{ utc_offset: string; }>;
  response_length?: "short" | "medium" | "long";
}): Promise<unknown>; };
```"""


def _exec_description_with_full_web_run() -> str:
    return "\n\n".join(
        [
            "Run JavaScript.",
            _section("apply_patch", "input", "string"),
            _web_run_section(),
            _section(
                "skills__read",
                "args",
                "{ authority: { kind: string; }; package: string; resource: string; }",
            ),
        ]
    )


def _exec_description() -> str:
    sections = [
        _section("apply_patch", "input", "string"),
        _section(
            "create_goal",
            "args",
            "{ // Goal objective.\n objective: string; token_budget?: number; }",
        ),
        _section(
            "exec_command",
            "args",
            "{ // Shell command.\n cmd: string; workdir?: string; "
            'sandbox_permissions?: "use_default" | "require_escalated"; }',
        ),
        _section("get_goal", "args", "{}"),
        _section(
            "update_goal",
            "args",
            '{ status: "complete" | "blocked"; }',
        ),
        _section(
            "update_plan",
            "args",
            "{ explanation?: string; plan: Array<{ step: string; "
            'status: "pending" | "in_progress" | "completed"; }>; }',
        ),
        _section(
            "view_image",
            "args",
            '{ path: string; detail?: "high" | "original"; }',
        ),
        _section(
            "write_stdin",
            "args",
            "{ session_id: number; chars?: string; yield_time_ms?: number; }",
        ),
        _section(
            "wait_for_environment",
            "args",
            "{ timeout_ms?: number; }",
        ),
        _section(
            "request_permissions",
            "args",
            "{ permissions: Array<string>; }",
        ),
        _section("get_context_remaining", "args", "{}"),
        _section("list_available_plugins_to_install", "args", "{}"),
        _section(
            "request_plugin_install",
            "args",
            "{ plugin_id: string; suggest_reason: string; }",
        ),
        _section("list_mcp_resources", "args", "{ cursor?: string; }"),
        _section(
            "list_mcp_resource_templates",
            "args",
            "{ cursor?: string; server?: string; }",
        ),
        _section(
            "read_mcp_resource",
            "args",
            "{ server: string; uri: string; }",
        ),
        _section(
            "spawn_agents_on_csv",
            "args",
            "{ csv_path: string; task: string; }",
        ),
        _section(
            "report_agent_job_result",
            "args",
            "{ result: string; }",
        ),
        _section(
            "web__run",
            "args",
            "{ search_query?: Array<{ q: string; domains?: Array<string>; }>; "
            "open?: Array<{ ref_id: string; lineno?: number; }>; }",
        ),
        _section("clock__curr_time", "args", "{}"),
        _section("clock__sleep", "args", "{ seconds: number; }"),
        _section(
            "image_gen__imagegen",
            "args",
            "{ prompt: string; num_last_images_to_include?: number | null; }",
        ),
        _section(
            "memories__add_ad_hoc_note",
            "args",
            "{ title: string; content: string; }",
        ),
        _section("memories__list", "args", "{ path?: string; }"),
        _section(
            "memories__read",
            "args",
            "{ path: string; line_start?: number; line_end?: number; }",
        ),
        _section("memories__search", "args", "{ query: string; }"),
        _section("skills__list", "args", "{ authority: { kind: string; }; }"),
        _section(
            "skills__read",
            "args",
            "{ authority: { kind: string; }; package: string; resource: string; }",
        ),
    ]
    return "Run JavaScript.\n\n" + "\n\n".join(sections)


def _deferred_exec_description() -> str:
    return (
        _exec_description()
        + "\n\n"
        + (
            "Some deferred nested tools may be omitted from this description. "
            "They remain available through the ALL_TOOLS runtime catalog."
        )
    )


def test_modified_web_run_projects_only_rosetta_supported_capabilities():
    route = _route(
        tool_runtime_capabilities=frozenset({WEB_RUN_BASIC_SEARCH_CAPABILITY})
    )
    definitions = project_exec_tool_definitions(
        _web_run_section(),
        exec_tool_projections_for_route(route),
        profile_route=route,
    )

    function = definitions["web-run"]["function"]
    parameters = function["parameters"]
    assert set(parameters["properties"]) == {
        "search_query",
        "open",
        "time",
        "response_length",
    }
    assert set(parameters["properties"]["search_query"]["items"]["properties"]) == {
        "q",
        "domains",
    }
    assert set(parameters["properties"]["open"]["items"]["properties"]) == {
        "ref_id",
        "lineno",
    }
    assert set(parameters["properties"]["time"]["items"]["properties"]) == {
        "utc_offset"
    }
    assert parameters["additionalProperties"] is False

    description = function["description"]
    assert "optionally by domain" in description
    assert "recency" not in description
    assert "image_query" not in description
    assert "click" not in description
    assert "find" not in description
    assert "empty query" not in description
    assert "## Decision boundary" in description


def test_modified_web_run_projects_browser_commands_when_sidecar_is_available():
    route = _route(
        tool_runtime_capabilities=frozenset(
            {WEB_RUN_BASIC_SEARCH_CAPABILITY, WEB_RUN_SIDECAR_CAPABILITY}
        )
    )
    definitions = project_exec_tool_definitions(
        _web_run_section(),
        exec_tool_projections_for_route(route),
        profile_route=route,
    )

    function = definitions["web-run"]["function"]
    assert set(function["parameters"]["properties"]) == {
        "search_query",
        "open",
        "click",
        "find",
        "screenshot",
        "time",
        "response_length",
    }
    assert function["parameters"]["properties"]["click"]["items"]["required"] == [
        "ref_id",
        "id",
    ]
    assert function["parameters"]["properties"]["screenshot"]["items"]["required"] == [
        "ref_id",
        "pageno",
    ]
    description = function["description"]
    assert "`click`" in description
    assert "`find`" in description
    assert "`screenshot`" in description
    assert "`finance`" not in description


def test_passthrough_web_run_preserves_the_live_codex_definition():
    route = _route()
    route.tool_profile["namespace.web.run"] = "passthrough"

    definitions = project_exec_tool_definitions(
        _web_run_section(),
        exec_tool_projections_for_route(route),
        profile_route=route,
    )

    function = definitions["web-run"]["function"]
    parameters = function["parameters"]
    assert "image_query" in parameters["properties"]
    assert "click" in parameters["properties"]
    assert "recency" in parameters["properties"]["search_query"]["items"]["properties"]
    assert "empty query" in function["description"]


def test_modified_web_run_with_no_parseable_supported_branch_fails_closed():
    route = _route()
    malformed = _section(
        "web__run",
        "args",
        "{ search_query?: string; open?: string; time?: string; }",
    )

    definitions = project_exec_tool_definitions(
        malformed,
        exec_tool_projections_for_route(route),
        profile_route=route,
    )

    assert "web-run" not in definitions


@pytest.mark.parametrize(
    ("capabilities", "expected_commands"),
    [
        (frozenset(), {"open", "time", "response_length"}),
        (
            frozenset({WEB_RUN_BASIC_SEARCH_CAPABILITY}),
            {"search_query", "open", "time", "response_length"},
        ),
        (
            frozenset({WEB_RUN_SIDECAR_CAPABILITY}),
            {"open", "click", "find", "screenshot", "time", "response_length"},
        ),
        (
            frozenset({WEB_RUN_BASIC_SEARCH_CAPABILITY, WEB_RUN_SIDECAR_CAPABILITY}),
            {
                "search_query",
                "open",
                "click",
                "find",
                "screenshot",
                "time",
                "response_length",
            },
        ),
    ],
)
def test_modified_exec_web_run_description_matches_runtime_capabilities(
    capabilities, expected_commands
):
    route = _route(tool_runtime_capabilities=capabilities)

    projected = project_modified_exec_web_run_description(
        _exec_description_with_full_web_run(), route
    )
    definitions = project_exec_tool_definitions(
        projected,
        {
            "web-run": ExecToolProjection(
                item_id="namespace.web.run",
                chat_name="web-run",
                nested_name="web__run",
            )
        },
    )

    assert set(definitions["web-run"]["function"]["parameters"]["properties"]) == (
        expected_commands
    )
    assert "finance" not in projected
    assert "weather" not in projected
    assert "sports" not in projected
    assert "image_query" not in projected


def test_modified_exec_web_run_preserves_other_sections():
    description = _exec_description_with_full_web_run()
    apply_patch_section = _section("apply_patch", "input", "string")
    skills_section = _section(
        "skills__read",
        "args",
        "{ authority: { kind: string; }; package: string; resource: string; }",
    )

    projected = project_modified_exec_web_run_description(description, _route())

    assert apply_patch_section in projected
    assert skills_section in projected


def test_modified_exec_web_run_removes_only_malformed_section():
    malformed_web = """### `web__run`
Malformed declaration.

exec tool declaration:
```ts
declare const tools: { web__run(args: Map<string, unknown>): Promise<unknown>; };
```"""
    next_section = _section("clock__curr_time", "args", "{}")
    description = f"Run JavaScript.\n\n{malformed_web}\n\n{next_section}"

    projected = project_modified_exec_web_run_description(description, _route())

    assert "web__run" not in projected
    assert next_section in projected


def test_modified_exec_web_run_does_not_invent_missing_section():
    description = f"Run JavaScript.\n\n{_section('update_plan', 'args', '{}')}"

    assert (
        project_modified_exec_web_run_description(description, _route()) == description
    )


def test_chat_default_retains_apply_patch_as_an_internal_exec_projection():
    projections = exec_tool_projections_for_route(_route())

    assert set(projections) == {
        "apply_patch",
        "clock-curr_time",
        "clock-sleep",
        "create_goal",
        "exec_command",
        "get_context_remaining",
        "get_goal",
        "image_gen-imagegen",
        "list_available_plugins_to_install",
        "list_mcp_resource_templates",
        "list_mcp_resources",
        "memories-add_ad_hoc_note",
        "memories-list",
        "memories-read",
        "memories-search",
        "read_mcp_resource",
        "report_agent_job_result",
        "request_permissions",
        "request_plugin_install",
        "skills-list",
        "skills-read",
        "spawn_agents_on_csv",
        "tool_search",
        "update_goal",
        "update_plan",
        "view_image",
        "wait_for_environment",
        "web-run",
        "write_stdin",
    }
    assert projections["apply_patch"].model_visible is False
    assert all(
        projection.model_visible
        for name, projection in projections.items()
        if name != "apply_patch"
    )


def test_exec_description_projects_precise_normal_function_schemas():
    route = _route()
    projections = exec_tool_projections_for_route(route)
    definitions = project_exec_tool_definitions(
        _exec_description(), projections, profile_route=route
    )

    assert set(definitions) == set(projections) - {"tool_search"}
    exec_schema = definitions["exec_command"]["function"]["parameters"]
    assert exec_schema["required"] == ["cmd"]
    assert exec_schema["properties"]["cmd"] == {
        "type": "string",
        "description": "Shell command.",
    }
    assert exec_schema["properties"]["sandbox_permissions"]["enum"] == [
        "use_default",
        "require_escalated",
    ]
    plan_schema = definitions["update_plan"]["function"]["parameters"]
    assert plan_schema["properties"]["plan"]["items"]["required"] == [
        "step",
        "status",
    ]
    assert definitions["apply_patch"]["function"]["parameters"] == {
        "type": "object",
        "properties": {"patch": {"type": "string"}},
        "required": ["patch"],
        "additionalProperties": False,
    }
    assert definitions["exec_command"]["function"]["description"] == (
        "Description for exec_command."
    )
    assert definitions["create_goal"]["function"]["description"].endswith(
        "Do not set token_budget unless the user explicitly provided a numeric token "
        "budget."
    )
    guidance = route.tool_profile_inputs["function.create_goal"]["guidance"]
    assert definitions["create_goal"]["function"]["description"].endswith(guidance)


def test_conditional_exec_projection_only_exposes_live_codex_declarations():
    projections = exec_tool_projections_for_route(_route())
    definitions = project_exec_tool_definitions(
        "Run JavaScript.\n\n"
        + _section(
            "read_mcp_resource",
            "args",
            "{ server: string; uri: string; }",
        ),
        projections,
        profile_route=_route(),
    )

    assert set(definitions) == {"read_mcp_resource"}
    assert definitions["read_mcp_resource"]["function"]["parameters"] == {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "uri": {"type": "string"},
        },
        "required": ["server", "uri"],
        "additionalProperties": False,
    }


def test_deferred_exec_projects_stateless_all_tools_search_definition():
    route = _route()
    definitions = project_exec_tool_definitions(
        _deferred_exec_description(),
        exec_tool_projections_for_route(route),
        profile_route=route,
    )

    function = definitions["tool_search"]["function"]
    assert "ALL_TOOLS" in function["description"]
    assert "raw exec tool" in function["description"]
    assert function["parameters"] == {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": (
                    "Capability, provider, tool name, or declaration text to find. "
                    "Regex patterns are limited to 200 characters."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 8,
                "description": "Maximum number of matching tools to return.",
            },
            "search_mode": {
                "type": "string",
                "enum": ["natural_language", "regex"],
                "default": "natural_language",
                "description": (
                    "Natural-language token ranking or case-insensitive JavaScript "
                    "regular-expression matching."
                ),
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def test_all_tools_search_is_not_invented_without_deferred_guidance():
    definitions = project_exec_tool_definitions(
        _exec_description(),
        exec_tool_projections_for_route(_route()),
    )

    assert "tool_search" not in definitions


def test_all_tools_search_script_uses_live_catalog_without_gateway_cache():
    projection = exec_tool_projections_for_route(_route())["tool_search"]

    script = build_exec_script(
        projection,
        {"query": "node repl browser", "limit": 3},
    )

    assert 'const query = "node repl browser";' in script
    assert 'const searchMode = "natural_language";' in script
    assert "const catalog = Array.isArray(ALL_TOOLS) ? ALL_TOOLS : [];" in script
    assert "ranked.slice(0, limit)" in script
    assert "name: entry.name" in script
    assert "description: entry.description" in script
    assert "tools." not in script


def test_all_tools_regex_search_script_and_argument_validation():
    projection = exec_tool_projections_for_route(_route())["tool_search"]

    script = build_exec_script(
        projection,
        {
            "query": r"^mcp__node_repl__js(?:_reset)?$",
            "search_mode": "regex",
            "limit": 2,
        },
    )

    assert 'const searchMode = "regex";' in script
    assert 'new RegExp(query, "i")' in script
    assert 'pattern.test(String(entry.name ?? ""))' in script
    assert 'pattern.test(String(entry.description ?? ""))' in script
    assert 'code: "invalid_pattern"' in script
    with pytest.raises(ValueError, match="non-empty"):
        build_exec_script(projection, {"query": " "})
    with pytest.raises(ValueError, match="1 to 50"):
        build_exec_script(projection, {"query": "browser", "limit": 0})
    with pytest.raises(ValueError, match="at most 200"):
        build_exec_script(
            projection,
            {"query": "x" * 201, "search_mode": "regex"},
        )


def test_deferred_exec_request_exposes_tool_search_and_keeps_raw_exec():
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": _deferred_exec_description(),
                    "parameters": {
                        "type": "object",
                        "properties": {"input": {"type": "string"}},
                        "required": ["input"],
                    },
                },
            }
        ]
    }

    adapted = localize_code_editing_chat_request(
        body,
        capabilities=NativeToolCapabilities(has_custom_exec=True),
        native_tool_names=frozenset(),
        injected_tool_names=frozenset(),
        exec_projections=exec_tool_projections_for_route(_route()),
        hide_exec_container=True,
    )

    names = [tool["function"]["name"] for tool in adapted["tools"]]
    assert "exec" in names
    assert "tool_search" in names
    assert "tool_search" in adapted[EXEC_PROJECTIONS_KEY]
    exec_description = next(
        tool["function"]["description"]
        for tool in adapted["tools"]
        if tool["function"]["name"] == "exec"
    )
    assert "deferred nested tools" in exec_description


def test_direct_tool_search_name_wins_over_synthetic_projection():
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": _deferred_exec_description(),
                    "parameters": {"type": "object"},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "tool_search",
                    "description": "Direct provider search.",
                    "parameters": {"type": "object"},
                },
            },
        ]
    }

    adapted = localize_code_editing_chat_request(
        body,
        capabilities=NativeToolCapabilities(has_custom_exec=True),
        native_tool_names=frozenset(),
        injected_tool_names=frozenset(),
        exec_projections=exec_tool_projections_for_route(_route()),
        hide_exec_container=True,
    )

    search_tools = [
        tool for tool in adapted["tools"] if tool["function"]["name"] == "tool_search"
    ]
    assert len(search_tools) == 1
    assert search_tools[0]["function"]["description"] == "Direct provider search."
    assert "tool_search" not in adapted.get(EXEC_PROJECTIONS_KEY, {})
    assert any(tool["function"]["name"] == "exec" for tool in adapted["tools"])


def test_all_tools_search_call_translates_to_native_custom_exec():
    projection = exec_tool_projections_for_route(_route())["tool_search"]

    translated = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_search",
            "tool_name": "tool_search",
            "tool_input": {"query": "browser", "limit": 4},
        },
        exec_projections={"tool_search": projection},
    )

    assert translated is not None
    assert translated.part["tool_name"] == "exec"
    assert translated.part["tool_type"] == "custom"
    assert "ALL_TOOLS" in translated.part["tool_input"]["input"]
    assert translated.mapping.localized_name == "tool_search"
    assert translated.mapping.native_name == "exec"


def test_exec_description_projects_full_codex_rendered_typescript_grammar():
    description = """### `complex_tool` (`complex-tool`)
Exercises the schema grammar emitted by Codex.

exec tool declaration:
```ts
declare const tools: { complex_tool(args: {
  exact_true: true;
  exact_false?: false;
  impossible?: never;
  combined?: { left: string; } & { right: number; };
  bag?: { fixed: number; [key: string]: string; };
  exponent?: 1e10 | -2.5E-3;
  literal_null?: null;
  literal_array?: [1, "two", true];
  literal_object?: { "not-valid-name": false; };
  tuple?: [string, number];
  unknowns?: unknown[];
}): Promise<unknown>; };
```"""
    projection = ExecToolProjection(
        item_id="function.complex_tool",
        chat_name="complex-tool",
        nested_name="complex_tool",
    )

    definitions = project_exec_tool_definitions(
        description, {projection.chat_name: projection}
    )

    parameters = definitions["complex-tool"]["function"]["parameters"]
    assert parameters["required"] == ["exact_true"]
    assert parameters["properties"]["exact_true"] == {
        "type": "boolean",
        "const": True,
    }
    assert parameters["properties"]["exact_false"] == {
        "type": "boolean",
        "const": False,
    }
    assert parameters["properties"]["impossible"] == {"not": {}}
    assert parameters["properties"]["combined"] == {
        "allOf": [
            {
                "type": "object",
                "properties": {"left": {"type": "string"}},
                "required": ["left"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"right": {"type": "number"}},
                "required": ["right"],
                "additionalProperties": False,
            },
        ]
    }
    assert parameters["properties"]["bag"] == {
        "type": "object",
        "properties": {"fixed": {"type": "number"}},
        "required": ["fixed"],
        "additionalProperties": {"type": "string"},
    }
    assert parameters["properties"]["exponent"] == {
        "type": "number",
        "enum": [1e10, -2.5e-3],
    }
    assert parameters["properties"]["literal_null"] == {"type": "null"}
    assert parameters["properties"]["literal_array"] == {
        "type": "array",
        "prefixItems": [
            {"type": "number", "const": 1},
            {"type": "string", "const": "two"},
            {"type": "boolean", "const": True},
        ],
        "minItems": 3,
        "maxItems": 3,
    }
    assert parameters["properties"]["literal_object"] == {
        "type": "object",
        "properties": {"not-valid-name": {"type": "boolean", "const": False}},
        "required": ["not-valid-name"],
        "additionalProperties": False,
    }
    assert parameters["properties"]["tuple"] == {
        "type": "array",
        "prefixItems": [{"type": "string"}, {"type": "number"}],
        "minItems": 2,
        "maxItems": 2,
    }
    assert parameters["properties"]["unknowns"] == {
        "type": "array",
        "items": {},
    }


def test_exec_description_with_unknown_typescript_syntax_fails_closed():
    projection = ExecToolProjection(
        item_id="function.invalid",
        chat_name="invalid",
        nested_name="invalid",
    )
    for input_type in (
        "{ value: string @ number; }",
        "{ value: UnrenderedNamedType; }",
    ):
        description = _section("invalid", "args", input_type)
        assert (
            project_exec_tool_definitions(
                description, {projection.chat_name: projection}
            )
            == {}
        )


def test_projection_plan_rejects_duplicate_sections_and_preserves_them():
    projection = ExecToolProjection(
        item_id="function.exec_command",
        chat_name="exec_command",
        nested_name="exec_command",
    )
    description = "\n\n".join(
        [
            "Run JavaScript.",
            _section("exec_command", "args", "{ cmd: string; }"),
            _section("exec_command", "args", "{ cmd: string; cwd?: string; }"),
        ]
    )

    plan = plan_exec_tool_definitions(description, {projection.chat_name: projection})

    assert plan.definitions == {}
    assert plan.sections == {}
    assert plan.duplicate_section_names == frozenset({"exec_command"})
    assert prune_exec_tool_description(description, tuple(plan.sections.values())) == (
        description
    )


def test_pruning_removes_only_exact_projected_declaration_section():
    projection = ExecToolProjection(
        item_id="function.exec_command",
        chat_name="exec_command",
        nested_name="exec_command",
    )
    known = _section("exec_command", "args", "{ cmd: string; }")
    unknown = _section("future_tool", "args", "{ value: string; }")
    suffix = (
        "Some deferred nested tools may be omitted from this description. "
        "They remain available through the ALL_TOOLS runtime catalog."
    )
    description = f"Run JavaScript.\n\n{known}\n\n{unknown}\n\n{suffix}"
    plan = plan_exec_tool_definitions(description, {projection.chat_name: projection})

    pruned = prune_exec_tool_description(description, tuple(plan.sections.values()))

    assert pruned == f"Run JavaScript.\n\n\n\n{unknown}\n\n{suffix}"
    assert exec_tool_section_names(pruned) == ("future_tool",)


def test_projection_retains_parseable_declaration_with_unclosed_fence():
    projection = ExecToolProjection(
        item_id="function.exec_command",
        chat_name="exec_command",
        nested_name="exec_command",
    )
    description = """### `exec_command`
Description for exec_command.

exec tool declaration:
```ts
declare const tools: { exec_command(args: { cmd: string; }): Promise<unknown>; };

Deferred guidance remains here."""

    plan = plan_exec_tool_definitions(description, {projection.chat_name: projection})

    assert plan.definitions == {}
    assert plan.sections == {}
    assert prune_exec_tool_description(description, ()) == description


def test_request_prunes_known_section_but_retains_unknown_exec_capability():
    description = "\n\n".join(
        [
            "Run JavaScript.",
            _section("exec_command", "args", "{ cmd: string; }"),
            _section("future_tool", "args", "{ value: string; }"),
        ]
    )
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": description,
                    "parameters": {},
                },
            }
        ]
    }

    adapted = localize_code_editing_chat_request(
        body,
        capabilities=NativeToolCapabilities(has_custom_exec=True),
        native_tool_names=frozenset(),
        injected_tool_names=frozenset(),
        exec_projections=exec_tool_projections_for_route(_route()),
    )

    tools = {tool["function"]["name"]: tool["function"] for tool in adapted["tools"]}
    assert set(tools) == {"exec", "exec_command"}
    assert exec_tool_section_names(tools["exec"]["description"]) == ("future_tool",)
    assert "Description for future_tool." in tools["exec"]["description"]


def test_request_retains_conflicting_and_malformed_known_sections():
    valid = _section("exec_command", "args", "{ cmd: string; }")
    malformed = _section("update_plan", "args", "{ plan: InvalidType; }")
    description = f"Run JavaScript.\n\n{valid}\n\n{malformed}"
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": description,
                    "parameters": {},
                },
            },
            {
                "type": "function",
                "function": {"name": "exec_command", "parameters": {}},
            },
        ]
    }

    adapted = localize_code_editing_chat_request(
        body,
        capabilities=NativeToolCapabilities(has_custom_exec=True),
        native_tool_names=frozenset(),
        injected_tool_names=frozenset(),
        exec_projections=exec_tool_projections_for_route(_route()),
    )

    exec_function = next(
        tool["function"]
        for tool in adapted["tools"]
        if tool["function"]["name"] == "exec"
    )
    assert exec_function["description"] == description
    assert exec_tool_section_names(exec_function["description"]) == (
        "exec_command",
        "update_plan",
    )


def test_internal_section_requires_all_declared_replacements_before_pruning():
    description = "Run JavaScript.\n\n" + _section("apply_patch", "input", "string")
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": description,
                    "parameters": {},
                },
            }
        ]
    }

    adapted = localize_code_editing_chat_request(
        body,
        capabilities=NativeToolCapabilities(has_custom_exec=True),
        native_tool_names=frozenset(),
        injected_tool_names=frozenset({"Edit"}),
        exec_projections=exec_tool_projections_for_route(_route()),
    )

    names = [tool["function"]["name"] for tool in adapted["tools"]]
    assert names == ["exec", "Edit"]
    assert exec_tool_section_names(adapted["tools"][0]["function"]["description"]) == (
        "apply_patch",
    )


def test_request_projection_preserves_direct_tools_and_records_only_added_tools():
    projections = exec_tool_projections_for_route(_route())
    body = {
        "tool_choice": {"type": "function", "function": {"name": "exec"}},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": _exec_description(),
                    "parameters": {
                        "type": "object",
                        "properties": {"input": {"type": "string"}},
                    },
                },
            },
            {"type": "function", "function": {"name": "wait", "parameters": {}}},
            {
                "type": "function",
                "function": {"name": "request_user_input", "parameters": {}},
            },
            {
                "type": "function",
                "function": {"name": "collaboration-spawn_agent", "parameters": {}},
            },
        ],
    }

    adapted = localize_code_editing_chat_request(
        body,
        capabilities=NativeToolCapabilities(has_custom_exec=True),
        native_tool_names=frozenset(),
        injected_tool_names=frozenset({"Edit", "Write"}),
        exec_projections=projections,
    )
    names = [tool["function"]["name"] for tool in adapted["tools"]]

    assert {"wait", "request_user_input", "collaboration-spawn_agent"}.issubset(names)
    visible_projection_names = {
        name
        for name, projection in projections.items()
        if projection.model_visible and name != "tool_search"
    }
    assert visible_projection_names.issubset(names)
    assert "exec" not in names
    assert "apply_patch" not in names
    assert adapted["tool_choice"] == "auto"
    assert set(adapted[EXEC_PROJECTIONS_KEY]) == set(projections) - {"tool_search"}


def test_modified_web_run_request_sends_pruned_function_to_upstream_model():
    route = _route(
        tool_runtime_capabilities=frozenset({WEB_RUN_BASIC_SEARCH_CAPABILITY})
    )
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": _web_run_section(),
                    "parameters": {"type": "object"},
                },
            }
        ]
    }

    adapted = localize_code_editing_chat_request(
        body,
        capabilities=NativeToolCapabilities(has_custom_exec=True),
        native_tool_names=frozenset(),
        injected_tool_names=frozenset(),
        exec_projections=exec_tool_projections_for_route(route),
        profile_route=route,
        hide_exec_container=True,
    )

    assert [tool["function"]["name"] for tool in adapted["tools"]] == ["web-run"]
    properties = adapted["tools"][0]["function"]["parameters"]["properties"]
    assert set(properties) == {"search_query", "open", "time", "response_length"}


def test_unparsed_exec_container_is_retained_fail_closed():
    body = {
        "tool_choice": {"type": "function", "function": {"name": "exec"}},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": "No parseable nested declarations.",
                    "parameters": {},
                },
            }
        ],
    }

    adapted = localize_code_editing_chat_request(
        body,
        capabilities=NativeToolCapabilities(has_custom_exec=True),
        native_tool_names=frozenset(),
        injected_tool_names=frozenset(),
        exec_projections=exec_tool_projections_for_route(_route()),
        hide_exec_container=True,
    )

    assert adapted["tools"] == body["tools"]
    assert adapted["tool_choice"] == body["tool_choice"]


def test_gateway_preserves_deferred_exec_container_and_custom_round_trip():
    captured_body: dict = {}
    script = (
        "const entry = ALL_TOOLS.find(({ name }) => "
        'name.includes("get_archive_proof"));\n'
        "const result = await tools[entry.name]({ record_id: "
        '"ARCHIVE-20260716" });\ntext(result);'
    )
    upstream_response = {
        "id": "chatcmpl-deferred-exec",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_exec",
                            "type": "function",
                            "function": {
                                "name": "exec",
                                "arguments": json.dumps({"input": script}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    async def send_request(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_body.update(body)
        return UpstreamResponse(
            status_code=200,
            body=upstream_response,
            raw_content=json.dumps(upstream_response).encode(),
        )

    transport = MagicMock()
    transport.send_request = AsyncMock(side_effect=send_request)
    provider_info = MagicMock()
    provider_info.base_url = "https://example.test"
    body = {
        "model": "deepseek-v4-flash",
        "input": [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {
                        "type": "custom",
                        "name": "exec",
                        "description": _deferred_exec_description(),
                    }
                ],
            },
            {"role": "user", "content": "retrieve the archive proof"},
        ],
    }

    async def run():
        return await handle_non_streaming(
            _route(),
            provider_info,
            body,
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=CodexToolLocalizationStore(),
        )

    response, _ = asyncio.run(run())

    assert response.status_code == 200
    target_exec = next(
        tool["function"]
        for tool in captured_body["tools"]
        if tool.get("function", {}).get("name") == "exec"
    )
    assert target_exec["parameters"] == {
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": ["input"],
    }
    target_description = target_exec["description"]
    assert exec_tool_section_names(target_description) == ()
    assert "deferred nested tools" in target_description
    output = json.loads(response.body)["output"][0]
    assert output["type"] == "custom_tool_call"
    assert output["name"] == "exec"
    assert output["input"] == script


def test_gateway_all_tools_search_round_trips_as_custom_exec():
    captured_body: dict = {}
    upstream_response = {
        "id": "chatcmpl-all-tools-search",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_search",
                            "type": "function",
                            "function": {
                                "name": "tool_search",
                                "arguments": json.dumps(
                                    {
                                        "query": "browser javascript repl",
                                        "limit": 4,
                                    }
                                ),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    async def send_request(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_body.update(body)
        return UpstreamResponse(
            status_code=200,
            body=upstream_response,
            raw_content=json.dumps(upstream_response).encode(),
        )

    transport = MagicMock()
    transport.send_request = AsyncMock(side_effect=send_request)
    provider_info = MagicMock()
    provider_info.base_url = "https://example.test"
    body = {
        "model": "deepseek-v4-flash",
        "input": [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {
                        "type": "custom",
                        "name": "exec",
                        "description": _deferred_exec_description(),
                    },
                    {
                        "type": "tool_search",
                        "description": "Legacy native mapping must be removed.",
                        "parameters": {"type": "object"},
                    },
                ],
            },
            {"role": "user", "content": "find the browser JavaScript tool"},
        ],
    }

    async def run():
        return await handle_non_streaming(
            _route(),
            provider_info,
            body,
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=CodexToolLocalizationStore(),
        )

    response, _ = asyncio.run(run())

    target_tools = {
        tool["function"]["name"]: tool["function"]
        for tool in captured_body["tools"]
        if isinstance(tool.get("function"), dict)
    }
    assert "tool_search" in target_tools
    assert "ALL_TOOLS" in target_tools["tool_search"]["description"]
    assert "Legacy native mapping" not in target_tools["tool_search"]["description"]
    assert "exec" in target_tools
    output = json.loads(response.body)["output"][0]
    assert output["type"] == "custom_tool_call"
    assert output["name"] == "exec"
    assert "Array.isArray(ALL_TOOLS)" in output["input"]
    assert 'const query = "browser javascript repl";' in output["input"]
    assert "const limit = 4;" in output["input"]


def test_projected_call_translates_to_custom_exec_and_round_trips_mapping():
    projection = exec_tool_projections_for_route(_route())["web-run"]
    translated = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_web",
            "tool_name": "web-run",
            "tool_input": {"open": [{"ref_id": "turn0search0"}]},
        },
        exec_projections={"web-run": projection},
    )

    assert translated is not None
    assert translated.part["tool_name"] == "exec"
    assert translated.part["tool_type"] == "custom"
    assert translated.part["tool_input"]["input"] == (
        'const result = await tools.web__run({"open":[{"ref_id":"turn0search0"}]});\n'
        "text(result);\n"
    )

    restored = localized_mapping_from_tool_calls(
        translated.mapping.original_tool_call(),
        translated.mapping.codex_tool_call(),
    )
    assert restored == translated.mapping


def test_localized_edit_uses_projected_apply_patch_with_custom_exec():
    projection = exec_tool_projections_for_route(_route())["apply_patch"]
    translated = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_edit",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "fixtures/alpha.txt",
                "old_string": "status=original",
                "new_string": "status=edited",
            },
        },
        capabilities=NativeToolCapabilities(
            has_custom_apply_patch=False,
            has_custom_exec=True,
        ),
        exec_projections={"apply_patch": projection},
    )

    assert translated is not None
    assert translated.part["tool_name"] == "exec"
    assert translated.part["tool_type"] == "custom"
    script = translated.part["tool_input"]["input"]
    assert script.startswith("const result = await tools.apply_patch(")
    assert "*** Update File: fixtures/alpha.txt" in script
    assert "-status=original" in script
    assert "+status=edited" in script
    assert script.endswith("text(result);\n")
    assert translated.mapping.localized_name == "Edit"


def test_localized_write_uses_projected_apply_patch_with_custom_exec():
    projection = exec_tool_projections_for_route(_route())["apply_patch"]
    translated = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_write",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "fixtures/created.txt",
                "content": "CREATED_BY_WRITE\n",
            },
        },
        capabilities=NativeToolCapabilities(
            has_custom_apply_patch=False,
            has_custom_exec=True,
        ),
        exec_projections={"apply_patch": projection},
    )

    assert translated is not None
    assert translated.part["tool_name"] == "exec"
    assert translated.part["tool_type"] == "custom"
    script = translated.part["tool_input"]["input"]
    assert script.startswith("const result = await tools.apply_patch(")
    assert "*** Add File: fixtures/created.txt" in script
    assert "+CREATED_BY_WRITE" in script
    assert script.endswith("text(result);\n")
    assert translated.mapping.localized_name == "Write"


def test_view_image_projection_uses_image_output_helper():
    projection = exec_tool_projections_for_route(_route())["view_image"]

    assert build_exec_script(projection, {"path": "/tmp/test.png"}) == (
        'const result = await tools.view_image({"path":"/tmp/test.png"});\n'
        "image(result);\n"
    )


def test_modified_view_image_limits_detail_values_in_schema_and_exec_script():
    route = _route()
    route.tool_profile["function.view_image"] = "modified"
    route.tool_profile_inputs["function.view_image"] = {
        "supported_details": "auto,original"
    }
    projections = exec_tool_projections_for_route(route)
    definitions = project_exec_tool_definitions(
        _section(
            "view_image",
            "args",
            '{ path: string; detail?: "auto" | "low" | "high" | "original"; }',
        ),
        projections,
        profile_route=route,
    )

    detail = definitions["view_image"]["function"]["parameters"]["properties"]["detail"]
    assert detail["enum"] == ["auto", "original"]
    assert detail["default"] == "auto"
    assert build_exec_script(
        projections["view_image"], {"path": "/tmp/test.png", "detail": "original"}
    ).startswith("const result = await tools.view_image(")
    with pytest.raises(ValueError, match="detail must be one of"):
        build_exec_script(
            projections["view_image"],
            {"path": "/tmp/test.png", "detail": "high"},
        )


def test_image_generation_projection_uses_generated_image_output_helper():
    route = _route()
    route.tool_profile["namespace.image_gen.imagegen"] = "modified"
    projection = exec_tool_projections_for_route(route)["image_gen-imagegen"]

    assert build_exec_script(projection, {"prompt": "Draw a fox"}) == (
        'const result = await tools.image_gen__imagegen({"prompt":"Draw a fox"});\n'
        "generatedImage(result);\n"
    )


def test_streaming_projected_call_emits_custom_exec_input():
    projection = exec_tool_projections_for_route(_route())["exec_command"]
    transformer = LocalizedToolCallStreamTransformer(
        exec_projections={"exec_command": projection}
    )

    assert (
        transformer.transform(
            {
                "type": "tool_call_start",
                "tool_call_id": "call_exec",
                "tool_name": "exec_command",
            }
        )
        == []
    )
    assert (
        transformer.transform(
            {
                "type": "tool_call_delta",
                "tool_call_id": "call_exec",
                "arguments_delta": '{"cmd":"pwd"}',
            }
        )
        == []
    )
    events = transformer.transform({"type": "finish"})

    assert events[0]["tool_name"] == "exec"
    assert events[0]["tool_type"] == "custom"
    assert events[1]["arguments_delta"] == (
        'const result = await tools.exec_command({"cmd":"pwd"});\ntext(result);\n'
    )


def test_same_named_direct_function_prevents_projection_and_translation_metadata():
    projections = exec_tool_projections_for_route(_route())
    body = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "exec",
                    "description": _exec_description(),
                    "parameters": {},
                },
            },
            {
                "type": "function",
                "function": {"name": "exec_command", "parameters": {}},
            },
        ]
    }

    adapted = localize_code_editing_chat_request(
        body,
        capabilities=NativeToolCapabilities(has_custom_exec=True),
        native_tool_names=frozenset(),
        injected_tool_names=frozenset(),
        exec_projections=projections,
    )

    assert [tool["function"]["name"] for tool in adapted["tools"]].count(
        "exec_command"
    ) == 1
    assert "exec_command" not in adapted[EXEC_PROJECTIONS_KEY]


def test_projected_mapping_history_restores_original_chat_call():
    projection = exec_tool_projections_for_route(_route())["update_plan"]
    arguments = {"plan": [{"step": "Test", "status": "in_progress"}]}
    translated = translate_localized_tool_call_part(
        {
            "type": "tool_call",
            "tool_call_id": "call_plan",
            "tool_name": "update_plan",
            "tool_input": arguments,
        },
        exec_projections={"update_plan": projection},
    )
    assert translated is not None

    adapted = localize_code_editing_chat_request(
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [translated.mapping.codex_tool_call()],
                }
            ]
        },
        mappings=[translated.mapping],
    )
    function = adapted["messages"][0]["tool_calls"][0]["function"]

    assert function["name"] == "update_plan"
    assert json.loads(function["arguments"]) == arguments


def test_gateway_projects_direct_tools_and_persists_exec_round_trip_with_ttl(tmp_path):
    captured_bodies: list[dict] = []
    upstream_responses = [
        {
            "id": "chatcmpl-first",
            "object": "chat.completion",
            "created": 1,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_web",
                                "type": "function",
                                "function": {
                                    "name": "web-run",
                                    "arguments": json.dumps(
                                        {"open": [{"ref_id": "turn0search0"}]}
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
        {
            "id": "chatcmpl-second",
            "object": "chat.completion",
            "created": 2,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ],
        },
    ]

    async def send_request(
        provider_info, target_provider, body, model, *, extra_headers=None
    ):
        captured_bodies.append(body)
        response = upstream_responses.pop(0)
        return UpstreamResponse(
            status_code=200,
            body=response,
            raw_content=json.dumps(response).encode(),
        )

    transport = MagicMock()
    transport.send_request = AsyncMock(side_effect=send_request)
    provider_info = MagicMock()
    provider_info.base_url = "https://example.test"
    persistence = PersistenceManager(str(tmp_path))
    route = _route()
    scope = GatewayStateScope(
        principal_id="client",
        provider_name="test",
        model="deepseek-v4-flash",
        conversation_id="window-1",
        persistent=True,
    )
    direct_tools = [
        {
            "type": "custom",
            "name": "exec",
            "description": _exec_description(),
        },
        {
            "type": "function",
            "name": "wait",
            "description": "Wait for an exec cell.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "function",
            "name": "request_user_input",
            "description": "Ask the user.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "namespace",
            "name": "collaboration",
            "description": "Coordinate sub-agents.",
            "tools": [
                {
                    "type": "function",
                    "name": "spawn_agent",
                    "description": "Spawn a child.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_name": {"type": "string"}},
                        "required": ["task_name"],
                    },
                }
            ],
        },
    ]
    first_body = {
        "model": "deepseek-v4-flash",
        "input": [
            {"type": "additional_tools", "role": "developer", "tools": direct_tools},
            {"role": "user", "content": "open the result"},
        ],
    }

    async def run(body):
        return await handle_non_streaming(
            route,
            provider_info,
            body,
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=CodexToolLocalizationStore(),
            persistence=persistence,
            state_scope=scope,
        )

    first_response, _ = asyncio.run(run(first_body))
    first_output = json.loads(first_response.body)["output"][0]
    assert first_output["type"] == "custom_tool_call"
    assert first_output["name"] == "exec"
    assert "tools.web__run" in first_output["input"]
    first_names = {
        tool["function"]["name"]
        for tool in captured_bodies[0]["tools"]
        if isinstance(tool.get("function"), dict)
    }
    assert {"wait", "request_user_input", "collaboration-spawn_agent"}.issubset(
        first_names
    )
    projections = exec_tool_projections_for_route(route)
    assert {
        name
        for name, projection in projections.items()
        if projection.model_visible and name != "tool_search"
    }.issubset(first_names)
    assert {"Edit", "Write"}.issubset(first_names)
    assert "apply_patch" not in first_names

    rows = persistence.query_tool_call_mappings(
        principal_id="client",
        provider_name="test",
        model="deepseek-v4-flash",
        session_id="window-1",
        now=datetime.now(timezone.utc).isoformat(),
    )
    assert rows[0]["original_tool_call"]["function"]["name"] == "web-run"
    assert rows[0]["codex_tool_call"]["function"]["name"] == "exec"
    created = datetime.fromisoformat(rows[0]["created_at"])
    expires = datetime.fromisoformat(rows[0]["expire_at"])
    assert (expires - created).total_seconds() == 24 * 60 * 60

    second_body = {
        "model": "deepseek-v4-flash",
        "input": [
            {"type": "additional_tools", "role": "developer", "tools": direct_tools},
            first_output,
            {
                "type": "custom_tool_call_output",
                "call_id": "call_web",
                "output": "opened",
            },
            {"role": "user", "content": "continue"},
        ],
    }
    asyncio.run(run(second_body))
    history_call = captured_bodies[1]["messages"][0]["tool_calls"][0]

    assert history_call["function"]["name"] == "web-run"
    assert json.loads(history_call["function"]["arguments"]) == {
        "open": [{"ref_id": "turn0search0"}]
    }
    persistence.close()


def test_gateway_keeps_wait_request_user_input_and_collaboration_native(tmp_path):
    upstream_response = {
        "id": "chatcmpl-direct",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_wait",
                            "type": "function",
                            "function": {
                                "name": "wait",
                                "arguments": '{"cell_id":"cell-1"}',
                            },
                        },
                        {
                            "id": "call_input",
                            "type": "function",
                            "function": {
                                "name": "request_user_input",
                                "arguments": '{"questions":[]}',
                            },
                        },
                        {
                            "id": "call_spawn",
                            "type": "function",
                            "function": {
                                "name": "collaboration-spawn_agent",
                                "arguments": '{"task_name":"worker"}',
                            },
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    transport = MagicMock()
    transport.send_request = AsyncMock(
        return_value=UpstreamResponse(
            status_code=200,
            body=upstream_response,
            raw_content=json.dumps(upstream_response).encode(),
        )
    )
    provider_info = MagicMock()
    provider_info.base_url = "https://example.test"
    direct_tools = [
        {
            "type": "custom",
            "name": "exec",
            "description": _exec_description(),
        },
        {
            "type": "function",
            "name": "wait",
            "description": "Wait for exec.",
            "parameters": {
                "type": "object",
                "properties": {"cell_id": {"type": "string"}},
                "required": ["cell_id"],
            },
        },
        {
            "type": "function",
            "name": "request_user_input",
            "description": "Ask the user.",
            "parameters": {
                "type": "object",
                "properties": {"questions": {"type": "array"}},
                "required": ["questions"],
            },
        },
        {
            "type": "namespace",
            "name": "collaboration",
            "description": "Coordinate sub-agents.",
            "tools": [
                {
                    "type": "function",
                    "name": "spawn_agent",
                    "description": "Spawn a child.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_name": {"type": "string"}},
                        "required": ["task_name"],
                    },
                }
            ],
        },
    ]
    response, _ = asyncio.run(
        handle_non_streaming(
            _route(),
            provider_info,
            {
                "model": "deepseek-v4-flash",
                "input": [
                    {
                        "type": "additional_tools",
                        "role": "developer",
                        "tools": direct_tools,
                    },
                    {"role": "user", "content": "use direct tools"},
                ],
            },
            transport=transport,
            metadata_store=ProviderMetadataStore(),
            codex_tool_store=CodexToolLocalizationStore(),
            persistence=PersistenceManager(str(tmp_path)),
            state_scope=GatewayStateScope(
                principal_id="client",
                provider_name="test",
                model="deepseek-v4-flash",
                conversation_id="window-direct",
                persistent=True,
            ),
        )
    )

    output = json.loads(response.body)["output"]
    by_call_id = {item["call_id"]: item for item in output}
    assert by_call_id["call_wait"] == {
        "type": "function_call",
        "id": "fc_wait",
        "call_id": "call_wait",
        "name": "wait",
        "arguments": '{"cell_id": "cell-1"}',
        "status": "completed",
    }
    assert by_call_id["call_input"]["type"] == "function_call"
    assert by_call_id["call_input"]["name"] == "request_user_input"
    assert by_call_id["call_spawn"]["type"] == "function_call"
    assert by_call_id["call_spawn"]["name"] == "spawn_agent"
    assert by_call_id["call_spawn"]["namespace"] == "collaboration"
