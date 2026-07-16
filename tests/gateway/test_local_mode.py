"""Tests for Codex local-mode catalog generation and file synchronization."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from codex_rosetta.gateway.local_mode import (
    CodexLocalModeTransaction,
    build_model_catalog,
    catalog_path,
    codex_api_key_value,
    config_toml_has_model_catalog,
    ensure_codex_api_key,
)


def _sync_transaction(
    codex_home: Path,
    raw_config: dict | None = None,
    *,
    gateway_port: int = 8765,
    api_key: str = "test-codex-key",
) -> CodexLocalModeTransaction:
    return CodexLocalModeTransaction.sync(
        str(codex_home),
        raw_config or {},
        gateway_port=gateway_port,
        api_key=api_key,
    )


def test_catalog_uses_only_configured_models_and_clones_terra_for_custom_names() -> (
    None
):
    raw = {
        "model_groups": {
            "llm": {
                "type": "llm",
                "models": {
                    "gpt-5.6-sol": {},
                    "zeta-model": {},
                    "alpha-model": {},
                },
            },
        }
    }

    catalog = build_model_catalog(raw)
    models = catalog["models"]
    slugs = [model["slug"] for model in models]

    assert slugs == ["alpha-model", "gpt-5.6-sol", "zeta-model"]

    bundled = build_model_catalog({})["models"]
    assert [model["slug"] for model in bundled] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.2",
        "codex-auto-review",
    ]

    terra = next(model for model in bundled if model["slug"] == "gpt-5.6-terra")
    custom = next(model for model in models if model["slug"] == "alpha-model")
    assert custom["slug"] == custom["display_name"] == custom["description"]
    assert custom["slug"] == "alpha-model"
    for key, value in terra.items():
        if key not in {"slug", "display_name", "description", "comp_hash"}:
            assert custom[key] == value
    assert custom["comp_hash"].startswith("rosetta-comp-v1:custom:")


def test_catalog_applies_auto_review_override_to_every_selected_model() -> None:
    raw = {
        "codex": {"auto_review_model_override": "review-alias"},
        "model_groups": {
            "models": {
                "type": "llm",
                "models": {"review-alias": {}, "work-alias": {}},
            }
        },
    }

    models = build_model_catalog(raw)["models"]

    assert {model["slug"] for model in models} == {"review-alias", "work-alias"}
    assert all(
        model["auto_review_model_override"] == "review-alias" for model in models
    )


def test_catalog_preserves_official_bundled_entries_for_configured_slugs() -> None:
    raw = {
        "model_groups": {
            "llm": {
                "type": "llm",
                "models": {"gpt-5.5": {}, "gpt-5.6-terra": {}},
            }
        }
    }

    defaults = {model["slug"]: model for model in build_model_catalog({})["models"]}
    configured = build_model_catalog(raw)["models"]

    assert [model["slug"] for model in configured] == ["gpt-5.5", "gpt-5.6-terra"]
    assert configured == [defaults["gpt-5.5"], defaults["gpt-5.6-terra"]]


@pytest.mark.parametrize(
    "model_config",
    [
        {},
        {"upstream_model": ""},
        {"upstream_model": "codex-auto-review"},
        "codex-auto-review",
    ],
)
def test_catalog_preserves_auto_review_tool_mode_without_model_mapping(
    model_config: dict[str, str] | str,
) -> None:
    raw = {
        "model_groups": {
            "review": {
                "type": "llm",
                "models": {"codex-auto-review": model_config},
            }
        }
    }

    default = next(
        model
        for model in build_model_catalog({})["models"]
        if model["slug"] == "codex-auto-review"
    )

    assert default["tool_mode"] is None
    assert build_model_catalog(raw)["models"] == [default]


@pytest.mark.parametrize(
    "model_config",
    [{"upstream_model": "deepseek-v4-flash"}, "deepseek-v4-flash"],
)
def test_catalog_forces_code_mode_for_mapped_auto_review_model(
    model_config: dict[str, str] | str,
) -> None:
    raw = {
        "model_groups": {
            "review": {
                "type": "llm",
                "models": {"codex-auto-review": model_config},
            }
        }
    }

    default = next(
        model
        for model in build_model_catalog({})["models"]
        if model["slug"] == "codex-auto-review"
    )
    expected = dict(
        default,
        tool_mode="code_mode_only",
        comp_hash="dsv4-pre",
    )

    assert build_model_catalog(raw)["models"] == [expected]


def test_catalog_materializes_named_third_party_presets_from_terra() -> None:
    expected = {
        "deepseek-v4-pro": (
            "DeepSeek V4 Pro",
            "Stronger version of DeepSeek V4",
            "DeepSeek V4 Pro",
            1_000_000,
            ["text"],
            ["high", "max"],
        ),
        "deepseek-v4-flash": (
            "DeepSeek V4 Flash",
            "Cheaper version of DeepSeek V4",
            "DeepSeek V4 Flash",
            1_000_000,
            ["text"],
            ["high", "max"],
        ),
        "glm-5.2": (
            "GLM 5.2",
            "Flagship model by Z.ai",
            "GLM 5.2 by z.ai(智谱)",
            1_000_000,
            ["text"],
            ["high", "max"],
        ),
        "qwen3.7-plus": (
            "Qwen 3.7 Plus",
            "Multi-modal Qwen 3.7",
            "Qwen 3.7 Plus by Alibaba",
            1_000_000,
            ["text", "image"],
            ["low", "medium", "high", "xhigh", "max"],
        ),
        "qwen3.7-max": (
            "Qwen 3.7 Max",
            "Stronger Qwen 3.7 (without multi-modal)",
            "Qwen 3.7 Max by Alibaba",
            1_000_000,
            ["text"],
            ["low", "medium", "high", "xhigh", "max"],
        ),
        "mimo-v2.5": (
            "MiMo V2.5",
            "Cheaper version of MiMo V2.5 by Xiaomi, best for working not coding",
            "MiMo V2.5 Flash by Xiaomi",
            1_000_000,
            ["text", "image"],
            ["high"],
        ),
        "mimo-v2.5-pro": (
            "MiMo V2.5 Pro",
            "Stronger version of MiMo V2.5 by Xiaomi, best for working not coding",
            "MiMo V2.5 Pro by Xiaomi",
            1_000_000,
            ["text", "image"],
            ["high"],
        ),
        "minimax-m3": (
            "MiniMax M3",
            "MiniMax M3",
            "MiniMax M3",
            1_000_000,
            ["text", "image"],
            ["high"],
        ),
        "kimi-k2.7-code": (
            "Kimi K2.7 Code",
            "Kimi K2.7 Code",
            "Kimi K2.7 Code by Moonshot",
            262_144,
            ["text", "image"],
            ["high"],
        ),
    }
    raw = {
        "model_groups": {
            "third-party": {
                "type": "llm",
                "models": {slug: {} for slug in expected},
            }
        }
    }

    models = {model["slug"]: model for model in build_model_catalog(raw)["models"]}
    expected_comp_hashes = {
        "deepseek-v4-pro": "dsv4-pre",
        "deepseek-v4-flash": "dsv4-pre",
        "glm-5.2": "glm-5.2",
        "qwen3.7-plus": "qwen3.7-plus",
        "qwen3.7-max": "qwen3.7-max-text",
        "mimo-v2.5": "mimo-2.5",
        "mimo-v2.5-pro": "mimo-2.5",
        "minimax-m3": "minimax-3",
        "kimi-k2.7-code": "kimi-2.7",
    }

    for slug, values in expected.items():
        display_name, description, identity, context, modalities, efforts = values
        model = models[slug]
        assert model["display_name"] == display_name
        assert model["description"] == description
        assert model["context_window"] == model["max_context_window"] == context
        assert model["input_modalities"] == modalities
        assert [level["effort"] for level in model["supported_reasoning_levels"]] == (
            efforts
        )
        if slug == "minimax-m3":
            assert model["supports_reasoning_summaries"] is True
            assert model["default_reasoning_summary"] == "none"
            assert model["truncation_policy"] == {"mode": "bytes", "limit": 10000}
            assert model["supports_parallel_tool_calls"] is True
        assert model["default_reasoning_level"] == (
            "medium" if "medium" in efforts else efforts[0]
        )
        assert model["supports_image_detail_original"] is False
        assert model["tool_mode"] == "code_mode_only"
        assert model["apply_patch_tool_type"] == "freeform"
        assert model["supports_parallel_tool_calls"] is (slug == "minimax-m3")
        assert model["supports_search_tool"] is True
        assert model["web_search_tool_type"] == "text_and_image"
        assert model["use_responses_lite"] is True
        assert model["multi_agent_version"] == "v2"
        assert model["support_verbosity"] is False
        assert model["default_verbosity"] is None
        assert model["service_tiers"] == []
        assert model["additional_speed_tiers"] == []
        assert model["effective_context_window_percent"] == 85
        assert model["comp_hash"] == expected_comp_hashes[slug]
        assert identity in model["base_instructions"]
        assert "GPT-5" not in model["base_instructions"]
        messages = json.dumps(model["model_messages"], ensure_ascii=False)
        assert identity in messages
        assert "GPT-5" not in messages


def test_catalog_detects_preset_from_upstream_model_for_an_exposed_alias() -> None:
    raw = {
        "model_groups": {
            "third-party": {
                "type": "llm",
                "models": {"deepseek-alias": {"upstream_model": "deepseek-v4-pro"}},
            }
        }
    }

    [model] = build_model_catalog(raw)["models"]

    assert model["slug"] == "deepseek-alias"
    assert model["display_name"] == "DeepSeek V4 Pro"
    assert model["context_window"] == 1_000_000
    assert model["input_modalities"] == ["text"]
    assert model["comp_hash"] == "dsv4-pre"


@pytest.mark.parametrize(
    ("alias", "upstream", "expected_hash"),
    [
        ("qwen3.7-max", None, "qwen3.7-max-text"),
        (
            "qwen-image-alias",
            "qwen3.7-max-2026-06-08",
            "qwen3.7-max-image",
        ),
    ],
)
def test_catalog_honors_explicit_preset_compaction_hash(
    alias: str, upstream: str | None, expected_hash: str
) -> None:
    model_config = {} if upstream is None else {"upstream_model": upstream}
    for provider in ("provider-a", "provider-b"):
        raw = {
            "model_groups": {
                "third-party": {
                    "provider": provider,
                    "type": "llm",
                    "models": {alias: model_config},
                }
            }
        }

        [model] = build_model_catalog(raw)["models"]

        assert model["slug"] == alias
        assert model["comp_hash"] == expected_hash


def test_catalog_compaction_hash_depends_only_on_upstream_model_name() -> None:
    def comp_hash(alias: str, upstream: str, provider: str) -> str:
        raw = {
            "model_groups": {
                "models": {
                    "provider": provider,
                    "type": "llm",
                    "models": {alias: {"upstream_model": upstream}},
                }
            }
        }
        [model] = build_model_catalog(raw)["models"]
        return model["comp_hash"]

    expected = (
        "rosetta-comp-v1:custom:" + hashlib.sha256(b"shared-upstream-model").hexdigest()
    )
    assert comp_hash("first-alias", "shared-upstream-model", "provider-a") == expected
    assert comp_hash("second-alias", "shared-upstream-model", "provider-b") == expected
    assert comp_hash("first-alias", "different-upstream", "provider-a") != expected

    defaults = {model["slug"]: model for model in build_model_catalog({})["models"]}
    assert (
        comp_hash("official-alias", "gpt-5.5", "provider-a")
        == defaults["gpt-5.5"]["comp_hash"]
    )


def test_catalog_applies_complete_manual_model_info_to_an_exposed_alias() -> None:
    model_info = {
        "slug": "ignored-source-slug",
        "display_name": "Custom Vision Model",
        "description": "A manually configured model",
        "identity": "Custom Vision Model by Example",
        "priority": 7,
        "context_window": 131_072,
        "input_modalities": ["text", "image"],
        "supported_reasoning_levels": ["medium", "high"],
    }
    raw = {
        "model_groups": {
            "custom": {
                "type": "llm",
                "models": {"custom-alias": {"model_info": model_info}},
            }
        }
    }

    [model] = build_model_catalog(raw)["models"]

    assert model["slug"] == "custom-alias"
    assert model["display_name"] == "Custom Vision Model"
    assert model["description"] == "A manually configured model"
    assert model["priority"] == 7
    assert model["context_window"] == model["max_context_window"] == 131_072
    assert model["input_modalities"] == ["text", "image"]
    assert [level["effort"] for level in model["supported_reasoning_levels"]] == [
        "medium",
        "high",
    ]


def test_catalog_compaction_hash_groups_are_stable_and_non_null() -> None:
    requested = {
        "gpt-5.6-sol": {},
        "gpt-5.6-terra": {},
        "gpt-5.6-luna": {},
        "gpt-5.5": {},
        "gpt-5.4": {},
        "gpt-5.4-mini": {},
        "gpt-5.2": {},
        "codex-auto-review": {},
        "deepseek-v4-flash": {},
        "deepseek-v4-pro": {},
        "glm-5.2": {},
        "qwen3.7-plus": {},
        "qwen3.7-max": {},
        "mimo-v2.5": {},
        "mimo-v2.5-pro": {},
        "minimax-m3": {},
        "kimi-k2.7-code": {},
        "unlisted-model": {},
    }
    catalog = build_model_catalog(
        {"model_groups": {"models": {"type": "llm", "models": requested}}}
    )
    hashes = {model["slug"]: model["comp_hash"] for model in catalog["models"]}
    assert all(isinstance(value, str) and value for value in hashes.values())
    assert hashes["gpt-5.6-sol"] == hashes["gpt-5.6-terra"] == hashes["gpt-5.6-luna"]
    assert hashes["gpt-5.5"] == hashes["gpt-5.4"] == hashes["gpt-5.4-mini"]
    assert hashes["deepseek-v4-flash"] == hashes["deepseek-v4-pro"]
    assert hashes["mimo-v2.5"] == hashes["mimo-v2.5-pro"]
    groups = {
        hashes["gpt-5.6-sol"],
        hashes["gpt-5.5"],
        hashes["gpt-5.2"],
        hashes["codex-auto-review"],
        hashes["deepseek-v4-flash"],
        hashes["glm-5.2"],
        hashes["qwen3.7-plus"],
        hashes["qwen3.7-max"],
        hashes["mimo-v2.5"],
        hashes["minimax-m3"],
        hashes["kimi-k2.7-code"],
        hashes["unlisted-model"],
    }
    assert len(groups) == 12


def test_sync_replaces_catalog_setting_and_preserves_other_toml(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    external_catalog = tmp_path / "external.json"
    external_catalog.write_text("keep", encoding="utf-8")
    original = (
        f'model_catalog_json = "{external_catalog}"\n'
        'model = "gpt-5.6-sol"\n\n'
        "# keep this comment\n"
        "[profile.test]\n"
        'model_catalog_json = "/profile/catalog.json"\n'
        'personality = "pragmatic"\n'
    )
    config_toml = codex_home / "config.toml"
    config_toml.write_text(original, encoding="utf-8")

    transaction = _sync_transaction(codex_home)
    transaction.apply()

    updated = config_toml.read_text(encoding="utf-8")
    expected_catalog = str(codex_home / "model_catalog.json")
    assert updated.startswith(f'model_catalog_json = "{expected_catalog}"\n')
    assert updated.count("model_catalog_json") == 1
    assert 'model_provider = "codex_rosetta"' in updated
    assert (
        "enabled-reasoning-efforts = "
        '["low", "medium", "high", "xhigh", "max", "ultra"]' in updated
    )
    assert "[model_providers.codex_rosetta]" in updated
    assert 'name = "OpenAI"' in updated
    assert 'wire_api = "responses"' in updated
    assert "requires_openai_auth = true" in updated
    assert 'base_url = "http://127.0.0.1:8765/v1"' in updated
    assert 'experimental_bearer_token = "test-codex-key"' in updated
    assert 'model = "gpt-5.6-sol"' in updated
    assert "# keep this comment" in updated
    assert "[profile.test]" in updated
    assert 'personality = "pragmatic"' in updated
    assert external_catalog.read_text(encoding="utf-8") == "keep"
    assert config_toml_has_model_catalog(str(codex_home)) is True

    written = json.loads(Path(catalog_path(str(codex_home))).read_text("utf-8"))
    assert len(written["models"]) == 8

    transaction.rollback()
    assert config_toml.read_text(encoding="utf-8") == original
    assert not Path(catalog_path(str(codex_home))).exists()


def test_sync_uncomments_existing_local_mode_assignments_in_place(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        '# model_catalog_json = "/stale/catalog.json"\n'
        'model = "gpt-5.6-sol"\n'
        '# model_provider = "other"\n\n'
        "[features]\n"
        "multi_agent_v2 = true\n",
        encoding="utf-8",
    )

    transaction = _sync_transaction(codex_home)
    transaction.apply()

    updated = config_toml.read_text(encoding="utf-8")
    expected_catalog = str(codex_home / "model_catalog.json")
    assert updated.startswith(f'model_catalog_json = "{expected_catalog}"\n')
    assert 'model = "gpt-5.6-sol"\nmodel_provider = "codex_rosetta"\n' in updated
    assert "# model_catalog_json" not in updated
    assert "# model_provider" not in updated
    assert updated.count("model_catalog_json =") == 1
    assert updated.count("model_provider =") == 1


def test_sync_writes_and_clear_removes_only_memory_model_overrides(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        "[memories]\n"
        "generate_memories = false\n"
        'extract_model = "old-extract"\n'
        'consolidation_model = "old-consolidation"\n\n'
        "[features]\n"
        "multi_agent_v2 = true\n",
        encoding="utf-8",
    )
    raw = {
        "codex": {
            "memories": {
                "extract_model": "extract-alias",
                "consolidation_model": "consolidation-alias",
            }
        }
    }

    _sync_transaction(codex_home, raw).apply()

    parsed = tomllib.loads(config_toml.read_text(encoding="utf-8"))
    assert parsed["memories"] == {
        "generate_memories": False,
        "extract_model": "extract-alias",
        "consolidation_model": "consolidation-alias",
    }
    assert parsed["features"]["multi_agent_v2"] is True

    CodexLocalModeTransaction.clear(str(codex_home)).apply()

    cleared = tomllib.loads(config_toml.read_text(encoding="utf-8"))
    assert cleared["memories"] == {"generate_memories": False}
    assert cleared["features"]["multi_agent_v2"] is True


def test_sync_only_uncomments_assignment_that_already_exists(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        '# model_provider = "codex_rosetta"\nmodel = "gpt-5.6-sol"\n',
        encoding="utf-8",
    )

    transaction = _sync_transaction(codex_home)
    transaction.apply()

    updated = config_toml.read_text(encoding="utf-8")
    assert updated.startswith(
        f'model_catalog_json = "{codex_home / "model_catalog.json"}"\n'
        'model_provider = "codex_rosetta"\n'
        'model = "gpt-5.6-sol"\n'
    )
    assert (
        "[desktop]\nenabled-reasoning-efforts = "
        '["low", "medium", "high", "xhigh", "max", "ultra"]\n' in updated
    )


def test_sync_preserves_complete_enabled_reasoning_efforts_line(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    existing = (
        "[desktop]\nenabled-reasoning-efforts = "
        '["ultra", "max", "xhigh", "high", "medium", "low"] # keep order\n'
    )
    config_toml.write_text(existing, encoding="utf-8")

    _sync_transaction(codex_home).apply()

    updated = config_toml.read_text(encoding="utf-8")
    assert existing in updated
    assert updated.count("enabled-reasoning-efforts =") == 1


@pytest.mark.parametrize(
    "existing",
    (
        "",
        '[desktop]\nenabled-reasoning-efforts = ["low", "medium", "high"]\n',
        "[desktop]\nenabled-reasoning-efforts = "
        '["low", "medium", "high", "xhigh", "max", "bad"]\n',
    ),
)
def test_sync_writes_all_enabled_reasoning_efforts_when_missing_or_incomplete(
    tmp_path: Path, existing: str
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(existing, encoding="utf-8")

    _sync_transaction(codex_home).apply()

    updated = config_toml.read_text(encoding="utf-8")
    expected = (
        'enabled-reasoning-efforts = ["low", "medium", "high", "xhigh", "max", "ultra"]'
    )
    assert expected in updated
    assert updated.count("enabled-reasoning-efforts =") == 1


def test_sync_removes_buggy_root_duplicate_and_updates_desktop_setting(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        "enabled-reasoning-efforts = "
        '["low", "medium", "high", "xhigh", "max", "ultra"]\n'
        'model = "gpt-5.6-sol"\n\n'
        "[desktop]\n"
        "preventSleepWhileRunning = true\n"
        "keepRemoteControlAwakeWhilePluggedIn = false\n\n"
        'enabled-reasoning-efforts = ["low", "medium", "high"]\n\n'
        "[features]\n"
        "multi_agent_v2 = true\n",
        encoding="utf-8",
    )

    _sync_transaction(codex_home).apply()

    updated = config_toml.read_text(encoding="utf-8")
    parsed = tomllib.loads(updated)
    assert "enabled-reasoning-efforts" not in parsed
    assert parsed["desktop"]["enabled-reasoning-efforts"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert updated.count("enabled-reasoning-efforts =") == 1
    assert (
        "keepRemoteControlAwakeWhilePluggedIn = false\n"
        "enabled-reasoning-efforts = "
        '["low", "medium", "high", "xhigh", "max", "ultra"]\n\n'
        "[features]" in updated
    )


def test_sync_overwrites_selected_provider_but_preserves_other_provider_params(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        'model_provider = "other"\n'
        'model = "gpt-5.6-sol"\n\n'
        "[model_providers.other]\n"
        'name = "Other"\n'
        'base_url = "https://other.example/v1"\n'
        'custom_parameter = "keep"\n\n'
        "[model_providers.codex_rosetta]\n"
        'name = "Old"\n'
        'base_url = "http://old.example/v1"\n'
        'experimental_bearer_token = "old-key"\n\n'
        "[model_providers.codex_rosetta.extra]\n"
        'stale = "remove"\n',
        encoding="utf-8",
    )

    transaction = _sync_transaction(
        codex_home, gateway_port=43210, api_key="stable-codex-key"
    )
    transaction.apply()
    updated = config_toml.read_text(encoding="utf-8")

    assert updated.count('model_provider = "codex_rosetta"') == 1
    assert updated.count("[model_providers.codex_rosetta]") == 1
    assert 'base_url = "http://127.0.0.1:43210/v1"' in updated
    assert 'experimental_bearer_token = "stable-codex-key"' in updated
    assert "http://old.example/v1" not in updated
    assert "[model_providers.codex_rosetta.extra]" not in updated
    assert "stale" not in updated
    assert "[model_providers.other]" in updated
    assert 'base_url = "https://other.example/v1"' in updated
    assert 'custom_parameter = "keep"' in updated
    parsed = tomllib.loads(updated)
    assert parsed["model_provider"] == "codex_rosetta"
    assert parsed["model_providers"]["codex_rosetta"] == {
        "name": "OpenAI",
        "wire_api": "responses",
        "requires_openai_auth": True,
        "base_url": "http://127.0.0.1:43210/v1",
        "experimental_bearer_token": "stable-codex-key",
    }
    assert parsed["model_providers"]["other"]["custom_parameter"] == "keep"

    second = _sync_transaction(
        codex_home, gateway_port=43210, api_key="stable-codex-key"
    )
    second.apply()
    assert config_toml.read_text(encoding="utf-8") == updated


def test_ensure_codex_api_key_creates_once_and_never_rotates() -> None:
    raw = {
        "server": {
            "api_keys": [{"id": "existing", "label": "Existing", "key": "existing-key"}]
        }
    }

    assert ensure_codex_api_key(raw) is True
    first_key = codex_api_key_value(raw["server"]["api_keys"])
    assert first_key.startswith("rsk-")
    assert raw["server"]["api_keys"][-1] == {
        "id": "codex",
        "label": "codex",
        "key": first_key,
    }

    assert ensure_codex_api_key(raw) is False
    assert codex_api_key_value(raw["server"]["api_keys"]) == first_key


def test_ensure_codex_api_key_reuses_existing_named_label() -> None:
    raw = {
        "server": {
            "api_keys": [
                {"id": "ui-created", "label": "codex", "key": "existing-codex-key"}
            ]
        }
    }

    assert ensure_codex_api_key(raw) is False
    assert codex_api_key_value(raw["server"]["api_keys"]) == "existing-codex-key"
    assert len(raw["server"]["api_keys"]) == 1


def test_clear_removes_only_rosetta_catalog_and_toml_assignments(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    managed = codex_home / "model_catalog.json"
    managed.write_text("managed", encoding="utf-8")
    external = tmp_path / "do-not-delete.json"
    external.write_text("external", encoding="utf-8")
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        f'model_catalog_json = "{external}"\n'
        'model_provider = "codex_rosetta"\n'
        'enabled-reasoning-efforts = ["low", "medium", "high", "xhigh", "max", "ultra"]\n'
        'model = "gpt-5.6-sol"\n\n'
        "[model_providers.other]\n"
        'base_url = "https://keep.example/v1"\n\n'
        "[model_providers.codex_rosetta]\n"
        'base_url = "http://127.0.0.1:8765/v1"\n',
        encoding="utf-8",
    )

    transaction = CodexLocalModeTransaction.clear(str(codex_home))
    transaction.apply()

    assert not managed.exists()
    assert external.read_text(encoding="utf-8") == "external"
    assert "model_catalog_json" not in config_toml.read_text(encoding="utf-8")
    assert "enabled-reasoning-efforts" not in config_toml.read_text(encoding="utf-8")
    assert 'model_provider = "codex_rosetta"' not in config_toml.read_text(
        encoding="utf-8"
    )
    assert "[model_providers.codex_rosetta]" not in config_toml.read_text(
        encoding="utf-8"
    )
    assert "[model_providers.other]" in config_toml.read_text(encoding="utf-8")
    assert "https://keep.example/v1" in config_toml.read_text(encoding="utf-8")
    assert 'model = "gpt-5.6-sol"' in config_toml.read_text(encoding="utf-8")

    transaction.rollback()
    assert managed.read_text(encoding="utf-8") == "managed"
    assert str(external) in config_toml.read_text(encoding="utf-8")


def test_clear_preserves_a_user_selected_non_rosetta_provider(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        'model_provider = "other"\n\n'
        "[model_providers.other]\n"
        'custom_parameter = "keep"\n\n'
        "[model_providers.codex_rosetta]\n"
        'name = "OpenAI"\n',
        encoding="utf-8",
    )

    transaction = CodexLocalModeTransaction.clear(str(codex_home))
    transaction.apply()
    updated = config_toml.read_text(encoding="utf-8")

    assert 'model_provider = "other"' in updated
    assert "[model_providers.other]" in updated
    assert 'custom_parameter = "keep"' in updated
    assert "[model_providers.codex_rosetta]" not in updated


def test_toml_editor_does_not_remove_managed_text_inside_multiline_string(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        'instructions = """\n'
        'model_catalog_json = "keep as text"\n'
        'model_provider = "codex_rosetta"\n'
        "[model_providers.codex_rosetta]\n"
        'base_url = "keep as text"\n'
        '"""\n'
        'model_catalog_json = "/remove/this.json"\n',
        encoding="utf-8",
    )

    transaction = CodexLocalModeTransaction.clear(str(codex_home))
    transaction.apply()

    updated = config_toml.read_text(encoding="utf-8")
    assert 'model_catalog_json = "keep as text"' in updated
    assert 'model_provider = "codex_rosetta"' in updated
    assert "[model_providers.codex_rosetta]" in updated
    assert 'base_url = "keep as text"' in updated
    assert "/remove/this.json" not in updated


def test_toml_editor_removes_a_multiline_catalog_assignment(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        'model_catalog_json = """\n/remove/this.json\n"""\nmodel = "keep"\n',
        encoding="utf-8",
    )

    transaction = CodexLocalModeTransaction.clear(str(codex_home))
    transaction.apply()

    assert config_toml.read_text(encoding="utf-8") == 'model = "keep"\n'


def test_sync_skips_writes_when_managed_file_contents_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from codex_rosetta.gateway import local_mode

    codex_home = tmp_path / "codex"
    raw = {
        "codex": {
            "memories": {
                "extract_model": "gpt-5.4-mini",
                "consolidation_model": "gpt-5.4",
            }
        }
    }
    first = _sync_transaction(codex_home, raw)
    first.apply()
    assert first.changed is True

    catalog_file = codex_home / "model_catalog.json"
    config_file = codex_home / "config.toml"
    before = {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in (catalog_file, config_file)
    }
    writes: list[str] = []

    def record_write(path: str, _content: bytes) -> None:
        writes.append(path)

    monkeypatch.setattr(local_mode, "_atomic_write_bytes", record_write)
    second = _sync_transaction(codex_home, raw)
    second.apply()

    assert second.changed is False
    assert writes == []
    assert {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in (catalog_file, config_file)
    } == before


def test_sync_rolls_back_both_files_when_toml_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from codex_rosetta.gateway import local_mode

    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    catalog_file = codex_home / "model_catalog.json"
    config_file = codex_home / "config.toml"
    catalog_file.write_text("old catalog", encoding="utf-8")
    config_file.write_text('model = "old"\n', encoding="utf-8")
    real_atomic_write = local_mode._atomic_write_bytes
    failed = False

    def fail_config_toml(path: str, content: bytes) -> None:
        nonlocal failed
        if path == str(config_file) and not failed:
            failed = True
            raise OSError("simulated config.toml failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(local_mode, "_atomic_write_bytes", fail_config_toml)
    transaction = _sync_transaction(codex_home)

    with pytest.raises(OSError, match="simulated"):
        transaction.apply()

    assert catalog_file.read_text(encoding="utf-8") == "old catalog"
    assert config_file.read_text(encoding="utf-8") == 'model = "old"\n'
