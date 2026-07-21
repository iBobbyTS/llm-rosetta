"""Tests for admin config route handlers."""

from __future__ import annotations

import asyncio
import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from codex_rosetta.gateway.admin.routes import _shared
from codex_rosetta.gateway import web_run_health
from codex_rosetta.gateway.admin.routes.config import (
    delete_model_group,
    get_config,
    get_network_search_status,
    put_codex_settings,
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


def _load_admin_i18n() -> dict[str, dict[str, str]]:
    path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin_i18n.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _config_data() -> dict[str, Any]:
    return {
        "providers": {
            "openai": {
                "api_type": "chat",
                "base_url": "https://api.example.com",
                "api_key": "sk-test",
            }
        },
        "model_groups": {
            "OpenAI": {
                "provider": "openai",
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
    health_invalidations = []
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=None,
        persistence=persistence,
        metrics=metrics,
        internal_token="internal-token",
        auth_state=auth_state,
        web_run_health_state=SimpleNamespace(
            invalidate=lambda: health_invalidations.append(True)
        ),
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
    assert health_invalidations == [True]
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


def _runtime_redactors(app: Any) -> list[Any]:
    return [
        app.stream_trace_state._redactor,
        app.upstream_error_log_state._redactor,
        app.body_log_state._redactor,
        app.persistence._redactor,
        app.metrics._redactor,
    ]


def _assert_exact_tokens(
    redactors: list[Any],
    *,
    hidden: tuple[str, ...],
    visible: tuple[str, ...] = (),
) -> None:
    for redactor in redactors:
        for token in hidden:
            assert token not in redactor.redact(f"before {token} after")
        for token in visible:
            assert redactor.redact(token) == token


def test_startup_registers_every_rotated_provider_key_in_all_runtime_redactors(
    tmp_path,
) -> None:
    data = _config_data()
    raw_keys = " first-startup , , startup-prefix,startup-prefix-long,first-startup "
    data["providers"]["openai"]["api_key"] = raw_keys
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    config = GatewayConfig(data)
    app = cast(Any, create_app(config, config_path=str(config_path)))

    try:
        assert config.providers["openai"].credential_values == (
            "first-startup",
            "startup-prefix",
            "startup-prefix-long",
            "first-startup",
        )
        assert raw_keys in config.token_values
        _assert_exact_tokens(
            _runtime_redactors(app),
            hidden=("first-startup", "startup-prefix", "startup-prefix-long"),
        )
    finally:
        app.persistence.close()


def test_hot_reload_and_rollback_atomically_swap_all_provider_credentials(
    tmp_path,
) -> None:
    old_tokens = ("old-first", "old-prefix", "old-prefix-long")
    new_tokens = ("new-first", "new-prefix", "new-prefix-long")
    initial_data = _config_data()
    initial_data["providers"]["openai"]["api_key"] = ",".join(old_tokens)
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(initial_data), encoding="utf-8")
    initial_config = GatewayConfig(initial_data)
    app = cast(Any, create_app(initial_config, config_path=str(config_path)))

    candidate = _config_data()
    candidate["providers"]["openai"]["api_key"] = (
        " new-first, ,new-prefix,new-prefix-long,new-first "
    )
    new_config = GatewayConfig(candidate)

    try:
        prepared = _shared._prepare_gateway_activation(
            SimpleNamespace(app=app), new_config
        )
        assert app.gateway_config is initial_config
        _assert_exact_tokens(
            _runtime_redactors(app), hidden=old_tokens, visible=new_tokens
        )

        rollback = _shared._activate_gateway_config(
            SimpleNamespace(app=app), new_config, prepared
        )

        assert app.gateway_config is new_config
        assert app.gateway_config.providers["openai"].credential_values == (
            "new-first",
            "new-prefix",
            "new-prefix-long",
            "new-first",
        )
        _assert_exact_tokens(
            _runtime_redactors(app), hidden=new_tokens, visible=old_tokens
        )

        _shared._rollback_gateway_activation(SimpleNamespace(app=app), rollback)

        assert app.gateway_config is initial_config
        assert app.gateway_config.providers["openai"].credential_values == old_tokens
        _assert_exact_tokens(
            _runtime_redactors(app), hidden=old_tokens, visible=new_tokens
        )
    finally:
        app.persistence.close()


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


def test_put_server_settings_preserves_masked_web_search_key_and_hot_reloads(
    tmp_path,
):
    config = _config_data()
    config["server"]["web_search"] = {
        "provider": "tavily",
        "tavily_api_key": "tvly-secret-value",
    }
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        auth_state=None,
        stream_trace_state=None,
    )
    request = SimpleNamespace(
        app=app,
        json=lambda: {
            "web_search": {
                "provider": "tavily",
                "tavily_api_key": "tvly***alue",
            }
        },
    )

    response = _run(put_server_settings(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["web_search"] == {
        "provider": "tavily",
        "tavily_api_key": "tvly-secret-value",
    }
    assert app.gateway_config.web_search["tavily_api_key"] == "tvly-secret-value"
    assert "tvly-secret-value" in app.gateway_config.token_values
    assert (
        json.loads(response.body)["server"]["web_search"]["tavily_api_key"]
        == "tvly***alue"
    )


def test_put_server_settings_clears_web_search_key(tmp_path):
    config = _config_data()
    config["server"]["web_search"] = {
        "provider": "tavily",
        "tavily_api_key": "tvly-secret-value",
    }
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        auth_state=None,
        stream_trace_state=None,
    )
    request = SimpleNamespace(
        app=app,
        json=lambda: {"web_search": {"provider": "tavily", "tavily_api_key": ""}},
    )

    response = _run(put_server_settings(request))

    assert response.status_code == 200
    assert json.loads(config_path.read_text(encoding="utf-8"))["server"][
        "web_search"
    ] == {"provider": "tavily"}
    assert app.gateway_config.web_search["tavily_api_key"] == ""


def test_put_server_settings_selects_self_hosted_google_and_preserves_tavily_key(
    tmp_path,
):
    config = _config_data()
    config["server"]["web_search"] = {
        "provider": "tavily",
        "tavily_api_key": "tvly-secret-value",
    }
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=GatewayConfig(config),
        auth_state=None,
        stream_trace_state=None,
    )
    request = SimpleNamespace(
        app=app,
        json=lambda: {
            "web_search": {
                "provider": "self_hosted_google",
                "tavily_api_key": "tvly***alue",
            }
        },
    )

    response = _run(put_server_settings(request))

    assert response.status_code == 200
    assert json.loads(config_path.read_text(encoding="utf-8"))["server"][
        "web_search"
    ] == {
        "provider": "self_hosted_google",
        "tavily_api_key": "tvly-secret-value",
    }
    assert app.gateway_config.web_search["provider"] == "self_hosted_google"


def test_put_server_settings_rejects_invalid_web_search_fields(tmp_path):
    config_path = tmp_path / "config.jsonc"
    original = json.dumps(_config_data()).encode()
    config_path.write_bytes(original)
    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(config_path=str(config_path), gateway_config=initial_config)
    request = SimpleNamespace(
        app=app,
        json=lambda: {"web_search": {"provider": "other", "token": "legacy"}},
    )

    response = _run(put_server_settings(request))

    assert response.status_code == 400
    assert config_path.read_bytes() == original
    assert app.gateway_config is initial_config


class _FakeAsyncClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


def test_network_search_status_is_unconfigured_without_sidecar():
    request = SimpleNamespace(
        app=SimpleNamespace(gateway_config=GatewayConfig(_config_data()))
    )

    response = _run(get_network_search_status(request))

    assert json.loads(response.body) == {
        "configured": False,
        "service_online": False,
        "browser_ready": None,
    }


@pytest.mark.parametrize(
    ("health", "expected"),
    [
        ({"status": "ok", "browser_ready": False}, (True, False)),
        ({"status": "ok", "browser_ready": True}, (True, True)),
    ],
)
def test_network_search_status_reports_service_and_browser(
    monkeypatch, health, expected
):
    config = _config_data()
    config["server"]["web_run"] = {
        "base_url": "http://web-run:8080",
        "token": "sidecar-token",
    }
    request = SimpleNamespace(app=SimpleNamespace(gateway_config=GatewayConfig(config)))
    calls = []

    async def fake_request(client, method, url, **kwargs):
        calls.append((client.kwargs, method, url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: health)

    monkeypatch.setattr(web_run_health, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(web_run_health, "request_bounded_response", fake_request)

    response = _run(get_network_search_status(request))

    body = json.loads(response.body)
    assert body == {
        "configured": True,
        "service_online": expected[0],
        "browser_ready": expected[1],
    }
    assert calls[0][0]["timeout"] == 2.0
    assert calls[0][1:3] == ("GET", "http://web-run:8080/health")
    assert calls[0][3] == {
        "max_success_bytes": 64 * 1024,
        "max_error_bytes": 64 * 1024,
    }


def test_network_search_status_hides_unreachable_sidecar_error(monkeypatch):
    config = _config_data()
    config["server"]["web_run"] = {
        "base_url": "http://web-run:8080",
        "token": "sidecar-token",
    }
    request = SimpleNamespace(app=SimpleNamespace(gateway_config=GatewayConfig(config)))

    async def fail_request(*args, **kwargs):
        raise RuntimeError("sensitive upstream detail")

    monkeypatch.setattr(web_run_health, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(web_run_health, "request_bounded_response", fail_request)

    response = _run(get_network_search_status(request))

    assert json.loads(response.body) == {
        "configured": True,
        "service_online": False,
        "browser_ready": None,
    }
    assert b"sensitive upstream detail" not in response.body


def test_get_config_masks_web_run_sidecar_token(tmp_path):
    config = _config_data()
    config["server"]["web_run"] = {
        "base_url": "http://web-run:8080",
        "token": "sidecar-secret-token-1234567890",
    }
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=GatewayConfig(config),
    )

    response = _run(get_config(SimpleNamespace(app=app)))

    assert response.status_code == 200
    body = json.loads(response.body.decode())
    assert body["server"]["web_run"] == {
        "base_url": "http://web-run:8080",
        "token": "side***7890",
    }


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


def test_put_provider_persists_url_and_api_type_without_ui_provider_option(tmp_path):
    """Admin provider options are derived from URL instead of persisted."""
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
        "allow_redirects": True,
    }

    response = _run(put_provider(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["providers"]["DeepSeek"] == {
        "api_key": "sk-new",
        "base_url": "https://api.deepseek.com",
        "api_type": "chat",
        "allow_redirects": True,
    }
    assert "type" not in saved["providers"]["DeepSeek"]
    assert app.gateway_config.provider_types["DeepSeek"] == "openai_chat"
    assert app.gateway_config.provider_shim_names["DeepSeek"] == "deepseek"
    assert app.gateway_config.providers["DeepSeek"].allow_redirects is True


def test_put_provider_persists_direct_responses_protocol(tmp_path):
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(_config_data()), encoding="utf-8")
    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "Qwen"})
    request.json = lambda: {
        "provider": "qwen",
        "api_type": "responses",
        "base_url": "https://qwen.example.test/v1",
        "api_key": "sk-new",
    }

    response = _run(put_provider(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["providers"]["Qwen"]["api_type"] == "responses"
    assert app.gateway_config.provider_types["Qwen"] == "openai_responses"
    assert app.gateway_config.providers["Qwen"].allow_redirects is False


def test_put_provider_masked_key_preserves_existing_key_with_api_type(tmp_path):
    """Editing a new-style provider with a masked key keeps the old secret."""
    config = _config_data()
    config["providers"]["DeepSeek"] = {
        "api_key": "sk-1234567890",
        "base_url": "https://api.deepseek.com",
        "provider": "deepseek",
        "api_type": "chat",
    }
    config["model_groups"]["DeepSeek"] = {
        "provider": "DeepSeek",
        "type": "llm",
        "models": {"deepseek-test": {}},
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
    assert saved["providers"]["DeepSeek"]["api_type"] == "chat"
    assert "provider" not in saved["providers"]["DeepSeek"]
    assert "type" not in saved["providers"]["DeepSeek"]


def test_get_config_returns_model_groups_and_effective_models(tmp_path):
    """Admin config exposes grouped management data and expanded runtime models."""
    config = _config_data()
    config["models"] = {"standalone": "openai"}
    config["model_groups"] = {
        "OpenAI": {
            "provider": "openai",
            "type": "llm",
            "models": {"grouped": {"upstream_model": "grouped-upstream"}},
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
    assert set(body["models"]) == {"grouped"}
    assert "standalone_models" not in body
    assert body["model_groups"]["OpenAI"]["provider"] == "openai"
    assert body["model_groups"]["OpenAI"]["type"] == "llm"
    assert body["model_groups"]["OpenAI"]["tool_profile"] == "builtin"
    assert body["providers"]["openai"]["default_tool_profile"] == "builtin"
    assert "validation_error" not in body["providers"]["openai"]
    assert body["tool_profile_presets"] == [
        {
            "id": "builtin",
            "name": "Chat Default（适用于第三方仅提供chat api的模型）",
        },
        {
            "id": "openai-responses-tool-mapping-only",
            "name": "透传（适用于OpenAI官方API）",
        },
        {
            "id": "web-run-injection",
            "name": "web.run 注入（适用于尚未支持/alpha/search端点的中转站）",
        },
        {
            "id": "responses-tool-mapping",
            "name": "工具映射（适用于第三方模型提供的Responses接口）",
        },
    ]
    assert any(
        preset["slug"] == "gpt-5.6-terra" and preset["display_name"] == "GPT-5.6-Terra"
        for preset in body["model_presets"]
    )
    assert any(preset["slug"] == "deepseek-v4-pro" for preset in body["model_presets"])
    assert body["codex"] == {}
    assert body["model_groups"]["OpenAI"]["models"]["grouped"]["upstream_model"] == (
        "grouped-upstream"
    )
    assert body["models"]["grouped"]["provider"] == "openai"


def test_get_config_renders_inferred_provider_api_type_and_group(tmp_path):
    valid_config = _config_data()
    runtime_config = GatewayConfig(valid_config)
    invalid_config = json.loads(json.dumps(valid_config))
    invalid_config["providers"]["openai"].pop("api_type")
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(invalid_config), encoding="utf-8")
    request = SimpleNamespace(
        app=SimpleNamespace(
            config_path=str(config_path),
            gateway_config=runtime_config,
        )
    )

    response = _run(get_config(request))

    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert body["providers"]["openai"]["api_type"] == "responses"
    assert body["providers"]["openai"]["default_tool_profile"] == "web-run-injection"
    assert "validation_error" not in body["providers"]["openai"]
    assert "validation_error" not in body["model_groups"]["OpenAI"]
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert "api_type" not in persisted["providers"]["openai"]


def test_get_config_treats_unrecognized_provider_api_type_as_missing(tmp_path):
    config = _config_data()
    config["providers"]["openai"]["api_type"] = "removed-protocol"
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    request = SimpleNamespace(
        app=SimpleNamespace(
            config_path=str(config_path),
            gateway_config=GatewayConfig(config),
        )
    )

    response = _run(get_config(request))

    assert response.status_code == 200
    body = json.loads(response.body.decode("utf-8"))
    assert body["providers"]["openai"]["api_type"] == "responses"
    assert "validation_error" not in body["providers"]["openai"]
    assert "validation_error" not in body["model_groups"]["OpenAI"]
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["providers"]["openai"]["api_type"] == "removed-protocol"


@pytest.mark.parametrize(
    ("base_url", "expected_profile"),
    [
        (
            "https://api.openai.com/v1/",
            "openai-responses-tool-mapping-only",
        ),
        ("https://relay.example/v1", "web-run-injection"),
    ],
)
def test_get_config_derives_responses_default_profile_from_authoritative_url(
    tmp_path, base_url, expected_profile
):
    config = _config_data()
    config["providers"]["openai"].update(
        {"api_type": "responses", "base_url": base_url}
    )
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    request = SimpleNamespace(
        app=SimpleNamespace(
            config_path=str(config_path),
            gateway_config=GatewayConfig(config),
        )
    )

    response = _run(get_config(request))

    body = json.loads(response.body.decode("utf-8"))
    assert body["providers"]["openai"]["default_tool_profile"] == expected_profile


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
        "type": "llm",
        "tool_profile": "builtin",
        "models": {
            "gpt-grouped": {
                "upstream_model": "gpt-upstream",
            }
        },
    }

    response = _run(put_model_group(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model_groups"]["OpenAI"] == {
        "provider": "openai",
        "type": "llm",
        "tool_profile": "builtin",
        "models": {
            "gpt-grouped": {
                "upstream_model": "gpt-upstream",
            }
        },
    }
    route, _provider = app.gateway_config.resolve("openai_responses", "gpt-grouped")
    assert route.provider_name == "openai"
    assert route.upstream_model == "gpt-upstream"
    assert route.input_modalities is None
    assert route.tool_profile_name == "builtin"


def test_put_model_group_persists_model_info_without_runtime_modality_override(
    tmp_path,
):
    config = _config_data()
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    model_info = {
        "slug": "vision-alias",
        "display_name": "Vision Alias",
        "description": "Custom model metadata",
        "identity": "Vision Alias by Example",
        "priority": 10,
        "context_window": 262_144,
        "input_modalities": ["text", "image"],
        "supported_reasoning_levels": ["high"],
    }
    request = SimpleNamespace(app=app, path_params={"name": "Vision"})
    request.json = lambda: {
        "provider": "openai",
        "type": "llm",
        "tool_profile": "builtin",
        "models": {"vision-alias": {"model_info": model_info}},
    }

    response = _run(put_model_group(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model_groups"]["Vision"]["models"]["vision-alias"] == {
        "model_info": model_info
    }
    route, _provider = app.gateway_config.resolve("openai_responses", "vision-alias")
    assert route.input_modalities is None
    assert route.tool_profile


def test_put_model_group_rejects_embedding_type(tmp_path):
    config_path = tmp_path / "config.jsonc"
    original = json.dumps(_config_data())
    config_path.write_text(original, encoding="utf-8")
    initial_config = GatewayConfig(_config_data())
    app = SimpleNamespace(config_path=str(config_path), gateway_config=initial_config)
    request = SimpleNamespace(app=app, path_params={"name": "Embeddings"})
    request.json = lambda: {
        "provider": "openai",
        "type": "embedding",
        "models": {"text-embedding": {}},
    }

    response = _run(put_model_group(request))

    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "'type' must be 'llm'"}
    assert config_path.read_text(encoding="utf-8") == original


def test_local_mode_model_save_syncs_catalog_and_disable_clears_it(tmp_path):
    config = _config_data()
    config["server"].update({"local_mode": True, "local_mode_confirmed": True})
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        codex_home=str(codex_home),
        gateway_port=45678,
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "OpenAI"})
    request.json = lambda: {
        "provider": "openai",
        "type": "llm",
        "tool_profile": "builtin",
        "models": {"third-party-model": {}},
    }

    response = _run(put_model_group(request))

    assert response.status_code == 200
    catalog = json.loads(
        (codex_home / "model_catalog.json").read_text(encoding="utf-8")
    )
    custom = next(
        model for model in catalog["models"] if model["slug"] == "third-party-model"
    )
    assert custom["display_name"] == custom["description"] == "third-party-model"
    config_toml = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert str(codex_home / "model_catalog.json") in config_toml
    assert 'model_provider = "codex_rosetta"' in config_toml
    assert 'base_url = "http://127.0.0.1:45678/v1"' in config_toml
    saved_after_sync = json.loads(config_path.read_text(encoding="utf-8"))
    codex_key = next(
        entry
        for entry in saved_after_sync["server"]["api_keys"]
        if entry["id"] == "codex"
    )
    assert f'experimental_bearer_token = "{codex_key["key"]}"' in config_toml

    delete_request = SimpleNamespace(app=app, path_params={"name": "OpenAI"})
    delete_response = _run(delete_model_group(delete_request))
    assert delete_response.status_code == 200
    catalog_after_delete = json.loads(
        (codex_home / "model_catalog.json").read_text(encoding="utf-8")
    )
    assert "third-party-model" not in {
        model["slug"] for model in catalog_after_delete["models"]
    }
    assert len(catalog_after_delete["models"]) == 8

    disable_request = SimpleNamespace(app=app)
    disable_request.json = lambda: {"local_mode": False}
    disable_response = _run(put_server_settings(disable_request))

    assert disable_response.status_code == 200
    assert not (codex_home / "model_catalog.json").exists()
    assert "model_catalog_json" not in (codex_home / "config.toml").read_text(
        encoding="utf-8"
    )
    assert "model_providers.codex_rosetta" not in (
        codex_home / "config.toml"
    ).read_text(encoding="utf-8")
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["local_mode"] is False


def test_put_codex_settings_syncs_task_models_to_catalog_and_memories(tmp_path):
    config = _config_data()
    config["server"].update({"local_mode": True, "local_mode_confirmed": True})
    config["model_groups"]["OpenAI"]["models"] = {
        "review-alias": {},
        "consolidation-alias": {},
        "extract-alias": {},
    }
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    codex_home = tmp_path / "codex"
    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        codex_home=str(codex_home),
        gateway_port=45678,
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app)
    request.json = lambda: {
        "auto_review_model_override": "review-alias",
        "memories": {
            "consolidation_model": "consolidation-alias",
            "extract_model": "extract-alias",
        },
    }

    response = _run(put_codex_settings(request))

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["codex"] == request.json()
    catalog = json.loads(
        (codex_home / "model_catalog.json").read_text(encoding="utf-8")
    )
    assert all(
        model["auto_review_model_override"] == "review-alias"
        for model in catalog["models"]
    )
    codex_config = tomllib.loads(
        (codex_home / "config.toml").read_text(encoding="utf-8")
    )
    assert codex_config["memories"]["consolidation_model"] == ("consolidation-alias")
    assert codex_config["memories"]["extract_model"] == "extract-alias"

    request.json = lambda: {
        "auto_review_model_override": None,
        "memories": {"consolidation_model": None, "extract_model": None},
    }
    clear_response = _run(put_codex_settings(request))

    assert clear_response.status_code == 200
    cleared = json.loads(config_path.read_text(encoding="utf-8"))
    assert "codex" not in cleared
    cleared_codex_config = tomllib.loads(
        (codex_home / "config.toml").read_text(encoding="utf-8")
    )
    assert cleared_codex_config.get("memories", {}) == {}


@pytest.mark.parametrize(
    ("local_mode", "confirmed"),
    [(False, True), (True, False)],
)
def test_put_codex_settings_requires_confirmed_local_mode(
    tmp_path, local_mode, confirmed
):
    config = _config_data()
    config["server"].update(
        {"local_mode": local_mode, "local_mode_confirmed": confirmed}
    )
    config_path = tmp_path / "config.jsonc"
    original = json.dumps(config)
    config_path.write_text(original, encoding="utf-8")
    app = SimpleNamespace(
        config_path=str(config_path),
        gateway_config=GatewayConfig(config),
    )
    request = SimpleNamespace(
        app=app,
        json=lambda: {"auto_review_model_override": "gpt-test"},
    )

    response = _run(put_codex_settings(request))

    assert response.status_code == 409
    assert config_path.read_text(encoding="utf-8") == original


def test_enabling_local_mode_through_admin_requires_explicit_confirmation(tmp_path):
    config = _config_data()
    config["server"].update({"local_mode": False, "local_mode_confirmed": False})
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    codex_home = tmp_path / "codex"
    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        codex_home=str(codex_home),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app)
    request.json = lambda: {"local_mode": True}

    rejected = _run(put_server_settings(request))

    assert rejected.status_code == 400
    assert (
        json.loads(config_path.read_text(encoding="utf-8"))["server"]["local_mode"]
        is False
    )

    request.json = lambda: {
        "local_mode": True,
        "local_mode_confirmed": True,
    }
    accepted = _run(put_server_settings(request))

    assert accepted.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["local_mode"] is True
    assert saved["server"]["local_mode_confirmed"] is True
    assert any(entry["id"] == "codex" for entry in saved["server"]["api_keys"])
    assert (codex_home / "model_catalog.json").is_file()


def test_local_mode_sync_failure_rolls_back_admin_config_and_codex_files(
    tmp_path, monkeypatch
):
    from codex_rosetta.gateway import local_mode

    config = _config_data()
    config["server"].update({"local_mode": True, "local_mode_confirmed": True})
    config_path = tmp_path / "config.jsonc"
    original_config = json.dumps(config)
    config_path.write_text(original_config, encoding="utf-8")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text('model = "original"\n', encoding="utf-8")
    initial_config = GatewayConfig(config)
    app = SimpleNamespace(
        config_path=str(config_path),
        codex_home=str(codex_home),
        gateway_config=initial_config,
        stream_trace_state=StreamTraceState(initial_config.stream_trace),
        auth_state=None,
    )
    request = SimpleNamespace(app=app, path_params={"name": "OpenAI"})
    request.json = lambda: {
        "provider": "openai",
        "type": "llm",
        "tool_profile": "builtin",
        "models": {"new-model": {}},
    }
    real_atomic_write = local_mode._atomic_write_bytes
    failed = False

    def fail_once(path: str, content: bytes) -> None:
        nonlocal failed
        if path == str(config_toml) and not failed:
            failed = True
            raise OSError("simulated Codex config failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(local_mode, "_atomic_write_bytes", fail_once)

    response = _run(put_model_group(request))

    assert response.status_code == 500
    assert json.loads(config_path.read_text(encoding="utf-8")) == config
    assert config_toml.read_text(encoding="utf-8") == 'model = "original"\n'
    assert not (codex_home / "model_catalog.json").exists()
    assert "new-model" not in app.gateway_config.models


def test_delete_model_group_removes_group_and_runtime_models(tmp_path):
    """Deleting a model group removes its expanded model routes."""
    config = _config_data()
    config["model_groups"] = {
        "OpenAI": {
            "provider": "openai",
            "type": "llm",
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


def test_admin_html_confirmation_button_triggers_action():
    """The full second-stage confirmation button executes the pending action."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")
    inline_confirm = html[
        html.index("function inlineConfirm(btn, action)") : html.index(
            "function openFetchModelsModal()"
        )
    ]

    assert (
        "btn.onclick = (e) => { e.stopPropagation(); clearTimeout(timer); "
        "revert(); action(); };"
    ) in inline_confirm


def test_admin_html_renders_tools_as_compact_cards():
    """Tools render in a three-column grid beside one shared detail column."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")
    render_item = html[
        html.index(
            "function renderToolItem(item, policies, nested=false)"
        ) : html.index("function renderToolNamespace(namespaceItem, childIds, index)")
    ]

    assert (
        ".tool-catalog-layout { display:grid;grid-template-columns:minmax(0,3fr) minmax(260px,1fr)"
        in html
    )
    assert ".tool-card-grid { grid-template-columns:repeat(3,minmax(0,1fr))" in html
    assert "renderToolStateSelect(item)" in render_item
    assert "toolStateClass(item)" in render_item
    assert "renderToolProfileInputs(item)" not in render_item
    assert "item.description_i18n" not in render_item
    assert "renderToolKindBadge(item)" not in render_item
    assert "renderToolPolicy(item, policy)" not in render_item
    assert '<div class="tool-list tool-card-grid">${cards}</div>' in html
    assert '<div class="tool-list">${namespaces}</div>' in html
    assert 'id="toolDetailPanel"' in html
    assert "function renderToolDetail()" in html
    assert ".tool-item.selected, .tool-namespace-head.selected { background:" in html
    assert ".tool-item.tool-state-passthrough" in html
    assert "border-color:var(--green)" in html
    assert ".tool-item.tool-state-modified" in html
    assert "border-color:var(--orange)" in html
    assert ".tool-item.tool-state-disabled" in html
    assert "border-color:var(--red)" in html
    assert (
        ".tool-item.selected, .tool-namespace-head.selected { border-color:" not in html
    )
    assert "${esc(t('tools.default'))}:" not in render_item


def test_admin_html_function_filter_excludes_namespace_group():
    """The Function filter renders only the standalone Function group."""
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")
    render_catalog = html[
        html.index("function renderToolCatalog()") : html.index("function doLogout()")
    ]
    render_namespace = html[
        html.index(
            "function renderToolNamespace(namespaceItem, childIds, index)"
        ) : html.index("function renderToolGroup(groupId, itemIds, index)")
    ]

    assert (
        "toolCatalogFilter === 'all' || toolCatalogFilter === 'namespace'"
        in render_catalog
    )
    assert "toolCatalogFilter === 'function'" not in render_namespace


def test_admin_html_exposes_one_responses_protocol_without_handling_hints():
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")
    i18n = _load_admin_i18n()

    assert i18n["en"]["protocol.responses"] == "OpenAI Responses"
    assert "{value: 'responses', labelKey: 'protocol.responses'}" in html
    assert "{value: 'responses_passthrough'" not in html
    assert "{value: 'responses_rosetta'" not in html
    assert 'id="provProtocolHint"' not in html
    assert "protocol.responsesPassthroughHint" not in html
    assert "protocol.responsesRosettaHint" not in html


def test_admin_html_shows_model_group_profile_for_all_llm_protocols():
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")

    assert 'id="modelGroupProvider" onchange="onModelGroupProviderChange()"' in html
    assert "return !!provider;" in html
    assert "responses_pass_through" not in html
    assert "group?.tool_profile || _defaultToolProfileForProvider(provider)" in html
    assert "_modelGroupProviderUsesToolProfiles() ? '' : 'none'" in html
    assert "if (_modelGroupProviderUsesToolProfiles())" in html
    assert "function _defaultToolProfileForProvider(providerName)" in html
    assert "return provider.default_tool_profile || 'builtin';" in html
    assert "provider.provider" not in html
    assert "group.validation_error || ''" in html
    assert "cfg?.validation_error || selection.validationError" in html


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


def test_admin_html_exposes_confirmed_local_mode_setting():
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")
    i18n = _load_admin_i18n()

    assert 'id="localModeEnabled"' in html
    assert "configData.server.local_mode !== false" in html
    assert "server.local_mode_confirmed !== true" in html
    assert "configData.codex_home" in html
    assert "configData.model_catalog_configured === true" in html
    assert "body.local_mode_confirmed = true" in html
    assert i18n["en"]["label.localMode"] == "Local mode"
    assert (
        "configure the codex_rosetta provider in config.toml"
        in i18n["en"]["confirm.localMode"]
    )
    assert "stable gateway API key named codex" in i18n["en"]["confirm.localMode"]
    assert "配置codex_rosetta Provider" in i18n["zh"]["confirm.localMode"]
    assert i18n["en"]["confirm.localModeExisting"]


def test_admin_html_requires_confirmation_to_close_codex_restart_notice():
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")
    i18n = _load_admin_i18n()
    notice_source = html[
        html.index("function showCodexRestartNotice()") : html.index(
            "// ===================== Modal ====================="
        )
    ]

    assert 'id="codexRestartNotice"' in html
    assert 'onclick="confirmCodexRestartNotice()"' in html
    assert "r.headers.get('X-Codex-Restart-Required') === 'true'" in html
    assert "hidden = false" in notice_source
    assert "hidden = true" in notice_source
    assert "setTimeout" not in notice_source
    assert "Restart Codex" in i18n["en"]["notice.codexRestart"]
    assert "重启 Codex" in i18n["zh"]["notice.codexRestart"]
    assert i18n["en"]["btn.confirm"] == "Confirm"
    assert i18n["zh"]["btn.confirm"] == "确认"


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
    i18n = _load_admin_i18n()

    assert 'id="provProvider"' in html
    assert 'id="provProviderVariant"' in html
    assert 'id="provApiType"' in html
    assert 'id="provAllowRedirects"' in html
    assert "const PROVIDER_PRESETS" in html
    assert "const PROVIDER_VENDOR_PRESETS" in html
    assert "PROTOCOL_DIVIDER_VALUE" in html
    assert "divider.disabled = true" in html
    assert "opt.dataset.unsupported = 'true'" in html
    assert i18n["en"]["provider.kimi"] == "Kimi"
    assert i18n["en"]["provider.minimax"] == "MiniMax"
    assert i18n["en"]["providerVariant.official"] == "Official"
    assert i18n["en"]["providerVariant.china"] == "China"
    assert i18n["en"]["providerVariant.international"] == "International"
    assert i18n["en"]["providerVariant.custom"] == "Custom"
    assert i18n["en"]["provider.qwen"] == "Qwen"
    assert i18n["zh"]["provider.qwen"] == "通义千问"
    assert i18n["en"]["provider.zhipu"] == "Zhipu (GLM)"
    assert i18n["zh"]["provider.zhipu"] == "智谱 GLM"
    assert "protocol.unsupportedSuffix" in html
    assert (
        "const body = {api_type: apiType, base_url: baseUrl, proxy, "
        "allow_redirects: allowRedirects}" in html
    )
    assert i18n["en"]["label.allowRedirects"] == "Allow redirects"
    assert i18n["zh"]["label.allowRedirects"] == "允许重定向"
    assert 'id="provType"' not in html
    assert "variantSel.value = 'custom'" in html
    assert "document.getElementById('provProvider').value = 'custom'" not in html

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
    i18n = _load_admin_i18n()

    assert 'onclick="openModelGroupModal()"' in html
    assert 'id="modelGroupList"' in html
    assert 'id="modelGroupModal"' in html
    assert 'id="modelGroupRows"' in html
    assert 'id="fetchModelGroup"' in html
    assert "max-height: 90vh; overflow-y: auto;" in html
    assert "function openModelGroupModal(groupName)" in html
    assert "function toggleModelGroup(groupName)" in html
    assert "function onModelGroupTypeChange()" not in html
    assert "function saveModelGroup()" in html
    assert "/admin/api/config/model-groups/" in html
    assert "/admin/api/config/models" not in html
    assert "standalone_models" not in html
    assert "_collapsedModelGroups" in html
    assert "model-group-card${collapsed ? ' collapsed' : ''}" in html
    assert 'class="model-group-body"' in html
    assert 'name="modelGroupType"' not in html
    assert 'class="checkbox-group group-cap-wrap"' not in html
    assert 'id="modelInfoModal"' in html
    assert "function refreshModelGroupRowPreset(row)" in html
    assert "function openModelInfo(row)" in html
    assert "const MODEL_INFO_PRESET_FIELDS = [" in html
    assert "function _modelInfoMatchesPreset(info, preset)" in html
    comparison_fields = (
        "slug",
        "display_name",
        "description",
        "identity",
        "priority",
        "context_window",
        "input_modalities",
        "supported_reasoning_levels",
    )
    comparison_section = html[
        html.index("const MODEL_INFO_PRESET_FIELDS = [") : html.index(
            "function updateModelInfoRestorePresetLabel(row)"
        )
    ]
    for field in comparison_fields:
        assert f"'{field}'" in comparison_section
    assert "current.every((value, index) => value === expected[index])" in html
    assert "t(modified ? 'modelInfo.detectedModified' : 'modelInfo.detected'" in html
    assert ".model-preset-status.modified { color:var(--orange); }" in html
    assert 'id="modelInfoRestorePreset"' in html
    assert "function updateModelInfoRestorePresetLabel(row)" in html
    assert "t('modelInfo.restorePreset', {display_name: preset.display_name})" in html
    assert (
        'class="checkbox-group model-info-checkbox-group" id="modelInfoModalities"'
        in html
    )
    assert (
        'class="checkbox-group model-info-checkbox-group" id="modelInfoReasoningLevels"'
        in html
    )
    assert html.count('<input type="checkbox" value="text">text') == 1
    assert html.count('<input type="checkbox" value="image">image') == 1
    for effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
        assert html.count(f'<input type="checkbox" value="{effort}">{effort}') == 1
    assert (
        "_setModelInfoCheckboxes('modelInfoModalities', info?.input_modalities)" in html
    )
    assert "input_modalities: _checkedModelInfoValues('modelInfoModalities')" in html
    assert (
        "supported_reasoning_levels: _checkedModelInfoValues('modelInfoReasoningLevels')"
        in html
    )
    assert "function _commaSeparatedModelInfo(fieldId)" not in html
    assert "entry.model_info = structuredClone(row._modelInfo)" in html
    assert 'id="fetchCapText"' not in html
    assert 'id="fetchCapVision"' not in html
    assert 'name="fetchModelType"' not in html
    assert "type: 'llm'" in html
    assert 'id="modelReasoningGroup"' not in html
    assert 'id="modelToolAdaptationGroup"' not in html
    assert i18n["en"]["btn.addModelGroup"] == "+ Add Model Group"
    assert i18n["zh"]["btn.addModelGroup"] == "+ 添加模型组"
    assert i18n["en"]["modelInfo.modalities"] == "Input Modalities"
    assert i18n["zh"]["modelInfo.reasoningLevels"] == "支持的推理等级"
    assert (
        i18n["zh"]["modelInfo.detectedModified"] == "自动检测: {display_name}(已修改)"
    )
    assert i18n["zh"]["modelInfo.restorePreset"] == "恢复{display_name}预设配置"


def test_admin_html_exposes_local_mode_task_model_selects():
    html_path = (
        Path(__file__).parents[2]
        / "src"
        / "codex_rosetta"
        / "gateway"
        / "admin"
        / "admin.html"
    )
    html = html_path.read_text(encoding="utf-8")
    i18n = _load_admin_i18n()

    assert html.index('data-i18n="section.codexTaskModels"') < html.index(
        'data-i18n="section.models"'
    )
    assert 'id="autoReviewModel"' in html
    assert 'id="memoryConsolidationModel"' in html
    assert 'id="memoryExtractModel"' in html
    assert "auto_review_model_override: 'codex-auto-review'" in html
    assert "consolidation_model: 'gpt-5.4'" in html
    assert "extract_model: 'gpt-5.4-mini'" in html
    assert "server.local_mode !== false && server.local_mode_confirmed === true" in html
    assert "'task-model-configured' : 'task-model-missing'" in html
    assert "'task-model-configured' : 'task-model-unconfigured'" in html
    assert "/admin/api/config/codex" in html
    assert i18n["zh"]["label.autoApprovalModel"] == ("自动审批(默认codex-auto-review)")
    assert i18n["zh"]["label.memoryConsolidationModel"] == ("记忆固化(默认gpt-5.4)")
    assert i18n["zh"]["label.memoryExtractModel"] == ("记忆提取(默认gpt-5.4-mini)")


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
    assert 'href="/admin/network-search"' in html
    assert 'href="/admin/dashboard"' in html
    assert 'href="/admin/logs"' in html
    assert 'href="/admin/gateway-logs"' in html
    assert 'data-page="keys"' in html
    assert 'data-page="network-search"' in html
    assert 'id="page-keys"' in html
    assert 'id="page-network-search"' in html
    network_page = html.split('id="page-network-search"', 1)[1].split(
        "<!-- Dashboard Page -->", 1
    )[0]
    assert 'id="networkSearchProvider"' in network_page
    assert '<option value="self_hosted_google">Self-hosted (Google)</option>' in (
        network_page
    )
    assert (
        '<option value="self_hosted_bing">Self-hosted (Bing RSS)</option>'
        in network_page
    )
    assert (
        '<option value="self_hosted_bing_browser">Self-hosted (Bing Browser)</option>'
        in network_page
    )
    assert 'id="networkSearchApiKey"' in network_page
    assert "apiKeyGroup.hidden = provider.value !== 'tavily'" in html
    assert 'id="networkSearchSidecarStatus"' in network_page
    assert 'id="networkSearchBrowserStatus"' in network_page
    assert 'name="webRunBaseUrl"' not in network_page
    assert "setInterval(loadNetworkSearchStatus, 5000)" in html
    assert "clearInterval(networkSearchTimer)" in html
    assert "codex-rosetta-tab" not in html
    assert "data-tab" not in html
    assert "currentTab" not in html
