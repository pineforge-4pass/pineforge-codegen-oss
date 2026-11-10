"""Compile every fixture that translates; retain all compiler failures.

Known compiler defects have strict, exception-specific xfails. All issue #281 rows
must translate and compile without xfail markers.
Reports include known compile failures as well as every unexpected compiler failure.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import STRATEGY_FP_FLAGS, compile_cpp, skip_if_no_compile_env
from tests.test_compile_corpus import _resolve_corpus_root


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOTS = (ROOT / "tests/fixtures", ROOT / "tests/gate-corpus")
CORPUS_FLOOR = 312
ISSUE_281 = tuple(
    ROOT / "tests/fixtures/community_repros" / name
    for name in (
        "issue_281_comma_body.pine",
        "issue_281_string_na.pine",
        "issue_281_na_array_field.pine",
    )
)
KNOWN_COMPILE_FAILURES = {
    "tests/fixtures/drawing_lifetime/w9dg-label-collect/strategy.pine": (
        "same class as issue #281 case 3: no is_na overload for std::string"
    ),
}
MATRIX_SCHEMA_VERSION = 1
MATRIX_GENERATOR_VERSION = "3"
MATRIX_INPUT_PINS = {
    "generator": (
        "scripts/generate_compile_matrix.py",
        "cb32efc3df5fd6b7936d78e7821fdd10227b6b0fa8257f562ab686d2354a7cfe",
    ),
    "spec": (
        "tests/fixtures/generated_compile_matrix/coverage_spec.json",
        "4a18c44b2f33f55530ed2b6e12fe45b77f1f8524e5f3f720f8617770196cedd9",
    ),
    "seedIndex": (
        "tests/fixtures/generated_compile_matrix/seed-index.json",
        "e1fe87c0f8e9a4b25980206c03d1800c4dbf3a3940b062461a10c5eacb10b867",
    ),
    "seed:single_line_parameter_after_comma": (
        "tests/fixtures/generated_compile_matrix/single_line_parameter_after_comma.pine",
        "0c42aca26fa0199fbd4547efb48fe08e4f2e789954aba075584731a2318c842d",
    ),
    "seed:ternary_string_missing_branch": (
        "tests/fixtures/generated_compile_matrix/ternary_string_missing_branch.pine",
        "9d9aac30e97db1448e8aeb392ecfb4e3ad3ce3be0ab535353acc7d4da74566c8",
    ),
    "seed:array_string_field_missing_predicate": (
        "tests/fixtures/generated_compile_matrix/array_string_field_missing_predicate.pine",
        "9cd4381f340e216579c6616108e77cf523ba625f2281b16fcde254a777911cac",
    ),
}


def _checked_path(path: Path) -> Path:
    resolved = path.resolve()
    for candidate in (path.absolute(), resolved):
        if (
            any(part in {".ssh", ".aws", ".codex"} or part.endswith(".env")
                for part in candidate.parts)
            or "/.config/zsh/keys" in candidate.as_posix()
            or "/.config/pineforge" in candidate.as_posix()
        ):
            raise ValueError(f"refusing fixture sweep path: {candidate}")
    return resolved


def _fixtures() -> list[Path]:
    paths = []
    for root in FIXTURE_ROOTS:
        assert root.is_dir(), f"fixture root is missing: {root}"
        paths.extend(root.rglob("*.pine"))
    return sorted(paths)


def _label(path: Path, *, corpus_root: Path | None = None) -> str:
    if corpus_root is not None:
        return f"engine-corpus/{path.relative_to(corpus_root).as_posix()}"
    return Path(os.path.relpath(path, ROOT)).as_posix()


def _ordinary_paths(paths: list[Path]) -> list[Path]:
    return [
        path for path in paths
        if path not in ISSUE_281 and _label(path) not in KNOWN_COMPILE_FAILURES
    ]


def _libraries() -> dict[str, str]:
    root = ROOT / "tests/fixtures/pine_libraries"
    paths = sorted(root.rglob("*.pine"))
    for path in paths:
        _checked_path(path)
    print("Fixture sweep library files:", *(str(path) for path in paths),
          sep="\n", flush=True)
    return {
        path.relative_to(root).with_suffix("").as_posix(): path.read_text(encoding="utf-8")
        for path in paths
    }


def _assert_corpus_floor(paths: list[Path]) -> None:
    assert len(paths) >= CORPUS_FLOOR, (
        f"paired engine corpus has {len(paths)} strategies; minimum is {CORPUS_FLOOR}"
    )


def _read_rejection_baseline(path: Path) -> set[str]:
    path = _checked_path(path)
    print(f"Front-end rejection baseline file: {path}", flush=True)
    labels = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        label = line.strip()
        if not label or label.startswith("#"):
            continue
        assert label not in seen, f"duplicate front-end rejection baseline entry: {label}"
        labels.append(label)
        seen.add(label)
    assert labels == sorted(labels), "front-end rejection baseline must be sorted"
    return seen


def _pairing() -> dict[str, str]:
    required = os.environ.get("PINEFORGE_REQUIRE_COMPILE") == "1"
    pairing_mode = "unrecorded"
    if required:
        pairing_mode = os.environ.get("PINEFORGE_ENGINE_PAIRING", "pinned")
        if pairing_mode not in {"pinned", "floating"}:
            pytest.fail(
                "PINEFORGE_REQUIRE_COMPILE=1: PINEFORGE_ENGINE_PAIRING must be "
                "either 'pinned' or 'floating'",
                pytrace=False,
            )
    engine_pin = "unrecorded"
    if required:
        pin_path = _checked_path(ROOT / "ci/engine-pin")
        try:
            pin_contents = pin_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            pytest.fail(
                f"PINEFORGE_REQUIRE_COMPILE=1: ci/engine-pin cannot be read: {error}",
                pytrace=False,
            )
        if not re.fullmatch(r"[0-9a-f]{40}\n", pin_contents):
            pytest.fail(
                "PINEFORGE_REQUIRE_COMPILE=1: ci/engine-pin must contain exactly "
                "one line of 40 lowercase hex characters",
                pytrace=False,
            )
        engine_pin = pin_contents[:-1]

    pairing = {}
    for key, variable in (
        ("engine_sha", "PINEFORGE_ENGINE_SHA"), ("corpus_sha", "PINEFORGE_CORPUS_SHA"),
    ):
        value = os.environ.get(variable, "")
        if re.fullmatch(r"[0-9a-fA-F]{40}", value):
            pairing[key] = value
        elif os.environ.get("PINEFORGE_REQUIRE_COMPILE") == "1":
            pytest.fail(
                f"PINEFORGE_REQUIRE_COMPILE=1: {variable} must be 40 hex characters",
                pytrace=False,
            )
        else:
            pairing[key] = "unrecorded"
        if (
            required
            and pairing_mode == "pinned"
            and key == "engine_sha"
            and pairing[key] != engine_pin
        ):
            pytest.fail(
                f"PINEFORGE_REQUIRE_COMPILE=1: {variable} {pairing[key]} does not "
                f"match ci/engine-pin {engine_pin}",
                pytrace=False,
            )
    pairing["engine_pin"] = engine_pin
    pairing["pairing_mode"] = pairing_mode
    return pairing


def _shard(paths: list[Path]) -> list[Path]:
    index = int(os.environ.get("PINEFORGE_COMPILE_SHARD", "0"))
    count = int(os.environ.get("PINEFORGE_COMPILE_SHARDS", "1"))
    assert count > 0 and 0 <= index < count, "invalid fixture compile shard"
    return paths[index::count]


def _first_error(error: AssertionError) -> str:
    # The fallback keeps these tests usable with the pre-rule helper for
    # red-first verification. The updated helper retains untruncated stderr.
    diagnostics = getattr(error, "diagnostics", str(error).split("--- emitted C++", 1)[0])
    lines = diagnostics.splitlines()
    return next(
        (line.strip() for line in lines if re.search(r"\b(?:fatal )?error:", line)),
        lines[0] if lines else str(error).splitlines()[0],
    )


@dataclass
class Result:
    path: str
    outcome: str
    diagnostic: str = ""
    seconds: float = 0.0


def _assert_front_end_rejections(
    labels: list[str], results: list[Result], baseline: set[str],
) -> None:
    rejected = {result.path for result in results if result.outcome == "front-end-rejected"}
    new = sorted(rejected - baseline)
    stale = sorted((baseline & set(labels)) - rejected)
    errors = []
    if new:
        errors.append("new front-end rejections:\n  " + "\n  ".join(new))
    if stale:
        errors.append("stale front-end rejection baseline entries:\n  " + "\n  ".join(stale))
    assert not errors, "\n".join(errors)


class FixtureCompileFailure(AssertionError):
    """Only a failed compiler result can satisfy a compile-failure xfail."""


def _compile(path: Path, cpp: str, *, corpus_root: Path | None = None) -> Result:
    started = time.monotonic()
    label = _label(path, corpus_root=corpus_root)
    try:
        compile_cpp(cpp, label=label, extra_flags=STRATEGY_FP_FLAGS)
    except AssertionError as error:
        return Result(label, "compile-failed", _first_error(error),
                      time.monotonic() - started)
    return Result(label, "compiled", seconds=time.monotonic() - started)


def _compile_sources(
    sources: list[tuple[Path, str]], *, corpus_root: Path | None = None,
) -> list[Result]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [
            pool.submit(_compile, path, cpp, corpus_root=corpus_root) for path, cpp in sources
        ]
        return [future.result() for future in pending]


def _sweep(paths: list[Path], *, corpus_root: Path | None = None) -> list[Result]:
    """Transpile serially, then use at most two concurrent compiler processes."""
    results = []
    sources = []
    # These are the only source files this sweep follows.
    for path in paths:
        _checked_path(path)
    print("Fixture sweep source files:", *(str(path) for path in paths),
          sep="\n", flush=True)
    libraries = _libraries()
    for path in paths:
        label = _label(path, corpus_root=corpus_root)
        try:
            cpp = transpile(path.read_text(encoding="utf-8"),
                            filename=label, libraries=libraries)
        except CompileError as error:
            # Rejection is outside the implication. Keep it visible in
            # a separate report; it is never counted as compiled.
            results.append(Result(label, "front-end-rejected", str(error).splitlines()[0]))
        else:
            sources.append((path, cpp))
    # Read every work result, even when an earlier compile failed.
    results.extend(_compile_sources(sources, corpus_root=corpus_root))
    return results


def _sweep_report_dir(tmp_path_factory) -> Path:
    """The report directory: the environment override, else one folder per pytest session.

    A plain function, not a fixture: the pairing controls of tests/test_require_compile.py
    import `sweep_results` alone into a probe module, so that fixture must not depend on
    another fixture of this module. Both fixtures that write a report call this function
    and so share the same directory.
    """
    configured = os.environ.get("PINEFORGE_COMPILE_REPORT_DIR")
    destination = _checked_path(
        Path(configured) if configured
        else tmp_path_factory.getbasetemp() / "translate-implies-compile"
    )
    destination.mkdir(parents=True, exist_ok=True)
    return destination


@pytest.fixture(scope="module")
def sweep_results(tmp_path_factory):
    # Setup failures happen outside all xfail-marked test bodies.
    pairing = _pairing()
    skip_if_no_compile_env()
    # An incompatible header tree or missing generated header must fail setup,
    # before the known compile-failure xfail markers can apply.
    compile_cpp(
        transpile('//@version=6\nstrategy("compile sweep preflight")\n'),
        label="fixture-sweep-preflight", extra_flags=STRATEGY_FP_FLAGS,
    )
    started = time.monotonic()
    results: list[Result] = []
    yield results
    destination = _sweep_report_dir(tmp_path_factory)
    results.sort(key=lambda result: result.path)
    failures = [result for result in results if result.outcome == "compile-failed"]
    rejected = [result for result in results if result.outcome == "front-end-rejected"]
    (destination / "compile-failures.txt").write_text(
        "".join(f"{result.path}\t{result.diagnostic}\n" for result in failures),
        encoding="utf-8",
    )
    (destination / "front-end-rejections.txt").write_text(
        "".join(f"{result.path}\t{result.diagnostic}\n" for result in rejected),
        encoding="utf-8",
    )
    (destination / "results.json").write_text(json.dumps({
        **pairing,
        "shard": int(os.environ.get("PINEFORGE_COMPILE_SHARD", "0")),
        "shards": int(os.environ.get("PINEFORGE_COMPILE_SHARDS", "1")),
        "wall_seconds": time.monotonic() - started,
        "results": [vars(result) for result in results],
    }, indent=2) + "\n", encoding="utf-8")
    (destination / "pairing.txt").write_text(
        f"PINEFORGE_ENGINE_SHA={pairing['engine_sha']} "
        f"PINEFORGE_CORPUS_SHA={pairing['corpus_sha']} "
        f"engine_pin={pairing['engine_pin']} "
        f"pairing_mode={pairing['pairing_mode']}\n",
        encoding="utf-8",
    )
    print(f"Fixture compile report: {destination / 'compile-failures.txt'}")


def test_compiler_batch_keeps_both_failures_and_the_success(sweep_results):
    # Compile real malformed C++, independently of any codegen bug that CGI
    # may fix. These are diagnostic labels, not files the batch opens.
    sources = [
        (ROOT / "tests/control-first.cpp", "int first = ;\n"),
        (ROOT / "tests/control-valid.cpp", "int valid = 1;\n"),
        (ROOT / "tests/control-last.cpp", "int last = ;\n"),
    ]
    results = _compile_sources(sources)
    assert [result.path for result in results] == [_label(path) for path, _ in sources]
    assert [result.outcome for result in results] == [
        "compile-failed", "compiled", "compile-failed",
    ]
    for result in (results[0], results[2]):
        assert re.search(r"\berror:", result.diagnostic), result
    assert results[1].diagnostic == ""


def test_shards_cover_every_repository_fixture_once(monkeypatch):
    paths = _fixtures()
    ordinary = _ordinary_paths(paths)
    monkeypatch.setenv("PINEFORGE_COMPILE_SHARDS", "4")
    collected = []
    for index in range(4):
        monkeypatch.setenv("PINEFORGE_COMPILE_SHARD", str(index))
        collected.extend(_shard(ordinary))
    collected.extend(ISSUE_281)
    collected.extend(ROOT / label for label in KNOWN_COMPILE_FAILURES)
    assert sorted(collected) == paths
    assert len(set(collected)) == len(paths)
    assert all(path in paths for path in ISSUE_281)


def test_fixture_translates_implies_compiles(sweep_results):
    paths = _shard(_ordinary_paths(_fixtures()))
    results = _sweep(paths)
    sweep_results.extend(results)
    _assert_front_end_rejections(
        [_label(path) for path in paths], results,
        _read_rejection_baseline(ROOT / "tests/front_end_rejection_baseline.txt"),
    )
    failures = [result for result in results if result.outcome == "compile-failed"]
    assert not failures, "\n".join(
        f"{result.path}\t{result.diagnostic}" for result in failures
    )


def test_paired_engine_corpus_translates_implies_compiles(sweep_results):
    corpus = _resolve_corpus_root()
    if corpus is None:
        if os.environ.get("PINEFORGE_REQUIRE_COMPILE") == "1":
            pytest.fail("PINEFORGE_REQUIRE_COMPILE=1: paired engine corpus is missing")
        pytest.skip("paired engine corpus is unavailable")
    _checked_path(corpus)
    # Cover the deeper special-validation and symbol-specified directories too.
    paths = sorted(corpus.rglob("strategy.pine"))
    assert paths, f"paired engine corpus contains no strategy.pine: {corpus}"
    _assert_corpus_floor(paths)
    paths = _shard(_ordinary_paths(paths))
    results = _sweep(paths, corpus_root=corpus)
    sweep_results.extend(results)
    _assert_front_end_rejections(
        [_label(path, corpus_root=corpus) for path in paths], results,
        _read_rejection_baseline(ROOT / "tests/front_end_rejection_baseline.txt"),
    )
    failures = [result for result in results if result.outcome == "compile-failed"]
    assert not failures, "\n".join(
        f"{result.path}\t{result.diagnostic}" for result in failures
    )


@pytest.mark.parametrize("pine_path", [
    pytest.param(path, id=path.stem)
    for path in ISSUE_281
])
def test_issue_281_translates_and_compiles(pine_path, sweep_results):
    _checked_path(pine_path)
    print(f"Issue #281 source file: {pine_path}", flush=True)
    # Front-end and compiler failures must both fail this unmarked row.
    cpp = transpile(pine_path.read_text(encoding="utf-8"), filename=_label(pine_path))
    result = _compile(pine_path, cpp)
    sweep_results.append(result)
    if result.outcome != "compiled":
        raise FixtureCompileFailure(f"{result.path}\t{result.diagnostic}")


@pytest.mark.parametrize("pine_path", [
    pytest.param(path, id=path.stem)
    for path in ISSUE_281
])
def test_issue_281_rows_carry_no_xfail(pine_path):
    parameters, = (
        mark.args[1] for mark in test_issue_281_translates_and_compiles.pytestmark
        if mark.name == "parametrize"
    )
    assert sorted(
        (ROOT / "tests/fixtures/community_repros").glob("issue_281_*.pine")
    ) == sorted(ISSUE_281)
    assert len(ISSUE_281) == 3
    assert all(path.is_file() for path in ISSUE_281)
    parameter, = (parameter for parameter in parameters if parameter.values == (pine_path,))
    xfails = [mark for mark in parameter.marks if mark.name == "xfail"]
    assert not xfails


@pytest.mark.parametrize("pine_path", [
    pytest.param(
        ROOT / label, id=label,
        marks=pytest.mark.xfail(
            strict=True, raises=FixtureCompileFailure, reason=reason,
        ),
    )
    for label, reason in KNOWN_COMPILE_FAILURES.items()
])
def test_known_compile_failure_translates_and_compiles(pine_path, sweep_results):
    _checked_path(pine_path)
    print(f"Known compile failure source file: {pine_path}", flush=True)
    cpp = transpile(pine_path.read_text(encoding="utf-8"), filename=_label(pine_path))
    result = _compile(pine_path, cpp)
    sweep_results.append(result)
    if result.outcome != "compiled":
        raise FixtureCompileFailure(f"{result.path}\t{result.diagnostic}")


@pytest.mark.parametrize("label", KNOWN_COMPILE_FAILURES)
def test_known_compile_failure_is_a_separate_strict_row(label):
    pine_path = ROOT / label
    _checked_path(pine_path)
    assert pine_path.is_file(), f"known compile failure is missing: {label}"
    paths = _fixtures()
    assert pine_path in paths
    assert pine_path not in _ordinary_paths(paths)
    parameters, = (
        mark.args[1] for mark in test_known_compile_failure_translates_and_compiles.pytestmark
        if mark.name == "parametrize"
    )
    assert [parameter.values[0] for parameter in parameters] == [
        ROOT / registered for registered in KNOWN_COMPILE_FAILURES
    ]
    parameter, = (parameter for parameter in parameters if parameter.values == (pine_path,))
    xfail, = (mark for mark in parameter.marks if mark.name == "xfail")
    assert xfail.kwargs["strict"] is True
    assert xfail.kwargs["raises"] is FixtureCompileFailure
    assert xfail.kwargs["reason"] == KNOWN_COMPILE_FAILURES[label]


def _rejection_control_tree(tmp_path, monkeypatch, source: str, baseline: str):
    roots = (tmp_path / "tests/fixtures", tmp_path / "tests/gate-corpus")
    for root in roots:
        root.mkdir(parents=True)
    path = roots[0] / "control.pine"
    path.write_text(source, encoding="utf-8")
    baseline_path = tmp_path / "tests/front_end_rejection_baseline.txt"
    baseline_path.write_text(baseline, encoding="utf-8")
    # Configure input locations only; the parser, sweep and checks remain real.
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    monkeypatch.setitem(globals(), "FIXTURE_ROOTS", roots)
    monkeypatch.setenv("PINEFORGE_COMPILE_SHARD", "0")
    monkeypatch.setenv("PINEFORGE_COMPILE_SHARDS", "1")
    return path, baseline_path


def test_corpus_floor_rejects_a_partial_checkout_before_sharding(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    (corpus / "data").mkdir(parents=True)
    first = corpus / "000/strategy.pine"
    first.parent.mkdir(parents=True)
    source = '//@version=6\nstrategy("floor control")\nvalue = )\n'
    first.write_text(source, encoding="utf-8")
    monkeypatch.setenv("PINEFORGE_ENGINE_CORPUS", str(corpus))
    monkeypatch.setenv("PINEFORGE_COMPILE_SHARD", "3")
    monkeypatch.setenv("PINEFORGE_COMPILE_SHARDS", "4")
    message = r"paired engine corpus has 1 strategies; minimum is 312"
    # This existing entry point accepts the nonempty checkout on main. Its
    # shard is empty, so the missing floor, rather than compilation, is tested.
    with pytest.raises(AssertionError, match=message):
        test_paired_engine_corpus_translates_implies_compiles([])
    with pytest.raises(AssertionError, match=message):
        _assert_corpus_floor(sorted(corpus.rglob("strategy.pine")))
    for index in range(1, 312):
        path = corpus / f"{index:03d}/strategy.pine"
        path.parent.mkdir()
        path.write_text(source, encoding="utf-8")
    paths = sorted(corpus.rglob("strategy.pine"))
    assert len(paths) == 312
    _assert_corpus_floor(paths)
    assert len(_shard(paths)) == 78


@pytest.mark.parametrize("baseline,message", [
    (
        "tests/fixtures/control.pine\ntests/fixtures/control.pine\n",
        r"duplicate front-end rejection baseline entry: tests/fixtures/control\.pine",
    ),
    (
        "tests/fixtures/control.pine\nengine-corpus/a/strategy.pine\n",
        r"front-end rejection baseline must be sorted",
    ),
], ids=["duplicate", "unsorted"])
def test_rejection_baseline_reader_rejects_noncanonical_entries(
    tmp_path, monkeypatch, baseline, message,
):
    _, baseline_path = _rejection_control_tree(
        tmp_path, monkeypatch,
        '//@version=6\nstrategy("reader control")\nvalue = )\n', baseline,
    )
    with pytest.raises(AssertionError, match=message):
        test_fixture_translates_implies_compiles([])
    baseline_path.write_text(
        "# reader control\n\nengine-corpus/a/strategy.pine\n"
        "# a comment between entries\n\ntests/fixtures/control.pine\n",
        encoding="utf-8",
    )
    assert _read_rejection_baseline(baseline_path) == {
        "engine-corpus/a/strategy.pine", "tests/fixtures/control.pine",
    }


def test_front_end_ratchet_rejects_a_new_rejection(tmp_path, monkeypatch):
    path, baseline_path = _rejection_control_tree(
        tmp_path, monkeypatch,
        '//@version=6\nstrategy("new rejection control")\nvalue = )\n',
        "# no allowed rejections\n",
    )
    results = []
    message = r"new front-end rejections:\n\s+tests/fixtures/control\.pine"
    with pytest.raises(AssertionError, match=message):
        test_fixture_translates_implies_compiles(results)
    assert [(result.path, result.outcome) for result in results] == [
        ("tests/fixtures/control.pine", "front-end-rejected"),
    ]
    with pytest.raises(AssertionError, match=message):
        _assert_front_end_rejections(
            [_label(path)], results, _read_rejection_baseline(baseline_path),
        )
    baseline_path.write_text(
        "engine-corpus/another-shard/strategy.pine\ntests/fixtures/control.pine\n",
        encoding="utf-8",
    )
    _assert_front_end_rejections(
        [_label(path)], results, _read_rejection_baseline(baseline_path),
    )


def test_front_end_ratchet_rejects_a_stale_entry(tmp_path, monkeypatch):
    skip_if_no_compile_env()
    path, baseline_path = _rejection_control_tree(
        tmp_path, monkeypatch,
        '//@version=6\nstrategy("stale rejection control")\n',
        "engine-corpus/another-shard/strategy.pine\ntests/fixtures/control.pine\n",
    )
    results = []
    message = r"stale front-end rejection baseline entries:\n\s+tests/fixtures/control\.pine"
    with pytest.raises(AssertionError, match=message):
        test_fixture_translates_implies_compiles(results)
    assert [(result.path, result.outcome) for result in results] == [
        ("tests/fixtures/control.pine", "compiled"),
    ]
    with pytest.raises(AssertionError, match=message):
        _assert_front_end_rejections(
            [_label(path)], results, _read_rejection_baseline(baseline_path),
        )
    baseline_path.write_text("engine-corpus/another-shard/strategy.pine\n",
                             encoding="utf-8")
    _assert_front_end_rejections(
        [_label(path)], results, _read_rejection_baseline(baseline_path),
    )


def test_engine_corpus_ratchet_uses_root_relative_labels(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    paths = []
    for index in range(312):
        bucket = corpus / "data" if index == 2 else corpus
        path = bucket / f"{index:03d}/strategy.pine"
        path.parent.mkdir(parents=True)
        path.write_text('//@version=6\nstrategy("corpus label control")\nvalue = )\n',
                        encoding="utf-8")
        paths.append(path)
    paths.sort()
    baseline = tmp_path / "tests/front_end_rejection_baseline.txt"
    baseline.parent.mkdir()
    labels = [f"engine-corpus/{path.relative_to(corpus).as_posix()}" for path in paths]
    baseline.write_text("\n".join(labels) + "\n", encoding="utf-8")
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    monkeypatch.setenv("PINEFORGE_ENGINE_CORPUS", str(corpus))
    monkeypatch.setenv("PINEFORGE_COMPILE_SHARD", "3")
    monkeypatch.setenv("PINEFORGE_COMPILE_SHARDS", "4")
    results = []
    test_paired_engine_corpus_translates_implies_compiles(results)
    assert [(result.path, result.outcome) for result in results] == [
        (label, "front-end-rejected") for label in labels[3::4]
    ]


def _matrix_group(case: dict) -> str:
    expected = case["expected"]
    diagnostics = (
        case["expectedDiagnosticCode"], case["expectedDiagnosticPhase"],
        case["expectedDiagnosticHint"],
    )
    if (
        case["legality"] == "valid"
        and expected["classification"] in {"valid_intended_supported", "valid_unsupported"}
        and expected["translation"] == expected["compile"] == "MUST_SUCCEED"
        and diagnostics == (None, None, None)
        and case["renderFamily"] in {"ordinary", "community_seed"}
    ):
        return case["renderFamily"]
    if (
        case["legality"] == "invalid"
        and case["renderFamily"] == "invalid_control"
        and expected["classification"] == "language_invalid"
        and expected["translation"] == "MUST_REJECT_WITH_EXPECTED_PF_E"
        and expected["compile"] == "NOT_APPLICABLE"
        and isinstance(diagnostics[0], str)
        and re.fullmatch(r"PF-E[0-9]{4}", diagnostics[0])
        and isinstance(diagnostics[1], str) and diagnostics[1]
        and isinstance(diagnostics[2], str) and diagnostics[2]
        and isinstance(case["expectedDiagnosticMessage"], str)
        and case["expectedDiagnosticMessage"]
    ):
        return "invalid_control"
    raise ValueError("inconsistent legality, expectation, family or diagnostic fields")


def _matrix_source(output_dir: Path, source_path: str) -> Path:
    relative = Path(source_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"sourcePath must stay below output directory: {source_path}")
    path = output_dir / relative
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"symlink in matrix source path: {component}")
    return _checked_path(path)


def check_matrix_manifest(manifest, output_dir) -> list[str]:
    """Check the reviewed interface and exact disk inputs without a compiler."""
    problems = []

    def problem(message):
        problems.append("INCOMPLETE: incompatible matrix manifest: " + message)

    if not isinstance(manifest, dict):
        problem("manifest must be an object")
        return problems
    if type(manifest.get("schemaVersion")) is not int or (
        manifest.get("schemaVersion") != MATRIX_SCHEMA_VERSION
    ):
        problem("schemaVersion must be integer 1")
    if manifest.get("generatorVersion") != MATRIX_GENERATOR_VERSION:
        problem('generatorVersion must be string "3"')
    identities = manifest.get("inputIdentities")
    if not isinstance(identities, dict):
        problem("inputIdentities must be an object")
        identities = {}
    if set(identities) != set(MATRIX_INPUT_PINS):
        problem("inputIdentities keys differ from reviewed inputs")
    for identity, (relative, pinned) in MATRIX_INPUT_PINS.items():
        recorded = identities.get(identity)
        if recorded != pinned:
            problem(f"inputIdentities.{identity} pin mismatch or missing")
        try:
            path = _checked_path(ROOT / relative)
            print(f"Matrix input file: {path}", flush=True)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError) as error:
            problem(f"inputIdentities.{identity} input unavailable: {error}")
            continue
        if actual != pinned or actual != recorded:
            problem(f"inputIdentities.{identity} differs from real file sha256")
        header = {"generator": "generatorSourceHash", "spec": "specSourceHash"}.get(identity)
        if header is not None and (manifest.get(header) != pinned or manifest.get(header) != actual):
            problem(f"{header} pin mismatch, missing or differs from real file sha256")

    for section, fields in (
        ("generation", ("status",)),
        ("coverage", ("status", "inventoryStatus", "positiveDomainStatus", "positiveCompileCoverage")),
        ("results", ("status",)),
        ("consumerContract", ("status", "positive", "invalidControl", "failureRule", "verdictRule")),
    ):
        value = manifest.get(section)
        if not isinstance(value, dict):
            problem(f"{section} must be an object")
        elif any(not isinstance(value.get(field), str) or not value[field] for field in fields):
            problem(f"{section} is missing required status/contract fields")
    cases = manifest.get("cases")
    coverage = manifest.get("coverage")
    if not isinstance(cases, list) or not cases:
        problem("cases must be a nonempty list")
        return problems
    if not isinstance(coverage, dict):
        return problems
    groups = {"ordinary": [], "community_seed": [], "invalid_control": []}
    ids = []
    paths = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            problem("case has missing or invalid id")
            continue
        name = case["id"]
        ids.append(name)
        try:
            groups[_matrix_group(case)].append(name)
        except (KeyError, TypeError, ValueError) as error:
            problem(f"{name}: case classification: {error}")
        try:
            source = _matrix_source(Path(output_dir), case["sourcePath"])
            paths.append(source)
            print(f"Matrix source file: {source}", flush=True)
            actual = hashlib.sha256(source.read_bytes()).hexdigest()
            if case.get("sourceSha256") != actual:
                problem(f"{name}: sourceSha256 differs from source bytes")
        except (KeyError, OSError, TypeError, ValueError) as error:
            problem(f"{name}: source unavailable: {error}")
    if len(set(ids)) != len(ids):
        problem("duplicate case ids")
    if len(set(paths)) != len(paths):
        problem("duplicate source paths")
    ordinary = len(groups["ordinary"])
    controls = len(groups["invalid_control"])
    seeds = len(groups["community_seed"])
    for field, actual in (
        ("positiveUnionCount", ordinary), ("positiveEmittedCount", ordinary),
        ("invalidControlCount", controls), ("seedCount", seeds),
        ("ordinarySelectedCount", ordinary + controls),
        ("ordinaryEmittedCount", ordinary + controls), ("totalEmittedCount", len(cases)),
    ):
        if type(coverage.get(field)) is not int or coverage[field] != actual:
            problem(f"coverage.{field} does not match computed case count {actual}")
    if sorted(groups["ordinary"]) != coverage.get("positiveUnionCaseIds"):
        problem("coverage.positiveUnionCaseIds differs from ordinary positives")
    specification = manifest.get("specification")
    if not isinstance(specification, dict):
        problem("specification is missing")
    else:
        required_seeds = specification.get("requiredSeeds")
        if (
            not isinstance(required_seeds, list)
            or any(not isinstance(seed, str) for seed in required_seeds)
            or sorted(groups["community_seed"]) != sorted(required_seeds)
        ):
            problem("community seeds differ from specification.requiredSeeds")
        declared_controls = specification.get("invalidControls")
        if not isinstance(declared_controls, list) or controls != len(declared_controls):
            problem("invalid control count differs from specification.invalidControls")
    producer_results = manifest.get("results", {})
    if not isinstance(producer_results, dict) or (
        not isinstance(producer_results.get("cases"), dict)
        or set(producer_results["cases"]) != set(ids)
    ):
        problem("results.cases does not contain every case exactly once")
    return problems


def _matrix_phase(outcome=None, diagnostic=None, *, diagnostics=None):
    return {
        "outcome": outcome, "firstDiagnostic": diagnostic,
        "diagnostics": diagnostics, "exitCode": None,
    }


def _matrix_selection(manifest):
    groups = {"ordinary": [], "community_seed": [], "invalid_control": [], "unclassified": []}
    cases = manifest.get("cases", [])
    for case in cases if isinstance(cases, list) else []:
        if not isinstance(case, dict):
            continue
        try:
            if not isinstance(case.get("id"), str) or not case["id"]:
                raise ValueError("missing or invalid case id")
            group = _matrix_group(case)
        except (KeyError, TypeError, ValueError):
            group = "unclassified"
        groups[group].append(case)
    positives = sorted(
        groups["ordinary"] + groups["community_seed"], key=lambda case: case["id"],
    )
    selected_ids = set(_shard([case["id"] for case in positives]))
    selected = [case for case in positives if case["id"] in selected_ids]
    controls = sorted(groups["invalid_control"], key=lambda case: case["id"])
    return groups, selected, controls


@pytest.fixture(scope="module")
def generated_matrix(tmp_path_factory, request):
    output = tmp_path_factory.mktemp("generated-compile-matrix") / "packet"
    report = {
        "shard": int(os.environ.get("PINEFORGE_COMPILE_SHARD", "0")),
        "shards": int(os.environ.get("PINEFORGE_COMPILE_SHARDS", "1")),
        "generator": {"exitCode": None, "stderr": None},
        "producer": None, "cases": [],
        "consumer": {"status": "INCOMPLETE", "reasons": ["generation has not completed"]},
    }

    def retain():
        reasons = report["consumer"]["reasons"]
        for row in report["cases"]:
            if row["translation"]["outcome"] is None or (
                row["group"] != "invalid_control" and row["compile"]["outcome"] is None
            ):
                reasons.append(f"{row['id']}: missing result or unexecuted compile")
        report["counts"] = {
            group: sum(row["group"] == group for row in report["cases"])
            for group in ("ordinary", "community_seed", "invalid_control")
        }
        (_sweep_report_dir(tmp_path_factory) / "generated-matrix-results.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8",
        )

    # Retain incomplete evidence even if generator setup, admission or native setup fails.
    request.addfinalizer(retain)
    try:
        generation = subprocess.run(
            [
                sys.executable, str(ROOT / MATRIX_INPUT_PINS["generator"][0]),
                "--spec", str(ROOT / MATRIX_INPUT_PINS["spec"][0]),
                "--output-dir", str(output),
            ],
            cwd=ROOT, capture_output=True, text=True,
        )
    except OSError as error:
        report["consumer"]["reasons"] = [f"generator unavailable: {error}"]
        pytest.fail(f"INCOMPLETE: matrix generator unavailable: {error}", pytrace=False)
    report["generator"] = {"exitCode": generation.returncode, "stderr": generation.stderr}
    try:
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        report["consumer"]["reasons"] = [f"manifest unavailable: {error}"]
        pytest.fail(
            f"INCOMPLETE: matrix generator exit={generation.returncode}; "
            f"manifest unavailable: {error}\n{generation.stderr}", pytrace=False,
        )
    problems = check_matrix_manifest(manifest, output)
    selected = controls = []
    if isinstance(manifest, dict):
        coverage = manifest.get("coverage")
        producer_results = manifest.get("results")
        report["producer"] = {
            "generation": manifest.get("generation"),
            "coverage": ({
                key: value for key, value in manifest.get("coverage", {}).items()
                if key == "status" or key.endswith("Status") or key == "positiveCompileCoverage"
            } if isinstance(coverage, dict) else coverage),
            "results": {
                "status": producer_results.get("status") if isinstance(producer_results, dict) else None,
            },
            "consumerContract": manifest.get("consumerContract"),
        }
        groups, selected, controls = _matrix_selection(manifest)
        report["matrixCounts"] = {group: len(cases) for group, cases in groups.items()}
        # Keep unavailable sources and unexecuted cases visible even on admission failure.
        for case in controls + selected + groups["unclassified"]:
            report["cases"].append({
                "id": case.get("id"), "sourceSha256": case.get("sourceSha256"),
                "group": (
                    "unclassified" if case in groups["unclassified"] else _matrix_group(case)
                ),
                "shard": report["shard"], "shards": report["shards"],
                "translation": _matrix_phase(), "compile": _matrix_phase(),
                "diagnostics": [], "failure": None,
            })
    report["consumer"]["reasons"] = list(problems)
    if generation.returncode != 0:
        reason = f"matrix generator exit={generation.returncode}\n{generation.stderr}"
        report["consumer"]["reasons"].append(reason)
        pytest.fail("INCOMPLETE: " + reason, pytrace=False)
    if problems:
        pytest.fail("\n".join(problems), pytrace=False)
    for field in ("status", "inventoryStatus"):
        value = manifest["coverage"][field]
        if value != "COMPLETE":
            report["consumer"]["reasons"].append(f"producer coverage.{field}={value}")
    # The public compile helper does not expose all native identities or raw success receipts.
    report["consumer"]["reasons"].append(
        "native command/version, input identities and raw exit receipts are unavailable "
        "through the public compile helper; no complete verification is claimed"
    )
    report["nativeReceipts"] = {
        key: None for key in (
            "codegenIdentity", "engineIdentity", "compilerIdentity", "headersIdentity",
            "eigenIdentity", "generatedHeadersIdentity",
        )
    }
    return {
        "manifest": manifest, "output": output, "report": report,
        "positives": selected, "controls": controls,
        "rows": {row["id"]: row for row in report["cases"]},
    }


def _matrix_translate(case, generated_matrix):
    row = generated_matrix["rows"][case["id"]]
    try:
        source = _matrix_source(generated_matrix["output"], case["sourcePath"]).read_bytes()
        if hashlib.sha256(source).hexdigest() != case["sourceSha256"]:
            raise ValueError("sourceSha256 differs from source bytes before translation")
        cpp = transpile(source.decode("utf-8"), filename=case["id"])
    except CompileError as error:
        row["diagnostics"] = [
            {
                "code": diagnostic.code, "phase": diagnostic.phase.value.lower(),
                "message": diagnostic.message, "hint": diagnostic.hint,
            }
            for diagnostic in error.diagnostics
        ]
        row["translation"] = _matrix_phase(
            "REJECTED", (str(error).splitlines() or [type(error).__name__])[0],
            diagnostics=str(error),
        )
        return None
    except Exception as error:
        row["translation"] = _matrix_phase(
            "ERROR", f"{type(error).__name__}: {(str(error).splitlines() or [''])[0]}",
            diagnostics=str(error),
        )
        return None
    row["translation"] = _matrix_phase("SUCCEEDED")
    return cpp


@pytest.fixture(scope="module")
def generated_matrix_controls(generated_matrix):
    for case in generated_matrix["controls"]:
        row = generated_matrix["rows"][case["id"]]
        _matrix_translate(case, generated_matrix)
        diagnostics = row["diagnostics"]
        if (
            row["translation"]["outcome"] != "REJECTED" or not diagnostics
            or diagnostics[0]["code"] != case["expectedDiagnosticCode"]
            or diagnostics[0]["phase"] != case["expectedDiagnosticPhase"]
        ):
            row["failure"] = (
                f"{case['id']}: expected {case['expectedDiagnosticCode']} "
                f"phase={case['expectedDiagnosticPhase']}; "
                f"got {row['translation']['outcome']} {diagnostics} "
                f"{row['translation']['firstDiagnostic']}"
            )
            generated_matrix["report"]["consumer"]["reasons"].append(row["failure"])
            # The producer contract stops execution at the first failed invalid control.
            pytest.fail(row["failure"], pytrace=False)
    return generated_matrix


def test_generated_matrix_invalid_controls(generated_matrix_controls):
    assert all(
        row["translation"]["outcome"] == "REJECTED"
        for row in generated_matrix_controls["report"]["cases"]
        if row["group"] == "invalid_control"
    )


@pytest.mark.parametrize("family", ["ordinary", "community_seed"])
def test_generated_matrix_positive_translates_and_compiles(
    family, generated_matrix_controls, sweep_results,
):
    matrix = generated_matrix_controls
    failures = []
    for case in matrix["positives"]:
        if case["renderFamily"] != family:
            continue
        row = matrix["rows"][case["id"]]
        cpp = _matrix_translate(case, matrix)
        if cpp is not None:
            try:
                compile_cpp(cpp, label=case["id"], extra_flags=STRATEGY_FP_FLAGS)
            except AssertionError as error:
                row["compile"] = _matrix_phase(
                    "FAILED", _first_error(error),
                    diagnostics=getattr(error, "diagnostics", str(error)),
                )
            except (pytest.skip.Exception, pytest.fail.Exception, Exception) as error:
                row["compile"] = _matrix_phase(
                    "INCOMPLETE", f"{type(error).__name__}: {error}", diagnostics=str(error),
                )
            else:
                row["compile"] = _matrix_phase("SUCCEEDED")
        if row["translation"]["outcome"] != "SUCCEEDED" or row["compile"]["outcome"] != "SUCCEEDED":
            row["failure"] = (
                f"{case['id']}: translation={row['translation']['outcome']} "
                f"{row['translation']['firstDiagnostic']}; "
                f"compile={row['compile']['outcome']} {row['compile']['firstDiagnostic']}"
            )
            failures.append(row["failure"])
    matrix["report"]["consumer"]["reasons"].extend(failures)
    assert not failures, "\n".join(failures)


def test_generated_matrix_manifest_is_compatible(generated_matrix):
    assert check_matrix_manifest(generated_matrix["manifest"], generated_matrix["output"]) == []


def test_generated_matrix_shards_cover_every_positive_once(generated_matrix, monkeypatch):
    manifest = generated_matrix["manifest"]
    expected = sorted(
        case["id"] for case in manifest["cases"]
        if _matrix_group(case) in {"ordinary", "community_seed"}
    )
    collected = []
    monkeypatch.setenv("PINEFORGE_COMPILE_SHARDS", "4")
    for index in range(4):
        monkeypatch.setenv("PINEFORGE_COMPILE_SHARD", str(index))
        _, positives, controls = _matrix_selection(manifest)
        ids = [case["id"] for case in positives]
        assert ids == expected[index::4]
        assert all(_matrix_group(case) == "invalid_control" for case in controls)
        collected.extend(ids)
    assert sorted(collected) == expected
    assert len(set(collected)) == len(collected)


@pytest.mark.parametrize("mutation,expected_problem", [
    ("old-generator", "generatorVersion must be"),
    ("string-schema", "schemaVersion must be"),
    ("boolean-schema", "schemaVersion must be"),
    ("deleted-pin", "generatorSourceHash pin mismatch"),
    ("changed-pin", "inputIdentities.seedIndex pin mismatch"),
    ("changed-source-hash", "sourceSha256 differs from source bytes"),
    ("deleted-case", "coverage.totalEmittedCount does not match"),
    ("changed-group-count", "coverage.positiveUnionCount does not match"),
])
def test_generated_matrix_manifest_mutations(generated_matrix, mutation, expected_problem):
    manifest = copy.deepcopy(generated_matrix["manifest"])
    if mutation == "old-generator":
        manifest["generatorVersion"] = "2"
    elif mutation == "string-schema":
        manifest["schemaVersion"] = "1"
    elif mutation == "boolean-schema":
        manifest["schemaVersion"] = True
    elif mutation == "deleted-pin":
        del manifest["generatorSourceHash"]
    elif mutation == "changed-pin":
        manifest["inputIdentities"]["seedIndex"] = "0" * 64
    elif mutation == "changed-source-hash":
        manifest["cases"][0]["sourceSha256"] = "0" * 64
    elif mutation == "deleted-case":
        manifest["cases"].pop()
    elif mutation == "changed-group-count":
        manifest["coverage"]["positiveUnionCount"] += 1
    problems = check_matrix_manifest(manifest, generated_matrix["output"])
    assert any(expected_problem in problem for problem in problems), problems
