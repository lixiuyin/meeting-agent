"""Idempotency lifecycle retention must survive payload-key rotation."""

import stat
from datetime import UTC, datetime, timedelta

from src.core.database import idempotency
from src.core.database.idempotency import cleanup_expired_idempotency_keys


def _timestamp(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%d %H:%M:%S")


def _insert(db_conn, *, key: str, state: str, created: timedelta) -> None:
    db_conn.execute(
        "INSERT INTO idempotency_keys "
        "(key, method, path, user_id, response_body, created_at, expires_at, lifecycle_state) "
        "VALUES (?, 'POST', '/resource', 'user', 'not-decryptable', ?, ?, ?)",
        (key, _timestamp(created), _timestamp(timedelta(hours=-1)), state),
    )


def test_expired_completed_payload_is_deleted_without_decryption(db_conn) -> None:
    _insert(db_conn, key="completed", state="completed", created=timedelta(days=-1))

    assert cleanup_expired_idempotency_keys(db_conn) == 1
    assert db_conn.execute("SELECT 1 FROM idempotency_keys").fetchone() is None


def test_recent_ambiguous_commit_is_retained_fail_closed(db_conn) -> None:
    _insert(db_conn, key="ambiguous", state="effects_committed", created=timedelta(days=-1))

    assert cleanup_expired_idempotency_keys(db_conn) == 0
    assert db_conn.execute("SELECT 1 FROM idempotency_keys").fetchone() is not None


def test_ambiguous_or_legacy_rows_have_a_bounded_retention(db_conn) -> None:
    _insert(db_conn, key="ambiguous", state="effects_committed", created=timedelta(days=-8))
    _insert(db_conn, key="legacy", state="legacy_unknown", created=timedelta(days=-8))

    assert cleanup_expired_idempotency_keys(db_conn) == 2
    assert db_conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0


def test_development_key_is_private_and_survives_process_cache_reset(tmp_path) -> None:
    first = idempotency._load_or_create_dev_key(tmp_path)
    second = idempotency._load_or_create_dev_key(tmp_path)

    assert first == second
    key_path = tmp_path / ".idempotency-key"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert key_path.read_text().strip() == first


def test_development_key_rejects_insecure_permissions(tmp_path) -> None:
    key_path = tmp_path / ".idempotency-key"
    key_path.write_text("a" * 64)
    key_path.chmod(0o644)

    try:
        idempotency._load_or_create_dev_key(tmp_path)
    except RuntimeError as exc:
        assert "permissions must be 0600" in str(exc)
    else:
        raise AssertionError("insecure idempotency key permissions were accepted")
