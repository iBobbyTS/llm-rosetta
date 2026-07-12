"""Tool-profile contracts derived from the bundled Codex tool catalog."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .admin.tool_catalog import load_tool_catalog

BUILTIN_TOOL_PROFILE = "builtin"
RESPONSES_PASS_THROUGH_TOOL_PROFILE = "responses_pass_through"
MAX_TOOL_PROFILE_NAME_LENGTH = 128
MAX_TOOL_PROFILE_INPUT_LENGTH = 16_384


def _normalize_profile_input_definition(
    item_id: str,
    value: Any,
    existing_ids: set[str],
) -> tuple[str, dict[str, Any]]:
    """Validate one catalog-declared Function-card input definition."""
    if not isinstance(value, dict):
        raise ValueError(f"catalog item {item_id!r} profile input must be an object")
    unsupported = set(value) - {
        "id",
        "label_i18n",
        "default",
        "type",
        "placeholder_i18n",
    }
    if unsupported:
        raise ValueError(
            f"catalog item {item_id!r} profile input has unsupported fields: "
            f"{sorted(unsupported)}"
        )
    input_id = value.get("id")
    if not isinstance(input_id, str) or not input_id:
        raise ValueError(
            f"catalog item {item_id!r} profile input id must be a non-empty string"
        )
    if input_id in existing_ids:
        raise ValueError(
            f"catalog item {item_id!r} has duplicate profile input {input_id!r}"
        )
    label_i18n = value.get("label_i18n")
    if not isinstance(label_i18n, str) or not label_i18n:
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "label_i18n must be a non-empty string"
        )
    default = value.get("default", "")
    if not isinstance(default, str):
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "default must be a string"
        )
    if len(default) > MAX_TOOL_PROFILE_INPUT_LENGTH:
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            f"default exceeds {MAX_TOOL_PROFILE_INPUT_LENGTH} characters"
        )
    input_type = value.get("type", "text")
    if input_type not in {"text", "password"}:
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "type must be 'text' or 'password'"
        )
    placeholder_i18n = value.get("placeholder_i18n")
    if placeholder_i18n is not None and (
        not isinstance(placeholder_i18n, str) or not placeholder_i18n
    ):
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "placeholder_i18n must be a non-empty string"
        )
    return input_id, dict(value, default=default, type=input_type)


def _profile_input_contract(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return validated Function-card input definitions keyed by tool and input ID."""
    definitions: dict[str, dict[str, Any]] = {}
    for item in catalog["items"]:
        raw_inputs = item.get("profile_inputs", [])
        if not isinstance(raw_inputs, list):
            raise ValueError(
                f"catalog item {item['id']!r} profile_inputs must be a list"
            )
        if raw_inputs and item["type"] != "function":
            raise ValueError(
                f"catalog item {item['id']!r} profile_inputs are only supported "
                "for Function tools"
            )
        item_inputs: dict[str, Any] = {}
        for raw_input in raw_inputs:
            input_id, input_definition = _normalize_profile_input_definition(
                item["id"], raw_input, set(item_inputs)
            )
            item_inputs[input_id] = input_definition
        if item_inputs:
            definitions[item["id"]] = item_inputs
    return definitions


@lru_cache(maxsize=1)
def tool_profile_contract() -> dict[str, Any]:
    """Return supported states and the immutable bundled profiles."""
    catalog = load_tool_catalog()
    input_definitions = _profile_input_contract(catalog)
    policies = {policy["id"]: policy for policy in catalog["policies"]}
    supported: dict[str, tuple[str, ...]] = {}
    builtin: dict[str, str] = {}

    for item in catalog["items"]:
        item_id = item["id"]
        item_type = item["type"]
        if item_type == "custom_injection":
            supported[item_id] = ("disabled", "injected")
            builtin[item_id] = "injected"
            continue

        policy = policies[item["policy_id"]]
        if item_type == "namespace":
            supported[item_id] = tuple(
                policy.get("namespace_supported", ("disabled", "expanded"))
            )
            builtin[item_id] = (
                "disabled" if policy["default"] == "disabled" else "expanded"
            )
            continue

        states = tuple(policy["supported"])
        supported[item_id] = states
        builtin[item_id] = policy["default"]

    profiles = [
        {
            **dict(catalog["builtin_profile"]),
            "tools": dict(builtin),
            "inputs": {
                item_id: {
                    input_id: definition["default"]
                    for input_id, definition in item_inputs.items()
                }
                for item_id, item_inputs in input_definitions.items()
            },
        }
    ]
    for preset in catalog.get("preset_profiles", []):
        defaults = preset.get("defaults", {})
        overrides = preset.get("tools", {})
        tools: dict[str, str] = {}
        for item in catalog["items"]:
            item_id = item["id"]
            state = overrides.get(item_id, defaults.get(item["type"]))
            if state not in supported[item_id]:
                raise ValueError(
                    f"bundled profile {preset.get('id')!r} has unsupported state "
                    f"{state!r} for {item_id}"
                )
            tools[item_id] = state
        profiles.append(
            {
                "id": preset["id"],
                "name": preset["name"],
                "tools": tools,
                "inputs": {
                    item_id: {
                        input_id: definition["default"]
                        for input_id, definition in item_inputs.items()
                    }
                    for item_id, item_inputs in input_definitions.items()
                },
            }
        )

    return {
        "profiles": profiles,
        "supported": supported,
        "builtin": builtin,
        "input_definitions": input_definitions,
        "readonly": {profile["id"]: profile for profile in profiles},
    }


def validate_tool_profile_name(value: Any, *, allow_readonly: bool = False) -> str:
    """Validate and return one persisted profile identifier."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("tool profile name must be a non-empty string")
    name = value.strip()
    if len(name) > MAX_TOOL_PROFILE_NAME_LENGTH:
        raise ValueError(
            f"tool profile name must be at most {MAX_TOOL_PROFILE_NAME_LENGTH} characters"
        )
    if name in tool_profile_contract()["readonly"] and not allow_readonly:
        raise ValueError(f"the bundled tool profile '{name}' is read-only")
    return name


def normalize_tool_profile_tools(value: Any, *, field: str) -> dict[str, str]:
    """Validate a complete tool-state mapping against the bundled catalog."""
    if not isinstance(value, dict):
        raise ValueError(f"{field}.tools must be an object")

    contract = tool_profile_contract()
    supported: dict[str, tuple[str, ...]] = contract["supported"]
    actual_ids = set(value)
    expected_ids = set(supported)
    missing = sorted(expected_ids - actual_ids)
    unknown = sorted(actual_ids - expected_ids)
    if missing:
        raise ValueError(f"{field}.tools is missing catalog IDs: {missing}")
    if unknown:
        raise ValueError(f"{field}.tools contains unknown catalog IDs: {unknown}")

    normalized: dict[str, str] = {}
    for item_id, state in value.items():
        if not isinstance(state, str) or state not in supported[item_id]:
            raise ValueError(
                f"{field}.tools.{item_id} must be one of {list(supported[item_id])}"
            )
        normalized[item_id] = state
    return normalized


def normalize_tool_profile_inputs(
    value: Any, *, field: str
) -> dict[str, dict[str, str]]:
    """Validate and fill Function-card input values from the bundled defaults."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"{field}.inputs must be an object")

    definitions: dict[str, dict[str, Any]] = tool_profile_contract()[
        "input_definitions"
    ]
    unknown_items = sorted(set(value) - set(definitions))
    if unknown_items:
        raise ValueError(
            f"{field}.inputs contains unknown catalog IDs: {unknown_items}"
        )

    normalized: dict[str, dict[str, str]] = {}
    for item_id, item_definitions in definitions.items():
        raw_item_values = value.get(item_id, {})
        if not isinstance(raw_item_values, dict):
            raise ValueError(f"{field}.inputs.{item_id} must be an object")
        unknown_inputs = sorted(set(raw_item_values) - set(item_definitions))
        if unknown_inputs:
            raise ValueError(
                f"{field}.inputs.{item_id} contains unknown input IDs: {unknown_inputs}"
            )
        normalized[item_id] = {}
        for input_id, definition in item_definitions.items():
            input_value = raw_item_values.get(input_id, definition["default"])
            if not isinstance(input_value, str):
                raise ValueError(
                    f"{field}.inputs.{item_id}.{input_id} must be a string"
                )
            if len(input_value) > MAX_TOOL_PROFILE_INPUT_LENGTH:
                raise ValueError(
                    f"{field}.inputs.{item_id}.{input_id} must be at most "
                    f"{MAX_TOOL_PROFILE_INPUT_LENGTH} characters"
                )
            normalized[item_id][input_id] = input_value
    return normalized


def normalize_tool_profile_documents(
    value: Any,
) -> dict[str, dict[str, Any]]:
    """Validate complete persisted Profile documents for Admin editing."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("config: 'tool_profiles' must be an object")

    profiles: dict[str, dict[str, Any]] = {}
    for raw_name, raw_profile in value.items():
        name = validate_tool_profile_name(raw_name)
        if not isinstance(raw_profile, dict):
            raise ValueError(f"config: tool_profiles.{name} must be an object")
        unsupported_fields = set(raw_profile) - {"tools", "inputs"}
        if unsupported_fields:
            raise ValueError(
                f"config: tool_profiles.{name} has unsupported fields: "
                f"{sorted(unsupported_fields)}"
            )
        field = f"config: tool_profiles.{name}"
        profiles[name] = {
            "tools": normalize_tool_profile_tools(
                raw_profile.get("tools"), field=field
            ),
            "inputs": normalize_tool_profile_inputs(
                raw_profile.get("inputs"), field=field
            ),
        }
    return profiles


def normalize_tool_profiles(value: Any) -> dict[str, dict[str, str]]:
    """Validate user-defined profiles from the top-level config object."""
    return {
        name: profile["tools"]
        for name, profile in normalize_tool_profile_documents(value).items()
    }


def resolve_tool_profile(
    name: str,
    profiles: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Resolve a built-in or user profile to an independent state mapping."""
    readonly = tool_profile_contract()["readonly"]
    if name in readonly:
        return dict(readonly[name]["tools"])
    try:
        return dict(profiles[name])
    except KeyError as exc:
        raise ValueError(f"unknown tool profile '{name}'") from exc


def resolve_tool_profile_inputs(
    name: str,
    profiles: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Resolve persisted Function-card input values for one Profile."""
    readonly = tool_profile_contract()["readonly"]
    profile = readonly.get(name, profiles.get(name))
    if profile is None:
        raise ValueError(f"unknown tool profile '{name}'")
    return {
        item_id: dict(values) for item_id, values in profile.get("inputs", {}).items()
    }


def validate_tool_profile_reference(
    value: Any,
    profiles: dict[str, dict[str, str]],
    *,
    field: str,
) -> str:
    """Validate a model-group profile reference."""
    name = validate_tool_profile_name(value, allow_readonly=True)
    if name not in tool_profile_contract()["readonly"] and name not in profiles:
        raise ValueError(f"{field} references unknown tool profile '{name}'")
    return name


def tool_profiles_for_admin(
    profiles: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the ordered public representation consumed by the Admin UI."""
    contract = tool_profile_contract()
    result = [
        {
            "id": profile["id"],
            "name": profile["name"],
            "tools": dict(profile["tools"]),
            "inputs": {
                item_id: dict(values) for item_id, values in profile["inputs"].items()
            },
            "readonly": True,
        }
        for profile in contract["profiles"]
    ]
    result.extend(
        {
            "id": name,
            "name": name,
            "tools": dict(profile["tools"]),
            "inputs": {
                item_id: dict(values) for item_id, values in profile["inputs"].items()
            },
            "readonly": False,
        }
        for name, profile in sorted(profiles.items())
    )
    return result


@lru_cache(maxsize=1)
def tool_catalog_lookups() -> dict[str, Any]:
    """Return catalog indexes used by request-time policy application."""
    catalog = load_tool_catalog()
    items = {item["id"]: item for item in catalog["items"]}
    by_type_name = {
        (item["type"], item["name"]): item["id"] for item in catalog["items"]
    }
    namespace_children: dict[tuple[str, str], str] = {}
    for placement in catalog["placements"]["namespaces"]:
        namespace = items[placement["namespace_id"]]["name"]
        for child_id in placement["child_ids"]:
            namespace_children[(namespace, items[child_id]["name"])] = child_id
    return {
        "items": items,
        "by_type_name": by_type_name,
        "namespace_children": namespace_children,
    }


def route_tool_state(route: Any, item_id: str, default: str = "passthrough") -> str:
    """Return one effective state, retaining fixed behavior for bare test routes."""
    profile = getattr(route, "tool_profile", None)
    if not profile:
        return default
    return profile.get(item_id, default)


def modified_tool_names(route: Any) -> set[str] | None:
    """Return catalog names whose Chat definitions may be modified."""
    if not getattr(route, "tool_profile", None):
        return None
    return {
        item["name"]
        for item_id, item in tool_catalog_lookups()["items"].items()
        if item["type"] not in {"namespace", "custom_injection"}
        and route_tool_state(route, item_id) == "modified"
    }
