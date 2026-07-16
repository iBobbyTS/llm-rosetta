"""Provider-specific reasoning request mapping.

The gateway-facing reasoning configuration is intentionally separate from the
older shim ``ReasoningCapability`` helper.  Codex-style requests keep thinking
enabled, then map the requested effort onto each upstream provider's public
control fields.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import re
from typing import Any, Literal, cast

from codex_rosetta.converters.base.context import ConversionContext
from codex_rosetta.converters.base.helpers.reasoning import normalize_reasoning_input
from codex_rosetta.types.ir.configs import ReasoningConfig

ReasoningMapping = Literal[
    "auto",
    "openai_chat",
    "openai_responses",
    "anthropic",
    "deepseek_v4",
    "glm_5_2",
    "qwen_3_7",
    "kimi_k2_7_code",
    "minimax_m3",
    "mimo_v2_5",
]
ResolvedReasoningMapping = Literal[
    "openai_chat",
    "openai_responses",
    "anthropic",
    "deepseek_v4",
    "glm_5_2",
    "qwen_3_7",
    "kimi_k2_7_code",
    "minimax_m3",
    "mimo_v2_5",
]
ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]
ReasoningMappingSource = Literal["config", "model", "target_api"]

VALID_REASONING_MAPPINGS: tuple[ReasoningMapping, ...] = (
    "auto",
    "openai_chat",
    "openai_responses",
    "anthropic",
    "deepseek_v4",
    "glm_5_2",
    "qwen_3_7",
    "kimi_k2_7_code",
    "minimax_m3",
    "mimo_v2_5",
)

_QWEN_BUDGETS: dict[ReasoningEffort, int] = {
    "low": 2048,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
}


@dataclass(frozen=True, slots=True)
class ReasoningMappingResolution:
    """Resolved reasoning mapping and the source of the decision."""

    requested: ReasoningMapping
    effective: ResolvedReasoningMapping
    source: ReasoningMappingSource


def normalize_reasoning_mapping(value: Any) -> ReasoningMapping:
    """Return a validated reasoning mapping value.

    Empty values are treated as ``"auto"``.  Dashes are accepted as a
    user-friendly alias and normalised to underscores.
    """
    if value is None or value == "":
        return "auto"
    mapping = str(value).strip().lower().replace("-", "_")
    if mapping not in VALID_REASONING_MAPPINGS:
        valid = ", ".join(VALID_REASONING_MAPPINGS)
        raise ValueError(
            f"invalid reasoning_mapping '{value}'; expected one of {valid}"
        )
    return cast(ReasoningMapping, mapping)


def resolve_reasoning_mapping(
    *,
    explicit: Any = None,
    target_provider: str | None = None,
    provider_name: str | None = None,
    shim_name: str | None = None,
    upstream_model: str | None = None,
    model_name: str | None = None,
    provider_config: dict[str, Any] | None = None,
) -> ReasoningMappingResolution:
    """Resolve the effective reasoning mapping.

    Priority:
    1. explicit non-auto ``reasoning_mapping``
    2. model-name detection
    3. target API fallback
    """
    requested = normalize_reasoning_mapping(explicit)
    if requested != "auto":
        return ReasoningMappingResolution(
            requested=requested,
            effective=requested,
            source="config",
        )

    model_key = _clean_key(upstream_model or model_name)
    detected = _detect_model_mapping(model_key)
    if detected is not None:
        return ReasoningMappingResolution(
            requested="auto",
            effective=detected,
            source="model",
        )

    return ReasoningMappingResolution(
        requested="auto",
        effective=_fallback_mapping(target_provider),
        source="target_api",
    )


def normalize_reasoning_effort(
    raw_effort: Any,
    *,
    mode: Any = None,
    warnings: list[str] | None = None,
) -> ReasoningEffort:
    """Normalise a source request effort into the internal effort ladder."""
    mode_key = _clean_key(mode)
    effort_key = _clean_key(raw_effort)

    if mode_key == "disabled" or effort_key in {"none", "disabled", "off", "false"}:
        _warn(
            warnings,
            "Reasoning disable request ignored; using low effort because "
            "reasoning-capable coding models keep thinking enabled.",
        )
        return "low"

    if not effort_key:
        return "high"

    if effort_key in {"minimal", "low", "light"}:
        return "low"
    if effort_key == "ultra":
        normalized = normalize_reasoning_input(
            cast(ReasoningConfig, {"effort": effort_key})
        )
        return cast(ReasoningEffort, normalized["effort"])
    if effort_key in {"medium", "high", "xhigh", "max"}:
        return cast(ReasoningEffort, effort_key)

    _warn(
        warnings,
        f"Unknown reasoning effort '{raw_effort}', using high.",
    )
    return "high"


def apply_reasoning_mapping_to_provider_request(
    target_body: dict[str, Any],
    *,
    ir_request: dict[str, Any],
    target_provider: str,
    reasoning_mapping: Any = None,
    provider_name: str | None = None,
    shim_name: str | None = None,
    upstream_model: str | None = None,
    model_name: str | None = None,
    context: ConversionContext | None = None,
) -> dict[str, Any]:
    """Apply reasoning mapping to a final provider request body."""

    warnings = context.warnings if context is not None else None
    reasoning = ir_request.get("reasoning")
    reasoning_cfg = reasoning if isinstance(reasoning, dict) else {}
    effort = normalize_reasoning_effort(
        reasoning_cfg.get("effort"),
        mode=reasoning_cfg.get("mode"),
        warnings=warnings,
    )
    resolution = resolve_reasoning_mapping(
        explicit=reasoning_mapping,
        target_provider=target_provider,
        provider_name=provider_name,
        shim_name=shim_name,
        upstream_model=upstream_model,
        model_name=model_name,
    )
    if context is not None:
        context.metadata["reasoning_mapping"] = {
            "requested": resolution.requested,
            "effective": resolution.effective,
            "source": resolution.source,
            "effort": effort,
        }

    has_history = _has_reasoning_history(target_body)
    result = _remove_reasoning_controls(target_body)
    _write_mapping_fields(
        result,
        mapping=resolution.effective,
        target_provider=target_provider,
        effort=effort,
        has_reasoning_history=has_history,
        warnings=warnings,
    )
    return result


def _write_mapping_fields(
    body: dict[str, Any],
    *,
    mapping: ResolvedReasoningMapping,
    target_provider: str,
    effort: ReasoningEffort,
    has_reasoning_history: bool,
    warnings: list[str] | None,
) -> None:
    if mapping == "openai_responses":
        body["reasoning"] = {"effort": effort}
    elif mapping == "openai_chat":
        body["reasoning_effort"] = effort
    elif mapping == "anthropic":
        body["thinking"] = {"type": "adaptive"}
        output_config = dict(body.get("output_config") or {})
        output_config["effort"] = effort
        body["output_config"] = output_config
    elif mapping == "deepseek_v4":
        _write_deepseek_v4_fields(body, target_provider, effort)
    elif mapping == "glm_5_2":
        _write_glm_5_2_fields(body, target_provider, effort, has_reasoning_history)
    elif mapping == "qwen_3_7":
        _write_qwen_3_7_fields(body, target_provider, effort, warnings)
    elif mapping == "kimi_k2_7_code":
        _write_kimi_k2_7_code_fields(body, target_provider, effort, warnings)
    elif mapping == "minimax_m3":
        _write_minimax_m3_fields(body, target_provider, effort, warnings)
    elif mapping == "mimo_v2_5":
        _write_mimo_v2_5_fields(body, target_provider, effort, warnings)


def _write_deepseek_v4_fields(
    body: dict[str, Any], target_provider: str, effort: ReasoningEffort
) -> None:
    mapped_effort = "max" if effort in {"xhigh", "max"} else "high"
    if target_provider == "anthropic":
        body["thinking"] = {"type": "enabled"}
        body["output_config"] = {"effort": mapped_effort}
    elif target_provider == "openai_chat":
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = mapped_effort
    else:
        _write_target_protocol_fields(body, target_provider, effort)


def _write_glm_5_2_fields(
    body: dict[str, Any],
    target_provider: str,
    effort: ReasoningEffort,
    has_reasoning_history: bool,
) -> None:
    if target_provider != "openai_chat":
        _write_target_protocol_fields(body, target_provider, effort)
        return
    thinking: dict[str, Any] = {"type": "enabled"}
    if has_reasoning_history:
        thinking["clear_thinking"] = False
    body["thinking"] = thinking
    body["reasoning_effort"] = effort


def _write_qwen_3_7_fields(
    body: dict[str, Any],
    target_provider: str,
    effort: ReasoningEffort,
    warnings: list[str] | None,
) -> None:
    if target_provider == "openai_responses":
        body["reasoning"] = {
            "effort": _responses_compatible_effort(
                effort, model="Qwen 3.7", warnings=warnings
            )
        }
    elif target_provider == "anthropic":
        thinking: dict[str, Any] = {"type": "enabled"}
        if effort != "max":
            thinking["budget_tokens"] = _QWEN_BUDGETS[effort]
        body["thinking"] = thinking
    else:
        body["enable_thinking"] = True
        if effort != "max":
            body["thinking_budget"] = _QWEN_BUDGETS[effort]
        body["preserve_thinking"] = True


def _write_kimi_k2_7_code_fields(
    body: dict[str, Any],
    target_provider: str,
    effort: ReasoningEffort,
    warnings: list[str] | None,
) -> None:
    if target_provider != "openai_chat":
        _write_target_protocol_fields(body, target_provider, effort)
        return
    _warn(
        warnings,
        "Reasoning mapping kimi_k2_7_code has no request effort control; "
        "leaving upstream defaults.",
    )


def _write_minimax_m3_fields(
    body: dict[str, Any],
    target_provider: str,
    effort: ReasoningEffort,
    warnings: list[str] | None,
) -> None:
    if target_provider == "openai_responses":
        body["reasoning"] = {
            "effort": _responses_compatible_effort(
                effort, model="MiniMax M3", warnings=warnings
            )
        }
        warning = (
            "MiniMax M3 Responses accepts non-none effort levels for "
            "compatibility but does not tune reasoning depth."
        )
    else:
        body["thinking"] = {"type": "adaptive"}
        if target_provider != "anthropic":
            body["reasoning_split"] = True
        warning = (
            "Reasoning mapping minimax_m3 has no request effort control; "
            "leaving upstream defaults."
        )
    _warn(warnings, warning)


def _write_mimo_v2_5_fields(
    body: dict[str, Any],
    target_provider: str,
    effort: ReasoningEffort,
    warnings: list[str] | None,
) -> None:
    if target_provider == "openai_responses":
        body["reasoning"] = {
            "effort": _responses_compatible_effort(
                effort, model="MiMo V2.5", warnings=warnings
            )
        }
        warning = (
            "MiMo V2.5 Responses accepts low, medium, and high with "
            "identical reasoning behavior."
        )
    else:
        body["thinking"] = {"type": "enabled"}
        warning = (
            "Reasoning mapping mimo_v2_5 has no request effort control; "
            "leaving upstream defaults."
        )
    _warn(warnings, warning)


def _write_target_protocol_fields(
    body: dict[str, Any], target_provider: str, effort: ReasoningEffort
) -> None:
    """Write generic controls when a model has no official protocol endpoint."""
    if target_provider == "openai_responses":
        body["reasoning"] = {"effort": effort}
    elif target_provider == "anthropic":
        body["thinking"] = {"type": "adaptive"}
        body["output_config"] = {"effort": effort}
    else:
        body["reasoning_effort"] = effort


def _responses_compatible_effort(
    effort: ReasoningEffort,
    *,
    model: str,
    warnings: list[str] | None,
) -> Literal["low", "medium", "high"]:
    """Clamp model-specific Responses controls to their documented ladder."""
    if effort in {"xhigh", "max"}:
        _warn(
            warnings,
            f"{model} Responses API accepts reasoning effort only through high; "
            f"mapping {effort} to high.",
        )
        return "high"
    return cast(Literal["low", "medium", "high"], effort)


def _remove_reasoning_controls(body: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(body)
    for key in (
        "reasoning",
        "reasoning_effort",
        "thinking",
        "enable_thinking",
        "thinking_budget",
        "preserve_thinking",
        "reasoning_split",
    ):
        result.pop(key, None)
    output_config = result.get("output_config")
    if isinstance(output_config, dict) and "effort" in output_config:
        output_config = dict(output_config)
        output_config.pop("effort", None)
        if output_config:
            result["output_config"] = output_config
        else:
            result.pop("output_config", None)
    return result


def _fallback_mapping(target_provider: str | None) -> ResolvedReasoningMapping:
    if target_provider == "openai_responses":
        return "openai_responses"
    if target_provider == "anthropic":
        return "anthropic"
    return "openai_chat"


def _detect_model_mapping(
    model_key: str,
) -> ResolvedReasoningMapping | None:
    patterns: tuple[tuple[ResolvedReasoningMapping, str], ...] = (
        ("deepseek_v4", r"\bdeepseek[-_ ]?v?4(?:[-_ ]?(?:flash|pro))?\b"),
        ("glm_5_2", r"\bglm[-_ ]?5[._-]?2\b"),
        ("qwen_3_7", r"\bqwen[-_ ]?3[._-]?7(?:[-_ ]?(?:plus|max))?\b"),
        ("kimi_k2_7_code", r"\bkimi[-_ ]?k2[._-]?7(?:[-_ ]?code)?\b"),
        ("minimax_m3", r"\bminimax[-_ ]?m3\b"),
        ("mimo_v2_5", r"\bmimo[-_ ]?v?2[._-]?5\b"),
    )
    for mapping, pattern in patterns:
        if re.search(pattern, model_key):
            return mapping
    return None


def _has_reasoning_history(value: Any) -> bool:
    if isinstance(value, dict):
        if "reasoning_content" in value:
            return True
        if value.get("type") in {"reasoning", "thinking"}:
            return True
        return any(_has_reasoning_history(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_reasoning_history(v) for v in value)
    return False


def _clean_key(value: Any) -> str:
    if value is None:
        return ""
    key = str(value).strip().lower()
    key = key.replace("z.ai", "zai")
    key = key.replace("z-ai", "zai")
    return key.replace("-", "_")


def _warn(warnings: list[str] | None, message: str) -> None:
    if warnings is not None:
        warnings.append(message)
