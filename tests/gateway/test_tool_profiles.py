"""Tool-profile configuration and request-policy tests."""

from __future__ import annotations

import copy

import pytest

from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.proxy import (
    _apply_converted_request_tool_adaptation,
    _apply_tool_adaptation,
)
from codex_rosetta.gateway.tool_profiles import (
    resolve_tool_profile,
    tool_profile_contract,
)
from codex_rosetta.routing import ResolvedRoute


def _profile(**overrides: str) -> dict[str, str]:
    profile = dict(tool_profile_contract()["builtin"])
    profile.update(overrides)
    return profile


def _route(profile: dict[str, str]) -> ResolvedRoute:
    return ResolvedRoute(
        source_provider="openai_responses",
        target_provider="openai_chat",
        provider_name="test",
        tool_profile_name="custom",
        tool_profile=profile,
    )


def test_builtin_profile_covers_catalog_with_type_specific_states():
    contract = tool_profile_contract()

    assert set(contract["builtin"]) == set(contract["supported"])
    assert contract["supported"]["namespace.clock"] == ("disabled", "expanded")
    assert contract["builtin"]["namespace.clock"] == "expanded"
    assert contract["supported"]["injection.claude_code.read"] == (
        "disabled",
        "injected",
    )
    assert contract["builtin"]["injection.claude_code.read"] == "injected"


def test_gateway_config_resolves_group_profile_into_route():
    tools = _profile(**{"function.update_plan": "disabled"})
    raw = {
        "providers": {
            "test": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com",
                "api_type": "chat",
            }
        },
        "tool_profiles": {"custom": {"tools": tools}},
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

    assert route.tool_profile_name == "custom"
    assert route.tool_profile["function.update_plan"] == "disabled"


def test_gateway_config_rejects_unknown_group_profile():
    raw = {
        "providers": {
            "test": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com",
                "api_type": "chat",
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


def test_profile_filters_top_level_lite_and_namespace_children():
    profile = _profile(
        **{
            "function.update_plan": "disabled",
            "function.request_user_input": "disabled",
            "namespace.clock.sleep": "disabled",
        }
    )
    body = {
        "tools": [
            {"type": "function", "name": "update_plan", "parameters": {}},
            {
                "type": "namespace",
                "name": "clock",
                "tools": [
                    {"type": "function", "name": "curr_time", "parameters": {}},
                    {"type": "function", "name": "sleep", "parameters": {}},
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
    assert [tool["name"] for tool in adapted["tools"][0]["tools"]] == ["curr_time"]
    assert adapted["input"] == []
    assert "tool_choice" not in adapted


def test_profile_limits_localized_native_and_injected_tools():
    profile = _profile()
    for item_id in copy.copy(profile):
        if item_id.startswith("injection."):
            profile[item_id] = "disabled"
    profile["injection.claude_code.bash"] = "injected"
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

    assert "exec_command" not in names
    assert "shell_command" in names
    assert "Bash" in names
    assert "Read" not in names


def test_injected_state_adds_selected_alias_without_modifying_native_tool():
    profile = _profile()
    for item_id in copy.copy(profile):
        if item_id.startswith("injection."):
            profile[item_id] = "disabled"
    profile["injection.claude_code.bash"] = "injected"
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

    assert names == ["exec_command", "Bash"]


def test_resolve_builtin_profile_returns_independent_copy():
    first = resolve_tool_profile("builtin", {})
    second = resolve_tool_profile("builtin", {})

    first["function.update_plan"] = "disabled"

    assert second["function.update_plan"] == "passthrough"
