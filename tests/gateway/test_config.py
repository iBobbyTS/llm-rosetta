"""Tests for gateway configuration parsing and validation."""

from __future__ import annotations

import pytest

from llm_rosetta.gateway.config import GatewayConfig


def _minimal_raw(**server_overrides) -> dict:
    """Return a minimal valid config dict with optional server overrides."""
    raw = {
        "providers": {
            "test": {
                "api_key": "sk-test",
                "base_url": "https://api.example.com",
                "type": "openai",
            }
        },
        "models": {"gpt-test": "test"},
        "server": {},
    }
    raw["server"].update(server_overrides)
    return raw


class TestAdminPasswordUnresolvedEnvVar:
    """admin_password must not contain unresolved ${...} placeholders."""

    def test_reject_unresolved_placeholder(self):
        raw = _minimal_raw(admin_password="${ADMIN_PASSWORD}")
        with pytest.raises(ValueError, match="unresolved"):
            GatewayConfig(raw)

    def test_reject_partial_placeholder(self):
        raw = _minimal_raw(admin_password="prefix-${SOME_VAR}-suffix")
        with pytest.raises(ValueError, match="unresolved"):
            GatewayConfig(raw)

    def test_accept_literal_password(self):
        raw = _minimal_raw(admin_password="my-secret-password")
        cfg = GatewayConfig(raw)
        assert cfg.admin_password == "my-secret-password"

    def test_accept_none(self):
        raw = _minimal_raw()
        cfg = GatewayConfig(raw)
        assert cfg.admin_password is None


class TestStreamTraceConfig:
    """server.stream_trace is parsed into runtime trace settings."""

    def test_defaults_disabled(self):
        cfg = GatewayConfig(_minimal_raw())

        assert cfg.stream_trace.enabled is False
        assert cfg.stream_trace.filter == ""
        assert cfg.stream_trace.path == ""
        assert cfg.stream_trace.max_string_chars == 20_000

    def test_parses_config_values(self):
        cfg = GatewayConfig(
            _minimal_raw(
                stream_trace={
                    "enabled": True,
                    "filter": "glm,opencode",
                    "path": "~/trace/log.jsonl",
                    "max_string_chars": "5000",
                }
            )
        )

        assert cfg.stream_trace.enabled is True
        assert cfg.stream_trace.filter == "glm,opencode"
        assert cfg.stream_trace.path == "~/trace/log.jsonl"
        assert cfg.stream_trace.max_string_chars == 5000
