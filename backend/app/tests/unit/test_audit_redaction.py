"""CHR-183 / CHR-OPS-002 audit details never carry credentials or raw content."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.core.errors import APIError
from app.services.audit import REDACTED, _safe, audit_rejections


def test_credentials_session_values_and_raw_content_are_redacted():
    identifier = UUID("00000000-0000-4000-8000-000000000183")
    safe = _safe(
        {
            "password": "hunter2",
            "new_password_hash": "$argon2id$...",
            "preview_token": "signed",
            "session_cookie": "abc",
            "client_secret": "s",
            "nested": [{"auth_token": "t", "count": 2}],
            "file": b"raw,historical,rows",
            "note": "x" * 2000,
            "id": identifier,
            "at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "support": Decimal("0.25"),
            "flag": True,
            "missing": None,
        }
    )

    for key in ("password", "new_password_hash", "preview_token", "session_cookie", "client_secret"):
        assert safe[key] == REDACTED
    assert safe["nested"] == [{"auth_token": REDACTED, "count": 2}]
    assert safe["file"] == "[binary omitted]"
    assert len(safe["note"]) < 600 and safe["note"].endswith("[truncated]")
    assert safe["id"] == str(identifier)
    assert safe["at"] == "2026-01-01T00:00:00+00:00"
    assert safe["support"] == "0.25"
    assert (safe["flag"], safe["missing"]) == (True, None)


def test_rejections_keep_only_scalar_error_details():
    db = MagicMock()
    details = {
        "hard_conflict_count": 3,
        "batch_id": "b",
        "unassigned_class_ids": ["c"] * 5000,
        "conflicts": [{"class_ids": ["c"]}],
    }

    with pytest.raises(APIError):
        with audit_rejections(db, actor_id=None, operation="x", target_type="y"):
            raise APIError(409, "SCHEDULE_NOT_EXPORTABLE", "no", details)

    [event] = [call.args[0] for call in db.add.call_args_list]
    assert event.outcome == "rejected"
    assert event.details["error_details"] == {"hard_conflict_count": 3, "batch_id": "b"}


def test_a_failure_to_record_a_rejection_never_masks_the_refusal():
    db = MagicMock()
    db.rollback.side_effect = RuntimeError("connection lost")

    with pytest.raises(APIError) as raised:
        with audit_rejections(db, actor_id=None, operation="x", target_type="y"):
            raise APIError(409, "REFUSED", "no")
    assert raised.value.code == "REFUSED"
