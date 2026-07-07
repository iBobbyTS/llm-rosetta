"""Tests for admin config route handlers."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from llm_rosetta.gateway.admin.routes.config import put_model, put_server_settings
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
            "remove_image_generation": True,
            "tool_call_cache_ttl_hours": 12,
        },
    }

    response = _run(put_model(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["models"]["gpt-test"]["tool_adaptation"] == {
        "localize_code_editing_tools": False,
        "remove_image_generation": True,
        "tool_call_cache_ttl_hours": 12.0,
    }
    route, _provider = app.gateway_config.resolve("openai_responses", "gpt-test")
    assert route.tool_adaptation == {
        "localize_code_editing_tools": False,
        "remove_image_generation": True,
        "tool_call_cache_ttl_hours": 12.0,
    }
