"""Config CRUD and upstream model fetch route handlers."""

from __future__ import annotations

import asyncio
from typing import Any

from codex_rosetta._vendor.httpclient import AsyncClient
from codex_rosetta._vendor.httpserver import JSONResponse, Response
from codex_rosetta.reasoning_mapping import (
    normalize_reasoning_mapping,
    resolve_reasoning_mapping,
)
from codex_rosetta.shims import list_shims

from ...config import (
    GatewayConfig,
    load_config_raw,
    resolve_provider_config_type_and_shim,
)
from ...providers import known_provider_types
from ...stream_trace import DEFAULT_MAX_CHARS
from ...tool_adaptation import (
    DEFAULT_ENABLE_PHASE_DETECTION,
    DEFAULT_ENABLE_TOOL_DESCRIPTION_OPTIMIZATION,
    DEFAULT_TOOL_CALL_CACHE_TTL_HOURS,
    DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS,
    validate_tool_call_cache_ttl_hours,
)
from ...transport.http.transport import request_bounded_response
from ._shared import (
    _build_provider_entry,
    _commit_gateway_config,
    _get_config_path,
    _handle_provider_rename,
    _mask_api_key,
    _parse_json_object,
    _reload_gateway_config,
)

import logging

logger = logging.getLogger("codex-rosetta-gateway")


def _mask_web_search_config(value: Any) -> dict[str, Any]:
    """Return a copy of server.web_search with sensitive values masked."""
    if not isinstance(value, dict):
        return {}
    masked = dict(value)
    if "tavily_api_key" in masked:
        masked["tavily_api_key"] = _mask_api_key(str(masked["tavily_api_key"]))
    return masked


def _mask_server_config(value: Any) -> dict[str, Any]:
    """Return a copy of server config with sensitive admin values masked."""
    server = dict(value) if isinstance(value, dict) else {}
    server.pop("admin_password", None)
    if "api_key" in server:
        server["api_key"] = _mask_api_key(server["api_key"])
    if "api_keys" in server:
        server["api_keys"] = [
            {**entry, "key": _mask_api_key(entry.get("key", ""))}
            for entry in server["api_keys"]
        ]
    if "web_search" in server:
        server["web_search"] = _mask_web_search_config(server["web_search"])
    return server


def _apply_web_search_settings(
    server: dict[str, Any], body: dict[str, Any]
) -> str | None:
    """Merge admin web search settings into the server config."""
    if "web_search" not in body:
        return None

    web_search = body.get("web_search") or {}
    if not isinstance(web_search, dict):
        return "'web_search' must be an object"

    existing_web_search = server.get("web_search", {})
    if not isinstance(existing_web_search, dict):
        existing_web_search = {}
    next_web_search = dict(existing_web_search)

    if "tavily_api_key" in web_search:
        tavily_api_key = str(web_search.get("tavily_api_key") or "").strip()
        if "***" in tavily_api_key:
            existing_key = existing_web_search.get("tavily_api_key")
            if existing_key:
                next_web_search["tavily_api_key"] = existing_key
        elif tavily_api_key:
            next_web_search["tavily_api_key"] = tavily_api_key
        else:
            next_web_search.pop("tavily_api_key", None)

    if next_web_search:
        server["web_search"] = next_web_search
    else:
        server.pop("web_search", None)
    return None


def _get_gateway_config(request: Any) -> GatewayConfig | None:
    """Return the live GatewayConfig owned by this app instance."""
    return getattr(request.app, "gateway_config", None)


def _get_version() -> str:
    """Return the codex-rosetta package version."""
    try:
        from codex_rosetta import __version__

        return __version__
    except Exception:
        return "unknown"


def _clean_tool_adaptation(value: Any) -> dict[str, Any] | None:
    """Normalize model-level tool adaptation settings from admin requests."""
    if not isinstance(value, dict):
        return None

    ttl_hours = validate_tool_call_cache_ttl_hours(
        value.get("tool_call_cache_ttl_hours", DEFAULT_TOOL_CALL_CACHE_TTL_HOURS)
    )

    cleaned = {
        "localize_code_editing_tools": bool(value.get("localize_code_editing_tools")),
        "use_apply_patch_for_code_edits": bool(
            value.get(
                "use_apply_patch_for_code_edits",
                DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS,
            )
        ),
        "remove_image_generation": bool(value.get("remove_image_generation")),
        "enable_tool_description_optimization": bool(
            value.get(
                "enable_tool_description_optimization",
                DEFAULT_ENABLE_TOOL_DESCRIPTION_OPTIMIZATION,
            )
        ),
        "enable_phase_detection": bool(
            value.get("enable_phase_detection", DEFAULT_ENABLE_PHASE_DETECTION)
        ),
        "tool_call_cache_ttl_hours": ttl_hours,
    }
    return (
        cleaned
        if cleaned["localize_code_editing_tools"]
        or cleaned["remove_image_generation"]
        or not cleaned["use_apply_patch_for_code_edits"]
        or not cleaned["enable_tool_description_optimization"]
        or not cleaned["enable_phase_detection"]
        or ttl_hours != DEFAULT_TOOL_CALL_CACHE_TTL_HOURS
        else None
    )


def _resolve_model_reasoning_mapping(
    model_name: str,
    entry: dict[str, Any],
    raw_models: dict[str, Any],
    providers: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the effective reasoning mapping for admin display."""
    provider_name = entry.get("provider", "")
    provider_cfg = providers.get(provider_name, {})
    provider_type, shim_name = resolve_provider_config_type_and_shim(
        provider_name, provider_cfg
    )
    upstream = entry.get("upstream_model") or model_name

    raw_model = raw_models.get(model_name, {})
    explicit = (
        raw_model.get("reasoning_mapping")
        if isinstance(raw_model, dict)
        else entry.get("reasoning_mapping")
    )
    resolution = resolve_reasoning_mapping(
        explicit=explicit,
        target_provider=provider_type,
        provider_name=provider_name,
        shim_name=shim_name,
        upstream_model=upstream,
        model_name=model_name,
        provider_config=provider_cfg,
    )
    return {
        "source": resolution.source,
        "requested": resolution.requested,
        "effective": resolution.effective,
        "target_provider": provider_type,
    }


def _normalize_model_entry(
    model_name: str,
    value: Any,
    *,
    group_provider: str | None = None,
) -> dict[str, Any]:
    """Return a model config entry in admin-UI dict form."""
    if group_provider is not None:
        entry: dict[str, Any] = {
            "provider": group_provider,
            "capabilities": ["text"],
        }
        if isinstance(value, str):
            if value:
                entry["upstream_model"] = value
            return entry
        if not isinstance(value, dict):
            return entry
        entry["capabilities"] = value.get("capabilities", ["text"])
        if value.get("upstream_model"):
            entry["upstream_model"] = value["upstream_model"]
    elif isinstance(value, str):
        entry = {"provider": value, "capabilities": ["text"]}
    elif isinstance(value, dict):
        entry = {
            "provider": value.get("provider", ""),
            "capabilities": value.get("capabilities", ["text"]),
        }
        if value.get("upstream_model"):
            entry["upstream_model"] = value["upstream_model"]
    else:
        return {}

    if isinstance(value, dict):
        if "reasoning_mapping" in value:
            entry["reasoning_mapping"] = normalize_reasoning_mapping(
                value.get("reasoning_mapping")
            )
        if value.get("tool_adaptation"):
            entry["tool_adaptation"] = value["tool_adaptation"]
    return entry


def _normalize_models_for_admin(
    raw_models: dict[str, Any],
    providers: dict[str, Any],
    reasoning_raw_models: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a flat model mapping and attach effective reasoning metadata."""
    reasoning_raw_models = reasoning_raw_models or raw_models
    normalized: dict[str, Any] = {}
    for name, value in raw_models.items():
        entry = _normalize_model_entry(name, value)
        if not entry:
            continue
        entry["reasoning"] = _resolve_model_reasoning_mapping(
            name, entry, reasoning_raw_models, providers
        )
        normalized[name] = entry
    return normalized


def _normalize_model_groups_for_admin(
    raw_model_groups: dict[str, Any],
    providers: dict[str, Any],
    expanded_raw_models: dict[str, Any],
) -> dict[str, Any]:
    """Normalize model group config for admin UI consumption."""
    groups: dict[str, Any] = {}
    if not isinstance(raw_model_groups, dict):
        return groups

    for group_name, group_value in raw_model_groups.items():
        if not isinstance(group_value, dict):
            continue
        provider = group_value.get("provider", "")
        raw_group_models = group_value.get("models", {})
        models: dict[str, Any] = {}
        if isinstance(raw_group_models, dict):
            for model_name, model_value in raw_group_models.items():
                entry = _normalize_model_entry(
                    model_name, model_value, group_provider=provider
                )
                entry.pop("provider", None)
                reasoning_entry = dict(entry)
                reasoning_entry["provider"] = provider
                entry["reasoning"] = _resolve_model_reasoning_mapping(
                    model_name,
                    reasoning_entry,
                    expanded_raw_models,
                    providers,
                )
                models[model_name] = entry
        groups[group_name] = {"provider": provider, "models": models}
    return groups


def _clean_group_model_entry(value: Any) -> dict[str, Any]:
    """Normalize one model entry inside a model group request."""
    if isinstance(value, str):
        return {"upstream_model": value} if value else {}
    if not isinstance(value, dict):
        raise ValueError("model entries must be objects or strings")

    capabilities = value.get("capabilities")
    entry: dict[str, Any] = {
        "capabilities": (
            [str(capability) for capability in capabilities]
            if isinstance(capabilities, list) and capabilities
            else ["text"]
        )
    }

    upstream_model = str(value.get("upstream_model") or "").strip()
    if upstream_model:
        entry["upstream_model"] = upstream_model

    if "reasoning_mapping" in value:
        entry["reasoning_mapping"] = normalize_reasoning_mapping(
            value.get("reasoning_mapping")
        )

    cleaned_tool_adaptation = _clean_tool_adaptation(value.get("tool_adaptation"))
    if cleaned_tool_adaptation:
        entry["tool_adaptation"] = cleaned_tool_adaptation

    return entry


def _model_group_model_names(
    model_groups: dict[str, Any],
    *,
    exclude_group: str | None = None,
) -> set[str]:
    """Return downstream model names defined by model groups."""
    names: set[str] = set()
    if not isinstance(model_groups, dict):
        return names
    for group_name, group_value in model_groups.items():
        if exclude_group and group_name == exclude_group:
            continue
        if not isinstance(group_value, dict):
            continue
        group_models = group_value.get("models", {})
        if isinstance(group_models, dict):
            names.update(str(name) for name in group_models)
    return names


def _handle_model_rename(
    data: dict[str, Any], rename_from: str | None, name: str
) -> Response | None:
    """Apply top-level model rename validation and mutation."""
    if not rename_from or rename_from == name:
        return None

    models = data.setdefault("models", {})
    if rename_from not in models:
        return JSONResponse(
            {"error": f"Original model '{rename_from}' not found"},
            status_code=404,
        )
    if name in models:
        return JSONResponse(
            {"error": f"Model '{name}' already exists"},
            status_code=409,
        )
    del models[rename_from]
    return None


def _handle_model_group_rename(
    model_groups: dict[str, Any], rename_from: str | None, name: str
) -> Response | None:
    """Apply model group rename validation and mutation."""
    if not rename_from or rename_from == name:
        return None

    if rename_from not in model_groups:
        return JSONResponse(
            {"error": f"Original model group '{rename_from}' not found"},
            status_code=404,
        )
    if name in model_groups:
        return JSONResponse(
            {"error": f"Model group '{name}' already exists"},
            status_code=409,
        )
    del model_groups[rename_from]
    return None


def _clean_group_models(models_body: dict[str, Any]) -> dict[str, Any]:
    """Normalize all model entries from a model group request."""
    cleaned_models: dict[str, Any] = {}
    for model_name, model_value in models_body.items():
        clean_name = str(model_name).strip()
        if not clean_name:
            continue
        cleaned_models[clean_name] = _clean_group_model_entry(model_value)
    return cleaned_models


def _model_group_duplicate_response(
    data: dict[str, Any],
    model_groups: dict[str, Any],
    cleaned_models: dict[str, Any],
    *,
    exclude_group: str | None,
) -> Response | None:
    """Return a conflict response when grouped models duplicate other routes."""
    flat_models = data.get("models", {})
    duplicate_flat = sorted(set(cleaned_models) & set(flat_models))
    if duplicate_flat:
        return JSONResponse(
            {"error": f"Models already exist outside this group: {duplicate_flat}"},
            status_code=409,
        )

    duplicate_group = sorted(
        set(cleaned_models)
        & _model_group_model_names(model_groups, exclude_group=exclude_group)
    )
    if duplicate_group:
        return JSONResponse(
            {
                "error": f"Models already exist in another model group: {duplicate_group}"
            },
            status_code=409,
        )
    return None


async def get_config(request: Any) -> Response:
    """Return the current (raw) gateway configuration."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    try:
        raw = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    # Mask API keys and keep legacy type fallback only for legacy entries.
    providers = raw.get("providers", {})
    masked_providers: dict[str, Any] = {}
    for name, cfg in providers.items():
        masked = dict(cfg)
        if "api_key" in masked:
            masked["api_key"] = _mask_api_key(masked["api_key"])
        # Ensure old provider-name-as-shim configs remain editable in the UI.
        if "api_type" not in masked and "type" not in masked and "shim" not in masked:
            masked["type"] = name
        masked_providers[name] = masked

    # Normalize models to dict format for consistent admin UI.  ``models`` is
    # the effective runtime view; ``standalone_models`` and ``model_groups`` are
    # the management views used by the admin page.
    raw_models = raw.get("models", {}) or {}
    raw_model_groups = raw.get("model_groups", {}) or {}
    expanded_raw_models = GatewayConfig._expand_model_groups(
        raw_models, raw_model_groups
    )
    standalone_models = _normalize_models_for_admin(
        raw_models, providers, expanded_raw_models
    )
    models_normalized = _normalize_models_for_admin(
        expanded_raw_models, providers, expanded_raw_models
    )
    model_groups = _normalize_model_groups_for_admin(
        raw_model_groups, providers, expanded_raw_models
    )

    config: GatewayConfig = request.app.gateway_config
    server = _mask_server_config(raw.get("server", {}))
    server.setdefault("request_body_limit_mb", config.request_body_limit_config_value)
    return JSONResponse(
        {
            "config_path": config_path,
            "providers": masked_providers,
            "models": models_normalized,
            "standalone_models": standalone_models,
            "model_groups": model_groups,
            "server": server,
            "credential_visible": config.credential_visible,
            "version": _get_version(),
            "known_provider_types": known_provider_types(),
            "registered_shims": [
                {
                    "name": s.name,
                    "base": s.base,
                    "logo": s.logo,
                    "default_base_url": s.default_base_url,
                    "default_api_key_env": s.default_api_key_env,
                }
                for s in list_shims()
            ],
        }
    )


async def put_provider(request: Any, **kwargs: Any) -> Response:
    """Add or update a provider entry."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    name = request.path_params["name"]

    body = _parse_json_object(request)
    if isinstance(body, Response):
        return body

    api_key = body.get("api_key", "")
    base_url = body.get("base_url", "")

    try:
        data = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    existing_providers = data.get("providers", {})
    resolve_name = body.get("rename_from", name) or name

    # When api_key is omitted/empty and we're editing, keep the existing key
    if not api_key and resolve_name in existing_providers:
        api_key = existing_providers[resolve_name].get("api_key", "")

    if not api_key or not base_url:
        return JSONResponse(
            {"error": "Both 'api_key' and 'base_url' are required"}, status_code=400
        )

    provider_entry = _build_provider_entry(
        body, api_key, base_url, existing_providers, resolve_name
    )

    # Handle rename: remove old entry and update model references
    rename_from = body.get("rename_from")
    if rename_from and rename_from != name:
        rename_err = _handle_provider_rename(data, rename_from, name)
        if rename_err is not None:
            return rename_err

    data.setdefault("providers", {})[name] = provider_entry

    new_config, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error
    assert new_config is not None

    return JSONResponse(
        {
            "ok": True,
            "provider": name,
            "providers": list(new_config.providers.keys()),
        }
    )


async def delete_provider(request: Any, **kwargs: Any) -> Response:
    """Remove a provider entry."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    name = request.path_params["name"]

    try:
        data = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    providers = data.get("providers", {})
    if name not in providers:
        return JSONResponse({"error": f"Provider '{name}' not found"}, status_code=404)

    # Check if any model still references this provider
    models = data.get("models", {})
    referencing = [
        m
        for m, p in models.items()
        if (p["provider"] if isinstance(p, dict) else p) == name
    ]
    raw_model_groups = data.get("model_groups", {})
    model_groups = raw_model_groups if isinstance(raw_model_groups, dict) else {}
    referencing_groups = [
        group_name
        for group_name, group in model_groups.items()
        if isinstance(group, dict) and group.get("provider") == name
    ]

    from ._shared import _qp

    cascade = _qp(request, "cascade") in ("true", "1")
    if (referencing or referencing_groups) and not cascade:
        return JSONResponse(
            {
                "error": (
                    f"Cannot delete provider '{name}': referenced by models: "
                    f"{referencing}, model groups: {referencing_groups}"
                )
            },
            status_code=409,
        )

    # Cascade: remove referencing models first
    cascade_deleted: list[str] = []
    if referencing and cascade:
        for model_name in referencing:
            del models[model_name]
            cascade_deleted.append(model_name)
    cascade_deleted_groups: list[str] = []
    if referencing_groups and cascade:
        for group_name in referencing_groups:
            del model_groups[group_name]
            cascade_deleted_groups.append(group_name)

    del providers[name]

    new_config, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error
    assert new_config is not None

    result: dict[str, Any] = {
        "ok": True,
        "deleted": name,
        "providers": list(new_config.providers.keys()),
    }
    if cascade_deleted:
        result["cascade_deleted_models"] = cascade_deleted
    if cascade_deleted_groups:
        result["cascade_deleted_model_groups"] = cascade_deleted_groups
    return JSONResponse(result)


async def toggle_provider(request: Any, **kwargs: Any) -> Response:
    """Toggle a provider's enabled/disabled state."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    name = request.path_params["name"]

    try:
        data = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    providers = data.get("providers", {})
    if name not in providers:
        return JSONResponse({"error": f"Provider '{name}' not found"}, status_code=404)

    # Toggle: if currently enabled (or unset → default True), disable; otherwise enable
    currently_enabled = providers[name].get("enabled", True)
    new_enabled = not currently_enabled

    if new_enabled:
        # Remove the key entirely when re-enabling (True is the default)
        providers[name].pop("enabled", None)
    else:
        providers[name]["enabled"] = False

    _, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error

    return JSONResponse({"ok": True, "provider": name, "enabled": new_enabled})


async def put_model(request: Any, **kwargs: Any) -> Response:
    """Add or update a model routing entry."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    name = request.path_params["name"]

    body = _parse_json_object(request)
    if isinstance(body, Response):
        return body

    provider = body.get("provider")
    if not provider:
        return JSONResponse({"error": "'provider' is required"}, status_code=400)

    try:
        data = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    # Validate that the provider exists
    providers = data.get("providers", {})
    if provider not in providers:
        return JSONResponse(
            {"error": f"Provider '{provider}' not found in config"}, status_code=400
        )

    capabilities_value = body.get("capabilities")
    capabilities = (
        [str(capability) for capability in capabilities_value]
        if isinstance(capabilities_value, list) and capabilities_value
        else ["text"]
    )

    # Handle rename: remove old entry
    rename_from = body.get("rename_from")
    models = data.setdefault("models", {})
    grouped_model_names = _model_group_model_names(data.get("model_groups", {}))
    if name in grouped_model_names:
        return JSONResponse(
            {"error": f"Model '{name}' already exists in a model group"},
            status_code=409,
        )
    rename_error = _handle_model_rename(data, rename_from, name)
    if rename_error is not None:
        return rename_error

    model_entry: dict[str, Any] = {
        "provider": provider,
        "capabilities": capabilities,
    }
    upstream_model = body.get("upstream_model")
    if upstream_model:
        model_entry["upstream_model"] = upstream_model

    if "reasoning_mapping" in body and "embedding" not in capabilities:
        try:
            model_entry["reasoning_mapping"] = normalize_reasoning_mapping(
                body.get("reasoning_mapping")
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    try:
        cleaned_tool_adaptation = _clean_tool_adaptation(body.get("tool_adaptation"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if cleaned_tool_adaptation:
        model_entry["tool_adaptation"] = cleaned_tool_adaptation
    models[name] = model_entry

    new_config, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error
    assert new_config is not None

    return JSONResponse(
        {
            "ok": True,
            "model": name,
            "provider": provider,
            "capabilities": capabilities,
            "models": dict(new_config.models),
        }
    )


async def delete_model(request: Any, **kwargs: Any) -> Response:
    """Remove a model routing entry."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    name = request.path_params["name"]

    try:
        data = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    models = data.get("models", {})
    if name not in models:
        return JSONResponse({"error": f"Model '{name}' not found"}, status_code=404)

    del models[name]

    new_config, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error
    assert new_config is not None

    return JSONResponse(
        {
            "ok": True,
            "deleted": name,
            "models": dict(new_config.models),
        }
    )


async def put_model_group(request: Any, **kwargs: Any) -> Response:
    """Add or update a grouped set of model routing entries."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    name = request.path_params["name"]

    body = _parse_json_object(request)
    if isinstance(body, Response):
        return body

    provider = body.get("provider")
    if not provider:
        return JSONResponse({"error": "'provider' is required"}, status_code=400)

    models_body = body.get("models", {})
    if not isinstance(models_body, dict):
        return JSONResponse({"error": "'models' must be an object"}, status_code=400)

    try:
        data = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    providers = data.get("providers", {})
    if provider not in providers:
        return JSONResponse(
            {"error": f"Provider '{provider}' not found in config"}, status_code=400
        )

    model_groups = data.setdefault("model_groups", {})
    if not isinstance(model_groups, dict):
        return JSONResponse(
            {"error": "'model_groups' must be an object"}, status_code=400
        )

    rename_from = body.get("rename_from")
    resolve_name = rename_from or name
    rename_error = _handle_model_group_rename(model_groups, rename_from, name)
    if rename_error is not None:
        return rename_error

    try:
        cleaned_models = _clean_group_models(models_body)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    duplicate_error = _model_group_duplicate_response(
        data,
        model_groups,
        cleaned_models,
        exclude_group=resolve_name,
    )
    if duplicate_error is not None:
        return duplicate_error

    model_groups[name] = {"provider": provider, "models": cleaned_models}

    new_config, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error
    assert new_config is not None

    return JSONResponse(
        {
            "ok": True,
            "model_group": name,
            "provider": provider,
            "models": dict(new_config.models),
        }
    )


async def delete_model_group(request: Any, **kwargs: Any) -> Response:
    """Remove a model group and its grouped model mappings."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    name = request.path_params["name"]

    try:
        data = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    model_groups = data.get("model_groups", {})
    if not isinstance(model_groups, dict) or name not in model_groups:
        return JSONResponse(
            {"error": f"Model group '{name}' not found"}, status_code=404
        )

    del model_groups[name]

    new_config, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error
    assert new_config is not None

    return JSONResponse(
        {
            "ok": True,
            "deleted": name,
            "models": dict(new_config.models),
        }
    )


async def put_server_settings(request: Any) -> Response:
    """Update server settings (e.g. global proxy)."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    body = _parse_json_object(request)
    if isinstance(body, Response):
        return body

    try:
        data = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    server = data.setdefault("server", {})

    # Update proxy — empty string removes it
    if "proxy" in body:
        proxy = body["proxy"]
        if proxy:
            server["proxy"] = proxy
        else:
            server.pop("proxy", None)

    if "request_body_limit_mb" in body:
        server["request_body_limit_mb"] = body["request_body_limit_mb"]

    if "stream_trace" in body:
        stream_trace = body.get("stream_trace") or {}
        if not isinstance(stream_trace, dict):
            return JSONResponse(
                {"error": "'stream_trace' must be an object"}, status_code=400
            )

        try:
            max_string_chars = int(
                stream_trace.get("max_string_chars", DEFAULT_MAX_CHARS)
            )
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "'stream_trace.max_string_chars' must be an integer"},
                status_code=400,
            )
        if max_string_chars <= 0:
            max_string_chars = DEFAULT_MAX_CHARS

        next_trace = {
            "enabled": bool(stream_trace.get("enabled", False)),
            "filter": str(stream_trace.get("filter", "") or "").strip(),
            "path": str(stream_trace.get("path", "") or "").strip(),
            "max_string_chars": max_string_chars,
        }
        server["stream_trace"] = next_trace

    web_search_error = _apply_web_search_settings(server, body)
    if web_search_error:
        return JSONResponse({"error": web_search_error}, status_code=400)

    _, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error

    response_server = _mask_server_config(data.get("server", {}))
    return JSONResponse({"ok": True, "server": response_server})


async def reload_config(request: Any) -> Response:
    """Force hot-reload of the config from disk."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    try:
        new_config = _reload_gateway_config(request, config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Reload failed: {exc}"}, status_code=500)

    return JSONResponse(
        {
            "ok": True,
            "providers": list(new_config.providers.keys()),
            "models": dict(new_config.models),
        }
    )


def _format_connection_error(exc: Exception, url: str) -> str:
    """Return a user-friendly message for common upstream connection errors."""
    err_str = str(exc)
    if "Connection refused" in err_str or "Errno 111" in err_str:
        return (
            f"Connection refused at {url}. "
            "Check that the service is running and the port is correct. "
            "If running in Docker, ensure the host firewall (e.g. ufw) "
            "allows connections from the Docker bridge network."
        )
    if "timed out" in err_str.lower():
        return (
            f"Connection to {url} timed out. "
            "Check that the host/port is reachable from this container."
        )
    if "Name or service not known" in err_str or "getaddrinfo" in err_str:
        return f"Cannot resolve hostname in {url}. Check the Base URL."
    return f"Failed to connect to upstream: {err_str}"


async def fetch_upstream_models(request: Any, **kwargs: Any) -> Response:
    """Fetch the model list from an upstream provider's /v1/models endpoint."""
    from codex_rosetta.shims import get_shim

    provider_name = request.path_params["name"]
    config = _get_gateway_config(request)
    if config is None:
        return JSONResponse({"error": "Gateway config not loaded"}, status_code=500)

    if provider_name not in config.providers:
        return JSONResponse(
            {"error": f"Provider '{provider_name}' not found"}, status_code=404
        )

    pinfo = config.providers[provider_name]
    ptype = config.provider_types.get(provider_name, "unknown")

    # Build the models listing URL based on provider type
    if ptype == "google":
        models_url = f"{pinfo.base_url}/v1beta/models"
    elif ptype == "anthropic":
        models_url = f"{pinfo.base_url}/v1/models"
    else:
        # OpenAI-compatible (openai_chat, openai_responses, etc.)
        models_url = f"{pinfo.base_url}/models"

    headers = pinfo.auth_headers()

    try:
        async with AsyncClient(timeout=10.0, proxy=pinfo.proxy_url) as client:
            resp = await request_bounded_response(
                client,
                "GET",
                models_url,
                headers=headers,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Failed to fetch models from %s: %s", provider_name, exc)
        msg = _format_connection_error(exc, models_url)
        return JSONResponse({"error": msg})  # 200 so reverse proxies don't intercept

    if resp.status_code >= 400:
        logger.warning(
            "Upstream %s returned %d for model listing", provider_name, resp.status_code
        )
        return JSONResponse(
            {
                "error": (
                    f"Upstream returned HTTP {resp.status_code}. "
                    "This provider may not support model listing."
                ),
            },
        )

    try:
        body = resp.json()
    except Exception:
        return JSONResponse(
            {"error": "Upstream returned non-JSON response"},
        )

    # Resolve model_id_field from shim (e.g. Argo uses "internal_id")
    shim_name = config.provider_shim_names.get(provider_name)
    shim = get_shim(shim_name) if shim_name else None
    id_field = shim.model_id_field if shim and shim.model_id_field else None

    # Normalize response — different providers return different formats
    model_ids: list[str] = []
    if ptype == "google":
        # Google: {"models": [{"name": "models/gemini-...", ...}]}
        for m in body.get("models", []):
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[len("models/") :]
            model_ids.append(m.get(id_field, name) if id_field else name)
    else:
        # Anthropic & OpenAI-compatible: {"data": [{"id": "...", ...}]}
        for m in body.get("data", []):
            model_ids.append(
                m.get(id_field, m.get("id", "")) if id_field else m.get("id", "")
            )

    model_ids = [m for m in model_ids if m]
    model_ids.sort()

    return JSONResponse(
        {
            "provider": provider_name,
            "api_standard": ptype,
            "models": model_ids,
        }
    )


async def bulk_add_models(request: Any) -> Response:
    """Bulk-add multiple models for a given provider."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    body = _parse_json_object(request)
    if isinstance(body, Response):
        return body

    provider = body.get("provider")
    models_to_add: list[str] = body.get("models", [])
    prefix = body.get("prefix", "")
    capabilities_value = body.get("capabilities", ["text", "vision"])
    capabilities = (
        [str(capability) for capability in capabilities_value]
        if isinstance(capabilities_value, list) and capabilities_value
        else ["text", "vision"]
    )

    if not provider or not models_to_add:
        return JSONResponse(
            {"error": "'provider' and 'models' are required"}, status_code=400
        )

    try:
        data = load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    # Validate provider exists
    providers = data.get("providers", {})
    if provider not in providers:
        return JSONResponse(
            {"error": f"Provider '{provider}' not found"}, status_code=400
        )

    models_section = data.setdefault("models", {})
    grouped_model_names = _model_group_model_names(data.get("model_groups", {}))
    added: list[str] = []
    skipped: list[str] = []

    for model_id in models_to_add:
        display_name = f"{prefix}{model_id}" if prefix else model_id
        if display_name in models_section or display_name in grouped_model_names:
            skipped.append(display_name)
            continue
        entry: dict[str, Any] = {
            "provider": provider,
            "capabilities": capabilities,
        }
        # When a prefix is used, the gateway name differs from the
        # upstream model id — store the original as upstream_model so the
        # proxy handler can substitute it before forwarding.
        if prefix:
            entry["upstream_model"] = model_id
        models_section[display_name] = entry
        added.append(display_name)

    if not added:
        return JSONResponse(
            {
                "ok": True,
                "added": [],
                "skipped": skipped,
                "message": "All models already exist",
            }
        )

    new_config, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error
    assert new_config is not None

    return JSONResponse(
        {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "models": dict(new_config.models),
        }
    )
