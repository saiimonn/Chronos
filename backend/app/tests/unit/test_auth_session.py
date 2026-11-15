from datetime import datetime, timedelta, timezone

from app.models import AuthSession


def test_session_validity_accounts_for_expiry_and_revocation():
    now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    session = AuthSession(
        token_hash="a" * 64,
        created_at=now,
        expires_at=now + timedelta(hours=8),
    )

    assert not session.is_valid(now - timedelta(seconds=1))
    assert session.is_valid(now)
    assert not session.is_valid(session.expires_at)

    revoked_at = now + timedelta(hours=1)
    session.revoke(revoked_at)
    assert session.revoked_at == revoked_at
    assert not session.is_valid(revoked_at)


def test_revocation_is_idempotent():
    now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    session = AuthSession(
        token_hash="a" * 64,
        created_at=now,
        expires_at=now + timedelta(hours=8),
    )

    session.revoke(now + timedelta(hours=1))
    session.revoke(now + timedelta(hours=2))

    assert session.revoked_at == now + timedelta(hours=1)
