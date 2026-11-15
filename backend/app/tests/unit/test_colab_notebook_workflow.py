"""CHR-99 / CHR-COLAB-003 offline notebook bootstrap and staged workflow tests."""

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
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile

import pytest


BACKEND = Path(__file__).parents[3]
NOTEBOOK = BACKEND / "scripts/colab_prototype/CHR_COLAB_003_apriori_holdout_evaluation.ipynb"
COMMIT = "a" * 40
TOKEN = "SYNTHETIC-NOT-A-REAL-TOKEN"
DATASET_PREFIX = "dataset/preprocessed/synthetic-v1/"
CODE_PATHS = (
    "backend/app/__init__.py",
    "backend/app/services/__init__.py",
    "backend/app/services/arm/__init__.py",
    "backend/app/services/arm/mining.py",
    "backend/app/services/arm/preprocessing.py",
    "backend/app/services/arm/rule_package.py",
    "backend/requirements.txt",
    "backend/scripts/colab_prototype/explore_apriori_holdout.py",
    "backend/scripts/colab_prototype/package_candidate_rules.py",
)


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    namespace = {}
    requests = []
    commands = []
    events = []
    cli_module = None
    package_module = None
    values = {
        "min_support": "0.25", "min_confidence": "0.5",
        "min_lift": "1.1", "min_term_recurrence": 1,
    }
    streams = ("teacher_subject", "teacher_day", "teacher_time", "teacher_subject_room")
    grid = json.dumps({s: {k: [v] for k, v in values.items()} for s in streams}).encode()
    selected = json.dumps({s: values for s in streams}).encode()
    crosswalk_buffer = io.StringIO()
    crosswalk_writer = csv.DictWriter(
        crosswalk_buffer,
        fieldnames=("research_faculty_key", "faculty_name", "faculty_id"),
        lineterminator="\n",
    )
    crosswalk_writer.writeheader()
    for index, name in enumerate(("FACULTY A", "FACULTY B"), 1):
        crosswalk_writer.writerow(
            {
                "research_faculty_key": uuid5(
                    NAMESPACE_URL, f"chronos:historical-faculty:{name}"
                ),
                "faculty_name": name,
                "faculty_id": f"20000000-0000-0000-0000-{index:012d}",
            }
        )
    crosswalk = crosswalk_buffer.getvalue().encode()

    def records(year, terms, number):
        buffer = io.StringIO()
        fields = (
            "historical_record_id", "academic_year", "term_number", "canonical_course_code",
            "faculty_name", "meeting_days", "start_time", "end_time", "room_code",
            "teacher_subject_eligible", "full_stream_eligible",
        )
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for term in terms:
            for faculty in ("A", "B"):
                number += 1
                writer.writerow(dict(zip(fields, (
                    f"00000000-0000-0000-0000-{number:012d}", year, term,
                    "SUBJ-" + faculty, "FACULTY " + faculty,
                    "MWF" if faculty == "A" else "TTh",
                    "08:00" if faculty == "A" else "10:00",
                    "09:30" if faculty == "A" else "11:30",
                    "ROOM-" + faculty, 1, 1,
                ))))
        return buffer.getvalue().encode()

    training = records("2023-2024", (1, 2), 0)
    holdout = records("2024-2025", (1, 2, 3), 100)
    # Read only Python sources and requirements, never repository dataset files.
    payloads = {path: (BACKEND.parent / path).read_bytes() for path in CODE_PATHS}
    payloads[DATASET_PREFIX + "training_records.csv"] = training
    payloads[DATASET_PREFIX + "holdout_records.csv"] = holdout
    payloads[DATASET_PREFIX + "source_manifest.json"] = json.dumps({
        "holdout_periods": [["2024-2025", term] for term in (1, 2, 3)],
        "output_sha256": {
            name: hashlib.sha256(content).hexdigest() for name, content in (
                ("training_records.csv", training), ("holdout_records.csv", holdout),
            )
        },
    }).encode()

    class StorageRoot(ast.NodeTransformer):
        def visit_Constant(self, node):
            if node.value == "/content":
                return ast.copy_location(ast.Constant(str(tmp_path)), node)
            return node

    def execute(index):
        tree = StorageRoot().visit(ast.parse("".join(cells[index]["source"])))
        exec(compile(ast.fix_missing_locations(tree), f"{NOTEBOOK}:{cells[index]['id']}", "exec"), namespace)

    def authenticate(prompt):
        events.append("authenticate")
        return TOKEN

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
            if path.endswith("holdout_records.csv"):
                assert namespace["lock_downloaded_sha256"]
                assert events[-2:] == ["verify", "authenticate"]
                assert any(
                    Path(call.args[0]) == namespace["selection_lock"] for call in downloads.call_args_list
                )
            requests.append(path)
            events.append("retrieve:" + path)
            return io.BytesIO(payloads[path])

    def build_opener(*handlers):
        assert len(handlers) == 1
        assert handlers[0].redirect_request(None, None, 302, "redirect", {}, "https://example.invalid") is None
        return Opener()

    def run(command, **kwargs):
        nonlocal cli_module, package_module
        # Matplotlib may query fontconfig; no real subprocess is ever launched.
        if command[0] == "fc-list":
            return subprocess.CompletedProcess(command, 0, stdout=b"")
        commands.append(command)
        assert kwargs["cwd"] == namespace["backend_dir"] and kwargs["check"] is True
        if command[1:3] == ["-m", "pip"]:
            assert command[3:] in (["install", "--quiet", "-r", "requirements.txt"], ["freeze"])
            return subprocess.CompletedProcess(command, 0, stdout="synthetic-installed-packages\n")
        assert command[:2] == [sys.executable, "-m"]
        if command[2] == "scripts.colab_prototype.explore_apriori_holdout":
            events.append(command[3])
            if cli_module is None:
                monkeypatch.syspath_prepend(str(namespace["backend_dir"]))
                source = namespace["backend_dir"] / "scripts/colab_prototype/explore_apriori_holdout.py"
                spec = importlib.util.spec_from_file_location("synthetic_notebook_cli", source)
                cli_module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = cli_module
                spec.loader.exec_module(cli_module)
            module = cli_module
        else:
            assert command[2] == "scripts.colab_prototype.package_candidate_rules"
            events.append("package")
            if package_module is None:
                source = namespace["backend_dir"] / "scripts/colab_prototype/package_candidate_rules.py"
                spec = importlib.util.spec_from_file_location("synthetic_package_cli", source)
                package_module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = package_module
                spec.loader.exec_module(package_module)
            module = package_module
        with patch.object(sys, "argv", [str(module.__file__), *command[3:]]):
            module.main()
        return subprocess.CompletedProcess(command, 0)

    def forbidden_network(*args, **kwargs):
        pytest.fail("Notebook workflow tests must not access the network")

    inputs = Mock(side_effect=[
        "YES", COMMIT, "synthetic-v1", "synthetic-assignee", "YES",
        "Synthetic training-only selection", "DOWNLOADED",
    ])
    uploads = Mock(
        side_effect=[
            {"threshold_grid.json": grid},
            {"selected_thresholds.json": selected},
            {"faculty_crosswalk.csv": crosswalk},
        ]
    )
    downloads = Mock()
    authentication = Mock(side_effect=authenticate)
    colab = ModuleType("google.colab")
    colab.files = SimpleNamespace(upload=uploads, download=downloads)
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
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    # Existing tests may have imported the real app. Import the fetched subset afresh.
    saved_modules = {name: module for name, module in sys.modules.items() if name == "app" or name.startswith("app.")}
    for name in saved_modules:
        del sys.modules[name]
    try:
        with patch.dict(os.environ, {"MPLCONFIGDIR": str(tmp_path / "mpl")}, clear=True):
            yield SimpleNamespace(
                execute=execute, namespace=namespace, requests=requests, commands=commands,
                events=events, inputs=inputs, uploads=uploads, downloads=downloads,
                authentication=authentication, payloads=payloads, storage=tmp_path,
            )
    finally:
        for name in list(sys.modules):
            if name == "app" or name.startswith("app.") or name in {
                "synthetic_notebook_cli",
                "synthetic_package_cli",
            }:
                del sys.modules[name]
        sys.modules.update(saved_modules)


def test_notebook_format_and_cell_ids():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert (notebook["nbformat"], notebook["nbformat_minor"]) == (4, 5)
    assert len(notebook["cells"]) == 12
    ids = [cell["id"] for cell in notebook["cells"]]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cell_id) for cell_id in ids)
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["execution_count"] is None and cell["outputs"] == []
            compile("".join(cell["source"]), str(NOTEBOOK), "exec")
    if importlib.util.find_spec("nbformat") is not None:
        import nbformat

        nbformat.validate(notebook)


def test_notebook_staged_workflow_and_checkpoint_metadata(workflow):
    w = workflow
    w.execute(0)
    assert w.requests == [*CODE_PATHS, DATASET_PREFIX + "training_records.csv", DATASET_PREFIX + "source_manifest.json"]
    assert not (w.namespace["preprocessing_dir"] / "holdout_records.csv").exists()
    w.execute(1)
    for name in ("app.core.config", "app.db.session", "app.services.arm.providers", "app.services.arm.service"):
        assert name not in sys.modules
    for name in ("app.services.arm.mining", "app.services.arm.preprocessing"):
        assert Path(sys.modules[name].__file__).is_relative_to(w.namespace["backend_dir"])
    w.execute(2)
    assert not (w.namespace["preprocessing_dir"] / "holdout_records.csv").exists()
    w.execute(3)
    w.execute(4)
    assert w.requests == [*CODE_PATHS, DATASET_PREFIX + "training_records.csv", DATASET_PREFIX + "source_manifest.json", DATASET_PREFIX + "holdout_records.csv"]
    assert w.authentication.call_count == 2
    analysis_commands = [
        command
        for command in w.commands
        if len(command) > 2
        and command[2] == "scripts.colab_prototype.explore_apriori_holdout"
    ]
    assert [command[3] for command in analysis_commands] == [
        "explore",
        "freeze",
        "verify",
        "evaluate",
    ]
    assert any(
        command[2] == "scripts.colab_prototype.package_candidate_rules"
        for command in w.commands
        if len(command) > 2
    )
    assert w.downloads.call_count == 5
    candidate = w.namespace["candidate_package"]
    assert candidate in [Path(call.args[0]) for call in w.downloads.call_args_list]
    package = json.loads(candidate.read_text(encoding="utf-8"))
    assert package["candidate_status"] == "draft"
    assert package["traceability"] == "CHR-100 / CHR-COLAB-004"
    assert len(package["rules"]) == 8
    for index, phase in enumerate(("exploration", "frozen-selection", "evaluation")):
        archive = next(Path(call.args[0]) for call in w.downloads.call_args_list if str(call.args[0]).endswith(f"-{phase}.zip"))
        with ZipFile(archive) as checkpoint:
            names = set(checkpoint.namelist())
            metadata = json.loads(checkpoint.read("run_metadata.json"))
            assert metadata["traceability"] == "CHR-99 / CHR-COLAB-003"
            assert metadata["code_commit"] == metadata["dataset_commit"] == COMMIT
            assert metadata["official_publisher"] == {
                "source": "jira_assignee", "ticket": "CHR-99", "account_id": "synthetic-assignee",
            }
            assert [p["phase"] for p in metadata["completed_phases"]] == ["exploration", "frozen-selection", "evaluation"][:index + 1]
            expected_paths = w.requests if phase == "evaluation" else w.requests[:-1]
            assert metadata["retrieved_sha256"] == {
                path: hashlib.sha256(w.payloads[path]).hexdigest() for path in expected_paths
            }
            assert ("preprocessed/holdout_records.csv" in names) == (phase == "evaluation")
            assert all(TOKEN.encode() not in checkpoint.read(name) for name in names if not name.endswith("/"))
            assert "installed_packages.txt" in names
            if phase != "exploration":
                assert hashlib.sha256(checkpoint.read("selection_lock.json")).hexdigest() == metadata["selection_lock_sha256"]
                assert metadata["selection_rationale"] == "Synthetic training-only selection"
            if phase == "evaluation":
                assert metadata["lock_download_confirmed_at_utc"]
                assert {"evaluation/selected_rules.csv", "evaluation/holdout_evaluation.csv", "evaluation/evaluation_manifest.json"} <= names


@pytest.mark.parametrize("version", [(3, 10), (3, 11)])
def test_notebook_old_python_rejected_before_retrieval_or_install(workflow, monkeypatch, version):
    monkeypatch.setattr(sys, "version_info", version)
    with pytest.raises(RuntimeError, match="Python 3.12"):
        workflow.execute(0)
    assert not workflow.requests and not workflow.commands
    workflow.authentication.assert_not_called()
    workflow.inputs.assert_not_called()
    assert not list(workflow.storage.iterdir())


def test_notebook_replays_and_early_holdout_are_rejected(workflow):
    w = workflow
    w.execute(0)
    for action, message in (
        (lambda: w.execute(0), "Restart the runtime"),
        (lambda: w.namespace["fetch_github"]((w.namespace["holdout_path"],)), "Freeze and download"),
        (lambda: w.namespace["fetch_github"](("dataset/original/synthetic.xlsx",)), "whitelist"),
    ):
        with pytest.raises((RuntimeError, ValueError), match=message):
            action()
    assert w.authentication.call_count == 1
    w.execute(1)
    grid = w.namespace["threshold_grid"].read_bytes()
    archive = Path(w.downloads.call_args.args[0])
    archive_bytes = archive.read_bytes()
    uploads = w.uploads.call_count
    with pytest.raises(RuntimeError, match="Exploration already exists"):
        w.execute(1)
    assert w.uploads.call_count == uploads
    assert w.namespace["threshold_grid"].read_bytes() == grid
    assert archive.read_bytes() == archive_bytes
    w.execute(2)
    for index, message in ((1, "Cannot explore"), (2, "Selection already frozen")):
        with pytest.raises(RuntimeError, match=message):
            w.execute(index)
    w.inputs.side_effect = None
    w.inputs.return_value = "NOT DOWNLOADED"
    with pytest.raises(RuntimeError, match="Download the frozen lock"):
        w.execute(3)
    assert w.authentication.call_count == 1
    assert not (w.namespace["preprocessing_dir"] / "holdout_records.csv").exists()
    w.inputs.return_value = "DOWNLOADED"
    w.execute(3)
    before = list(w.requests)
    with pytest.raises(RuntimeError, match="Evaluation already exists"):
        w.execute(3)
    assert w.requests == before and w.authentication.call_count == 2
    w.execute(4)
    with pytest.raises(RuntimeError, match="already exists"):
        w.execute(4)


def test_notebook_preflight_mutations_rejected_before_authentication(workflow):
    w = workflow
    for index in (0, 1, 2):
        w.execute(index)
    w.inputs.side_effect = None
    w.inputs.return_value = "DOWNLOADED"
    paths = (
        w.namespace["selected_thresholds"],
        w.namespace["preprocessing_dir"] / "training_records.csv",
        w.namespace["preprocessing_dir"] / "source_manifest.json",
        w.namespace["selection_lock"],
        w.namespace["backend_dir"] / "app/services/arm/mining.py",
        w.namespace["backend_dir"] / "app/services/arm/preprocessing.py",
        w.namespace["backend_dir"] / "scripts/colab_prototype/explore_apriori_holdout.py",
    )
    before = list(w.requests)
    for path in paths:
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\n ")
            with pytest.raises((ValueError, RuntimeError), match="hash|changed|does not match"):
                w.execute(3)
        finally:
            path.write_bytes(original)
        assert w.requests == before and w.authentication.call_count == 1
        assert not (w.namespace["preprocessing_dir"] / "holdout_records.csv").exists()
        assert not (w.namespace["run_root"] / "evaluation").exists()
    for name in ("repository", "commit", "dataset_version"):
        original = w.namespace[name]
        try:
            w.namespace[name] = "changed"
            with pytest.raises(RuntimeError, match="Pinned inputs changed"):
                w.execute(3)
        finally:
            w.namespace[name] = original
        assert w.requests == before and w.authentication.call_count == 1
