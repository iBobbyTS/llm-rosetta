"""Codex local-mode catalog generation and file synchronization."""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from .config import _atomic_write_bytes

CATALOG_FILENAME = "model_catalog.json"
CODEX_CONFIG_FILENAME = "config.toml"
CATALOG_RESOURCE = "codex_models_0_144_1.json"
PRESET_RESOURCE = "codex_model_presets.json"

_MODEL_CATALOG_ASSIGNMENT_RE = re.compile(
    r"^[ \t]*(?:model_catalog_json|[\"']model_catalog_json[\"'])[ \t]*="
    r"[^\r\n]*(?:\r?\n|$)"
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


def build_model_catalog(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Build a Codex catalog from the bundled presets and configured LLM aliases."""
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

    custom_names: set[str] = set()
    model_groups = raw_config.get("model_groups", {})
    if isinstance(model_groups, dict):
        for group in model_groups.values():
            if not isinstance(group, dict) or group.get("type") != "llm":
                continue
            models = group.get("models", {})
            if not isinstance(models, dict):
                continue
            custom_names.update(
                name
                for name in models
                if isinstance(name, str) and name and name not in by_slug
            )

    for name in sorted(custom_names):
        model = copy.deepcopy(presets.get(name, terra))
        if name not in presets:
            model["slug"] = name
            model["display_name"] = name
            model["description"] = name
        base_models.append(model)

    return {"models": base_models}


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


def _edit_config_toml(text: str, model_catalog_path: str | None) -> str:
    """Remove all catalog assignments and optionally add one root assignment."""
    assignments = _active_catalog_assignment_lines(text)
    cleaned = "".join(
        line
        for index, line in enumerate(text.splitlines(keepends=True))
        if index not in assignments
    )
    if model_catalog_path is None:
        return cleaned
    newline = "\r\n" if "\r\n" in text else "\n"
    assignment = f"model_catalog_json = {json.dumps(model_catalog_path)}{newline}"
    return assignment + cleaned


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
    ) -> None:
        self.codex_home = codex_home
        self.catalog = catalog
        self._snapshots: tuple[_FileSnapshot, _FileSnapshot] | None = None

    @classmethod
    def sync(
        cls, codex_home: str, raw_config: dict[str, Any]
    ) -> CodexLocalModeTransaction:
        return cls(codex_home, catalog=build_model_catalog(raw_config))

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
            updated = _edit_config_toml(current, os.path.abspath(catalog_file))
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
