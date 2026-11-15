from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli.seed_catalog import (
    CURRICULUM_FILE,
    PROGRAMS_FILE,
    SUBJECTS_FILE,
    SeedDataError,
    seed,
)
from app.models import CourseProgram, Curriculum, CurriculumRequirement, Subject

pytestmark = pytest.mark.postgres

PROGRAMS = """\
program_code,program_name
BSCS,Bachelor of Science in Computer Science
BSIT,Bachelor of Science in Information Technology
BSIS,Bachelor of Science in Information Systems
"""

SUBJECTS = """\
subject_code,subject_title,units,subject_type
CIS 1101,Introduction to Computing,3,LEC
CIS 1101L,Introduction to Computing (Laboratory),1,LAB
"""

CURRICULUM = """\
program_code,effective_year,year_level,term_number,subject_code
BSCS,2023-2024,1,1,CIS 1101
BSCS,2023-2024,1,1,CIS 1101L
"""


def make_data_dir(
    tmp_path: Path, programs=PROGRAMS, subjects=SUBJECTS, curriculum=CURRICULUM
) -> Path:
    (tmp_path / PROGRAMS_FILE).write_text(programs, encoding="utf-8")
    (tmp_path / SUBJECTS_FILE).write_text(subjects, encoding="utf-8")
    (tmp_path / CURRICULUM_FILE).write_text(curriculum, encoding="utf-8")
    return tmp_path


def test_seeding_an_empty_database_populates_every_catalog(
    db_session: Session, tmp_path: Path
):
    report = seed(db_session, make_data_dir(tmp_path))
    db_session.flush()

    assert report.programs.created == 3
    assert report.subjects.created == 2
    assert report.curricula.created == 1
    assert report.requirements.created == 2

    programs = db_session.scalars(select(CourseProgram.program_code)).all()
    assert set(programs) == {"BSCS", "BSIT", "BSIS"}

    subject = db_session.get(Subject, "CIS 1101L")
    assert subject.subject_type == "LAB"
    assert subject.units == 1
    assert subject.duration_minutes == 150


def test_running_twice_changes_nothing(db_session: Session, tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    seed(db_session, data_dir)
    db_session.flush()

    report = seed(db_session, data_dir)
    db_session.flush()

    assert report.programs.created == 0
    assert report.programs.unchanged == 3
    assert report.subjects.created == 0
    assert report.subjects.unchanged == 2
    assert report.curricula.created == 0
    assert report.requirements.created == 0
    assert report.requirements.unchanged == 2

    assert len(db_session.scalars(select(Curriculum)).all()) == 1
    assert len(db_session.scalars(select(CurriculumRequirement)).all()) == 2


def test_editing_a_value_updates_only_that_row(db_session: Session, tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    seed(db_session, data_dir)
    db_session.flush()

    corrected = SUBJECTS.replace(
        "CIS 1101,Introduction to Computing,3,LEC",
        "CIS 1101,Introduction to Computing I,4,LEC",
    )
    (data_dir / SUBJECTS_FILE).write_text(corrected, encoding="utf-8")

    report = seed(db_session, data_dir)
    db_session.flush()

    assert report.subjects.updated == 1
    assert report.subjects.unchanged == 1
    assert report.subjects.created == 0

    changed = db_session.get(Subject, "CIS 1101")
    assert changed.subject_title == "Introduction to Computing I"
    assert changed.units == 4
    untouched = db_session.get(Subject, "CIS 1101L")
    assert untouched.subject_title == "Introduction to Computing (Laboratory)"


def test_a_failing_validation_writes_nothing(db_session: Session, tmp_path: Path):
    data_dir = make_data_dir(
        tmp_path,
        curriculum=(
            "program_code,effective_year,year_level,term_number,subject_code\n"
            "BSCS,2023-2024,1,1,CIS 9999\n"
        ),
    )

    with pytest.raises(SeedDataError):
        seed(db_session, data_dir)
    db_session.flush()

    # Programs and subjects are written before the curriculum file is reached
    # in a naive implementation; validation runs first precisely so they are
    # not, and the catalog is left exactly as it was.
    assert db_session.scalars(select(CourseProgram)).all() == []
    assert db_session.scalars(select(Subject)).all() == []


def test_moving_a_requirement_leaves_no_stale_placement(
    db_session: Session, tmp_path: Path
):
    data_dir = make_data_dir(tmp_path)
    seed(db_session, data_dir)
    db_session.flush()

    moved = CURRICULUM.replace(
        "BSCS,2023-2024,1,1,CIS 1101L", "BSCS,2023-2024,1,2,CIS 1101L"
    )
    (data_dir / CURRICULUM_FILE).write_text(moved, encoding="utf-8")

    report = seed(db_session, data_dir)
    db_session.flush()

    assert report.requirements.created == 1
    assert report.requirements.unchanged == 1
    assert report.requirements.removed == 1

    placements = db_session.execute(
        select(
            CurriculumRequirement.year_level,
            CurriculumRequirement.term_number,
            CurriculumRequirement.subject_code,
        )
    ).all()
    assert sorted(placements) == [(1, 1, "CIS 1101"), (1, 2, "CIS 1101L")]


def test_removing_a_requirement_deletes_it(db_session: Session, tmp_path: Path):
    data_dir = make_data_dir(tmp_path)
    seed(db_session, data_dir)
    db_session.flush()

    removed = CURRICULUM.replace("BSCS,2023-2024,1,1,CIS 1101L\n", "")
    (data_dir / CURRICULUM_FILE).write_text(removed, encoding="utf-8")

    report = seed(db_session, data_dir)
    db_session.flush()

    assert report.requirements.removed == 1
    assert report.requirements.unchanged == 1
    subjects = db_session.scalars(select(CurriculumRequirement.subject_code)).all()
    assert subjects == ["CIS 1101"]
    # The subject itself is catalog data and stays; only the placement goes.
    assert db_session.get(Subject, "CIS 1101L") is not None


def test_deleting_a_versions_final_placement_removes_the_version(
    db_session: Session, tmp_path: Path
):
    both = CURRICULUM + "BSCS,2019-2020,1,1,CIS 1101\n"
    data_dir = make_data_dir(tmp_path, curriculum=both)
    seed(db_session, data_dir)
    db_session.flush()

    # 2019-2020 loses its only row, so no row names it any more. Left behind,
    # it would stay selectable for cohorts that entered between 2019 and 2022.
    (data_dir / CURRICULUM_FILE).write_text(CURRICULUM, encoding="utf-8")
    report = seed(db_session, data_dir)
    db_session.flush()
    db_session.expire_all()

    assert report.curricula.removed == 1
    assert report.curricula.unchanged == 1
    assert report.requirements.removed == 1
    assert report.requirements.unchanged == 2

    versions = db_session.scalars(select(Curriculum.effective_year)).all()
    assert versions == ["2023-2024"]
    assert len(db_session.scalars(select(CurriculumRequirement)).all()) == 2


def test_correcting_every_rows_effective_year_replaces_the_version(
    db_session: Session, tmp_path: Path
):
    data_dir = make_data_dir(tmp_path)
    seed(db_session, data_dir)
    db_session.flush()

    corrected = CURRICULUM.replace("2023-2024", "2022-2023")
    (data_dir / CURRICULUM_FILE).write_text(corrected, encoding="utf-8")
    report = seed(db_session, data_dir)
    db_session.flush()
    db_session.expire_all()

    assert report.curricula.created == 1
    assert report.curricula.removed == 1
    assert report.requirements.created == 2
    assert report.requirements.removed == 2

    curricula = db_session.scalars(select(Curriculum)).all()
    assert [curriculum.effective_year for curriculum in curricula] == ["2022-2023"]
    owners = set(
        db_session.scalars(select(CurriculumRequirement.curriculum_id)).all()
    )
    assert owners == {curricula[0].curriculum_id}


def test_programs_the_seed_does_not_name_keep_their_curricula(
    db_session: Session, tmp_path: Path
):
    with_bsem = PROGRAMS + "BSEM,Bachelor of Science in Entertainment Media\n"
    data_dir = make_data_dir(
        tmp_path,
        programs=with_bsem,
        curriculum=CURRICULUM + "BSEM,2023-2024,1,1,CIS 1101\n",
    )
    seed(db_session, data_dir)
    db_session.flush()

    # BSEM leaves the seed entirely. Its curriculum is no longer the seed's to
    # reconcile, so it stays as it is.
    (data_dir / PROGRAMS_FILE).write_text(PROGRAMS, encoding="utf-8")
    (data_dir / CURRICULUM_FILE).write_text(CURRICULUM, encoding="utf-8")
    report = seed(db_session, data_dir)
    db_session.flush()

    assert report.curricula.removed == 0
    assert report.requirements.removed == 0
    kept = db_session.scalars(
        select(Curriculum).where(Curriculum.program_code == "BSEM")
    ).all()
    assert len(kept) == 1


def test_structural_faults_in_every_file_are_reported_together(
    db_session: Session, tmp_path: Path
):
    data_dir = make_data_dir(
        tmp_path,
        programs="program_code,name\nBSCS,Computer Science\n",
        subjects=(
            "subject_code,subject_title,units,subject_type\n"
            "CIS 1101,Introduction to Computing\n"
            "CIS 1101L,Introduction to Computing (Laboratory),1\n"
        ),
        curriculum=(
            "program_code,effective_year,year_level,term_number,subject_code\n"
            "BSCS,2023-2024,1,1\n"
        ),
    )

    with pytest.raises(SeedDataError) as excinfo:
        seed(db_session, data_dir)

    violations = excinfo.value.violations
    assert len(violations) == 4
    assert violations[0].startswith(f"{PROGRAMS_FILE}:1: header is")
    assert violations[1].startswith(f"{SUBJECTS_FILE}:2:")
    assert violations[2].startswith(f"{SUBJECTS_FILE}:3:")
    assert violations[3].startswith(f"{CURRICULUM_FILE}:2:")
    assert db_session.scalars(select(CourseProgram)).all() == []


def test_several_prospectus_versions_seed_side_by_side(
    db_session: Session, tmp_path: Path
):
    data_dir = make_data_dir(
        tmp_path,
        curriculum=(
            "program_code,effective_year,year_level,term_number,subject_code\n"
            "BSCS,2019-2020,1,1,CIS 1101\n"
            "BSCS,2023-2024,1,1,CIS 1101\n"
            "BSCS,2023-2024,1,1,CIS 1101L\n"
        ),
    )
    report = seed(db_session, data_dir)
    db_session.flush()

    assert report.curricula.created == 2
    assert report.requirements.created == 3

    versions = db_session.scalars(
        select(Curriculum.effective_year).where(Curriculum.program_code == "BSCS")
    ).all()
    assert sorted(versions) == ["2019-2020", "2023-2024"]


def test_a_curriculum_with_no_requirements_is_not_created(
    db_session: Session, tmp_path: Path
):
    data_dir = make_data_dir(
        tmp_path,
        curriculum="program_code,effective_year,year_level,term_number,subject_code\n",
    )
    report = seed(db_session, data_dir)
    db_session.flush()

    assert report.curricula.created == 0
    assert db_session.scalars(select(Curriculum)).all() == []


def test_shipped_templates_seed_cleanly(db_session: Session):
    # The committed CSVs must actually load, not merely validate.
    data_dir = Path(__file__).resolve().parents[3] / "data"
    report = seed(db_session, data_dir)
    db_session.flush()

    assert report.programs.created == 3
    programs = db_session.scalars(select(CourseProgram.program_code)).all()
    assert set(programs) == {"BSCS", "BSIT", "BSIS"}
