"""CHR-100/107/133 / CHR-COLAB-004/006/007 private publication contracts."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.services.catalog.reference_onboarding import (
    ReferenceOnboardingError,
    load_reviewed_reference_data,
)


# The research data and its ignore/attribute rules live in the private
# saiimonn/Chronos-docs checkout at private/. Public clones skip this module.
REPO = Path(__file__).resolve().parents[4] / "private"
pytestmark = pytest.mark.skipif(
    not (REPO / ".git").exists(), reason="private Chronos-docs checkout absent"
)
HANDOFF = "dataset/handoff/CHR-133-CHR-COLAB-007-2026-10-24-v1"
SOURCE = "CHR-100-CHR-COLAB-004-reference-review-20261015T081716026212Z/onboarding_review"
DRAFT = "CHR-133-CHR-COLAB-007-administrator-review-draft-20261023T103632Z"


def test_approved_repository_paths_exclude_credentials_and_scratch():
    permitted = (
        "dataset/README.md",
        "dataset/original/approved.xlsx",
        "dataset/original/approved.manifest.json",
        "dataset/preprocessed/v1/README.md",
        "dataset/preprocessed/v1/canonical_records.csv",
        "dataset/preprocessed/v1/training_records.csv",
        "dataset/preprocessed/v1/holdout_records.csv",
        "dataset/preprocessed/v1/rejection_ledger.csv",
        "dataset/preprocessed/v1/term_manifest.csv",
        "dataset/preprocessed/v1/scope_mapping.csv",
        "dataset/preprocessed/v1/source_manifest.json",
        f"{HANDOFF}/README.md",
        f"{HANDOFF}/publication_manifest.json",
        f"{HANDOFF}/{SOURCE}/review_manifest.json",
        f"{HANDOFF}/{SOURCE}/faculty_onboarding.pending.csv",
        f"{HANDOFF}/{DRAFT}/approval_manifest.template.json",
        "experiments/README.md",
        "experiments/CHR-99-CHR-COLAB-003/run1/README.md",
        "experiments/CHR-99-CHR-COLAB-003/run1/threshold_exploration.csv",
        "experiments/CHR-99-CHR-COLAB-003/run1/selected_rules.csv",
        "experiments/CHR-99-CHR-COLAB-003/run1/holdout_evaluation.csv",
        "experiments/CHR-99-CHR-COLAB-003/run1/faculty_identity_map.csv",
        "experiments/CHR-99-CHR-COLAB-003/run1/transactions_teacher_subject.csv",
        "experiments/CHR-99-CHR-COLAB-003/run1/threshold_rule_distribution.svg",
        "experiments/CHR-99-CHR-COLAB-003/run1/holdout_pattern_attainment.svg",
        "experiments/CHR-99-CHR-COLAB-003/run1/installed_packages.txt",
        "experiments/CHR-99-CHR-COLAB-003/run1/run_metadata.json",
        "experiments/CHR-99-CHR-COLAB-003/run1/threshold_grid.json",
        "experiments/CHR-99-CHR-COLAB-003/run1/selected_thresholds.json",
        "experiments/CHR-99-CHR-COLAB-003/run1/selection_lock.json",
        "experiments/CHR-99-CHR-COLAB-003/run1/exploration_manifest.json",
        "experiments/CHR-99-CHR-COLAB-003/run1/evaluation_manifest.json",
        "experiments/CHR-99-CHR-COLAB-003/run1/download_verification.json",
        "experiments/CHR-100-CHR-COLAB-004/run1/README.md",
        "experiments/CHR-100-CHR-COLAB-004/run1/faculty_crosswalk.pending.csv",
        "experiments/CHR-100-CHR-COLAB-004/run1/faculty_crosswalk.csv",
        "experiments/CHR-100-CHR-COLAB-004/run1/reference_inventory.csv",
        "experiments/CHR-100-CHR-COLAB-004/run1/inventory_manifest.json",
        "experiments/CHR-100-CHR-COLAB-004/run1/candidate_rule_set.json",
        "experiments/CHR-101-CHR-COLAB-005/run1/README.md",
        "experiments/CHR-101-CHR-COLAB-005/run1/evaluation_batch.csv",
        "experiments/CHR-101-CHR-COLAB-005/run1/evaluation_manifest.json",
        "experiments/CHR-101-CHR-COLAB-005/run1/validated_runs.csv",
        "experiments/CHR-101-CHR-COLAB-005/run1/mode_summary.csv",
        "experiments/CHR-101-CHR-COLAB-005/run1/descriptive_statistics.csv",
        "experiments/CHR-101-CHR-COLAB-005/run1/matched_seed_differences.csv",
        "experiments/CHR-101-CHR-COLAB-005/run1/convergence_by_run.csv",
        "experiments/CHR-101-CHR-COLAB-005/run1/convergence_summary.csv",
        "experiments/CHR-101-CHR-COLAB-005/run1/metric_boxplots.svg",
        "experiments/CHR-101-CHR-COLAB-005/run1/matched_seed_hpa.svg",
        "experiments/CHR-101-CHR-COLAB-005/run1/fitness_convergence.svg",
        "experiments/CHR-101-CHR-COLAB-005/run1/analysis_manifest.json",
        "experiments/CHR-101-CHR-COLAB-005/run1/checkpoint_manifest.json",
        "experiments/CHR-101-CHR-COLAB-005/run1/installed_packages.txt",
        "experiments/CHR-101-CHR-COLAB-005/run1/run_metadata.json",
    )
    ignored = (
        "dataset/local/training_records.csv",
        "dataset/preprocessed/local/training_records.csv",
        "dataset/preprocessed/LOCAL/training_records.csv",
        "dataset/preprocessed/v1/credentials.csv",
        "dataset/original/PASSWORDS.xlsx",
        "dataset/original/token.xlsx",
        f"{HANDOFF}/.env",
        f"{HANDOFF}/backup.dump",
        f"{HANDOFF}/unknown.csv",
        f"{HANDOFF}/{DRAFT}/approval_manifest.json",
        f"{HANDOFF}/{DRAFT}/scratch/README.md",
        f"{HANDOFF}/credentials/README.md",
        "experiments/CHR-99-CHR-COLAB-003/scratch/holdout_evaluation.csv",
        "experiments/CHR-99-CHR-COLAB-003/SCRATCH/holdout_evaluation.csv",
        "experiments/CHR-99-CHR-COLAB-003/run1/token.json",
        "experiments/CHR-99-CHR-COLAB-003/run1/.env",
        "experiments/CHR-99-CHR-COLAB-003/run1/checkpoint.zip",
        "experiments/CHR-99-CHR-COLAB-003/run1/backend/requirements.txt",
        "experiments/CHR-99-CHR-COLAB-003/run1/local/download_verification.json",
        "experiments/CHR-99-CHR-COLAB-003/run1/nested/download_verification.json",
        "experiments/CHR-100-CHR-COLAB-004/run1/checkpoint.zip",
        "experiments/CHR-100-CHR-COLAB-004/run1/token.json",
        "experiments/CHR-100-CHR-COLAB-004/run1/.env",
        "experiments/CHR-100-CHR-COLAB-004/run1/backend/requirements.txt",
        "experiments/CHR-100-CHR-COLAB-004/LOCAL/faculty_crosswalk.csv",
        "experiments/CHR-100-CHR-COLAB-004/run1/nested/inventory_manifest.json",
        "experiments/CHR-101-CHR-COLAB-005/scratch/validated_runs.csv",
        "experiments/CHR-101-CHR-COLAB-005/SCRATCH/validated_runs.csv",
        "experiments/CHR-101-CHR-COLAB-005/run1/token.json",
        "experiments/CHR-101-CHR-COLAB-005/run1/checkpoint.zip",
        "experiments/CHR-101-CHR-COLAB-005/run1/backend/requirements.txt",
        "experiments/CHR-100-CHR-COLAB-005/run1/analysis_manifest.json",
        "experiments/CHR-98-CHR-COLAB-003/run1/run_metadata.json",
        "experiments/CHR--CHR-COLAB-/run1/run_metadata.json",
    )
    for paths, expected in ((permitted, 1), (ignored, 0)):
        for path in paths:
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", "-q", path],
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == expected, (path, result.stderr)


def test_generated_research_charts_preserve_hash_bound_whitespace():
    paths = (
        "experiments/CHR-99-CHR-COLAB-003/run1/threshold_rule_distribution.svg",
        "experiments/CHR-99-CHR-COLAB-003/run1/holdout_pattern_attainment.svg",
        "experiments/CHR-99-CHR-COLAB-003/run1/unrelated.svg",
        "frontend/src/assets/unrelated.svg",
    )
    result = subprocess.run(
        ["git", "check-attr", "whitespace", "--", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.splitlines() == [
        f"{path}: whitespace: {value}"
        for path, value in zip(
            paths,
            ("-blank-at-eol", "-blank-at-eol", "unspecified", "unspecified"),
            strict=True,
        )
    ]


def test_private_handoff_inventory_is_complete_and_byte_exact():
    bundle = REPO / HANDOFF
    manifest = json.loads((bundle / "publication_manifest.json").read_text())
    assert manifest["traceability"] == "CHR-133 / CHR-COLAB-007"
    assert manifest["administrator_approved"] is False
    assert manifest["threshold_selection_eligible"] is False
    assert manifest["database_writes_performed"] is False
    entries = manifest["entries"]
    assert len(entries) == len({entry["source_path"] for entry in entries}) == 85
    published = [entry for entry in entries if entry["disposition"] == "published"]
    assert len(published) == 52
    assert sum(entry["disposition"] == "already_versioned" for entry in entries) == 25
    assert sum(entry["disposition"] == "excluded" for entry in entries) == 8
    assert {path.relative_to(REPO).as_posix() for path in bundle.rglob("*") if path.is_file()} == {
        f"{HANDOFF}/README.md",
        f"{HANDOFF}/publication_manifest.json",
        *(entry["repository_path"] for entry in published),
    }
    for entry in entries:
        if entry["disposition"] == "excluded":
            assert entry["reason"]
            assert "repository_path" not in entry
            continue
        path = (REPO / entry["repository_path"]).resolve()
        assert path.is_relative_to(REPO)
        assert "local" not in path.relative_to(REPO).parts
        content = path.read_bytes()
        assert len(content) == entry["size_bytes"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], str(path)


def test_published_review_source_and_draft_remain_unapproved():
    source = REPO / HANDOFF / SOURCE
    draft = REPO / HANDOFF / DRAFT
    manifest = json.loads((source / "review_manifest.json").read_text())
    template = json.loads((draft / "approval_manifest.template.json").read_text())
    assert manifest["administrator_reviewed"] is False
    assert template["source_review_manifest_sha256"] == hashlib.sha256(
        (source / "review_manifest.json").read_bytes()
    ).hexdigest()
    assert list(manifest["row_counts"].values()) == [28, 19, 10, 20]
    for name, expected in manifest["output_sha256"].items():
        content = (source / name).read_bytes()
        assert content == (draft / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == expected
        assert template["source_worksheet_sha256"][name] == expected
    assert not (draft / "approval_manifest.json").exists()
    with pytest.raises(ReferenceOnboardingError, match="approval manifest reviewer must be reviewed"):
        load_reviewed_reference_data(source, draft, draft / "approval_manifest.template.json")


def test_handoff_checkout_preserves_hash_bound_crlf_bytes():
    path = f"{HANDOFF}/CHR-99-CHR-COLAB-003-2026-10-04/training-only/training_records.csv"
    result = subprocess.run(
        ["git", "check-attr", "text", "--", path],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == f"{path}: text: unset"
    assert b"\r\n" in (REPO / path).read_bytes()


@pytest.mark.parametrize("path", [
    "dataset/original/20260926 Offered Courses .xlsx",
    "dataset/preprocessed/2026-10-04-v1/training_records.csv",
    "experiments/CHR-99-CHR-COLAB-003/CHR-COLAB-003-57eebe19a06d48b89804484aca170b82/faculty_identity_map.csv",
])
def test_research_dependencies_disable_checkout_newline_conversion(path: str):
    result = subprocess.run(
        ["git", "check-attr", "text", "--", path],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == f"{path}: text: unset"
