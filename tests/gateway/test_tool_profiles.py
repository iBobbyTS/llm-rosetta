"""Tool-profile configuration and request-policy tests."""

from __future__ import annotations

import copy

import pytest

from codex_rosetta.auto_detect import ProviderType
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.admin.tool_catalog import load_tool_catalog
from codex_rosetta.gateway import tool_profiles as tool_profiles_module
from codex_rosetta.gateway.code_mode_projection import (
    ExecToolProjection,
    project_exec_tool_definitions,
)
from codex_rosetta.gateway.proxy import (
    _apply_converted_request_tool_adaptation,
    _apply_tool_adaptation,
)
from codex_rosetta.gateway.tool_profiles import (
    normalize_tool_profile_input_overrides,
    normalize_tool_profile_documents,
    resolve_tool_profile,
    tool_profile_contract,
)
from codex_rosetta.gateway.web_run_capabilities import (
    WEB_RUN_BASIC_SEARCH_CAPABILITY,
    WEB_RUN_SIDECAR_CAPABILITY,
)
from codex_rosetta.routing import ResolvedRoute


def _profile(**overrides: str) -> dict[str, str]:
    profile = dict(tool_profile_contract()["builtin"])
    profile.update(overrides)
    return profile


def _route(
    profile: dict[str, str],
    inputs: dict[str, dict[str, str]] | None = None,
    *,
    target_provider: ProviderType = "openai_chat",
    tool_runtime_capabilities: frozenset[str] = frozenset(),
) -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider=target_provider,
        provider_name="test",
        tool_profile_name="custom",
        tool_profile=profile,
        tool_profile_inputs=inputs or {},
        tool_runtime_capabilities=tool_runtime_capabilities,
    )


def test_builtin_profile_covers_catalog_with_type_specific_states():
    contract = tool_profile_contract()

    assert set(contract["builtin"]) == set(contract["supported"])
    assert set(contract["namespace_children"]) == {
        "namespace.multi_agent_v1",
        "namespace.multi_agent_v2",
    }
    for item_id in (
        "namespace.clock.curr_time",
        "namespace.clock.sleep",
        "namespace.memories.add_ad_hoc_note",
        "namespace.memories.list",
        "namespace.memories.read",
        "namespace.memories.search",
        "namespace.skills.list",
        "namespace.skills.read",
    ):
        assert contract["builtin"][item_id] == "passthrough"
    assert contract["builtin"]["namespace.web.run"] == "modified"
    assert contract["builtin"]["namespace.image_gen.imagegen"] == "modified"
    assert contract["builtin"]["hosted.web_search"] == "disabled"
    assert contract["builtin"]["namespace.multi_agent_v1"] == "disabled"
    assert "namespace.mcp_github" not in contract["builtin"]
    assert contract["builtin"]["custom.exec"] == "disabled"
    assert contract["internal_containers_when_disabled"] == frozenset({"custom.exec"})
    assert contract["builtin"]["namespace.multi_agent_v2.spawn_agent"] == "modified"
    assert contract["supported"]["namespace.multi_agent_v2.spawn_agent"] == (
        "disabled",
        "passthrough",
        "modified",
    )
    assert (
        "message field is the complete child task"
        in contract["readonly"]["builtin"]["inputs"][
            "namespace.multi_agent_v2.spawn_agent"
        ]["guidance"]
    )
    assert (
        "Pass raw JavaScript source"
        in contract["readonly"]["builtin"]["inputs"]["custom.exec"]["guidance"]
    )
    assert set(contract["readonly"]) == {
        "builtin",
        "openai-responses-tool-mapping-only",
        "web-run-injection",
        "responses-tool-mapping",
    }
    assert all(
        contract["builtin"][child_id] == "disabled"
        for child_id in contract["namespace_children"]["namespace.multi_agent_v1"]
    )
    assert contract["supported"]["injection.claude_code.read"] == (
        "disabled",
        "injected",
    )
    assert contract["builtin"]["injection.claude_code.read"] == "injected"


def test_openai_responses_tool_mapping_only_profile_never_replaces_tools():
    contract = tool_profile_contract()
    profile = contract["readonly"]["openai-responses-tool-mapping-only"]["tools"]
    catalog_items = load_tool_catalog()["items"]

    assert set(profile) == set(contract["supported"])
    for item in catalog_items:
        expected = "disabled" if item["type"] == "custom_injection" else "passthrough"
        assert profile[item["id"]] == expected


def test_disabled_namespace_forces_all_child_states_to_disabled():
    tools = dict(tool_profile_contract()["builtin"])
    tools["namespace.multi_agent_v2"] = "disabled"
    for child_id in tool_profile_contract()["namespace_children"][
        "namespace.multi_agent_v2"
    ]:
        tools[child_id] = "passthrough"

    documents = normalize_tool_profile_documents({"custom": {"tools": tools}})

    assert documents["custom"]["tools"]["namespace.multi_agent_v2"] == "disabled"
    assert all(
        documents["custom"]["tools"][child_id] == "disabled"
        for child_id in tool_profile_contract()["namespace_children"][
            "namespace.multi_agent_v2"
        ]
    )


def test_function_profile_inputs_support_text_password_and_select_values(monkeypatch):
    catalog = copy.deepcopy(load_tool_catalog())
    function_item = next(
        item for item in catalog["items"] if item["id"] == "function.update_plan"
    )
    function_item["profile_inputs"] = [
        {
            "id": "endpoint",
            "label_i18n": "tools.input.endpoint",
            "default": "https://example.test/v1",
        },
        {
            "id": "token",
            "label_i18n": "tools.input.token",
            "type": "password",
            "default": "",
            "placeholder_i18n": "tools.input.tokenPlaceholder",
        },
        {
            "id": "quality",
            "label_i18n": "tools.input.quality",
            "type": "select",
            "default": "standard",
            "visible_when": ["disabled"],
            "options": [
                {"value": "standard", "label": "Standard"},
                {"value": "hd", "label": "HD"},
            ],
        },
    ]
    monkeypatch.setattr(tool_profiles_module, "load_tool_catalog", lambda: catalog)
    tool_profiles_module.tool_profile_contract.cache_clear()
    try:
        contract = tool_profiles_module.tool_profile_contract()
        assert contract["readonly"]["builtin"]["inputs"]["function.update_plan"] == {
            "endpoint": "https://example.test/v1",
            "token": "",
            "quality": "standard",
        }
        assert contract["input_definitions"]["function.update_plan"]["token"] == {
            "id": "token",
            "label_i18n": "tools.input.token",
            "type": "password",
            "default": "",
            "placeholder_i18n": "tools.input.tokenPlaceholder",
        }
        assert contract["input_definitions"]["function.update_plan"]["quality"] == {
            "id": "quality",
            "label_i18n": "tools.input.quality",
            "type": "select",
            "default": "standard",
            "visible_when": ["disabled"],
            "options": [
                {"value": "standard", "label": "Standard"},
                {"value": "hd", "label": "HD"},
            ],
        }

        tools = dict(contract["builtin"])
        documents = normalize_tool_profile_documents(
            {
                "custom": {
                    "tools": tools,
                    "inputs": {
                        "function.update_plan": {
                            "endpoint": "https://gateway.test/v1",
                            "token": "secret-token",
                            "quality": "hd",
                        }
                    },
                }
            }
        )
        assert documents["custom"]["inputs"]["function.update_plan"] == {
            "endpoint": "https://gateway.test/v1",
            "token": "secret-token",
            "quality": "hd",
        }

        with pytest.raises(ValueError, match="quality must be one of"):
            normalize_tool_profile_documents(
                {
                    "invalid": {
                        "tools": tools,
                        "inputs": {"function.update_plan": {"quality": "ultra"}},
                    }
                }
            )
    finally:
        tool_profiles_module.tool_profile_contract.cache_clear()


def test_bundled_profile_input_overrides_are_normalized_without_tool_states():
    name = "builtin"
    overrides = normalize_tool_profile_input_overrides(
        {
            name: {
                "hosted.web_search": {
                    "provider": "tavily",
                    "token": "profile-token",
                }
            }
        }
    )

    assert overrides[name]["hosted.web_search"] == {
        "provider": "tavily",
        "token": "profile-token",
        "guidance": "",
    }
    assert overrides[name]["namespace.image_gen.imagegen"] == {
        "base_url": "https://api.openai.com/v1",
        "token": "",
    }
    assert "tools" not in overrides[name]


def test_bundled_profile_input_overrides_reject_user_profiles():
    with pytest.raises(ValueError, match="may only contain bundled Profiles"):
        normalize_tool_profile_input_overrides({"custom": {}})


def test_bundled_profile_input_override_rejects_removed_web_run_inputs():
    name = "builtin"

    with pytest.raises(ValueError, match="unknown catalog IDs"):
        normalize_tool_profile_input_overrides(
            {
                name: {
                    "namespace.web.run": {
                        "provider": "tavily",
                        "token": "search-token",
                    }
                }
            }
        )


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        (
            {"type": "select", "default": "x"},
            "select options must be a non-empty list",
        ),
        (
            {
                "type": "select",
                "default": "missing",
                "options": [{"value": "x", "label": "X"}],
            },
            "select default must match an option value",
        ),
        (
            {
                "type": "select",
                "default": "x",
                "options": [
                    {"value": "x", "label": "X"},
                    {"value": "x", "label": "Duplicate"},
                ],
            },
            "duplicate select option value",
        ),
        (
            {"type": "text", "visible_when": ["modified"]},
            "visible_when contains unsupported states",
        ),
        (
            {"type": "text", "visible_when": "disabled"},
            "visible_when must be a list of strings",
        ),
        (
            {"type": "textarea", "readonly": "true"},
            "readonly must be boolean",
        ),
        (
            {"type": "text", "readonly": True},
            "readonly is only supported for type 'textarea'",
        ),
    ],
)
def test_function_profile_select_definition_validation(
    monkeypatch, definition, message
):
    catalog = copy.deepcopy(load_tool_catalog())
    function_item = next(
        item for item in catalog["items"] if item["id"] == "function.wait"
    )
    function_item["profile_inputs"] = [
        {
            "id": "mode",
            "label_i18n": "tools.input.mode",
            **definition,
        }
    ]
    monkeypatch.setattr(tool_profiles_module, "load_tool_catalog", lambda: catalog)
    tool_profiles_module.tool_profile_contract.cache_clear()
    try:
        with pytest.raises(ValueError, match=message):
            tool_profiles_module.tool_profile_contract()
    finally:
        tool_profiles_module.tool_profile_contract.cache_clear()


def test_description_visibility_defaults_to_all_states_and_supports_override(
    monkeypatch,
):
    catalog = copy.deepcopy(load_tool_catalog())
    item = next(
        item for item in catalog["items"] if item["id"] == "function.exec_command"
    )
    item.pop("description_i18n", None)
    item.pop("description_visible_when", None)
    item["description_i18n"] = "tools.description.exec_command"
    item["description_visible_when"] = ["modified"]
    monkeypatch.setattr(tool_profiles_module, "load_tool_catalog", lambda: catalog)
    tool_profiles_module.tool_profile_contract.cache_clear()
    try:
        tool_profiles_module.tool_profile_contract()
        item["description_visible_when"] = ["expanded"]
        tool_profiles_module.tool_profile_contract.cache_clear()
        with pytest.raises(ValueError, match="contains unsupported states"):
            tool_profiles_module.tool_profile_contract()
    finally:
        tool_profiles_module.tool_profile_contract.cache_clear()


def test_state_descriptions_require_supported_states_and_i18n_keys(monkeypatch):
    catalog = copy.deepcopy(load_tool_catalog())
    item = next(
        item for item in catalog["items"] if item["id"] == "function.exec_command"
    )
    item["state_descriptions_i18n"] = {"modified": "tools.description.wait"}
    monkeypatch.setattr(tool_profiles_module, "load_tool_catalog", lambda: catalog)
    tool_profiles_module.tool_profile_contract.cache_clear()
    try:
        tool_profiles_module.tool_profile_contract()
        item["state_descriptions_i18n"] = {"expanded": "tools.description.wait"}
        tool_profiles_module.tool_profile_contract.cache_clear()
        with pytest.raises(ValueError, match="contains unsupported states"):
            tool_profiles_module.tool_profile_contract()
        item["state_descriptions_i18n"] = {"modified": ""}
        tool_profiles_module.tool_profile_contract.cache_clear()
        with pytest.raises(ValueError, match="non-empty i18n keys"):
            tool_profiles_module.tool_profile_contract()
    finally:
        tool_profiles_module.tool_profile_contract.cache_clear()


def test_exec_projection_internal_when_disabled_must_be_boolean(monkeypatch):
    catalog = copy.deepcopy(load_tool_catalog())
    item = next(item for item in catalog["items"] if item["id"] == "custom.apply_patch")
    item["exec_projection"]["internal_when_disabled"] = "true"
    monkeypatch.setattr(tool_profiles_module, "load_tool_catalog", lambda: catalog)
    tool_profiles_module.tool_profile_contract.cache_clear()
    try:
        with pytest.raises(ValueError, match="internal_when_disabled must be boolean"):
            tool_profiles_module.tool_profile_contract()
    finally:
        tool_profiles_module.tool_profile_contract.cache_clear()


def test_internal_container_when_disabled_must_be_boolean(monkeypatch):
    catalog = copy.deepcopy(load_tool_catalog())
    item = next(item for item in catalog["items"] if item["id"] == "custom.exec")
    item["internal_container_when_disabled"] = "true"
    monkeypatch.setattr(tool_profiles_module, "load_tool_catalog", lambda: catalog)
    tool_profiles_module.tool_profile_contract.cache_clear()
    try:
        with pytest.raises(
            ValueError, match="internal_container_when_disabled must be boolean"
        ):
            tool_profiles_module.tool_profile_contract()
    finally:
        tool_profiles_module.tool_profile_contract.cache_clear()


@pytest.mark.parametrize("api_type", ["responses", "chat", "anthropic", "google"])
def test_gateway_config_resolves_group_profile_into_supported_route(api_type):
    tools = _profile(**{"function.update_plan": "disabled"})
    raw = {
        "providers": {
            "test": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com",
                "api_type": api_type,
            }
        },
        "tool_profiles": {
            "custom": {
                "tools": tools,
                "inputs": {
                    "namespace.image_gen.imagegen": {
                        "base_url": "https://images.example/v1",
                        "token": "image-token",
                    }
                },
            }
        },
        "model_groups": {
            "Test": {
                "provider": "test",
                "type": "llm",
                "tool_profile": "custom",
                "models": {"gpt-test": {}},
            }
        },
        "server": {
            "admin_password": "test-password",
            "api_keys": [{"id": "test", "key": "test-key"}],
        },
    }

    route, _provider = GatewayConfig(raw).resolve("openai_responses", "gpt-test")
    body = {"tools": [{"type": "function", "name": "update_plan", "parameters": {}}]}
    adapted = _apply_tool_adaptation(body, route)

    assert route.tool_profile_name == "custom"
    assert route.tool_profile["function.update_plan"] == "disabled"
    assert route.tool_profile_inputs["namespace.image_gen.imagegen"] == {
        "base_url": "https://images.example/v1",
        "token": "image-token",
    }
    assert "tools" not in adapted


def test_gateway_config_rejects_unknown_group_profile():
    raw = {
        "providers": {
            "test": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com",
                "api_type": "responses",
            }
        },
        "tool_profiles": {},
        "model_groups": {
            "Test": {
                "provider": "test",
                "type": "llm",
                "tool_profile": "missing",
                "models": {"gpt-test": {}},
            }
        },
        "server": {
            "admin_password": "test-password",
            "api_keys": [{"id": "test", "key": "test-key"}],
        },
    }

    with pytest.raises(ValueError, match="unknown tool profile 'missing'"):
        GatewayConfig(raw)


def test_gateway_config_resolves_bundled_profile_input_overrides():
    raw = {
        "providers": {
            "test": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com",
                "api_type": "responses",
            }
        },
        "tool_profile_input_overrides": {
            "builtin": {
                "hosted.web_search": {
                    "provider": "tavily",
                    "token": "builtin-search-token",
                }
            }
        },
        "model_groups": {
            "Test": {
                "provider": "test",
                "type": "llm",
                "tool_profile": "builtin",
                "models": {"gpt-test": {}},
            }
        },
        "server": {
            "admin_password": "test-password",
            "api_keys": [{"id": "test", "key": "test-key"}],
        },
    }

    route, _provider = GatewayConfig(raw).resolve("openai_responses", "gpt-test")

    assert route.tool_profile == tool_profile_contract()["builtin"]
    assert route.tool_profile_inputs["hosted.web_search"] == {
        "provider": "tavily",
        "token": "builtin-search-token",
        "guidance": "",
    }


def test_tool_mapping_only_provider_applies_selected_group_profile():
    tools = _profile(**{"function.update_plan": "disabled"})
    raw = {
        "providers": {
            "test": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com",
                "api_type": "responses",
            }
        },
        "tool_profiles": {"custom": {"tools": tools}},
        "model_groups": {
            "Test": {
                "provider": "test",
                "type": "llm",
                "tool_profile": "custom",
                "models": {"gpt-test": {}},
            }
        },
        "server": {
            "admin_password": "test-password",
            "api_keys": [{"id": "test", "key": "test-key"}],
        },
    }

    route, _provider = GatewayConfig(raw).resolve("openai_responses", "gpt-test")
    body = {"tools": [{"type": "function", "name": "update_plan", "parameters": {}}]}
    adapted = _apply_tool_adaptation(body, route)

    assert route.tool_profile_name == "custom"
    assert route.tool_profile["function.update_plan"] == "disabled"
    assert "tools" not in adapted


def test_bundled_profiles_expose_chat_and_responses_defaults():
    contract = tool_profile_contract()

    assert set(contract["readonly"]) == {
        "builtin",
        "openai-responses-tool-mapping-only",
        "web-run-injection",
        "responses-tool-mapping",
    }
    assert resolve_tool_profile("builtin", {}) == contract["builtin"]
    assert "hosted.image_generation" not in contract["builtin"]
    assert contract["builtin"]["hosted.web_search"] == "disabled"
    assert contract["builtin"]["custom.apply_patch"] == "disabled"
    assert contract["builtin"]["namespace.web.run"] == "modified"
    assert contract["readonly"]["web-run-injection"]["tools"] != contract["builtin"]
    assert (
        contract["readonly"]["web-run-injection"]["tools"]["namespace.web.run"]
        == "modified"
    )
    assert (
        contract["readonly"]["responses-tool-mapping"]["tools"] == contract["builtin"]
    )


def test_profile_filters_top_level_lite_and_namespace_children():
    profile = _profile(
        **{
            "function.update_plan": "disabled",
            "function.request_user_input": "disabled",
            "namespace.multi_agent_v2.wait_agent": "disabled",
        }
    )
    body = {
        "tools": [
            {"type": "function", "name": "update_plan", "parameters": {}},
            {
                "type": "namespace",
                "name": "collaboration",
                "tools": [
                    {"type": "function", "name": "spawn_agent", "parameters": {}},
                    {"type": "function", "name": "wait_agent", "parameters": {}},
                ],
            },
        ],
        "input": [
            {
                "type": "additional_tools",
                "tools": [
                    {
                        "type": "function",
                        "name": "request_user_input",
                        "parameters": {},
                    }
                ],
            }
        ],
        "tool_choice": {"type": "function", "name": "update_plan"},
    }

    adapted = _apply_tool_adaptation(body, _route(profile))

    assert adapted is not body
    assert [tool["type"] for tool in adapted["tools"]] == ["namespace"]
    assert [tool["name"] for tool in adapted["tools"][0]["tools"]] == ["spawn_agent"]
    assert adapted["input"] == []
    assert "tool_choice" not in adapted


def test_modified_profile_guidance_is_appended_to_direct_and_namespace_tools():
    profile = _profile(
        **{
            "function.create_goal": "modified",
            "namespace.multi_agent_v2.spawn_agent": "modified",
        }
    )
    route = _route(
        profile,
        {
            "function.create_goal": {"guidance": "Create only when required."},
            "namespace.multi_agent_v2.spawn_agent": {
                "guidance": "The child message is complete."
            },
        },
    )
    body = {
        "tools": [
            {
                "type": "function",
                "name": "create_goal",
                "description": "Create a goal.",
                "parameters": {},
            },
            {
                "type": "namespace",
                "name": "collaboration",
                "tools": [
                    {
                        "type": "function",
                        "name": "spawn_agent",
                        "description": "Spawn a child.",
                        "parameters": {},
                    }
                ],
            },
        ]
    }

    adapted = _apply_tool_adaptation(body, route)

    assert adapted["tools"][0]["description"] == (
        "Create a goal.\n\nCreate only when required."
    )
    assert adapted["tools"][1]["tools"][0]["description"] == (
        "Spawn a child.\n\nThe child message is complete."
    )


def test_modified_custom_exec_profile_appends_raw_javascript_guidance():
    route = _route(
        _profile(**{"custom.exec": "modified"}),
        {"custom.exec": {"guidance": "Pass raw JavaScript source only."}},
    )
    body = {
        "input": [
            {
                "type": "additional_tools",
                "tools": [
                    {
                        "type": "custom",
                        "name": "exec",
                        "description": "Run JavaScript.",
                        "format": {"type": "grammar"},
                    }
                ],
            }
        ]
    }

    adapted = _apply_tool_adaptation(body, route)

    assert adapted["input"][0]["tools"][0]["description"] == (
        "Run JavaScript.\n\nPass raw JavaScript source only."
    )


def test_disabled_custom_exec_survives_only_for_responses_to_chat_conversion():
    body = {
        "tools": [
            {
                "type": "custom",
                "name": "exec",
                "description": "Run JavaScript.",
                "format": {"type": "grammar"},
            }
        ]
    }
    profile = _profile(**{"custom.exec": "disabled"})

    chat_adapted = _apply_tool_adaptation(body, _route(profile))
    responses_route = ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_responses",
        provider_name="test",
        tool_profile_name="custom",
        tool_profile=profile,
    )
    responses_adapted = _apply_tool_adaptation(body, responses_route)

    assert chat_adapted is body
    assert "tools" not in responses_adapted


def test_modified_web_search_profile_only_changes_description_from_its_input():
    body = {
        "tools": [
            {
                "type": "web_search",
                "description": "Search current documentation.",
            }
        ]
    }
    route = _route(
        _profile(**{"hosted.web_search": "modified"}),
        {"hosted.web_search": {"guidance": "Prefer primary sources."}},
    )

    modified = _apply_tool_adaptation(body, route)
    passthrough = _apply_tool_adaptation(
        body, _route(_profile(**{"hosted.web_search": "passthrough"}))
    )

    assert modified["tools"][0]["description"] == (
        "Search current documentation.\n\nPrefer primary sources."
    )
    assert passthrough is body


def test_modified_web_search_profile_matches_preview_alias():
    body = {
        "tools": [
            {
                "type": "web_search_preview",
                "description": "Search current documentation.",
            }
        ]
    }
    route = _route(
        _profile(**{"hosted.web_search": "modified"}),
        {"hosted.web_search": {"guidance": "Prefer primary sources."}},
    )

    adapted = _apply_tool_adaptation(body, route)

    assert adapted["tools"][0]["description"] == (
        "Search current documentation.\n\nPrefer primary sources."
    )


def _web_run_tool(*commands: str) -> dict:
    command_properties = {
        "search_query": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "recency": {"type": "number"},
                },
                "required": ["q"],
            },
        },
        "click": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string"},
                    "id": {"type": "number"},
                },
                "required": ["ref_id", "id"],
            },
        },
        "open": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string"},
                    "lineno": {"type": "number"},
                },
                "required": ["ref_id"],
            },
        },
        "time": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"utc_offset": {"type": "string"}},
                "required": ["utc_offset"],
            },
        },
        "response_length": {
            "type": "string",
            "enum": ["short", "medium", "long"],
        },
        "finance": {
            "type": "array",
            "items": {"type": "object", "properties": {}},
        },
    }
    return {
        "type": "function",
        "name": "web__run",
        "description": "\n".join(f"Use `{command}`." for command in commands),
        "parameters": {
            "type": "object",
            "properties": {
                command: command_properties[command] for command in commands
            },
        },
    }


def _exec_web_run_description() -> str:
    return """Run JavaScript.

### `update_plan`
Update the plan.

exec tool declaration:
```ts
declare const tools: { update_plan(args: {}): Promise<unknown>; };
```

### `web__run`
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

exec tool declaration:
```ts
declare const tools: { web__run(args: {
  search_query?: Array<{ q: string; recency?: number; domains?: Array<string>; }>;
  image_query?: Array<{ q: string; }>;
  open?: Array<{ ref_id: string; lineno?: number; }>;
  click?: Array<{ ref_id: string; id: number; }>;
  find?: Array<{ ref_id: string; pattern: string; }>;
  screenshot?: Array<{ ref_id: string; pageno: number; }>;
  finance?: Array<{ ticker: string; }>;
  weather?: Array<{ location: string; }>;
  sports?: Array<{ fn: string; }>;
  time?: Array<{ utc_offset: string; }>;
  response_length?: "short" | "medium" | "long";
}): Promise<unknown>; };
```"""


def _projected_exec_web_commands(description: str) -> set[str]:
    definitions = project_exec_tool_definitions(
        description,
        {
            "web-run": ExecToolProjection(
                item_id="namespace.web.run",
                chat_name="web-run",
                nested_name="web__run",
            )
        },
    )
    return set(definitions["web-run"]["function"]["parameters"]["properties"])


def test_modified_web_run_removes_locally_unsupported_schema_branches():
    body = {"tools": [_web_run_tool("search_query", "click", "finance")]}
    route = _route(
        _profile(**{"namespace.web.run": "modified"}),
        tool_runtime_capabilities=frozenset({WEB_RUN_BASIC_SEARCH_CAPABILITY}),
    )

    adapted = _apply_tool_adaptation(body, route)

    function = adapted["tools"][0]
    assert set(function["parameters"]["properties"]) == {"search_query"}
    assert set(
        function["parameters"]["properties"]["search_query"]["items"]["properties"]
    ) == {"q"}
    assert "finance" not in function["description"]
    assert "click" not in function["description"]


def test_modified_web_run_without_tavily_keeps_only_static_capabilities():
    body = {
        "tools": [
            _web_run_tool(
                "search_query",
                "open",
                "time",
                "response_length",
            )
        ]
    }
    route = _route(_profile(**{"namespace.web.run": "modified"}))

    adapted = _apply_tool_adaptation(body, route)

    assert set(adapted["tools"][0]["parameters"]["properties"]) == {
        "open",
        "time",
        "response_length",
    }


def test_modified_web_run_removes_unknown_schema_and_guidance():
    tool = _web_run_tool("open")
    tool["description"] += "\nUse `future_command` for a future capability."
    tool["parameters"]["properties"]["future_command"] = {
        "type": "array",
        "items": {"type": "object", "properties": {}},
    }

    adapted = _apply_tool_adaptation(
        {"tools": [tool]},
        _route(_profile(**{"namespace.web.run": "modified"})),
    )

    function = adapted["tools"][0]
    assert set(function["parameters"]["properties"]) == {"open"}
    assert "future_command" not in function["description"]


def test_modified_web_run_keeps_supported_browser_branches_with_sidecar():
    body = {"tools": [_web_run_tool("click", "finance")]}
    route = _route(_profile(**{"namespace.web.run": "modified"}))
    object.__setattr__(
        route,
        "tool_runtime_capabilities",
        frozenset({WEB_RUN_SIDECAR_CAPABILITY}),
    )

    adapted = _apply_tool_adaptation(body, route)

    assert set(adapted["tools"][0]["parameters"]["properties"]) == {"click"}


def test_passthrough_web_run_keeps_complete_schema():
    body = {"tools": [_web_run_tool("search_query", "finance")]}
    route = _route(_profile(**{"namespace.web.run": "passthrough"}))

    assert _apply_tool_adaptation(body, route) is body


def test_modified_web_run_without_supported_schema_fails_closed():
    body = {"tools": [_web_run_tool("finance")]}
    route = _route(_profile(**{"namespace.web.run": "modified"}))

    adapted = _apply_tool_adaptation(body, route)

    assert "tools" not in adapted


def test_tool_mapping_only_modified_web_run_rewrites_nested_exec_description():
    description = _exec_web_run_description()
    body = {"tools": [{"type": "custom", "name": "exec", "description": description}]}
    route = _route(
        _profile(
            **{
                "custom.exec": "passthrough",
                "namespace.web.run": "modified",
            }
        ),
        target_provider="openai_responses",
        tool_runtime_capabilities=frozenset({WEB_RUN_BASIC_SEARCH_CAPABILITY}),
    )

    adapted = _apply_tool_adaptation(body, route)

    projected_description = adapted["tools"][0]["description"]
    assert _projected_exec_web_commands(projected_description) == {
        "search_query",
        "open",
        "time",
        "response_length",
    }
    assert "recency" not in projected_description
    assert "### `update_plan`" in projected_description


def test_tool_mapping_only_modified_web_run_rewrites_lite_additional_tools():
    body = {
        "input": [
            {
                "type": "additional_tools",
                "tools": [
                    {
                        "type": "custom",
                        "name": "exec",
                        "description": _exec_web_run_description(),
                    }
                ],
            }
        ]
    }
    route = _route(
        _profile(
            **{
                "custom.exec": "passthrough",
                "namespace.web.run": "modified",
            }
        ),
        target_provider="openai_responses",
        tool_runtime_capabilities=frozenset({WEB_RUN_SIDECAR_CAPABILITY}),
    )

    adapted = _apply_tool_adaptation(body, route)

    projected_description = adapted["input"][0]["tools"][0]["description"]
    assert _projected_exec_web_commands(projected_description) == {
        "open",
        "click",
        "find",
        "screenshot",
        "time",
        "response_length",
    }


def test_tool_mapping_only_passthrough_web_run_keeps_nested_exec_description():
    description = _exec_web_run_description()
    body = {"tools": [{"type": "custom", "name": "exec", "description": description}]}
    route = _route(
        _profile(
            **{
                "custom.exec": "passthrough",
                "namespace.web.run": "passthrough",
            }
        ),
        target_provider="openai_responses",
    )

    assert _apply_tool_adaptation(body, route) is body


def test_chat_default_disables_hosted_web_search():
    body = {
        "tools": [
            {"type": "web_search", "description": "Search current documentation."},
            {"type": "function", "name": "wait", "parameters": {}},
        ]
    }

    adapted = _apply_tool_adaptation(body, _route(tool_profile_contract()["builtin"]))

    assert adapted["tools"] == [{"type": "function", "name": "wait", "parameters": {}}]


def test_profile_limits_localized_native_and_injected_tools():
    profile = _profile()
    for item_id in copy.copy(profile):
        if item_id.startswith("injection."):
            profile[item_id] = "disabled"
    profile["injection.claude_code.read"] = "injected"
    profile["function.exec_command"] = "modified"
    profile["function.shell_command"] = "passthrough"
    body = {
        "tools": [
            {
                "type": "function",
                "function": {"name": "exec_command", "parameters": {}},
            },
            {
                "type": "function",
                "function": {"name": "shell_command", "parameters": {}},
            },
        ]
    }

    adapted = _apply_converted_request_tool_adaptation(body, _route(profile))
    names = [tool["function"]["name"] for tool in adapted["tools"]]

    # Modified exec projections preserve an already-direct normal function;
    # projection is only added when Codex supplied the custom exec surface.
    assert "exec_command" in names
    assert "shell_command" in names
    assert "Read" in names
    assert "Edit" not in names


def test_injected_state_adds_selected_alias_without_modifying_native_tool():
    profile = _profile()
    for item_id in copy.copy(profile):
        if item_id.startswith("injection."):
            profile[item_id] = "disabled"
    profile["injection.claude_code.read"] = "injected"
    profile["function.exec_command"] = "passthrough"
    profile["function.write_stdin"] = "passthrough"
    profile["function.shell_command"] = "passthrough"
    profile["custom.apply_patch"] = "passthrough"
    body = {
        "tools": [
            {
                "type": "function",
                "function": {"name": "exec_command", "parameters": {}},
            }
        ]
    }

    adapted = _apply_converted_request_tool_adaptation(body, _route(profile))
    names = [tool["function"]["name"] for tool in adapted["tools"]]

    assert names == ["exec_command", "Read"]


def test_resolve_builtin_profile_returns_independent_copy():
    first = resolve_tool_profile("builtin", {})
    second = resolve_tool_profile("builtin", {})

    first["function.update_plan"] = "disabled"

    assert second["function.update_plan"] == "passthrough"
