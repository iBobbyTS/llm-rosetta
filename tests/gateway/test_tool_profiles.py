"""Tool-profile configuration and request-policy tests."""

from __future__ import annotations

import copy

import pytest

from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.admin.tool_catalog import load_tool_catalog
from codex_rosetta.gateway import tool_profiles as tool_profiles_module
from codex_rosetta.gateway.proxy import (
    _apply_converted_request_tool_adaptation,
    _apply_tool_adaptation,
)
from codex_rosetta.gateway.tool_profiles import (
    normalize_tool_profile_input_overrides,
    normalize_tool_profile_documents,
    resolve_tool_profile,
    resolve_tool_profile_inputs,
    tool_profile_contract,
)
from codex_rosetta.routing import ResolvedRoute


def _profile(**overrides: str) -> dict[str, str]:
    profile = dict(tool_profile_contract()["builtin"])
    profile.update(overrides)
    return profile


def _route(
    profile: dict[str, str],
    inputs: dict[str, dict[str, str]] | None = None,
) -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="test",
        tool_profile_name="custom",
        tool_profile=profile,
        tool_profile_inputs=inputs or {},
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
    assert set(contract["readonly"]) == {"builtin"}
    assert all(
        contract["builtin"][child_id] == "disabled"
        for child_id in contract["namespace_children"]["namespace.multi_agent_v1"]
    )
    assert contract["supported"]["injection.claude_code.read"] == (
        "disabled",
        "injected",
    )
    assert contract["builtin"]["injection.claude_code.read"] == "injected"


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


def test_bundled_profile_input_override_changes_fields_but_not_tool_states():
    name = "builtin"
    tools_before = resolve_tool_profile(name, {})
    overrides = normalize_tool_profile_input_overrides(
        {
            name: {
                "namespace.web.run": {
                    "provider": "tavily",
                    "token": "search-token",
                }
            }
        }
    )

    inputs = resolve_tool_profile_inputs(name, {}, overrides)

    assert inputs["namespace.web.run"]["token"] == "search-token"
    assert resolve_tool_profile(name, {}) == tools_before
    assert tools_before["namespace.web.run"] == "modified"


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


@pytest.mark.parametrize(
    "api_type", ["responses_rosetta", "chat", "anthropic", "google"]
)
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
                "models": {"gpt-test": {"capabilities": ["text"]}},
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
                "api_type": "responses_rosetta",
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
                "api_type": "responses_rosetta",
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
                "models": {"gpt-test": {"capabilities": ["text"]}},
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
                "api_type": "responses_passthrough",
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

    assert route.responses_processing == "passthrough"
    assert route.tool_profile_name == "custom"
    assert route.tool_profile["function.update_plan"] == "disabled"
    assert "tools" not in adapted


def test_chat_default_is_the_only_bundled_profile():
    contract = tool_profile_contract()

    assert set(contract["readonly"]) == {"builtin"}
    assert resolve_tool_profile("builtin", {}) == contract["builtin"]
    assert "hosted.image_generation" not in contract["builtin"]
    assert contract["builtin"]["custom.apply_patch"] == "disabled"
    assert contract["builtin"]["namespace.web.run"] == "modified"


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
