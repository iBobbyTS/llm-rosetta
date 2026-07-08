"""Gateway-only tool adaptation for Codex-facing routes.

The functions in this module localize Codex's native editing tools for
OpenAI-compatible chat upstreams, then translate model-selected localized tools
back to Codex-native tool calls before the response is returned to Codex.
"""

from __future__ import annotations

import base64
import json
import shlex
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


DEFAULT_TOOL_CALL_CACHE_TTL_HOURS = 24.0
DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS = True
DEFAULT_ENABLE_TOOL_DESCRIPTION_OPTIMIZATION = True
DEFAULT_ENABLE_PHASE_DETECTION = True
LOCALIZED_CODE_TOOL_NAMES = frozenset({"Read", "Edit", "Write", "Glob", "Grep", "Bash"})
NATIVE_CODE_TOOL_NAMES = frozenset(
    {"apply_patch", "exec_command", "write_stdin", "shell_command"}
)
LOCALIZATION_CAPABILITIES_KEY = "_codex_tool_localization_capabilities"
READ_OUTPUT_CACHE_KEY = "_codex_read_output_cache"


@dataclass(frozen=True)
class LocalizedToolMapping:
    """Mapping between a model-facing localized call and a Codex-native call."""

    call_id: str
    localized_name: str
    localized_input: dict[str, Any]
    native_name: str
    native_input: Any
    native_type: str = "function"

    def original_tool_call(self) -> dict[str, Any]:
        """Return the model-facing Chat tool call shape for persistence."""
        return _chat_tool_call(self.call_id, self.localized_name, self.localized_input)

    def codex_tool_call(self) -> dict[str, Any]:
        """Return the Codex-native Chat tool call shape for persistence."""
        return _chat_tool_call(self.call_id, self.native_name, self.native_input)


@dataclass(frozen=True)
class ReadCall:
    """Model-facing Read call identity and file path."""

    call_id: str
    file_path: str


class ReadOutputCache:
    """Session-local cache rebuilt from localized Chat history."""

    def __init__(self) -> None:
        self._items: dict[str, list[str]] = {}
        self._pending_reads: dict[str, str] = {}
        self._pending_mutations: dict[str, str] = {}

    def remember_read_call(self, call_id: str, file_path: str) -> None:
        """Remember the file path associated with a Read call."""
        if call_id and file_path:
            self._pending_reads[call_id] = file_path

    def remember_tool_output(self, call_id: str, text: str) -> None:
        """Remember Read outputs and invalidate cache after successful edits."""
        file_path = self._pending_reads.get(call_id)
        if file_path is not None:
            self.remember(file_path, text)
            return
        file_path = self._pending_mutations.get(call_id)
        if file_path is not None and not _tool_output_indicates_failure(text):
            self.invalidate(file_path)

    def remember(self, file_path: str, text: str) -> None:
        """Remember one Read output for a file path."""
        if not file_path:
            return
        self._items.setdefault(file_path, []).append(_unwrap_command_output(text))

    def remember_mutating_call(self, call_id: str, file_path: str) -> None:
        """Remember a localized call that may change one file."""
        if call_id and file_path:
            self._pending_mutations[call_id] = file_path

    def invalidate(self, file_path: str) -> None:
        """Drop cached Read outputs for a file path."""
        self._items.pop(file_path, None)

    def expand_edit(
        self,
        *,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> tuple[str, str] | None:
        """Expand a substring edit to a full-line replacement when unambiguous."""
        if not file_path or not old_string:
            return None

        candidates: list[tuple[str, str]] = []
        for text in self._items.get(file_path, []):
            candidates.extend(
                _line_expansion_candidates(
                    text,
                    old_string=old_string,
                    new_string=new_string,
                )
            )

        unique: list[tuple[str, str]] = []
        for candidate in candidates:
            if candidate not in unique:
                unique.append(candidate)

        if len(unique) != 1:
            return None
        expanded_old, expanded_new = unique[0]
        if expanded_old == old_string:
            return None
        return expanded_old, expanded_new


@dataclass(frozen=True)
class NativeToolCapabilities:
    """Executable Codex tool capabilities present in the original request."""

    has_exec_command: bool = False
    has_shell_command: bool = False
    has_custom_apply_patch: bool = True

    @classmethod
    def from_chat_tools(cls, tools: Any) -> NativeToolCapabilities:
        """Infer native tool capabilities from converted Chat tool definitions."""
        if not isinstance(tools, list):
            return cls()

        has_exec_command = False
        has_shell_command = False
        has_custom_apply_patch = False
        for tool in tools:
            name = _chat_tool_name(tool)
            if name == "exec_command":
                has_exec_command = True
            elif name == "shell_command":
                has_shell_command = True
            elif name == "apply_patch" and _chat_tool_type(tool) == "custom":
                has_custom_apply_patch = True

        return cls(
            has_exec_command=has_exec_command,
            has_shell_command=has_shell_command,
            has_custom_apply_patch=has_custom_apply_patch,
        )

    def to_metadata(self) -> dict[str, bool]:
        """Serialize capabilities for internal gateway metadata."""
        return {
            "has_exec_command": self.has_exec_command,
            "has_shell_command": self.has_shell_command,
            "has_custom_apply_patch": self.has_custom_apply_patch,
        }

    @classmethod
    def from_metadata(cls, value: Any) -> NativeToolCapabilities:
        """Deserialize capabilities from internal gateway metadata."""
        if not isinstance(value, dict):
            return cls()
        return cls(
            has_exec_command=bool(value.get("has_exec_command")),
            has_shell_command=bool(value.get("has_shell_command")),
            has_custom_apply_patch=bool(value.get("has_custom_apply_patch", True)),
        )


class CodexToolLocalizationStore:
    """Small in-memory store for localizing prior assistant tool calls.

    Codex sends the conversation history back on later turns.  The gateway
    returns native Codex tool calls downstream, so later Responses->Chat request
    conversion sees native names in assistant history.  This store lets the
    gateway restore the original localized names/arguments for the upstream
    model when it recognizes the call_id.
    """

    def __init__(self, *, max_size: int = 10_000) -> None:
        self._items: OrderedDict[str, LocalizedToolMapping] = OrderedDict()
        self._max_size = max_size

    def remember(self, mapping: LocalizedToolMapping) -> None:
        """Remember one localized/native call mapping."""
        if not mapping.call_id:
            return
        self._items[mapping.call_id] = mapping
        self._items.move_to_end(mapping.call_id)
        while len(self._items) > self._max_size:
            self._items.popitem(last=False)

    def get(self, call_id: str) -> LocalizedToolMapping | None:
        """Return a mapping by call_id, if present."""
        mapping = self._items.get(call_id)
        if mapping is not None:
            self._items.move_to_end(call_id)
        return mapping

    def clear(self) -> None:
        """Remove all remembered mappings."""
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


def should_localize_code_tools(route: Any) -> bool:
    """Return whether Codex editing tools should be localized for this route."""
    tool_adaptation = getattr(route, "tool_adaptation", None) or {}
    return (
        bool(tool_adaptation.get("localize_code_editing_tools"))
        and getattr(route, "source_provider", None)
        in ("openai_responses", "open_responses")
        and getattr(route, "target_provider", None) == "openai_chat"
    )


def use_apply_patch_for_code_edits(tool_adaptation: dict[str, Any] | None) -> bool:
    """Return whether localized code edits should use Codex apply_patch."""
    if not isinstance(tool_adaptation, dict):
        return DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS
    return bool(
        tool_adaptation.get(
            "use_apply_patch_for_code_edits",
            DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS,
        )
    )


def enable_tool_description_optimization(
    tool_adaptation: dict[str, Any] | None,
) -> bool:
    """Return whether Chat-facing tool descriptions may be enhanced."""
    if not isinstance(tool_adaptation, dict):
        return DEFAULT_ENABLE_TOOL_DESCRIPTION_OPTIMIZATION
    return bool(
        tool_adaptation.get(
            "enable_tool_description_optimization",
            DEFAULT_ENABLE_TOOL_DESCRIPTION_OPTIMIZATION,
        )
    )


def enable_phase_detection(tool_adaptation: dict[str, Any] | None) -> bool:
    """Return whether Chat→Responses streams should receive phase detection."""
    if not isinstance(tool_adaptation, dict):
        return DEFAULT_ENABLE_PHASE_DETECTION
    return bool(
        tool_adaptation.get(
            "enable_phase_detection",
            DEFAULT_ENABLE_PHASE_DETECTION,
        )
    )


def localize_code_editing_chat_request(
    body: dict[str, Any],
    *,
    store: CodexToolLocalizationStore | None = None,
    mappings: list[LocalizedToolMapping] | None = None,
    used_call_ids: set[str] | None = None,
    capabilities: NativeToolCapabilities | None = None,
) -> dict[str, Any]:
    """Replace Codex-native edit tools with Claude-Code-like Chat tools."""
    adapted = dict(body)
    tools = adapted.get("tools")
    removed_native = False
    native_capabilities = capabilities or NativeToolCapabilities.from_chat_tools(tools)
    read_cache = ReadOutputCache()

    if isinstance(tools, list):
        preserved_tools: list[Any] = []
        existing_names: set[str] = set()
        for tool in tools:
            name = _chat_tool_name(tool)
            if name in NATIVE_CODE_TOOL_NAMES:
                removed_native = True
                continue
            if name:
                existing_names.add(name)
            preserved_tools.append(tool)

        if removed_native or LOCALIZED_CODE_TOOL_NAMES.intersection(existing_names):
            localized_tools = [
                tool
                for tool in _localized_chat_tool_definitions()
                if _chat_tool_name(tool) not in existing_names
            ]
            adapted["tools"] = preserved_tools + localized_tools
            if _tool_choice_name(adapted.get("tool_choice")) in NATIVE_CODE_TOOL_NAMES:
                adapted["tool_choice"] = "auto"
            adapted[LOCALIZATION_CAPABILITIES_KEY] = native_capabilities.to_metadata()

    messages = adapted.get("messages")
    if isinstance(messages, list) and (store is not None or mappings):
        localized_messages: list[Any] = []
        for message in messages:
            localized = _localize_history_message(
                message,
                store,
                mappings,
                used_call_ids=used_call_ids,
            )
            _update_read_output_cache_from_message(localized, read_cache)
            localized_messages.append(localized)
        adapted["messages"] = localized_messages
        adapted[READ_OUTPUT_CACHE_KEY] = read_cache

    return adapted


def restore_localized_history_from_mappings(
    body: dict[str, Any],
    mappings: list[LocalizedToolMapping],
) -> tuple[dict[str, Any], set[str]]:
    """Restore localized history calls from persisted mappings.

    Returns the adapted body and the set of mapping call IDs that were used.
    """
    adapted = dict(body)
    messages = adapted.get("messages")
    if not isinstance(messages, list) or not mappings:
        return adapted, set()

    used_call_ids: set[str] = set()
    adapted["messages"] = [
        _localize_history_message(message, None, mappings, used_call_ids=used_call_ids)
        for message in messages
    ]
    return adapted, used_call_ids


def translate_localized_ir_response(
    ir_response: dict[str, Any],
    *,
    store: CodexToolLocalizationStore | None = None,
    capabilities: NativeToolCapabilities | None = None,
    read_cache: ReadOutputCache | None = None,
    use_apply_patch: bool = DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS,
) -> dict[str, Any]:
    """Translate localized IR tool calls in-place inside an IR response."""
    for choice in ir_response.get("choices", []):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_call":
                continue
            translated = translate_localized_tool_call_part(
                part,
                capabilities=capabilities,
                read_cache=read_cache,
                use_apply_patch=use_apply_patch,
            )
            if translated is None:
                continue
            part.clear()
            part.update(translated.part)
            if store is not None:
                store.remember(translated.mapping)
    return ir_response


@dataclass(frozen=True)
class TranslatedToolCall:
    """Translated Codex-native IR tool call plus reversible mapping metadata."""

    part: dict[str, Any]
    mapping: LocalizedToolMapping


def translate_localized_tool_call_part(
    part: dict[str, Any],
    *,
    capabilities: NativeToolCapabilities | None = None,
    read_cache: ReadOutputCache | None = None,
    use_apply_patch: bool = DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS,
) -> TranslatedToolCall | None:
    """Translate one localized IR tool_call part to a Codex-native tool call."""
    localized_name = part.get("tool_name", "")
    if localized_name not in LOCALIZED_CODE_TOOL_NAMES:
        return None

    call_id = part.get("tool_call_id", "")
    localized_input = _ensure_input_dict(part.get("tool_input"))
    if localized_input is None:
        return _error_translation(
            call_id,
            localized_name,
            {},
            f"{localized_name} arguments must be a JSON object.",
        )

    try:
        native_name, native_input, native_type = _localized_call_to_native(
            localized_name,
            localized_input,
            capabilities=capabilities or NativeToolCapabilities(),
            read_cache=read_cache,
            use_apply_patch=use_apply_patch,
        )
    except ValueError as exc:
        return _error_translation(call_id, localized_name, localized_input, str(exc))

    native_part = {
        "type": "tool_call",
        "tool_call_id": call_id,
        "tool_name": native_name,
        "tool_input": native_input,
        "tool_type": native_type,
    }
    if "tool_call_index" in part:
        native_part["tool_call_index"] = part["tool_call_index"]
    if "provider_metadata" in part:
        native_part["provider_metadata"] = part["provider_metadata"]

    return TranslatedToolCall(
        part=native_part,
        mapping=LocalizedToolMapping(
            call_id=call_id,
            localized_name=localized_name,
            localized_input=localized_input,
            native_name=native_name,
            native_input=native_input,
            native_type=native_type,
        ),
    )


class LocalizedToolCallStreamTransformer:
    """Buffer localized streaming tool calls and emit native Codex calls."""

    def __init__(
        self,
        *,
        store: CodexToolLocalizationStore | None = None,
        on_mapping: Any | None = None,
        capabilities: NativeToolCapabilities | None = None,
        read_cache: ReadOutputCache | None = None,
        use_apply_patch: bool = DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS,
    ) -> None:
        self._store = store
        self._on_mapping = on_mapping
        self._capabilities = capabilities or NativeToolCapabilities()
        self._use_apply_patch = use_apply_patch
        self._read_cache = read_cache
        self._pending: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def transform(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """Transform one IR stream event into zero or more IR events."""
        event_type = event.get("type")

        if (
            event_type == "tool_call_start"
            and event.get("tool_name") in LOCALIZED_CODE_TOOL_NAMES
        ):
            call_id = event.get("tool_call_id", "")
            if call_id:
                self._pending[call_id] = {
                    "start": dict(event),
                    "arguments": "",
                }
            return []

        if event_type == "tool_call_delta":
            call_id = event.get("tool_call_id", "")
            if call_id in self._pending:
                self._pending[call_id]["arguments"] += event.get("arguments_delta", "")
                return []

        if event_type in ("finish", "stream_end"):
            flushed = self.flush()
            return flushed + [event]

        return [event]

    def flush(self) -> list[dict[str, Any]]:
        """Flush buffered localized calls as Codex-native stream events."""
        events: list[dict[str, Any]] = []
        while self._pending:
            _call_id, buffered = self._pending.popitem(last=False)
            start = buffered["start"]
            part = {
                "type": "tool_call",
                "tool_call_id": start.get("tool_call_id", ""),
                "tool_name": start.get("tool_name", ""),
                "tool_input": _parse_stream_arguments(buffered["arguments"]),
            }
            if "tool_call_index" in start:
                part["tool_call_index"] = start["tool_call_index"]
            if "provider_metadata" in start:
                part["provider_metadata"] = start["provider_metadata"]

            translated = translate_localized_tool_call_part(
                part,
                capabilities=self._capabilities,
                read_cache=self._read_cache,
                use_apply_patch=self._use_apply_patch,
            )
            if translated is None:
                events.append(start)
                if buffered["arguments"]:
                    events.append(
                        {
                            "type": "tool_call_delta",
                            "tool_call_id": start.get("tool_call_id", ""),
                            "arguments_delta": buffered["arguments"],
                            **_copy_stream_indices(start),
                        }
                    )
                continue

            if self._store is not None:
                self._store.remember(translated.mapping)
            if self._on_mapping is not None:
                self._on_mapping(translated.mapping)
            native_part = translated.part
            start_event = {
                "type": "tool_call_start",
                "tool_call_id": native_part["tool_call_id"],
                "tool_name": native_part["tool_name"],
                "tool_type": native_part.get("tool_type", "function"),
                **_copy_stream_indices(start),
            }
            if "provider_metadata" in native_part:
                start_event["provider_metadata"] = native_part["provider_metadata"]
            events.append(start_event)

            events.append(
                {
                    "type": "tool_call_delta",
                    "tool_call_id": native_part["tool_call_id"],
                    "arguments_delta": _serialize_native_stream_arguments(native_part),
                    **_copy_stream_indices(start),
                }
            )
        return events


def generated_patch_for_edit(
    file_path: str,
    old_string: str,
    new_string: str,
) -> str:
    """Generate a Codex apply_patch update for one exact replacement."""
    return "\n".join(
        [
            "*** Begin Patch",
            f"*** Update File: {file_path}",
            "@@",
            *_prefixed_patch_lines(old_string, "-"),
            *_prefixed_patch_lines(new_string, "+"),
            "*** End Patch",
            "",
        ]
    )


def generated_patch_for_write(args: dict[str, Any]) -> str:
    """Generate a Codex apply_patch add-file patch for Write."""
    file_path = _required_string(args, "file_path", tool_name="Write")
    content = _required_string(args, "content", tool_name="Write")
    return "\n".join(
        [
            "*** Begin Patch",
            f"*** Add File: {file_path}",
            *_prefixed_patch_lines(content, "+"),
            "*** End Patch",
            "",
        ]
    )


def generated_apply_patch_heredoc_command(patch: str) -> str:
    """Generate a shell command that Codex exec_command can intercept."""
    marker = "PATCH"
    while marker in patch:
        marker += "_EOF"
    return f"apply_patch <<'{marker}'\n{patch}{marker}\n"


def generated_command_for_read(args: dict[str, Any]) -> str:
    """Generate a Codex exec_command shell command for Read."""
    file_path = _required_string(args, "file_path", tool_name="Read")
    offset = _optional_int(args, "offset")
    limit = _optional_int(args, "limit")
    script = (
        "from pathlib import Path\n"
        "import sys\n"
        "path = Path(sys.argv[1])\n"
        "offset = int(sys.argv[2]) if sys.argv[2] else None\n"
        "limit = int(sys.argv[3]) if sys.argv[3] else None\n"
        "text = path.read_text(encoding='utf-8')\n"
        "lines = text.splitlines(True)\n"
        "start = max((offset or 1) - 1, 0)\n"
        "end = None if limit is None else start + max(limit, 0)\n"
        "sys.stdout.write(''.join(lines[start:end]))\n"
    )
    return _python_command(
        script,
        file_path,
        "" if offset is None else offset,
        "" if limit is None else limit,
    )


def generated_command_for_glob(args: dict[str, Any]) -> str:
    """Generate a Codex exec_command shell command for Glob."""
    pattern = _required_string(args, "pattern", tool_name="Glob")
    path = str(args.get("path") or ".")
    script = (
        "import glob\n"
        "import os\n"
        "import sys\n"
        "pattern, base = sys.argv[1], sys.argv[2]\n"
        "query = pattern if os.path.isabs(pattern) else os.path.join(base, pattern)\n"
        "for item in sorted(glob.glob(query, recursive=True)):\n"
        "    print(item)\n"
    )
    return _python_command(script, pattern, path)


def generated_command_for_grep(args: dict[str, Any]) -> str:
    """Generate a Codex exec_command shell command for Grep."""
    pattern = _required_string(args, "pattern", tool_name="Grep")
    path = str(args.get("path") or ".")
    command: list[str] = ["rg", "--color=never"]

    output_mode = args.get("output_mode")
    if output_mode == "files_with_matches":
        command.append("--files-with-matches")
    elif output_mode == "count":
        command.append("--count")
    else:
        command.append("--line-number")

    if args.get("case_insensitive"):
        command.append("--ignore-case")
    if args.get("multiline"):
        command.append("--multiline")
    if args.get("glob"):
        command.extend(["-g", str(args["glob"])])
    if args.get("type"):
        command.extend(["-t", str(args["type"])])

    context = _optional_int(args, "context")
    before_context = _optional_int(args, "before_context")
    after_context = _optional_int(args, "after_context")
    if context is not None:
        command.extend(["-C", str(context)])
    if before_context is not None:
        command.extend(["-B", str(before_context)])
    if after_context is not None:
        command.extend(["-A", str(after_context)])

    command.extend(["--", pattern, path])
    rendered = " ".join(shlex.quote(part) for part in command)
    head_limit = _optional_int(args, "head_limit")
    if head_limit is not None and head_limit > 0:
        rendered += " | " + " ".join(["head", "-n", shlex.quote(str(head_limit))])
    return rendered


def generated_command_for_write(args: dict[str, Any]) -> str:
    """Generate a Codex exec_command shell command for Write."""
    file_path = _required_string(args, "file_path", tool_name="Write")
    content = _required_string(args, "content", tool_name="Write")
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    script = (
        "from pathlib import Path\n"
        "import base64\n"
        "import sys\n"
        "path = Path(sys.argv[1])\n"
        "content = base64.b64decode(sys.argv[2]).decode('utf-8')\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "path.write_text(content, encoding='utf-8')\n"
        "print(f'Wrote {len(content)} bytes to {path}')\n"
    )
    return _python_command(script, file_path, encoded)


def generated_command_for_replace_all(args: dict[str, Any]) -> str:
    """Generate a Codex exec_command shell command for Edit replace_all."""
    file_path = _required_string(args, "file_path", tool_name="Edit")
    old_string = _required_string(args, "old_string", tool_name="Edit")
    new_string = _required_string(args, "new_string", tool_name="Edit")
    old_encoded = base64.b64encode(old_string.encode("utf-8")).decode("ascii")
    new_encoded = base64.b64encode(new_string.encode("utf-8")).decode("ascii")
    script = (
        "from pathlib import Path\n"
        "import base64\n"
        "import sys\n"
        "path = Path(sys.argv[1])\n"
        "old = base64.b64decode(sys.argv[2]).decode('utf-8')\n"
        "new = base64.b64decode(sys.argv[3]).decode('utf-8')\n"
        "text = path.read_text(encoding='utf-8')\n"
        "count = text.count(old)\n"
        "if count == 0:\n"
        "    print(f'Edit failed: old_string was not found in {path}', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "path.write_text(text.replace(old, new), encoding='utf-8')\n"
        "print(f'Replaced {count} occurrence(s) in {path}')\n"
    )
    return _python_command(script, file_path, old_encoded, new_encoded)


def generated_command_for_edit_exact(args: dict[str, Any]) -> str:
    """Generate a Codex exec_command shell command for one exact Edit."""
    file_path = _required_string(args, "file_path", tool_name="Edit")
    old_string = _required_string(args, "old_string", tool_name="Edit")
    new_string = _required_string(args, "new_string", tool_name="Edit")
    old_encoded = base64.b64encode(old_string.encode("utf-8")).decode("ascii")
    new_encoded = base64.b64encode(new_string.encode("utf-8")).decode("ascii")
    script = (
        "from pathlib import Path\n"
        "import base64\n"
        "import sys\n"
        "path = Path(sys.argv[1])\n"
        "old = base64.b64decode(sys.argv[2]).decode('utf-8')\n"
        "new = base64.b64decode(sys.argv[3]).decode('utf-8')\n"
        "text = path.read_text(encoding='utf-8')\n"
        "count = text.count(old)\n"
        "if count == 0:\n"
        "    print(f'Edit failed: old_string was not found in {path}', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "if count > 1:\n"
        "    print(f'Edit failed: old_string matched {count} times in {path}', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "path.write_text(text.replace(old, new, 1), encoding='utf-8')\n"
        "print(f'Replaced 1 occurrence in {path}')\n"
    )
    return _python_command(script, file_path, old_encoded, new_encoded)


def _localized_call_to_native(
    localized_name: str,
    localized_input: dict[str, Any],
    *,
    capabilities: NativeToolCapabilities,
    read_cache: ReadOutputCache | None = None,
    use_apply_patch: bool = DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS,
) -> tuple[str, Any, str]:
    if localized_name == "Bash":
        command = _required_string(localized_input, "command", tool_name="Bash")
        tool_input: dict[str, Any] = {"cmd": command}
        timeout = _optional_int(localized_input, "timeout")
        if timeout is not None:
            tool_input["yield_time_ms"] = max(250, min(timeout, 30_000))
        if localized_input.get("run_in_background"):
            tool_input.setdefault("yield_time_ms", 1_000)
        return "exec_command", tool_input, "function"

    if localized_name == "Read":
        return (
            "exec_command",
            {
                "cmd": generated_command_for_read(localized_input),
                "yield_time_ms": 1_000,
                "max_output_tokens": 20_000,
            },
            "function",
        )

    if localized_name == "Glob":
        return (
            "exec_command",
            {
                "cmd": generated_command_for_glob(localized_input),
                "yield_time_ms": 1_000,
                "max_output_tokens": 20_000,
            },
            "function",
        )

    if localized_name == "Grep":
        return (
            "exec_command",
            {
                "cmd": generated_command_for_grep(localized_input),
                "yield_time_ms": 1_000,
                "max_output_tokens": 20_000,
            },
            "function",
        )

    if localized_name == "Edit":
        return _localized_edit_to_native(
            localized_input,
            capabilities=capabilities,
            read_cache=read_cache,
            use_apply_patch=use_apply_patch,
        )

    if localized_name == "Write":
        if use_apply_patch and capabilities.has_custom_apply_patch:
            return (
                "apply_patch",
                {"input": generated_patch_for_write(localized_input)},
                "custom",
            )
        if not use_apply_patch and capabilities.has_exec_command:
            return (
                "exec_command",
                {
                    "cmd": generated_command_for_write(localized_input),
                    "yield_time_ms": 1_000,
                    "max_output_tokens": 20_000,
                },
                "function",
            )
        if capabilities.has_exec_command:
            return (
                "exec_command",
                {
                    "cmd": generated_apply_patch_heredoc_command(
                        generated_patch_for_write(localized_input)
                    ),
                    "yield_time_ms": 1_000,
                    "max_output_tokens": 20_000,
                },
                "function",
            )
        raise ValueError("Write requires apply_patch or exec_command support.")

    raise ValueError(f"Unsupported localized tool: {localized_name}")


def _localized_edit_to_native(
    localized_input: dict[str, Any],
    *,
    capabilities: NativeToolCapabilities,
    read_cache: ReadOutputCache | None = None,
    use_apply_patch: bool = DEFAULT_USE_APPLY_PATCH_FOR_CODE_EDITS,
) -> tuple[str, Any, str]:
    if localized_input.get("replace_all"):
        return (
            "exec_command",
            {
                "cmd": generated_command_for_replace_all(localized_input),
                "yield_time_ms": 1_000,
                "max_output_tokens": 20_000,
            },
            "function",
        )
    file_path = _required_string(localized_input, "file_path", tool_name="Edit")
    old_string = _required_string(localized_input, "old_string", tool_name="Edit")
    new_string = _required_string(localized_input, "new_string", tool_name="Edit")
    if old_string == "":
        raise ValueError("Edit old_string must not be empty.")
    if read_cache is not None:
        expanded = read_cache.expand_edit(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
        )
        if expanded is not None:
            old_string, new_string = expanded
            localized_input = {
                **localized_input,
                "old_string": old_string,
                "new_string": new_string,
            }
    if not use_apply_patch:
        if capabilities.has_exec_command:
            return (
                "exec_command",
                {
                    "cmd": generated_command_for_edit_exact(localized_input),
                    "yield_time_ms": 1_000,
                    "max_output_tokens": 20_000,
                },
                "function",
            )
        raise ValueError("Edit requires exec_command support.")
    patch = generated_patch_for_edit(file_path, old_string, new_string)
    if not capabilities.has_custom_apply_patch:
        if capabilities.has_exec_command:
            return (
                "exec_command",
                {
                    "cmd": generated_apply_patch_heredoc_command(patch),
                    "yield_time_ms": 1_000,
                    "max_output_tokens": 20_000,
                },
                "function",
            )
        if capabilities.has_shell_command:
            return (
                "shell_command",
                {"command": generated_apply_patch_heredoc_command(patch)},
                "function",
            )
        raise ValueError("Edit requires apply_patch or exec_command support.")
    return (
        "apply_patch",
        {"input": patch},
        "custom",
    )


def _localized_chat_tool_definitions() -> list[dict[str, Any]]:
    return [
        _function_tool(
            "Read",
            "Read a UTF-8 text file. Use offset and limit for large files.",
            {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        ),
        _function_tool(
            "Edit",
            "Replace exact text in a file. old_string must match the raw file text exactly. Prefer replacing complete lines or complete consecutive line blocks, including indentation and unchanged surrounding text within those lines, rather than substrings.",
            {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["file_path", "old_string", "new_string"],
                "additionalProperties": False,
            },
        ),
        _function_tool(
            "Write",
            "Create a new UTF-8 text file with the provided full content.",
            {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
        ),
        _function_tool(
            "Glob",
            "Find files by glob pattern.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        ),
        _function_tool(
            "Grep",
            "Search file contents with ripgrep.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "type": {"type": "string"},
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files_with_matches", "count"],
                    },
                    "case_insensitive": {"type": "boolean"},
                    "line_numbers": {"type": "boolean"},
                    "before_context": {"type": "integer"},
                    "after_context": {"type": "integer"},
                    "context": {"type": "integer"},
                    "head_limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "multiline": {"type": "boolean"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        ),
        _function_tool(
            "Bash",
            "Run a shell command in the current workspace.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                    "description": {"type": "string"},
                    "run_in_background": {"type": "boolean"},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        ),
    ]


def _function_tool(
    name: str, description: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
        "strict": False,
    }


def _localize_history_message(
    message: Any,
    store: CodexToolLocalizationStore | None,
    mappings: list[LocalizedToolMapping] | None = None,
    *,
    used_call_ids: set[str] | None = None,
) -> Any:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return message
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return message

    changed = False
    localized_tool_calls: list[Any] = []
    for tool_call in tool_calls:
        localized = _localize_history_tool_call(
            tool_call,
            store,
            mappings,
            used_call_ids=used_call_ids,
        )
        changed = changed or localized is not tool_call
        localized_tool_calls.append(localized)
    if not changed:
        return message

    adapted = dict(message)
    adapted["tool_calls"] = localized_tool_calls
    return adapted


def _update_read_output_cache_from_message(
    message: Any,
    cache: ReadOutputCache,
) -> None:
    if not isinstance(message, dict):
        return
    if message.get("role") == "assistant":
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return
        for tool_call in tool_calls:
            read_call = _read_call_from_tool_call(tool_call)
            if read_call is not None:
                cache.remember_read_call(read_call.call_id, read_call.file_path)
                continue
            mutating_call = _mutating_call_from_tool_call(tool_call)
            if mutating_call is not None:
                cache.remember_mutating_call(
                    mutating_call.call_id,
                    mutating_call.file_path,
                )
        return

    if message.get("role") == "tool":
        call_id = str(message.get("tool_call_id") or "")
        content = _tool_message_text(message.get("content"))
        if call_id and content is not None:
            cache.remember_tool_output(call_id, content)


def _read_call_from_tool_call(tool_call: Any) -> ReadCall | None:
    if not isinstance(tool_call, dict):
        return None
    function = tool_call.get("function")
    if not isinstance(function, dict) or function.get("name") != "Read":
        return None
    try:
        args = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(args, dict) or not isinstance(args.get("file_path"), str):
        return None
    call_id = str(tool_call.get("id") or "")
    if not call_id:
        return None
    return ReadCall(call_id=call_id, file_path=args["file_path"])


def _mutating_call_from_tool_call(tool_call: Any) -> ReadCall | None:
    if not isinstance(tool_call, dict):
        return None
    function = tool_call.get("function")
    if not isinstance(function, dict) or function.get("name") not in {"Edit", "Write"}:
        return None
    try:
        args = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(args, dict) or not isinstance(args.get("file_path"), str):
        return None
    call_id = str(tool_call.get("id") or "")
    if not call_id:
        return None
    return ReadCall(call_id=call_id, file_path=args["file_path"])


def _tool_message_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return None


def _tool_output_indicates_failure(text: str) -> bool:
    lowered = text.lower()
    return (
        "exit code: 1" in lowered
        or "apply_patch verification failed" in lowered
        or "edit failed:" in lowered
        or "tool adaptation error:" in lowered
    )


def _localize_history_tool_call(
    tool_call: Any,
    store: CodexToolLocalizationStore | None,
    mappings: list[LocalizedToolMapping] | None = None,
    *,
    used_call_ids: set[str] | None = None,
) -> Any:
    if not isinstance(tool_call, dict):
        return tool_call
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return tool_call
    call_id = tool_call.get("id", "")
    mapping = store.get(call_id) if store is not None else None
    if mapping is None:
        mapping = _mapping_for_codex_tool_call(tool_call, mappings or [])
    if mapping is not None and used_call_ids is not None:
        used_call_ids.add(mapping.call_id)
    if mapping is None:
        name = function.get("name")
        if name != "exec_command":
            return tool_call
        try:
            native_args = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            return tool_call
        cmd = native_args.get("cmd")
        if not isinstance(cmd, str):
            return tool_call
        localized_name = "Bash"
        localized_input = {"command": cmd}
    else:
        localized_name = mapping.localized_name
        localized_input = mapping.localized_input

    adapted = dict(tool_call)
    adapted_function = dict(function)
    adapted_function["name"] = localized_name
    adapted_function["arguments"] = json.dumps(localized_input, ensure_ascii=False)
    adapted["function"] = adapted_function
    return adapted


def _mapping_for_codex_tool_call(
    tool_call: dict[str, Any],
    mappings: list[LocalizedToolMapping],
) -> LocalizedToolMapping | None:
    for mapping in mappings:
        if _tool_call_matches_mapping(tool_call, mapping):
            return mapping
    return None


def _tool_call_matches_mapping(
    tool_call: dict[str, Any],
    mapping: LocalizedToolMapping,
) -> bool:
    if tool_call.get("id") != mapping.call_id:
        return False
    function = tool_call.get("function")
    if not isinstance(function, dict) or function.get("name") != mapping.native_name:
        return False
    try:
        args = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError:
        return False
    return args == mapping.native_input


def _chat_tool_call(call_id: str, name: str, arguments: Any) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False)
            if isinstance(arguments, dict)
            else str(arguments),
        },
    }


def localized_mapping_from_tool_calls(
    original_tool_call: dict[str, Any],
    codex_tool_call: dict[str, Any],
) -> LocalizedToolMapping | None:
    """Rebuild a localized mapping from persisted Chat tool-call shapes."""
    original_function = original_tool_call.get("function")
    codex_function = codex_tool_call.get("function")
    if not isinstance(original_function, dict) or not isinstance(codex_function, dict):
        return None
    call_id = str(codex_tool_call.get("id") or original_tool_call.get("id") or "")
    if not call_id:
        return None
    localized_name = original_function.get("name")
    native_name = codex_function.get("name")
    if not isinstance(localized_name, str) or not isinstance(native_name, str):
        return None
    try:
        localized_input = json.loads(original_function.get("arguments") or "{}")
        native_input = json.loads(codex_function.get("arguments") or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(localized_input, dict):
        return None
    return LocalizedToolMapping(
        call_id=call_id,
        localized_name=localized_name,
        localized_input=localized_input,
        native_name=native_name,
        native_input=native_input,
        native_type="custom" if native_name == "apply_patch" else "function",
    )


def tool_call_cache_ttl_hours(tool_adaptation: dict[str, Any] | None) -> float:
    """Return the configured persistent tool-call mapping cache TTL in hours."""
    if not isinstance(tool_adaptation, dict):
        return DEFAULT_TOOL_CALL_CACHE_TTL_HOURS
    value = tool_adaptation.get("tool_call_cache_ttl_hours")
    try:
        ttl = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TOOL_CALL_CACHE_TTL_HOURS
    if ttl <= 0:
        return DEFAULT_TOOL_CALL_CACHE_TTL_HOURS
    return ttl


def _chat_tool_name(tool: Any) -> str | None:
    if not isinstance(tool, dict):
        return None
    if tool.get("type") == "function":
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return function["name"]
        if tool.get("name"):
            return tool["name"]
    return tool.get("name") or tool.get("type")


def _chat_tool_type(tool: Any) -> str | None:
    return tool.get("type") if isinstance(tool, dict) else None


def _tool_choice_name(tool_choice: Any) -> str | None:
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("tool_name"):
        return tool_choice["tool_name"]
    function = tool_choice.get("function")
    if isinstance(function, dict):
        return function.get("name")
    return tool_choice.get("name")


def _ensure_input_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _parse_stream_arguments(arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return {"raw_arguments": arguments}
    return parsed if isinstance(parsed, dict) else {"raw_arguments": arguments}


def _serialize_native_stream_arguments(native_part: dict[str, Any]) -> str:
    tool_input = native_part.get("tool_input", {})
    if native_part.get("tool_type") == "custom":
        if isinstance(tool_input, dict) and set(tool_input) == {"input"}:
            return str(tool_input["input"])
        return json.dumps(tool_input, ensure_ascii=False)
    return json.dumps(
        tool_input if isinstance(tool_input, dict) else {},
        ensure_ascii=False,
    )


def _copy_stream_indices(event: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "tool_call_index" in event:
        result["tool_call_index"] = event["tool_call_index"]
    if "choice_index" in event:
        result["choice_index"] = event["choice_index"]
    return result


def _error_translation(
    call_id: str,
    localized_name: str,
    localized_input: dict[str, Any],
    message: str,
) -> TranslatedToolCall:
    native_input = {
        "cmd": "printf '%s\\n' "
        + shlex.quote(f"Tool adaptation error: {message}")
        + " >&2; exit 1",
        "yield_time_ms": 1_000,
        "max_output_tokens": 2_000,
    }
    return TranslatedToolCall(
        part={
            "type": "tool_call",
            "tool_call_id": call_id,
            "tool_name": "exec_command",
            "tool_input": native_input,
            "tool_type": "function",
        },
        mapping=LocalizedToolMapping(
            call_id=call_id,
            localized_name=localized_name,
            localized_input=localized_input,
            native_name="exec_command",
            native_input=native_input,
            native_type="function",
        ),
    )


def _required_string(args: dict[str, Any], key: str, *, tool_name: str) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{tool_name} requires string field '{key}'.")
    return value


def _optional_int(args: dict[str, Any], key: str) -> int | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _prefixed_patch_lines(text: str, prefix: str) -> list[str]:
    if text == "":
        return [prefix]
    return [prefix + line for line in text.splitlines()]


def _unwrap_command_output(text: str) -> str:
    marker = "Output:\n"
    if marker in text:
        return text.split(marker, 1)[1]
    return text


def _line_expansion_candidates(
    text: str,
    *,
    old_string: str,
    new_string: str,
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    start = 0
    while True:
        index = text.find(old_string, start)
        if index < 0:
            return candidates

        before = text[index - 1] if index > 0 else "\n"
        after_index = index + len(old_string)
        after = text[after_index] if after_index < len(text) else "\n"
        if before == "\n" and after == "\n":
            start = index + 1
            continue

        line_start = text.rfind("\n", 0, index) + 1
        line_end = text.find("\n", after_index)
        if line_end < 0:
            line_end = len(text)
        full_old = text[line_start:line_end]
        if old_string in full_old:
            candidates.append((full_old, full_old.replace(old_string, new_string, 1)))
        start = index + 1


def _python_command(script: str, *args: Any) -> str:
    command = ["python3", "-c", script, *[str(arg) for arg in args]]
    return " ".join(shlex.quote(part) for part in command)
