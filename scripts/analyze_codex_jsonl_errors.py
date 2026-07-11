#!/usr/bin/env python3
"""Stream and classify errors from Codex session JSONL files.

The scanner deliberately reads one bounded line at a time.  It does not load
session files (or their request payloads) into memory, and it reports only
redacted, normalized error signatures rather than conversation content.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ROOTS = (
    Path("/Users/ibobby/.codex/archived_sessions"),
    Path("/Users/ibobby/.codex/sessions"),
    Path("/Volumes/Backups/AI Agent Sessions/Codex"),
)
ROLLOUT_TRACE_ROOT_ENV = "CODEX_ROLLOUT_TRACE_ROOT"
DEFAULT_MAX_LINE_BYTES = 1_048_576
DEFAULT_SAMPLE_LIMIT = 3
MAX_TEXT_CHARS = 12_000
MAX_TEXT_FIELDS_PER_RECORD = 256
SESSION_ID_RE = re.compile(
    r"(?<![0-9a-f])([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?![0-9a-f])",
    re.IGNORECASE,
)
ERROR_SIGNAL_RE = re.compile(
    r"\b(?:error|exception|traceback|failed|failure|fatal|timeout|timed out|"
    r"cancelled|canceled|interrupted|unauthori[sz]ed|forbidden|rate limit|"
    r"quota|overload(?:ed)?|unavailable|connection (?:reset|closed|lost|failed)|"
    r"disconnected|permission denied|"
    r"exit code\s*[1-9])\b|\b(?:HTTP\s*)?[45]\d\d\b",
    re.IGNORECASE,
)
STRONG_ERROR_RE = re.compile(
    r"(?:^|\n)\s*(?:fatal error|error|exception|traceback|failed|failure)\b|"
    r"\bHTTP\s*[45]\d\d\b|\b(?:wait timed out|js execution timed out|"
    r"connection reset|connection refused|permission denied|command not found|"
    r"no module named|computer use server error|write_stdin failed)\b",
    re.IGNORECASE,
)
ERROR_CONTAINER_RE = re.compile(r"(?:error|exception|fail)", re.IGNORECASE)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:authorization|api[ _-]?key|[a-z0-9_-]*token)\b\s*[:=]\s*)"
    r"(?:(['\"])(.*?)\2|([^,;\r\n]+))"
)
BEARER_RE = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
OPENAI_KEY_RE = re.compile(r"\b(?:sk|rk|sess)-[A-Za-z0-9_-]{8,}\b")
ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.-])/(?:[^\s'\"`]+/)+[^\s'\"`]+")
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
LONG_NUMBER_RE = re.compile(r"\b\d{4,}\b")
LINE_NUMBER_RE = re.compile(r"\bline\s+\d+\b", re.IGNORECASE)
ERROR_LEAF_NAMES = frozenset(
    {
        "error",
        "error_message",
        "message",
        "detail",
        "reason",
        "output",
        "stderr",
        "result",
        "status",
    }
)
TOOL_OUTPUT_TYPES = frozenset(
    {
        "function_call_output",
        "custom_tool_call_output",
        "tool_output",
        "tool_result",
        "tool_call_result",
    }
)


@dataclass(frozen=True)
class RetryAdvice:
    """Safe default action for one class of historical error."""

    retry: str
    key_rotation: str
    provider_failover: str


@dataclass(frozen=True)
class ErrorRule:
    """Classification and operational advice for one error category."""

    key: str
    label: str
    retry_advice: RetryAdvice


NO_RETRY = RetryAdvice("否", "否", "否")
RULES: dict[str, ErrorRule] = {
    "invalid_jsonl": ErrorRule("invalid_jsonl", "JSONL 损坏", NO_RETRY),
    "cancelled": ErrorRule("cancelled", "取消或中断", NO_RETRY),
    "stale_patch_context": ErrorRule(
        "stale_patch_context",
        "补丁上下文过期",
        RetryAdvice("是，重新读取目标片段后仅重试 1 次", "否", "否"),
    ),
    "local_permission": ErrorRule("local_permission", "本地权限或审批", NO_RETRY),
    "environment": ErrorRule("environment", "本地环境或命令", NO_RETRY),
    "agent_limit": ErrorRule("agent_limit", "Agent 执行限制", NO_RETRY),
    "tool_timeout": ErrorRule(
        "tool_timeout",
        "本地工具等待或执行超时",
        RetryAdvice("是，仅重新轮询/附着 1 次", "否", "否"),
    ),
    "external_tool": ErrorRule(
        "external_tool",
        "外部工具服务故障",
        RetryAdvice("视工具幂等性重开或附着 1 次", "否", "否"),
    ),
    "upstream_rate_limit": ErrorRule(
        "upstream_rate_limit",
        "上游限流 (429)",
        RetryAdvice(
            "是，遵守 Retry-After，指数退避最多 2 次",
            "可：仅确认限额为 key 级时",
            "是：同能力候选 provider",
        ),
    ),
    "upstream_quota": ErrorRule(
        "upstream_quota",
        "上游配额或账单耗尽",
        RetryAdvice("否", "可：仅确认限额为 key 级时", "是：同能力候选 provider"),
    ),
    "upstream_auth": ErrorRule(
        "upstream_auth",
        "上游认证或授权 (401/403)",
        RetryAdvice("否", "可：仅确认该 key 已失效时", "是：同能力候选 provider"),
    ),
    "upstream_capacity": ErrorRule(
        "upstream_capacity",
        "上游过载或 5xx",
        RetryAdvice("是，快速退避后最多 1 次", "否", "是：同能力候选 provider"),
    ),
    "upstream_connection": ErrorRule(
        "upstream_connection",
        "上游连接或超时",
        RetryAdvice("是，退避后最多 1 次", "否", "是：同能力候选 provider"),
    ),
    "model_unavailable": ErrorRule(
        "model_unavailable",
        "模型或部署不可用",
        RetryAdvice("否", "否", "仅显式 model fallback 映射"),
    ),
    "context_limit": ErrorRule("context_limit", "上下文长度限制", NO_RETRY),
    "content_policy": ErrorRule("content_policy", "内容或安全策略拒绝", NO_RETRY),
    "tool_contract": ErrorRule("tool_contract", "工具或协议兼容性", NO_RETRY),
    "request_contract": ErrorRule("request_contract", "请求参数或转换契约", NO_RETRY),
    "unknown": ErrorRule("unknown", "未归类错误", NO_RETRY),
}
CLASSIFICATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "stale_patch_context",
        re.compile(r"failed to find expected lines|invalid patch", re.IGNORECASE),
    ),
    ("cancelled", re.compile(r"\b(cancelled|canceled|interrupted|aborted)\b")),
    (
        "agent_limit",
        re.compile(r"\b(tool.?use limit|maximum turns|max[_ -]?turns|token limit)\b"),
    ),
    (
        "environment",
        re.compile(
            r"\b(command not found|no module named|modulenotfounderror|"
            r"executable file not found)\b"
        ),
    ),
    (
        "external_tool",
        re.compile(
            r"\b(computer use server error|browser server error|playwright.*(?:error|failed))\b"
        ),
    ),
    (
        "tool_timeout",
        re.compile(
            r"\b(wait timed out|js execution timed out|timeoutreached|kernel reset)\b"
        ),
    ),
    (
        "upstream_quota",
        re.compile(
            r"\b(insufficient[_ -]?quota|quota exceeded|billing|credit balance)\b"
        ),
    ),
    (
        "upstream_rate_limit",
        re.compile(r"\b(rate limit|too many requests|http\s*429)\b"),
    ),
    (
        "upstream_auth",
        re.compile(r"\bhttp\s*(401|403)\b|unauthori[sz]ed|invalid api key|forbidden"),
    ),
    (
        "context_limit",
        re.compile(
            r"\b(context length|context window|maximum context|max(?:imum)? tokens)\b"
        ),
    ),
    (
        "content_policy",
        re.compile(
            r"\b(content policy|safety (?:system )?blocked|moderation|content filter)\b"
        ),
    ),
    (
        "model_unavailable",
        re.compile(
            r"\b(model|deployment).{0,80}\b(not found|does not exist|unavailable)\b"
        ),
    ),
    (
        "upstream_capacity",
        re.compile(
            r"\bhttp\s*(500|501|502|503|504|529)\b|service unavailable|server error|overloaded|capacity"
        ),
    ),
    (
        "upstream_connection",
        re.compile(
            r"\b(timeout|timed out|connection (?:reset|refused|closed|lost|failed)|"
            r"disconnected|upstream closed|dns|econn|network is unreachable)\b"
        ),
    ),
    (
        "tool_contract",
        re.compile(
            r"\b(tool|function|apply_patch).{0,100}\b(unsupported|invalid|schema|grammar|not available)\b"
        ),
    ),
    (
        "request_contract",
        re.compile(
            r"\bhttp\s*(400|404|409|413|415|422)\b|invalid[_ -]?request|"
            r"validation error|unknown parameter|unsupported parameter"
        ),
    ),
)


@dataclass
class ScanStats:
    """Counters collected while scanning files without retaining their payloads."""

    files_discovered: int = 0
    files_selected: int = 0
    duplicate_files_skipped: int = 0
    missing_roots: list[str] = field(default_factory=list)
    unreadable_files: int = 0
    lines_seen: int = 0
    lines_parsed: int = 0
    malformed_lines: int = 0
    oversized_lines_skipped: int = 0


@dataclass
class ErrorGroup:
    """One stable signature, with bounded, non-sensitive occurrence samples."""

    category: str
    signature: str
    evidence: str
    provider_failover_eligible: bool
    count: int = 0
    roots: Counter[str] = field(default_factory=Counter)
    samples: list[dict[str, Any]] = field(default_factory=list)

    def add(self, root: str, path: Path, line: int, sample_limit: int) -> None:
        self.count += 1
        self.roots[root] += 1
        if len(self.samples) < sample_limit:
            self.samples.append({"file": str(path), "line": line})


@dataclass(frozen=True)
class CandidateFile:
    """A JSONL path with enough metadata to de-duplicate without opening it."""

    path: Path
    root: Path
    root_index: int
    size: int
    session_id: str | None
    source_kind: str


@dataclass(frozen=True)
class ErrorCandidate:
    """One bounded failure text plus the reliability of its event source."""

    text: str
    evidence: str
    provider_failover_eligible: bool = False


EVIDENCE_LABELS = {
    "structured_provider": "结构化 provider 事件",
    "session_terminal": "Codex session 终态错误",
    "session_transient": "Codex session 非终态流错误",
    "structured_runtime": "结构化运行时事件",
    "tool_output": "工具输出强信号",
}


def redact_text(value: str) -> str:
    """Remove common credential forms from a reportable message."""

    def replace_assignment(match: re.Match[str]) -> str:
        quote = match.group(2) or ""
        return f"{match.group(1)}{quote}<redacted>{quote}"

    value = SENSITIVE_ASSIGNMENT_RE.sub(replace_assignment, value)
    value = BEARER_RE.sub(r"\1<redacted>", value)
    return OPENAI_KEY_RE.sub("<redacted-key>", value)


def normalize_signature(value: str) -> str:
    """Make a safe, stable error signature suitable for aggregation."""

    value = redact_text(value)
    value = ABSOLUTE_PATH_RE.sub("<path>", value)
    value = UUID_RE.sub("<uuid>", value)
    value = LINE_NUMBER_RE.sub("line <n>", value)
    value = LONG_NUMBER_RE.sub("<n>", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:500]


def classify_error(value: str) -> ErrorRule:
    """Classify one error string without inferring that a retry is safe."""

    text = value.lower()
    if re.search(
        r"\b(approval required|permission denied|operation not permitted)\b", text
    ):
        if not re.search(r"\b(http\s*)?40[13]\b|api key|bearer|upstream", text):
            return RULES["local_permission"]
    for category, pattern in CLASSIFICATION_PATTERNS:
        if pattern.search(text):
            return RULES[category]
    return RULES["unknown"]


def _session_id_for_path(path: Path) -> str | None:
    match = SESSION_ID_RE.search(path.name)
    return match.group(1).lower() if match else None


def trace_roots_from_env() -> tuple[Path, ...]:
    """Return the opt-in raw rollout-trace root, separate from session roots."""

    value = os.environ.get(ROLLOUT_TRACE_ROOT_ENV, "").strip()
    return (Path(value).expanduser(),) if value else ()


def discover_jsonl_files(
    roots: Iterable[Path], stats: ScanStats, *, source_kind: str = "session"
) -> list[CandidateFile]:
    """Discover files using metadata only; JSONL contents are not opened here."""

    files: list[CandidateFile] = []
    for root_index, root in enumerate(roots):
        root = root.expanduser()
        if not root.exists():
            stats.missing_roots.append(str(root))
            continue
        if root.is_file():
            candidate_paths = [root] if root.suffix == ".jsonl" else []
        else:
            candidate_paths = []
            for directory, subdirs, names in os.walk(root, followlinks=False):
                subdirs.sort()
                for name in sorted(names):
                    if name.endswith(".jsonl"):
                        candidate_paths.append(Path(directory) / name)
        for path in candidate_paths:
            try:
                size = path.stat().st_size
            except OSError:
                stats.unreadable_files += 1
                continue
            files.append(
                CandidateFile(
                    path=path,
                    root=root,
                    root_index=root_index,
                    size=size,
                    session_id=_session_id_for_path(path),
                    source_kind=source_kind,
                )
            )
    stats.files_discovered += len(files)
    return files


def select_files(
    candidates: Iterable[CandidateFile],
    stats: ScanStats,
    *,
    include_duplicates: bool,
) -> list[CandidateFile]:
    """Select one largest copy of each UUID-named session unless requested otherwise."""

    candidates = list(candidates)
    if include_duplicates:
        selected = candidates
    else:
        chosen: dict[tuple[str, str], CandidateFile] = {}
        unique_without_session: list[CandidateFile] = []
        for candidate in candidates:
            if candidate.session_id is None:
                unique_without_session.append(candidate)
                continue
            dedup_key = (candidate.source_kind, candidate.session_id)
            existing = chosen.get(dedup_key)
            if existing is None or (candidate.size, -candidate.root_index) > (
                existing.size,
                -existing.root_index,
            ):
                chosen[dedup_key] = candidate
        selected = [*chosen.values(), *unique_without_session]
        stats.duplicate_files_skipped = len(candidates) - len(selected)
    selected.sort(key=lambda item: str(item.path))
    stats.files_selected = len(selected)
    return selected


def _read_bounded_lines(
    path: Path, max_line_bytes: int
) -> Iterator[tuple[int, bytes | None]]:
    """Yield one JSONL line at a time, yielding ``None`` for oversized lines."""

    with path.open("rb") as handle:
        line_number = 0
        while True:
            raw = handle.readline(max_line_bytes + 1)
            if not raw:
                return
            line_number += 1
            if len(raw) <= max_line_bytes or raw.endswith(b"\n"):
                yield line_number, raw
                continue
            while raw and not raw.endswith(b"\n"):
                raw = handle.readline(max_line_bytes + 1)
            yield line_number, None


def _collect_error_texts(record: Any, *, source_kind: str) -> list[ErrorCandidate]:
    """Extract failures using the parser assigned to this input root."""

    if source_kind == "trace":
        return _trace_error_texts(record)
    session_candidates = _session_error_texts(record)
    if session_candidates:
        return session_candidates
    if isinstance(record, dict) and record.get("schema_version") == 1:
        # A raw trace passed as a session root must not gain session authority.
        return []
    return _runtime_error_texts(record)


def _runtime_error_texts(record: Any) -> list[ErrorCandidate]:
    """Extract structured runtime/tool failures from ordinary session records."""

    found: list[ErrorCandidate] = []
    stack: list[tuple[str, Any, bool, bool, bool, int]] = [
        ("", record, False, False, False, 0)
    ]
    while stack and len(found) < MAX_TEXT_FIELDS_PER_RECORD:
        (
            field_name,
            value,
            in_error_container,
            in_failed_container,
            in_tool_output,
            depth,
        ) = stack.pop()
        if depth > 12:
            continue
        if isinstance(value, str):
            field_name = field_name.lower()
            if field_name not in ERROR_LEAF_NAMES:
                continue
            if field_name == "status" and in_failed_container:
                continue
            if field_name in {"error", "error_message"}:
                found.append(
                    ErrorCandidate(value[:MAX_TEXT_CHARS], "structured_runtime")
                )
            elif in_failed_container:
                found.append(
                    ErrorCandidate(value[:MAX_TEXT_CHARS], "structured_runtime")
                )
            elif in_error_container and ERROR_SIGNAL_RE.search(value):
                found.append(
                    ErrorCandidate(value[:MAX_TEXT_CHARS], "structured_runtime")
                )
            elif in_tool_output and STRONG_ERROR_RE.search(value):
                found.append(ErrorCandidate(value[:MAX_TEXT_CHARS], "tool_output"))
            continue
        if isinstance(value, dict):
            signals_failure = _dict_signals_failure(value)
            item_type = str(value.get("type", "")).lower()
            for key, child in value.items():
                key_text = str(key)
                stack.append(
                    (
                        key_text,
                        child,
                        in_error_container
                        or signals_failure
                        or bool(ERROR_CONTAINER_RE.search(key_text)),
                        in_failed_container or signals_failure,
                        in_tool_output or item_type in TOOL_OUTPUT_TYPES,
                        depth + 1,
                    )
                )
        elif isinstance(value, list):
            for child in value:
                stack.append(
                    (
                        field_name,
                        child,
                        in_error_container,
                        in_failed_container,
                        in_tool_output,
                        depth + 1,
                    )
                )
    return found


def _codex_error_info_text(value: Any) -> str | None:
    """Render only stable Codex error kind/status fields, never arbitrary payloads."""

    if isinstance(value, str) and value:
        return f"Codex error: {value.replace('_', ' ')}"
    if not isinstance(value, dict) or len(value) != 1:
        return None
    kind, details = next(iter(value.items()))
    if not isinstance(kind, str) or not kind:
        return None
    text = f"Codex error: {kind.replace('_', ' ')}"
    if isinstance(details, dict):
        status = details.get("http_status_code")
        if isinstance(status, int) and not isinstance(status, bool):
            text += f"; HTTP {status}"
    return text


def _codex_error_affects_turn_status(value: Any) -> bool:
    """Mirror the protocol variants that are explicitly non-terminal."""

    if isinstance(value, str):
        kind = value
    elif isinstance(value, dict) and len(value) == 1:
        kind = next(iter(value))
    else:
        return True
    return kind not in {"active_turn_not_steerable", "thread_rollback_failed"}


def _session_error_texts(record: Any) -> list[ErrorCandidate]:
    """Parse persisted Codex ``event_msg`` error records."""

    if not isinstance(record, dict) or record.get("type") != "event_msg":
        return []
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return []
    event_type = payload.get("type")
    if event_type not in {"error", "stream_error"}:
        return []

    parts: list[str] = []
    message = payload.get("message")
    if isinstance(message, str) and message:
        parts.append(message)
    additional_details = payload.get("additional_details")
    if (
        isinstance(additional_details, str)
        and additional_details
        and additional_details != message
    ):
        parts.append(additional_details)
    codex_error_info = payload.get("codex_error_info")
    structured_info = _codex_error_info_text(codex_error_info)
    if structured_info is not None:
        parts.append(structured_info)
    if not parts:
        return []

    is_terminal = event_type == "error"
    return [
        ErrorCandidate(
            "\n".join(parts)[:MAX_TEXT_CHARS],
            "session_terminal" if is_terminal else "session_transient",
            provider_failover_eligible=is_terminal
            and _codex_error_affects_turn_status(codex_error_info),
        )
    ]


def _trace_error_texts(record: Any) -> list[ErrorCandidate]:
    """Read direct provider failures from the versioned rollout-trace schema."""

    if not isinstance(record, dict):
        return []
    payload = record.get("payload")
    if not isinstance(payload, dict) or record.get("schema_version") != 1:
        return []
    event_type = payload.get("type")
    if event_type in {"inference_failed", "compaction_request_failed"}:
        error = payload.get("error")
        if isinstance(error, str) and error:
            return [
                ErrorCandidate(
                    error[:MAX_TEXT_CHARS],
                    "structured_provider",
                    provider_failover_eligible=True,
                )
            ]
    if event_type == "inference_cancelled":
        reason = payload.get("reason")
        if isinstance(reason, str) and reason:
            return [ErrorCandidate(reason[:MAX_TEXT_CHARS], "structured_provider")]
    if event_type in {"tool_call_runtime_ended", "tool_call_ended"}:
        status = payload.get("status")
        if isinstance(status, str) and status.lower() != "completed":
            return [
                ErrorCandidate(
                    f"Structured tool status: {status}", "structured_runtime"
                )
            ]
    if event_type in {"codex_turn_ended", "thread_ended", "rollout_ended"}:
        status = payload.get("status")
        if isinstance(status, str) and status.lower() != "completed":
            return [
                ErrorCandidate(
                    f"Structured execution status: {status}", "structured_runtime"
                )
            ]
    return []


def _dict_signals_failure(value: dict[Any, Any]) -> bool:
    """Return whether structured fields explicitly mark this record as failed."""

    if value.get("error") not in (None, "", False):
        return True
    if (
        value.get("timed_out") is True
        or value.get("success") is False
        or value.get("ok") is False
    ):
        return True
    exit_code = value.get("exit_code")
    if (
        isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code != 0
    ):
        return True
    item_type = value.get("type")
    if isinstance(item_type, str) and item_type.lower() in {
        "error",
        "failed",
        "failure",
    }:
        return True
    status = value.get("status")
    if isinstance(status, int) and status >= 400:
        return True
    return isinstance(status, str) and status.lower() in {
        "error",
        "failed",
        "failure",
        "cancelled",
        "canceled",
        "timed_out",
    }


def _malformed_signature(exc: Exception) -> str:
    return f"JSONL parse failed: {type(exc).__name__}"


def analyze_paths(
    roots: Iterable[Path],
    *,
    trace_roots: Iterable[Path] = (),
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    include_duplicates: bool = False,
) -> dict[str, Any]:
    """Return an aggregated, bounded-memory analysis of all discovered JSONL files."""

    if max_line_bytes <= 0:
        raise ValueError("max_line_bytes must be positive")
    if sample_limit < 0:
        raise ValueError("sample_limit cannot be negative")

    roots = tuple(Path(root).expanduser() for root in roots)
    trace_roots = tuple(Path(root).expanduser() for root in trace_roots)
    stats = ScanStats()
    candidates = [
        *discover_jsonl_files(roots, stats, source_kind="session"),
        *discover_jsonl_files(trace_roots, stats, source_kind="trace"),
    ]
    selected = select_files(candidates, stats, include_duplicates=include_duplicates)
    groups: dict[tuple[str, str, str, bool], ErrorGroup] = {}
    category_counts: Counter[str] = Counter()

    def add_error(
        rule: ErrorRule,
        text: str,
        evidence: str,
        provider_failover_eligible: bool,
        root: Path,
        path: Path,
        line: int,
    ) -> None:
        signature = normalize_signature(text)
        if not signature:
            return
        key = (rule.key, signature, evidence, provider_failover_eligible)
        group = groups.setdefault(
            key,
            ErrorGroup(rule.key, signature, evidence, provider_failover_eligible),
        )
        group.add(str(root), path, line, sample_limit)
        category_counts[rule.key] += 1

    for candidate in selected:
        try:
            for line_number, raw in _read_bounded_lines(candidate.path, max_line_bytes):
                stats.lines_seen += 1
                if raw is None:
                    stats.oversized_lines_skipped += 1
                    continue
                try:
                    record = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    stats.malformed_lines += 1
                    add_error(
                        RULES["invalid_jsonl"],
                        _malformed_signature(exc),
                        "structured_runtime",
                        False,
                        candidate.root,
                        candidate.path,
                        line_number,
                    )
                    continue
                stats.lines_parsed += 1
                unique_candidates = dict.fromkeys(
                    _collect_error_texts(record, source_kind=candidate.source_kind)
                )
                for error_candidate in unique_candidates:
                    add_error(
                        classify_error(error_candidate.text),
                        error_candidate.text,
                        error_candidate.evidence,
                        error_candidate.provider_failover_eligible,
                        candidate.root,
                        candidate.path,
                        line_number,
                    )
        except OSError:
            stats.unreadable_files += 1

    sorted_groups = sorted(
        groups.values(),
        key=lambda group: (-group.count, group.category, group.signature),
    )
    categories = [
        {
            "key": key,
            "label": RULES[key].label,
            "count": count,
            "retry_advice": asdict(RULES[key].retry_advice),
        }
        for key, count in sorted(
            category_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    provider_actionable_counts = {
        key: sum(
            group.count
            for group in groups.values()
            if group.provider_failover_eligible
            and group.category == key
            and RULES[key].retry_advice.provider_failover.startswith("是")
        )
        for key in RULES
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": [str(root) for root in roots],
        "trace_inputs": [str(root) for root in trace_roots],
        "scan": asdict(stats),
        "categories": categories,
        "structured_provider_failover_candidates": {
            key: count for key, count in provider_actionable_counts.items() if count
        },
        "error_groups": [
            {
                "category": group.category,
                "label": RULES[group.category].label,
                "count": group.count,
                "signature": group.signature,
                "evidence": group.evidence,
                "evidence_label": EVIDENCE_LABELS[group.evidence],
                "provider_failover_eligible": group.provider_failover_eligible,
                "roots": dict(group.roots),
                "samples": group.samples,
            }
            for group in sorted_groups
        ],
        "provider_failover_guardrails": [
            "仅在上游尚未向客户端输出任何响应字节时自动重试或切换 provider；否则会重复执行工具或写操作。",
            "对 POST/工具调用，必须有上游可接受的 idempotency key 或可证明的只读语义，才允许自动重放。",
            "候选 provider 必须由显式 route/model fallback 映射声明同等协议、模型能力、工具与上下文窗口；不得按名称盲切。",
            "按 provider+model 维护熔断状态，429/5xx/连接故障进入冷却；401/403、配额耗尽和契约错误不做普通重试。",
            "冷却中的高优先级 provider 应以低频、只读健康检查半开探测；连续成功后才恢复其优先级。",
        ],
    }


def render_markdown(report: dict[str, Any], *, top_groups: int = 20) -> str:
    """Render a compact report without exposing raw session conversations."""

    scan = report["scan"]
    lines = [
        "# Codex JSONL 错误汇总",
        "",
        "## 扫描范围",
        "",
        f"- 发现文件：{scan['files_discovered']}；实际扫描：{scan['files_selected']}；按 session UUID 去重跳过：{scan['duplicate_files_skipped']}",
        f"- 已解析行：{scan['lines_parsed']} / {scan['lines_seen']}；损坏行：{scan['malformed_lines']}；超限跳过：{scan['oversized_lines_skipped']}",
        f"- 不可读文件：{scan['unreadable_files']}",
        "- Raw rollout trace 根目录："
        + (", ".join(report["trace_inputs"]) if report["trace_inputs"] else "未启用"),
    ]
    if scan["missing_roots"]:
        lines.append(f"- 不存在的根目录：{', '.join(scan['missing_roots'])}")
    provider_candidates = report["structured_provider_failover_candidates"]
    lines.append(
        "- 可作为 Rosetta provider 故障切换依据的结构化上游失败："
        + (
            ", ".join(
                f"{RULES[key].label} {count}"
                for key, count in provider_candidates.items()
            )
            if provider_candidates
            else "0"
        )
    )
    lines.extend(
        [
            "",
            "## 错误分类与默认动作",
            "",
            "| 分类 | 次数 | 同 provider 重试 | key 轮换 | provider 故障切换 |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for category in report["categories"]:
        advice = category["retry_advice"]
        lines.append(
            f"| {category['label']} | {category['count']} | {advice['retry']} | "
            f"{advice['key_rotation']} | {advice['provider_failover']} |"
        )
    lines.extend(["", f"## 前 {top_groups} 个错误签名", ""])
    if not report["error_groups"]:
        lines.append("未发现满足错误信号规则的记录。")
    for group in report["error_groups"][:top_groups]:
        lines.append(
            f"- **{group['label']} × {group['count']}** [{group['evidence_label']}]："
            f"`{group['signature']}`"
        )
    lines.extend(["", "## Rosetta 自动切换边界", ""])
    lines.extend(f"- {item}" for item in report["provider_failover_guardrails"])
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        default=list(DEFAULT_ROOTS),
        help="Codex session JSONL 根目录或文件（默认扫描三处历史目录）",
    )
    parser.add_argument(
        "--trace-root",
        dest="trace_roots",
        action="append",
        type=Path,
        default=list(trace_roots_from_env()),
        help=(
            "raw rollout trace JSONL 根目录；默认读取 "
            f"{ROLLOUT_TRACE_ROOT_ENV}，与 session roots 分开解析"
        ),
    )
    parser.add_argument("--output", type=Path, help="将完整 JSON 报告写入此路径")
    parser.add_argument(
        "--max-line-bytes",
        type=int,
        default=DEFAULT_MAX_LINE_BYTES,
        help="单行 JSONL 的最大读取字节数；超过会跳过并计数",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=DEFAULT_SAMPLE_LIMIT,
        help="每个错误签名最多保留多少文件/行号样本",
    )
    parser.add_argument(
        "--include-duplicates",
        action="store_true",
        help="不按 session UUID 去重（通常会把备份重复计入）",
    )
    parser.add_argument(
        "--top-groups",
        type=int,
        default=20,
        help="标准输出中显示的签名数",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the scanner and optionally persist a machine-readable aggregate report."""

    args = _parse_args(argv)
    try:
        report = analyze_paths(
            args.roots,
            trace_roots=args.trace_roots,
            max_line_bytes=args.max_line_bytes,
            sample_limit=args.sample_limit,
            include_duplicates=args.include_duplicates,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(render_markdown(report, top_groups=max(args.top_groups, 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
