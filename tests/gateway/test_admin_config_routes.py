"""Tests for admin config route handlers."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from codex_rosetta.gateway.admin.routes import _shared
from codex_rosetta.gateway.admin.routes.config import (
    bulk_add_models,
    delete_model_group,
    get_config,
    put_model,
    put_model_group,
    put_provider,
    put_server_settings,
    reload_config,
)
from codex_rosetta.gateway.app import create_app
from codex_rosetta.gateway.auth import AuthState
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.logging import BodyLogState
from codex_rosetta.gateway.stream_trace import StreamTraceState
from codex_rosetta.observability.metrics import MetricsCollector
from codex_rosetta.observability.request_log import RequestLogEntry


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


class _PersistenceState:
    def __init__(self, redactor: Any) -> None:
        self._redactor = redactor
        self.success_max = 50000
        self.error_max = 10000

    def prepare_update(
        self,
        values: set[str],
        *,
        success_max: int,
        error_max: int,
    ) -> tuple[set[str], int, int]:
        return set(values), success_max, error_max

    def commit_update(
        self, prepared: tuple[set[str], int, int]
    ) -> tuple[Any, int, int]:
        rollback = (self._redactor, self.success_max, self.error_max)
        self._redactor, self.success_max, self.error_max = prepared
        return rollback

    def rollback_update(self, rollback: tuple[Any, int, int]) -> None:
        self._redactor, self.success_max, self.error_max = rollback


def _log_entry(index: int, *, status_code: int = 200) -> dict[str, Any]:
    return RequestLogEntry.create(
        model=f"model-{index}",
        source_provider="openai_responses",
        target_provider="openai_chat",
        is_stream=False,
        status_code=status_code,
        duration_ms=1.0,
    ).to_dict()


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
    assert "admin_password" not in json.loads(response.body.decode("utf-8"))["server"]
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


@pytest.mark.parametrize(
    ("value", "expected_bytes"),
    [
        (64, 64 * 1024 * 1024),
        (128, 128 * 1024 * 1024),
        (256, 256 * 1024 * 1024),
        (512, 512 * 1024 * 1024),
        (1024, 1024 * 1024 * 1024),
        ("unlimited", sys.maxsize),
    ],
)
def test_put_server_settings_updates_request_body_limit_at_runtime(
    tmp_path, value, expected_bytes
):
    """Admin body-limit settings persist and affect new requests immediately."""
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")
    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        max_body_size=initial_config.request_body_limit_bytes,
        auth_state=None,
        stream_trace_state=None,
    )
    request = SimpleNamespace(app=app, json=lambda: {"request_body_limit_mb": value})

    response = _run(put_server_settings(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["request_body_limit_mb"] == value
    assert app.gateway_config.request_body_limit_config_value == value
    assert app.max_body_size == expected_bytes


def test_put_server_settings_rejects_invalid_request_body_limit(tmp_path):
    config_path = tmp_path / "config.jsonc"
    original = json.dumps(_config_data()).encode()
    config_path.write_bytes(original)
    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        max_body_size=initial_config.request_body_limit_bytes,
        auth_state=None,
        stream_trace_state=None,
    )
    request = SimpleNamespace(app=app, json=lambda: {"request_body_limit_mb": 129})

    response = _run(put_server_settings(request))

    assert response.status_code == 400
    assert b"request_body_limit_mb must be one of" in response.body
    assert config_path.read_bytes() == original
    assert app.gateway_config is initial_config
    assert app.max_body_size == 128 * 1024 * 1024


def test_reload_config_rotates_runtime_admin_credentials(tmp_path):
    config_path = tmp_path / "config.jsonc"
    initial_data = _config_data()
    config_path.write_text(json.dumps(initial_data), encoding="utf-8")
    initial_config = GatewayConfig(initial_data)
    auth_state = AuthState(
        dict(initial_config.api_key_principals),
        dict(initial_config.api_key_labels),
        "internal-token",
        initial_config.admin_password,
    )
    previous_token = auth_state.admin_token
    captured_tokens: set[str] = set()
    persistence = _PersistenceState(captured_tokens)
    metrics = MetricsCollector()
    metrics.update_token_values(initial_config.token_values)
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=None,
        persistence=persistence,
        metrics=metrics,
        internal_token="internal-token",
        auth_state=auth_state,
    )

    updated_data = _config_data()
    updated_data["server"]["admin_password"] = "rotated-admin-password"
    updated_data["server"]["api_keys"][0]["key"] = "rotated-gateway-token"
    updated_data["server"]["proxy"] = "http://user:ordinary-proxy-password@example.test"
    updated_data["server"]["request_body_limit_mb"] = 512
    updated_data["providers"]["openai"]["api_key"] = "rotated-provider-token"
    updated_data["providers"]["openai"]["client_secret"] = "ordinary-client-secret"
    config_path.write_text(json.dumps(updated_data), encoding="utf-8")

    response = _run(reload_config(SimpleNamespace(app=app)))

    assert response.status_code == 200
    assert auth_state.admin_password == "rotated-admin-password"
    assert auth_state.admin_token is not None
    assert auth_state.admin_token != previous_token
    assert app.max_body_size == 512 * 1024 * 1024
    assert persistence._redactor == {
        "internal-token",
        "rotated-gateway-token",
        "rotated-provider-token",
    }
    assert (
        metrics.redact_sensitive(
            "rotated-provider-token prompt=user@example.com password=ordinary-password"
        )
        == "[REDACTED] prompt=user@example.com password=ordinary-password"
    )


def test_reload_config_preserves_special_environment_password_as_data(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    special = 'admin","credential_visible":true,"injected":"\\line\nrest'
    monkeypatch.setenv("SPECIAL_ADMIN_PASSWORD", special)
    config_path = tmp_path / "config.jsonc"
    stored_data = _config_data()
    stored_data["server"]["admin_password"] = "${SPECIAL_ADMIN_PASSWORD}"
    stored_data["server"]["credential_visible"] = False
    config_path.write_text(json.dumps(stored_data), encoding="utf-8")

    initial_config = GatewayConfig(_config_data())
    auth_state = AuthState(
        dict(initial_config.api_key_principals),
        dict(initial_config.api_key_labels),
        "internal-token",
        initial_config.admin_password,
    )
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=None,
        persistence=None,
        metrics=None,
        internal_token="internal-token",
        auth_state=auth_state,
    )

    response = _run(reload_config(SimpleNamespace(app=app)))

    assert response.status_code == 200
    assert app.gateway_config.admin_password == special
    assert app.gateway_config.credential_visible is False
    assert auth_state.admin_password == special
    assert "injected" not in json.loads(config_path.read_text())["server"]


@pytest.mark.parametrize(
    "failure_stage",
    ["auth", "trace", "body_log", "persistence", "metrics", "cors"],
)
def test_config_prepare_failure_leaves_disk_and_all_runtime_state_unchanged(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
):
    """Every fallible activation stage completes before config persistence."""
    config_path = tmp_path / "config.jsonc"
    initial_data = _config_data()
    initial_data["server"]["admin_cors_origins"] = ["https://old.example"]
    original = json.dumps(initial_data, indent=2).encode()
    config_path.write_bytes(original)

    initial_config = GatewayConfig(initial_data)
    auth_state = AuthState(
        dict(initial_config.api_key_principals),
        dict(initial_config.api_key_labels),
        "internal-token",
        initial_config.admin_password,
    )
    trace_state = StreamTraceState(
        initial_config.stream_trace,
        token_values=initial_config.token_values,
    )
    persistence_redactor = object()
    persistence = _PersistenceState(persistence_redactor)
    body_log_state = BodyLogState(
        enabled=False,
        token_values={"test-gateway-key", "internal-token"},
    )
    metrics_redactor = object()
    metrics = SimpleNamespace(
        _redactor=metrics_redactor,
        prepare_token_values=lambda values: object(),
    )
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        auth_state=auth_state,
        stream_trace_state=trace_state,
        body_log_state=body_log_state,
        persistence=persistence,
        metrics=metrics,
        internal_token="internal-token",
        admin_cors_origins=("https://old.example",),
    )
    request = SimpleNamespace(app=app)

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"simulated {failure_stage} prepare failure")

    if failure_stage == "auth":
        monkeypatch.setattr(auth_state, "prepare_update", _fail)
    elif failure_stage == "trace":
        monkeypatch.setattr(trace_state, "prepare_update", _fail)
    elif failure_stage == "body_log":
        monkeypatch.setattr(body_log_state, "prepare_update", _fail)
    elif failure_stage == "persistence":
        monkeypatch.setattr(persistence, "prepare_update", _fail)
    elif failure_stage == "metrics":
        monkeypatch.setattr(metrics, "prepare_token_values", _fail)
    else:
        monkeypatch.setattr(_shared, "_prepare_admin_cors_origins", _fail)

    candidate = _config_data()
    candidate["server"]["admin_password"] = "new-admin-password"
    candidate["server"]["api_keys"][0]["key"] = "new-gateway-key"
    candidate["server"]["stream_trace"] = {"enabled": True}
    candidate["server"]["admin_cors_origins"] = ["https://new.example"]
    candidate["debug"] = {"log_bodies": True}

    _config, error = _shared._commit_gateway_config(
        request, str(config_path), candidate
    )

    assert _config is None
    assert error is not None
    assert error.status_code == 500
    assert f"simulated {failure_stage} prepare failure" in error.body.decode()
    assert config_path.read_bytes() == original
    assert app.gateway_config is initial_config
    assert auth_state.admin_password == "test-admin-password"
    assert auth_state.principals == {"test-gateway-key": "test-client"}
    assert trace_state.config is initial_config.stream_trace
    assert body_log_state.enabled is False
    assert "test-gateway-key" not in body_log_state.render("test-gateway-key")
    assert persistence._redactor is persistence_redactor
    assert metrics._redactor is metrics_redactor
    assert app.admin_cors_origins == ("https://old.example",)


def test_config_commit_persists_normalized_cors_and_updates_live_allowlist(tmp_path):
    config_path = tmp_path / "config.jsonc"
    initial_data = _config_data()
    config_path.write_text(json.dumps(initial_data), encoding="utf-8")
    initial_config = GatewayConfig(initial_data)
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        auth_state=None,
        stream_trace_state=None,
        persistence=None,
        admin_cors_origins=(),
    )
    candidate = _config_data()
    candidate["server"]["admin_cors_origins"] = [
        "HTTPS://ADMIN.EXAMPLE:443/",
        "https://admin.example",
    ]

    config, error = _shared._commit_gateway_config(
        SimpleNamespace(app=app), str(config_path), candidate
    )

    assert error is None
    assert config is not None
    assert app.admin_cors_origins == ("https://admin.example",)
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["admin_cors_origins"] == ["https://admin.example"]


def test_config_commit_hot_reloads_caps_and_prunes_immediately(tmp_path, monkeypatch):
    monkeypatch.delenv("REQUEST_LOG_SUCCESS_MAX", raising=False)
    monkeypatch.delenv("REQUEST_LOG_ERROR_MAX", raising=False)
    initial_data = _config_data()
    initial_data["server"]["request_log"] = {"success_max": 10, "error_max": 10}
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(initial_data), encoding="utf-8")
    app = cast(
        Any,
        create_app(GatewayConfig(initial_data), config_path=str(config_path)),
    )
    app.persistence.insert_log_entries(
        [_log_entry(index) for index in range(5)]
        + [_log_entry(index, status_code=500) for index in range(5, 9)]
    )
    candidate = _config_data()
    candidate["server"]["request_log"] = {"success_max": 2, "error_max": 1}

    try:
        config, error = _shared._commit_gateway_config(
            SimpleNamespace(app=app),
            str(config_path),
            candidate,
        )

        assert error is None
        assert config is not None
        assert app.gateway_config is config
        assert app.persistence.success_max == 2
        assert app.persistence.error_max == 1
        assert app.persistence.count_success_entries() == 2
        assert app.persistence.count_error_entries() == 1
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["server"]["request_log"] == {
            "success_max": 2,
            "error_max": 1,
        }
    finally:
        app.persistence.close()


def test_config_commit_zero_caps_prunes_both_request_classes(tmp_path, monkeypatch):
    monkeypatch.delenv("REQUEST_LOG_SUCCESS_MAX", raising=False)
    monkeypatch.delenv("REQUEST_LOG_ERROR_MAX", raising=False)
    initial_data = _config_data()
    initial_data["server"]["request_log"] = {"success_max": 10, "error_max": 10}
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(initial_data), encoding="utf-8")
    app = cast(
        Any, create_app(GatewayConfig(initial_data), config_path=str(config_path))
    )
    app.persistence.insert_log_entries(
        [_log_entry(index) for index in range(3)]
        + [_log_entry(index, status_code=500) for index in range(3, 6)]
    )
    candidate = _config_data()
    candidate["server"]["request_log"] = {"success_max": 0, "error_max": 0}

    try:
        config, error = _shared._commit_gateway_config(
            SimpleNamespace(app=app), str(config_path), candidate
        )

        assert error is None
        assert config is not None
        assert app.persistence.count_success_entries() == 0
        assert app.persistence.count_error_entries() == 0
    finally:
        app.persistence.close()


def test_config_write_failure_after_activation_restores_runtime_and_pruned_rows(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("REQUEST_LOG_SUCCESS_MAX", raising=False)
    monkeypatch.delenv("REQUEST_LOG_ERROR_MAX", raising=False)
    initial_data = _config_data()
    initial_data["server"]["request_log"] = {"success_max": 10, "error_max": 10}
    config_path = tmp_path / "config.jsonc"
    original = json.dumps(initial_data).encode("utf-8")
    config_path.write_bytes(original)
    initial_config = GatewayConfig(initial_data)
    app = cast(Any, create_app(initial_config, config_path=str(config_path)))
    app.persistence.insert_log_entries([_log_entry(index) for index in range(5)])
    old_admin_token = app.auth_state.admin_token
    candidate = _config_data()
    candidate["server"]["admin_password"] = "new-admin-password"
    candidate["server"]["request_log"] = {"success_max": 1, "error_max": 1}
    candidate["server"]["request_body_limit_mb"] = 256
    candidate["debug"] = {"log_bodies": True}

    def activate_then_fail(
        _path: str,
        _data: dict[str, Any],
        *,
        activate: Any,
    ) -> None:
        activate()
        raise OSError("simulated post-activation write failure")

    monkeypatch.setattr(_shared, "write_config", activate_then_fail)

    try:
        config, error = _shared._commit_gateway_config(
            SimpleNamespace(app=app),
            str(config_path),
            candidate,
        )

        assert config is None
        assert error is not None
        assert error.status_code == 500
        assert app.gateway_config is initial_config
        assert app.auth_state.admin_password == "test-admin-password"
        assert app.auth_state.admin_token == old_admin_token
        assert app.body_log_state.enabled is False
        assert app.max_body_size == 128 * 1024 * 1024
        assert "test-gateway-key" not in app.body_log_state.render("test-gateway-key")
        assert app.persistence.success_max == 10
        assert app.persistence.error_max == 10
        assert app.persistence.count_success_entries() == 5
        assert config_path.read_bytes() == original
    finally:
        app.persistence.close()


def test_retention_activation_is_isolated_between_apps(tmp_path, monkeypatch):
    monkeypatch.delenv("REQUEST_LOG_SUCCESS_MAX", raising=False)
    monkeypatch.delenv("REQUEST_LOG_ERROR_MAX", raising=False)
    initial_data = _config_data()
    initial_data["server"]["request_log"] = {"success_max": 10, "error_max": 10}
    path_a = tmp_path / "a" / "config.jsonc"
    path_b = tmp_path / "b" / "config.jsonc"
    path_a.parent.mkdir()
    path_b.parent.mkdir()
    path_a.write_text(json.dumps(initial_data), encoding="utf-8")
    path_b.write_text(json.dumps(initial_data), encoding="utf-8")
    app_a = cast(
        Any,
        create_app(GatewayConfig(initial_data), config_path=str(path_a)),
    )
    app_b = cast(
        Any,
        create_app(GatewayConfig(initial_data), config_path=str(path_b)),
    )
    app_a.persistence.insert_log_entries([_log_entry(index) for index in range(5)])
    app_b.persistence.insert_log_entries([_log_entry(index) for index in range(5)])
    updated_data = _config_data()
    updated_data["server"]["request_log"] = {"success_max": 1, "error_max": 2}

    try:
        _shared._activate_gateway_config(
            SimpleNamespace(app=app_a),
            GatewayConfig(updated_data),
        )

        assert app_a.persistence.success_max == 1
        assert app_a.persistence.count_success_entries() == 1
        assert app_b.persistence.success_max == 10
        assert app_b.persistence.count_success_entries() == 5
    finally:
        app_a.persistence.close()
        app_b.persistence.close()


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
    assert body["server"]["request_body_limit_mb"] == 128


@pytest.mark.parametrize("credential_visible", [False, True])
@pytest.mark.parametrize(
    ("stored_password", "runtime_password"),
    [
        ("literal-admin-password", "literal-admin-password"),
        ("${TEST_ADMIN_PASSWORD}", "environment-admin-password"),
    ],
)
def test_get_config_never_returns_admin_password(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    credential_visible: bool,
    stored_password: str,
    runtime_password: str,
):
    """Admin config responses never expose literal or env-backed passwords."""
    monkeypatch.setenv("TEST_ADMIN_PASSWORD", runtime_password)
    config = _config_data()
    config["server"]["admin_password"] = stored_password
    config["server"]["credential_visible"] = credential_visible
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    request = SimpleNamespace(
        app=SimpleNamespace(
            config_path=str(config_path),
            gateway_config=GatewayConfig.from_raw_with_env(config),
        )
    )

    response = _run(get_config(request))

    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert "admin_password" not in body["server"]
    assert stored_password not in response.body.decode("utf-8")
    assert runtime_password not in response.body.decode("utf-8")


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
        "capabilities": ["text"],
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
        "capabilities": ["text"],
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
    assert saved["models"]["gpt-test"]["capabilities"] == ["text"]


@pytest.mark.parametrize("ttl", [True, "nan", "inf", "1e999", 720.01])
def test_put_model_rejects_invalid_tool_mapping_ttl_without_writing(tmp_path, ttl):
    config_path = tmp_path / "config.jsonc"
    original = _config_data()
    config_path.write_text(json.dumps(original), encoding="utf-8")
    initial_config = GatewayConfig(original)
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "gpt-test"})
    request.json = lambda: {
        "provider": "openai",
        "capabilities": ["text", "tools"],
        "tool_adaptation": {"tool_call_cache_ttl_hours": ttl},
    }

    response = _run(put_model(request))

    assert response.status_code == 400
    assert b"at most 720 hours" in response.body
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def test_put_model_accepts_maximum_tool_mapping_ttl(tmp_path):
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")
    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "gpt-test"})
    request.json = lambda: {
        "provider": "openai",
        "capabilities": ["text", "tools"],
        "tool_adaptation": {"tool_call_cache_ttl_hours": 720},
    }

    response = _run(put_model(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert (
        saved["models"]["gpt-test"]["tool_adaptation"]["tool_call_cache_ttl_hours"]
        == 720.0
    )


def test_put_model_persists_reasoning_mapping_and_drops_legacy_override(tmp_path):
    """Model saves the new reasoning mapping and discards old reasoning fields."""
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")

    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "gpt-test"})
    request.json = lambda: {
        "provider": "openai",
        "capabilities": ["text"],
        "reasoning_mapping": "qwen_3_7",
        "reasoning_override": {"thinking_type": "adaptive"},
    }

    response = _run(put_model(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["models"]["gpt-test"]["reasoning_mapping"] == "qwen_3_7"
    assert saved["models"]["gpt-test"]["capabilities"] == ["text"]
    assert "reasoning_override" not in saved["models"]["gpt-test"]
    route, _provider = app.gateway_config.resolve("openai_responses", "gpt-test")
    assert route.reasoning_mapping == "qwen_3_7"


def test_get_config_returns_reasoning_mapping_metadata_and_drops_legacy(tmp_path):
    """Admin config response exposes mapping metadata without legacy override."""
    config = _config_data()
    config["models"] = {
        "qwen-public": {
            "provider": "openai",
            "upstream_model": "qwen3.7-plus",
            "capabilities": ["text"],
            "reasoning_override": {"thinking_type": "adaptive"},
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
    model = body["models"]["qwen-public"]
    assert "reasoning_override" not in model
    assert "reasoning_mapping" not in model
    assert model["reasoning"] == {
        "source": "model",
        "requested": "auto",
        "effective": "qwen_3_7",
        "target_provider": "openai_chat",
    }


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
                    "capabilities": ["text"],
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
                "capabilities": ["text"],
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
                "capabilities": ["text"],
                "upstream_model": "gpt-upstream",
            }
        },
    }
    route, _provider = app.gateway_config.resolve("openai_responses", "gpt-grouped")
    assert route.provider_name == "openai"
    assert route.upstream_model == "gpt-upstream"
    assert route.model_capabilities == ["text"]


def test_put_model_group_persists_reasoning_mapping_and_drops_legacy(tmp_path):
    """Grouped model entries save reasoning_mapping and discard legacy override."""
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
            "qwen-public": {
                "upstream_model": "qwen3.7-plus",
                "capabilities": ["text"],
                "reasoning_mapping": "auto",
                "reasoning_override": {"thinking_type": "adaptive"},
            }
        },
    }

    response = _run(put_model_group(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    entry = saved["model_groups"]["OpenAI"]["models"]["qwen-public"]
    assert entry["reasoning_mapping"] == "auto"
    assert entry["capabilities"] == ["text"]
    assert "reasoning_override" not in entry
    route, _provider = app.gateway_config.resolve("openai_responses", "qwen-public")
    assert route.reasoning_mapping == "auto"


def test_bulk_add_models_persists_explicit_llm_capabilities(tmp_path):
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
        "provider": "openai",
        "models": ["gpt-bulk"],
        "capabilities": ["text"],
    }

    response = _run(bulk_add_models(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["models"]["gpt-bulk"]["capabilities"] == ["text"]


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
        / "codex_rosetta"
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


def test_admin_html_exposes_request_body_limit_options():
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")

    assert 'id="requestBodyLimitMb"' in html
    for value in (64, 128, 256, 512, 1024):
        assert f'<option value="{value}">{value} MB</option>' in html
    assert '<option value="unlimited"' in html
    assert "request_body_limit_mb" in html


def test_admin_html_exposes_reasoning_mapping_controls():
    """Model modal uses reasoning_mapping instead of legacy reasoning controls."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")

    assert 'id="reasoningMapping"' in html
    assert 'id="reasoningAutoResult"' in html
    assert 'id="modelReasoningGroup" style="display:none' not in html
    assert "function _detectReasoningMappingByModelName(modelName)" in html
    assert "body.reasoning_mapping" in html
    assert 'id="capReasoning"' not in html
    assert 'id="fetchCapReasoning"' not in html
    assert 'value="reasoning"' not in html
    assert "reasoning_override" not in html
    assert "reasoningThinkingType" not in html
    assert "reasoningBudgetRatio" not in html
    assert "reasoningDisabled" not in html


def test_admin_html_assumes_all_llm_models_support_tools():
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")

    assert 'id="capTools"' not in html
    assert 'id="fetchCapTools"' not in html
    assert 'class="group-cap" value="tools"' not in html
    assert "capabilities.push('tools');" not in html
    assert "capabilities.push('reasoning');" not in html
    assert "caps.push('tools');" not in html
    assert "caps.push('reasoning');" not in html


def test_admin_html_exposes_provider_preset_protocol_controls():
    """Provider modal exposes provider/protocol selects and preset behavior."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")

    assert 'id="provProvider"' in html
    assert 'id="provProviderVariant"' in html
    assert 'id="provApiType"' in html
    assert "const PROVIDER_PRESETS" in html
    assert "const PROVIDER_VENDOR_PRESETS" in html
    assert "PROTOCOL_DIVIDER_VALUE" in html
    assert "divider.disabled = true" in html
    assert "opt.dataset.unsupported = 'true'" in html
    assert "'provider.kimi':'Kimi'" in html
    assert "'provider.minimax':'MiniMax'" in html
    assert "'providerVariant.official':'Official'" in html
    assert "'providerVariant.china':'China'" in html
    assert "'providerVariant.international':'International'" in html
    assert "'providerVariant.custom':'Custom'" in html
    assert "'provider.qwen':'Qwen'" in html
    assert "'provider.qwen':'\\u901a\\u4e49\\u5343\\u95ee'" in html
    assert "'provider.zhipu':'Zhipu (GLM)'" in html
    assert "'provider.zhipu':'\\u667a\\u8c31 GLM'" in html
    assert "protocol.unsupportedSuffix" in html
    assert (
        "const body = {provider, api_type: apiType, base_url: baseUrl, proxy}" in html
    )
    assert 'id="provType"' not in html
    assert "variantSel.value = 'custom'" in html
    assert "document.getElementById('provProvider').value = 'custom'" not in html
    assert "const provider = _providerResolvedProviderId(providerId, variantId)" in html

    provider_order = [
        "{id: 'deepseek', label:",
        "{id: 'zhipu', label:",
        "{id: 'moonshot', label:",
        "{id: 'minimax', label:",
        "{id: 'qwen', label:",
        "{id: 'openai', label:",
        "{id: 'google', label:",
        "{id: 'anthropic', label:",
        "{id: 'openrouter', label:",
        "{id: 'opencode_go', label:",
        "{id: 'custom', label:",
    ]
    vendor_section = html[html.index("const PROVIDER_VENDOR_PRESETS") :]
    positions = [vendor_section.index(item) for item in provider_order]
    assert positions == sorted(positions)


def test_admin_html_exposes_model_group_controls():
    """Models page exposes model group management controls."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
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
        / "codex_rosetta"
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
    assert "codex-rosetta-tab" not in html
    assert "data-tab" not in html
    assert "currentTab" not in html
