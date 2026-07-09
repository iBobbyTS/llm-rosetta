#!/usr/bin/env python3
"""Extract and compare Codex source contracts used by Codex-Rosetta."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "openai-codex-src"
DEFAULT_BASELINE = (
    Path(__file__).resolve().parents[1]
    / "version-compatibility"
    / "codex-source-contract.json"
)

HIGH_CONFIDENCE_CONTRACT_KEYS = {
    "apply_patch",
    "codex_header_constants",
    "endpoints",
    "responses_metadata_keys",
    "sse_event_names",
    "tool_spec_wire_types",
    "transport_constants",
    "websocket_client_metadata_keys",
}

HIGH_CONFIDENCE_DESCRIPTIONS = {
    "apply_patch": "tool 名称、format、syntax 与 grammar SHA-256 一致",
    "codex_header_constants": "已提取的 Codex HTTP header 名称和值一致",
    "endpoints": "已提取的 endpoint 常量一致",
    "responses_metadata_keys": "已提取的 turn metadata key 名称和值一致",
    "sse_event_names": "SSE parser 处理的 event 名称集合一致",
    "tool_spec_wire_types": "tool spec 的 serde wire type 映射一致",
    "transport_constants": "已提取的 transport 常量一致",
    "websocket_client_metadata_keys": "WebSocket client metadata key 一致",
}


class ContractExtractionError(RuntimeError):
    """Raised when a required Codex source contract cannot be extracted."""


def _read(source_root: Path, relative_path: str) -> str:
    path = source_root / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractExtractionError(
            f"cannot read required source file: {path}"
        ) from exc


def _find_declaration(text: str, declaration: str, name: str) -> int:
    pattern = re.compile(
        rf"\bpub(?:\(crate\))?\s+{re.escape(declaration)}\s+{re.escape(name)}\b"
    )
    match = pattern.search(text)
    if match is None:
        raise ContractExtractionError(f"missing {declaration} declaration: {name}")
    return match.start()


def _find_function(text: str, name: str) -> int:
    pattern = re.compile(rf"\bpub(?:\(crate\))?\s+fn\s+{re.escape(name)}\b")
    match = pattern.search(text)
    if match is None:
        raise ContractExtractionError(f"missing function declaration: {name}")
    return match.start()


def _raw_string_closer(text: str, index: int) -> tuple[str, int] | None:
    match = re.match(r'r(#+)?"', text[index:])
    if match is None:
        return None
    hashes = match.group(1) or ""
    return f'"{hashes}', match.end()


def _skip_line_comment(text: str, index: int) -> tuple[int, bool]:
    newline = text.find("\n", index)
    if newline < 0:
        return len(text), True
    return newline + 1, False


def _skip_block_comment(text: str, index: int, depth: int) -> tuple[int, int]:
    if text.startswith("/*", index):
        return index + 2, depth + 1
    if text.startswith("*/", index):
        return index + 2, depth - 1
    return index + 1, depth


def _skip_raw_string(text: str, index: int, closer: str) -> tuple[int, str | None]:
    close_index = text.find(closer, index)
    if close_index < 0:
        return len(text), closer
    return close_index + len(closer), None


def _skip_string(text: str, index: int) -> tuple[int, bool]:
    if text[index] == "\\":
        return index + 2, True
    if text[index] == '"':
        return index + 1, False
    return index + 1, True


def _matching_brace(text: str, open_index: int) -> int:
    depth = 1
    index = open_index + 1
    block_comment_depth = 0
    in_line_comment = False
    in_string = False
    raw_closer: str | None = None

    while index < len(text):
        if in_line_comment:
            index, in_line_comment = _skip_line_comment(text, index)
            continue

        if block_comment_depth:
            index, block_comment_depth = _skip_block_comment(
                text, index, block_comment_depth
            )
            continue

        if raw_closer is not None:
            index, raw_closer = _skip_raw_string(text, index, raw_closer)
            continue

        if in_string:
            index, in_string = _skip_string(text, index)
            continue

        if text.startswith("//", index):
            in_line_comment = True
            index += 2
            continue
        if text.startswith("/*", index):
            block_comment_depth = 1
            index += 2
            continue

        raw = _raw_string_closer(text, index)
        if raw is not None:
            raw_closer, consumed = raw
            index += consumed
            continue

        character = text[index]
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1

    raise ContractExtractionError("unterminated braced source block")


def _braced_block(text: str, declaration_index: int) -> str:
    open_index = text.find("{", declaration_index)
    if open_index < 0:
        raise ContractExtractionError("declaration has no opening brace")
    close_index = _matching_brace(text, open_index)
    return text[open_index + 1 : close_index]


def _struct_fields(text: str, name: str) -> list[str]:
    block = _braced_block(text, _find_declaration(text, "struct", name))
    fields = re.findall(
        r"^    pub(?:\(crate\))?\s+(?:r#)?([a-zA-Z_][a-zA-Z0-9_]*)\s*:",
        block,
        flags=re.MULTILINE,
    )
    if not fields:
        raise ContractExtractionError(f"no public fields extracted from struct: {name}")
    return sorted(set(fields))


def _enum_variants(text: str, name: str) -> list[str]:
    block = _braced_block(text, _find_declaration(text, "enum", name))
    variants = re.findall(r"^    ([A-Z][a-zA-Z0-9_]*)\b", block, flags=re.MULTILINE)
    if not variants:
        raise ContractExtractionError(f"no variants extracted from enum: {name}")
    return sorted(set(variants))


def _serde_enum_wire_types(text: str, name: str) -> dict[str, str]:
    block = _braced_block(text, _find_declaration(text, "enum", name))
    pairs = re.findall(
        r'#\[serde\(rename\s*=\s*"([^"]+)"\)\]\s*([A-Z][a-zA-Z0-9_]*)',
        block,
    )
    if not pairs:
        raise ContractExtractionError(
            f"no serde wire types extracted from enum: {name}"
        )
    return dict(sorted(pairs))


def _string_constants(text: str, name_pattern: str) -> dict[str, str]:
    constant_pattern = re.compile(
        rf"(?:pub(?:\(crate\))?\s+)?const\s+({name_pattern})\s*:\s*&str\s*=\s*\"([^\"]+)\"\s*;",
        flags=re.MULTILINE,
    )
    return dict(sorted(constant_pattern.findall(text)))


def _sse_event_names(text: str) -> list[str]:
    block = _braced_block(text, _find_function(text, "process_responses_event"))
    events = re.findall(r'^\s*"(response\.[^"]+)"\s*=>', block, flags=re.MULTILINE)
    if not events:
        raise ContractExtractionError("no Responses SSE event names extracted")
    return sorted(set(events))


def _required_string(text: str, pattern: str, description: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise ContractExtractionError(f"missing {description}")
    return match.group(1)


def _git_commit(source_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ContractExtractionError(
            f"cannot resolve Codex source commit from {source_root}"
        )
    return completed.stdout.strip()


def extract_contract(source_root: Path) -> dict[str, Any]:
    """Extract the Codex source contracts that Codex-Rosetta depends on."""
    common = _read(source_root, "codex-rs/codex-api/src/common.rs")
    sse = _read(source_root, "codex-rs/codex-api/src/sse/responses.rs")
    models = _read(source_root, "codex-rs/protocol/src/models.rs")
    openai_models = _read(source_root, "codex-rs/protocol/src/openai_models.rs")
    config_types = _read(source_root, "codex-rs/protocol/src/config_types.rs")
    protocol = _read(source_root, "codex-rs/protocol/src/protocol.rs")
    tool_spec = _read(source_root, "codex-rs/tools/src/tool_spec.rs")
    client = _read(source_root, "codex-rs/core/src/client.rs")
    responses_metadata = _read(source_root, "codex-rs/core/src/responses_metadata.rs")
    apply_patch_spec = _read(
        source_root, "codex-rs/core/src/tools/handlers/apply_patch_spec.rs"
    )
    apply_patch_grammar = _read(
        source_root, "codex-rs/core/src/tools/handlers/apply_patch.lark"
    )

    header_constants = _string_constants(
        client,
        r"(?:OPENAI_BETA_HEADER|X_CODEX_[A-Z0-9_]+_HEADER|X_OPENAI_[A-Z0-9_]+_HEADER|X_RESPONSESAPI_[A-Z0-9_]+_HEADER)",
    )
    if "X_CODEX_WINDOW_ID_HEADER" not in header_constants:
        raise ContractExtractionError("missing x-codex-window-id header constant")

    websocket_metadata_keys = _string_constants(
        common, r"WS_REQUEST_HEADER_[A-Z0-9_]+_CLIENT_METADATA_KEY"
    )
    websocket_metadata_keys.update(
        _string_constants(
            client,
            r"(?:WS_REQUEST_HEADER_[A-Z0-9_]+_CLIENT_METADATA_KEY|X_CODEX_WS_[A-Z0-9_]+_CLIENT_METADATA_KEY)",
        )
    )

    return {
        "apply_patch": {
            "format_type": _required_string(
                apply_patch_spec,
                r'r#type:\s*"([^"]+)"\.to_string\(\)',
                "apply_patch format type",
            ),
            "grammar_sha256": hashlib.sha256(
                apply_patch_grammar.encode("utf-8")
            ).hexdigest(),
            "name": _required_string(
                apply_patch_spec,
                r'name:\s*"([^"]+)"\.to_string\(\)',
                "apply_patch tool name",
            ),
            "syntax": _required_string(
                apply_patch_spec,
                r'syntax:\s*"([^"]+)"\.to_string\(\)',
                "apply_patch grammar syntax",
            ),
        },
        "codex_header_constants": header_constants,
        "compaction_input_fields": _struct_fields(common, "CompactionInput"),
        "content_item_variants": _enum_variants(models, "ContentItem"),
        "endpoints": _string_constants(client, r"[A-Z0-9_]+_ENDPOINT"),
        "message_phase_variants": _enum_variants(models, "MessagePhase"),
        "model_enum_variants": {
            "ApplyPatchToolType": _enum_variants(openai_models, "ApplyPatchToolType"),
            "ConfigShellToolType": _enum_variants(openai_models, "ConfigShellToolType"),
            "InputModality": _enum_variants(openai_models, "InputModality"),
            "ModelVisibility": _enum_variants(openai_models, "ModelVisibility"),
            "MultiAgentVersion": _enum_variants(protocol, "MultiAgentVersion"),
            "ReasoningEffort": _enum_variants(openai_models, "ReasoningEffort"),
            "ReasoningSummary": _enum_variants(config_types, "ReasoningSummary"),
            "ToolMode": _enum_variants(openai_models, "ToolMode"),
            "TruncationMode": _enum_variants(openai_models, "TruncationMode"),
            "Verbosity": _enum_variants(config_types, "Verbosity"),
            "WebSearchToolType": _enum_variants(openai_models, "WebSearchToolType"),
        },
        "model_info_fields": _struct_fields(openai_models, "ModelInfo"),
        "reasoning_fields": _struct_fields(common, "Reasoning"),
        "response_create_ws_request_fields": _struct_fields(
            common, "ResponseCreateWsRequest"
        ),
        "response_event_variants": _enum_variants(common, "ResponseEvent"),
        "response_input_item_variants": _enum_variants(models, "ResponseInputItem"),
        "response_item_variants": _enum_variants(models, "ResponseItem"),
        "responses_api_request_fields": _struct_fields(common, "ResponsesApiRequest"),
        "responses_metadata_keys": _string_constants(
            responses_metadata, r"[A-Z0-9_]+_KEY"
        ),
        "sse_event_names": _sse_event_names(sse),
        "stream_options_fields": _struct_fields(common, "StreamOptions"),
        "tool_spec_wire_types": _serde_enum_wire_types(tool_spec, "ToolSpec"),
        "transport_constants": _string_constants(
            client, r"RESPONSES_WEBSOCKETS_[A-Z0-9_]+_HEADER_VALUE"
        ),
        "websocket_client_metadata_keys": dict(sorted(websocket_metadata_keys.items())),
    }


def build_snapshot(source_root: Path) -> dict[str, Any]:
    """Build a versioned contract snapshot for one Codex source checkout."""
    source_root = source_root.resolve()
    return {
        "schema_version": SCHEMA_VERSION,
        "codex_source_commit": _git_commit(source_root),
        "contract": extract_contract(source_root),
    }


def snapshot_json(snapshot: dict[str, Any]) -> str:
    """Serialize a contract snapshot deterministically."""
    return json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def compare_snapshots(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    check_source_commit: bool = True,
) -> str:
    """Return a unified diff, or an empty string when snapshots match."""
    expected = dict(baseline)
    observed = dict(current)
    if not check_source_commit:
        expected.pop("codex_source_commit", None)
        observed.pop("codex_source_commit", None)
    return "".join(
        difflib.unified_diff(
            snapshot_json(expected).splitlines(keepends=True),
            snapshot_json(observed).splitlines(keepends=True),
            fromfile="baseline",
            tofile="current",
        )
    )


def classify_snapshots(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    check_source_commit: bool = True,
) -> dict[str, list[str]]:
    """Classify every checked contract group by confidence and observed drift."""
    high_confidence: list[str] = []
    possibly_unchanged: list[str] = []
    changed: list[str] = []

    baseline_commit = baseline.get("codex_source_commit")
    current_commit = current.get("codex_source_commit")
    if baseline_commit == current_commit:
        high_confidence.append(f"codex_source_commit: {current_commit}")
    else:
        ignored = "（已忽略，不影响退出码）" if not check_source_commit else ""
        changed.append(
            "codex_source_commit: "
            f"{baseline_commit or '<missing>'} -> {current_commit or '<missing>'}{ignored}"
        )

    baseline_contract = baseline.get("contract")
    current_contract = current.get("contract")
    if not isinstance(baseline_contract, dict) or not isinstance(
        current_contract, dict
    ):
        changed.append("contract: baseline 或 current 不是对象")
        return {
            "high_confidence_unchanged": high_confidence,
            "possibly_unchanged": possibly_unchanged,
            "changed": changed,
        }

    for key in sorted(baseline_contract.keys() | current_contract.keys()):
        path = f"contract.{key}"
        if key not in baseline_contract:
            changed.append(f"{path}: 新增检查项（见详细 diff）")
            continue
        if key not in current_contract:
            changed.append(f"{path}: 当前源码未提取到该检查项（见详细 diff）")
            continue
        if baseline_contract[key] != current_contract[key]:
            changed.append(f"{path}: 已提取值发生变化（见详细 diff）")
            continue
        if key in HIGH_CONFIDENCE_CONTRACT_KEYS:
            description = HIGH_CONFIDENCE_DESCRIPTIONS[key]
            high_confidence.append(f"{path}: {description}")
        else:
            possibly_unchanged.append(
                f"{path}: 已提取的名称/成员集合一致；"
                "类型、默认值或 serde 语义尚未完整覆盖"
            )

    return {
        "high_confidence_unchanged": high_confidence,
        "possibly_unchanged": possibly_unchanged,
        "changed": changed,
    }


def render_classification(classification: dict[str, list[str]]) -> str:
    """Render a stable three-section compatibility report."""
    sections = (
        ("高置信度没有变化的", "high_confidence_unchanged"),
        ("可能没有变化的", "possibly_unchanged"),
        ("有变化的", "changed"),
    )
    lines: list[str] = []
    for title, key in sections:
        lines.append(f"{title}：")
        entries = classification.get(key, [])
        if entries:
            lines.extend(f"  - {entry}" for entry in entries)
        else:
            lines.append("  - 无")
    return "\n".join(lines)


def _load_baseline(path: Path) -> dict[str, Any]:
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractExtractionError(f"cannot read contract baseline: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractExtractionError(
            f"invalid contract baseline JSON: {path}"
        ) from exc
    if baseline.get("schema_version") != SCHEMA_VERSION:
        raise ContractExtractionError(
            f"unsupported contract schema version in {path}: "
            f"{baseline.get('schema_version')!r}"
        )
    return baseline


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--ignore-source-commit",
        action="store_true",
        help="compare extracted contracts without requiring the same source commit",
    )
    parser.add_argument(
        "--print-current",
        action="store_true",
        help="print the current extracted snapshot instead of comparing it",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="replace the baseline after reviewing the Codex source changes",
    )
    return parser.parse_args()


def main() -> int:
    """Run the contract extractor and baseline comparison CLI."""
    args = _parse_args()
    try:
        current = build_snapshot(args.source)
        if args.print_current:
            sys.stdout.write(snapshot_json(current))
            return 0
        if args.write_baseline:
            args.baseline.parent.mkdir(parents=True, exist_ok=True)
            args.baseline.write_text(snapshot_json(current), encoding="utf-8")
            print(f"Updated Codex contract baseline: {args.baseline}")
            return 0

        baseline = _load_baseline(args.baseline)
        classification = classify_snapshots(
            baseline,
            current,
            check_source_commit=not args.ignore_source_commit,
        )
        print(render_classification(classification), flush=True)
        diff = compare_snapshots(
            baseline,
            current,
            check_source_commit=not args.ignore_source_commit,
        )
        if diff:
            print("\n详细 diff：", file=sys.stderr)
            sys.stderr.write(diff)
            return 1
        print(
            "结论：未发现会阻断兼容性检查的变化 "
            f"({current['codex_source_commit'][:12]})."
        )
        return 0
    except ContractExtractionError as exc:
        print(f"Codex compatibility check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
