"""Tests for gateway reasoning mapping helpers."""

from __future__ import annotations

import pytest

from llm_rosetta.converters.base.context import ConversionContext
from llm_rosetta.reasoning_mapping import (
    apply_reasoning_mapping_to_provider_request,
    normalize_reasoning_effort,
    resolve_reasoning_mapping,
)


def _apply(mapping: str, effort: str, target_provider: str = "openai_chat") -> dict:
    ctx = ConversionContext()
    return apply_reasoning_mapping_to_provider_request(
        {"model": "test", "messages": []},
        ir_request={"reasoning": {"effort": effort}},
        target_provider=target_provider,
        reasoning_mapping=mapping,
        upstream_model="test",
        model_capabilities=["text", "reasoning"],
        context=ctx,
    )


@pytest.mark.parametrize(
    ("raw", "mode", "expected"),
    [
        ("minimal", None, "light"),
        ("low", None, "light"),
        ("light", None, "light"),
        ("medium", None, "medium"),
        ("high", None, "high"),
        ("xhigh", None, "xhigh"),
        ("max", None, "max"),
        (None, None, "high"),
        ("none", None, "light"),
        ("disabled", None, "light"),
        ("high", "disabled", "light"),
    ],
)
def test_normalize_reasoning_effort(raw, mode, expected):
    warnings: list[str] = []

    assert normalize_reasoning_effort(raw, mode=mode, warnings=warnings) == expected
    assert bool(warnings) is (
        expected == "light" and (raw in {"none", "disabled"} or mode == "disabled")
    )


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("deepseek-v4-flash", "deepseek_v4"),
        ("deepseek-v4-pro", "deepseek_v4"),
        ("glm-5.2", "glm_5_2"),
        ("qwen3.7-plus", "qwen_3_7"),
        ("qwen3.7-max", "qwen_3_7"),
        ("kimi-k2.7-code", "kimi_k2_7_code"),
        ("minimax-m3", "minimax_m3"),
        ("mimo-v2.5", "mimo_v2_5"),
    ],
)
def test_resolve_reasoning_mapping_uses_model_name(model, expected):
    resolution = resolve_reasoning_mapping(
        target_provider="openai_chat",
        provider_name="unrelated",
        upstream_model=model,
    )

    assert resolution.requested == "auto"
    assert resolution.effective == expected
    assert resolution.source == "model"


def test_resolve_reasoning_mapping_prefers_explicit_value():
    resolution = resolve_reasoning_mapping(
        explicit="anthropic",
        target_provider="openai_chat",
        upstream_model="qwen3.7-plus",
    )

    assert resolution.effective == "anthropic"
    assert resolution.source == "config"


@pytest.mark.parametrize(
    ("target_provider", "expected"),
    [
        ("openai_chat", "openai_chat"),
        ("openai_responses", "openai_responses"),
        ("anthropic", "anthropic"),
        ("google", "openai_chat"),
    ],
)
def test_resolve_reasoning_mapping_falls_back_to_target_api(target_provider, expected):
    resolution = resolve_reasoning_mapping(
        target_provider=target_provider,
        upstream_model="unknown-model",
    )

    assert resolution.effective == expected
    assert resolution.source == "target_api"


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("light", "light"),
        ("medium", "medium"),
        ("high", "high"),
        ("xhigh", "xhigh"),
        ("max", "xhigh"),
    ],
)
def test_openai_responses_mapping_efforts(effort, expected):
    result = _apply("openai_responses", effort, "openai_responses")

    assert result["reasoning"] == {"effort": expected}


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("light", "light"),
        ("medium", "medium"),
        ("high", "high"),
        ("xhigh", "xhigh"),
        ("max", "xhigh"),
    ],
)
def test_openai_chat_mapping_efforts(effort, expected):
    result = _apply("openai_chat", effort)

    assert result["reasoning_effort"] == expected


@pytest.mark.parametrize("effort", ["light", "medium", "high", "xhigh", "max"])
def test_anthropic_mapping_efforts(effort):
    result = _apply("anthropic", effort, "anthropic")

    assert result["thinking"] == {"type": "adaptive"}
    assert result["output_config"] == {"effort": effort}


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("light", "high"),
        ("medium", "high"),
        ("high", "high"),
        ("xhigh", "max"),
        ("max", "max"),
    ],
)
def test_deepseek_v4_mapping_efforts(effort, expected):
    result = _apply("deepseek_v4", effort)

    assert result["thinking"] == {"type": "enabled"}
    assert result["reasoning_effort"] == expected


@pytest.mark.parametrize("effort", ["light", "medium", "high", "xhigh", "max"])
def test_glm_5_2_mapping_efforts(effort):
    result = _apply("glm_5_2", effort)

    assert result["thinking"] == {"type": "enabled"}
    assert result["reasoning_effort"] == effort


@pytest.mark.parametrize(
    ("effort", "expected_budget"),
    [
        ("light", 2048),
        ("medium", 4096),
        ("high", 8192),
        ("xhigh", 16384),
        ("max", None),
    ],
)
def test_qwen_3_7_mapping_efforts(effort, expected_budget):
    result = _apply("qwen_3_7", effort)

    assert result["enable_thinking"] is True
    assert result["preserve_thinking"] is True
    if expected_budget is None:
        assert "thinking_budget" not in result
    else:
        assert result["thinking_budget"] == expected_budget


@pytest.mark.parametrize("effort", ["light", "medium", "high", "xhigh", "max"])
def test_kimi_k2_7_code_mapping_efforts_are_noop(effort):
    ctx = ConversionContext()
    result = apply_reasoning_mapping_to_provider_request(
        {"model": "kimi", "messages": []},
        ir_request={"reasoning": {"effort": effort}},
        target_provider="openai_chat",
        reasoning_mapping="kimi_k2_7_code",
        model_capabilities=["text", "reasoning"],
        context=ctx,
    )

    assert result == {"model": "kimi", "messages": []}
    assert ctx.warnings


@pytest.mark.parametrize("effort", ["light", "medium", "high", "xhigh", "max"])
def test_minimax_m3_mapping_efforts_are_adaptive_with_split_for_chat(effort):
    ctx = ConversionContext()
    result = apply_reasoning_mapping_to_provider_request(
        {"model": "minimax", "messages": []},
        ir_request={"reasoning": {"effort": effort}},
        target_provider="openai_chat",
        reasoning_mapping="minimax_m3",
        model_capabilities=["text", "reasoning"],
        context=ctx,
    )

    assert result["thinking"] == {"type": "adaptive"}
    assert result["reasoning_split"] is True
    assert ctx.warnings


def test_minimax_m3_anthropic_mapping_omits_reasoning_split():
    result = _apply("minimax_m3", "high", "anthropic")

    assert result["thinking"] == {"type": "adaptive"}
    assert "reasoning_split" not in result


@pytest.mark.parametrize("effort", ["light", "medium", "high", "xhigh", "max"])
def test_mimo_v2_5_mapping_efforts_enable_thinking(effort):
    ctx = ConversionContext()
    result = apply_reasoning_mapping_to_provider_request(
        {"model": "mimo", "messages": []},
        ir_request={"reasoning": {"effort": effort}},
        target_provider="openai_chat",
        reasoning_mapping="mimo_v2_5",
        model_capabilities=["text", "reasoning"],
        context=ctx,
    )

    assert result["thinking"] == {"type": "enabled"}
    assert ctx.warnings


def test_disabled_input_promotes_to_light_without_disable_field():
    ctx = ConversionContext()
    result = apply_reasoning_mapping_to_provider_request(
        {"model": "qwen", "messages": []},
        ir_request={"reasoning": {"mode": "disabled", "effort": "none"}},
        target_provider="openai_chat",
        reasoning_mapping="qwen_3_7",
        model_capabilities=["text", "reasoning"],
        context=ctx,
    )

    assert result["enable_thinking"] is True
    assert result["thinking_budget"] == 2048
    assert not any(
        value == {"type": "disabled"}
        for value in result.values()
        if isinstance(value, dict)
    )
    assert ctx.warnings


def test_glm_preserves_reasoning_history_signal():
    result = apply_reasoning_mapping_to_provider_request(
        {
            "model": "glm",
            "messages": [{"role": "assistant", "reasoning_content": ""}],
        },
        ir_request={"reasoning": {"effort": "high"}},
        target_provider="openai_chat",
        reasoning_mapping="glm_5_2",
        model_capabilities=["text", "reasoning"],
    )

    assert result["thinking"] == {"type": "enabled", "clear_thinking": False}


def test_models_without_reasoning_capability_are_unchanged():
    result = apply_reasoning_mapping_to_provider_request(
        {"model": "qwen", "messages": [], "reasoning_effort": "none"},
        ir_request={"reasoning": {"effort": "high"}},
        target_provider="openai_chat",
        reasoning_mapping="qwen_3_7",
        model_capabilities=["text"],
    )

    assert result == {"model": "qwen", "messages": [], "reasoning_effort": "none"}
