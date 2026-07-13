"""Regression coverage for gateway terminal log-level CLI wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_rosetta.gateway import app as gateway_app
from codex_rosetta.gateway import cli


@pytest.mark.parametrize(
    ("log_level_args", "expected_level"),
    [
        ([], "warning"),
        (["--log-level", "info"], "info"),
        (["--log-level", "stats"], "stats"),
        (["--log-level", "warning"], "warning"),
        (["--log-level", "error"], "error"),
    ],
)
def test_main_passes_selected_log_level_to_logging_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_level_args: list[str],
    expected_level: str,
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
        local_mode=True,
    )
    selected_levels: list[str] = []
    app_kwargs: list[dict[str, object]] = []
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "original-codex-home"))

    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "codex-rosetta-gateway",
            "--no-banner",
            "--config",
            str(tmp_path),
            "--codex-home",
            str(codex_home),
            "--confirm-clear-existing-catalog",
            *log_level_args,
        ],
    )
    monkeypatch.setattr(cli, "discover_config", lambda _path: str(config_path))
    monkeypatch.setattr(cli, "load_config", lambda _path: {})

    class FakeGatewayConfig:
        def __new__(cls, _raw):
            return config

        @classmethod
        def from_raw_with_env(cls, _raw):
            return config

    monkeypatch.setattr(cli, "GatewayConfig", FakeGatewayConfig)
    monkeypatch.setattr(
        cli,
        "setup_logging",
        lambda *, log_level: selected_levels.append(log_level),
    )
    monkeypatch.setattr(
        gateway_app,
        "create_app",
        lambda *_args, **kwargs: app_kwargs.append(kwargs) or object(),
    )

    async def fake_run_gateway(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(gateway_app, "run_gateway", fake_run_gateway)

    cli.main()

    assert selected_levels == [expected_level]
    assert app_kwargs[0]["codex_home"] == str(codex_home)
    assert cli.os.environ["CODEX_HOME"] == str(codex_home)


@pytest.mark.parametrize("explicit_config", [False, True])
def test_main_initializes_missing_config_and_continues_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_config: bool,
) -> None:
    config_dir = tmp_path / ("explicit" if explicit_config else "default")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    argv = [
        "codex-rosetta-gateway",
        "--no-banner",
        "--confirm-clear-existing-catalog",
    ]
    if explicit_config:
        argv.extend(["--config", str(config_dir)])
    else:
        monkeypatch.setattr(cli, "DEFAULT_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(cli.sys, "argv", argv)
    monkeypatch.setattr(cli, "setup_logging", lambda **_kwargs: None)
    monkeypatch.setattr(gateway_app, "create_app", lambda *_args, **_kwargs: object())
    started: list[tuple[str, int]] = []

    async def fake_run_gateway(_app, host, port, **_kwargs) -> None:
        started.append((host, port))

    monkeypatch.setattr(gateway_app, "run_gateway", fake_run_gateway)

    cli.main()

    config_path = config_dir / "config.jsonc"
    assert config_path.is_file()
    assert started == [("127.0.0.1", 8765)]


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
