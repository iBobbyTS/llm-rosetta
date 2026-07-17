"""Tests for codex_rosetta.pipeline and codex_rosetta.capabilities.

Note: This file imports private helpers (resolve_shim, _apply_config_reasoning_override)
directly from codex_rosetta.capabilities for unit-testing internal logic.
"""

import copy
from typing import Any

import pytest

from codex_rosetta.capabilities import (
    _apply_config_reasoning_override,
    enforce_reasoning,
)
from codex_rosetta.converters.base.context import ConversionContext
from codex_rosetta.pipeline import apply_ir_transforms
from codex_rosetta.shims.provider_shim import (
    ProviderShim,
    ReasoningCapability,
    register_shim,
    resolve_shim,
    unregister_shim,
)
from codex_rosetta.shims.transforms import (
    strip_non_vision_images,
    truncate_images as truncate_images_transform,
    unwind_parallel_tool_calls as unwind_transform,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_REASONING_CAP = ReasoningCapability(
    disabled="omit",
    effort_field="reasoning_effort",
    effort_map={"low": "low", "medium": "medium", "high": "high"},
)

_MODEL_REASONING_CAP = ReasoningCapability(
    disabled="thinking_disabled",
    effort_field="output_config.effort",
    thinking_type="enabled",
    effort_map={"low": "low", "high": "high"},
    budget_tokens_default_ratio=0.8,
)


def _make_shim(**kwargs: Any) -> ProviderShim:
    """Create a ProviderShim with sensible defaults, overridable via kwargs."""
    defaults: dict[str, Any] = dict(name="test-shim", base="openai_chat")
    defaults.update(kwargs)
    return ProviderShim(**defaults)


@pytest.fixture(autouse=True)
def _register_cleanup():
    """Ensure test shims are cleaned up after each test."""
    yield
    for name in ("test-shim", "test-shim-img", "test-shim-unwind"):
        unregister_shim(name)


def _simple_ir_request(n_messages: int = 1, n_images: int = 0) -> dict[str, Any]:
    """Build a minimal IR request dict for testing."""
    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"message {i}"} for i in range(n_messages)
    ]
    for i in range(n_images):
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "data": f"img{i}",
                    "media_type": "image/png",
                },
            }
        )
    return {
        "messages": [{"role": "user", "content": content}],
        "tools": [],
    }


# ---------------------------------------------------------------------------
# resolve_shim
# ---------------------------------------------------------------------------


class TestResolveShim:
    def test_none(self):
        assert resolve_shim(None) is None

    def test_provider_shim_instance(self):
        shim = _make_shim()
        assert resolve_shim(shim) is shim

    def test_registered_name(self):
        shim = _make_shim()
        register_shim(shim)
        assert resolve_shim("test-shim") is shim

    def test_unknown_name(self):
        assert resolve_shim("nonexistent-shim") is None


# ---------------------------------------------------------------------------
# enforce_reasoning
# ---------------------------------------------------------------------------


class TestEnforceReasoning:
    def test_none_shim_is_noop(self):
        ctx = ConversionContext()
        enforce_reasoning(ctx, None)
        assert "reasoning_cap" not in ctx.options

    def test_shim_without_reasoning_is_noop(self):
        ctx = ConversionContext()
        shim = _make_shim(reasoning=None)
        enforce_reasoning(ctx, shim)
        assert "reasoning_cap" not in ctx.options

    def test_provider_level_reasoning(self):
        ctx = ConversionContext()
        shim = _make_shim(reasoning=_REASONING_CAP)
        enforce_reasoning(ctx, shim)
        assert ctx.options["reasoning_cap"] is _REASONING_CAP

    def test_model_level_override(self):
        ctx = ConversionContext()
        shim = _make_shim(
            reasoning=_REASONING_CAP,
            model_reasoning={"gpt-4": _MODEL_REASONING_CAP},
        )
        enforce_reasoning(ctx, shim, model="gpt-4")
        assert ctx.options["reasoning_cap"] is _MODEL_REASONING_CAP

    def test_model_not_in_overrides_falls_back(self):
        ctx = ConversionContext()
        shim = _make_shim(
            reasoning=_REASONING_CAP,
            model_reasoning={"gpt-4": _MODEL_REASONING_CAP},
        )
        enforce_reasoning(ctx, shim, model="gpt-3.5")
        assert ctx.options["reasoning_cap"] is _REASONING_CAP

    def test_config_override_highest_priority(self):
        ctx = ConversionContext()
        shim = _make_shim(reasoning=_REASONING_CAP)
        enforce_reasoning(ctx, shim, config_override={"thinking_type": "adaptive"})
        cap = ctx.options["reasoning_cap"]
        assert cap.thinking_type == "adaptive"
        # Other fields inherited from base
        assert cap.disabled == _REASONING_CAP.disabled
        assert cap.effort_field == _REASONING_CAP.effort_field

    def test_config_override_on_model_override(self):
        """Config override should apply on top of model-level override."""
        ctx = ConversionContext()
        shim = _make_shim(
            reasoning=_REASONING_CAP,
            model_reasoning={"gpt-4": _MODEL_REASONING_CAP},
        )
        enforce_reasoning(
            ctx, shim, model="gpt-4", config_override={"disabled": "block"}
        )
        cap = ctx.options["reasoning_cap"]
        assert cap.disabled == "block"
        # Rest inherited from model-level
        assert cap.thinking_type == _MODEL_REASONING_CAP.thinking_type

    def test_accepts_registered_name(self):
        ctx = ConversionContext()
        shim = _make_shim(reasoning=_REASONING_CAP)
        register_shim(shim)
        enforce_reasoning(ctx, "test-shim")
        assert ctx.options["reasoning_cap"] is _REASONING_CAP

    def test_unknown_name_is_noop(self):
        ctx = ConversionContext()
        enforce_reasoning(ctx, "nonexistent")
        assert "reasoning_cap" not in ctx.options


# ---------------------------------------------------------------------------
# apply_ir_transforms
# ---------------------------------------------------------------------------


class TestApplyIrTransforms:
    def test_none_shim_passthrough(self):
        ir = _simple_ir_request()
        original = copy.deepcopy(ir)
        result = apply_ir_transforms(ir, None)
        assert result == original

    def test_shim_no_features_passthrough(self):
        ir = _simple_ir_request()
        original = copy.deepcopy(ir)
        shim = _make_shim()
        result = apply_ir_transforms(ir, shim)
        assert result == original

    def test_strip_images_when_preset_is_text_only(self):
        """Images should be stripped when the preset is text-only."""
        ir = _simple_ir_request(n_images=3)
        shim = _make_shim(ir_transforms=(strip_non_vision_images(),))
        result = apply_ir_transforms(
            ir, shim, input_modalities=["text"], upstream_model="deepseek-chat"
        )
        # Images should be replaced with text placeholders
        content = result["messages"][0]["content"]
        image_parts = [p for p in content if p.get("type") == "image"]
        assert len(image_parts) == 0

    def test_no_strip_when_preset_supports_images(self):
        """Images should not be stripped when the preset supports images."""
        ir = _simple_ir_request(n_images=3)
        original = copy.deepcopy(ir)
        shim = _make_shim(ir_transforms=(strip_non_vision_images(),))
        result = apply_ir_transforms(
            ir, shim, input_modalities=["text", "image"], upstream_model="gpt-4o"
        )
        assert result == original

    def test_no_strip_when_modalities_are_unknown(self):
        """Images should not be stripped when preset modalities are unknown."""
        ir = _simple_ir_request(n_images=3)
        original = copy.deepcopy(ir)
        shim = _make_shim(ir_transforms=(strip_non_vision_images(),))
        result = apply_ir_transforms(ir, shim, input_modalities=None)
        assert result == original

    def test_image_limit_enforced(self):
        """Shim with max_images should truncate excess images."""
        ir = _simple_ir_request(n_images=5)
        shim = _make_shim(
            name="test-shim-img", ir_transforms=(truncate_images_transform(2),)
        )
        result = apply_ir_transforms(ir, shim)
        content = result["messages"][0]["content"]
        image_parts = [p for p in content if p.get("type") == "image"]
        assert len(image_parts) <= 2

    def test_image_limit_pattern_match(self):
        """Image limit should only fire when model matches pattern."""
        ir = _simple_ir_request(n_images=5)
        shim = _make_shim(
            name="test-shim-img",
            ir_transforms=(truncate_images_transform(2, pattern="^gpt"),),
        )
        # Matching model
        result = apply_ir_transforms(copy.deepcopy(ir), shim, upstream_model="gpt-4o")
        content = result["messages"][0]["content"]
        image_parts = [p for p in content if p.get("type") == "image"]
        assert len(image_parts) <= 2

    def test_image_limit_pattern_no_match(self):
        """Image limit should NOT fire when model doesn't match pattern."""
        ir = _simple_ir_request(n_images=5)
        shim = _make_shim(
            name="test-shim-img",
            ir_transforms=(truncate_images_transform(2, pattern="^gpt"),),
        )
        result = apply_ir_transforms(
            copy.deepcopy(ir), shim, upstream_model="gemini-pro"
        )
        content = result["messages"][0]["content"]
        image_parts = [p for p in content if p.get("type") == "image"]
        assert len(image_parts) == 5  # untouched

    def test_unwind_parallel_tool_calls(self):
        """Shim with unwind should split parallel tool calls."""
        ir = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_call",
                            "tool_call_id": "call_1",
                            "tool_name": "fn_a",
                            "tool_input": {},
                            "tool_type": "function",
                        },
                        {
                            "type": "tool_call",
                            "tool_call_id": "call_2",
                            "tool_name": "fn_b",
                            "tool_input": {},
                            "tool_type": "function",
                        },
                    ],
                },
                {
                    "role": "tool",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_call_id": "call_1",
                            "result": "a",
                        },
                    ],
                },
                {
                    "role": "tool",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_call_id": "call_2",
                            "result": "b",
                        },
                    ],
                },
            ],
            "tools": [],
        }
        shim = _make_shim(name="test-shim-unwind", ir_transforms=(unwind_transform(),))
        result = apply_ir_transforms(ir, shim)
        # After unwind: user + (assistant+tool) + (assistant+tool) = 5
        assert len(result["messages"]) == 5

    def test_unwind_pattern_no_match(self):
        """Unwind should NOT fire when model doesn't match pattern."""
        ir = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_call",
                            "tool_call_id": "call_1",
                            "tool_name": "fn_a",
                            "tool_input": {},
                            "tool_type": "function",
                        },
                        {
                            "type": "tool_call",
                            "tool_call_id": "call_2",
                            "tool_name": "fn_b",
                            "tool_input": {},
                            "tool_type": "function",
                        },
                    ],
                },
                {
                    "role": "tool",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_call_id": "call_1",
                            "result": "a",
                        },
                    ],
                },
                {
                    "role": "tool",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_call_id": "call_2",
                            "result": "b",
                        },
                    ],
                },
            ],
            "tools": [],
        }
        shim = _make_shim(
            name="test-shim-unwind",
            ir_transforms=(unwind_transform(pattern="^gemini"),),
        )
        result = apply_ir_transforms(ir, shim, upstream_model="gpt-4o")
        assert len(result["messages"]) == 4  # untouched

    def test_accepts_registered_name(self):
        ir = _simple_ir_request(n_images=5)
        shim = _make_shim(
            name="test-shim-img", ir_transforms=(truncate_images_transform(2),)
        )
        register_shim(shim)
        result = apply_ir_transforms(ir, "test-shim-img")
        content = result["messages"][0]["content"]
        image_parts = [p for p in content if p.get("type") == "image"]
        assert len(image_parts) <= 2


# ---------------------------------------------------------------------------
# _apply_config_reasoning_override
# ---------------------------------------------------------------------------


class TestApplyConfigReasoningOverride:
    def test_partial_override(self):
        result = _apply_config_reasoning_override(
            _REASONING_CAP, {"thinking_type": "adaptive"}
        )
        assert result.thinking_type == "adaptive"
        assert result.disabled == _REASONING_CAP.disabled
        assert result.effort_field == _REASONING_CAP.effort_field
        assert result.effort_map == _REASONING_CAP.effort_map

    def test_full_override(self):
        override = {
            "disabled": "block",
            "effort_field": "custom_effort",
            "max_effort": "high",
            "thinking_type": "enabled",
            "unsigned_reasoning_blocks": "drop",
            "effort_map": {"a": "b"},
            "budget_tokens_default_ratio": 0.5,
        }
        result = _apply_config_reasoning_override(_REASONING_CAP, override)
        assert result.disabled == "block"
        assert result.effort_field == "custom_effort"
        assert result.thinking_type == "enabled"
        assert result.budget_tokens_default_ratio == 0.5

    def test_empty_override_preserves_base(self):
        result = _apply_config_reasoning_override(_REASONING_CAP, {})
        assert result.disabled == _REASONING_CAP.disabled
        assert result.effort_field == _REASONING_CAP.effort_field
        assert result.effort_map == _REASONING_CAP.effort_map


# ---------------------------------------------------------------------------
# ConversionPipeline
# ---------------------------------------------------------------------------


class TestConversionPipeline:
    """Tests for the high-level ConversionPipeline class."""

    def test_convert_request_openai_to_openai(self):
        """Same-format round-trip produces valid target body."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
        }
        target = pipeline.convert_request(body)
        assert "messages" in target
        assert target["model"] == "gpt-4"

    def test_convert_request_openai_responses_preserves_include(self):
        """Responses same-format conversion preserves native include fields."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_responses")
        target = pipeline.convert_request(
            {
                "model": "gpt-5.4",
                "input": "test",
                "stream": True,
                "include": ["reasoning.encrypted_content"],
                "reasoning": {"effort": "low"},
            }
        )

        assert target["include"] == ["reasoning.encrypted_content"]
        assert target["reasoning"] == {"effort": "low"}

    def test_responses_to_deepseek_v4_chat_applies_reasoning_mapping(self):
        """Responses effort maps to DeepSeek V4 thinking and capped effort."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline(
            "openai_responses",
            "openai_chat",
            upstream_model="deepseek-v4-flash",
            reasoning_mapping="deepseek_v4",
        )
        target = pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "test",
                "reasoning": {"effort": "medium"},
            }
        )

        assert target["thinking"] == {"type": "enabled"}
        assert target["reasoning_effort"] == "high"

    def test_responses_to_qwen_3_7_chat_auto_mapping_uses_budget(self):
        """Auto mapping detects Qwen 3.7 from the upstream model name."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline(
            "openai_responses",
            "openai_chat",
            upstream_model="qwen3.7-plus",
            reasoning_mapping="auto",
        )
        target = pipeline.convert_request(
            {
                "model": "qwen3.7-plus",
                "input": "test",
                "reasoning": {"effort": "medium"},
            }
        )

        assert target["enable_thinking"] is True
        assert target["thinking_budget"] == 4096
        assert target["preserve_thinking"] is True

    def test_anthropic_target_fallback_applies_official_reasoning_format(self):
        """Unknown model on Anthropic target uses Anthropic official fields."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline(
            "openai_chat",
            "anthropic",
            upstream_model="unknown-model",
            reasoning_mapping="auto",
        )
        target = pipeline.convert_request(
            {
                "model": "unknown-model",
                "messages": [{"role": "user", "content": "test"}],
                "reasoning_effort": "xhigh",
            }
        )

        assert target["thinking"] == {"type": "adaptive"}
        assert target["output_config"] == {"effort": "xhigh"}

    def test_kimi_mapping_preserves_empty_reasoning_content(self):
        """Kimi mapping is no-op for controls and keeps empty reasoning_content."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline(
            "openai_chat",
            "openai_chat",
            upstream_model="kimi-k2.7-code",
            reasoning_mapping="kimi_k2_7_code",
        )
        target = pipeline.convert_request(
            {
                "model": "kimi-k2.7-code",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "",
                    },
                    {"role": "user", "content": "continue"},
                ],
                "reasoning_effort": "high",
            }
        )

        assert target["messages"][0]["reasoning_content"] == ""
        assert "thinking" not in target
        assert "reasoning_effort" not in target

    def test_responses_namespace_tools_are_flattened_for_chat_target(self):
        """Responses namespace tools become Chat functions for upstream models."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        target = pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "use a subagent",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "multi_agent_v1",
                        "description": "Spawn and manage sub-agents.",
                        "tools": [
                            {
                                "type": "function",
                                "name": "spawn_agent",
                                "description": "Spawn a sub-agent.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"prompt": {"type": "string"}},
                                    "required": ["prompt"],
                                },
                            }
                        ],
                    }
                ],
            }
        )

        tool_names = [tool["function"]["name"] for tool in target["tools"]]
        assert tool_names == ["multi_agent_v1-spawn_agent"]
        assert "multi_agent_v1" not in tool_names

    def test_responses_lite_tools_are_flattened_for_chat_target(self):
        """Responses Lite embedded tools reach Chat with developer context."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        target = pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": [
                    {
                        "type": "additional_tools",
                        "role": "developer",
                        "tools": [
                            {
                                "type": "namespace",
                                "name": "multi_agent_v1",
                                "tools": [
                                    {
                                        "type": "function",
                                        "name": "spawn_agent",
                                        "description": "Spawn a sub-agent.",
                                        "parameters": {
                                            "type": "object",
                                            "properties": {
                                                "prompt": {"type": "string"}
                                            },
                                            "required": ["prompt"],
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [
                            {"type": "input_text", "text": "Use tools carefully."}
                        ],
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Delegate."}],
                    },
                ],
                "parallel_tool_calls": False,
            }
        )

        assert target["messages"][0] == {
            "role": "system",
            "content": "Use tools carefully.",
        }
        assert [tool["function"]["name"] for tool in target["tools"]] == [
            "multi_agent_v1-spawn_agent"
        ]
        assert target["parallel_tool_calls"] is False

    def test_responses_custom_tool_output_reaches_chat_target(self):
        """Codex exec output is paired with its Chat-visible function call."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        target = pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": [
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_exec",
                        "name": "exec",
                        "input": "text(await tools.web__run({}));",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_exec",
                        "output": [
                            {"type": "input_text", "text": "Script completed\n"},
                            {
                                "type": "input_text",
                                "text": "URL: https://docs.python.org",
                            },
                        ],
                    },
                ],
            }
        )

        assert target["messages"] == [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_exec",
                        "type": "function",
                        "function": {
                            "name": "exec",
                            "arguments": '{"input": "text(await tools.web__run({}));"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_exec",
                "content": (
                    '[{"type": "text", "text": "Script completed\\n"}, '
                    '{"type": "text", "text": "URL: https://docs.python.org"}]'
                ),
            },
        ]

    def test_responses_reasoning_context_is_omitted_for_chat_target(self):
        """Responses-only reasoning context does not leak into Chat requests."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        target = pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "hello",
                "reasoning": {"effort": "medium", "context": "all_turns"},
            }
        )

        assert target["reasoning_effort"] == "high"
        assert "context" not in target
        assert "reasoning" not in target

    def test_responses_namespace_duplicate_child_names_are_unique_for_chat_target(self):
        """Namespace child names are disambiguated when flattened for Chat."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        target = pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "fetch from different connectors",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "mcp__codex_apps__github",
                        "tools": [
                            {
                                "type": "function",
                                "name": "_fetch",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"id": {"type": "string"}},
                                },
                            }
                        ],
                    },
                    {
                        "type": "namespace",
                        "name": "mcp__codex_apps__gmail",
                        "tools": [
                            {
                                "type": "function",
                                "name": "_fetch",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"id": {"type": "string"}},
                                },
                            }
                        ],
                    },
                ],
            }
        )

        tool_names = [tool["function"]["name"] for tool in target["tools"]]
        assert tool_names == [
            "mcp__codex_apps__github-_fetch",
            "mcp__codex_apps__gmail-_fetch",
        ]
        assert len(tool_names) == len(set(tool_names))

    def test_responses_goal_tools_preserve_profile_prepared_guidance(self):
        """Chat conversion preserves descriptions already adapted by a Profile."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        target = pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "mark goal complete",
                "tools": [
                    {
                        "type": "function",
                        "name": "create_goal",
                        "description": "Create a goal.\n\nProfile create guidance.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "objective": {"type": "string"},
                                "token_budget": {"type": "number"},
                            },
                            "required": ["objective"],
                        },
                    },
                    {
                        "type": "function",
                        "name": "update_goal",
                        "description": "Update the existing goal.\n\nProfile update guidance.",
                        "parameters": {
                            "type": "object",
                            "properties": {"status": {"type": "string"}},
                            "required": ["status"],
                        },
                    },
                ],
            }
        )

        descriptions = {
            tool["function"]["name"]: tool["function"]["description"]
            for tool in target["tools"]
        }
        assert descriptions["create_goal"].endswith("Profile create guidance.")
        assert descriptions["update_goal"].endswith("Profile update guidance.")

    def test_responses_request_user_input_preserves_profile_prepared_guidance(self):
        """Chat conversion does not add request-user-input guidance itself."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        target = pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "plan a README update",
                "tools": [
                    {
                        "type": "function",
                        "name": "request_user_input",
                        "description": "Request user input.\n\nProfile user-input guidance.",
                        "parameters": {
                            "type": "object",
                            "properties": {"questions": {"type": "array"}},
                            "required": ["questions"],
                        },
                    }
                ],
            }
        )

        assert target["tools"][0]["function"]["description"].endswith(
            "Profile user-input guidance."
        )

    def test_responses_namespace_tools_stay_namespaced_for_responses_target(self):
        """Responses target keeps namespace tools in native shape."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_responses")
        target = pipeline.convert_request(
            {
                "model": "gpt-5.5",
                "input": "use a subagent",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "multi_agent_v1",
                        "description": "Spawn and manage sub-agents.",
                        "tools": [
                            {
                                "type": "function",
                                "name": "spawn_agent",
                                "description": "Spawn a sub-agent.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"prompt": {"type": "string"}},
                                    "required": ["prompt"],
                                },
                            }
                        ],
                    }
                ],
            }
        )

        assert target["tools"] == [
            {
                "type": "namespace",
                "name": "multi_agent_v1",
                "description": "Spawn and manage sub-agents.",
                "tools": [
                    {
                        "type": "function",
                        "name": "spawn_agent",
                        "description": "Spawn a sub-agent.",
                        "parameters": {
                            "type": "object",
                            "properties": {"prompt": {"type": "string"}},
                            "required": ["prompt"],
                        },
                    }
                ],
            }
        ]

    @pytest.mark.parametrize(
        "model_tool_name",
        [
            "multi_agent_v1-spawn_agent",
            "multi_agent_v1.spawn_agent",
            "multi_agent_v1_spawn_agent",
            "spawn_agent",
        ],
    )
    def test_chat_response_tool_call_restores_responses_namespace(
        self, model_tool_name
    ):
        """Chat tool calls restore Responses namespace for Codex runtime."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "use a subagent",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "multi_agent_v1",
                        "tools": [
                            {
                                "type": "function",
                                "name": "spawn_agent",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"prompt": {"type": "string"}},
                                    "required": ["prompt"],
                                },
                            }
                        ],
                    }
                ],
            }
        )

        response = pipeline.convert_response(
            {
                "id": "chatcmpl-1",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": model_tool_name,
                                        "arguments": '{"prompt":"Translate README"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )

        tool_call = response["output"][0]
        assert tool_call["type"] == "function_call"
        assert tool_call["name"] == "spawn_agent"
        assert tool_call["namespace"] == "multi_agent_v1"

    @pytest.mark.parametrize(
        ("namespace_children", "model_tool_name"),
        [
            (("alpha", "beta_gamma", "alpha_beta", "gamma"), "alpha_beta_gamma"),
            (("alpha", "beta-gamma", "alpha-beta", "gamma"), "alpha-beta-gamma"),
            (("alpha", "beta.gamma", "alpha.beta", "gamma"), "alpha.beta.gamma"),
        ],
    )
    def test_chat_response_namespace_alias_stays_flat_when_ambiguous(
        self,
        namespace_children,
        model_tool_name,
    ):
        """An alias shared by multiple Namespaces is not guessed."""
        from codex_rosetta.pipeline import ConversionPipeline

        namespace_a, child_a, namespace_b, child_b = namespace_children

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "call one tool",
                "tools": [
                    {
                        "type": "namespace",
                        "name": namespace_a,
                        "tools": [
                            {
                                "type": "function",
                                "name": child_a,
                                "parameters": {"type": "object", "properties": {}},
                            }
                        ],
                    },
                    {
                        "type": "namespace",
                        "name": namespace_b,
                        "tools": [
                            {
                                "type": "function",
                                "name": child_b,
                                "parameters": {"type": "object", "properties": {}},
                            }
                        ],
                    },
                ],
            }
        )

        response = pipeline.convert_response(
            {
                "id": "chatcmpl-1",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": model_tool_name,
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )

        tool_call = response["output"][0]
        assert tool_call["name"] == model_tool_name
        assert "namespace" not in tool_call

    def test_chat_response_bare_child_stays_flat_when_namespaces_conflict(self):
        """A child shared by multiple Namespaces is not guessed."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "call one tool",
                "tools": [
                    {
                        "type": "namespace",
                        "name": namespace,
                        "tools": [
                            {
                                "type": "function",
                                "name": "shared",
                                "parameters": {"type": "object", "properties": {}},
                            }
                        ],
                    }
                    for namespace in ("alpha", "beta")
                ],
            }
        )

        response = pipeline.convert_response(
            {
                "id": "chatcmpl-1",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": "shared",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )

        tool_call = response["output"][0]
        assert tool_call["name"] == "shared"
        assert "namespace" not in tool_call

    @pytest.mark.parametrize(
        "top_level_name",
        [
            "multi_agent_v1-spawn_agent",
            "multi_agent_v1_spawn_agent",
            "multi_agent_v1.spawn_agent",
        ],
    )
    def test_chat_response_namespace_name_does_not_shadow_top_level_function(
        self, top_level_name
    ):
        """A top-level Function wins over a matching Namespace name."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "call one tool",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "multi_agent_v1",
                        "tools": [
                            {
                                "type": "function",
                                "name": "spawn_agent",
                                "parameters": {"type": "object", "properties": {}},
                            }
                        ],
                    },
                    {
                        "type": "function",
                        "name": top_level_name,
                        "parameters": {"type": "object", "properties": {}},
                    },
                ],
            }
        )

        response = pipeline.convert_response(
            {
                "id": "chatcmpl-1",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": top_level_name,
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )

        tool_call = response["output"][0]
        assert tool_call["name"] == top_level_name
        assert "namespace" not in tool_call

    def test_chat_response_bare_child_does_not_shadow_top_level_function(self):
        """A bare child alias does not replace a same-named top-level Function."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "call one tool",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "multi_agent_v1",
                        "tools": [
                            {
                                "type": "function",
                                "name": "spawn_agent",
                                "parameters": {"type": "object", "properties": {}},
                            }
                        ],
                    },
                    {
                        "type": "function",
                        "name": "spawn_agent",
                        "parameters": {"type": "object", "properties": {}},
                    },
                ],
            }
        )

        response = pipeline.convert_response(
            {
                "id": "chatcmpl-1",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": "spawn_agent",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )

        tool_call = response["output"][0]
        assert tool_call["name"] == "spawn_agent"
        assert "namespace" not in tool_call

    def test_chat_response_duplicate_namespace_child_tool_call_restores_source_name(
        self,
    ):
        """Disambiguated Chat tool calls restore original namespace child names."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "fetch from github",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "mcp__codex_apps__github",
                        "tools": [
                            {
                                "type": "function",
                                "name": "_fetch",
                                "parameters": {"type": "object", "properties": {}},
                            }
                        ],
                    },
                    {
                        "type": "namespace",
                        "name": "mcp__codex_apps__gmail",
                        "tools": [
                            {
                                "type": "function",
                                "name": "_fetch",
                                "parameters": {"type": "object", "properties": {}},
                            }
                        ],
                    },
                ],
            }
        )

        response = pipeline.convert_response(
            {
                "id": "chatcmpl-1",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": "mcp__codex_apps__github._fetch",
                                        "arguments": '{"id":"issue-1"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        )

        tool_call = response["output"][0]
        assert tool_call["type"] == "function_call"
        assert tool_call["name"] == "_fetch"
        assert tool_call["namespace"] == "mcp__codex_apps__github"

    def test_convert_response_openai_to_openai(self):
        """Response round-trip produces valid source response."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        pipeline.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        )
        response = pipeline.convert_response(
            {
                "id": "resp-1",
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            }
        )
        assert "choices" in response

    def test_convert_request_raises_conversion_error(self):
        """Completely invalid body should raise ConversionError with phase info."""
        from codex_rosetta.pipeline import ConversionError, ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        with pytest.raises(ConversionError) as exc_info:
            # messages must be iterable — passing an int triggers a parse error
            pipeline.convert_request({"model": "gpt-4", "messages": 123})
        assert exc_info.value.phase == "source_to_ir"

    def test_convert_request_twice_raises(self):
        """Calling convert_request twice raises RuntimeError (one-shot)."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        pipeline.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        )
        with pytest.raises(RuntimeError, match="one-shot"):
            pipeline.convert_request(
                {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
            )

    def test_convert_response_before_request_raises(self):
        """Calling convert_response before convert_request raises RuntimeError."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        with pytest.raises(RuntimeError):
            pipeline.convert_response({"choices": []})

    def test_on_ir_ready_callback_request(self):
        """on_ir_ready callback fires after source→IR, before shim transforms."""
        from codex_rosetta.pipeline import ConversionPipeline

        captured: list[dict[str, Any]] = []
        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        pipeline.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            on_ir_ready=lambda ir: captured.append(ir),
        )
        assert len(captured) == 1
        assert "messages" in captured[0]

    def test_on_ir_ready_callback_response(self):
        """on_ir_ready callback fires after target→IR in convert_response."""
        from codex_rosetta.pipeline import ConversionPipeline

        captured: list[dict[str, Any]] = []
        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        pipeline.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        )
        pipeline.convert_response(
            {
                "id": "resp-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            },
            on_ir_ready=lambda ir: captured.append(ir),
        )
        assert len(captured) == 1

    def test_context_available_after_convert_request(self):
        """Pipeline context should be accessible after convert_request."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        with pytest.raises(RuntimeError):
            _ = pipeline.context
        pipeline.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        )
        ctx = pipeline.context
        assert ctx.options.get("metadata_mode") == "preserve"

    def test_ir_request_available_after_convert_request(self):
        """Pipeline ir_request should be accessible after convert_request."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        with pytest.raises(RuntimeError):
            _ = pipeline.ir_request
        pipeline.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        )
        ir = pipeline.ir_request
        assert "messages" in ir

    def test_no_shim_passthrough(self):
        """Pipeline without shim still works — no transforms applied."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat", None)
        target = pipeline.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        )
        assert "messages" in target

    def test_cross_format_openai_to_anthropic(self):
        """Cross-format conversion produces valid target body."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "anthropic")
        target = pipeline.convert_request(
            {"model": "claude-3", "messages": [{"role": "user", "content": "hi"}]}
        )
        # Anthropic format should have "messages" with different structure
        assert "messages" in target

    def test_create_stream_processor(self):
        """StreamProcessor should be creatable after convert_request."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        pipeline.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        )
        processor = pipeline.create_stream_processor()
        assert processor is not None

    def test_create_stream_processor_before_request_raises(self):
        """create_stream_processor before convert_request raises RuntimeError."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        with pytest.raises(RuntimeError):
            pipeline.create_stream_processor()

    def test_stream_processor_on_ir_event_callback(self):
        """StreamProcessor on_ir_event callback fires for each IR event."""
        from codex_rosetta.pipeline import ConversionPipeline

        captured: list[dict[str, Any]] = []
        pipeline = ConversionPipeline("openai_chat", "openai_chat")
        pipeline.convert_request(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
        )
        processor = pipeline.create_stream_processor(
            on_ir_event=lambda ev: captured.append(ev)
        )
        # Feed a simple streaming chunk
        chunk = {
            "id": "chatcmpl-1",
            "choices": [{"delta": {"content": "hello"}, "index": 0}],
        }
        events = processor.process_chunk(chunk)
        # Should produce source events and fire callback for IR events
        assert isinstance(events, list)
        # Callback should have been called at least once if IR events were produced
        if events:
            assert len(captured) > 0

    @pytest.mark.parametrize(
        "model_tool_name",
        [
            "multi_agent_v1-spawn_agent",
            "multi_agent_v1.spawn_agent",
            "multi_agent_v1_spawn_agent",
            "spawn_agent",
        ],
    )
    def test_stream_processor_restores_responses_namespace_for_chat_tool_call(
        self, model_tool_name
    ):
        """Streaming Chat tool calls restore Responses namespace metadata."""
        from codex_rosetta.pipeline import ConversionPipeline

        pipeline = ConversionPipeline("openai_responses", "openai_chat")
        pipeline.convert_request(
            {
                "model": "deepseek-v4-flash",
                "input": "use a subagent",
                "stream": True,
                "tools": [
                    {
                        "type": "namespace",
                        "name": "multi_agent_v1",
                        "tools": [
                            {
                                "type": "function",
                                "name": "spawn_agent",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"prompt": {"type": "string"}},
                                    "required": ["prompt"],
                                },
                            }
                        ],
                    }
                ],
            }
        )
        processor = pipeline.create_stream_processor()

        events = processor.process_chunk(
            {
                "id": "chatcmpl-1",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": model_tool_name,
                                        "arguments": "",
                                    },
                                }
                            ]
                        },
                    }
                ],
            }
        )

        added = next(
            event for event in events if event["type"] == "response.output_item.added"
        )
        assert added["item"]["type"] == "function_call"
        assert added["item"]["name"] == "spawn_agent"
        assert added["item"]["namespace"] == "multi_agent_v1"
