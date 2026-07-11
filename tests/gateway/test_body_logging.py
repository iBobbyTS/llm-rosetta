"""Regression coverage for app-owned token-safe body logging."""

from __future__ import annotations

import copy
import io
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from codex_rosetta.gateway import logging as gateway_logging
from codex_rosetta.gateway.admin.routes import _shared
from codex_rosetta.gateway.config import GatewayConfig
from codex_rosetta.gateway.logging import (
    BodyLogState,
    log_converted_request,
    log_ir_request,
    log_original_request,
    log_response,
)


@pytest.fixture
def isolated_gateway_loggers():
    """Restore process-global logger wiring after each handler-level test."""
    logger = gateway_logging.get_logger()
    body_logger = logging.getLogger("codex-rosetta-gateway.body")
    old_level = logger.level
    old_handlers = list(logger.handlers)
    old_handler = gateway_logging._handler
    old_body_level = body_logger.level
    old_body_handlers = list(body_logger.handlers)
    old_body_propagate = body_logger.propagate
    logger.handlers = []
    body_logger.handlers = []
    gateway_logging._handler = None
    try:
        yield logger
    finally:
        for handler in logger.handlers:
            if handler not in old_handlers:
                handler.close()
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        gateway_logging._handler = old_handler
        body_logger.handlers = old_body_handlers
        body_logger.setLevel(old_body_level)
        body_logger.propagate = old_body_propagate


@pytest.mark.parametrize(
    (
        "log_level",
        "log_bodies",
        "expect_info",
        "expect_warning",
        "expect_body",
    ),
    [
        ("info", False, True, True, False),
        ("info", True, True, True, True),
        ("warning", False, False, True, False),
        ("warning", True, False, True, True),
        ("error", False, False, False, False),
        ("error", True, False, False, True),
    ],
)
def test_console_and_file_handlers_honor_logging_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_gateway_loggers: logging.Logger,
    log_level: str,
    log_bodies: bool,
    expect_info: bool,
    expect_warning: bool,
    expect_body: bool,
) -> None:
    console = io.StringIO()
    monkeypatch.setattr(gateway_logging.sys, "stderr", console)
    log_path = tmp_path / "gateway.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    isolated_gateway_loggers.addHandler(file_handler)

    gateway_logging.setup_logging(log_level=log_level, use_colors=False)
    state = BodyLogState(enabled=log_bodies, token_values={"configured-token"})
    isolated_gateway_loggers.info("ordinary-info")
    isolated_gateway_loggers.warning("ordinary-warning")
    isolated_gateway_loggers.error("ordinary-error")
    state.log("ORIGINAL REQUEST", {"prompt": "ordinary body text"})
    for handler in isolated_gateway_loggers.handlers:
        handler.flush()

    outputs = (console.getvalue(), log_path.read_text(encoding="utf-8"))
    for output in outputs:
        assert ("ordinary-info" in output) is expect_info
        assert ("ordinary-warning" in output) is expect_warning
        assert "ordinary-error" in output
        assert ("ordinary body text" in output) is expect_body
        assert ("[ORIGINAL REQUEST]" in output) is expect_body


def test_setup_logging_rejects_unsupported_level(
    isolated_gateway_loggers: logging.Logger,
) -> None:
    with pytest.raises(ValueError, match="expected info, warning, or error"):
        gateway_logging.setup_logging(log_level="debug", use_colors=False)


def test_body_log_redacts_tokens_before_serialization_and_preserves_other_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = BodyLogState(
        enabled=True,
        token_values={"configured-token", "internal-token"},
    )
    arguments = (
        '{"Authorization":"Bearer bearer-secret",'
        '"api_key":"argument-key","password":"argument-password",'
        '"secret":"argument-secret","client_secret":"argument-client-secret",'
        '"proxy_password":"argument-proxy-password",'
        '"prompt":"configured-token and ordinary argument text"}'
    )
    body = {
        "prompt": "keep PII alice@example.com and ordinary body text",
        "Authorization": "Bearer header-secret",
        "api_key": "body-api-key",
        "known": "prefix configured-token suffix",
        "password": "ordinary-password",
        "secret": "ordinary-secret",
        "client_secret": "ordinary-client-secret",
        "proxy_password": "ordinary-proxy-password",
        "function": {"name": "Bash", "arguments": arguments},
    }

    with caplog.at_level(logging.DEBUG, logger="codex-rosetta-gateway.body"):
        state.log("CONVERTED REQUEST", body)

    output = caplog.text
    for secret in (
        "configured-token",
        "internal-token",
        "bearer-secret",
        "header-secret",
        "argument-key",
        "body-api-key",
    ):
        assert secret not in output
    for retained in (
        "alice@example.com",
        "ordinary body text",
        "ordinary-password",
        "ordinary-secret",
        "ordinary-client-secret",
        "ordinary-proxy-password",
        "argument-password",
        "argument-secret",
        "argument-client-secret",
        "argument-proxy-password",
        "ordinary argument text",
    ):
        assert retained in output
    assert output.count("[REDACTED]") >= 5


def test_body_log_is_single_line_bounded_and_never_uses_raw_repr_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Unserializable:
        def __deepcopy__(self, memo: dict[int, Any]) -> _Unserializable:
            del memo
            return self

        def __repr__(self) -> str:
            return "configured-token-in-repr"

    state = BodyLogState(
        enabled=True,
        token_values={"configured-token-in-repr"},
        max_chars=80,
    )
    with caplog.at_level(logging.DEBUG, logger="codex-rosetta-gateway.body"):
        state.log("UPSTREAM RESPONSE", {"value": _Unserializable()})
        state.log("UPSTREAM RESPONSE", {"text": "line-1\n" + "x" * 200})

    records = [
        record.getMessage()
        for record in caplog.records
        if record.name == "codex-rosetta-gateway.body"
    ]
    assert len(records) == 2
    assert records[0].endswith("[body serialization failed]")
    assert "configured-token-in-repr" not in caplog.text
    assert "\n" not in records[1]
    assert r"\n" in records[1]
    rendered = records[1].split("] ", 1)[1]
    assert len(rendered) == 80
    assert rendered.endswith("...[truncated]")


def test_disabled_body_log_does_not_touch_or_emit_the_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _ExplodesOnCopy:
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            del memo
            raise AssertionError("disabled body was inspected")

    state = BodyLogState(enabled=False, token_values={"configured-token"})
    with caplog.at_level(logging.DEBUG, logger="codex-rosetta-gateway.body"):
        state.log("ORIGINAL REQUEST", _ExplodesOnCopy())

    assert not [
        record
        for record in caplog.records
        if record.name == "codex-rosetta-gateway.body"
    ]


def test_all_proxy_body_categories_use_the_same_state_and_distinct_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        gateway_logging._body_logger,
        "debug",
        lambda *args: calls.append(args),
    )
    state = BodyLogState(enabled=True, token_values={"configured-token"})

    log_original_request({"value": "configured-token"}, state=state)
    log_ir_request({"value": "configured-token"}, state=state)
    log_converted_request({"value": "configured-token"}, state=state)
    log_response(
        {"value": "configured-token"},
        label="UPSTREAM RESPONSE",
        state=state,
    )

    assert [call[1] for call in calls] == [
        "ORIGINAL REQUEST",
        "IR REQUEST",
        "CONVERTED REQUEST",
        "UPSTREAM RESPONSE",
    ]
    assert all("configured-token" not in call[2] for call in calls)


def _config(token: str, *, log_bodies: bool) -> GatewayConfig:
    return GatewayConfig(
        {
            "server": {
                "admin_password": "test-admin-password",
                "api_keys": [{"id": "caller", "key": token, "label": "caller"}],
            },
            "debug": {"log_bodies": log_bodies},
        }
    )


def test_hot_reload_and_rollback_keep_body_log_state_isolated_per_app() -> None:
    initial_a = _config("token-a-old", log_bodies=False)
    state_a = BodyLogState(enabled=False, token_values={"token-a-old", "internal-a"})
    state_b = BodyLogState(enabled=True, token_values={"token-b", "internal-b"})
    app_a = SimpleNamespace(
        gateway_config=initial_a,
        admin_cors_origins=(),
        internal_token="internal-a",
        body_log_state=state_a,
    )
    app_b = SimpleNamespace(
        gateway_config=_config("token-b", log_bodies=True),
        admin_cors_origins=(),
        internal_token="internal-b",
        body_log_state=state_b,
    )

    rollback = _shared._activate_gateway_config(
        SimpleNamespace(app=app_a),
        _config("token-a-new", log_bodies=True),
    )

    assert state_a.enabled is True
    assert "token-a-new" not in state_a.render({"value": "token-a-new"})
    assert "token-a-old" in state_a.render({"value": "token-a-old"})
    assert state_b.enabled is True
    assert app_b.body_log_state is state_b
    assert "token-b" not in state_b.render({"value": "token-b"})
    assert "token-a-new" in state_b.render({"value": "token-a-new"})

    _shared._rollback_gateway_activation(SimpleNamespace(app=app_a), rollback)

    assert state_a.enabled is False
    assert "token-a-old" not in state_a.render({"value": "token-a-old"})
    assert "token-a-new" in state_a.render({"value": "token-a-new"})
    assert state_b.enabled is True
    assert "token-b" not in state_b.render({"value": "token-b"})


def test_redaction_failure_uses_constant_safe_fallback() -> None:
    class _RedactionFailure:
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            del memo
            raise RuntimeError("configured-token-in-exception")

    state = BodyLogState(
        enabled=True,
        token_values={"configured-token-in-exception"},
    )

    assert state.render(_RedactionFailure()) == "[body redaction failed]"


def test_render_does_not_mutate_the_original_body() -> None:
    body = {"api_key": "body-api-key", "nested": {"prompt": "keep"}}
    original = copy.deepcopy(body)

    BodyLogState(enabled=True).render(body)

    assert body == original
