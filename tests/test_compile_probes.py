"""Black-box probe-driver controls using the real transpiler and real g++.

These rows require the paired native inputs. There are no skip or xfail paths.
Every subprocess has its own sources, report and temporary-file directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts/compile_probes.py"
BASELINE = ROOT / "tests/front_end_rejection_baseline.txt"
GREEN_FIXTURES = (
    "tests/fixtures/community_repros/issue_281_comma_body.pine",
    "tests/fixtures/community_repros/issue_281_string_na.pine",
    "tests/fixtures/community_repros/issue_281_na_array_field.pine",
)
STANDING_BUILD_FAILURE = "tests/fixtures/drawing_lifetime/w9dg-label-collect/strategy.pine"
VERDICTS = {
    "translated_compiled", "front_end_refusal", "compile_failed", "internal_error",
}


def _copy(sources: Path, fixture: str, name: str, *, marker: str = "") -> Path:
    original = ROOT / fixture
    destination = sources / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = original.read_bytes()
    if marker:
        # On the refusal fixture this is the rejected line itself, so a rich
        # diagnostic that accidentally prints source context leaks the marker.
        payload = payload.rstrip(b"\r\n") + f" // {marker}\n".encode("ascii")
    destination.write_bytes(payload)
    return destination


def _refused_fixture() -> str:
    labels = {
        line.strip() for line in BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    # Select an actual baseline entry, rather than inventing a refused script.
    label = "tests/gate-corpus/err/unknown_math.pine"
    assert label in labels
    return label


@pytest.fixture(scope="module")
def native_inputs():
    from tests import _compile

    assert _compile._ENGINE_INC is not None, "paired engine headers are required"
    assert _compile._EIGEN_INC is not None, "Eigen is required"
    assert shutil.which("g++") is not None, "real g++ is required"
    engine = _compile._ENGINE_INC.parent
    env = {
        "PINEFORGE_ENGINE_INCLUDE": str(_compile._ENGINE_INC),
        "PINEFORGE_EIGEN_INCLUDE": str(_compile._EIGEN_INC),
    }
    if _compile._GENERATED_INC is not None:
        env["PINEFORGE_GENERATED_INCLUDE"] = str(_compile._GENERATED_INC)
    return engine, env


def _run(
    case: Path, sources: Path, native_inputs, *,
    engine: Path | None = None, default_engine: bool = False,
    index: Path | None = None, out: Path | None = None,
    extra: tuple[str, ...] = (), env_updates: dict[str, str] | None = None,
    expected_stderr: str = "",
):
    paired_engine, native_env = native_inputs
    case = case.resolve()
    temporary = case / "temporary"
    temporary.mkdir(parents=True)
    destination = (out or case / "reports/probe-compile-report.json").resolve()
    command = [
        sys.executable, "-I", str(DRIVER), "--sources", str(sources),
        "--out", str(destination), "--jobs", "2",
    ]
    if not default_engine:
        command += ["--engine", str(engine or paired_engine)]
    if index is not None:
        command += ["--index", str(index)]
    command += list(extra)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PINEFORGE_REQUIRE_COMPILE": "1",
        "TMPDIR": str(temporary),
        **native_env,
        **(env_updates or {}),
    }
    process = subprocess.run(
        command, cwd=case, env=env, capture_output=True, text=True, check=False,
    )
    # The work's return code and JSON are the verdict; there is no wrapper
    # marker whose successful write could masquerade as a successful compile.
    assert list(temporary.iterdir()) == [], "driver leaked its temporary directory"
    assert not list(case.rglob("*.cpp")), "generated C++ escaped the owned tempdir"
    assert process.stdout == ""
    # Population outcomes stay in the report. Refusal controls supply exactly
    # one documented fixed line; any source, C++ or additional log line fails.
    assert process.stderr == expected_stderr
    return process, destination


def _report(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    assert set(report) == {
        "schema", "engine_sha", "codegen_sha", "source_count", "counts", "entries",
    }
    assert report["schema"] == 1
    assert report["source_count"] == len(report["entries"])
    assert set(report["counts"]) == VERDICTS
    measured = Counter(row["verdict"] for row in report["entries"])
    assert report["counts"] == {verdict: measured[verdict] for verdict in VERDICTS}
    assert sum(report["counts"].values()) == report["source_count"]
    for row in report["entries"]:
        assert row["verdict"] in VERDICTS
        assert set(row) <= {"id", "source_sha256", "verdict", "code", "first_line", "cpp_sha256"}
        assert re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"])
        if row["verdict"] in {"translated_compiled", "compile_failed"}:
            assert re.fullmatch(r"[0-9a-f]{64}", row["cpp_sha256"])
        if "first_line" in row:
            assert len(row["first_line"]) <= 300
            assert "\n" not in row["first_line"]
    return report


def _sha(tree: Path) -> str:
    process = subprocess.run(
        ["git", "-C", str(tree), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return process.stdout.strip()


def _private(marker: str, process, destination: Path) -> None:
    assert marker not in destination.read_text(encoding="utf-8")
    assert marker not in process.stdout
    assert marker not in process.stderr
    assert process.stdout == ""


@pytest.mark.parametrize("indexed", [False, True], ids=["relative-ids", "index-ids"])
def test_three_public_sources_compile_and_do_not_publish_private_comments(
    tmp_path, native_inputs, indexed,
):
    sources = tmp_path / "sources"
    marker = "PRIVATE_COMMENT_" + hashlib.sha256(str(tmp_path).encode()).hexdigest()
    names = ("z-last.pine", "a-first.pine", "nested/middle.pine")
    paths = [
        _copy(sources, fixture, name, marker=marker)
        for fixture, name in zip(GREEN_FIXTURES, names)
    ]
    index = None
    ids = {name: f"probe-{number}" for number, name in enumerate(names)}
    if indexed:
        index = tmp_path / "index.json"
        index.write_text(json.dumps([
            {"id": ids[name], "path": name} for name in reversed(names)
        ]), encoding="utf-8")
    # Exercise the default helper resolution on one of the two green runs.
    process, destination = _run(
        tmp_path / "run", sources, native_inputs,
        index=index, default_engine=indexed,
    )
    assert process.returncode == 0, process.stderr
    report = _report(destination)
    assert report["source_count"] == 3
    assert report["counts"]["translated_compiled"] == 3
    assert report["codegen_sha"] == _sha(ROOT)
    engine = native_inputs[0]
    if (engine / ".git").exists():
        assert report["engine_sha"] == _sha(engine)
    else:
        assert report["engine_sha"] == (ROOT / "ci/engine-pin").read_text().strip()
    ordered = sorted(names)
    assert [row["id"] for row in report["entries"]] == [
        ids[name] if indexed else name for name in ordered
    ]
    for name, row in zip(ordered, report["entries"]):
        assert row["source_sha256"] == hashlib.sha256((sources / name).read_bytes()).hexdigest()
    assert sorted(path.relative_to(sources).as_posix() for path in sources.rglob("*.pine")) == ordered
    assert all(path.is_file() for path in paths)
    _private(marker, process, destination)


def test_front_end_red_control_records_a_code_and_keeps_private_comment_private(
    tmp_path, native_inputs,
):
    sources = tmp_path / "sources"
    marker = "PRIVATE_REFUSAL_" + hashlib.sha256(str(tmp_path).encode()).hexdigest()
    _copy(sources, _refused_fixture(), "refused.pine", marker=marker)
    process, destination = _run(tmp_path / "run", sources, native_inputs)
    assert process.returncode == 1, process.stderr
    report = _report(destination)
    assert report["counts"]["front_end_refusal"] == 1
    row, = report["entries"]
    assert row["verdict"] == "front_end_refusal"
    assert re.fullmatch(r"PF-E[0-9]{4}", row["code"])
    assert row["first_line"]
    assert "cpp_sha256" not in row
    _private(marker, process, destination)


def test_real_registered_compile_failures_are_red_or_the_fix_really_compiles(
    tmp_path, native_inputs,
):
    from tests.test_translate_implies_compile import KNOWN_COMPILE_FAILURES

    sources = tmp_path / "sources"
    if KNOWN_COMPILE_FAILURES:
        fixtures = sorted(KNOWN_COMPILE_FAILURES)
        expected_verdict, expected_status = "compile_failed", 1
    else:
        assert KNOWN_COMPILE_FAILURES == {}, "the compile-failure registry must be empty"
        # Empty registry alone is not proof of a fix: compile the standing
        # public repro too, requiring the fix to have landed.
        fixtures = [STANDING_BUILD_FAILURE]
        expected_verdict, expected_status = "translated_compiled", 0
    for number, fixture in enumerate(fixtures):
        _copy(sources, fixture, f"registered-{number}.pine")
    _copy(sources, GREEN_FIXTURES[0], "zz-green.pine")
    process, destination = _run(tmp_path / "run", sources, native_inputs)
    assert process.returncode == expected_status, process.stderr
    report = _report(destination)
    assert report["source_count"] == len(fixtures) + 1
    assert report["counts"]["translated_compiled"] == (
        1 if expected_verdict == "compile_failed" else len(fixtures) + 1
    )
    assert report["entries"][-1]["id"] == "zz-green.pine"
    assert report["entries"][-1]["verdict"] == "translated_compiled"
    for row in report["entries"][:-1]:
        assert row["verdict"] == expected_verdict
        if expected_verdict == "compile_failed":
            assert row["first_line"].startswith(("error:", "fatal error:"))
            assert "<src>" in row["first_line"]
            assert "is_na" not in row["first_line"]
            assert "std::string" not in row["first_line"]


def test_a_mixed_batch_continues_after_refusal_and_internal_error(tmp_path, native_inputs):
    sources = tmp_path / "sources"
    _copy(sources, _refused_fixture(), "00-refusal.pine")
    invalid = sources / "01-invalid-utf8.pine"
    invalid.write_bytes(b"\xff")
    _copy(sources, GREEN_FIXTURES[0], "99-green.pine")
    process, destination = _run(tmp_path / "run", sources, native_inputs)
    assert process.returncode == 1, process.stderr
    report = _report(destination)
    assert report["counts"] == {
        "translated_compiled": 1, "front_end_refusal": 1,
        "compile_failed": 0, "internal_error": 1,
    }
    assert [row["id"] for row in report["entries"]] == [
        "00-refusal.pine", "01-invalid-utf8.pine", "99-green.pine",
    ]
    assert report["entries"][1]["first_line"] == "UnicodeDecodeError"


def test_empty_population_is_refused_with_a_report(tmp_path, native_inputs):
    sources = tmp_path / "empty"
    sources.mkdir()
    process, destination = _run(
        tmp_path / "run", sources, native_inputs,
        expected_stderr="compile_probes: refused: source selection is empty\n",
    )
    assert process.returncode == 2
    report = _report(destination)
    assert report["source_count"] == 0


@pytest.mark.parametrize("kind", ["pine-file", "non-pine-file", "directory"])
def test_any_symlink_in_the_source_tree_is_refused(tmp_path, native_inputs, kind):
    sources = tmp_path / "sources"
    _copy(sources, GREEN_FIXTURES[0], "green.pine")
    target = tmp_path / "outside"
    if kind == "directory":
        target.mkdir()
        link = sources / "linked-dir"
    else:
        target.write_bytes(b"not followed")
        link = sources / ("linked.pine" if kind == "pine-file" else "linked.bin")
    link.symlink_to(target, target_is_directory=(kind == "directory"))
    process, destination = _run(
        tmp_path / "run", sources, native_inputs,
        expected_stderr="compile_probes: refused: sources tree contains a symlink\n",
    )
    assert process.returncode == 2
    assert _report(destination)["source_count"] == 0


def test_output_inside_sources_is_refused_without_writing_there(tmp_path, native_inputs):
    sources = tmp_path / "sources"
    _copy(sources, GREEN_FIXTURES[0], "green.pine")
    output = sources / "probe-compile-report.json"
    process, destination = _run(
        tmp_path / "run", sources, native_inputs, out=output,
        expected_stderr="compile_probes: refused: output is inside sources tree\n",
    )
    assert process.returncode == 2
    # Writing the rejected destination would itself violate the refusal.
    assert not destination.exists()


def test_deliberate_driver_crash_is_2_and_writes_internal_error_not_a_false_pass(
    tmp_path, native_inputs,
):
    sources = tmp_path / "sources"
    _copy(sources, GREEN_FIXTURES[0], "green.pine")
    empty_engine = tmp_path / "empty-engine"
    empty_engine.mkdir()
    process, destination = _run(
        tmp_path / "run", sources, native_inputs, engine=empty_engine,
        expected_stderr="compile_probes: refused: engine headers not found\n",
    )
    assert process.returncode == 2, process.stderr
    report = _report(destination)
    assert report["source_count"] == 1
    assert report["counts"]["internal_error"] == 1
    assert report["counts"]["translated_compiled"] == 0
    assert report["entries"][0]["first_line"] == "RefusedPath"


def test_missing_gpp_refuses_instead_of_skipping(tmp_path, native_inputs):
    sources = tmp_path / "sources"
    _copy(sources, GREEN_FIXTURES[0], "green.pine")
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    process, destination = _run(
        tmp_path / "run", sources, native_inputs,
        env_updates={"PATH": str(empty_path)},
        expected_stderr="compile_probes: refused: g++ not found\n",
    )
    assert process.returncode == 2
    assert _report(destination)["counts"]["internal_error"] == 1


@pytest.mark.parametrize("rows", [
    [],
    [{"id": "probe-a", "path": "green.pine"}, {"id": "probe-b", "path": "green.pine"}],
    [{"id": "probe-a", "path": "../green.pine"}],
    [{"id": "probe-a", "path": "green.pine"}, {"id": "probe-a", "path": "extra.pine"}],
], ids=["incomplete", "duplicate-path", "escape", "duplicate-id"])
def test_an_index_cannot_drop_sources_or_rename_them_ambiguously(
    tmp_path, native_inputs, rows,
):
    sources = tmp_path / "sources"
    _copy(sources, GREEN_FIXTURES[0], "green.pine")
    index = tmp_path / "index.json"
    index.write_text(json.dumps(rows), encoding="utf-8")
    process, destination = _run(
        tmp_path / "run", sources, native_inputs, index=index,
        expected_stderr="compile_probes: refused: index must cover sources exactly\n",
    )
    assert process.returncode == 2
    assert _report(destination)["source_count"] == 0
