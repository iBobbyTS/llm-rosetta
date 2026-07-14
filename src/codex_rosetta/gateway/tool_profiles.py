"""Tool-profile contracts derived from the bundled Codex tool catalog."""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any, cast

from .admin.tool_catalog import load_tool_catalog

BUILTIN_TOOL_PROFILE = "builtin"
MAX_TOOL_PROFILE_NAME_LENGTH = 128
MAX_TOOL_PROFILE_INPUT_LENGTH = 16_384


def _normalize_visible_when(
    item_id: str,
    value: Any,
    supported_states: tuple[str, ...],
    *,
    field: str,
) -> list[str] | None:
    """Validate an optional state-based card visibility condition."""
    if value is None:
        return None
    if not isinstance(value, list) or any(
        not isinstance(state, str) for state in value
    ):
        raise ValueError(f"catalog item {item_id!r} {field} must be a list of strings")
    if len(value) != len(set(value)):
        raise ValueError(f"catalog item {item_id!r} {field} contains duplicate states")
    unsupported = sorted(set(value) - set(supported_states))
    if unsupported:
        raise ValueError(
            f"catalog item {item_id!r} {field} contains unsupported states: "
            f"{unsupported}"
        )
    return list(cast(list[str], value))


def _normalize_profile_select_options(
    item_id: str,
    input_id: str,
    value: Any,
    default: str,
) -> list[dict[str, str]]:
    """Validate one catalog-declared select option list."""
    if not isinstance(value, list) or not value:
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "select options must be a non-empty list"
        )
    normalized: list[dict[str, str]] = []
    option_values: set[str] = set()
    for option in value:
        if not isinstance(option, dict) or set(option) != {"value", "label"}:
            raise ValueError(
                f"catalog item {item_id!r} profile input {input_id!r} "
                "select options must contain exactly 'value' and 'label'"
            )
        option_value = option["value"]
        option_label = option["label"]
        if not isinstance(option_value, str) or not isinstance(option_label, str):
            raise ValueError(
                f"catalog item {item_id!r} profile input {input_id!r} "
                "select option value and label must be strings"
            )
        if not option_label:
            raise ValueError(
                f"catalog item {item_id!r} profile input {input_id!r} "
                "select option label must be non-empty"
            )
        if len(option_value) > MAX_TOOL_PROFILE_INPUT_LENGTH:
            raise ValueError(
                f"catalog item {item_id!r} profile input {input_id!r} "
                f"select option value exceeds {MAX_TOOL_PROFILE_INPUT_LENGTH} characters"
            )
        if option_value in option_values:
            raise ValueError(
                f"catalog item {item_id!r} profile input {input_id!r} "
                f"has duplicate select option value {option_value!r}"
            )
        option_values.add(option_value)
        normalized.append({"value": option_value, "label": option_label})
    if default not in option_values:
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "select default must match an option value"
        )
    return normalized


def _normalize_profile_input_definition(
    item_id: str,
    value: Any,
    existing_ids: set[str],
    supported_states: tuple[str, ...],
) -> tuple[str, dict[str, Any]]:
    """Validate one catalog-declared tool-card input definition."""
    if not isinstance(value, dict):
        raise ValueError(f"catalog item {item_id!r} profile input must be an object")
    unsupported = set(value) - {
        "id",
        "label_i18n",
        "default",
        "type",
        "placeholder_i18n",
        "options",
        "visible_when",
        "ui_hidden",
        "readonly",
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
    if input_type not in {"text", "password", "select", "textarea"}:
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "type must be 'text', 'password', 'select', or 'textarea'"
        )
    options = value.get("options")
    if input_type == "select":
        value = dict(
            value,
            options=_normalize_profile_select_options(
                item_id, input_id, options, default
            ),
        )
    elif options is not None:
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "options are only supported for type 'select'"
        )
    placeholder_i18n = value.get("placeholder_i18n")
    if placeholder_i18n is not None and (
        not isinstance(placeholder_i18n, str) or not placeholder_i18n
    ):
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "placeholder_i18n must be a non-empty string"
        )
    visible_when = _normalize_visible_when(
        item_id,
        value.get("visible_when"),
        supported_states,
        field=f"profile input {input_id!r} visible_when",
    )
    if visible_when is not None:
        value = dict(value, visible_when=visible_when)
    ui_hidden = value.get("ui_hidden", False)
    if not isinstance(ui_hidden, bool):
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "ui_hidden must be boolean"
        )
    if ui_hidden:
        value = dict(value, ui_hidden=True)
    value = _normalize_profile_input_readonly(item_id, input_id, value, input_type)
    return input_id, dict(value, default=default, type=input_type)


def _normalize_profile_input_readonly(
    item_id: str,
    input_id: str,
    value: dict[str, Any],
    input_type: str,
) -> dict[str, Any]:
    """Validate read-only presentation for a catalog textarea input."""
    readonly = value.get("readonly", False)
    if not isinstance(readonly, bool):
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "readonly must be boolean"
        )
    if readonly and input_type != "textarea":
        raise ValueError(
            f"catalog item {item_id!r} profile input {input_id!r} "
            "readonly is only supported for type 'textarea'"
        )
    if readonly:
        return dict(value, readonly=True)
    return value


def _profile_input_contract(
    catalog: dict[str, Any],
    supported: dict[str, tuple[str, ...]],
) -> dict[str, dict[str, Any]]:
    """Return validated tool-card input definitions keyed by tool and input ID."""
    definitions: dict[str, dict[str, Any]] = {}
    for item in catalog["items"]:
        raw_inputs = item.get("profile_inputs", [])
        if not isinstance(raw_inputs, list):
            raise ValueError(
                f"catalog item {item['id']!r} profile_inputs must be a list"
            )
        if raw_inputs and item["type"] not in {
            "function",
            "custom",
            "hosted",
            "namespace",
        }:
            raise ValueError(
                f"catalog item {item['id']!r} profile_inputs are only supported "
                "for Function, Custom, Hosted, and Namespace tools"
            )
        item_inputs: dict[str, Any] = {}
        for raw_input in raw_inputs:
            input_id, input_definition = _normalize_profile_input_definition(
                item["id"], raw_input, set(item_inputs), supported[item["id"]]
            )
            item_inputs[input_id] = input_definition
        if item_inputs:
            definitions[item["id"]] = item_inputs
    return definitions


def _profile_mutation_contract(
    catalog: dict[str, Any],
    input_definitions: dict[str, dict[str, Any]],
    supported: dict[str, tuple[str, ...]],
) -> dict[str, tuple[dict[str, str], ...]]:
    """Validate declarative Profile-owned model-description mutations."""
    mutations: dict[str, tuple[dict[str, str], ...]] = {}
    for item in catalog["items"]:
        item_id = item["id"]
        raw_mutations = item.get("profile_mutations", [])
        if not isinstance(raw_mutations, list):
            raise ValueError(
                f"catalog item {item_id!r} profile_mutations must be a list"
            )
        mutation_states = {"modified"}
        if item["type"] == "namespace":
            mutation_states.add("expanded")
        if raw_mutations and not mutation_states.intersection(supported[item_id]):
            raise ValueError(
                f"catalog item {item_id!r} profile_mutations requires Modified "
                "or Expanded support"
            )
        normalized = [
            _normalize_profile_mutation(
                item_id, raw_mutation, input_definitions.get(item_id, {})
            )
            for raw_mutation in raw_mutations
        ]
        if normalized:
            mutations[item_id] = tuple(normalized)
    return mutations


def _normalize_profile_mutation(
    item_id: str,
    raw_mutation: Any,
    input_definitions: dict[str, Any],
) -> dict[str, str]:
    """Validate one Profile-owned model-description mutation."""
    if not isinstance(raw_mutation, dict):
        raise ValueError(f"catalog item {item_id!r} profile mutation must be an object")
    unsupported = set(raw_mutation) - {"target", "input_id", "parameter"}
    if unsupported:
        raise ValueError(
            f"catalog item {item_id!r} profile mutation has unsupported "
            f"fields: {sorted(unsupported)}"
        )
    target = raw_mutation.get("target")
    input_id = raw_mutation.get("input_id")
    parameter = raw_mutation.get("parameter")
    if target not in {"description", "parameter_description"}:
        raise ValueError(
            f"catalog item {item_id!r} profile mutation has unsupported target"
        )
    if not isinstance(input_id, str) or input_id not in input_definitions:
        raise ValueError(
            f"catalog item {item_id!r} profile mutation references unknown input"
        )
    normalized = {"target": target, "input_id": input_id}
    if target == "parameter_description":
        if not isinstance(parameter, str) or not parameter:
            raise ValueError(
                f"catalog item {item_id!r} parameter mutation needs parameter"
            )
        normalized["parameter"] = parameter
    elif parameter is not None:
        raise ValueError(
            f"catalog item {item_id!r} description mutation cannot set parameter"
        )
    return normalized


def _exec_projection_internal_when_disabled(raw: dict[str, Any], item_id: str) -> bool:
    """Validate the internal-only projection flag for one catalog item."""
    value = raw.get("internal_when_disabled", False)
    if not isinstance(value, bool):
        raise ValueError(
            f"catalog item {item_id!r} exec_projection "
            "internal_when_disabled must be boolean"
        )
    return value


def _exec_projection_contract(
    catalog: dict[str, Any],
    supported: dict[str, tuple[str, ...]],
) -> dict[str, dict[str, Any]]:
    """Validate Profile-owned Code Mode exec projection declarations."""
    projections: dict[str, dict[str, Any]] = {}
    chat_names: set[str] = set()
    for item in catalog["items"]:
        item_id = item["id"]
        raw = item.get("exec_projection")
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise ValueError(
                f"catalog item {item_id!r} exec_projection must be an object"
            )
        unsupported = set(raw) - {
            "chat_name",
            "nested_name",
            "input_mode",
            "input_field",
            "output_mode",
            "internal_when_disabled",
        }
        if unsupported:
            raise ValueError(
                f"catalog item {item_id!r} exec_projection has unsupported fields: "
                f"{sorted(unsupported)}"
            )
        if "modified" not in supported[item_id]:
            raise ValueError(
                f"catalog item {item_id!r} exec_projection requires Modified support"
            )
        chat_name = raw.get("chat_name")
        nested_name = raw.get("nested_name")
        if not isinstance(chat_name, str) or not chat_name:
            raise ValueError(
                f"catalog item {item_id!r} exec_projection chat_name must be non-empty"
            )
        if chat_name in chat_names:
            raise ValueError(f"duplicate exec projection Chat name {chat_name!r}")
        if not isinstance(nested_name, str) or not nested_name:
            raise ValueError(
                f"catalog item {item_id!r} exec_projection nested_name must be non-empty"
            )
        input_mode = raw.get("input_mode", "args")
        if input_mode not in {"args", "freeform"}:
            raise ValueError(
                f"catalog item {item_id!r} exec_projection input_mode must be "
                "'args' or 'freeform'"
            )
        input_field = raw.get("input_field", "input")
        if not isinstance(input_field, str) or not input_field:
            raise ValueError(
                f"catalog item {item_id!r} exec_projection input_field must be non-empty"
            )
        output_mode = raw.get("output_mode", "text")
        if output_mode not in {"text", "image", "generated_image"}:
            raise ValueError(
                f"catalog item {item_id!r} exec_projection output_mode must be "
                "'text', 'image', or 'generated_image'"
            )
        internal_when_disabled = _exec_projection_internal_when_disabled(raw, item_id)
        chat_names.add(chat_name)
        projections[item_id] = {
            "chat_name": chat_name,
            "nested_name": nested_name,
            "input_mode": input_mode,
            "input_field": input_field,
            "output_mode": output_mode,
            "internal_when_disabled": internal_when_disabled,
        }
    return projections


def _normalize_profile_input_values(
    value: Any,
    definitions: dict[str, dict[str, Any]],
    *,
    field: str,
) -> dict[str, dict[str, str]]:
    """Validate values against catalog input definitions and fill defaults."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"{field}.inputs must be an object")

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
            if definition["type"] == "select" and input_value not in {
                option["value"] for option in definition["options"]
            }:
                raise ValueError(
                    f"{field}.inputs.{item_id}.{input_id} must be one of "
                    f"{[option['value'] for option in definition['options']]}"
                )
            normalized[item_id][input_id] = input_value
    return normalized


def _disable_namespace_children(
    tools: dict[str, str], namespace_children: dict[str, tuple[str, ...]]
) -> dict[str, str]:
    """Force every child of a disabled Namespace to Disabled."""
    normalized = dict(tools)
    for namespace_id, child_ids in namespace_children.items():
        if normalized.get(namespace_id) == "disabled":
            for child_id in child_ids:
                normalized[child_id] = "disabled"
    return normalized


def _namespace_children_contract(
    catalog: dict[str, Any], supported: dict[str, tuple[str, ...]]
) -> dict[str, tuple[str, ...]]:
    """Validate and return the configured Namespace-to-child relationships."""
    namespace_children = {
        placement["namespace_id"]: tuple(placement["child_ids"])
        for placement in catalog.get("placements", {}).get("namespaces", [])
    }
    for namespace_id, child_ids in namespace_children.items():
        if namespace_id not in supported:
            raise ValueError(f"unknown Namespace catalog ID {namespace_id!r}")
        for child_id in child_ids:
            if child_id not in supported:
                raise ValueError(
                    f"Namespace {namespace_id!r} contains unknown child {child_id!r}"
                )
            if "disabled" not in supported[child_id]:
                raise ValueError(
                    f"Namespace child {child_id!r} must support the disabled state"
                )
    return namespace_children


def _apply_bundled_tool_overrides(
    field: str,
    tools: dict[str, str],
    overrides: Any,
    supported: dict[str, tuple[str, ...]],
    namespace_children: dict[str, tuple[str, ...]],
) -> dict[str, str]:
    """Validate bundled overrides and enforce Namespace child states."""
    if not isinstance(overrides, dict):
        raise ValueError(f"{field} must be an object")
    unknown_ids = sorted(set(overrides) - set(supported))
    if unknown_ids:
        raise ValueError(f"{field} contains unknown catalog IDs: {unknown_ids}")
    merged = dict(tools)
    for item_id, state in overrides.items():
        if state not in supported[item_id]:
            raise ValueError(
                f"{field}.{item_id} must be one of {list(supported[item_id])}"
            )
        merged[item_id] = state
    return _disable_namespace_children(merged, namespace_children)


def _catalog_base_tool_states(
    catalog: dict[str, Any], policies: dict[str, dict[str, Any]]
) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """Build supported states and defaults from catalog policies."""
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
        if item_type != "namespace":
            supported[item_id] = tuple(policy["supported"])
            builtin[item_id] = policy["default"]
            continue

        supported[item_id] = tuple(
            policy.get("namespace_supported", ("disabled", "expanded"))
        )
        namespace_default = policy.get("namespace_default")
        if (
            namespace_default is not None
            and namespace_default not in supported[item_id]
        ):
            raise ValueError(
                f"namespace default {namespace_default!r} is unsupported "
                f"for {item_id!r}"
            )
        builtin[item_id] = namespace_default or (
            "disabled" if policy["default"] == "disabled" else "expanded"
        )
    return supported, builtin


def _validate_description_visibility_contract(
    catalog: dict[str, Any], supported: dict[str, tuple[str, ...]]
) -> None:
    """Validate catalog description visibility declarations."""
    for item in catalog["items"]:
        description_visible_when = item.get("description_visible_when")
        if description_visible_when is None:
            continue
        if not item.get("description_i18n"):
            raise ValueError(
                f"catalog item {item['id']!r} description_visible_when requires "
                "description_i18n"
            )
        _normalize_visible_when(
            item["id"],
            description_visible_when,
            supported[item["id"]],
            field="description_visible_when",
        )


def _internal_containers_when_disabled_contract(
    catalog: dict[str, Any], supported: dict[str, tuple[str, ...]]
) -> frozenset[str]:
    """Validate tools retained only for internal conversion while Disabled."""
    internal: set[str] = set()
    for item in catalog["items"]:
        item_id = item["id"]
        value = item.get("internal_container_when_disabled", False)
        if not isinstance(value, bool):
            raise ValueError(
                f"catalog item {item_id!r} internal_container_when_disabled "
                "must be boolean"
            )
        if not value:
            continue
        if "disabled" not in supported[item_id]:
            raise ValueError(
                f"catalog item {item_id!r} internal container requires Disabled support"
            )
        internal.add(item_id)
    return frozenset(internal)


def _build_preset_profile(
    preset: dict[str, Any],
    catalog: dict[str, Any],
    supported: dict[str, tuple[str, ...]],
    input_definitions: dict[str, dict[str, Any]],
    namespace_children: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    """Build and validate one immutable preset Profile."""
    defaults = preset.get("defaults", {})
    overrides = preset.get("tools", {})
    tools: dict[str, str] = {}
    for item in catalog["items"]:
        item_id = item["id"]
        state = overrides.get(item_id, defaults.get(item["type"]))
        if state not in supported[item_id]:
            raise ValueError(
                f"bundled profile {preset.get('id')!r} has unsupported state "
                f"{state!r} for {item_id!r}"
            )
        tools[item_id] = state
    return {
        "id": preset["id"],
        "name": preset["name"],
        "tools": _disable_namespace_children(tools, namespace_children),
        "inputs": {
            item_id: {
                input_id: definition["default"]
                for input_id, definition in item_inputs.items()
            }
            for item_id, item_inputs in input_definitions.items()
        },
    }


@lru_cache(maxsize=1)
def tool_profile_contract() -> dict[str, Any]:
    """Return supported states and the immutable bundled profiles."""
    catalog = load_tool_catalog()
    policies = {policy["id"]: policy for policy in catalog["policies"]}
    supported, builtin = _catalog_base_tool_states(catalog, policies)

    namespace_children = _namespace_children_contract(catalog, supported)

    builtin_profile = dict(catalog["builtin_profile"])
    builtin_overrides = builtin_profile.pop("tools", {})
    builtin = _apply_bundled_tool_overrides(
        "builtin_profile.tools",
        builtin,
        builtin_overrides,
        supported,
        namespace_children,
    )

    input_definitions = _profile_input_contract(catalog, supported)
    profile_mutations = _profile_mutation_contract(
        catalog, input_definitions, supported
    )
    exec_projections = _exec_projection_contract(catalog, supported)
    internal_containers_when_disabled = _internal_containers_when_disabled_contract(
        catalog, supported
    )
    builtin_inputs = _normalize_profile_input_values(
        builtin_profile.pop("inputs", {}),
        input_definitions,
        field="builtin_profile",
    )
    _validate_description_visibility_contract(catalog, supported)

    profiles = [
        {
            **builtin_profile,
            "tools": dict(builtin),
            "inputs": builtin_inputs,
        }
    ]
    for preset in catalog.get("preset_profiles", []):
        profiles.append(
            _build_preset_profile(
                preset,
                catalog,
                supported,
                input_definitions,
                namespace_children,
            )
        )

    return {
        "profiles": profiles,
        "supported": supported,
        "builtin": builtin,
        "input_definitions": input_definitions,
        "profile_mutations": profile_mutations,
        "exec_projections": exec_projections,
        "internal_containers_when_disabled": internal_containers_when_disabled,
        "namespace_children": namespace_children,
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
    return _disable_namespace_children(normalized, contract["namespace_children"])


def normalize_tool_profile_inputs(
    value: Any, *, field: str
) -> dict[str, dict[str, str]]:
    """Validate and fill tool-card input values from the bundled defaults."""
    definitions: dict[str, dict[str, Any]] = tool_profile_contract()[
        "input_definitions"
    ]
    return _normalize_profile_input_values(value, definitions, field=field)


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


def normalize_tool_profile_input_overrides(
    value: Any,
) -> dict[str, dict[str, dict[str, str]]]:
    """Validate input-only overrides for immutable bundled Profiles."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("config: 'tool_profile_input_overrides' must be an object")

    readonly = tool_profile_contract()["readonly"]
    overrides: dict[str, dict[str, dict[str, str]]] = {}
    for raw_name, raw_inputs in value.items():
        name = validate_tool_profile_name(raw_name, allow_readonly=True)
        if name not in readonly:
            raise ValueError(
                "config: tool_profile_input_overrides may only contain bundled "
                f"Profiles; got '{name}'"
            )
        if not isinstance(raw_inputs, dict):
            raise ValueError(
                f"config: tool_profile_input_overrides.{name}.inputs must be an object"
            )
        merged_inputs = {
            item_id: dict(values)
            for item_id, values in readonly[name]["inputs"].items()
        }
        for item_id, values in raw_inputs.items():
            if isinstance(values, dict) and isinstance(
                merged_inputs.get(item_id), dict
            ):
                merged_inputs[item_id].update(values)
            else:
                merged_inputs[item_id] = values
        overrides[name] = normalize_tool_profile_inputs(
            merged_inputs, field=f"config: tool_profile_input_overrides.{name}"
        )
    return overrides


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
    input_overrides: dict[str, dict[str, dict[str, str]]] | None = None,
) -> dict[str, dict[str, str]]:
    """Resolve persisted tool-card input values for one Profile."""
    readonly = tool_profile_contract()["readonly"]
    profile = readonly.get(name, profiles.get(name))
    if profile is None:
        raise ValueError(f"unknown tool profile '{name}'")
    if name in readonly and input_overrides and name in input_overrides:
        return {
            item_id: dict(values) for item_id, values in input_overrides[name].items()
        }
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
    input_overrides: dict[str, dict[str, dict[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    """Build the ordered public representation consumed by the Admin UI."""
    contract = tool_profile_contract()
    result = [
        {
            "id": profile["id"],
            "name": profile["name"],
            "tools": dict(profile["tools"]),
            "inputs": resolve_tool_profile_inputs(
                profile["id"], profiles, input_overrides
            ),
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
    for item in catalog["items"]:
        if item["type"] != "hosted":
            continue
        provider_types = (item["name"], *item.get("aliases", []))
        for provider_type in provider_types:
            by_type_name[(provider_type, provider_type)] = item["id"]
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


def is_internal_container_when_disabled(route: Any, item_id: str) -> bool:
    """Return whether a Disabled tool must survive until conversion finishes."""
    return (
        route_tool_state(route, item_id) == "disabled"
        and item_id in tool_profile_contract()["internal_containers_when_disabled"]
    )


def apply_profile_tool_mutations(
    tool: Any,
    item_id: str,
    route: Any,
) -> Any:
    """Apply catalog-declared mutations to one flat tool definition."""
    if not isinstance(tool, dict) or route_tool_state(route, item_id) not in {
        "modified",
        "expanded",
    }:
        return tool
    mutations = tool_profile_contract()["profile_mutations"].get(item_id, ())
    values = getattr(route, "tool_profile_inputs", {}).get(item_id, {})
    if not mutations or not isinstance(values, dict):
        return tool
    adapted = copy.deepcopy(tool)
    changed = False
    for mutation in mutations:
        value = values.get(mutation["input_id"])
        if not isinstance(value, str) or not (append := value.strip()):
            continue
        if mutation["target"] == "description":
            description = _append_profile_description(
                adapted.get("description"), append
            )
            if description != adapted.get("description"):
                adapted["description"] = description
                changed = True
            continue
        parameters = adapted.get("parameters")
        if not isinstance(parameters, dict):
            continue
        properties = parameters.get("properties")
        if not isinstance(properties, dict):
            continue
        parameter = properties.get(mutation["parameter"])
        if not isinstance(parameter, dict):
            continue
        description = _append_profile_description(parameter.get("description"), append)
        if description != parameter.get("description"):
            parameter["description"] = description
            changed = True
    return adapted if changed else tool


def _append_profile_description(value: Any, append: str) -> str:
    """Append one Profile-owned model instruction without duplication."""
    description = value if isinstance(value, str) else ""
    if append in description:
        return description
    return f"{description}\n\n{append}" if description else append
