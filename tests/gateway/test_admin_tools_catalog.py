"""Contract tests for the Admin tool catalog and profiles."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from codex_rosetta._vendor.httpserver import Request
from codex_rosetta.gateway.admin.static import load_admin_html
from codex_rosetta.gateway.admin.tool_catalog import load_tool_catalog
from codex_rosetta.gateway.app import create_app
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.tool_profiles import tool_profile_contract

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = (
    REPO_ROOT / "docs" / "dev" / "version-compatibility" / "codex-source-contract.json"
)

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
    "clock": {"curr_time", "sleep"},
    "image_gen": {"imagegen"},
    "web": {"run"},
    "memories": {"add_ad_hoc_note", "list", "read", "search"},
    "skills": {"list", "read"},
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
    assert referenced <= items.keys()

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

    assert {items[item_id]["name"] for item_id in groups["function"]} == (
        EXPECTED_FUNCTIONS
    )
    assert {items[item_id]["name"] for item_id in groups["custom"]} == {
        "apply_patch",
        "exec",
    }
    assert {items[item_id]["name"] for item_id in groups["hosted"]} == {
        "web_search",
        "image_generation",
    }
    assert {items[item_id]["name"] for item_id in groups["namespace"]} == set(
        EXPECTED_NAMESPACE_CHILDREN
    )

    actual_namespace_children = {
        items[namespace_id]["name"]: {items[child_id]["name"] for child_id in child_ids}
        for namespace_id, child_ids in namespaces.items()
    }
    assert actual_namespace_children == EXPECTED_NAMESPACE_CHILDREN

    serialized = json.dumps(catalog)
    assert "tool_search" not in serialized
    assert "codex_app" not in serialized
    assert "mcp__" not in serialized


def test_catalog_defaults_and_shared_image_policy():
    catalog, items, policies, _groups, _namespaces = _catalog_maps()

    assert catalog["metadata"]["schema_version"] == 1
    assert catalog["metadata"]["codex_cli_version"] == "0.144.0"
    assert catalog["metadata"]["profile_selection"] == "model_group"
    assert catalog["builtin_profile"] == {"id": "builtin", "name": "Built-in"}

    assert policies[items["custom.apply_patch"]["policy_id"]]["default"] == ("disabled")
    image_policy = items["hosted.image_generation"]["policy_id"]
    assert image_policy == items["namespace.image_gen.imagegen"]["policy_id"]
    assert policies[image_policy]["default"] == "disabled"

    for namespace_id in ("namespace.multi_agent_v1", "namespace.multi_agent_v2"):
        namespace = items[namespace_id]
        assert namespace["default_expanded"] is True
        assert policies[namespace["policy_id"]]["default"] == "expanded"

    modified = {
        "function.request_user_input",
        "function.create_goal",
        "function.update_goal",
        "hosted.web_search",
    }
    assert {
        item_id
        for item_id, item in items.items()
        if item.get("policy_id")
        and policies[item["policy_id"]]["default"] == "modified"
    } == modified

    builtin = tool_profile_contract()["builtin"]
    assert builtin["function.exec_command"] == "passthrough"
    assert builtin["function.write_stdin"] == "passthrough"
    assert builtin["function.shell_command"] == "disabled"
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
        "Edit",
        "Write",
        "Glob",
        "Grep",
    ]


def test_catalog_source_commit_matches_compatibility_contract():
    contract = json.loads(CONTRACT_PATH.read_text("utf-8"))
    assert (
        load_tool_catalog()["metadata"]["codex_source_commit"]
        == contract["codex_source_commit"]
    )


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
        "function",
        "namespace",
        "hosted",
        "custom",
        "custom_injection",
    ):
        assert f'data-tool-filter="{filter_name}"' in page

    assert 'id="toolProfileSelect"' in page
    assert 'id="saveToolProfileBtn"' in page
    assert 'onclick="openToolProfileCloneModal()"' in page
    assert "updateToolProfileState" in html
    assert 'type="checkbox"' not in page
    assert "tools.disabledHint" in page
    assert "toolCatalogFilter === 'all' || toolCatalogFilter === 'namespace'" in html
    assert "api.get('/admin/api/tools/profiles')" in html


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
        app._dispatch(
            _api_request(
                app,
                "PUT",
                "/admin/api/tools/profiles/restricted",
                {"tools": tools},
            )
        )
    )
    assert response.status_code == 200

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
