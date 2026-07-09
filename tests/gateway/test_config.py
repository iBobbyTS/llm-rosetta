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
            "models": {"deepseek-test": "DeepSeek"},
            "server": {},
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
            "models": {"minimax-test": "MiniMax"},
            "server": {},
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
                    "api_type": "responses",
                }
            },
            "models": {"pixel-test": "Pixel"},
            "server": {},
        }

        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_chat", "pixel-test")

        assert cfg.provider_types["Pixel"] == "openai_responses"
        assert cfg.provider_shim_names["Pixel"] is None
        assert route.target_provider == "openai_responses"
        assert route.shim_name is None

    def test_legacy_type_config_still_resolves(self):
        raw = _minimal_raw()

        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_responses", "gpt-test")

        assert cfg.provider_types["test"] == "openai_chat"
        assert cfg.provider_shim_names["test"] == "openai"
        assert route.target_provider == "openai_chat"
        assert route.shim_name == "openai"


class TestModelToolAdaptation:
    """Per-model tool adaptation is available on resolved routes."""

    def test_resolve_includes_tool_adaptation(self):
        raw = _minimal_raw()
        raw["models"] = {
            "gpt-test": {
                "provider": "test",
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
        }

        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_responses", "gpt-test")

        assert cfg.model_tool_adaptations["gpt-test"] == {
            "localize_code_editing_tools": False,
            "use_apply_patch_for_code_edits": False,
            "remove_image_generation": True,
            "enable_tool_description_optimization": False,
            "enable_phase_detection": False,
            "tool_call_cache_ttl_hours": 12,
        }
        assert route.tool_adaptation == {
            "localize_code_editing_tools": False,
            "use_apply_patch_for_code_edits": False,
            "remove_image_generation": True,
            "enable_tool_description_optimization": False,
            "enable_phase_detection": False,
            "tool_call_cache_ttl_hours": 12,
        }


class TestModelGroups:
    """Model groups expand into the existing flat runtime routing table."""

    def test_group_string_mapping_uses_group_provider_and_upstream_model(self):
        raw = _minimal_raw()
        raw["models"] = {}
        raw["model_groups"] = {
            "OpenAI": {
                "provider": "test",
                "models": {
                    "gpt-public": "gpt-upstream",
                },
            }
        }

        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_responses", "gpt-public")

        assert cfg.models == {"gpt-public": "test"}
        assert cfg.model_upstream_names == {"gpt-public": "gpt-upstream"}
        assert cfg.model_capabilities == {"gpt-public": ["text"]}
        assert route.provider_name == "test"
        assert route.upstream_model == "gpt-upstream"

    def test_group_dict_mapping_preserves_capabilities_and_model_overrides(self):
        raw = _minimal_raw()
        raw["models"] = {}
        raw["model_groups"] = {
            "OpenAI": {
                "provider": "test",
                "models": {
                    "gpt-tools": {
                        "upstream_model": "gpt-tools-upstream",
                        "capabilities": ["text", "tools", "reasoning"],
                        "reasoning_override": {"thinking_type": "adaptive"},
                        "tool_adaptation": {"remove_image_generation": True},
                    },
                },
            }
        }

        cfg = GatewayConfig(raw)
        route, _provider = cfg.resolve("openai_responses", "gpt-tools")

        assert cfg.models == {"gpt-tools": "test"}
        assert cfg.model_upstream_names == {"gpt-tools": "gpt-tools-upstream"}
        assert cfg.model_capabilities == {"gpt-tools": ["text", "tools", "reasoning"]}
        assert route.reasoning_override == {"thinking_type": "adaptive"}
        assert route.tool_adaptation == {"remove_image_generation": True}

    def test_duplicate_model_names_across_flat_and_group_config_are_rejected(self):
        raw = _minimal_raw()
        raw["model_groups"] = {
            "OpenAI": {
                "provider": "test",
                "models": {"gpt-test": "gpt-upstream"},
            }
        }

        with pytest.raises(ValueError, match="defined more than once"):
            GatewayConfig(raw)

    def test_models_from_disabled_group_provider_are_skipped(self):
        raw = _minimal_raw()
        raw["providers"]["test"]["enabled"] = False
        raw["models"] = {}
        raw["model_groups"] = {
            "OpenAI": {
                "provider": "test",
                "models": {"gpt-public": "gpt-upstream"},
            }
        }

        cfg = GatewayConfig(raw)

        assert cfg.models == {}
