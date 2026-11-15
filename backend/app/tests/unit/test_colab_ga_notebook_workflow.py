"""CHR-101 / CHR-COLAB-005 pinned Colab workflow tests."""

import ast
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import pytest

from app.tests.unit.test_colab_ga_analysis import analysis, evaluation_rows


BACKEND = Path(__file__).parents[3]
NOTEBOOK = BACKEND / "scripts/colab_prototype/CHR_COLAB_005_matched_ga_analysis.ipynb"
COMMIT = "b" * 40
TOKEN = "SYNTHETIC-NOT-A-REAL-TOKEN"
EVALUATION_PATH = (
    "experiments/CHR-101-CHR-COLAB-005/synthetic-run/evaluation_batch.csv"
)
INPUT_README_PATH = "experiments/CHR-101-CHR-COLAB-005/synthetic-run/README.md"
GENERATION_MANIFEST_PATH = (
    "experiments/CHR-101-CHR-COLAB-005/synthetic-run/evaluation_manifest.json"
)
CODE_PATHS = (
    "backend/requirements.txt",
    "backend/scripts/colab_prototype/analyze_matched_ga.py",
)


def evaluation_csv() -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=analysis.REQUIRED_COLUMNS, lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(evaluation_rows())
    return buffer.getvalue().encode("utf-8-sig")


def generation_manifest(evaluation_content: bytes) -> bytes:
    rows = evaluation_rows()
    content = {
        "schema_version": 1,
        "traceability": "CHR-38 / CHR-EVAL-001",
        "evaluation_csv_sha256": hashlib.sha256(evaluation_content).hexdigest(),
        "input_fingerprint": "a" * 64,
        "generator_commit": "c" * 40,
        "generated_at_utc": "2026-10-03T00:00:00+00:00",
        "environment": {
            "python": "3.12.synthetic",
            "platform": "synthetic-linux",
            "processor": "synthetic-cpu",
        },
        "execution": {
            "concurrent_runs": 1,
            "shared_runtime": True,
            "run_order": [
                {
                    "pair_index": row["pair_index"],
                    "mode": row["mode"],
                    "random_seed": row["random_seed"],
                }
                for row in sorted(rows, key=lambda row: (row["pair_index"], row["mode"]))
            ],
        },
        "scoring": {
            "implementation": "synthetic.FixedHistoricalScorer",
            "rule_set_id": "00000000-0000-0000-0000-000000000001",
            "rule_set_sha256": "d" * 64,
            "weights": {
                "teacher_subject": 0.25,
                "teacher_day": 0.25,
                "teacher_time": 0.25,
                "teacher_subject_room": 0.25,
            },
        },
    }
    return (json.dumps(content, sort_keys=True) + "\n").encode()


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    namespace = {}
    requests = []
    commands = []
    analysis_module = None
    payloads = {path: (BACKEND.parent / path).read_bytes() for path in CODE_PATHS}
    payloads[EVALUATION_PATH] = evaluation_csv()
    payloads[GENERATION_MANIFEST_PATH] = generation_manifest(
        payloads[EVALUATION_PATH]
    )
    payloads[INPUT_README_PATH] = b"# Synthetic run provenance\n"

    class StorageRoot(ast.NodeTransformer):
        def visit_Constant(self, node):
            if node.value == "/content":
                return ast.copy_location(ast.Constant(str(tmp_path)), node)
            return node

    def execute(index):
        tree = StorageRoot().visit(ast.parse("".join(cells[index]["source"])))
        exec(
            compile(
                ast.fix_missing_locations(tree),
                f"{NOTEBOOK}:{cells[index]['id']}",
                "exec",
            ),
            namespace,
        )

    class Opener:
        def open(self, request, timeout):
            url = urlsplit(request.full_url)
            assert url.scheme == "https" and url.netloc == "api.github.com"
            assert url.path.startswith("/repos/saiimonn/Chronos/contents/")
            assert parse_qs(url.query) == {"ref": [COMMIT]}
            path = url.path.removeprefix("/repos/saiimonn/Chronos/contents/")
            assert path in payloads
            assert request.get_header("Authorization") == f"Bearer {TOKEN}"
            assert request.get_header("Accept") == "application/vnd.github.raw+json"
            assert TOKEN not in request.full_url
            assert timeout == 120
            requests.append(path)
            return io.BytesIO(payloads[path])

    def build_opener(*handlers):
        assert len(handlers) == 1
        assert (
            handlers[0].redirect_request(
                None, None, 302, "redirect", {}, "https://example.invalid"
            )
            is None
        )
        return Opener()

    def run(command, **kwargs):
        nonlocal analysis_module
        if command[0] in ("fc-list", "fc-match"):
            return subprocess.CompletedProcess(command, 0, stdout=b"")
        commands.append(command)
        assert kwargs.get("check") is True
        if command[1:3] == ["-m", "pip"]:
            assert kwargs["cwd"] == namespace["backend_dir"]
            if command[3:] == ["freeze"]:
                return subprocess.CompletedProcess(
                    command, 0, stdout="matplotlib==synthetic\npandas==synthetic\n"
                )
            assert command[3:] == ["install", "--quiet", "-r", "requirements.txt"]
            return subprocess.CompletedProcess(command, 0)
        script = Path(command[1])
        assert script == namespace["analysis_script"]
        if analysis_module is None:
            spec = importlib.util.spec_from_file_location(
                "synthetic_matched_ga_analysis", script
            )
            assert spec is not None and spec.loader is not None
            analysis_module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = analysis_module
            spec.loader.exec_module(analysis_module)
        analysis_module.analyze(
            Path(command[command.index("--input") + 1]),
            Path(command[command.index("--generation-manifest") + 1]),
            Path(command[command.index("--output-dir") + 1]),
        )
        return subprocess.CompletedProcess(command, 0)

    def forbidden_network(*args, **kwargs):
        pytest.fail("Notebook workflow tests must not access the network")

    inputs = Mock(
        side_effect=[
            "YES",
            COMMIT,
            EVALUATION_PATH,
            "synthetic-assignee",
            "YES",
        ]
    )
    downloads = Mock()
    authentication = Mock(return_value=TOKEN)
    colab = ModuleType("google.colab")
    colab.files = SimpleNamespace(download=downloads)
    google = ModuleType("google")
    google.colab = colab
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    monkeypatch.setattr("builtins.input", inputs)
    monkeypatch.setattr("getpass.getpass", authentication)
    monkeypatch.setattr("urllib.request.build_opener", build_opener)
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("platform.platform", lambda: "synthetic-platform")
    monkeypatch.setattr("platform.processor", lambda: "synthetic-cpu")
    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_network)
    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_network)
    try:
        with patch.dict(os.environ, {"MPLCONFIGDIR": str(tmp_path / "mpl")}, clear=True):
            yield SimpleNamespace(
                execute=execute,
                namespace=namespace,
                requests=requests,
                commands=commands,
                downloads=downloads,
                authentication=authentication,
                payloads=payloads,
            )
    finally:
        sys.modules.pop("synthetic_matched_ga_analysis", None)


def test_notebook_format_and_cell_ids():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert (notebook["nbformat"], notebook["nbformat_minor"]) == (4, 5)
    assert len(notebook["cells"]) == 10
    ids = [cell["id"] for cell in notebook["cells"]]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cell_id) for cell_id in ids)
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["execution_count"] is None and cell["outputs"] == []
            compile("".join(cell["source"]), str(NOTEBOOK), "exec")


def test_notebook_runs_pinned_analysis_and_downloads_review_checkpoint(workflow):
    workflow.execute(0)
    assert workflow.requests == [
        *CODE_PATHS,
        EVALUATION_PATH,
        GENERATION_MANIFEST_PATH,
        INPUT_README_PATH,
    ]
    assert workflow.authentication.call_count == 1
    workflow.execute(1)
    workflow.execute(2)
    workflow.execute(4)

    assert workflow.downloads.call_count == 1
    archive = Path(workflow.downloads.call_args.args[0])
    with ZipFile(archive) as checkpoint:
        names = set(checkpoint.namelist())
        metadata = json.loads(checkpoint.read("run_metadata.json"))
        assert "evaluation_batch.csv" not in names
        assert {
            "validated_runs.csv",
            "mode_summary.csv",
            "descriptive_statistics.csv",
            "matched_seed_differences.csv",
            "convergence_by_run.csv",
            "convergence_summary.csv",
            "metric_boxplots.svg",
            "matched_seed_hpa.svg",
            "fitness_convergence.svg",
            "analysis_manifest.json",
            "checkpoint_manifest.json",
            "installed_packages.txt",
            "run_metadata.json",
        } <= names
        assert metadata["traceability"] == "CHR-101 / CHR-COLAB-005"
        assert metadata["input_commit"] == COMMIT
        assert metadata["input_path"] == EVALUATION_PATH
        assert metadata["declared_runner"] == {
            "source": "jira_assignee",
            "ticket": "CHR-101",
            "account_id": "synthetic-assignee",
        }
        assert metadata["input_sha256"] == hashlib.sha256(
            workflow.payloads[EVALUATION_PATH]
        ).hexdigest()
        assert metadata["retrieved_files_sha256"][INPUT_README_PATH] == hashlib.sha256(
            workflow.payloads[INPUT_README_PATH]
        ).hexdigest()
        assert metadata["generation_manifest_path"] == GENERATION_MANIFEST_PATH
        assert metadata["generation_manifest_sha256"] == hashlib.sha256(
            workflow.payloads[GENERATION_MANIFEST_PATH]
        ).hexdigest()
        checkpoint_manifest = json.loads(checkpoint.read("checkpoint_manifest.json"))
        assert checkpoint_manifest["files_sha256"] == {
            name: hashlib.sha256(checkpoint.read(name)).hexdigest()
            for name in names - {"checkpoint_manifest.json"}
        }
        assert all(
            TOKEN.encode() not in checkpoint.read(name)
            for name in names
            if not name.endswith("/")
        )


def test_notebook_rejects_unreviewed_input_path_before_authentication(
    tmp_path: Path, monkeypatch
):
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    bootstrap = next(
        cell for cell in notebook["cells"] if cell["id"] == "chr101-bootstrap"
    )
    source = "".join(bootstrap["source"]).replace("Path('/content')", f"Path({str(tmp_path)!r})")
    inputs = Mock(side_effect=["YES", COMMIT, "dataset/local/evaluation.csv", "assignee"])
    authentication = Mock()
    monkeypatch.setattr("builtins.input", inputs)
    monkeypatch.setattr("getpass.getpass", authentication)
    monkeypatch.setattr("shutil.which", lambda name: None)
    google = ModuleType("google")
    colab = ModuleType("google.colab")
    colab.files = SimpleNamespace(download=Mock())
    google.colab = colab
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.colab", colab)

    with pytest.raises(ValueError, match="reviewed CHR-101"):
        exec(compile(source, str(NOTEBOOK), "exec"), {})

    authentication.assert_not_called()


def test_notebook_has_no_drive_mount_or_publication_command():
    source = NOTEBOOK.read_text(encoding="utf-8")
    assert "drive.mount" not in source
    assert "git clone" not in source
    assert "git push" not in source
    assert "api.github.com" in source
    assert "getpass('GitHub contents-read token: ')" in source
