"""Tests for bundled model detection shared by Admin and runtime config."""

import pytest

from codex_rosetta.gateway.model_presets import (
    detect_model_preset,
    load_model_preset_resource,
    model_input_modalities,
    model_presets_for_admin,
    normalize_model_preset,
)


ALPHA_20_TERRA_SHARED_OVERRIDES = {
    "prefer_websockets": True,
    "support_verbosity": True,
    "default_verbosity": "low",
    "apply_patch_tool_type": "freeform",
    "web_search_tool_type": "text_and_image",
    "supports_image_detail_original": True,
    "truncation_policy": {"mode": "tokens", "limit": 10000},
    "supports_parallel_tool_calls": True,
    "tool_mode": "code_mode_only",
    "multi_agent_version": "v2",
    "use_responses_lite": True,
    "include_skills_usage_instructions": False,
    "auto_review_model_override": None,
    "auto_compact_token_limit": None,
    "reasoning_summary_format": "experimental",
    "default_reasoning_summary": "none",
    "shell_type": "shell_command",
    "visibility": "list",
    "minimal_client_version": "0.144.0",
    "supported_in_api": True,
    "availability_nux": None,
    "upgrade": None,
    "experimental_supported_tools": [],
    "available_in_plans": [
        "business",
        "edu",
        "edu_plus",
        "edu_pro",
        "education",
        "enterprise",
        "enterprise_cbp_automation",
        "enterprise_cbp_usage_based",
        "finserv",
        "free",
        "free_workspace",
        "go",
        "hc",
        "k12",
        "plus",
        "pro",
        "prolite",
        "quorum",
        "sci",
        "self_serve_business_usage_based",
        "team",
    ],
    "supports_search_tool": True,
    "default_service_tier": None,
    "service_tiers": [
        {
            "id": "priority",
            "name": "Fast",
            "description": "1.5x speed, increased usage",
        }
    ],
    "additional_speed_tiers": ["fast"],
}


def test_shared_overrides_match_alpha_20_terra_catalog_snapshot() -> None:
    resource = load_model_preset_resource()

    assert resource["template_slug"] == "gpt-5.6-terra"
    assert resource["shared_overrides"] == ALPHA_20_TERRA_SHARED_OVERRIDES


def test_every_shared_override_is_allowed_in_each_model_preset() -> None:
    resource = load_model_preset_resource()
    shared_overrides = resource["shared_overrides"]
    raw_preset = dict(resource["models"][0], **shared_overrides)

    normalized = normalize_model_preset(
        raw_preset,
        field="test preset",
        shared_overrides=shared_overrides,
    )

    for key, value in shared_overrides.items():
        assert key in normalized
        assert normalized[key] == value


def test_admin_detection_combines_codex_catalog_and_third_party_presets() -> None:
    presets = {preset["slug"]: preset for preset in model_presets_for_admin()}

    assert presets["gpt-5.6-terra"]["display_name"] == "GPT-5.6-Terra"
    assert presets["gpt-5.6-terra"]["identity"] == "GPT-5.6-Terra"
    assert presets["gpt-5.6-terra"]["supported_reasoning_levels"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert presets["deepseek-v4-pro"]["display_name"] == "DeepSeek V4 Pro"
    assert presets["qwen3.7-max"]["comp_hash"] == "qwen3.7-max-text"
    assert presets["qwen3.7-max-2026-06-08"]["comp_hash"] == "qwen3.7-max-image"
    assert presets["minimax-m3"]["supports_reasoning_summaries"] is True
    assert presets["minimax-m3"]["default_reasoning_summary"] == "none"
    assert presets["minimax-m3"]["truncation_policy"] == {
        "mode": "bytes",
        "limit": 10000,
    }
    assert presets["minimax-m3"]["supports_parallel_tool_calls"] is True


def test_model_detection_uses_exact_upstream_slug_then_exposed_slug() -> None:
    upstream_match = detect_model_preset("alias", "gpt-5.4")
    exposed_match = detect_model_preset("gpt-5.4-mini")

    assert upstream_match is not None
    assert upstream_match["display_name"] == "GPT-5.4"
    assert exposed_match is not None
    assert exposed_match["display_name"] == "GPT-5.4-Mini"
    assert detect_model_preset("glm-5.2-flash") is None


def test_compact_preset_modalities_drive_runtime_input_filtering() -> None:
    assert model_input_modalities("qwen3.7-plus") == ["text", "image"]
    assert model_input_modalities("gpt-5.6-sol") is None
    assert model_input_modalities("unknown-model") is None


@pytest.mark.parametrize("comp_hash", ["", "   ", 123, None])
def test_model_preset_rejects_invalid_explicit_compaction_hash(
    comp_hash: object,
) -> None:
    preset = {
        "slug": "test-model",
        "display_name": "Test Model",
        "description": "Test model preset",
        "identity": "Test Model",
        "priority": 20,
        "context_window": 128_000,
        "input_modalities": ["text"],
        "supported_reasoning_levels": ["high"],
        "comp_hash": comp_hash,
    }

    with pytest.raises(ValueError, match="comp_hash must be a non-empty string"):
        normalize_model_preset(preset, field="test preset")
