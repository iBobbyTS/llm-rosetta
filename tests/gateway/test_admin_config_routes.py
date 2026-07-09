"""Tests for admin config route handlers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from llm_rosetta.gateway.admin.routes.config import (
    delete_model_group,
    get_config,
    put_model,
    put_model_group,
    put_provider,
    put_server_settings,
)
from llm_rosetta.gateway.config import GatewayConfig
from llm_rosetta.gateway.stream_trace import StreamTraceState


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _config_data() -> dict[str, Any]:
    return {
        "providers": {
            "openai": {
                "type": "openai",
                "base_url": "https://api.example.com",
                "api_key": "sk-test",
            }
        },
        "models": {"gpt-test": "openai"},
        "server": {},
    }


def test_put_server_settings_updates_stream_trace_and_runtime_state(tmp_path):
    """Admin stream trace settings persist to config and hot-reload state."""
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")

    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app)
    request.json = lambda: {
        "stream_trace": {
            "enabled": True,
            "filter": "glm,opencode",
            "path": "~/trace/log.jsonl",
            "max_string_chars": 1234,
        }
    }

    response = _run(put_server_settings(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["stream_trace"] == {
        "enabled": True,
        "filter": "glm,opencode",
        "path": "~/trace/log.jsonl",
        "max_string_chars": 1234,
    }
    assert app.stream_trace_state.config.enabled is True
    assert app.stream_trace_state.config.filter == "glm,opencode"
    assert app.stream_trace_state.config.path == "~/trace/log.jsonl"


def test_put_server_settings_persists_tavily_api_key(tmp_path):
    """Admin web search settings persist to server.web_search."""
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")

    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app)
    request.json = lambda: {"web_search": {"tavily_api_key": "tvly-test-key"}}

    response = _run(put_server_settings(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["web_search"] == {"tavily_api_key": "tvly-test-key"}


def test_put_server_settings_preserves_masked_tavily_api_key(tmp_path):
    """Saving the masked admin value keeps the existing Tavily API key."""
    config = _config_data()
    config["server"]["web_search"] = {"tavily_api_key": "tvly-1234567890"}
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app)
    request.json = lambda: {"web_search": {"tavily_api_key": "tvly***7890"}}

    response = _run(put_server_settings(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["web_search"] == {"tavily_api_key": "tvly-1234567890"}


def test_get_config_masks_tavily_api_key(tmp_path):
    """Admin config response does not expose the raw Tavily API key."""
    config = _config_data()
    config["server"]["web_search"] = {"tavily_api_key": "tvly-1234567890"}
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=GatewayConfig(config),
    )
    request = SimpleNamespace(app=app)

    response = _run(get_config(request))

    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert body["server"]["web_search"]["tavily_api_key"] == "tvly***7890"


def test_put_provider_persists_provider_and_api_type(tmp_path):
    """New admin provider saves use provider/api_type instead of legacy type."""
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")

    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "DeepSeek"})
    request.json = lambda: {
        "provider": "deepseek",
        "api_type": "chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-new",
    }

    response = _run(put_provider(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["providers"]["DeepSeek"] == {
        "api_key": "sk-new",
        "base_url": "https://api.deepseek.com",
        "provider": "deepseek",
        "api_type": "chat",
    }
    assert "type" not in saved["providers"]["DeepSeek"]
    assert app.gateway_config.provider_types["DeepSeek"] == "openai_chat"
    assert app.gateway_config.provider_shim_names["DeepSeek"] == "deepseek"


def test_put_provider_masked_key_preserves_existing_key_with_api_type(tmp_path):
    """Editing a new-style provider with a masked key keeps the old secret."""
    config = _config_data()
    config["providers"]["DeepSeek"] = {
        "api_key": "sk-1234567890",
        "base_url": "https://api.deepseek.com",
        "provider": "deepseek",
        "api_type": "chat",
    }
    config["models"]["deepseek-test"] = "DeepSeek"
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "DeepSeek"})
    request.json = lambda: {
        "provider": "deepseek",
        "api_type": "chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-1***7890",
    }

    response = _run(put_provider(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["providers"]["DeepSeek"]["api_key"] == "sk-1234567890"
    assert saved["providers"]["DeepSeek"]["provider"] == "deepseek"
    assert saved["providers"]["DeepSeek"]["api_type"] == "chat"
    assert "type" not in saved["providers"]["DeepSeek"]


def test_put_model_persists_tool_adaptation_and_reloads_runtime_config(tmp_path):
    """Model tool adaptation settings persist and hot-reload into routing."""
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")

    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(
        app=app,
        path_params={"name": "gpt-test"},
    )
    request.json = lambda: {
        "provider": "openai",
        "capabilities": ["text", "tools"],
        "tool_adaptation": {
            "localize_code_editing_tools": False,
            "use_apply_patch_for_code_edits": False,
            "remove_image_generation": True,
            "enable_tool_description_optimization": False,
            "enable_phase_detection": False,
            "tool_call_cache_ttl_hours": 12,
        },
    }

    response = _run(put_model(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["models"]["gpt-test"]["tool_adaptation"] == {
        "localize_code_editing_tools": False,
        "use_apply_patch_for_code_edits": False,
        "remove_image_generation": True,
        "enable_tool_description_optimization": False,
        "enable_phase_detection": False,
        "tool_call_cache_ttl_hours": 12.0,
    }
    route, _provider = app.gateway_config.resolve("openai_responses", "gpt-test")
    assert route.tool_adaptation == {
        "localize_code_editing_tools": False,
        "use_apply_patch_for_code_edits": False,
        "remove_image_generation": True,
        "enable_tool_description_optimization": False,
        "enable_phase_detection": False,
        "tool_call_cache_ttl_hours": 12.0,
    }


def test_put_model_omits_default_tool_adaptation(tmp_path):
    """Default-only tool adaptation settings do not create config noise."""
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")

    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(
        app=app,
        path_params={"name": "gpt-test"},
    )
    request.json = lambda: {
        "provider": "openai",
        "capabilities": ["text", "tools"],
        "tool_adaptation": {
            "localize_code_editing_tools": False,
            "use_apply_patch_for_code_edits": True,
            "remove_image_generation": False,
            "enable_tool_description_optimization": True,
            "enable_phase_detection": True,
            "tool_call_cache_ttl_hours": 24,
        },
    }

    response = _run(put_model(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "tool_adaptation" not in saved["models"]["gpt-test"]


def test_get_config_returns_model_groups_and_effective_models(tmp_path):
    """Admin config exposes grouped management data and expanded runtime models."""
    config = _config_data()
    config["models"] = {"standalone": "openai"}
    config["model_groups"] = {
        "OpenAI": {
            "provider": "openai",
            "models": {
                "grouped": {
                    "upstream_model": "grouped-upstream",
                    "capabilities": ["text", "tools"],
                }
            },
        }
    }
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=GatewayConfig(config),
    )
    request = SimpleNamespace(app=app)

    response = _run(get_config(request))

    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert set(body["models"]) == {"standalone", "grouped"}
    assert set(body["standalone_models"]) == {"standalone"}
    assert body["model_groups"]["OpenAI"]["provider"] == "openai"
    assert body["model_groups"]["OpenAI"]["models"]["grouped"]["upstream_model"] == (
        "grouped-upstream"
    )
    assert body["models"]["grouped"]["provider"] == "openai"


def test_put_model_group_persists_and_reloads_runtime_config(tmp_path):
    """Saving a model group persists grouped config and expands runtime routes."""
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")

    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "OpenAI"})
    request.json = lambda: {
        "provider": "openai",
        "models": {
            "gpt-grouped": {
                "upstream_model": "gpt-upstream",
                "capabilities": ["text", "tools"],
            }
        },
    }

    response = _run(put_model_group(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model_groups"]["OpenAI"] == {
        "provider": "openai",
        "models": {
            "gpt-grouped": {
                "capabilities": ["text", "tools"],
                "upstream_model": "gpt-upstream",
            }
        },
    }
    route, _provider = app.gateway_config.resolve("openai_responses", "gpt-grouped")
    assert route.provider_name == "openai"
    assert route.upstream_model == "gpt-upstream"
    assert route.model_capabilities == ["text", "tools"]


def test_put_model_group_rejects_duplicate_flat_model_name(tmp_path):
    """A grouped model cannot reuse an existing top-level model name."""
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")

    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "OpenAI"})
    request.json = lambda: {
        "provider": "openai",
        "models": {"gpt-test": {"capabilities": ["text"]}},
    }

    response = _run(put_model_group(request))

    assert response.status_code == 409
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "model_groups" not in saved


def test_delete_model_group_removes_group_and_runtime_models(tmp_path):
    """Deleting a model group removes its expanded model routes."""
    config = _config_data()
    config["model_groups"] = {
        "OpenAI": {
            "provider": "openai",
            "models": {"gpt-grouped": "gpt-upstream"},
        }
    }
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "OpenAI"})

    response = _run(delete_model_group(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model_groups"] == {}
    assert "gpt-grouped" not in app.gateway_config.models


def test_admin_html_exposes_tool_adaptation_switches():
    """Model modal exposes all configurable tool adaptation switches."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "llm_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")

    assert 'id="toolUseApplyPatchForCodeEdits" checked' in html
    assert 'id="toolUseApplyPatchRow" style="display:none' in html
    assert 'onchange="updateToolAdaptationVisibility()"' in html
    assert "function updateToolAdaptationVisibility()" in html
    assert "toolAdaptation.use_apply_patch_for_code_edits !== false" in html
    assert "toolAdaptation.enable_tool_description_optimization !== false" in html
    assert "toolAdaptation.enable_phase_detection !== false" in html
    assert "toolFlattenNestedNamespaceTools" not in html


def test_admin_html_exposes_provider_preset_protocol_controls():
    """Provider modal exposes provider/protocol selects and preset behavior."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "llm_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")

    assert 'id="provProvider"' in html
    assert 'id="provApiType"' in html
    assert "const PROVIDER_PRESETS" in html
    assert "PROTOCOL_DIVIDER_VALUE" in html
    assert "divider.disabled = true" in html
    assert "opt.dataset.unsupported = 'true'" in html
    assert "'provider.qwen':'Qwen'" in html
    assert "'provider.qwen':'\\u901a\\u4e49\\u5343\\u95ee'" in html
    assert "'provider.zhipu':'Zhipu (GLM)'" in html
    assert "'provider.zhipu':'\\u667a\\u8c31 GLM'" in html
    assert "protocol.unsupportedSuffix" in html
    assert (
        "const body = {provider, api_type: apiType, base_url: baseUrl, proxy}" in html
    )
    assert 'id="provType"' not in html

    provider_order = [
        "id: 'deepseek'",
        "id: 'zhipu'",
        "id: 'moonshot_china'",
        "id: 'moonshot_international'",
        "id: 'minimax_china'",
        "id: 'minimax_international'",
        "id: 'qwen'",
        "id: 'openai'",
        "id: 'google'",
        "id: 'anthropic'",
        "id: 'openrouter'",
        "id: 'opencode_go'",
        "id: 'custom'",
    ]
    positions = [html.index(item) for item in provider_order]
    assert positions == sorted(positions)


def test_admin_html_exposes_model_group_controls():
    """Models page exposes model group management controls."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "llm_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")

    assert 'onclick="openModelGroupModal()"' in html
    assert 'id="modelGroupList"' in html
    assert 'id="modelGroupModal"' in html
    assert 'id="modelGroupRows"' in html
    assert "max-height: 90vh; overflow-y: auto;" in html
    assert "function openModelGroupModal(groupName)" in html
    assert "function toggleModelGroup(groupName)" in html
    assert "function onModelGroupRowTypeChange(input)" in html
    assert "function saveModelGroup()" in html
    assert "/admin/api/config/model-groups/" in html
    assert "configData.standalone_models || models" in html
    assert "_collapsedModelGroups" in html
    assert "model-group-card${collapsed ? ' collapsed' : ''}" in html
    assert 'class="model-group-body"' in html
    assert 'class="group-model-type-input"' in html
    assert 'class="checkbox-group group-cap-wrap"' in html
    assert 'class="group-cap" value="embedding"' not in html
    assert "modelType === 'embedding'" in html
    assert "'btn.addModelGroup':'+ Add Model Group'" in html
    assert "'btn.addModelGroup':'+ \\u6dfb\\u52a0\\u6a21\\u578b\\u7ec4'" in html


def test_admin_html_uses_page_routes():
    """Admin navigation uses URL pages instead of tab-local routing."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "llm_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")

    assert 'href="/admin/providers"' in html
    assert 'href="/admin/models"' in html
    assert 'href="/admin/keys"' in html
    assert 'href="/admin/web-search"' in html
    assert 'href="/admin/dashboard"' in html
    assert 'href="/admin/logs"' in html
    assert 'href="/admin/gateway-logs"' in html
    assert 'data-page="keys"' in html
    assert 'data-page="web-search"' in html
    assert 'id="page-keys"' in html
    assert 'id="page-web-search"' in html
    assert "llm-rosetta-tab" not in html
    assert "data-tab" not in html
    assert "currentTab" not in html
