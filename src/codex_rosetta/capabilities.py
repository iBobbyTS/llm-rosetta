"""Input adaptation for model reasoning and preset-defined modalities.

This module handles **platform-level** constraints that apply regardless of
provider dialect. The pipeline adapts the IR request to match the input
modalities declared by the bundled model preset.

This is distinct from **shim transforms** (provider-specific dialect
adaptation) and from **converter logic** (API-standard translation).

Functions follow the ``enforce_*`` naming convention:

- :func:`enforce_reasoning` — configure reasoning output mode (pre-IR)
- :func:`enforce_vision` — strip images for non-vision models (post-IR)

Called by :class:`~codex_rosetta.pipeline.ConversionPipeline` at the
appropriate pipeline stages.
"""

from __future__ import annotations

from typing import Any

from codex_rosetta.converters.base.context import ConversionContext
from codex_rosetta.shims.provider_shim import (
    ProviderShim,
    ReasoningCapability,
    resolve_shim,
)


def _apply_config_reasoning_override(
    base: ReasoningCapability,
    override: dict[str, Any],
) -> ReasoningCapability:
    """Merge config-level reasoning overrides onto a base capability.

    Only fields present in *override* are replaced; the rest inherit
    from *base*.
    """
    return ReasoningCapability(
        disabled=override.get("disabled", base.disabled),
        effort_field=override.get("effort_field", base.effort_field),
        max_effort=override.get("max_effort", base.max_effort),
        thinking_type=override.get("thinking_type", base.thinking_type),
        unsigned_reasoning_blocks=override.get(
            "unsigned_reasoning_blocks", base.unsigned_reasoning_blocks
        ),
        effort_map=override.get("effort_map", base.effort_map),
        budget_tokens_default_ratio=override.get(
            "budget_tokens_default_ratio", base.budget_tokens_default_ratio
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enforce_reasoning(
    ctx: ConversionContext,
    shim: ProviderShim | str | None,
    *,
    model: str | None = None,
    config_override: dict[str, Any] | None = None,
) -> None:
    """Configure reasoning capability in the conversion context.

    Injects ``reasoning_cap`` into *ctx* so converters produce the
    correct thinking/reasoning output for the target provider.

    Must be called **before** source → IR conversion (converters read
    ``ctx.options["reasoning_cap"]`` during parsing).

    Resolution priority (highest first):

    1. *config_override* — per-model override from external config
       (e.g. gateway admin UI).
    2. ``shim.model_reasoning[model]`` — per-model override from the
       provider YAML.
    3. ``shim.reasoning`` — provider-level default.

    Args:
        ctx: Conversion context to mutate.
        shim: ProviderShim instance, registered name, or None (no-op).
        model: Upstream model ID (for per-model reasoning overrides).
        config_override: External reasoning override (highest priority).
    """
    resolved = resolve_shim(shim)
    if resolved is None:
        return

    cap = resolved.reasoning
    # Model-level override (keyed by upstream model ID)
    if model and resolved.model_reasoning and model in resolved.model_reasoning:
        cap = resolved.model_reasoning[model]
    # Config-level override (from admin UI, keyed by gateway model name)
    if cap is not None and config_override:
        cap = _apply_config_reasoning_override(cap, config_override)
    if cap is not None:
        ctx.options["reasoning_cap"] = cap


def enforce_vision(
    ir_request: dict[str, Any],
    *,
    input_modalities: list[str] | None = None,
    model: str = "",
    request_id: str = "-",
) -> dict[str, Any]:
    """Strip images from the IR request if its preset lacks image input.

    Must be called **after** source → IR conversion (operates on the IR
    dict, not the raw provider body).

    No-op when *input_modalities* is ``None`` (unknown) or includes ``"image"``.

    Args:
        ir_request: The IR request dict — **always use the return value**.
        input_modalities: Input modalities declared by the model preset.
        model: Upstream model identifier (for logging).
        request_id: Request identifier (for logging).

    Returns:
        The IR request with images replaced by text placeholders, or
        the original request if the model has vision capability.
    """
    if input_modalities is None or "image" in input_modalities:
        return ir_request

    from codex_rosetta.converters.base.helpers.image_limit import (
        strip_images_for_non_vision,
    )

    return strip_images_for_non_vision(ir_request, model=model, request_id=request_id)
