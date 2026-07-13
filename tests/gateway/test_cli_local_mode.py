"""CLI lifecycle tests for Codex local mode."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_rosetta.gateway import app as gateway_app
from codex_rosetta.gateway import cli


def _gateway_config(*, local_mode: bool, confirmed: bool, host: str = "127.0.0.1"):
    return {
        "providers": {},
        "tool_profiles": {},
        "model_groups": {},
        "server": {
            "host": host,
            "port": 8765,
            "local_mode": local_mode,
            "local_mode_confirmed": confirmed,
            "admin_password": "test-admin-password",
            "api_keys": [
                {
                    "id": "test-client",
                    "label": "Test client",
                    "key": "test-gateway-key",
                }
            ],
        },
    }


def _stub_server(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    started: list[tuple[str, int]] = []
    monkeypatch.setattr(cli, "setup_logging", lambda **_kwargs: None)
    monkeypatch.setattr(gateway_app, "create_app", lambda *_args, **_kwargs: object())

    async def fake_run_gateway(_app, host, port, **_kwargs) -> None:
        started.append((host, port))

    monkeypatch.setattr(gateway_app, "run_gateway", fake_run_gateway)
    return started


def test_local_mode_flag_persists_for_later_startups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "gateway"
    config_dir.mkdir()
    config_path = config_dir / "config.jsonc"
    config_path.write_text(
        json.dumps(_gateway_config(local_mode=False, confirmed=False)),
        encoding="utf-8",
    )
    codex_home = tmp_path / "codex"
    started = _stub_server(monkeypatch)

    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "codex-rosetta-gateway",
            "--no-banner",
            "--config",
            str(config_dir),
            "--codex-home",
            str(codex_home),
            "--local-mode",
            "--confirm-clear-existing-catalog",
        ],
    )
    cli.main()

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["local_mode"] is True
    assert saved["server"]["local_mode_confirmed"] is True
    assert (codex_home / "model_catalog.json").is_file()
    assert str(codex_home / "model_catalog.json") in (
        codex_home / "config.toml"
    ).read_text(encoding="utf-8")

    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "codex-rosetta-gateway",
            "--no-banner",
            "--config",
            str(config_dir),
            "--codex-home",
            str(codex_home),
        ],
    )
    cli.main()

    assert started == [("127.0.0.1", 8765), ("127.0.0.1", 8765)]


def test_confirm_flag_records_consent_without_enabling_local_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "gateway"
    config_dir.mkdir()
    config_path = config_dir / "config.jsonc"
    config_path.write_text(
        json.dumps(_gateway_config(local_mode=False, confirmed=False)),
        encoding="utf-8",
    )
    codex_home = tmp_path / "codex"
    started = _stub_server(monkeypatch)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "codex-rosetta-gateway",
            "--no-banner",
            "--config",
            str(config_dir),
            "--codex-home",
            str(codex_home),
            "--confirm-clear-existing-catalog",
        ],
    )

    cli.main()

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["local_mode"] is False
    assert saved["server"]["local_mode_confirmed"] is True
    assert not codex_home.exists()
    assert started == [("127.0.0.1", 8765)]


def test_noninteractive_first_start_requires_confirmation_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "gateway"
    config_dir.mkdir()
    (config_dir / "config.jsonc").write_text(
        json.dumps(_gateway_config(local_mode=True, confirmed=False)),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "codex-rosetta-gateway",
            "--no-banner",
            "--config",
            str(config_dir),
            "--codex-home",
            str(tmp_path / "codex"),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
    assert "--confirm-clear-existing-catalog" in capsys.readouterr().err


@pytest.mark.parametrize("has_existing", [False, True])
def test_interactive_confirmation_only_mentions_clearing_when_catalog_is_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_existing: bool,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    if has_existing:
        (codex_home / "config.toml").write_text(
            'model_catalog_json = "/existing/catalog.json"\n', encoding="utf-8"
        )
    prompts: list[str] = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt: prompts.append(prompt) or "yes"
    )

    assert cli._confirm_local_mode(str(codex_home)) is True

    assert str(codex_home) in prompts[0]
    assert ("Existing model_catalog_json settings will be cleared." in prompts[0]) is (
        has_existing
    )


def test_interactive_decline_disables_mode_without_modifying_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "gateway"
    config_dir.mkdir()
    config_path = config_dir / "config.jsonc"
    config_path.write_text(
        json.dumps(_gateway_config(local_mode=True, confirmed=False)),
        encoding="utf-8",
    )
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    original_toml = 'model_catalog_json = "/user/catalog.json"\nmodel = "user"\n'
    config_toml.write_text(original_toml, encoding="utf-8")
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    started = _stub_server(monkeypatch)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "codex-rosetta-gateway",
            "--no-banner",
            "--config",
            str(config_dir),
            "--codex-home",
            str(codex_home),
        ],
    )

    cli.main()

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["local_mode"] is False
    assert saved["server"]["local_mode_confirmed"] is False
    assert config_toml.read_text(encoding="utf-8") == original_toml
    assert not (codex_home / "model_catalog.json").exists()
    assert started == [("127.0.0.1", 8765)]


def test_local_mode_clear_disables_and_removes_only_managed_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "gateway"
    config_dir.mkdir()
    config_path = config_dir / "config.jsonc"
    config_path.write_text(
        json.dumps(_gateway_config(local_mode=True, confirmed=True)),
        encoding="utf-8",
    )
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "model_catalog.json").write_text("managed", encoding="utf-8")
    external = tmp_path / "external.json"
    external.write_text("keep", encoding="utf-8")
    (codex_home / "config.toml").write_text(
        f'model_catalog_json = "{external}"\nmodel = "keep"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "codex-rosetta-gateway",
            "--config",
            str(config_dir),
            "--codex-home",
            str(codex_home),
            "local-mode",
            "clear",
        ],
    )

    cli.main()

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["server"]["local_mode"] is False
    assert not (codex_home / "model_catalog.json").exists()
    assert "model_catalog_json" not in (codex_home / "config.toml").read_text(
        encoding="utf-8"
    )
    assert external.read_text(encoding="utf-8") == "keep"


def test_remote_host_prints_manual_configuration_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "gateway"
    config_dir.mkdir()
    (config_dir / "config.jsonc").write_text(
        json.dumps(_gateway_config(local_mode=True, confirmed=True, host="0.0.0.0")),
        encoding="utf-8",
    )
    _stub_server(monkeypatch)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "codex-rosetta-gateway",
            "--no-banner",
            "--config",
            str(config_dir),
            "--codex-home",
            str(tmp_path / "codex"),
        ],
    )

    cli.main()

    assert (
        "Remote use of this gateway requires manually configuring "
        "config.toml and model_catalog_json."
    ) in capsys.readouterr().err
