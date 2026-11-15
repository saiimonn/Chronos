"""CHR-120 / CHR-ING-005 staged historical upload, review, and confirm API tests.

The upload takes the Offered Courses workbook. Terms, course scope, and the
teacher-to-faculty mapping come from the local catalog.
"""

import io
from base64 import b64encode
from dataclasses import replace
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.models import (
    Faculty,
    HistoricalImportBatch,
    HistoricalRecord,
    HistoricalSnapshot,
    RuleSet,
    Subject,
    Term,
)
from app.services.ingestion import historical_upload
from app.services.ingestion.historical_import import (
    HistoricalImportError,
    validate_historical_import,
)
from app.services.ingestion.offered_courses import REQUIRED_COLUMNS
from app.tests.support.api import make_api_client, make_client
from app.tests.support.historical_bootstrap import FACULTY_ID

pytestmark = pytest.mark.postgres

URL = "/api/v1/historical-uploads"
TEACHER = "DELA CRUZ, JUAN M."
OWNER = "DEPARTMENT OF COMPUTER, INFORMATION SCIENCES AND MATHEMATICS"


def _row(
    group="1",
    code="CIS 1101",
    period="1ST Semester - 2023",
    teacher=TEACHER,
    schedule="MWF 08:00 AM - 09:00 AM LB447TC",
    status="REGULAR",
    title="PROGRAMMING 1",
) -> list:
    values = {
        "Group Number": f"  {group}" if group else None,
        "Course Code": code,
        "Description": title,
        "Academic Units": 3,
        "Academic Period - Year": period,
        "Gender": "COED",
        "Course Status": status,
        "Request Status": "Approved",
        "Population": "0/40",
        "Teacher/s": teacher,
        "Disapproved Remarks": None,
        "Disapproved Date": None,
        "Schedule": schedule,
        "Owner": OWNER,
    }
    return [values[name] for name in REQUIRED_COLUMNS]


def _continuation(schedule: str) -> list:
    return [schedule if name == "Schedule" else None for name in REQUIRED_COLUMNS]


def _two_term_sheets() -> dict[str, list[list]]:
    return {
        "S1 AY2023-24": [_row()],
        "S2 AY2023-24": [
            _row(
                code="CIS 1102",
                period="2ND Semester - 2023",
                schedule="TTh 01:00 PM - 02:30 PM LB448TC",
            )
        ],
    }


def _workbook(sheets: dict[str, list[list]] | None = None, *, extra: str | None = None) -> bytes:
    """A synthetic Offered Courses export; `extra` adds an ignored sheet so the
    bytes differ while the records stay the same."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in (sheets or _two_term_sheets()).items():
        sheet = workbook.create_sheet(name)
        sheet.append(list(REQUIRED_COLUMNS))
        for row in rows:
            sheet.append(row)
    if extra:
        workbook.create_sheet(extra).append(["notes"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _payload(data: bytes | None = None) -> dict:
    return {"workbook": b64encode(_workbook() if data is None else data).decode()}


def _seed(db: Session, *, faculty: bool = True, terms: bool = True) -> None:
    db.add_all(
        Subject(subject_code=code, subject_title=title, units=3, subject_type="LEC")
        for code, title in (("CIS 1101", "Programming 1"), ("CIS 1102", "Programming 2"))
    )
    if faculty:
        db.add(
            Faculty(
                faculty_id=FACULTY_ID,
                full_name="Juan M. Dela Cruz",
                is_full_time=False,
                availability_mask="",
            )
        )
    if terms:
        _add_terms(db)
    db.commit()


def _add_terms(db: Session) -> None:
    db.add_all(
        Term(academic_year="2023-2024", term_number=n, is_active=False) for n in (1, 2)
    )


def _count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _admin(db: Session):
    return make_api_client(db, role="department_administrator")


def test_valid_workbook_is_staged_without_writing_history(db_session: Session):
    _seed(db_session)

    resp = _admin(db_session).post(URL, json=_payload())

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "staged"
    assert (body["accepted_count"], body["rejected_count"], body["skipped_count"]) == (2, 0, 0)
    assert (body["populated_period_count"], body["empty_period_count"]) == (2, 0)
    assert (body["unknown_subject_count"], body["unknown_room_count"]) == (0, 2)
    assert body["rejections"] == body["skipped_rows"] == []
    assert _count(db_session, HistoricalSnapshot) == 0
    assert _count(db_session, HistoricalRecord) == 0


def test_confirming_the_reviewed_batch_imports_it_atomically(db_session: Session):
    _seed(db_session)
    client = _admin(db_session)
    staged = client.post(URL, json=_payload()).json()

    resp = client.post(
        f"{URL}/{staged['batch_id']}/confirm",
        json={"content_sha256": staged["content_sha256"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["batch"]["status"] == "committed"
    assert body["batch"]["committed_at"] is not None
    assert len(body["snapshot_ids"]) == 2
    assert body["mining_or_activation_performed"] is False
    records = db_session.scalars(select(HistoricalRecord)).all()
    assert {r.source_faculty_id for r in records} == {FACULTY_ID}
    assert {(r.subject_code, r.meeting_days, r.time_slot, r.room_code) for r in records} == {
        ("CIS 1101", "MWF", "08:00-09:00", "LB447TC"),
        ("CIS 1102", "TTh", "13:00-14:30", "LB448TC"),
    }
    assert _count(db_session, RuleSet) == 0

    again = client.post(
        f"{URL}/{staged['batch_id']}/confirm",
        json={"content_sha256": staged["content_sha256"]},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "HISTORY_UPLOAD_NOT_STAGED"


def test_continuation_rows_become_separate_meetings(db_session: Session):
    _seed(db_session)
    sheets = {
        "S1 AY2023-24": [
            _row(schedule="MW 08:00 AM - 09:00 AM LB447TC"),
            _continuation("F 08:00 AM - 10:00 AM LB300TC"),
        ]
    }

    body = _admin(db_session).post(URL, json=_payload(_workbook(sheets))).json()

    assert (body["accepted_count"], body["rejected_count"]) == (2, 0)


def test_rows_that_are_not_history_are_skipped_without_blocking(db_session: Session):
    _seed(db_session)
    client = _admin(db_session)
    sheets = {
        "S1 AY2023-24": [
            _row(),
            _row(group="2", status="DISSOLVED"),
            _row(code="ICT 146"),
            _row(group="3", teacher=None),
            _row(group="4", schedule=None),
            _row(group="5", period="2ND Semester - 2023"),
            _row(),
            list(REQUIRED_COLUMNS),
        ],
        "Summer 2030": [_row(period="Summer - 2030")],
        "Pivot": [_row()],
    }

    body = client.post(URL, json=_payload(_workbook(sheets))).json()

    assert (body["status"], body["accepted_count"], body["rejected_count"]) == ("staged", 1, 0)
    reasons = {(item["file"], item["line"]): item["reason"] for item in body["skipped_rows"]}
    sheet = "S1 AY2023-24"
    assert reasons[(sheet, 3)] == "dissolved course"
    assert reasons[(sheet, 4)] == "course ICT 146 is not in the Subject catalog"
    assert reasons[(sheet, 5)] == "no teacher assigned"
    assert reasons[(sheet, 6)] == "no schedule"
    assert "not this sheet's 2023-2024 Term 1" in reasons[(sheet, 7)]
    assert reasons[(sheet, 8)] == "duplicate of row 2"
    assert reasons[(sheet, 9)] == "repeated header row"
    assert "no Term exists for 2029-2030 Term 3" in reasons[("Summer 2030", None)]
    assert "not a term" in reasons[("Pivot", None)]
    assert body["skipped_count"] == len(body["skipped_rows"]) == 9
    confirm = client.post(
        f"{URL}/{body['batch_id']}/confirm", json={"content_sha256": body["content_sha256"]}
    )
    assert confirm.status_code == 200
    assert _count(db_session, HistoricalRecord) == 1


def test_active_and_already_imported_terms_are_skipped(db_session: Session):
    _seed(db_session, terms=False)
    db_session.add_all(
        [
            Term(academic_year="2023-2024", term_number=1, is_active=False),
            Term(academic_year="2023-2024", term_number=2, is_active=True),
        ]
    )
    db_session.commit()
    client = _admin(db_session)

    first = client.post(URL, json=_payload()).json()
    assert first["accepted_count"] == 1
    assert [item["reason"] for item in first["skipped_rows"]] == [
        "2023-2024 Term 2 is the active term, not history"
    ]
    client.post(
        f"{URL}/{first['batch_id']}/confirm", json={"content_sha256": first["content_sha256"]}
    )

    again = client.post(URL, json=_payload(_workbook(extra="Notes")))
    assert again.status_code == 422
    reasons = [item["reason"] for item in client.get(
        f"{URL}/{again.json()['details']['batch_id']}"
    ).json()["skipped_rows"]]
    assert "2023-2024 Term 1 history was already imported" in reasons


def test_blocking_rows_are_reported_and_prevent_confirmation(db_session: Session):
    _seed(db_session)
    client = _admin(db_session)
    sheets = {
        "S1 AY2023-24": [
            _row(),
            _row(group="2", teacher="NOBODY, KNOWN X."),
            _row(group="3", schedule="sometime in LB447TC"),
        ]
    }

    body = client.post(URL, json=_payload(_workbook(sheets))).json()

    assert (body["status"], body["accepted_count"], body["rejected_count"]) == ("staged", 1, 2)
    assert body["rejections"][0] == {
        "file": "S1 AY2023-24",
        "line": 3,
        "reason": "no Faculty record matches teacher 'NOBODY, KNOWN X.'; "
        "add or rename them on the Roster",
    }
    assert body["rejections"][1]["line"] == 4
    assert "is not '<days> <start> - <end> <room>'" in body["rejections"][1]["reason"]
    detail = client.get(f"{URL}/{body['batch_id']}").json()
    assert detail["rejections"] == body["rejections"]

    resp = client.post(
        f"{URL}/{body['batch_id']}/confirm", json={"content_sha256": body["content_sha256"]}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "HISTORY_UPLOAD_HAS_REJECTIONS"
    assert _count(db_session, HistoricalRecord) == 0


def test_teacher_matches_the_roster_id_the_faculty_import_assigned(db_session: Session):
    from app.services.ingestion.faculty_names import historical_faculty_id

    _seed(db_session, faculty=False)
    generated = historical_faculty_id(TEACHER)
    # A roster rename does not break the link the import CLI made.
    db_session.add(
        Faculty(faculty_id=generated, full_name="J. Dela Cruz", is_full_time=False, availability_mask="")
    )
    db_session.commit()
    client = _admin(db_session)

    body = client.post(URL, json=_payload()).json()
    client.post(f"{URL}/{body['batch_id']}/confirm", json={"content_sha256": body["content_sha256"]})

    assert body["rejected_count"] == 0
    assert set(db_session.scalars(select(HistoricalRecord.source_faculty_id))) == {generated}


def test_a_workbook_with_nothing_to_import_leaves_a_rejected_batch(db_session: Session):
    _seed(db_session, terms=False)
    client = _admin(db_session)

    resp = client.post(URL, json=_payload())

    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "HISTORY_UPLOAD_INVALID"
    assert body["message"] == "Offered Courses.xlsx: no row can be imported"
    batch = client.get(f"{URL}/{body['details']['batch_id']}").json()
    assert (batch["status"], batch["rejected_count"], batch["skipped_count"]) == ("rejected", 1, 2)
    assert batch["rejections"] == [{"file": "workbook", "line": None, "reason": body["message"]}]


def test_upload_rejected_for_missing_terms_can_be_retried_once_they_exist(db_session: Session):
    _seed(db_session, terms=False)
    client = _admin(db_session)
    payload = _payload()

    first = client.post(URL, json=payload)
    assert first.status_code == 422
    _add_terms(db_session)
    db_session.commit()

    retry = client.post(URL, json=payload)

    assert retry.status_code == 201
    staged = retry.json()
    assert staged["rejected_count"] == 0
    assert _count(db_session, HistoricalImportBatch) == 2
    stale = client.post(
        f"{URL}/{first.json()['details']['batch_id']}/confirm",
        json={"content_sha256": "0" * 64},
    )
    assert stale.json()["code"] == "HISTORY_UPLOAD_HAS_REJECTIONS"
    confirm = client.post(
        f"{URL}/{staged['batch_id']}/confirm", json={"content_sha256": staged["content_sha256"]}
    )
    assert confirm.status_code == 200
    assert _count(db_session, HistoricalRecord) == 2


def _not_an_export() -> bytes:
    workbook = Workbook()
    workbook.active.title = "S1 AY2023-24"
    workbook.active.append(["Teacher", "Subject", "Room", "Meeting Days", "Time Slot"])
    workbook.active.append(["DOE JANE", "CS 101", "LB 101", "MW", "07:30-09:00"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (
            b"Teacher,Subject\nDOE JANE,CS 101\n",
            "Offered Courses.xlsx: the file is not a readable .xlsx workbook",
        ),
        (
            _not_an_export(),
            "Offered Courses.xlsx: no sheet has the Offered Courses columns; "
            "download the template for the expected format",
        ),
    ],
    ids=["csv", "other-xlsx"],
)
def test_only_an_offered_courses_workbook_is_accepted(db_session: Session, data, reason):
    _seed(db_session)
    client = _admin(db_session)

    resp = client.post(URL, json=_payload(data))
    again = client.post(URL, json=_payload(data))

    assert resp.status_code == 422
    body = resp.json()
    assert (body["code"], body["message"]) == ("HISTORY_UPLOAD_INVALID", reason)
    batch = client.get(f"{URL}/{body['details']['batch_id']}").json()
    assert (batch["status"], batch["accepted_count"]) == ("rejected", 0)
    assert batch["rejections"] == [{"file": "workbook", "line": None, "reason": reason}]
    # CHR-191: a retry is revalidated as a new batch; the first stays as audit.
    assert again.json()["details"]["batch_id"] != body["details"]["batch_id"]
    confirm = client.post(
        f"{URL}/{batch['batch_id']}/confirm", json={"content_sha256": batch["content_sha256"]}
    )
    assert confirm.json()["code"] == "HISTORY_UPLOAD_HAS_REJECTIONS"
    assert _count(db_session, HistoricalRecord) == 0


def test_the_old_four_file_bundle_payload_is_refused(db_session: Session):
    resp = _admin(db_session).post(
        URL,
        json={
            "canonical_records": "YQ==",
            "term_manifest": "YQ==",
            "source_manifest": "YQ==",
            "faculty_crosswalk": "YQ==",
        },
    )

    assert resp.status_code == 422


def test_identical_content_is_refused_on_reupload(db_session: Session):
    _seed(db_session)
    client = _admin(db_session)
    payload = _payload()

    assert client.post(URL, json=payload).status_code == 201
    second = client.post(URL, json=payload)

    assert second.status_code == 409
    assert second.json()["code"] == "HISTORY_UPLOAD_DUPLICATE"
    assert _count(db_session, HistoricalImportBatch) == 1


def test_committed_content_is_still_refused_as_duplicate(db_session: Session):
    _seed(db_session)
    client = _admin(db_session)
    staged = client.post(URL, json=_payload()).json()
    client.post(
        f"{URL}/{staged['batch_id']}/confirm", json={"content_sha256": staged["content_sha256"]}
    )

    # The terms are captured now, so the conversion differs: nothing to import.
    again = client.post(URL, json=_payload())

    assert again.status_code == 422
    assert _count(db_session, HistoricalRecord) == 2


def test_confirming_with_a_different_hash_is_refused(db_session: Session):
    _seed(db_session)
    client = _admin(db_session)
    staged = client.post(URL, json=_payload()).json()

    resp = client.post(f"{URL}/{staged['batch_id']}/confirm", json={"content_sha256": "0" * 64})

    assert resp.status_code == 409
    assert resp.json()["code"] == "HISTORY_UPLOAD_STALE"
    assert _count(db_session, HistoricalRecord) == 0


def test_a_batch_made_stale_by_another_import_is_refused(db_session: Session):
    _seed(db_session)
    client = _admin(db_session)
    first = client.post(URL, json=_payload()).json()
    # Same records, different bytes: both stage, only one may import.
    second = client.post(URL, json=_payload(_workbook(extra="Notes"))).json()
    assert client.post(
        f"{URL}/{first['batch_id']}/confirm", json={"content_sha256": first["content_sha256"]}
    ).status_code == 200

    resp = client.post(
        f"{URL}/{second['batch_id']}/confirm", json={"content_sha256": second["content_sha256"]}
    )

    assert resp.status_code == 409
    assert resp.json()["code"] == "HISTORY_UPLOAD_STALE"
    assert _count(db_session, HistoricalRecord) == 2
    assert client.get(f"{URL}/{second['batch_id']}").json()["status"] == "staged"


def test_losing_a_concurrent_clean_upload_is_refused_as_duplicate(
    db_session: Session, monkeypatch
):
    """Two identical clean uploads both miss the duplicate check; the loser of
    the live-content insert must still be refused as a duplicate."""
    _seed(db_session)
    workbook = _workbook()
    winner = historical_upload.stage_upload(db_session, workbook, uuid4())

    real_refuse = historical_upload._refuse_existing

    def refuse_after_insert(session, content_hash, cause=None):
        # Skip the up-front check, as if the winner had not yet committed.
        if cause is not None:
            real_refuse(session, content_hash, cause)

    monkeypatch.setattr(historical_upload, "_refuse_existing", refuse_after_insert)
    with pytest.raises(APIError) as loser:
        historical_upload.stage_upload(db_session, workbook, uuid4())

    assert loser.value.status_code == 409
    assert loser.value.code == "HISTORY_UPLOAD_DUPLICATE"
    assert str(winner.batch_id) in loser.value.message
    assert _count(db_session, HistoricalImportBatch) == 1


def test_many_missing_periods_are_capped_in_the_message(db_session: Session):
    _seed(db_session)
    batch = historical_upload.stage_upload(db_session, _workbook(), uuid4())
    db_session.refresh(batch)
    data = historical_upload._parse(historical_upload._stored_files(batch))
    data = replace(
        data,
        records=tuple(
            replace(data.records[0], historical_record_id=uuid4(), academic_year=f"{y}-{y + 1}", term_number=1)
            for y in range(2001, 2013)
        ),
    )
    with pytest.raises(HistoricalImportError) as error:
        validate_historical_import(db_session, data)

    message = str(error.value)
    assert "2001-2002 T1, 2002-2003 T1" in message
    assert "2010-2011 T1 and 2 more" in message


def test_invalid_base64_is_refused(db_session: Session):
    resp = _admin(db_session).post(URL, json={"workbook": "not base64!"})

    assert resp.status_code == 422
    assert resp.json()["code"] == "HISTORY_UPLOAD_INVALID"


def test_template_has_the_offered_courses_columns_and_converts_cleanly(db_session: Session):
    client = _admin(db_session)

    resp = client.get(f"{URL}/template")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="Offered Courses template.xlsx"' in resp.headers["content-disposition"]
    workbook = load_workbook(io.BytesIO(resp.content), read_only=True)
    term_sheet = workbook.worksheets[0]
    assert term_sheet.title == "S1 AY2025-26"
    assert next(term_sheet.iter_rows(max_row=1, values_only=True)) == REQUIRED_COLUMNS
    assert "Instructions" in workbook.sheetnames
    # A blank template is the right format with nothing to import.
    blank = client.post(URL, json=_payload(resp.content)).json()
    assert blank["message"] == "Offered Courses.xlsx: no row can be imported"


def test_list_returns_newest_first_without_file_contents(db_session: Session):
    _seed(db_session)
    client = _admin(db_session)
    client.post(URL, json=_payload())

    items = client.get(URL).json()["items"]

    assert len(items) == 1
    assert items[0]["skipped_count"] == 0
    assert "canonical_records" not in items[0]
    assert "source_workbook" not in items[0]
    assert "rejections" not in items[0]


def test_missing_batch_returns_404(db_session: Session):
    client = _admin(db_session)

    assert client.get(f"{URL}/{uuid4()}").status_code == 404
    resp = client.post(f"{URL}/{uuid4()}/confirm", json={"content_sha256": "0" * 64})
    assert resp.status_code == 404


def test_uploads_require_an_administrator(db_session: Session):
    scheduler = make_api_client(db_session, role="scheduling_committee")
    anonymous = make_client(db_session)
    payload = _payload()

    for client, code in ((scheduler, 403), (anonymous, 401)):
        assert client.get(URL).status_code == code
        assert client.get(f"{URL}/template").status_code == code
        assert client.post(URL, json=payload).status_code == code
        assert client.post(
            f"{URL}/{uuid4()}/confirm", json={"content_sha256": "0" * 64}
        ).status_code == code


FILE_COLUMNS = {
    "canonical_records",
    "term_manifest",
    "source_manifest",
    "faculty_crosswalk",
    "source_workbook",
}


def test_summary_and_detail_reads_do_not_load_stored_files(db_session: Session):
    _seed(db_session)
    staged = _admin(db_session).post(URL, json=_payload()).json()
    db_session.expire_all()

    listed = historical_upload.list_batches(db_session)
    detail = historical_upload.get_batch(db_session, staged["batch_id"])

    for batch in (*listed, detail):
        assert FILE_COLUMNS <= inspect(batch).unloaded


def test_list_is_paginated(db_session: Session):
    _seed(db_session)
    client = _admin(db_session)
    for extra in (None, "Notes", "Notes 2"):
        assert client.post(URL, json=_payload(_workbook(extra=extra))).status_code == 201

    first = client.get(f"{URL}?limit=2").json()["items"]
    rest = client.get(f"{URL}?limit=2&offset=2").json()["items"]

    assert len(first) == 2 and len(rest) == 1
    assert {item["batch_id"] for item in first}.isdisjoint(item["batch_id"] for item in rest)
    assert client.get(f"{URL}?limit=0").status_code == 422
