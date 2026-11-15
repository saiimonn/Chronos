from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import APIError
from app.services.schedules import create_master_schedule


class _UniqueViolation(Exception):
    class diag:
        constraint_name = "uq_schedule_one_master_per_term"


def test_create_master_schedule_maps_a_concurrent_unique_conflict_to_409():
    db = Mock()
    db.get.return_value = object()
    db.scalar.return_value = None
    db.commit.side_effect = IntegrityError("insert", {}, _UniqueViolation())

    with pytest.raises(APIError) as error:
        create_master_schedule(db, uuid4())

    assert error.value.status_code == 409
    assert error.value.code == "MASTER_SCHEDULE_EXISTS"
    db.rollback.assert_called_once()
