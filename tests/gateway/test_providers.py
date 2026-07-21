"""Tests for gateway provider metadata and auth behavior."""

from __future__ import annotations

from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.providers import build_provider_info
from codex_rosetta.gateway.transport.provider_info import KeyRing
from codex_rosetta.shims.providers import load_providers


class TestBuildProviderInfo:
    def test_argo_openai_chat_uses_bearer_auth(self, monkeypatch):
        load_providers()
        monkeypatch.setenv("ARGO_API_KEY", "pding")

        info = build_provider_info("argo--openai_chat", {})

        assert info.auth_headers() == {"Authorization": "Bearer pding"}
        assert (
            info.upstream_url("gpt5")
            == "https://apps.inside.anl.gov/argoapi/v1/chat/completions"
        )

    def test_argo_anthropic_uses_x_api_key_auth(self, monkeypatch):
        load_providers()
        monkeypatch.setenv("ARGO_API_KEY", "pding")

        info = build_provider_info("argo--anthropic", {})

        assert info.auth_headers() == {
            "x-api-key": "pding",
            "anthropic-version": "2023-06-01",
        }
        assert (
            info.upstream_url("claudeopus47")
            == "https://apps.inside.anl.gov/argoapi/v1/messages"
        )


def _gateway_config(provider: dict[str, object]) -> GatewayConfig:
    return GatewayConfig(
        {
            "providers": {"upstream": provider},
            "model_groups": {
                "test": {
                    "provider": "upstream",
                    "type": "llm",
                    "models": {"test-model": {}},
                }
            },
            "server": {
                "admin_password": "test-admin-password",
                "api_keys": [{"id": "test", "key": "gateway-key"}],
            },
        }
    )


def test_key_ring_canonical_parser_preserves_rotation_order_and_duplicates():
    ring = KeyRing(" first , , second,first, third ,, ")

    assert ring.values == ("first", "second", "first", "third")
    assert [ring.next() for _ in range(8)] == [
        "first",
        "second",
        "first",
        "third",
        "first",
        "second",
        "first",
        "third",
    ]


def test_gateway_registers_raw_csv_and_every_selectable_provider_credential():
    raw_keys = " prefix ,prefix-long, , prefix,final "
    config = _gateway_config(
        {
            "api_type": "chat",
            "api_key": raw_keys,
            "base_url": "https://upstream.example/v1",
        }
    )

    assert config.providers["upstream"].credential_values == (
        "prefix",
        "prefix-long",
        "prefix",
        "final",
    )
    assert {raw_keys, "prefix", "prefix-long", "final"} <= config.token_values


def test_gateway_registers_environment_fallback_credential(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-provider-key")

    config = _gateway_config(
        {
            "api_type": "chat",
            "base_url": "https://upstream.example/v1",
        }
    )

    assert config.providers["upstream"].credential_values == (
        "environment-provider-key",
    )
    assert "environment-provider-key" in config.token_values
