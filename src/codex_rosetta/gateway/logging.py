"""Logging utilities for codex-rosetta gateway.

Provides colorized, loguru-style output with configurable request/response body
logging, truncation, and sanitization.  Ported from argo-proxy's logger module.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from codex_rosetta.observability.redaction import REDACTED, SecretRedactor


# ---------------------------------------------------------------------------
# ANSI colour codes
# ---------------------------------------------------------------------------


class Colors:
    """ANSI colour codes for terminal colourisation."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Foreground
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright foreground
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


# Level-specific colours (matching loguru style)
LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: Colors.BLUE,
    logging.INFO: Colors.BRIGHT_WHITE,
    logging.WARNING: Colors.YELLOW,
    logging.ERROR: Colors.RED,
    logging.CRITICAL: Colors.BRIGHT_RED + Colors.BOLD,
}

LEVEL_NAME_COLORS: dict[int, str] = {
    logging.DEBUG: Colors.CYAN,
    logging.INFO: Colors.GREEN,
    logging.WARNING: Colors.YELLOW,
    logging.ERROR: Colors.RED,
    logging.CRITICAL: Colors.BRIGHT_RED + Colors.BOLD,
}

LEVEL_NAMES: dict[int, str] = {
    logging.DEBUG: "DEBUG   ",
    logging.INFO: "INFO    ",
    logging.WARNING: "WARNING ",
    logging.ERROR: "ERROR   ",
    logging.CRITICAL: "CRITICAL",
}


# ---------------------------------------------------------------------------
# Colour detection
# ---------------------------------------------------------------------------


def _supports_color() -> bool:
    """Check if the terminal supports colour output."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stderr, "isatty"):
        return False
    if not sys.stderr.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term == "dumb":
        return False
    return True


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


class ColoredFormatter(logging.Formatter):
    """Loguru-style coloured formatter: ``YYYY-MM-DD HH:MM:SS.mmm | LEVEL | msg``."""

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        use_colors: bool = True,
    ) -> None:
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors and _supports_color()

    def formatTime(  # noqa: N802
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        """Format timestamp with millisecond precision."""
        import datetime

        ct = datetime.datetime.fromtimestamp(record.created)
        return ct.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(record.msecs):03d}"

    def format(self, record: logging.LogRecord) -> str:
        record = logging.makeLogRecord(record.__dict__)
        timestamp = self.formatTime(record, self.datefmt)
        level_name = LEVEL_NAMES.get(record.levelno, "UNKNOWN ")
        level_name_color = LEVEL_NAME_COLORS.get(record.levelno, Colors.WHITE)
        message_color = LEVEL_COLORS.get(record.levelno, Colors.WHITE)

        if self.use_colors:
            formatted = (
                f"{Colors.GREEN}{timestamp}{Colors.RESET} | "
                f"{level_name_color}{Colors.BOLD}{level_name}{Colors.RESET} | "
                f"{message_color}{record.getMessage()}{Colors.RESET}"
            )
        else:
            formatted = f"{timestamp} | {level_name} | {record.getMessage()}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                if self.use_colors:
                    formatted += f"\n{Colors.RED}{record.exc_text}{Colors.RESET}"
                else:
                    formatted += f"\n{record.exc_text}"

        return formatted


class StatsStreamHandler(logging.StreamHandler):
    """Render per-model request counts on one reusable terminal line."""

    def __init__(self, stream: Any = None, *, stats_enabled: bool = False) -> None:
        super().__init__(stream)
        self.stats_enabled = stats_enabled
        self._model_counts: dict[str, int] = {}
        self._stats_line_active = False

    def record_request(self, model: str) -> None:
        """Increment one model count and redraw the shared stats line."""
        if not self.stats_enabled:
            return
        safe_model = _single_line(str(model)).strip() or "<unknown>"
        self.acquire()
        try:
            self._model_counts[safe_model] = self._model_counts.get(safe_model, 0) + 1
            summary = ", ".join(
                f"{name}: {count}" for name, count in self._model_counts.items()
            )
            self.stream.write(f"\r{summary}")
            self.flush()
            self._stats_line_active = True
        except Exception:
            # Terminal stats are an optional side channel.  A closed pipe or
            # logging driver must never replace a successful proxy response.
            self.stats_enabled = False
            self._stats_line_active = False
        finally:
            self.release()

    def emit(self, record: logging.LogRecord) -> None:
        """Move ordinary records below an active stats line before emitting."""
        try:
            if self._stats_line_active:
                self.stream.write(self.terminator)
                self._stats_line_active = False
            super().emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """Terminate an active stats line before the terminal is released."""
        self.acquire()
        try:
            if self._stats_line_active:
                try:
                    self.stream.write(self.terminator)
                    self.flush()
                except Exception:
                    self.stats_enabled = False
                finally:
                    self._stats_line_active = False
        finally:
            self.release()
        try:
            super().close()
        except Exception:
            # Handler shutdown is best-effort for the same reason as redraws.
            pass


# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

_handler: logging.Handler | None = None
_logger: logging.Logger = logging.getLogger("codex-rosetta-gateway")
_logger.setLevel(logging.INFO)
_logger.propagate = False
_body_logger: logging.Logger = logging.getLogger("codex-rosetta-gateway.body")
_body_logger.setLevel(logging.DEBUG)
_body_logger.propagate = True

UPSTREAM_ERROR_MAX_CHARS = 4096
BODY_LOG_MAX_CHARS = 20_000
_TOKEN_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:authorization|api[ _-]?key|[a-z0-9_-]*token)\b\s*[:=]\s*)"
    r"(?:(['\"])(.*?)\2|([^,;\r\n]+))"
)


def _redact_token_assignments(value: str) -> str:
    """Redact explicit auth/token assignments without hiding other secrets."""

    def _replace(match: re.Match[str]) -> str:
        quote = match.group(2) or ""
        return f"{match.group(1)}{quote}{REDACTED}{quote}"

    return _TOKEN_ASSIGNMENT_RE.sub(_replace, value)


def _single_line(value: str) -> str:
    """Escape control and line-separator characters for log-safe output."""
    escaped: list[str] = []
    for char in value:
        code = ord(char)
        if char == "\n":
            escaped.append(r"\n")
        elif char == "\r":
            escaped.append(r"\r")
        elif char == "\t":
            escaped.append(r"\t")
        elif code < 0x20 or 0x7F <= code <= 0x9F or code in (0x2028, 0x2029):
            escaped.append(f"\\u{code:04x}")
        else:
            escaped.append(char)
    return "".join(escaped)


class UpstreamErrorLogState:
    """Per-app token redactor and output policy for upstream error logs."""

    def __init__(
        self,
        token_values: Iterable[str] = (),
        *,
        max_chars: int = UPSTREAM_ERROR_MAX_CHARS,
    ) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars
        self._redactor = SecretRedactor(token_values)

    def prepare_update(self, token_values: Iterable[str]) -> SecretRedactor:
        """Build a replacement redactor without mutating live state."""
        return SecretRedactor(token_values)

    def commit_update(self, redactor: SecretRedactor) -> None:
        """Swap in a prepared token redactor."""
        self._redactor = redactor

    def sanitize(self, error_text: Any) -> str:
        """Return redacted, single-line text bounded by ``max_chars``."""
        text = error_text if isinstance(error_text, str) else str(error_text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError, TypeError, ValueError:
            safe = self._redactor.redact(text)
            safe = _redact_token_assignments(safe)
        else:
            safe = json.dumps(
                self._redactor.redact(parsed),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        safe = _single_line(safe)
        if len(safe) <= self.max_chars:
            return safe
        suffix = "...[truncated]"
        if self.max_chars <= len(suffix):
            return suffix[: self.max_chars]
        return f"{safe[: self.max_chars - len(suffix)]}{suffix}"


@dataclass(frozen=True)
class PreparedBodyLogConfig:
    """Immutable per-app body-log policy ready for an assignment-only commit."""

    enabled: bool
    redactor: SecretRedactor


class BodyLogState:
    """Per-app opt-in policy for token-safe request and response body logs."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        token_values: Iterable[str] = (),
        max_chars: int = BODY_LOG_MAX_CHARS,
    ) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars
        self.enabled = False
        self._redactor = SecretRedactor()
        self.commit_update(
            self.prepare_update(enabled=enabled, token_values=token_values)
        )

    def prepare_update(
        self,
        *,
        enabled: bool,
        token_values: Iterable[str],
    ) -> PreparedBodyLogConfig:
        """Build a replacement body-log policy without mutating live state."""
        return PreparedBodyLogConfig(
            enabled=bool(enabled),
            redactor=SecretRedactor(token_values),
        )

    def commit_update(self, prepared: PreparedBodyLogConfig) -> None:
        """Commit a prepared body-log policy using assignment-only operations."""
        self.enabled = prepared.enabled
        self._redactor = prepared.redactor

    def render(self, value: Any) -> str:
        """Redact, serialize, single-line, and bound one body without raw fallback."""
        try:
            redacted = self._redactor.redact(value)
        except Exception:
            return "[body redaction failed]"
        try:
            serialized = json.dumps(
                redacted,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except Exception:
            return "[body serialization failed]"
        safe = _single_line(serialized)
        suffix = "...[truncated]"
        if len(safe) <= self.max_chars:
            return safe
        if self.max_chars <= len(suffix):
            return suffix[: self.max_chars]
        return f"{safe[: self.max_chars - len(suffix)]}{suffix}"

    def log(self, label: str, value: Any) -> None:
        """Emit one single-line body record only when this app opted in."""
        if not self.enabled:
            return
        _body_logger.debug("[%s] %s", label, self.render(value))


_default_upstream_error_state = UpstreamErrorLogState()


def get_logger() -> logging.Logger:
    """Return the gateway logger instance."""
    return _logger


def record_request_stat(model: str) -> None:
    """Record one request when terminal stats mode is active."""
    if isinstance(_handler, StatsStreamHandler):
        _handler.record_request(model)


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


def setup_logging(
    log_level: str = "warning",
    use_colors: bool = True,
) -> logging.Logger:
    """Configure the gateway logger.

    Args:
        log_level: Terminal output mode: ``info``, ``stats``, ``warning``, or
            ``error``.
        use_colors: Whether to use ANSI colours in output.
    Returns:
        The configured logger.

    Raises:
        ValueError: If *log_level* is not supported.
    """
    global _handler

    levels = {
        "info": logging.INFO,
        "stats": 25,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    try:
        level = levels[log_level.lower()]
    except (AttributeError, KeyError) as exc:
        raise ValueError(
            f"Unsupported log level {log_level!r}; expected info, stats, warning, "
            "or error"
        ) from exc

    logger = get_logger()
    logger.setLevel(level)

    # Remove existing handler if present
    if _handler is not None:
        logger.removeHandler(_handler)
        _handler.close()

    _handler = StatsStreamHandler(
        sys.stderr,
        stats_enabled=log_level.lower() == "stats",
    )
    _handler.setLevel(logging.DEBUG)

    formatter = ColoredFormatter(
        datefmt="%Y-%m-%d %H:%M:%S.%f",
        use_colors=use_colors,
    )

    _handler.setFormatter(formatter)
    logger.addHandler(_handler)

    # Body records use a dedicated DEBUG child logger. Every configured output
    # handler must accept those records; ordinary DEBUG noise remains gated by
    # the parent logger's level above.
    for handler in logger.handlers:
        handler.setLevel(logging.DEBUG)

    return logger


# ---------------------------------------------------------------------------
# Request summary
# ---------------------------------------------------------------------------


def create_request_summary(data: dict[str, Any]) -> str:
    """One-line summary of a request body."""
    parts: list[str] = []
    if "model" in data:
        parts.append(f"model={data['model']}")
    if "messages" in data and isinstance(data["messages"], list):
        parts.append(f"messages={len(data['messages'])}")
    if "tools" in data and isinstance(data["tools"], list):
        parts.append(f"tools={len(data['tools'])}")
    if "stream" in data:
        parts.append(f"stream={data['stream']}")
    if "max_tokens" in data:
        parts.append(f"max_tokens={data['max_tokens']}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------


def log_request(
    data: dict[str, Any],
    label: str = "REQUEST",
    *,
    state: BodyLogState | None = None,
    show_summary: bool = True,
) -> None:
    """Log a request summary and its opt-in token-safe body."""
    if show_summary:
        summary = create_request_summary(data)
        _logger.info("[%s] %s", label, summary)
    if state is not None:
        state.log(label, data)


def log_original_request(
    data: dict[str, Any],
    *,
    state: BodyLogState | None = None,
) -> None:
    """Log the original (source-format) request."""
    log_request(
        data,
        label="ORIGINAL REQUEST",
        state=state,
        show_summary=True,
    )


def log_converted_request(
    data: dict[str, Any],
    *,
    state: BodyLogState | None = None,
) -> None:
    """Log the converted (target-format) request."""
    log_request(
        data,
        label="CONVERTED REQUEST",
        state=state,
        show_summary=False,
    )


def log_ir_request(
    data: dict[str, Any],
    *,
    state: BodyLogState | None = None,
) -> None:
    """Log the intermediate-representation request body."""
    log_request(
        data,
        label="IR REQUEST",
        state=state,
        show_summary=False,
    )


def log_response(
    data: Any,
    label: str = "RESPONSE",
    *,
    state: BodyLogState | None = None,
) -> None:
    """Log one opt-in token-safe response body."""
    if state is not None:
        state.log(label, data)


def log_stream_summary(
    *,
    model: str,
    duration_s: float,
    chunk_count: int,
) -> None:
    """Log a streaming-session summary (no per-chunk spam)."""
    _logger.info(
        "[STREAM COMPLETE] model=%s chunks=%d duration=%.2fs",
        model,
        chunk_count,
        duration_s,
    )


def log_upstream_error(
    status_code: int,
    error_text: str,
    *,
    endpoint: str = "unknown",
    is_streaming: bool = False,
    state: UpstreamErrorLogState | None = None,
) -> None:
    """Log an upstream API error in a structured format."""
    request_type = "streaming" if is_streaming else "non-streaming"
    safe_error = (state or _default_upstream_error_state).sanitize(error_text)
    _logger.error(
        "[UPSTREAM ERROR] endpoint=%s, type=%s, status=%d, error=%s",
        endpoint,
        request_type,
        status_code,
        safe_error,
    )
