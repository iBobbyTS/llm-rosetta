"""Lifecycle coverage for app-owned cross-request state roots."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from codex_rosetta.gateway.app import create_app
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.codex_search_references import (
    CodexSearchReferenceScope,
    SearchQueryDraft,
    SearchResultDraft,
)
from codex_rosetta.gateway.proxy import close_resources
from codex_rosetta.gateway.state_scope import GatewayStateScope
from codex_rosetta.gateway.tool_adaptation import LocalizedToolMapping


def _config() -> GatewayConfig:
    return GatewayConfig(
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


def _scope() -> GatewayStateScope:
    return GatewayStateScope(
        principal_id="test-client",
        provider_name="test-provider",
        model="gpt-test",
        conversation_id="window-1",
        persistent=True,
    )


def _metadata_response() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": [
                        {
                            "type": "tool_call",
                            "tool_call_id": "call-1",
                            "provider_metadata": {"signature": "secret"},
                        }
                    ]
                }
            }
        ]
    }


def test_close_resources_clears_every_app_owned_state_root():
    app = cast(Any, create_app(_config()))
    scope = _scope()
    metadata = app.metadata_store.scoped(scope)
    localization = app.codex_tool_store.scoped(scope)

    metadata.cache_from_response(_metadata_response())
    localization.remember(
        LocalizedToolMapping("call-1", "Read", {}, "exec_command", {})
    )
    search_scope = CodexSearchReferenceScope("test-client", "search-session")
    app.codex_search_reference_store.remember_search(
        search_scope,
        "fingerprint",
        (
            SearchQueryDraft(
                "python",
                None,
                (SearchResultDraft("Python", "https://docs.python.org", "Docs"),),
                1,
            ),
        ),
    )

    asyncio.run(
        close_resources(
            metadata_store=app.metadata_store,
            codex_tool_store=app.codex_tool_store,
            codex_search_reference_store=app.codex_search_reference_store,
        )
    )

    assert len(metadata) == 0
    assert localization.get("call-1") is None
    assert (
        app.codex_search_reference_store.resolve(search_scope, "turn0search0") is None
    )


def test_create_app_uses_fresh_state_roots_after_same_process_shutdown():
    old_app = cast(Any, create_app(_config()))
    asyncio.run(
        close_resources(
            metadata_store=old_app.metadata_store,
            codex_tool_store=old_app.codex_tool_store,
            codex_search_reference_store=old_app.codex_search_reference_store,
        )
    )

    new_app = cast(Any, create_app(_config()))

    assert new_app.metadata_store is not old_app.metadata_store
    assert new_app.codex_tool_store is not old_app.codex_tool_store
    assert (
        new_app.codex_search_reference_store is not old_app.codex_search_reference_store
    )


def test_scoped_views_cannot_clear_their_shared_root():
    app = cast(Any, create_app(_config()))
    scope = _scope()

    for scoped_store in (
        app.metadata_store.scoped(scope),
        app.codex_tool_store.scoped(scope),
    ):
        with pytest.raises(RuntimeError, match="root store"):
            scoped_store.clear_all()
