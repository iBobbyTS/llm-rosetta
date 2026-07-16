"""Config CRUD and upstream model fetch route handlers."""

from __future__ import annotations

import asyncio
from typing import Any

from codex_rosetta._vendor.httpclient import AsyncClient
from codex_rosetta._vendor.httpserver import JSONResponse, Response
from codex_rosetta.shims import list_shims

from ...config import (
    GatewayConfig,
    default_tool_profile_for_provider,
    load_config_raw,
    normalize_codex_settings,
    normalize_local_mode_settings,
    normalize_web_search,
    provider_supports_tool_profiles,
)
from ...local_mode import config_toml_has_model_catalog
from ...model_presets import (
    detect_model_preset,
    model_presets_for_admin,
    normalize_model_info,
)
from ...providers import known_provider_types
from ...stream_trace import DEFAULT_MAX_CHARS
from ...tool_profiles import (
    normalize_tool_profile_input_overrides,
    normalize_tool_profile_documents,
    normalize_tool_profiles,
    tool_profile_contract,
    validate_tool_profile_reference,
)
from ...web_run_health import WebRunHealthState
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


def _mask_web_run_config(value: Any) -> dict[str, Any]:
    """Return a copy of server.web_run with its bearer token masked."""
    if not isinstance(value, dict):
        return {}
    masked = dict(value)
    if "token" in masked:
        masked["token"] = _mask_api_key(str(masked["token"]))
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
    if "web_run" in server:
        server["web_run"] = _mask_web_run_config(server["web_run"])
    return server


def _apply_local_mode_server_settings(
    server: dict[str, Any], body: dict[str, Any]
) -> Response | None:
    if "local_mode" not in body:
        return None
    local_mode = body["local_mode"]
    if not isinstance(local_mode, bool):
        return JSONResponse(
            {"error": "'local_mode' must be a boolean"}, status_code=400
        )
    if local_mode and not bool(server.get("local_mode_confirmed", False)):
        if body.get("local_mode_confirmed") is not True:
            return JSONResponse(
                {
                    "error": (
                        "Enabling local mode for the first time requires "
                        "explicit confirmation"
                    )
                },
                status_code=400,
            )
        server["local_mode_confirmed"] = True
    server["local_mode"] = local_mode
    return None


def _apply_web_search_settings(
    server: dict[str, Any], body: dict[str, Any]
) -> Response | None:
    """Merge one masked Admin edit into ``server.web_search``."""
    if "web_search" not in body:
        return None
    incoming = body.get("web_search")
    if not isinstance(incoming, dict):
        return JSONResponse(
            {"error": "'web_search' must be an object"}, status_code=400
        )
    unsupported = set(incoming) - {"provider", "tavily_api_key"}
    if unsupported:
        return JSONResponse(
            {"error": f"'web_search' has unsupported fields: {sorted(unsupported)}"},
            status_code=400,
        )
    current = server.get("web_search")
    current = dict(current) if isinstance(current, dict) else {}
    provider = incoming.get("provider", current.get("provider", "tavily"))
    next_value: dict[str, Any] = {"provider": provider}
    if "tavily_api_key" in incoming:
        api_key = incoming["tavily_api_key"]
        if not isinstance(api_key, str):
            return JSONResponse(
                {"error": "'web_search.tavily_api_key' must be a string"},
                status_code=400,
            )
        if "***" in api_key:
            existing_key = current.get("tavily_api_key")
            if isinstance(existing_key, str) and existing_key:
                next_value["tavily_api_key"] = existing_key
        elif api_key.strip():
            next_value["tavily_api_key"] = api_key.strip()
    else:
        existing_key = current.get("tavily_api_key")
        if isinstance(existing_key, str) and existing_key:
            next_value["tavily_api_key"] = existing_key
    try:
        normalize_web_search(next_value)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    server["web_search"] = next_value
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


def _normalize_model_entry(
    value: Any,
    *,
    model_name: str = "",
    group_provider: str | None = None,
) -> dict[str, Any]:
    """Return a model config entry in admin-UI dict form."""
    entry: dict[str, Any]
    if group_provider is not None:
        entry = {"provider": group_provider}
        if isinstance(value, str):
            if value:
                entry["upstream_model"] = value
            return entry
        if not isinstance(value, dict):
            return entry
        if value.get("upstream_model"):
            entry["upstream_model"] = value["upstream_model"]
        if isinstance(value.get("model_info"), dict):
            entry["model_info"] = value["model_info"]
    elif isinstance(value, str):
        entry = {"provider": value}
    elif isinstance(value, dict):
        entry = {"provider": value.get("provider", "")}
        if value.get("upstream_model"):
            entry["upstream_model"] = value["upstream_model"]
        if isinstance(value.get("model_info"), dict):
            entry["model_info"] = value["model_info"]
    else:
        return {}

    preset = detect_model_preset(
        model_name,
        entry.get("upstream_model"),
    )
    entry["input_modalities"] = (
        list(preset["input_modalities"]) if preset is not None else None
    )
    return entry


def _normalize_models_for_admin(
    raw_models: dict[str, Any],
) -> dict[str, Any]:
    """Normalize the expanded runtime model view for admin consumers."""
    normalized: dict[str, Any] = {}
    for name, value in raw_models.items():
        entry = _normalize_model_entry(value, model_name=name)
        if not entry:
            continue
        normalized[name] = entry
    return normalized


def _normalize_model_groups_for_admin(
    raw_model_groups: dict[str, Any],
    raw_providers: dict[str, Any],
) -> dict[str, Any]:
    """Normalize model group config for admin UI consumption."""
    groups: dict[str, Any] = {}
    if not isinstance(raw_model_groups, dict):
        return groups

    for group_name, group_value in raw_model_groups.items():
        if not isinstance(group_value, dict):
            continue
        provider = group_value.get("provider", "")
        group_type = group_value.get("type", "")
        raw_group_models = group_value.get("models", {})
        models: dict[str, Any] = {}
        if isinstance(raw_group_models, dict):
            for model_name, model_value in raw_group_models.items():
                entry = _normalize_model_entry(
                    model_value,
                    model_name=model_name,
                    group_provider=provider,
                )
                entry.pop("provider", None)
                models[model_name] = entry
        normalized_group = {
            "provider": provider,
            "type": group_type,
            "models": models,
        }
        if group_type == "llm" and provider_supports_tool_profiles(
            raw_providers.get(provider)
        ):
            normalized_group["tool_profile"] = group_value.get(
                "tool_profile",
                default_tool_profile_for_provider(raw_providers.get(provider)),
            )
        groups[group_name] = normalized_group
    return groups


def _clean_group_model_entry(value: Any) -> dict[str, Any]:
    """Normalize one model entry inside a model group request."""
    if isinstance(value, str):
        return {"upstream_model": value} if value else {}
    if not isinstance(value, dict):
        raise ValueError("model entries must be objects or strings")

    entry: dict[str, Any] = {}
    upstream_model = str(value.get("upstream_model") or "").strip()
    if upstream_model:
        entry["upstream_model"] = upstream_model

    model_info = value.get("model_info")
    if model_info is not None:
        entry["model_info"] = normalize_model_info(model_info, field="LLM model_info")

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
    model_groups: dict[str, Any],
    cleaned_models: dict[str, Any],
    *,
    exclude_group: str | None,
) -> Response | None:
    """Return a conflict response when grouped models duplicate other routes."""
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

    # ``models`` is an effective read-only runtime view. ``model_groups`` is
    # the sole persisted management view.
    raw_model_groups = raw.get("model_groups", {}) or {}
    expanded_raw_models = GatewayConfig._expand_model_groups(raw_model_groups)
    models_normalized = _normalize_models_for_admin(expanded_raw_models)
    model_groups = _normalize_model_groups_for_admin(raw_model_groups, providers)
    tool_profiles = normalize_tool_profile_documents(raw.get("tool_profiles"))
    tool_profile_input_overrides = normalize_tool_profile_input_overrides(
        raw.get("tool_profile_input_overrides")
    )

    config: GatewayConfig = request.app.gateway_config
    server = _mask_server_config(raw.get("server", {}))
    server.setdefault("request_body_limit_mb", config.request_body_limit_config_value)
    server.setdefault("local_mode", config.local_mode)
    server.setdefault("local_mode_confirmed", config.local_mode_confirmed)
    codex_home = getattr(request.app, "codex_home", "")
    return JSONResponse(
        {
            "config_path": config_path,
            "codex_home": codex_home,
            "model_catalog_configured": bool(
                codex_home and config_toml_has_model_catalog(codex_home)
            ),
            "providers": masked_providers,
            "models": models_normalized,
            "model_groups": model_groups,
            "tool_profiles": tool_profiles,
            "tool_profile_input_overrides": tool_profile_input_overrides,
            "tool_profile_presets": [
                {"id": profile["id"], "name": profile["name"]}
                for profile in tool_profile_contract()["profiles"]
            ],
            "model_presets": model_presets_for_admin(),
            "codex": config.codex,
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

    # Check if any model group still references this provider.
    raw_model_groups = data.get("model_groups", {})
    model_groups = raw_model_groups if isinstance(raw_model_groups, dict) else {}
    referencing_groups = [
        group_name
        for group_name, group in model_groups.items()
        if isinstance(group, dict) and group.get("provider") == name
    ]

    from ._shared import _qp

    cascade = _qp(request, "cascade") in ("true", "1")
    if referencing_groups and not cascade:
        return JSONResponse(
            {
                "error": (
                    f"Cannot delete provider '{name}': referenced by model groups: "
                    f"{referencing_groups}"
                )
            },
            status_code=409,
        )

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

    group_type = body.get("type")
    if group_type != "llm":
        return JSONResponse({"error": "'type' must be 'llm'"}, status_code=400)

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
        model_groups,
        cleaned_models,
        exclude_group=resolve_name,
    )
    if duplicate_error is not None:
        return duplicate_error

    provider_config = (data.get("providers", {}) or {}).get(provider)
    tool_profile: str | None = None
    if provider_supports_tool_profiles(provider_config):
        tool_profiles = normalize_tool_profiles(data.get("tool_profiles"))
        try:
            tool_profile = validate_tool_profile_reference(
                body.get(
                    "tool_profile", default_tool_profile_for_provider(provider_config)
                ),
                tool_profiles,
                field=f"model group '{name}' tool_profile",
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    model_groups[name] = {
        "provider": provider,
        "type": group_type,
        **({"tool_profile": tool_profile} if tool_profile is not None else {}),
        "models": cleaned_models,
    }

    new_config, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error
    assert new_config is not None

    return JSONResponse(
        {
            "ok": True,
            "model_group": name,
            "provider": provider,
            "type": group_type,
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

    local_mode_error = _apply_local_mode_server_settings(server, body)
    if local_mode_error is not None:
        return local_mode_error

    web_search_error = _apply_web_search_settings(server, body)
    if web_search_error is not None:
        return web_search_error

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
        except TypeError, ValueError:
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

    _, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error

    response_server = _mask_server_config(data.get("server", {}))
    return JSONResponse({"ok": True, "server": response_server})


async def put_codex_settings(request: Any) -> Response:
    """Persist Codex task-model overrides and synchronize local-mode files."""
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path available"}, status_code=500)

    body = _parse_json_object(request)
    if isinstance(body, Response):
        return body
    try:
        normalized = normalize_codex_settings(body)
        data = load_config_raw(config_path)
        local_mode, confirmed = normalize_local_mode_settings(data.get("server"))
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)

    if not local_mode or not confirmed:
        return JSONResponse(
            {"error": "Codex task models require confirmed local mode"},
            status_code=409,
        )
    if normalized:
        data["codex"] = normalized
    else:
        data.pop("codex", None)

    new_config, commit_error = _commit_gateway_config(request, config_path, data)
    if commit_error is not None:
        return commit_error
    assert new_config is not None
    return JSONResponse({"ok": True, "codex": new_config.codex})


async def get_network_search_status(request: Any) -> Response:
    """Return bounded, credential-free Docker sidecar health state."""
    config = _get_gateway_config(request)
    health_state = getattr(request.app, "web_run_health_state", None)
    if health_state is None:
        health_state = WebRunHealthState()
        request.app.web_run_health_state = health_state
    status = await health_state.status(
        config.web_run_sidecar_url if config is not None else None
    )
    return JSONResponse(status.as_dict())


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
