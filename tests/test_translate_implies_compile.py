"""Compile every fixture that translates; retain all compiler failures.

Known front-end and compiler defects have strict, exception-specific xfails. Run with
``--runxfail -k issue_281`` for red-first evidence against unfixed codegen.
Reports include known compile failures as well as every unexpected compiler failure.
"""

from __future__ import annotations

import json
import os
import re
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


@pytest.fixture(scope="module")
def sweep_results(tmp_path_factory):
    # Setup failures happen outside all xfail-marked test bodies.
    pairing = _pairing()
    skip_if_no_compile_env()
    # An incompatible header tree or missing generated header must fail setup,
    # before the issue #281 xfail markers can apply.
    compile_cpp(
        transpile('//@version=6\nstrategy("compile sweep preflight")\n'),
        label="fixture-sweep-preflight", extra_flags=STRATEGY_FP_FLAGS,
    )
    started = time.monotonic()
    results: list[Result] = []
    yield results
    configured = os.environ.get("PINEFORGE_COMPILE_REPORT_DIR")
    destination = _checked_path(
        Path(configured) if configured else tmp_path_factory.mktemp("translate-implies-compile")
    )
    destination.mkdir(parents=True, exist_ok=True)
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
        f"PINEFORGE_CORPUS_SHA={pairing['corpus_sha']}\n",
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
    pytest.param(
        path, id=path.stem,
        marks=(
            pytest.mark.xfail(
                strict=True,
                reason=(
                    "issue #281 case 1: the front end rejects a valid script (CompileError), "
                    "not a compile failure"
                ),
                raises=CompileError,
            )
            if path == ISSUE_281[0]
            else pytest.mark.xfail(
                strict=True, reason="issue #281", raises=FixtureCompileFailure,
            )
        ),
    )
    for path in ISSUE_281
])
def test_issue_281_translates_and_compiles(pine_path, sweep_results):
    _checked_path(pine_path)
    print(f"Issue #281 source file: {pine_path}", flush=True)
    # The parameter's marker distinguishes front-end and compiler failures.
    cpp = transpile(pine_path.read_text(encoding="utf-8"), filename=_label(pine_path))
    result = _compile(pine_path, cpp)
    sweep_results.append(result)
    if result.outcome != "compiled":
        raise FixtureCompileFailure(f"{result.path}\t{result.diagnostic}")


@pytest.mark.parametrize(("pine_path", "expected_exception"), [
    pytest.param(path, error, id=path.stem)
    for path, error in zip(
        ISSUE_281, (CompileError, FixtureCompileFailure, FixtureCompileFailure),
    )
])
def test_issue_281_xfail_accepts_only_the_expected_exception(pine_path, expected_exception):
    parameters, = (
        mark.args[1] for mark in test_issue_281_translates_and_compiles.pytestmark
        if mark.name == "parametrize"
    )
    parameter, = (parameter for parameter in parameters if parameter.values == (pine_path,))
    xfail, = (mark for mark in parameter.marks if mark.name == "xfail")
    assert xfail.kwargs["strict"] is True
    assert xfail.kwargs["raises"] is expected_exception
    assert xfail.kwargs["reason"] == (
        "issue #281 case 1: the front end rejects a valid script (CompileError), "
        "not a compile failure"
        if expected_exception is CompileError else "issue #281"
    )


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
