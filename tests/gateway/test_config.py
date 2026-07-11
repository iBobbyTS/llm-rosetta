"""Tests for gateway configuration parsing and validation."""

from __future__ import annotations

import json
import sys
from argparse import Namespace

import pytest

from codex_rosetta.gateway.cli import (
    _cmd_add_model,
    _cmd_add_model_group,
    _empty_config_template,
)
from codex_rosetta.gateway.config import GatewayConfig, load_config


def _secure_server(**overrides) -> dict:
    server = {
        "admin_password": "test-admin-password",
        "api_keys": [{"id": "test-client", "key": "test-gateway-key", "label": "Test"}],
    }
    server.update(overrides)
    return server


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
        "model_groups": {
            "test-llm": {
                "provider": "test",
                "type": "llm",
                "models": {"gpt-test": {"capabilities": ["text"]}},
            }
        },
        "server": _secure_server(),
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

    def test_reject_missing_password(self):
        raw = _minimal_raw()
        raw["server"].pop("admin_password")
        with pytest.raises(ValueError, match="admin_password"):
            GatewayConfig(raw)

    def test_reject_non_string_password(self):
        raw = _minimal_raw(admin_password=12345)
        with pytest.raises(ValueError, match="admin_password"):
            GatewayConfig(raw)


class TestEnvironmentSubstitution:
    """Environment placeholders are resolved as string data after JSON parsing."""

    def test_load_config_preserves_special_characters_without_structure_injection(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        special = 'admin","credential_visible":true,"injected":"\\line\nrest'
        monkeypatch.setenv("SPECIAL_ADMIN_PASSWORD", special)
        raw = _minimal_raw(
            admin_password="${SPECIAL_ADMIN_PASSWORD}",
            credential_visible=False,
        )
        raw["providers"]["test"]["api_key"] = "prefix-${SPECIAL_ADMIN_PASSWORD}-suffix"
        path = tmp_path / "config.jsonc"
        path.write_text(json.dumps(raw), encoding="utf-8")

        loaded = load_config(str(path))

        assert loaded["server"]["admin_password"] == special
        assert loaded["server"]["credential_visible"] is False
        assert "injected" not in loaded["server"]
        assert loaded["providers"]["test"]["api_key"] == f"prefix-{special}-suffix"
        assert GatewayConfig(loaded).admin_password == special

    def test_admin_candidate_substitution_keeps_special_value_as_data(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        special = 'quote"backslash\\newline\nrest'
        monkeypatch.setenv("SPECIAL_ADMIN_PASSWORD", special)
        raw = _minimal_raw(
            admin_password="${SPECIAL_ADMIN_PASSWORD}",
            credential_visible=False,
        )

        config = GatewayConfig.from_raw_with_env(raw)

        assert config.admin_password == special
        assert config.credential_visible is False


class TestGatewayAccessKeys:
    """Gateway access keys require stable, unique principal IDs."""

    def test_reject_missing_access_keys(self):
        raw = _minimal_raw()
        raw["server"].pop("api_keys")
        with pytest.raises(ValueError, match="at least one"):
            GatewayConfig(raw)

    @pytest.mark.parametrize("principal", ["", "   ", None])
    def test_reject_empty_principal_id(self, principal):
        raw = _minimal_raw()
        raw["server"]["api_keys"][0]["id"] = principal
        with pytest.raises(ValueError, match="id must be a non-empty"):
            GatewayConfig(raw)

    def test_reject_duplicate_principal_ids(self):
        raw = _minimal_raw()
        raw["server"]["api_keys"].append(
            {"id": "test-client", "key": "other-key", "label": "Other"}
        )
        with pytest.raises(ValueError, match="duplicate.*id"):
            GatewayConfig(raw)

    def test_maps_raw_keys_to_stable_principals(self):
        cfg = GatewayConfig(_minimal_raw())
        assert cfg.api_key_principals == {"test-gateway-key": "test-client"}

    @pytest.mark.parametrize("label", [{"nested": True}, ["label"], "x" * 129])
    def test_rejects_invalid_access_key_label(self, label):
        raw = _minimal_raw()
        raw["server"]["api_keys"][0]["label"] = label
        with pytest.raises(ValueError, match="label must"):
            GatewayConfig(raw)

    def test_allows_empty_access_key_label(self):
        raw = _minimal_raw()
        raw["server"]["api_keys"][0]["label"] = ""
        assert GatewayConfig(raw).api_key_labels == {"test-gateway-key": ""}

    def test_secure_defaults(self):
        cfg = GatewayConfig(_minimal_raw())
        assert cfg.host == "127.0.0.1"
        assert cfg.credential_visible is False

    def test_cli_scaffold_generates_unique_mandatory_credentials(self):
        first = _empty_config_template()
        second = _empty_config_template()

        first_config = GatewayConfig(first)
        second_config = GatewayConfig(second)
        assert first_config.admin_password != second_config.admin_password
        assert first_config.api_keys[0]["key"] != second_config.api_keys[0]["key"]
        assert first_config.host == "127.0.0.1"
        assert first_config.credential_visible is False
        assert first["server"]["request_body_limit_mb"] == 128


class TestAdminCorsOrigins:
    """Admin CORS accepts only canonical HTTP(S) origin allowlists."""

    @pytest.mark.parametrize("value", [None, "https://admin.example", {}])
    def test_rejects_non_list_allowlist(self, value):
        with pytest.raises(ValueError, match="admin_cors_origins must be a list"):
            GatewayConfig(_minimal_raw(admin_cors_origins=value))

    @pytest.mark.parametrize(
        "origin",
        [
            123,
            "ftp://admin.example",
            "https://user:password@admin.example",
            "https://admin.example/path",
            "https://admin.example?query=1",
            "https://admin.example#fragment",
            "https://admin.example:invalid",
        ],
    )
    def test_rejects_non_origin_entries(self, origin):
        with pytest.raises(ValueError, match=r"admin_cors_origins\[0\]"):
            GatewayConfig(_minimal_raw(admin_cors_origins=[origin]))

    def test_normalizes_default_ports_trailing_slashes_and_duplicates(self):
        config = GatewayConfig(
            _minimal_raw(
                admin_cors_origins=[
                    " HTTPS://ADMIN.EXAMPLE:443/ ",
                    "https://admin.example",
                    "http://localhost:80/",
                    "http://localhost:8765",
                ]
            )
        )

        assert config.admin_cors_origins == [
            "https://admin.example",
            "http://localhost",
            "http://localhost:8765",
        ]


class TestRequestBodyLimit:
    """Inbound request body limits use fixed, validated size tiers."""

    def test_defaults_to_128_mb(self):
        config = GatewayConfig(_minimal_raw())

        assert config.request_body_limit_mb == 128
        assert config.request_body_limit_config_value == 128
        assert config.request_body_limit_bytes == 128 * 1024 * 1024

    @pytest.mark.parametrize("value", [64, 128, 256, 512, 1024])
    def test_accepts_supported_size_tiers(self, value):
        config = GatewayConfig(_minimal_raw(request_body_limit_mb=value))

        assert config.request_body_limit_mb == value
        assert config.request_body_limit_config_value == value
        assert config.request_body_limit_bytes == value * 1024 * 1024

    def test_accepts_unlimited(self):
        config = GatewayConfig(_minimal_raw(request_body_limit_mb="unlimited"))

        assert config.request_body_limit_mb is None
        assert config.request_body_limit_config_value == "unlimited"
        assert config.request_body_limit_bytes == sys.maxsize

    @pytest.mark.parametrize(
        "value", [None, True, False, 0, 63, 129, 2048, "128", "none", {}]
    )
    def test_rejects_unsupported_values(self, value):
        with pytest.raises(ValueError, match="request_body_limit_mb must be one of"):
            GatewayConfig(_minimal_raw(request_body_limit_mb=value))


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


class TestProviderApiTypeResolution:
    """Provider entries can use provider/api_type instead of legacy type."""

    def test_api_type_takes_precedence_over_legacy_type(self):
        raw = {
            "providers": {
                "DeepSeek": {
                    "api_key": "sk-test",
                    "base_url": "https://api.deepseek.com",
                    "provider": "deepseek",
                    "api_type": "chat",
                    "type": "anthropic",
                }
            },
            "model_groups": {
                "DeepSeek": {
                    "provider": "DeepSeek",
                    "type": "llm",
                    "models": {"deepseek-test": {}},
                }
            },
            "server": _secure_server(),
        }

        cfg = GatewayConfig(raw)
        route, provider = cfg.resolve("openai_responses", "deepseek-test")

        assert cfg.provider_types["DeepSeek"] == "openai_chat"
        assert cfg.provider_shim_names["DeepSeek"] == "deepseek"
        assert route.target_provider == "openai_chat"
        assert route.shim_name == "deepseek"
        assert provider.base_url == "https://api.deepseek.com"

    def test_provider_api_type_can_derive_mixed_shim(self):
        raw = {
            "providers": {
                "MiniMax": {
                    "api_key": "sk-test",
                    "base_url": "https://api.minimaxi.com/anthropic",
                    "provider": "minimax_china",
                    "api_type": "anthropic",
                }
            },
            "model_groups": {
                "MiniMax": {
                    "provider": "MiniMax",
                    "type": "llm",
                    "models": {"minimax-test": {}},
                }
            },
            "server": _secure_server(),
        }

        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_responses", "minimax-test")

        assert cfg.provider_types["MiniMax"] == "anthropic"
        assert cfg.provider_shim_names["MiniMax"] == "minimax--anthropic"
        assert route.target_provider == "anthropic"
        assert route.shim_name == "minimax--anthropic"

    def test_custom_api_type_has_no_shim(self):
        raw = {
            "providers": {
                "Pixel": {
                    "api_key": "sk-test",
                    "base_url": "https://api.example.com",
                    "provider": "custom",
                    "api_type": "responses_passthrough",
                }
            },
            "model_groups": {
                "Pixel": {
                    "provider": "Pixel",
                    "type": "llm",
                    "models": {"pixel-test": {}},
                }
            },
            "server": _secure_server(),
        }

        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_chat", "pixel-test")

        assert cfg.provider_types["Pixel"] == "openai_responses"
        assert cfg.provider_shim_names["Pixel"] is None
        assert route.target_provider == "openai_responses"
        assert route.shim_name is None
        assert route.responses_processing == "passthrough"

    def test_responses_rosetta_uses_same_wire_protocol_with_conversion_mode(self):
        raw = {
            "providers": {
                "Qwen": {
                    "api_key": "sk-test",
                    "base_url": "https://api.example.com",
                    "provider": "qwen",
                    "api_type": "responses_rosetta",
                }
            },
            "model_groups": {
                "Qwen": {
                    "provider": "Qwen",
                    "type": "llm",
                    "models": {"qwen-test": {}},
                }
            },
            "server": _secure_server(),
        }

        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_responses", "qwen-test")

        assert cfg.provider_types["Qwen"] == "openai_responses"
        assert route.target_provider == "openai_responses"
        assert route.responses_processing == "rosetta"

    def test_legacy_type_config_still_resolves(self):
        raw = _minimal_raw()

        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_responses", "gpt-test")

        assert cfg.provider_types["test"] == "openai_chat"
        assert cfg.provider_shim_names["test"] == "openai"
        assert route.target_provider == "openai_chat"
        assert route.shim_name == "openai"


class TestModelGroups:
    """Model groups are the only persisted routing definition."""

    def test_top_level_models_are_ignored(self):
        raw = _minimal_raw()
        raw["models"] = {"ignored": "test"}
        cfg = GatewayConfig(raw)
        assert "ignored" not in cfg.models
        assert cfg.models == {"gpt-test": "test"}

    def test_llm_group_preserves_text_vision_and_upstream_model(self):
        raw = _minimal_raw()
        raw["model_groups"] = {
            "OpenAI": {
                "provider": "test",
                "type": "llm",
                "models": {
                    "gpt-public": {
                        "upstream_model": "gpt-upstream",
                        "capabilities": ["text", "vision"],
                    }
                },
            }
        }
        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_responses", "gpt-public")
        assert cfg.models == {"gpt-public": "test"}
        assert route.upstream_model == "gpt-upstream"
        assert route.model_capabilities == ["text", "vision"]

    def test_embedding_group_sets_embedding_capability_for_every_model(self):
        raw = _minimal_raw()
        raw["model_groups"] = {
            "Embeddings": {
                "provider": "test",
                "type": "embedding",
                "models": {"embed-public": "embed-upstream"},
            }
        }
        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_chat", "embed-public")
        assert route.upstream_model == "embed-upstream"
        assert route.model_capabilities == ["embedding"]

    @pytest.mark.parametrize("group_type", [None, "chat", ""])
    def test_group_requires_supported_type(self, group_type):
        raw = _minimal_raw()
        raw["model_groups"]["test-llm"]["type"] = group_type
        with pytest.raises(ValueError, match="type must be"):
            GatewayConfig(raw)

    def test_rejects_advanced_model_fields(self):
        raw = _minimal_raw()
        raw["model_groups"]["test-llm"]["models"]["gpt-test"] = {
            "reasoning_mapping": "auto"
        }
        with pytest.raises(ValueError, match="unsupported fields"):
            GatewayConfig(raw)

    def test_rejects_non_text_vision_llm_capabilities(self):
        raw = _minimal_raw()
        raw["model_groups"]["test-llm"]["models"]["gpt-test"] = {
            "capabilities": ["text", "tools"]
        }
        with pytest.raises(ValueError, match="unsupported capabilities"):
            GatewayConfig(raw)

    def test_duplicate_names_across_groups_are_rejected(self):
        raw = _minimal_raw()
        raw["model_groups"]["second"] = {
            "provider": "test",
            "type": "llm",
            "models": {"gpt-test": {}},
        }
        with pytest.raises(ValueError, match="defined more than once"):
            GatewayConfig(raw)

    def test_models_from_disabled_group_provider_are_skipped(self):
        raw = _minimal_raw()
        raw["providers"]["test"]["enabled"] = False
        cfg = GatewayConfig(raw)
        assert cfg.models == {}


def test_cli_add_model_group_then_grouped_model(tmp_path):
    config_path = tmp_path / "config.jsonc"
    config_path.write_text(
        json.dumps(
            {
                "providers": {
                    "test": {
                        "api_key": "sk-test",
                        "base_url": "https://api.example.test",
                        "type": "openai",
                    }
                },
                "model_groups": {},
                "server": _secure_server(),
            }
        ),
        encoding="utf-8",
    )

    _cmd_add_model_group(
        Namespace(
            config=str(config_path),
            name="Test LLMs",
            provider="test",
            type="llm",
        )
    )
    _cmd_add_model(
        Namespace(
            config=str(config_path),
            name="gpt-test",
            group="Test LLMs",
        )
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model_groups"]["Test LLMs"] == {
        "provider": "test",
        "type": "llm",
        "tool_profile": "builtin",
        "models": {"gpt-test": {}},
    }
