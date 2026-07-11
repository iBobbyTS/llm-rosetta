"""Admin tool catalog and profile routes."""

from __future__ import annotations

from typing import Any

from codex_rosetta._vendor.httpserver import JSONResponse, Response

from ...config import load_config_raw
from ...tool_profiles import (
    BUILTIN_TOOL_PROFILE,
    normalize_tool_profile_tools,
    normalize_tool_profiles,
    tool_profile_contract,
    tool_profiles_for_admin,
    validate_tool_profile_name,
)
from ..tool_catalog import load_tool_catalog
from ._shared import (
    _commit_gateway_config,
    _get_config_path,
    _parse_json_object,
)


async def get_tool_catalog(request: Any) -> Response:
    """Return the immutable tools catalog bundled with the package."""
    return JSONResponse(load_tool_catalog())


def _load_profile_config(request: Any) -> tuple[str, dict[str, Any]] | Response:
    config_path = _get_config_path(request)
    if not config_path:
        return JSONResponse({"error": "No config file path"}, status_code=500)
    try:
        return config_path, load_config_raw(config_path)
    except Exception as exc:
        return JSONResponse({"error": f"Failed to read config: {exc}"}, status_code=500)


async def get_tool_profiles(request: Any) -> Response:
    """Return the built-in and persisted user tool profiles."""
    loaded = _load_profile_config(request)
    if isinstance(loaded, Response):
        return loaded
    _config_path, data = loaded
    try:
        profiles = normalize_tool_profiles(data.get("tool_profiles"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    references: dict[str, list[str]] = {}
    model_groups = data.get("model_groups", {})
    if isinstance(model_groups, dict):
        for group_name, group in model_groups.items():
            if not isinstance(group, dict) or group.get("type") != "llm":
                continue
            profile_name = group.get("tool_profile", BUILTIN_TOOL_PROFILE)
            references.setdefault(profile_name, []).append(group_name)

    return JSONResponse(
        {
            "profiles": tool_profiles_for_admin(profiles),
            "supported_states": {
                item_id: list(states)
                for item_id, states in tool_profile_contract()["supported"].items()
            },
            "references": references,
        }
    )


async def put_tool_profile(request: Any, **kwargs: Any) -> Response:
    """Create or replace one complete user tool profile."""
    try:
        name = validate_tool_profile_name(
            kwargs.get("name") or request.path_params.get("name")
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    body = _parse_json_object(request)
    if isinstance(body, Response):
        return body
    try:
        tools = normalize_tool_profile_tools(
            body.get("tools"), field=f"tool profile '{name}'"
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    loaded = _load_profile_config(request)
    if isinstance(loaded, Response):
        return loaded
    config_path, data = loaded
    profiles = data.setdefault("tool_profiles", {})
    if not isinstance(profiles, dict):
        return JSONResponse(
            {"error": "'tool_profiles' must be an object"}, status_code=400
        )
    profiles[name] = {"tools": tools}
    _config, error = _commit_gateway_config(request, config_path, data)
    if error is not None:
        return error
    return JSONResponse({"ok": True, "profile": name})


async def delete_tool_profile(request: Any, **kwargs: Any) -> Response:
    """Delete an unreferenced user profile."""
    try:
        name = validate_tool_profile_name(
            kwargs.get("name") or request.path_params.get("name")
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    loaded = _load_profile_config(request)
    if isinstance(loaded, Response):
        return loaded
    config_path, data = loaded
    profiles = data.get("tool_profiles", {})
    if not isinstance(profiles, dict) or name not in profiles:
        return JSONResponse({"error": f"Tool profile '{name}' not found"}, 404)

    references = sorted(
        group_name
        for group_name, group in (data.get("model_groups", {}) or {}).items()
        if isinstance(group, dict) and group.get("tool_profile") == name
    )
    if references:
        return JSONResponse(
            {
                "error": f"Tool profile '{name}' is used by model groups",
                "model_groups": references,
            },
            status_code=409,
        )

    del profiles[name]
    _config, error = _commit_gateway_config(request, config_path, data)
    if error is not None:
        return error
    return JSONResponse({"ok": True, "profile": name})
