"""Tests for Codex local-mode catalog generation and file synchronization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_rosetta.gateway.local_mode import (
    CodexLocalModeTransaction,
    build_model_catalog,
    catalog_path,
    config_toml_has_model_catalog,
)


def test_catalog_keeps_bundled_models_and_clones_terra_for_custom_llms() -> None:
    raw = {
        "model_groups": {
            "llm": {
                "type": "llm",
                "models": {
                    "gpt-5.6-sol": {},
                    "zeta-model": {},
                    "alpha-model": {},
                },
            },
            "embedding": {
                "type": "embedding",
                "models": {"embedding-only": {}},
            },
        }
    }

    catalog = build_model_catalog(raw)
    models = catalog["models"]
    slugs = [model["slug"] for model in models]

    assert slugs[:8] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.2",
        "codex-auto-review",
    ]
    assert slugs[8:] == ["alpha-model", "zeta-model"]
    assert slugs.count("gpt-5.6-sol") == 1
    assert "embedding-only" not in slugs

    terra = next(model for model in models if model["slug"] == "gpt-5.6-terra")
    custom = next(model for model in models if model["slug"] == "alpha-model")
    assert custom["slug"] == custom["display_name"] == custom["description"]
    assert custom["slug"] == "alpha-model"
    for key, value in terra.items():
        if key not in {"slug", "display_name", "description"}:
            assert custom[key] == value


def test_sync_replaces_catalog_setting_and_preserves_other_toml(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    external_catalog = tmp_path / "external.json"
    external_catalog.write_text("keep", encoding="utf-8")
    original = (
        f'model_catalog_json = "{external_catalog}"\n'
        'model = "gpt-5.6-sol"\n\n'
        "# keep this comment\n"
        "[profile.test]\n"
        'model_catalog_json = "/profile/catalog.json"\n'
        'personality = "pragmatic"\n'
    )
    config_toml = codex_home / "config.toml"
    config_toml.write_text(original, encoding="utf-8")

    transaction = CodexLocalModeTransaction.sync(str(codex_home), {})
    transaction.apply()

    updated = config_toml.read_text(encoding="utf-8")
    expected_catalog = str(codex_home / "model_catalog.json")
    assert updated.startswith(f'model_catalog_json = "{expected_catalog}"\n')
    assert updated.count("model_catalog_json") == 1
    assert 'model = "gpt-5.6-sol"' in updated
    assert "# keep this comment" in updated
    assert "[profile.test]" in updated
    assert 'personality = "pragmatic"' in updated
    assert external_catalog.read_text(encoding="utf-8") == "keep"
    assert config_toml_has_model_catalog(str(codex_home)) is True

    written = json.loads(Path(catalog_path(str(codex_home))).read_text("utf-8"))
    assert len(written["models"]) == 8

    transaction.rollback()
    assert config_toml.read_text(encoding="utf-8") == original
    assert not Path(catalog_path(str(codex_home))).exists()


def test_clear_removes_only_rosetta_catalog_and_toml_assignments(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    managed = codex_home / "model_catalog.json"
    managed.write_text("managed", encoding="utf-8")
    external = tmp_path / "do-not-delete.json"
    external.write_text("external", encoding="utf-8")
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        f'model_catalog_json = "{external}"\nmodel = "gpt-5.6-sol"\n',
        encoding="utf-8",
    )

    transaction = CodexLocalModeTransaction.clear(str(codex_home))
    transaction.apply()

    assert not managed.exists()
    assert external.read_text(encoding="utf-8") == "external"
    assert "model_catalog_json" not in config_toml.read_text(encoding="utf-8")
    assert 'model = "gpt-5.6-sol"' in config_toml.read_text(encoding="utf-8")

    transaction.rollback()
    assert managed.read_text(encoding="utf-8") == "managed"
    assert str(external) in config_toml.read_text(encoding="utf-8")


def test_toml_editor_does_not_remove_catalog_text_inside_multiline_string(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        'instructions = """\nmodel_catalog_json = "keep as text"\n"""\n'
        'model_catalog_json = "/remove/this.json"\n',
        encoding="utf-8",
    )

    transaction = CodexLocalModeTransaction.clear(str(codex_home))
    transaction.apply()

    updated = config_toml.read_text(encoding="utf-8")
    assert 'model_catalog_json = "keep as text"' in updated
    assert "/remove/this.json" not in updated


def test_toml_editor_removes_a_multiline_catalog_assignment(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config_toml = codex_home / "config.toml"
    config_toml.write_text(
        'model_catalog_json = """\n/remove/this.json\n"""\nmodel = "keep"\n',
        encoding="utf-8",
    )

    transaction = CodexLocalModeTransaction.clear(str(codex_home))
    transaction.apply()

    assert config_toml.read_text(encoding="utf-8") == 'model = "keep"\n'


def test_sync_rolls_back_both_files_when_toml_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from codex_rosetta.gateway import local_mode

    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    catalog_file = codex_home / "model_catalog.json"
    config_file = codex_home / "config.toml"
    catalog_file.write_text("old catalog", encoding="utf-8")
    config_file.write_text('model = "old"\n', encoding="utf-8")
    real_atomic_write = local_mode._atomic_write_bytes
    failed = False

    def fail_config_toml(path: str, content: bytes) -> None:
        nonlocal failed
        if path == str(config_file) and not failed:
            failed = True
            raise OSError("simulated config.toml failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(local_mode, "_atomic_write_bytes", fail_config_toml)
    transaction = CodexLocalModeTransaction.sync(str(codex_home), {})

    with pytest.raises(OSError, match="simulated"):
        transaction.apply()

    assert catalog_file.read_text(encoding="utf-8") == "old catalog"
    assert config_file.read_text(encoding="utf-8") == 'model = "old"\n'
