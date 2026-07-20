"""Tests for SQLite-based persistence and request log integration."""

import base64
import gzip
import json
import shutil
import sqlite3
import stat
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from codex_rosetta.gateway.admin.persistence import (
    DEFAULT_ERROR_MAX,
    DEFAULT_SUCCESS_MAX,
    PersistenceManager,
)
from codex_rosetta.gateway.admin.request_log import RequestLog, RequestLogEntry
from codex_rosetta.observability.persistence import (
    CompactionMappingCapacityError,
    ToolMappingCapacityError,
)
from codex_rosetta.observability.tool_mapping_crypto import (
    KEY_ENV_VAR,
    KEY_FILENAME,
    ToolMappingCipher,
    ToolMappingIntegrityError,
    ToolMappingKeyError,
    mapping_aad,
)

_TOOL_SCOPE = {
    "principal_id": "client-a",
    "provider_name": "provider-a",
    "model": "model-a",
    "session_id": "s1",
}


class TestCodexCompactionMappings:
    def test_stores_plaintext_but_only_token_hash_and_renews_on_read(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        plaintext = "prefixed summary with Orchid and A7-KAPPA"
        pm.store_codex_compaction_mapping(
            principal_id="client-a",
            token_hash="a" * 64,
            replacement_text=plaintext,
            source_model="deepseek-v4-flash",
            reason="comp_hash_changed",
            prompt_sha256="b" * 64,
            created_at="2026-07-01T00:00:00+00:00",
            expires_at="2026-07-08T00:00:00+00:00",
        )

        row = pm.get_codex_compaction_mapping(
            principal_id="client-a",
            token_hash="a" * 64,
            now="2026-07-02T00:00:00+00:00",
            renewed_expires_at="2026-07-09T00:00:00+00:00",
        )

        assert row is not None
        assert row["replacement_text"] == plaintext
        assert row["replacement_bytes"] == len(plaintext.encode("utf-8"))
        assert row["expires_at"] == "2026-07-09T00:00:00+00:00"
        columns = pm._conn.execute(
            "SELECT token_hash, replacement_text FROM codex_compaction_mappings"
        ).fetchone()
        assert columns == ("a" * 64, plaintext)
        pm.close()

    def test_principal_isolation_and_expiry(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.store_codex_compaction_mapping(
            principal_id="client-a",
            token_hash="a" * 64,
            replacement_text="summary",
            source_model="model",
            reason="context_limit",
            prompt_sha256="b" * 64,
            created_at="2026-07-01T00:00:00+00:00",
            expires_at="2026-07-08T00:00:00+00:00",
        )
        assert (
            pm.get_codex_compaction_mapping(
                principal_id="client-b",
                token_hash="a" * 64,
                now="2026-07-02T00:00:00+00:00",
            )
            is None
        )
        assert (
            pm.cleanup_expired_codex_compaction_mappings("2026-07-08T00:00:00+00:00")
            == 1
        )
        assert pm.count_codex_compaction_mappings() == 0
        pm.close()

    def test_enforces_row_and_aggregate_quotas_transactionally(self, tmp_path):
        pm = PersistenceManager(
            str(tmp_path),
            codex_compaction_max_row_bytes=8,
            codex_compaction_max_principal_rows=1,
            codex_compaction_max_principal_bytes=8,
            codex_compaction_max_global_rows=2,
            codex_compaction_max_global_bytes=16,
        )

        def store(principal: str, token: str, text: str) -> None:
            pm.store_codex_compaction_mapping(
                principal_id=principal,
                token_hash=token,
                replacement_text=text,
                source_model="model",
                reason="test",
                prompt_sha256="b" * 64,
                created_at="2027-07-01T00:00:00+00:00",
                expires_at="2027-07-08T00:00:00+00:00",
            )

        store("client-a", "a" * 64, "12345678")
        with pytest.raises(CompactionMappingCapacityError, match="row byte limit"):
            store("client-a", "b" * 64, "123456789")
        with pytest.raises(CompactionMappingCapacityError, match="principal row count"):
            store("client-a", "b" * 64, "1234")
        store("client-b", "b" * 64, "12345678")
        with pytest.raises(CompactionMappingCapacityError, match="global row count"):
            store("client-c", "c" * 64, "1234")
        assert pm.count_codex_compaction_mappings() == 2
        pm.close()


# -- Helpers --


def _make_entry_dict(
    model: str = "gpt-4o",
    status: int = 200,
    provider: str = "openai_chat",
    error_detail: str | None = None,
    api_key_label: str | None = None,
) -> dict:
    e = RequestLogEntry.create(
        model=model,
        source_provider="openai_chat",
        target_provider=provider,
        is_stream=False,
        status_code=status,
        duration_ms=10.0,
        error_detail=error_detail,
        api_key_label=api_key_label,
    )
    return e.to_dict()


def _make_entry(
    model: str = "gpt-4o",
    status: int = 200,
    provider: str = "openai_chat",
) -> RequestLogEntry:
    return RequestLogEntry.create(
        model=model,
        source_provider="openai_chat",
        target_provider=provider,
        is_stream=False,
        status_code=status,
        duration_ms=10.0,
    )


def _upsert_mapping(
    pm: PersistenceManager,
    call_id: str,
    *,
    scope: dict[str, str] | None = None,
    payload: str = "payload",
    expire_at: str = "2030-01-01T00:00:00+00:00",
    timestamp: str = "2026-01-01T00:00:00+00:00",
) -> None:
    pm.upsert_tool_call_mapping(
        **(scope or _TOOL_SCOPE),
        tool_call_id=call_id,
        original_tool_call={"id": call_id, "payload": payload},
        codex_tool_call={"id": call_id, "payload": f"codex:{payload}"},
        expire_at=expire_at,
        timestamp=timestamp,
    )


# -- PersistenceManager tests --


class TestPersistenceManagerSchema:
    def test_creates_db_file(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        assert pm.db_path.exists()
        pm.close()

    def test_storage_permissions_are_owner_only(self, tmp_path):
        data_dir = tmp_path / "gateway-data"
        pm = PersistenceManager(str(data_dir))
        assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(pm.db_path.stat().st_mode) == 0o600
        for suffix in ("-wal", "-shm"):
            sidecar = pm.db_path.with_name(pm.db_path.name + suffix)
            if sidecar.exists():
                assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
        pm.close()

    def test_wal_mode(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        row = pm._conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"
        pm.close()

    def test_creates_tool_call_mapping_table(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        row = pm._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'tool_call_mappings'"
        ).fetchone()
        assert row[0] == "tool_call_mappings"
        pm.close()

    def test_rejects_same_compaction_columns_without_required_primary_key(
        self, tmp_path
    ):
        conn = sqlite3.connect(tmp_path / "gateway.db")
        conn.execute("""
            CREATE TABLE codex_compaction_mappings (
                principal_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                replacement_text TEXT NOT NULL,
                replacement_bytes INTEGER NOT NULL,
                source_model TEXT NOT NULL,
                reason TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        conn.close()

        with pytest.raises(RuntimeError, match="column/type/constraint shape differs"):
            PersistenceManager(str(tmp_path))

    @pytest.mark.parametrize(
        "index_sql",
        [
            "CREATE UNIQUE INDEX idx_ccm_principal "
            "ON codex_compaction_mappings(principal_id)",
            "CREATE INDEX idx_ccm_principal "
            "ON codex_compaction_mappings(principal_id) "
            "WHERE principal_id IS NOT NULL",
        ],
    )
    def test_rejects_existing_required_index_with_wrong_attributes(
        self, tmp_path, index_sql
    ):
        conn = sqlite3.connect(tmp_path / "gateway.db")
        conn.executescript("""
            CREATE TABLE codex_compaction_mappings (
                principal_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                replacement_text TEXT NOT NULL,
                replacement_bytes INTEGER NOT NULL,
                source_model TEXT NOT NULL,
                reason TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (principal_id, token_hash)
            );
        """)
        conn.execute(index_sql)
        conn.close()

        with pytest.raises(RuntimeError, match="has unexpected attributes"):
            PersistenceManager(str(tmp_path))

    def test_rejects_existing_required_index_with_wrong_columns(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "gateway.db")
        conn.executescript("""
            CREATE TABLE codex_compaction_mappings (
                principal_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                replacement_text TEXT NOT NULL,
                replacement_bytes INTEGER NOT NULL,
                source_model TEXT NOT NULL,
                reason TEXT NOT NULL,
                prompt_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (principal_id, token_hash)
            );
            CREATE INDEX idx_ccm_principal
                ON codex_compaction_mappings(token_hash);
        """)
        conn.close()

        with pytest.raises(RuntimeError, match="has unexpected columns"):
            PersistenceManager(str(tmp_path))


class TestPersistenceManagerToolCallMappings:
    def test_query_can_refresh_mapping_ttl(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        _upsert_mapping(
            pm,
            "call_refresh",
            expire_at="2026-01-01T01:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )

        rows = pm.query_tool_call_mappings(
            **_TOOL_SCOPE,
            now="2026-01-01T00:30:00+00:00",
            renew_expire_at="2026-01-02T00:30:00+00:00",
            renewed_at="2026-01-01T00:30:00+00:00",
        )

        assert rows[0]["expire_at"] == "2026-01-02T00:30:00+00:00"
        assert rows[0]["updated_at"] == "2026-01-01T00:30:00+00:00"
        assert pm.query_tool_call_mappings(
            **_TOOL_SCOPE,
            now="2026-01-01T01:30:00+00:00",
        )
        pm.close()

    def test_upsert_query_delete_and_cleanup(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        original = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "Edit", "arguments": '{"old_string":"a"}'},
        }
        codex = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "apply_patch", "arguments": '{"input":"patch"}'},
        }

        pm.upsert_tool_call_mapping(
            **_TOOL_SCOPE,
            tool_call_id="call_1",
            original_tool_call=original,
            codex_tool_call=codex,
            expire_at="2030-01-01T00:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )

        rows = pm.query_tool_call_mappings(
            **_TOOL_SCOPE,
            now="2026-01-01T00:00:00+00:00",
        )
        assert len(rows) == 1
        assert rows[0]["original_tool_call"] == original
        assert pm.count_tool_call_mappings() == 1

        pm.delete_tool_call_mappings(**_TOOL_SCOPE, tool_call_ids=["call_1"])
        assert pm.count_tool_call_mappings() == 0

        pm.upsert_tool_call_mapping(
            **_TOOL_SCOPE,
            tool_call_id="call_1",
            original_tool_call=original,
            codex_tool_call=codex,
            expire_at="2026-01-01T00:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        deleted = pm.cleanup_expired_tool_call_mappings("2026-01-01T00:00:01+00:00")
        assert deleted == 1
        assert pm.count_tool_call_mappings() == 0
        pm.close()

    def test_same_session_and_call_id_are_isolated_by_principal(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        original = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "Edit", "arguments": "{}"},
        }
        codex = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "apply_patch", "arguments": "{}"},
        }
        for principal in ("client-a", "client-b"):
            pm.upsert_tool_call_mapping(
                principal_id=principal,
                provider_name="provider-a",
                model="model-a",
                session_id="same-window",
                tool_call_id="call_1",
                original_tool_call={**original, "owner": principal},
                codex_tool_call=codex,
                expire_at="2030-01-01T00:00:00+00:00",
                timestamp="2026-01-01T00:00:00+00:00",
            )

        rows = pm.query_tool_call_mappings(
            principal_id="client-b",
            provider_name="provider-a",
            model="model-a",
            session_id="same-window",
            now="2026-01-01T00:00:00+00:00",
        )
        assert len(rows) == 1
        assert rows[0]["principal_id"] == "client-b"
        assert rows[0]["original_tool_call"]["owner"] == "client-b"
        assert pm.count_tool_call_mappings() == 2
        pm.close()

    def test_tool_mapping_persistence_encrypts_tokens_and_restores_exactly(
        self, tmp_path
    ):
        pm = PersistenceManager(str(tmp_path), token_values={"sk-live-secret"})
        command = (
            "curl -H 'Authorization: Bearer bearer-secret' "
            "https://user@example.com?key=sk-live-secret"
        )
        original = {
            "id": "call_secret",
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": json.dumps(
                    {
                        "command": command,
                        "api_key": "tool-api-key",
                        "password": "ordinary-password",
                        "secret": "ordinary-secret",
                        "client_secret": "ordinary-client-secret",
                        "prompt": "keep user@example.com",
                    }
                ),
            },
        }
        codex = {
            "id": "call_secret",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": json.dumps(
                    {
                        "cmd": command,
                        "password": "ordinary-password",
                        "client_secret": "ordinary-client-secret",
                    }
                ),
            },
        }

        pm.upsert_tool_call_mapping(
            **_TOOL_SCOPE,
            tool_call_id="call_secret",
            original_tool_call=original,
            codex_tool_call=codex,
            expire_at="2030-01-01T00:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )

        rows = pm.query_tool_call_mappings(
            **_TOOL_SCOPE,
            now="2026-01-01T00:00:00+00:00",
        )
        raw_row = pm._conn.execute(
            "SELECT key_id, nonce, encrypted_payload "
            "FROM tool_call_mappings WHERE tool_call_id = ?",
            ("call_secret",),
        ).fetchone()
        assert raw_row is not None
        raw_storage = b" ".join(
            item.encode() if isinstance(item, str) else item for item in raw_row
        )

        for plaintext in (
            "sk-live-secret",
            "bearer-secret",
            "tool-api-key",
            "ordinary-password",
            "ordinary-secret",
            "user@example.com",
        ):
            assert plaintext.encode() not in raw_storage
        pm._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        sqlite_bytes = b"".join(
            path.read_bytes()
            for path in (
                pm.db_path,
                pm.db_path.with_name(pm.db_path.name + "-wal"),
                pm.db_path.with_name(pm.db_path.name + "-shm"),
            )
            if path.exists()
        )
        for plaintext in ("sk-live-secret", "bearer-secret", "tool-api-key"):
            assert plaintext.encode() not in sqlite_bytes
        assert rows[0]["original_tool_call"] == original
        assert rows[0]["codex_tool_call"] == codex
        key_path = tmp_path / KEY_FILENAME
        assert key_path.exists()
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
        pm.close()

    def test_encrypted_mapping_survives_restart_with_exact_payload(self, tmp_path):
        original = {
            "id": "call_restart",
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"description":"exact","command":"printf sk-live"}',
            },
        }
        codex = {
            "id": "call_restart",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": '{"cmd":"printf sk-live","yield_time_ms":1000}',
            },
        }
        pm = PersistenceManager(str(tmp_path), token_values={"sk-live"})
        pm.upsert_tool_call_mapping(
            **_TOOL_SCOPE,
            tool_call_id="call_restart",
            original_tool_call=original,
            codex_tool_call=codex,
            expire_at="2030-01-01T00:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        pm.close()

        restarted = PersistenceManager(str(tmp_path), token_values={"sk-live"})
        rows = restarted.query_tool_call_mappings(
            **_TOOL_SCOPE,
            now="2026-01-01T00:00:00+00:00",
        )
        assert rows[0]["original_tool_call"] == original
        assert rows[0]["codex_tool_call"] == codex
        restarted.close()

    def test_matched_database_and_key_backup_restore_exact_mapping(self, tmp_path):
        live_dir = tmp_path / "live"
        backup_dir = tmp_path / "backup"
        restored_dir = tmp_path / "restored"
        original = {"id": "call_backup", "payload": "exact secret payload"}
        codex = {"id": "call_backup", "payload": "native exact payload"}
        pm = PersistenceManager(str(live_dir))
        pm.upsert_tool_call_mapping(
            **_TOOL_SCOPE,
            tool_call_id="call_backup",
            original_tool_call=original,
            codex_tool_call=codex,
            expire_at="2030-01-01T00:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        pm.close()

        backup_dir.mkdir()
        shutil.copy2(live_dir / "gateway.db", backup_dir / "gateway.db")
        shutil.copy2(live_dir / KEY_FILENAME, backup_dir / KEY_FILENAME)
        shutil.copytree(backup_dir, restored_dir)

        restored = PersistenceManager(str(restored_dir))
        rows = restored.query_tool_call_mappings(
            **_TOOL_SCOPE,
            now="2026-01-01T00:00:00+00:00",
        )
        assert rows[0]["original_tool_call"] == original
        assert rows[0]["codex_tool_call"] == codex
        restored.close()

    def test_missing_key_with_encrypted_rows_fails_without_regeneration(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.upsert_tool_call_mapping(
            **_TOOL_SCOPE,
            tool_call_id="call_missing",
            original_tool_call={"id": "call_missing"},
            codex_tool_call={"id": "call_missing"},
            expire_at="2030-01-01T00:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        pm.close()
        key_path = tmp_path / KEY_FILENAME
        key_path.unlink()

        with pytest.raises(ToolMappingKeyError, match="missing"):
            PersistenceManager(str(tmp_path))
        assert not key_path.exists()

    def test_malformed_key_file_with_encrypted_rows_fails_closed(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.upsert_tool_call_mapping(
            **_TOOL_SCOPE,
            tool_call_id="call_bad_key_file",
            original_tool_call={"id": "call_bad_key_file"},
            codex_tool_call={"id": "call_bad_key_file"},
            expire_at="2030-01-01T00:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        pm.close()
        key_path = tmp_path / KEY_FILENAME
        key_path.write_text("v1:not-valid-base64\n")
        key_path.chmod(0o600)

        with pytest.raises(ToolMappingKeyError, match="base64-encoded"):
            PersistenceManager(str(tmp_path))

    def test_wrong_environment_key_fails_without_disclosing_value(
        self, tmp_path, monkeypatch
    ):
        first_key = base64.urlsafe_b64encode(b"a" * 32).decode()
        second_key = base64.urlsafe_b64encode(b"b" * 32).decode()
        monkeypatch.setenv(KEY_ENV_VAR, first_key)
        pm = PersistenceManager(str(tmp_path))
        pm.upsert_tool_call_mapping(
            **_TOOL_SCOPE,
            tool_call_id="call_wrong_key",
            original_tool_call={"id": "call_wrong_key"},
            codex_tool_call={"id": "call_wrong_key"},
            expire_at="2030-01-01T00:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        pm.close()
        monkeypatch.setenv(KEY_ENV_VAR, second_key)

        with pytest.raises(ToolMappingKeyError) as raised:
            PersistenceManager(str(tmp_path))
        assert first_key not in str(raised.value)
        assert second_key not in str(raised.value)
        assert not (tmp_path / KEY_FILENAME).exists()

    def test_tampered_ciphertext_fails_authentication_on_restart(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.upsert_tool_call_mapping(
            **_TOOL_SCOPE,
            tool_call_id="call_tamper",
            original_tool_call={"id": "call_tamper"},
            codex_tool_call={"id": "call_tamper"},
            expire_at="2030-01-01T00:00:00+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        payload = pm._conn.execute(
            "SELECT encrypted_payload FROM tool_call_mappings"
        ).fetchone()[0]
        tampered = bytes([payload[0] ^ 1]) + payload[1:]
        pm._conn.execute(
            "UPDATE tool_call_mappings SET encrypted_payload = ?", (tampered,)
        )
        pm._conn.commit()
        pm.close()

        with pytest.raises(ToolMappingIntegrityError, match="authentication"):
            PersistenceManager(str(tmp_path))

    def test_concurrent_key_creation_returns_one_canonical_key(self, tmp_path):
        def load_key() -> str:
            return ToolMappingCipher.load(tmp_path, create=True).key_id

        with ThreadPoolExecutor(max_workers=8) as pool:
            key_ids = list(pool.map(lambda _index: load_key(), range(32)))

        assert len(set(key_ids)) == 1
        assert stat.S_IMODE((tmp_path / KEY_FILENAME).stat().st_mode) == 0o600
        assert not list(tmp_path.glob(f".{KEY_FILENAME}.*.tmp"))

    def test_legacy_mapping_migration_discards_only_mapping_rows(
        self, tmp_path, caplog
    ):
        db_path = tmp_path / "gateway.db"
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE metrics (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metrics (key, value) VALUES ('sentinel', '{"ok": true}');
            CREATE TABLE tool_call_mappings (
                principal_id TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                model TEXT NOT NULL,
                session_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                original_tool_call TEXT NOT NULL,
                codex_tool_call TEXT NOT NULL,
                expire_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO tool_call_mappings VALUES (
                'p', 'provider', 'model', 'session', 'call',
                '{"secret":"[REDACTED]"}', '{}', '2030', '2026', '2026'
            );
        """)
        conn.close()

        with pytest.raises(RuntimeError, match="incompatible gateway.db schema"):
            PersistenceManager(str(tmp_path))

    def test_row_byte_limit_rejects_before_durable_mutation(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), tool_mapping_max_row_bytes=128)

        with pytest.raises(ToolMappingCapacityError, match="row bytes"):
            _upsert_mapping(pm, "too-large", payload="x" * 256)

        assert pm.count_tool_call_mappings() == 0
        pm.close()

    def test_session_row_limit_allows_replacement_and_preserves_it_on_rejection(
        self, tmp_path
    ):
        pm = PersistenceManager(str(tmp_path), tool_mapping_max_session_rows=1)
        _upsert_mapping(pm, "call", payload="old")
        _upsert_mapping(pm, "call", payload="replacement")

        with pytest.raises(ToolMappingCapacityError, match="session row count"):
            _upsert_mapping(pm, "second")

        rows = pm.query_tool_call_mappings(
            **_TOOL_SCOPE,
            now="2026-01-01T00:00:00+00:00",
        )
        assert len(rows) == 1
        assert rows[0]["original_tool_call"]["payload"] == "replacement"
        pm.close()

    @pytest.mark.parametrize(
        ("limit_name", "expected"),
        [
            ("tool_mapping_max_session_bytes", "session bytes"),
            ("tool_mapping_max_principal_bytes", "principal bytes"),
            ("tool_mapping_max_global_bytes", "global bytes"),
        ],
    )
    def test_hierarchical_byte_limits_reject_atomically(
        self, tmp_path, limit_name, expected
    ):
        pm = PersistenceManager(
            str(tmp_path),
            tool_mapping_max_row_bytes=4096,
            tool_mapping_max_session_bytes=(
                128 if limit_name == "tool_mapping_max_session_bytes" else 4096
            ),
            tool_mapping_max_principal_bytes=(
                128 if limit_name == "tool_mapping_max_principal_bytes" else 4096
            ),
            tool_mapping_max_global_bytes=(
                128 if limit_name == "tool_mapping_max_global_bytes" else 4096
            ),
        )

        with pytest.raises(ToolMappingCapacityError, match=expected):
            _upsert_mapping(pm, "call")

        assert pm.count_tool_call_mappings() == 0
        pm.close()

    def test_principal_and_global_row_limits_span_sessions_and_principals(
        self, tmp_path
    ):
        principal_dir = tmp_path / "principal"
        principal = PersistenceManager(
            str(principal_dir),
            tool_mapping_max_session_rows=4,
            tool_mapping_max_principal_rows=1,
        )
        _upsert_mapping(principal, "one")
        other_session = {**_TOOL_SCOPE, "session_id": "s2"}
        with pytest.raises(ToolMappingCapacityError, match="principal row count"):
            _upsert_mapping(principal, "two", scope=other_session)
        assert principal.count_tool_call_mappings() == 1
        principal.close()

        global_manager = PersistenceManager(
            str(tmp_path / "global"),
            tool_mapping_max_session_rows=4,
            tool_mapping_max_principal_rows=4,
            tool_mapping_max_global_rows=1,
        )
        _upsert_mapping(global_manager, "one")
        other_principal = {
            **_TOOL_SCOPE,
            "principal_id": "client-b",
            "session_id": "s2",
        }
        with pytest.raises(ToolMappingCapacityError, match="global row count"):
            _upsert_mapping(global_manager, "two", scope=other_principal)
        assert global_manager.count_tool_call_mappings() == 1
        global_manager.close()

    def test_expiry_releases_row_and_byte_budgets_inside_upsert(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), tool_mapping_max_global_rows=1)
        _upsert_mapping(
            pm,
            "expired",
            expire_at="2026-01-01T00:00:01+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )

        _upsert_mapping(
            pm,
            "current",
            timestamp="2026-01-01T00:00:02+00:00",
        )

        assert pm.count_tool_call_mappings() == 1
        rows = pm.query_tool_call_mappings(
            **_TOOL_SCOPE,
            now="2026-01-01T00:00:02+00:00",
        )
        assert [row["tool_call_id"] for row in rows] == ["current"]
        pm.close()

    def test_concurrent_upserts_cannot_oversubscribe_global_rows(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), tool_mapping_max_global_rows=1)

        def insert(index: int) -> bool:
            try:
                _upsert_mapping(
                    pm,
                    f"call-{index}",
                    scope={
                        **_TOOL_SCOPE,
                        "principal_id": f"client-{index}",
                        "session_id": f"session-{index}",
                    },
                )
            except ToolMappingCapacityError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = list(pool.map(insert, range(8)))

        assert sum(accepted) == 1
        assert pm.count_tool_call_mappings() == 1
        pm.close()

    def test_raw_sqlite_accounting_matches_canonical_row_size_and_fails_closed(
        self, tmp_path
    ):
        pm = PersistenceManager(str(tmp_path))
        _upsert_mapping(pm, "call")
        row = pm._conn.execute(
            "SELECT principal_id, provider_name, model, session_id, tool_call_id, "
            "key_id, nonce, encrypted_payload, expire_at, created_at, updated_at, "
            "mapping_bytes FROM tool_call_mappings"
        ).fetchone()
        expected = pm._tool_mapping_row_bytes(
            principal_id=row[0],
            provider_name=row[1],
            model=row[2],
            session_id=row[3],
            tool_call_id=row[4],
            key_id=row[5],
            nonce=row[6],
            encrypted_payload=row[7],
            expire_at=row[8],
            created_at=row[9],
            updated_at=row[10],
        )
        assert row[11] == expected
        pm._conn.execute(
            "UPDATE tool_call_mappings SET mapping_bytes = mapping_bytes + 1"
        )
        pm._conn.commit()

        with pytest.raises(ToolMappingCapacityError, match="accounting is invalid"):
            pm.query_tool_call_mappings(
                **_TOOL_SCOPE,
                now="2026-01-01T00:00:00+00:00",
            )
        pm.close()

    def test_encrypted_v1_schema_migration_backfills_without_decrypting_or_loss(
        self, tmp_path
    ):
        cipher = ToolMappingCipher.load(tmp_path, create=True)
        aad = mapping_aad(**_TOOL_SCOPE, tool_call_id="legacy-encrypted")
        nonce, encrypted_payload = cipher.encrypt(
            original_tool_call={"id": "legacy-encrypted", "payload": "original"},
            codex_tool_call={"id": "legacy-encrypted", "payload": "codex"},
            aad=aad,
        )
        conn = sqlite3.connect(tmp_path / "gateway.db")
        conn.execute("""
            CREATE TABLE tool_call_mappings (
                principal_id TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                model TEXT NOT NULL,
                session_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                payload_version INTEGER NOT NULL,
                key_id TEXT NOT NULL,
                nonce BLOB NOT NULL,
                encrypted_payload BLOB NOT NULL,
                expire_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (
                    principal_id, provider_name, model, session_id, tool_call_id
                )
            )
        """)
        conn.execute(
            "INSERT INTO tool_call_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                *_TOOL_SCOPE.values(),
                "legacy-encrypted",
                1,
                cipher.key_id,
                nonce,
                encrypted_payload,
                "2030-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

        with pytest.raises(RuntimeError, match="incompatible gateway.db schema"):
            PersistenceManager(str(tmp_path))

    def test_query_rejects_abnormal_oversized_session_before_loading(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), tool_mapping_max_session_rows=2)
        _upsert_mapping(pm, "one")
        _upsert_mapping(pm, "two")
        pm._tool_mapping_max_session_rows = 1

        with pytest.raises(ToolMappingCapacityError, match="session row count"):
            pm.query_tool_call_mappings(
                **_TOOL_SCOPE,
                now="2026-01-01T00:00:00+00:00",
            )
        pm.close()

    def test_sqlite_write_failure_rolls_back_expiry_and_replacement(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        _upsert_mapping(
            pm,
            "expired",
            payload="expired",
            expire_at="2026-01-01T00:00:01+00:00",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        _upsert_mapping(pm, "current", payload="old")
        pm._conn.execute("""
            CREATE TRIGGER fail_tool_mapping_update
            BEFORE UPDATE ON tool_call_mappings
            BEGIN
                SELECT RAISE(ABORT, 'simulated disk failure');
            END
        """)
        pm._conn.commit()

        with pytest.raises(sqlite3.IntegrityError, match="simulated disk failure"):
            _upsert_mapping(
                pm,
                "current",
                payload="new",
                timestamp="2026-01-01T00:00:02+00:00",
            )

        pm._conn.execute("DROP TRIGGER fail_tool_mapping_update")
        pm._conn.commit()
        assert pm.count_tool_call_mappings() == 2
        rows = pm.query_tool_call_mappings(
            **_TOOL_SCOPE,
            now="2026-01-01T00:00:00+00:00",
        )
        by_id = {row["tool_call_id"]: row for row in rows}
        assert by_id["current"]["original_tool_call"]["payload"] == "old"
        assert "expired" in by_id
        pm.close()


class TestPersistenceManagerRequestLog:
    def test_insert_and_query(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        entries = [_make_entry_dict(model=f"m-{i}") for i in range(5)]
        pm.insert_log_entries(entries)

        results, total = pm.query_log_entries(limit=10)
        assert total == 5
        assert len(results) == 5
        pm.close()

    def test_zero_row_provider_backfill_closes_transaction(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))

        assert pm._conn.in_transaction is False

        prepared = pm.prepare_update((), success_max=10, error_max=10)
        pm.commit_update(prepared)
        pm.close()

    def test_newest_first(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        e1 = _make_entry_dict(model="first")
        time.sleep(0.01)  # ensure distinct timestamps
        e2 = _make_entry_dict(model="second")
        pm.insert_log_entries([e1, e2])

        results, _ = pm.query_log_entries()
        assert results[0]["model"] == "second"
        assert results[1]["model"] == "first"
        pm.close()

    def test_filter_by_model(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries(
            [
                _make_entry_dict(model="gpt-4o"),
                _make_entry_dict(model="claude"),
                _make_entry_dict(model="gpt-4o"),
            ]
        )

        results, total = pm.query_log_entries(model="gpt-4o")
        assert total == 2
        assert all(r["model"] == "gpt-4o" for r in results)
        pm.close()

    def test_filter_by_provider(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries(
            [
                _make_entry_dict(provider="openai_chat"),
                _make_entry_dict(provider="anthropic"),
            ]
        )

        results, total = pm.query_log_entries(provider="anthropic")
        assert total == 1
        assert results[0]["target_provider"] == "anthropic"
        pm.close()

    def test_filter_by_status(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries(
            [
                _make_entry_dict(status=200),
                _make_entry_dict(status=500),
                _make_entry_dict(status=404),
            ]
        )

        ok_results, ok_total = pm.query_log_entries(status="ok")
        assert ok_total == 1

        err_results, err_total = pm.query_log_entries(status="error")
        assert err_total == 2
        pm.close()

    def test_filter_by_api_key_label(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries(
            [
                _make_entry_dict(api_key_label="alice"),
                _make_entry_dict(api_key_label="bob"),
                _make_entry_dict(api_key_label="alice"),
                _make_entry_dict(),  # no label
            ]
        )

        results, total = pm.query_log_entries(api_key_label="alice")
        assert total == 2
        assert all(r["api_key_label"] == "alice" for r in results)

        results, total = pm.query_log_entries(api_key_label="bob")
        assert total == 1
        pm.close()

    def test_get_api_key_labels(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries(
            [
                _make_entry_dict(api_key_label="bob"),
                _make_entry_dict(api_key_label="alice"),
                _make_entry_dict(api_key_label="bob"),
                _make_entry_dict(),
            ]
        )

        assert pm.get_api_key_labels() == ["alice", "bob"]
        pm.close()

    def test_pagination(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        entries = [_make_entry_dict(model=f"m-{i}") for i in range(20)]
        pm.insert_log_entries(entries)

        page1, total = pm.query_log_entries(limit=5, offset=0)
        assert total == 20
        assert len(page1) == 5

        page2, _ = pm.query_log_entries(limit=5, offset=5)
        assert len(page2) == 5
        assert page1[0]["id"] != page2[0]["id"]
        pm.close()

    def test_get_log_entry(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        entry = _make_entry_dict()
        pm.insert_log_entries([entry])

        found = pm.get_log_entry(entry["id"])
        assert found is not None
        assert found["id"] == entry["id"]
        assert found["model"] == entry["model"]
        pm.close()

    def test_get_log_entry_not_found(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        assert pm.get_log_entry("nonexistent") is None
        pm.close()

    def test_clear_log(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries([_make_entry_dict() for _ in range(5)])
        assert pm.count_log_entries() == 5

        pm.clear_log()
        assert pm.count_log_entries() == 0
        pm.close()

    def test_prune(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), success_max=10)
        # Insert 150 successful entries in batches to trigger prune.
        for batch in range(3):
            entries = [_make_entry_dict(model=f"m-{batch}-{i}") for i in range(50)]
            pm.insert_log_entries(entries)

        assert pm.count_success_entries() <= 10
        pm.close()


class TestPersistenceManagerRetention:
    """Dual-threshold prune: success and error caps are independent."""

    def test_defaults(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        assert pm.success_max == DEFAULT_SUCCESS_MAX
        assert pm.error_max == DEFAULT_ERROR_MAX
        pm.close()

    def test_explicit_caps(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), success_max=123, error_max=45)
        assert pm.success_max == 123
        assert pm.error_max == 45
        pm.close()

    def test_errors_not_evicted_by_success_flood(self, tmp_path):
        # Tiny success cap, generous error cap: a flood of successes must
        # not evict the rare error rows.
        pm = PersistenceManager(str(tmp_path), success_max=20, error_max=10)

        err_entries = [_make_entry_dict(status=500, model=f"e-{i}") for i in range(5)]
        pm.insert_log_entries(err_entries)

        for batch in range(2):
            ok_entries = [_make_entry_dict(model=f"ok-{batch}-{i}") for i in range(100)]
            pm.insert_log_entries(ok_entries)

        assert pm.count_success_entries() <= 20
        assert pm.count_error_entries() == 5
        pm.close()

    def test_error_cap_pruned_independently(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), success_max=1000, error_max=10)
        # 150 errors, batched to trigger periodic prune at 100.
        for batch in range(3):
            entries = [
                _make_entry_dict(status=500, model=f"e-{batch}-{i}") for i in range(50)
            ]
            pm.insert_log_entries(entries)

        assert pm.count_error_entries() <= 10
        assert pm.count_success_entries() == 0
        pm.close()

    def test_policy_update_decreases_caps_immediately_and_can_rollback(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), success_max=10, error_max=10)
        pm.insert_log_entries(
            [_make_entry_dict(model=f"ok-{index}") for index in range(5)]
            + [
                _make_entry_dict(status=500, model=f"error-{index}")
                for index in range(4)
            ]
        )

        prepared = pm.prepare_update(
            {"new-token"},
            success_max=2,
            error_max=1,
        )
        rollback = pm.commit_update(prepared)

        assert pm.success_max == 2
        assert pm.error_max == 1
        assert pm.count_success_entries() == 2
        assert pm.count_error_entries() == 1
        assert pm.redact_sensitive("new-token") == "[REDACTED]"

        pm.rollback_update(rollback)

        assert pm.success_max == 10
        assert pm.error_max == 10
        assert pm.count_success_entries() == 5
        assert pm.count_error_entries() == 4
        assert pm.redact_sensitive("new-token") == "new-token"
        pm.close()

    def test_policy_update_increases_caps_without_deleting_rows(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), success_max=10, error_max=10)
        pm.insert_log_entries(
            [_make_entry_dict() for _ in range(5)]
            + [_make_entry_dict(status=500) for _ in range(4)]
        )

        pm.commit_update(pm.prepare_update((), success_max=20, error_max=30))

        assert pm.success_max == 20
        assert pm.error_max == 30
        assert pm.count_success_entries() == 5
        assert pm.count_error_entries() == 4
        pm.close()

    def test_partial_prune_failure_rolls_back_caps_and_rows(
        self,
        tmp_path,
        monkeypatch,
    ):
        pm = PersistenceManager(str(tmp_path), success_max=10, error_max=10)
        pm.insert_log_entries(
            [_make_entry_dict() for _ in range(5)]
            + [_make_entry_dict(status=500) for _ in range(4)]
        )

        def fail_after_first_delete(*, commit: bool = True) -> None:
            assert commit is False
            pm._conn.execute("DELETE FROM request_log WHERE status_code < 400")
            raise RuntimeError("simulated prune failure")

        monkeypatch.setattr(pm, "_prune", fail_after_first_delete)

        with pytest.raises(RuntimeError, match="simulated prune failure"):
            pm.commit_update(pm.prepare_update((), success_max=2, error_max=1))

        assert pm.success_max == 10
        assert pm.error_max == 10
        assert pm.count_success_entries() == 5
        assert pm.count_error_entries() == 4
        pm.close()

    def test_commit_failure_rolls_back_caps_and_rows(self, tmp_path, monkeypatch):
        pm = PersistenceManager(str(tmp_path), success_max=10, error_max=10)
        pm.insert_log_entries(
            [_make_entry_dict() for _ in range(5)]
            + [_make_entry_dict(status=500) for _ in range(4)]
        )

        def fail_commit() -> None:
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(pm, "_commit_retention_transaction", fail_commit)

        with pytest.raises(RuntimeError, match="simulated commit failure"):
            pm.commit_update(pm.prepare_update((), success_max=2, error_max=1))

        assert pm.success_max == 10
        assert pm.error_max == 10
        assert pm.count_success_entries() == 5
        assert pm.count_error_entries() == 4
        pm.close()

    def test_restart_applies_current_caps_to_existing_rows(self, tmp_path):
        pm = PersistenceManager(str(tmp_path), success_max=10, error_max=10)
        pm.insert_log_entries(
            [_make_entry_dict() for _ in range(5)]
            + [_make_entry_dict(status=500) for _ in range(4)]
        )
        pm.close()

        restarted = PersistenceManager(str(tmp_path), success_max=3, error_max=2)

        assert restarted.count_success_entries() == 3
        assert restarted.count_error_entries() == 2
        restarted.close()

    def test_count_success_and_error_separately(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries(
            [
                _make_entry_dict(status=200),
                _make_entry_dict(status=201),
                _make_entry_dict(status=404),
                _make_entry_dict(status=500),
                _make_entry_dict(status=502),
            ]
        )
        assert pm.count_log_entries() == 5
        assert pm.count_success_entries() == 2
        assert pm.count_error_entries() == 3
        pm.close()


class TestPersistenceManagerSizes:
    def test_db_file_sizes_keys(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        sizes = pm.db_file_sizes()
        assert set(sizes.keys()) == {"db_bytes", "wal_bytes", "shm_bytes"}
        assert all(isinstance(v, int) for v in sizes.values())
        pm.close()

    def test_db_file_sizes_nonzero_after_insert(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries([_make_entry_dict(model=f"m-{i}") for i in range(50)])
        sizes = pm.db_file_sizes()
        # Main db file always exists after init; WAL is created on first write.
        assert sizes["db_bytes"] > 0
        assert sizes["wal_bytes"] >= 0
        pm.close()

    def test_bool_roundtrip(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        e = RequestLogEntry.create(
            model="test",
            source_provider="a",
            target_provider="b",
            is_stream=True,
            status_code=200,
            duration_ms=1.0,
        )
        pm.insert_log_entries([e.to_dict()])

        results, _ = pm.query_log_entries()
        assert results[0]["is_stream"] is True
        pm.close()

    def test_error_detail_stored(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries(
            [
                _make_entry_dict(error_detail="upstream 500: internal error"),
            ]
        )

        results, _ = pm.query_log_entries()
        assert results[0]["error_detail"] == "upstream 500: internal error"
        pm.close()

    def test_request_metadata_is_token_redacted_at_sqlite_write_boundary(
        self, tmp_path
    ):
        pm = PersistenceManager(str(tmp_path), token_values={"provider-token"})
        entry = _make_entry_dict(
            error_detail=(
                "Bearer bearer-token provider-token ordinary-password ordinary-secret"
            )
        )
        entry["profile"] = {
            "stream_error": "provider-token",
            "authorization": "Bearer profile-token",
            "password": "ordinary-password",
            "client_secret": "ordinary-client-secret",
        }
        pm.insert_log_entries([entry])

        pm.update_entry_profile(
            entry["id"],
            {
                "stream_error": "Bearer update-token provider-token",
                "proxy_password": "ordinary-proxy-password",
            },
        )

        row = pm._conn.execute(
            "SELECT error_detail, profile FROM request_log WHERE id = ?",
            (entry["id"],),
        ).fetchone()
        assert row is not None
        persisted = " ".join(str(value) for value in row)
        for raw_token in (
            "bearer-token",
            "provider-token",
            "profile-token",
            "update-token",
        ):
            assert raw_token not in persisted
        assert "ordinary-password" in persisted
        assert "ordinary-secret" in persisted
        assert "ordinary-client-secret" in persisted
        assert "ordinary-proxy-password" in persisted
        pm.close()

    def test_none_fields_omitted(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.insert_log_entries([_make_entry_dict()])

        results, _ = pm.query_log_entries()
        assert "error_detail" not in results[0]
        assert "api_key_label" not in results[0]
        assert "client_ip" not in results[0]
        pm.close()


class TestPersistenceManagerMetrics:
    def test_save_and_load(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        data = {"total_requests": 42, "total_errors": 3}
        pm.save_metrics(data)

        loaded = pm.load_metrics()
        assert loaded == data
        pm.close()

    def test_load_empty(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        assert pm.load_metrics() is None
        pm.close()

    def test_overwrite(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        pm.save_metrics({"total_requests": 10})
        pm.save_metrics({"total_requests": 20})

        loaded = pm.load_metrics()
        assert loaded is not None
        assert loaded["total_requests"] == 20
        pm.close()


# -- Legacy migration tests --


class TestLegacyPersistenceRejected:
    def test_legacy_jsonl_is_rejected(self, tmp_path):
        # Write legacy JSONL
        entries = [_make_entry_dict(model=f"legacy-{i}") for i in range(3)]
        jsonl_path = tmp_path / "request_log.jsonl"
        with open(jsonl_path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        with pytest.raises(RuntimeError, match="legacy persistence files"):
            PersistenceManager(str(tmp_path))
        assert jsonl_path.exists()

    def test_legacy_metrics_json_is_rejected(self, tmp_path):
        metrics_path = tmp_path / "metrics.json"
        metrics_path.write_text(json.dumps({"total_requests": 99}))

        with pytest.raises(RuntimeError, match="legacy persistence files"):
            PersistenceManager(str(tmp_path))
        assert metrics_path.exists()

    def test_legacy_gzip_backups_are_rejected(self, tmp_path):
        # Write gzipped backup
        entries = [_make_entry_dict(model=f"gz-{i}") for i in range(5)]
        gz_path = tmp_path / "request_log.1.jsonl.gz"
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        # Also need the main file to trigger migration
        (tmp_path / "request_log.jsonl").write_text("")

        with pytest.raises(RuntimeError, match="legacy persistence files"):
            PersistenceManager(str(tmp_path))
        assert gz_path.exists()

    def test_no_migration_when_clean(self, tmp_path):
        # No legacy files — should just start clean
        pm = PersistenceManager(str(tmp_path))
        assert pm.count_log_entries() == 0
        assert pm.load_metrics() is None
        pm.close()


# -- RequestLog with persistence integration --


class TestRequestLogWithPersistence:
    def test_add_and_get(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        log = RequestLog(persistence=pm)
        log.add(_make_entry())

        entries, total = log.get_entries()
        assert total == 1
        assert len(entries) == 1
        pm.close()

    def test_filter_by_model(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        log = RequestLog(persistence=pm)
        log.add(_make_entry(model="gpt-4o"))
        log.add(_make_entry(model="claude"))
        log.add(_make_entry(model="gpt-4o"))

        entries, total = log.get_entries(model="gpt-4o")
        assert total == 2
        assert all(e["model"] == "gpt-4o" for e in entries)
        pm.close()

    def test_filter_by_status(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        log = RequestLog(persistence=pm)
        log.add(_make_entry(status=200))
        log.add(_make_entry(status=500))
        log.add(_make_entry(status=404))

        _, ok_total = log.get_entries(status="ok")
        assert ok_total == 1
        _, err_total = log.get_entries(status="error")
        assert err_total == 2
        pm.close()

    def test_clear(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        log = RequestLog(persistence=pm)
        log.add(_make_entry())
        log.add(_make_entry())
        assert len(log) == 2
        log.clear()
        assert len(log) == 0
        pm.close()

    def test_get_entry_by_id(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        log = RequestLog(persistence=pm)
        e = _make_entry()
        log.add(e)

        found = log.get_entry(e.id)
        assert found is not None
        assert found["id"] == e.id
        pm.close()

    def test_pending_returns_empty(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        log = RequestLog(persistence=pm)
        log.add(_make_entry())
        assert log.pending_entries() == []
        pm.close()

    def test_newest_first(self, tmp_path):
        pm = PersistenceManager(str(tmp_path))
        log = RequestLog(persistence=pm)
        log.add(_make_entry(model="first"))
        time.sleep(0.01)
        log.add(_make_entry(model="second"))

        entries, _ = log.get_entries()
        assert entries[0]["model"] == "second"
        assert entries[1]["model"] == "first"
        pm.close()
