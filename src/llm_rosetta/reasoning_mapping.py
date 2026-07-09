"""Provider-specific reasoning request mapping.

The gateway-facing reasoning configuration is intentionally separate from the
older shim ``ReasoningCapability`` helper.  Codex-style requests should keep
thinking enabled for reasoning-capable coding models, then map the requested
effort onto each upstream provider's public control fields.
"""

from __future__ import annotations

from dataclasses import dataclass
import copy
import re
from typing import Any, Literal, cast

from llm_rosetta.converters.base.context import ConversionContext

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
ReasoningEffort = Literal["light", "medium", "high", "xhigh", "max"]
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

_RESOLVED_MAPPINGS: set[str] = set(VALID_REASONING_MAPPINGS) - {"auto"}
_QWEN_BUDGETS: dict[ReasoningEffort, int] = {
    "light": 2048,
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
            effective=cast(ResolvedReasoningMapping, requested),
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
            "Reasoning disable request ignored; using light effort because "
            "reasoning-capable coding models keep thinking enabled.",
        )
        return "light"

    if not effort_key:
        return "high"

    if effort_key in {"minimal", "low", "light"}:
        return "light"
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
    model_capabilities: list[str] | None = None,
    context: ConversionContext | None = None,
) -> dict[str, Any]:
    """Apply reasoning mapping to a final provider request body.

    The mapping is only active when the resolved model declares the
    ``reasoning`` capability.  This keeps generic library conversions stable
    while allowing gateway models to opt into always-on thinking semantics.
    """
    if model_capabilities is None or "reasoning" not in model_capabilities:
        return target_body

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
        body["reasoning"] = {"effort": _openai_effort(effort)}
    elif mapping == "openai_chat":
        body["reasoning_effort"] = _openai_effort(effort)
    elif mapping == "anthropic":
        body["thinking"] = {"type": "adaptive"}
        output_config = dict(body.get("output_config") or {})
        output_config["effort"] = effort
        body["output_config"] = output_config
    elif mapping == "deepseek_v4":
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = "max" if effort in {"xhigh", "max"} else "high"
    elif mapping == "glm_5_2":
        thinking: dict[str, Any] = {"type": "enabled"}
        if has_reasoning_history:
            thinking["clear_thinking"] = False
        body["thinking"] = thinking
        body["reasoning_effort"] = effort
    elif mapping == "qwen_3_7":
        body["enable_thinking"] = True
        if effort != "max":
            body["thinking_budget"] = _QWEN_BUDGETS[effort]
        body["preserve_thinking"] = True
    elif mapping == "kimi_k2_7_code":
        _warn(
            warnings,
            "Reasoning mapping kimi_k2_7_code has no request effort control; "
            "leaving upstream defaults.",
        )
    elif mapping == "minimax_m3":
        body["thinking"] = {"type": "adaptive"}
        if target_provider != "anthropic":
            body["reasoning_split"] = True
        _warn(
            warnings,
            "Reasoning mapping minimax_m3 has no request effort control; "
            "leaving upstream defaults.",
        )
    elif mapping == "mimo_v2_5":
        body["thinking"] = {"type": "enabled"}
        _warn(
            warnings,
            "Reasoning mapping mimo_v2_5 has no request effort control; "
            "leaving upstream defaults.",
        )


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


def _openai_effort(effort: ReasoningEffort) -> str:
    return "xhigh" if effort == "max" else effort


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
