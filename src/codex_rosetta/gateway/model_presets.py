"""Shared validation and lookup helpers for bundled Codex model presets."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from importlib import resources
from typing import Any

PRESET_RESOURCE = "codex_model_presets.json"
MODEL_CATALOG_RESOURCE = "codex_models_0_144_4.json"
MODEL_INFO_STRING_FIELDS = ("slug", "display_name", "description", "identity")
MODEL_INFO_INTEGER_FIELDS = ("priority", "context_window")
MODEL_INFO_LIST_FIELDS = ("input_modalities", "supported_reasoning_levels")
MODEL_INFO_FIELDS = frozenset(
    (*MODEL_INFO_STRING_FIELDS, *MODEL_INFO_INTEGER_FIELDS, *MODEL_INFO_LIST_FIELDS)
)
MODEL_CATALOG_OVERRIDE_FIELDS = frozenset(
    {
        "comp_hash",
        "supports_reasoning_summaries",
        "default_reasoning_summary",
        "truncation_policy",
        "supports_parallel_tool_calls",
    }
)


@lru_cache(maxsize=1)
def _cached_model_preset_resource() -> dict[str, Any]:
    raw = (
        resources.files("codex_rosetta.gateway")
        .joinpath(PRESET_RESOURCE)
        .read_text("utf-8")
    )
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("bundled Codex model presets must be an object")
    if not isinstance(value.get("shared_overrides"), dict):
        raise ValueError("bundled Codex model presets have invalid shared overrides")
    if not isinstance(value.get("models"), list):
        raise ValueError("bundled Codex model presets have invalid models")
    return value


def load_model_preset_resource() -> dict[str, Any]:
    """Return an isolated copy of the bundled model-preset resource."""
    return copy.deepcopy(_cached_model_preset_resource())


@lru_cache(maxsize=1)
def _cached_model_catalog_resource() -> dict[str, Any]:
    raw = (
        resources.files("codex_rosetta.gateway")
        .joinpath(MODEL_CATALOG_RESOURCE)
        .read_text("utf-8")
    )
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("models"), list):
        raise ValueError("bundled Codex model catalog has invalid models")
    return value


def _catalog_model_info(value: Any, *, index: int) -> dict[str, Any]:
    """Project one full Codex catalog model into editable model-info fields."""
    if not isinstance(value, dict):
        raise ValueError(
            f"bundled Codex catalog model at index {index} must be an object"
        )
    reasoning = value.get("supported_reasoning_levels")
    if not isinstance(reasoning, list):
        raise ValueError(
            f"bundled Codex catalog model at index {index} has invalid reasoning levels"
        )
    efforts = [
        level.get("effort") if isinstance(level, dict) else level for level in reasoning
    ]
    projected = {
        "slug": value.get("slug"),
        "display_name": value.get("display_name"),
        "description": value.get("description"),
        "identity": value.get("display_name"),
        "priority": value.get("priority"),
        "context_window": value.get("context_window"),
        "input_modalities": value.get("input_modalities"),
        "supported_reasoning_levels": efforts,
    }
    return normalize_model_info(
        projected, field=f"bundled Codex catalog model at index {index}"
    )


def normalize_model_info(value: Any, *, field: str) -> dict[str, Any]:
    """Validate one editable model-info document in preset-resource shape."""
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    unknown = sorted(set(value) - MODEL_INFO_FIELDS)
    if unknown:
        raise ValueError(f"{field} contains unsupported fields: {unknown}")

    normalized: dict[str, Any] = {}
    for key in MODEL_INFO_STRING_FIELDS:
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field}.{key} must be a non-empty string")
        normalized[key] = item.strip()

    for key in MODEL_INFO_INTEGER_FIELDS:
        item = value.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            raise ValueError(f"{field}.{key} must be a positive integer")
        normalized[key] = item

    for key in MODEL_INFO_LIST_FIELDS:
        item = value.get(key)
        if (
            not isinstance(item, list)
            or not item
            or not all(isinstance(entry, str) and entry.strip() for entry in item)
        ):
            raise ValueError(f"{field}.{key} must be a non-empty string array")
        normalized[key] = list(dict.fromkeys(entry.strip() for entry in item))
    return normalized


def normalize_model_preset(value: Any, *, field: str) -> dict[str, Any]:
    """Validate one bundled preset and its optional catalog-only overrides."""
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    unknown = sorted(set(value) - MODEL_INFO_FIELDS - MODEL_CATALOG_OVERRIDE_FIELDS)
    if unknown:
        raise ValueError(f"{field} contains unsupported fields: {unknown}")

    normalized = normalize_model_info(
        {key: value.get(key) for key in MODEL_INFO_FIELDS},
        field=field,
    )
    if "comp_hash" in value:
        comp_hash = value["comp_hash"]
        if not isinstance(comp_hash, str) or not comp_hash.strip():
            raise ValueError(f"{field}.comp_hash must be a non-empty string")
        normalized["comp_hash"] = comp_hash.strip()

    for key in ("supports_reasoning_summaries", "supports_parallel_tool_calls"):
        item = value.get(key)
        if item is not None:
            if not isinstance(item, bool):
                raise ValueError(f"{field}.{key} must be a boolean")
            normalized[key] = item

    summary = value.get("default_reasoning_summary")
    if summary is not None:
        if summary not in {"none", "auto", "concise", "detailed"}:
            raise ValueError(
                f"{field}.default_reasoning_summary has an unsupported value"
            )
        normalized["default_reasoning_summary"] = summary

    truncation = value.get("truncation_policy")
    if truncation is not None:
        if not isinstance(truncation, dict):
            raise ValueError(f"{field}.truncation_policy must be an object")
        if set(truncation) != {"mode", "limit"}:
            raise ValueError(f"{field}.truncation_policy must contain mode and limit")
        if truncation.get("mode") != "bytes":
            raise ValueError(f"{field}.truncation_policy.mode must be 'bytes'")
        limit = truncation.get("limit")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError(
                f"{field}.truncation_policy.limit must be a positive integer"
            )
        normalized["truncation_policy"] = {"mode": "bytes", "limit": limit}
    return normalized


def model_presets_for_admin() -> list[dict[str, Any]]:
    """Return all bundled models available to exact-slug Admin detection."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(_cached_model_catalog_resource()["models"]):
        preset = _catalog_model_info(value, index=index)
        slug = preset["slug"]
        if slug in seen:
            raise ValueError(f"duplicate bundled Codex model preset: {slug}")
        seen.add(slug)
        result.append(preset)
    for index, value in enumerate(load_model_preset_resource()["models"]):
        preset = normalize_model_preset(
            value, field=f"bundled model preset at index {index}"
        )
        slug = preset["slug"]
        if slug in seen:
            raise ValueError(f"duplicate bundled Codex model preset: {slug}")
        seen.add(slug)
        result.append(preset)
    return result


def detect_model_preset(
    exposed_model: str, upstream_model: str | None = None
) -> dict[str, Any] | None:
    """Return an exact-slug preset, preferring the configured upstream model."""
    candidate = upstream_model.strip() if isinstance(upstream_model, str) else ""
    slug = candidate or exposed_model
    for preset in model_presets_for_admin():
        if preset["slug"] == slug:
            return preset
    return None


def model_input_modalities(
    exposed_model: str,
    upstream_model: str | None = None,
) -> list[str] | None:
    """Return compact-preset input modalities for one routed model.

    Runtime image filtering deliberately uses only ``codex_model_presets.json``.
    Full Codex catalog metadata and Admin ``model_info`` overrides describe the
    Codex-facing catalog but do not impose gateway-side modality restrictions.
    """
    candidate = upstream_model.strip() if isinstance(upstream_model, str) else ""
    slug = candidate or exposed_model
    for index, value in enumerate(load_model_preset_resource()["models"]):
        preset = normalize_model_preset(
            value, field=f"bundled model preset at index {index}"
        )
        if preset["slug"] != slug:
            continue
        return list(preset["input_modalities"])
    return None
