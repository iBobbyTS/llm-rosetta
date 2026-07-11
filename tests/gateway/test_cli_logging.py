"""Regression coverage for gateway terminal log-level CLI wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_rosetta.gateway import app as gateway_app
from codex_rosetta.gateway import cli


@pytest.mark.parametrize("log_level", ["info", "warning", "error"])
def test_main_passes_selected_log_level_to_logging_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_level: str,
) -> None:
    config_path = tmp_path / "config.jsonc"
    config_path.write_text("{}", encoding="utf-8")
    config = SimpleNamespace(
        host="127.0.0.1",
        port=8765,
        socket=None,
        providers={},
        models={},
        log_bodies=False,
    )
    selected_levels: list[str] = []

    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "codex-rosetta-gateway",
            "--no-banner",
            "--config",
            str(config_path),
            "--log-level",
            log_level,
        ],
    )
    monkeypatch.setattr(cli, "discover_config", lambda _path: str(config_path))
    monkeypatch.setattr(cli, "load_config", lambda _path: {})
    monkeypatch.setattr(cli, "GatewayConfig", lambda _raw: config)
    monkeypatch.setattr(
        cli,
        "setup_logging",
        lambda *, log_level: selected_levels.append(log_level),
    )
    monkeypatch.setattr(gateway_app, "create_app", lambda *_args, **_kwargs: object())

    async def fake_run_gateway(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(gateway_app, "run_gateway", fake_run_gateway)

    cli.main()

    assert selected_levels == [log_level]


@pytest.mark.parametrize("removed_option", ["--verbose", "-v"])
def test_main_rejects_removed_verbose_option(
    monkeypatch: pytest.MonkeyPatch,
    removed_option: str,
) -> None:
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["codex-rosetta-gateway", "--no-banner", removed_option],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
