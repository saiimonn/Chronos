"""CHR-112 / CHR-ING-004 review worksheet and mapping-format contract tests.

The review runbook documents two mapping headers and a worksheet. These tests
hold the documents to the real loaders, so a header change on either side fails
here instead of in an administrator's import.
"""

import csv
import importlib.util
import re
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest

from app.services.ingestion.historical_file import (
    HistoricalImportError,
    load_historical_import,
)
from app.tests.support.historical_bootstrap import (
    FACULTY_ID,
    FACULTY_NAME,
    write_bootstrap_dataset,
)

REPO = Path(__file__).resolve().parents[4]
RUNBOOK = REPO / "docs" / "HISTORICAL_DATA_REVIEW.md"
TEMPLATE = REPO / "docs" / "historical_data_review.template.csv"
# docs/ and dataset/ come from the private saiimonn/Chronos-docs checkout.
needs_private = pytest.mark.skipif(
    not RUNBOOK.exists(), reason="private Chronos-docs checkout absent"
)
PACKAGER_SCRIPT = REPO / "backend" / "scripts" / "colab_prototype" / "package_candidate_rules.py"

SPEC = importlib.util.spec_from_file_location("review_contract_packager", PACKAGER_SCRIPT)
packager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = packager
SPEC.loader.exec_module(packager)

CROSSWALK_HEADER = "research_faculty_key,faculty_name,faculty_id"
BOOTSTRAP_HEADER = "faculty_name,faculty_id"


def _write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _documented_csv_headers() -> list[str]:
    return re.findall(r"```csv\n(.+?)\n```", RUNBOOK.read_text(encoding="utf-8"))


def _documented_worksheet_columns() -> list[str]:
    section = RUNBOOK.read_text(encoding="utf-8").split("## Worksheet columns", 1)[1]
    section = section.split("\n## ", 1)[0]
    return re.findall(r"^\| `([a-z0-9_]+)` \|", section, flags=re.MULTILINE)


@needs_private
def test_runbook_documents_exactly_the_two_loader_headers():
    assert _documented_csv_headers() == [CROSSWALK_HEADER, BOOTSTRAP_HEADER]
    assert set(CROSSWALK_HEADER.split(",")) == packager._CROSSWALK_FIELDS


def test_documented_bootstrap_header_loads_a_genuine_mapping(tmp_path: Path):
    dataset_dir, _ = write_bootstrap_dataset(tmp_path)
    mapping = _write(tmp_path / "mapping.csv", BOOTSTRAP_HEADER, f"{FACULTY_NAME},{FACULTY_ID}")

    data = load_historical_import(dataset_dir, mapping)

    assert {record.source_faculty_id for record in data.records} == {FACULTY_ID}


def test_documented_crosswalk_header_loads_a_genuine_mapping(tmp_path: Path):
    research_key, faculty_id = uuid4(), uuid4()
    crosswalk = _write(
        tmp_path / "crosswalk.csv", CROSSWALK_HEADER, f"{research_key},Synthetic Faculty,{faculty_id}"
    )

    assert packager.load_faculty_crosswalk(crosswalk) == {
        research_key: ("SYNTHETIC FACULTY", faculty_id)
    }


def test_bootstrap_loader_refuses_the_frozen_rule_crosswalk_format(tmp_path: Path):
    dataset_dir, _ = write_bootstrap_dataset(tmp_path)
    wrong = _write(tmp_path / "wrong.csv", CROSSWALK_HEADER, f"{uuid4()},{FACULTY_NAME},{FACULTY_ID}")

    with pytest.raises(HistoricalImportError):
        load_historical_import(dataset_dir, wrong)


def test_crosswalk_loader_refuses_the_bootstrap_mapping_format(tmp_path: Path):
    wrong = _write(tmp_path / "wrong.csv", BOOTSTRAP_HEADER, f"Synthetic Faculty,{uuid4()}")

    with pytest.raises(ValueError, match="exactly research_faculty_key"):
        packager.load_faculty_crosswalk(wrong)


@pytest.mark.parametrize("row", [f"{FACULTY_NAME},<TBD>", "<TBD>,<TBD>"])
def test_bootstrap_loader_refuses_placeholders(tmp_path: Path, row: str):
    dataset_dir, _ = write_bootstrap_dataset(tmp_path)
    mapping = _write(tmp_path / "mapping.csv", BOOTSTRAP_HEADER, row)

    with pytest.raises(HistoricalImportError):
        load_historical_import(dataset_dir, mapping)


@pytest.mark.parametrize("row", ["<TBD>,<TBD>,<TBD>", f"{uuid4()},Synthetic Faculty,<TBD>"])
def test_crosswalk_loader_refuses_placeholders(tmp_path: Path, row: str):
    crosswalk = _write(tmp_path / "crosswalk.csv", CROSSWALK_HEADER, row)

    with pytest.raises(ValueError, match="invalid faculty crosswalk row"):
        packager.load_faculty_crosswalk(crosswalk)


@needs_private
def test_template_columns_match_the_runbook_and_hold_no_values():
    with TEMPLATE.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        columns = list(reader.fieldnames or ())
        rows = list(reader)

    assert columns == _documented_worksheet_columns()
    assert len(rows) == 1
    assert {value for column, value in rows[0].items() if column != "alias_of"} == {"<TBD>"}
    assert rows[0]["alias_of"] == ""


@needs_private
def test_the_worksheet_is_not_accepted_as_either_mapping(tmp_path: Path):
    dataset_dir, _ = write_bootstrap_dataset(tmp_path)

    with pytest.raises(HistoricalImportError):
        load_historical_import(dataset_dir, TEMPLATE)
    with pytest.raises(ValueError):
        packager.load_faculty_crosswalk(TEMPLATE)


@needs_private
def test_published_pending_crosswalks_are_not_importable():
    pending = sorted((REPO / "experiments").glob("CHR-100-*/*/faculty_crosswalk.pending.csv"))

    assert pending, "expected the published CHR-100 pending crosswalk snapshot"
    for path in pending:
        with pytest.raises(ValueError):
            packager.load_faculty_crosswalk(path)


NIL = UUID(int=0)


def test_bootstrap_loader_rejects_nil_ids_and_copied_research_keys(tmp_path: Path):
    dataset_dir, _ = write_bootstrap_dataset(tmp_path)
    research_key = uuid5(NAMESPACE_URL, f"chronos:historical-faculty:{FACULTY_NAME}")

    for faculty_id in (NIL, research_key):
        mapping = _write(tmp_path / "mapping.csv", BOOTSTRAP_HEADER, f"{FACULTY_NAME},{faculty_id}")
        with pytest.raises(HistoricalImportError):
            load_historical_import(dataset_dir, mapping)


@needs_private
def test_crosswalk_loader_only_checks_uuid_shape_as_the_runbook_states(tmp_path: Path):
    # The runbook says nil IDs and copied research keys pass this loader and
    # must be kept out by review; if the loader is hardened, update the runbook.
    research_key = uuid4()
    for faculty_id in (NIL, research_key):
        crosswalk = _write(tmp_path / "crosswalk.csv", CROSSWALK_HEADER, f"{research_key},Synthetic Faculty,{faculty_id}")
        assert packager.load_faculty_crosswalk(crosswalk)[research_key][1] == faculty_id
    assert "A nil UUID or a copied\n  research key passes it" in RUNBOOK.read_text(encoding="utf-8")


@needs_private
def test_runbook_does_not_claim_the_rule_set_path_is_receipt_bound():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "### Frozen-rule path (not receipt-bound)" in text
    assert "The rule-set import does not take an expected hash" in text
    assert "`operational_suitability=suitable`" in text
