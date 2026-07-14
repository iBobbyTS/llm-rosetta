"""Contract tests for the Admin tool catalog and profiles."""

from __future__ import annotations

import asyncio
import json

from codex_rosetta._vendor.httpserver import Request
from codex_rosetta.gateway.admin.static import load_admin_html
from codex_rosetta.gateway.admin.tool_catalog import load_tool_catalog
from codex_rosetta.gateway.app import create_app
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.tool_profiles import tool_profile_contract

CODEX_0_144_4_SOURCE_COMMIT = "8c68d4c87dc54d38861f5114e920c3de2efa5876"

EXPECTED_FUNCTIONS = {
    "create_goal",
    "exec_command",
    "get_context_remaining",
    "get_goal",
    "list_available_plugins_to_install",
    "list_mcp_resource_templates",
    "list_mcp_resources",
    "new_context",
    "read_mcp_resource",
    "report_agent_job_result",
    "request_permissions",
    "request_plugin_install",
    "request_user_input",
    "shell_command",
    "spawn_agents_on_csv",
    "test_sync_tool",
    "update_goal",
    "update_plan",
    "view_image",
    "wait",
    "wait_for_environment",
    "write_stdin",
}

EXPECTED_NAMESPACE_CHILDREN = {
    "multi_agent_v1": {
        "close_agent",
        "resume_agent",
        "send_input",
        "spawn_agent",
        "wait_agent",
    },
    "collaboration": {
        "followup_task",
        "interrupt_agent",
        "list_agents",
        "send_message",
        "spawn_agent",
        "wait_agent",
    },
}

EXPECTED_EXEC_TOOLS = {
    "apply_patch",
    "clock__curr_time",
    "clock__sleep",
    "create_goal",
    "exec_command",
    "get_goal",
    "image_gen__imagegen",
    "memories__add_ad_hoc_note",
    "memories__list",
    "memories__read",
    "memories__search",
    "skills__list",
    "skills__read",
    "update_goal",
    "update_plan",
    "view_image",
    "web__run",
    "write_stdin",
}


def _catalog_maps():
    catalog = load_tool_catalog()
    items = {item["id"]: item for item in catalog["items"]}
    policies = {policy["id"]: policy for policy in catalog["policies"]}
    groups = {
        group["id"]: group["item_ids"] for group in catalog["placements"]["groups"]
    }
    namespaces = {
        placement["namespace_id"]: placement["child_ids"]
        for placement in catalog["placements"]["namespaces"]
    }
    return catalog, items, policies, groups, namespaces


def _make_app():
    config = GatewayConfig(
        {
            "providers": {
                "test-provider": {
                    "api_key": "sk-test",
                    "base_url": "https://api.example.test/v1",
                    "type": "openai",
                }
            },
            "model_groups": {
                "test": {
                    "provider": "test-provider",
                    "type": "llm",
                    "models": {"gpt-test": {}},
                }
            },
            "server": {
                "admin_password": "test-admin-password",
                "api_keys": [
                    {
                        "id": "test-client",
                        "label": "Test client",
                        "key": "test-gateway-key",
                    }
                ],
            },
        }
    )
    return create_app(config)


def _request(app, method: str = "GET") -> Request:
    return Request(
        method=method,
        path="/admin/api/tools/catalog",
        query_string="",
        headers={"x-admin-token": app.auth_state.admin_token},
        body=b"",
        client_addr=("127.0.0.1", 12345),
        app=app,
    )


def _api_request(app, method: str, path: str, body: dict | None = None) -> Request:
    return Request(
        method=method,
        path=path,
        query_string="",
        headers={"x-admin-token": app.auth_state.admin_token},
        body=json.dumps(body or {}).encode(),
        client_addr=("127.0.0.1", 12345),
        app=app,
    )


def test_catalog_has_unique_resolvable_ids_and_policies():
    catalog, items, policies, groups, namespaces = _catalog_maps()

    assert len(items) == len(catalog["items"])
    assert len(policies) == len(catalog["policies"])

    referenced = {item_id for item_ids in groups.values() for item_id in item_ids}
    referenced.update(namespaces)
    referenced.update(
        child_id for child_ids in namespaces.values() for child_id in child_ids
    )
    referenced.update(catalog["custom_injection"]["item_ids"])
    assert referenced == items.keys()

    for item in catalog["items"]:
        if item["type"] == "custom_injection":
            assert "policy_id" not in item
            continue
        policy = policies[item["policy_id"]]
        supported = (
            policy.get("namespace_supported", policy["supported"])
            if item["type"] == "namespace"
            else policy["supported"]
        )
        assert {"disabled", "passthrough"} <= set(supported)
        assert policy["default"] in supported


def test_catalog_contains_all_fixed_tools_and_excludes_dynamic_search():
    catalog, items, _policies, groups, namespaces = _catalog_maps()

    assert {items[item_id]["name"] for item_id in groups["exec_expansion"]} == (
        EXPECTED_EXEC_TOOLS
    )
    assert {items[item_id]["name"] for item_id in groups["function"]} == (
        (EXPECTED_FUNCTIONS - EXPECTED_EXEC_TOOLS) | {"exec", "web_search"}
    )
    assert {items[item_id]["name"] for item_id in groups["namespace"]} == {
        "multi_agent_v1",
        "collaboration",
    }
    assert all(
        items[item_id]["type"] != "namespace" for item_id in groups["exec_expansion"]
    )
    assert all(
        "namespace_id" not in items[item_id] for item_id in groups["exec_expansion"]
    )
    for removed_parent in (
        "namespace.clock",
        "namespace.image_gen",
        "namespace.web",
        "namespace.memories",
        "namespace.skills",
        "namespace.mcp_github",
    ):
        assert removed_parent not in items
    assert [items[item_id]["name"] for item_id in groups["rosetta_injection"]] == [
        "Read",
        "Glob",
        "Grep",
        "Edit",
        "Write",
    ]
    assert set(groups) == {
        "exec_expansion",
        "function",
        "namespace",
        "rosetta_injection",
    }

    actual_namespace_children = {
        items[namespace_id]["name"]: {items[child_id]["name"] for child_id in child_ids}
        for namespace_id, child_ids in namespaces.items()
    }
    assert actual_namespace_children == EXPECTED_NAMESPACE_CHILDREN

    serialized = json.dumps(catalog)
    assert "tool_search" not in serialized
    assert '"codex_app"' not in serialized
    assert "mcp__codex_apps__github" not in serialized
    assert "github" not in serialized.lower()


def test_catalog_defaults_and_namespace_image_policy():
    catalog, items, policies, _groups, _namespaces = _catalog_maps()

    assert catalog["metadata"]["schema_version"] == 3
    assert catalog["metadata"]["catalog_version"] == "codex-0.144.4"
    assert catalog["metadata"]["codex_cli_version"] == "0.144.4"
    assert catalog["metadata"]["codex_source_commit"] == CODEX_0_144_4_SOURCE_COMMIT
    assert catalog["metadata"]["profile_selection"] == "model_group"
    assert catalog["builtin_profile"]["id"] == "builtin"
    assert catalog["builtin_profile"]["name"] == "Chat Default"
    assert catalog["builtin_profile"]["tools"] == {
        "namespace.multi_agent_v1": "disabled",
        "custom.apply_patch": "disabled",
        "custom.exec": "disabled",
    }
    assert "namespace.mcp_github" not in catalog["builtin_profile"]["inputs"]
    assert catalog["preset_profiles"] == []

    assert policies[items["custom.apply_patch"]["policy_id"]]["default"] == ("disabled")
    assert "hosted.image_generation" not in items
    image_policy = items["namespace.image_gen.imagegen"]["policy_id"]
    assert policies[image_policy]["default"] == "disabled"
    assert policies[image_policy]["supported"] == [
        "disabled",
        "passthrough",
        "modified",
    ]
    assert items["namespace.image_gen.imagegen"]["profile_inputs"] == [
        {
            "id": "base_url",
            "label_i18n": "tools.input.image_gen.base_url",
            "placeholder_i18n": "tools.input.image_gen.base_url_placeholder",
            "default": "https://api.openai.com/v1",
            "visible_when": ["modified"],
        },
        {
            "id": "token",
            "label_i18n": "tools.input.image_gen.token",
            "placeholder_i18n": "tools.input.image_gen.token_placeholder",
            "type": "password",
            "default": "",
            "visible_when": ["modified"],
        },
    ]
    search_inputs = [
        {
            "id": "provider",
            "label_i18n": "tools.input.web_search.provider",
            "type": "select",
            "default": "tavily",
            "visible_when": ["modified"],
            "options": [{"value": "tavily", "label": "Tavily"}],
        },
        {
            "id": "token",
            "label_i18n": "tools.input.web_search.token",
            "placeholder_i18n": "tools.input.web_search.token_placeholder",
            "type": "password",
            "default": "",
            "visible_when": ["modified"],
        },
        {
            "id": "guidance",
            "label_i18n": "tools.input.guidance",
            "default": "",
            "visible_when": ["modified"],
            "ui_hidden": True,
        },
    ]
    assert items["hosted.web_search"]["profile_inputs"] == search_inputs
    web_run_inputs = [dict(input_definition) for input_definition in search_inputs[:2]]
    web_run_inputs[1] = {
        **web_run_inputs[1],
        "label_i18n": "tools.input.web_run.token",
        "placeholder_i18n": "tools.input.web_run.token_placeholder",
    }
    assert items["namespace.web.run"]["profile_inputs"] == web_run_inputs
    assert items["custom.exec"]["profile_inputs"] == [
        {
            "id": "guidance",
            "label_i18n": "tools.input.guidance",
            "default": "",
            "visible_when": ["modified"],
            "ui_hidden": True,
        }
    ]

    for namespace_id in _namespaces:
        namespace = items[namespace_id]
        assert namespace["default_expanded"] is True

    modified = {
        "function.request_user_input",
        "function.create_goal",
        "function.update_goal",
        "namespace.multi_agent_v2.list_agents",
        "namespace.multi_agent_v2.send_message",
        "namespace.multi_agent_v2.spawn_agent",
        "namespace.multi_agent_v2.wait_agent",
        "hosted.web_search",
        "namespace.web.run",
    }
    assert {
        item_id
        for item_id, item in items.items()
        if item.get("policy_id")
        and policies[item["policy_id"]]["default"] == "modified"
    } == modified

    assert items["function.request_user_input"]["description_i18n"] == (
        "tools.description.append_guidance"
    )
    assert items["function.request_user_input"]["description_visible_when"] == [
        "modified"
    ]
    assert (
        items["function.request_user_input"]["profile_inputs"][0]["ui_hidden"] is True
    )

    builtin = tool_profile_contract()["builtin"]
    assert builtin["namespace.multi_agent_v1"] == "disabled"
    assert all(
        builtin[child_id] == "disabled"
        for child_id in _namespaces["namespace.multi_agent_v1"]
    )
    assert builtin["namespace.multi_agent_v2"] == "expanded"
    assert builtin["function.exec_command"] == "passthrough"
    assert builtin["function.write_stdin"] == "passthrough"
    assert builtin["custom.apply_patch"] == "disabled"
    assert builtin["custom.exec"] == "disabled"
    assert items["custom.exec"]["internal_container_when_disabled"] is True
    assert items["custom.exec"]["description_i18n"] == (
        "tools.description.exec_disabled"
    )
    assert items["custom.exec"]["description_visible_when"] == ["disabled"]
    assert tool_profile_contract()["internal_containers_when_disabled"] == frozenset(
        {"custom.exec"}
    )
    assert builtin["function.shell_command"] == "disabled"
    for item_id in ("function.create_goal", "function.update_goal"):
        assert "description_i18n" not in items[item_id]
        assert "description_visible_when" not in items[item_id]
        assert items[item_id]["profile_inputs"] == [
            {
                "id": "guidance",
                "label_i18n": "tools.input.appended_description_guidance",
                "type": "textarea",
                "default": "",
                "visible_when": ["modified"],
                "readonly": True,
            }
        ]
    for item_id in (
        "function.exec_command",
        "function.write_stdin",
        "function.update_plan",
        "function.view_image",
        "function.get_goal",
        "namespace.clock.curr_time",
        "namespace.clock.sleep",
        "namespace.memories.add_ad_hoc_note",
        "namespace.memories.list",
        "namespace.memories.read",
        "namespace.memories.search",
        "namespace.skills.list",
        "namespace.skills.read",
    ):
        assert builtin[item_id] == "passthrough"
        assert "description_i18n" not in items[item_id]
        assert "description_visible_when" not in items[item_id]
    for item_id in (
        "function.exec_command",
        "function.write_stdin",
        "function.shell_command",
    ):
        assert policies[items[item_id]["policy_id"]]["supported"] == [
            "disabled",
            "passthrough",
            "modified",
        ]

    assert set(tool_profile_contract()["exec_projections"]) == {
        "custom.apply_patch",
        "function.create_goal",
        "function.exec_command",
        "function.get_goal",
        "function.update_goal",
        "function.update_plan",
        "function.view_image",
        "function.write_stdin",
        "namespace.clock.curr_time",
        "namespace.clock.sleep",
        "namespace.image_gen.imagegen",
        "namespace.memories.add_ad_hoc_note",
        "namespace.memories.list",
        "namespace.memories.read",
        "namespace.memories.search",
        "namespace.skills.list",
        "namespace.skills.read",
        "namespace.web.run",
    }
    assert (
        tool_profile_contract()["exec_projections"]["custom.apply_patch"][
            "internal_when_disabled"
        ]
        is True
    )

    web_search_policy = policies[items["hosted.web_search"]["policy_id"]]
    assert web_search_policy["route_defaults"] == [
        {"route": "responses_to_responses", "policy": "passthrough"},
        {
            "route": "responses_to_chat",
            "policy": "modified",
            "fallback": {
                "when": "tavily_unavailable",
                "policy": "disabled",
            },
        },
    ]

    assert [
        items[item_id]["name"] for item_id in catalog["custom_injection"]["item_ids"]
    ] == [
        "Read",
        "Glob",
        "Grep",
        "Edit",
        "Write",
    ]


def test_catalog_api_is_read_only_and_returns_bundled_resource():
    app = _make_app()

    response = asyncio.run(app._dispatch(_request(app)))
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json; charset=utf-8"
    assert json.loads(response.body) == load_tool_catalog()

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = asyncio.run(app._dispatch(_request(app, method)))
        assert response.status_code == 405


def test_admin_tools_view_has_profile_editor_and_all_filters():
    html = load_admin_html()
    page = html.split('id="page-tools"', 1)[1].split("<!-- Dashboard Page -->", 1)[0]

    assert 'href="/admin/tools"' in html
    assert "api.get('/admin/api/tools/catalog')" in html
    for filter_name in (
        "all",
        "exec_expansion",
        "function",
        "namespace",
        "rosetta_injection",
    ):
        assert f'data-tool-filter="{filter_name}"' in page

    assert 'id="toolProfileSelect"' in page
    assert 'id="saveToolProfileBtn"' in page
    assert 'onclick="openToolProfileCloneModal()"' in page
    assert "updateToolProfileState" in html
    assert 'type="checkbox"' not in page
    assert "tools.disabledHint" in page
    assert "item.description_i18n" in html
    assert "item.profile_inputs" in html
    assert "renderToolProfileInputs(item)" in html
    assert "['function', 'custom', 'hosted', 'namespace'].includes(item.type)" in html
    assert "updateToolProfileInput" in html
    assert "saveToolProfileBtn').disabled = !toolProfileDirty" in html
    assert "if (currentToolProfile()?.readonly) return;" in html
    assert "if (!profile || profile.readonly)" not in html
    assert "namespaceDisabled" in html
    assert "toolProfileDraft[item.namespace_id] === 'disabled'" in html
    assert (
        "for (const childId of childIds) toolProfileDraft[childId] = 'disabled';"
        in html
    )
    assert "currentToolProfile()?.readonly || namespaceDisabled" in html
    assert "input.type === 'password'" in html
    assert "input.type === 'select'" in html
    assert "input.type === 'textarea'" in html
    assert '<textarea class="tool-profile-input tool-profile-textarea"' in html
    assert "input.readonly ? ' readonly' : ''" in html
    assert "option.value === value" in html
    assert "input.visible_when" in html
    assert "!input.ui_hidden" in html
    assert "item.description_visible_when" in html
    assert "renderToolNamespace(item, placement.child_ids, index)" in html
    assert "toolPolicyLabel(item, state)" in html
    assert "description + renderToolProfileInputs(namespaceItem)" in html
    assert "isToolCardContentVisible" in html
    assert "renderToolCatalog();" in html
    assert "${esc(option.label)}" in html
    assert '<select class="tool-profile-input"' in html
    assert "inputs: toolProfileInputDraft" in html
    assert "toolCatalogFilter === 'all' || toolCatalogFilter === 'namespace'" in html
    assert "if (item.type === 'namespace') expandedToolNamespaces.add(item.id);" in html
    assert "item.default_expanded" not in html
    assert "api.get('/admin/api/tools/profiles')" in html
    assert 'href="/admin/web-search"' not in html
    assert 'id="page-web-search"' not in html
    assert "saveWebSearchSettings" not in html


def test_admin_tool_profile_crud_and_reference_guard(tmp_path):
    raw = {
        "providers": {
            "test-provider": {
                "api_key": "sk-test",
                "base_url": "https://api.example.test/v1",
                "provider": "custom",
                "api_type": "responses_rosetta",
            }
        },
        "tool_profiles": {},
        "model_groups": {
            "Test": {
                "provider": "test-provider",
                "type": "llm",
                "tool_profile": "builtin",
                "models": {"gpt-test": {"capabilities": ["text"]}},
            }
        },
        "server": {
            "admin_password": "test-admin-password",
            "api_keys": [{"id": "test-client", "label": "Test", "key": "test-key"}],
        },
    }
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    app = create_app(GatewayConfig(raw), str(config_path))
    tools = dict(tool_profile_contract()["builtin"])
    tools["function.update_plan"] = "disabled"

    response = asyncio.run(
        app._dispatch(_api_request(app, "GET", "/admin/api/tools/profiles"))
    )
    assert response.status_code == 200
    profiles = json.loads(getattr(response, "body"))["profiles"]
    assert [(profile["id"], profile["readonly"]) for profile in profiles] == [
        ("builtin", True)
    ]

    response = asyncio.run(
        app._dispatch(
            _api_request(
                app,
                "PUT",
                "/admin/api/tools/profiles/builtin",
                {"tools": tools},
            )
        )
    )
    assert response.status_code == 400

    builtin_tools = dict(tool_profile_contract()["readonly"]["builtin"]["tools"])
    response = asyncio.run(
        app._dispatch(
            _api_request(
                app,
                "PUT",
                "/admin/api/tools/profiles/builtin",
                {
                    "tools": builtin_tools,
                    "inputs": {
                        "hosted.web_search": {
                            "provider": "tavily",
                            "token": "bundled-profile-token",
                        }
                    },
                },
            )
        )
    )
    assert response.status_code == 200

    response = asyncio.run(
        app._dispatch(_api_request(app, "GET", "/admin/api/tools/profiles"))
    )
    builtin = next(
        profile
        for profile in json.loads(getattr(response, "body"))["profiles"]
        if profile["id"] == "builtin"
    )
    assert builtin["inputs"]["hosted.web_search"]["token"] == ("bundled-profile-token")
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["tool_profile_input_overrides"]["builtin"] == builtin["inputs"]
    assert "builtin" not in saved["tool_profiles"]

    response = asyncio.run(
        app._dispatch(
            _api_request(
                app,
                "PUT",
                "/admin/api/tools/profiles/restricted",
                {"tools": tools, "inputs": {}},
            )
        )
    )
    assert response.status_code == 200

    response = asyncio.run(
        app._dispatch(_api_request(app, "GET", "/admin/api/tools/profiles"))
    )
    restricted = next(
        profile
        for profile in json.loads(getattr(response, "body"))["profiles"]
        if profile["id"] == "restricted"
    )
    expected_inputs = {
        item_id: {
            input_id: definition["default"]
            for input_id, definition in definitions.items()
        }
        for item_id, definitions in tool_profile_contract()["input_definitions"].items()
    }
    assert restricted["inputs"] == expected_inputs
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["tool_profiles"]["restricted"]["inputs"] == expected_inputs

    response = asyncio.run(
        app._dispatch(
            _api_request(
                app,
                "PUT",
                "/admin/api/config/model-groups/Test",
                {
                    "provider": "test-provider",
                    "type": "llm",
                    "tool_profile": "restricted",
                    "models": {"gpt-test": {"capabilities": ["text"]}},
                },
            )
        )
    )
    assert response.status_code == 200
    route, _provider = getattr(app, "gateway_config").resolve(
        "openai_responses", "gpt-test"
    )
    assert route.tool_profile_name == "restricted"
    assert route.tool_profile["function.update_plan"] == "disabled"

    response = asyncio.run(
        app._dispatch(
            _api_request(
                app,
                "DELETE",
                "/admin/api/tools/profiles/restricted",
            )
        )
    )
    assert response.status_code == 409
    assert json.loads(getattr(response, "body"))["model_groups"] == ["Test"]
