"""Codex local-mode catalog generation and file synchronization."""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import tomllib
from dataclasses import dataclass
from importlib import resources
from typing import Any

from .config import _atomic_write_bytes

CATALOG_FILENAME = "model_catalog.json"
CODEX_CONFIG_FILENAME = "config.toml"
CATALOG_RESOURCE = "codex_models_0_144_4.json"
PRESET_RESOURCE = "codex_model_presets.json"
CODEX_API_KEY_ID = "codex"
CODEX_API_KEY_LABEL = "codex"
CODEX_PROVIDER_ID = "codex_rosetta"
ENABLED_REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")

_MODEL_CATALOG_ASSIGNMENT_RE = re.compile(
    r"^[ \t]*(?:model_catalog_json|[\"']model_catalog_json[\"'])[ \t]*="
    r"[^\r\n]*(?:\r?\n|$)"
)
_MODEL_PROVIDER_ASSIGNMENT_RE = re.compile(
    r"^[ \t]*(?:model_provider|[\"']model_provider[\"'])[ \t]*="
    r"[^\r\n]*(?:\r?\n|$)"
)
_MANAGED_MODEL_PROVIDER_ASSIGNMENT_RE = re.compile(
    r"^[ \t]*(?:model_provider|[\"']model_provider[\"'])[ \t]*=[ \t]*"
    r"(?:\"codex_rosetta\"|'codex_rosetta')[ \t]*(?:#.*)?(?:\r?\n|$)"
)
_ENABLED_REASONING_EFFORTS_ASSIGNMENT_RE = re.compile(
    r"^[ \t]*(?:enabled-reasoning-efforts|[\"']enabled-reasoning-efforts[\"'])"
    r"[ \t]*=[^\r\n]*(?:\r?\n|$)"
)
_COMMENTED_MODEL_CATALOG_ASSIGNMENT_RE = re.compile(
    r"^(?P<indent>[ \t]*)#[ \t]*(?:model_catalog_json|[\"']model_catalog_json[\"'])"
    r"[ \t]*=[^\r\n]*(?:\r?\n|$)"
)
_COMMENTED_MODEL_PROVIDER_ASSIGNMENT_RE = re.compile(
    r"^(?P<indent>[ \t]*)#[ \t]*(?:model_provider|[\"']model_provider[\"'])"
    r"[ \t]*=[^\r\n]*(?:\r?\n|$)"
)
_TABLE_HEADER_RE = re.compile(r"^[ \t]*\[\[?.*?\]\]?[ \t]*(?:#.*)?(?:\r?\n|$)")
_DESKTOP_TABLE_RE = re.compile(
    r"^[ \t]*\[[ \t]*(?:desktop|[\"']desktop[\"'])[ \t]*\]"
    r"[ \t]*(?:#.*)?(?:\r?\n|$)"
)
_CODEX_PROVIDER_TABLE_RE = re.compile(
    r"^[ \t]*\[\[?[ \t]*(?:model_providers|[\"']model_providers[\"'])"
    r"[ \t]*\.[ \t]*(?:codex_rosetta|[\"']codex_rosetta[\"'])"
    r"(?=[ \t]*(?:\.|\]))"
)


def _is_unescaped(line: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and line[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 0


def _find_multiline_end(line: str, delimiter: str, start: int) -> int:
    end = line.find(delimiter, start)
    while end >= 0 and delimiter == '"""' and not _is_unescaped(line, end):
        end = line.find(delimiter, end + len(delimiter))
    return end


def _skip_basic_string(line: str, index: int) -> int:
    index += 1
    while index < len(line):
        if line[index] == "\\":
            index += 2
        elif line[index] == '"':
            return index + 1
        else:
            index += 1
    return index


def _skip_literal_string(line: str, index: int) -> int:
    end = line.find("'", index + 1)
    return len(line) if end < 0 else end + 1


def _multiline_state_after_line(line: str, state: str | None) -> str | None:
    """Track TOML multiline strings without interpreting ordinary values."""
    index = 0
    while index < len(line):
        if state is not None:
            end = _find_multiline_end(line, state, index)
            if end < 0:
                return state
            index = end + len(state)
            state = None
            continue

        char = line[index]
        if char == "#":
            return None
        if line.startswith('"""', index) or line.startswith("'''", index):
            state = line[index : index + 3]
            index += 3
            continue
        if char == '"':
            index = _skip_basic_string(line, index)
            continue
        if char == "'":
            index = _skip_literal_string(line, index)
            continue
        index += 1
    return state


def _active_catalog_assignment_lines(text: str) -> set[int]:
    assignments: set[int] = set()
    multiline_state: str | None = None
    assignment_multiline_state: str | None = None
    for index, line in enumerate(text.splitlines(keepends=True)):
        if assignment_multiline_state is not None:
            assignments.add(index)
            assignment_multiline_state = _multiline_state_after_line(
                line, assignment_multiline_state
            )
            continue
        if multiline_state is None and _MODEL_CATALOG_ASSIGNMENT_RE.match(line):
            assignments.add(index)
            assignment_multiline_state = _multiline_state_after_line(line, None)
            continue
        multiline_state = _multiline_state_after_line(line, multiline_state)
    return assignments


def _active_table_headers(text: str) -> list[int]:
    """Return active TOML table-header line indexes outside multiline strings."""
    headers: list[int] = []
    multiline_state: str | None = None
    for index, line in enumerate(text.splitlines(keepends=True)):
        if multiline_state is None and _TABLE_HEADER_RE.match(line):
            headers.append(index)
        multiline_state = _multiline_state_after_line(line, multiline_state)
    return headers


def _root_model_provider_assignment_lines(text: str, *, managed_only: bool) -> set[int]:
    """Return root model_provider assignments, optionally only Rosetta's value."""
    lines = text.splitlines(keepends=True)
    headers = _active_table_headers(text)
    root_end = headers[0] if headers else len(lines)
    pattern = (
        _MANAGED_MODEL_PROVIDER_ASSIGNMENT_RE
        if managed_only
        else _MODEL_PROVIDER_ASSIGNMENT_RE
    )
    assignments: set[int] = set()
    multiline_state: str | None = None
    for index in range(root_end):
        line = lines[index]
        if multiline_state is None and pattern.match(line):
            assignments.add(index)
        multiline_state = _multiline_state_after_line(line, multiline_state)
    return assignments


def _reasoning_effort_lines(text: str) -> tuple[set[int], int | None, int | None]:
    """Locate root/Desktop effort settings and the first Desktop table bounds."""
    lines = text.splitlines(keepends=True)
    headers = _active_table_headers(text)
    root_end = headers[0] if headers else len(lines)
    desktop_header = next(
        (index for index in headers if _DESKTOP_TABLE_RE.match(lines[index])), None
    )
    desktop_end = (
        next((index for index in headers if index > desktop_header), len(lines))
        if desktop_header is not None
        else None
    )
    assignments: set[int] = set()
    multiline_state: str | None = None
    for index, line in enumerate(lines):
        managed_scope = index < root_end or (
            desktop_header is not None
            and desktop_end is not None
            and desktop_header < index < desktop_end
        )
        if (
            managed_scope
            and multiline_state is None
            and _ENABLED_REASONING_EFFORTS_ASSIGNMENT_RE.match(line)
        ):
            assignments.add(index)
        multiline_state = _multiline_state_after_line(line, multiline_state)
    return assignments, desktop_header, desktop_end


def _complete_desktop_reasoning_efforts_line(
    text: str,
    lines: set[int],
    desktop_header: int | None,
    desktop_end: int | None,
) -> int | None:
    """Return one Desktop assignment containing all six efforts."""
    if desktop_header is None or desktop_end is None:
        return None
    source_lines = text.splitlines(keepends=True)
    expected = set(ENABLED_REASONING_EFFORTS)
    for index in lines:
        if not desktop_header < index < desktop_end:
            continue
        try:
            value = tomllib.loads(source_lines[index]).get("enabled-reasoning-efforts")
        except tomllib.TOMLDecodeError:
            continue
        if (
            isinstance(value, list)
            and len(value) == len(expected)
            and all(isinstance(item, str) for item in value)
            and set(value) == expected
        ):
            return index
    return None


def _edit_enabled_reasoning_efforts(text: str, *, enabled: bool) -> str:
    """Maintain reasoning-effort visibility inside the Desktop table."""
    lines = text.splitlines(keepends=True)
    assignments, desktop_header, desktop_end = _reasoning_effort_lines(text)
    complete_line = _complete_desktop_reasoning_efforts_line(
        text, assignments, desktop_header, desktop_end
    )
    preserved = {complete_line} if enabled and complete_line is not None else set()
    removed = assignments - preserved
    if not enabled or complete_line is not None:
        return "".join(line for index, line in enumerate(lines) if index not in removed)

    newline = "\r\n" if "\r\n" in text else "\n"
    setting = (
        f"enabled-reasoning-efforts = {json.dumps(ENABLED_REASONING_EFFORTS)}{newline}"
    )
    if desktop_header is None or desktop_end is None:
        cleaned = "".join(
            line for index, line in enumerate(lines) if index not in removed
        ).rstrip(" \t\r\n")
        prefix = cleaned + newline * 2 if cleaned else ""
        return prefix + f"[desktop]{newline}" + setting

    insert_at = desktop_header + 1
    for index in range(desktop_header + 1, desktop_end):
        if index not in removed and lines[index].strip():
            insert_at = index + 1
    trailing_blanks = {
        index
        for index in range(insert_at, desktop_end)
        if index not in removed and not lines[index].strip()
    }
    table_setting = setting + (newline if desktop_end < len(lines) else "")

    rebuilt: list[str] = []
    for index, line in enumerate(lines):
        if index == insert_at:
            rebuilt.append(table_setting)
        if index not in removed and index not in trailing_blanks:
            rebuilt.append(line)
    if insert_at == len(lines):
        rebuilt.append(table_setting)
    return "".join(rebuilt)


def _commented_root_assignment_lines(text: str) -> dict[str, int]:
    """Return the first reusable commented local-mode assignment per field."""
    lines = text.splitlines(keepends=True)
    headers = _active_table_headers(text)
    root_end = headers[0] if headers else len(lines)
    matches: dict[str, int] = {}
    multiline_state: str | None = None
    patterns = (
        ("model_catalog_json", _COMMENTED_MODEL_CATALOG_ASSIGNMENT_RE),
        ("model_provider", _COMMENTED_MODEL_PROVIDER_ASSIGNMENT_RE),
    )
    for index in range(root_end):
        line = lines[index]
        if multiline_state is None:
            for field, pattern in patterns:
                if field not in matches and pattern.match(line):
                    matches[field] = index
                    break
        multiline_state = _multiline_state_after_line(line, multiline_state)
    return matches


def _codex_provider_table_lines(text: str) -> set[int]:
    """Return all codex_rosetta provider-table sections, including subtables."""
    lines = text.splitlines(keepends=True)
    headers = _active_table_headers(text)
    removed: set[int] = set()
    for position, start in enumerate(headers):
        if not _CODEX_PROVIDER_TABLE_RE.match(lines[start]):
            continue
        end = headers[position + 1] if position + 1 < len(headers) else len(lines)
        removed.update(range(start, end))
    return removed


def _json_resource(name: str) -> Any:
    raw = resources.files("codex_rosetta.gateway").joinpath(name).read_text("utf-8")
    return json.loads(raw)


def _catalog_resource() -> dict[str, Any]:
    catalog = _json_resource(CATALOG_RESOURCE)
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("bundled Codex model catalog is empty or invalid")
    return catalog


def _preset_resource() -> dict[str, Any]:
    presets = _json_resource(PRESET_RESOURCE)
    if not isinstance(presets, dict):
        raise ValueError("bundled Codex model presets must be an object")
    if not isinstance(presets.get("shared_overrides"), dict):
        raise ValueError("bundled Codex model presets have invalid shared overrides")
    if not isinstance(presets.get("models"), list):
        raise ValueError("bundled Codex model presets have invalid models")
    return presets


def _replace_identity(value: Any, source: str, identity: str) -> Any:
    if isinstance(value, str):
        return value.replace(source, identity)
    if isinstance(value, list):
        return [_replace_identity(item, source, identity) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_identity(item, source, identity)
            for key, item in value.items()
        }
    return copy.deepcopy(value)


def _reasoning_levels(
    terra: dict[str, Any], requested_efforts: list[str]
) -> list[dict[str, Any]]:
    terra_levels = terra.get("supported_reasoning_levels")
    if not isinstance(terra_levels, list):
        raise ValueError("gpt-5.6-terra has invalid reasoning levels")
    by_effort = {
        level.get("effort"): level
        for level in terra_levels
        if isinstance(level, dict) and isinstance(level.get("effort"), str)
    }
    missing = [effort for effort in requested_efforts if effort not in by_effort]
    if missing:
        raise ValueError(f"model preset references unknown reasoning levels: {missing}")
    return [copy.deepcopy(by_effort[effort]) for effort in requested_efforts]


def _materialize_model_preset(
    terra: dict[str, Any],
    shared_overrides: dict[str, Any],
    raw_preset: dict[str, Any],
    identity_source: str,
) -> dict[str, Any]:
    required_strings = ("slug", "display_name", "description", "identity")
    if any(not isinstance(raw_preset.get(field), str) for field in required_strings):
        raise ValueError("bundled Codex model preset has invalid identity fields")
    requested_efforts = raw_preset.get("supported_reasoning_levels")
    if (
        not isinstance(requested_efforts, list)
        or not requested_efforts
        or not all(isinstance(effort, str) for effort in requested_efforts)
    ):
        raise ValueError("bundled Codex model preset has invalid reasoning levels")

    model = copy.deepcopy(terra)
    identity = raw_preset["identity"]
    for field in ("base_instructions", "model_messages"):
        model[field] = _replace_identity(model.get(field), identity_source, identity)

    model.update(copy.deepcopy(shared_overrides))
    model.update(
        {
            key: copy.deepcopy(value)
            for key, value in raw_preset.items()
            if key not in {"identity", "supported_reasoning_levels"}
        }
    )
    context_window = model.get("context_window")
    if not isinstance(context_window, int) or context_window <= 0:
        raise ValueError(f"model preset '{model['slug']}' has invalid context window")
    model["max_context_window"] = context_window
    model["supported_reasoning_levels"] = _reasoning_levels(terra, requested_efforts)
    default_reasoning = terra.get("default_reasoning_level")
    model["default_reasoning_level"] = (
        default_reasoning
        if default_reasoning in requested_efforts
        else requested_efforts[0]
    )
    return model


def _model_presets(terra: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resource = _preset_resource()
    template_slug = resource.get("template_slug")
    identity_source = resource.get("identity_source")
    if template_slug != terra.get("slug") or not isinstance(identity_source, str):
        raise ValueError("bundled Codex model presets have invalid template metadata")
    shared_overrides = resource["shared_overrides"]
    presets: dict[str, dict[str, Any]] = {}
    for raw_preset in resource["models"]:
        if not isinstance(raw_preset, dict):
            raise ValueError("bundled Codex model preset must be an object")
        model = _materialize_model_preset(
            terra, shared_overrides, raw_preset, identity_source
        )
        slug = model["slug"]
        if slug in presets:
            raise ValueError(f"duplicate bundled Codex model preset: {slug}")
        presets[slug] = model
    return presets


def _configured_model_upstreams(raw_config: dict[str, Any]) -> dict[str, str | None]:
    configured: dict[str, str | None] = {}
    model_groups = raw_config.get("model_groups", {})
    if not isinstance(model_groups, dict):
        return configured

    for group in model_groups.values():
        if not isinstance(group, dict):
            continue
        models = group.get("models", {})
        if not isinstance(models, dict):
            continue
        for name, raw_model in models.items():
            if not isinstance(name, str) or not name:
                continue
            raw_upstream = (
                raw_model.get("upstream_model")
                if isinstance(raw_model, dict)
                else raw_model
            )
            configured[name] = (
                raw_upstream.strip()
                if isinstance(raw_upstream, str) and raw_upstream.strip()
                else None
            )
    return configured


def build_model_catalog(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Build a Codex catalog from configured models or the bundled defaults."""
    bundled = _catalog_resource()
    base_models = copy.deepcopy(bundled["models"])
    by_slug = {
        model.get("slug"): model
        for model in base_models
        if isinstance(model, dict) and isinstance(model.get("slug"), str)
    }
    terra = by_slug.get("gpt-5.6-terra")
    if terra is None:
        raise ValueError("bundled Codex model catalog has no gpt-5.6-terra preset")
    presets = _model_presets(terra)

    configured_upstream_names = _configured_model_upstreams(raw_config)
    if not configured_upstream_names:
        return {"models": base_models}

    selected_models: list[dict[str, Any]] = []
    for name in sorted(configured_upstream_names):
        model = copy.deepcopy(by_slug.get(name) or presets.get(name) or terra)
        if name not in by_slug and name not in presets:
            model["slug"] = name
            model["display_name"] = name
            model["description"] = name
        if name == "codex-auto-review":
            upstream_name = configured_upstream_names.get(name)
            if upstream_name is not None and upstream_name != name:
                model["tool_mode"] = "code_mode_only"
        selected_models.append(model)

    return {"models": selected_models}


def catalog_path(codex_home: str) -> str:
    return os.path.join(codex_home, CATALOG_FILENAME)


def codex_config_path(codex_home: str) -> str:
    return os.path.join(codex_home, CODEX_CONFIG_FILENAME)


def config_toml_has_model_catalog(codex_home: str) -> bool:
    """Return whether config.toml contains an active model_catalog_json assignment."""
    path = codex_config_path(codex_home)
    try:
        with open(path, encoding="utf-8") as stream:
            return bool(_active_catalog_assignment_lines(stream.read()))
    except FileNotFoundError:
        return False


def _find_codex_api_key_entry(api_keys: Any) -> dict[str, Any] | None:
    if not isinstance(api_keys, list):
        return None
    entries = [entry for entry in api_keys if isinstance(entry, dict)]
    return next(
        (entry for entry in entries if entry.get("id") == CODEX_API_KEY_ID),
        next(
            (entry for entry in entries if entry.get("label") == CODEX_API_KEY_LABEL),
            None,
        ),
    )


def ensure_codex_api_key(raw_config: dict[str, Any]) -> bool:
    """Ensure local mode has one stable gateway key named ``codex``.

    Returns:
        Whether the raw gateway configuration was changed.
    """
    server = raw_config.setdefault("server", {})
    if not isinstance(server, dict):
        raise ValueError("config: server must be an object")
    api_keys = server.get("api_keys")
    changed = False
    if api_keys is None:
        api_keys = []
        legacy_key = server.pop("api_key", None)
        if legacy_key is not None:
            api_keys.append(
                {
                    "id": "default",
                    "label": "default",
                    "key": legacy_key,
                }
            )
        server["api_keys"] = api_keys
        changed = True
    if not isinstance(api_keys, list):
        raise ValueError("config: server.api_keys must be a list")

    entry = _find_codex_api_key_entry(api_keys)
    if entry is not None:
        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("config: the codex gateway API key is invalid")
        return changed

    api_keys.append(
        {
            "id": CODEX_API_KEY_ID,
            "label": CODEX_API_KEY_LABEL,
            "key": f"rsk-{secrets.token_hex(24)}",
        }
    )
    return True


def codex_api_key_value(api_keys: Any) -> str:
    """Return the resolved key value for the local-mode Codex client."""
    entry = _find_codex_api_key_entry(api_keys)
    key = entry.get("key") if entry is not None else None
    if not isinstance(key, str) or not key.strip():
        raise ValueError("config: local mode requires a gateway API key named 'codex'")
    return key


def _edit_config_toml(
    text: str,
    model_catalog_path: str | None,
    *,
    gateway_port: int | None = None,
    api_key: str | None = None,
) -> str:
    """Replace Rosetta-managed catalog and provider settings in Codex TOML."""
    text = _edit_enabled_reasoning_efforts(text, enabled=model_catalog_path is not None)
    assignments = _active_catalog_assignment_lines(text)
    assignments.update(
        _root_model_provider_assignment_lines(
            text, managed_only=model_catalog_path is None
        )
    )
    assignments.update(_codex_provider_table_lines(text))
    commented = (
        _commented_root_assignment_lines(text) if model_catalog_path is not None else {}
    )
    lines = text.splitlines(keepends=True)
    cleaned = "".join(
        line
        for index, line in enumerate(lines)
        if index not in assignments and index not in commented.values()
    )
    if model_catalog_path is None:
        return cleaned
    if (
        isinstance(gateway_port, bool)
        or not isinstance(gateway_port, int)
        or not 1 <= gateway_port <= 65535
    ):
        raise ValueError("gateway port must be an integer between 1 and 65535")
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("local mode requires a non-empty Codex gateway API key")
    newline = "\r\n" if "\r\n" in text else "\n"
    replacements = {
        "model_catalog_json": (
            f"model_catalog_json = {json.dumps(model_catalog_path)}{newline}"
        ),
        "model_provider": f'model_provider = "{CODEX_PROVIDER_ID}"{newline}',
    }
    if commented:
        by_index = {index: field for field, index in commented.items()}
        rebuilt: list[str] = []
        for index, line in enumerate(lines):
            field = by_index.get(index)
            if field is not None:
                indent = line[: len(line) - len(line.lstrip(" \t"))]
                rebuilt.append(indent + replacements[field])
            elif index not in assignments:
                rebuilt.append(line)
        missing = "".join(
            replacement
            for field, replacement in replacements.items()
            if field not in commented
        )
        cleaned = missing + "".join(rebuilt)
        root = ""
    else:
        root = "".join(replacements.values())
    base = (root + cleaned).rstrip(" \t\r\n")
    provider = newline.join(
        (
            f"[model_providers.{CODEX_PROVIDER_ID}]",
            'name = "OpenAI"',
            'wire_api = "responses"',
            "requires_openai_auth = true",
            f'base_url = "http://127.0.0.1:{gateway_port}/v1"',
            f"experimental_bearer_token = {json.dumps(api_key)}",
            "",
        )
    )
    return base + newline * 2 + provider


@dataclass(frozen=True)
class _FileSnapshot:
    path: str
    content: bytes | None

    @classmethod
    def capture(cls, path: str) -> _FileSnapshot:
        try:
            with open(path, "rb") as stream:
                return cls(path, stream.read())
        except FileNotFoundError:
            return cls(path, None)

    def restore(self) -> None:
        if self.content is None:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            return
        os.makedirs(os.path.dirname(self.path) or ".", mode=0o700, exist_ok=True)
        _atomic_write_bytes(self.path, self.content)


class CodexLocalModeTransaction:
    """Apply and, when needed, compensate one Codex local-mode file update."""

    def __init__(
        self,
        codex_home: str,
        *,
        catalog: dict[str, Any] | None,
        gateway_port: int | None = None,
        api_key: str | None = None,
    ) -> None:
        self.codex_home = codex_home
        self.catalog = catalog
        self.gateway_port = gateway_port
        self.api_key = api_key
        self._snapshots: tuple[_FileSnapshot, _FileSnapshot] | None = None

    @classmethod
    def sync(
        cls,
        codex_home: str,
        raw_config: dict[str, Any],
        *,
        gateway_port: int,
        api_key: str,
    ) -> CodexLocalModeTransaction:
        return cls(
            codex_home,
            catalog=build_model_catalog(raw_config),
            gateway_port=gateway_port,
            api_key=api_key,
        )

    @classmethod
    def clear(cls, codex_home: str) -> CodexLocalModeTransaction:
        return cls(codex_home, catalog=None)

    def apply(self) -> None:
        if self._snapshots is not None:
            raise RuntimeError("Codex local-mode transaction was already applied")
        os.makedirs(self.codex_home, mode=0o700, exist_ok=True)
        catalog_file = catalog_path(self.codex_home)
        config_file = codex_config_path(self.codex_home)
        self._snapshots = (
            _FileSnapshot.capture(catalog_file),
            _FileSnapshot.capture(config_file),
        )
        try:
            if self.catalog is None:
                try:
                    os.unlink(catalog_file)
                except FileNotFoundError:
                    pass
                if self._snapshots[1].content is not None:
                    current = self._snapshots[1].content.decode("utf-8")
                    updated = _edit_config_toml(current, None)
                    if updated != current:
                        _atomic_write_bytes(config_file, updated.encode("utf-8"))
                return

            serialized = (
                json.dumps(self.catalog, indent=2, ensure_ascii=False) + "\n"
            ).encode("utf-8")
            _atomic_write_bytes(catalog_file, serialized)
            current = (
                self._snapshots[1].content.decode("utf-8")
                if self._snapshots[1].content is not None
                else ""
            )
            updated = _edit_config_toml(
                current,
                os.path.abspath(catalog_file),
                gateway_port=self.gateway_port,
                api_key=self.api_key,
            )
            _atomic_write_bytes(config_file, updated.encode("utf-8"))
        except BaseException:
            self.rollback()
            raise

    def rollback(self) -> None:
        if self._snapshots is None:
            return
        snapshots = self._snapshots
        self._snapshots = None
        errors: list[BaseException] = []
        for snapshot in snapshots:
            try:
                snapshot.restore()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(f"failed to restore Codex local-mode files: {errors[0]}")
