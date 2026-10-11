"""``test_parse_basic_strategy`` finds the engine's corpus from the engine checkout.

It used to glob ``pineforge-engine/corpus/basic/*/strategy.pine`` against the
process's working directory, so a run started anywhere but a directory holding a
``pineforge-engine`` checkout collected no case, and pytest reported the empty
parameter set as one skip (the 1.5.0 proof's
``tests/test_parser.py::test_parse_basic_strategy[NOTSET]`` row). It now takes the
corpus from the engine checkout the suite already resolves
(``tests/_e2e.py:engine_root()``: ``PINEFORGE_ENGINE_INCLUDE``, else a sibling
checkout), and an engine checkout that holds no strategy is a failure.

Driven from outside, the way a verification kit runs it: pytest itself, in a
subprocess started from different working directories against a throwaway
engine checkout, so every outcome is read off pytest's own report. The
checkout is only what ``tests/_compile.py`` accepts as one
(``include/pineforge/engine.hpp``) plus the strategies under ``corpus/``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_PARSER = REPO_ROOT / "tests" / "test_parser.py"
TEST_NAME = "test_parse_basic_strategy"
# The public corpus keeps every verified strategy under corpus/validation/; the
# corpus/basic/ of the private-era layout is not in the paired engine.
BUCKET = "validation"

GOOD = '//@version=6\nstrategy("probe", overlay=true)\nup = close > open\n'
BROKEN = 'strategy("probe"\n'


def _write(path: Path, text: str = GOOD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _engine(base: Path) -> Path:
    """A throwaway engine checkout with no corpus yet."""
    root = base / "pineforge-engine"
    _write(root / "include" / "pineforge" / "engine.hpp", "// stub\n")
    return root


def _corpus(root: Path, names, *, bucket: str = BUCKET, source: str = GOOD) -> None:
    for name in names:
        _write(root / "corpus" / bucket / name / "strategy.pine", source)


def _pytest(*args: str, cwd: Path, include: Path | None) -> subprocess.CompletedProcess:
    """This module's pytest over ``tests/test_parser.py``'s basic-strategy test,
    started in ``cwd`` with ``PINEFORGE_ENGINE_INCLUDE`` = ``include`` (unset for
    None) and nothing else of the outer run's pytest or engine environment."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_")
           and k not in ("PINEFORGE_ENGINE_INCLUDE", "PINEFORGE_ENGINE_CORPUS")}
    if include is not None:
        env["PINEFORGE_ENGINE_INCLUDE"] = str(include)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=",
         "--rootdir", str(REPO_ROOT), str(TEST_PARSER), "-k", TEST_NAME, "-q", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=300)


def _collected(cwd: Path, include: Path) -> list[str]:
    proc = _pytest("--collect-only", cwd=cwd, include=include)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prefix = f"{TEST_NAME}["
    return [line.split(prefix, 1)[1].rstrip("]")
            for line in proc.stdout.splitlines() if prefix in line]


@pytest.mark.parametrize("names", [
    ["solo"],
    ["c-third", "a-first", "b-second"],
    [f"s{i:02d}" for i in range(17, 0, -1)],
], ids=["one", "three", "seventeen"])
def test_collects_exactly_the_engine_corpus_from_any_directory(tmp_path, names):
    engine = _engine(tmp_path / "workspace")
    _corpus(engine, names)
    # Not strategies of the corpus bucket: a directory with no strategy.pine, one
    # level deeper, and another bucket.
    (engine / "corpus" / BUCKET / "no-strategy-here").mkdir()
    _corpus(engine, ["nested-one"], bucket=f"{BUCKET}/group")
    _corpus(engine, ["draft-one"], bucket="draft-probes")
    # A working directory that holds the layout the old glob read, and the
    # corpus bucket's: neither may be taken in the engine's place.
    decoy = tmp_path / "decoy"
    _corpus(decoy / "pineforge-engine", ["decoy-basic"], bucket="basic")
    _corpus(decoy / "pineforge-engine", ["decoy-bucket"])
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    expected = sorted(names)
    for cwd in (REPO_ROOT, unrelated, decoy):
        assert _collected(cwd, engine / "include") == expected, f"started in {cwd}"


def test_every_collected_case_parses_its_strategy(tmp_path):
    engine = _engine(tmp_path)
    _corpus(engine, ["good-a", "good-b", "good-c"])
    proc = _pytest(cwd=tmp_path, include=engine / "include")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3 passed" in proc.stdout

    _corpus(engine, ["broken-one"], source=BROKEN)
    proc = _pytest("-rf", cwd=tmp_path, include=engine / "include")
    assert proc.returncode == 1
    assert "1 failed, 3 passed" in proc.stdout
    assert f"{TEST_NAME}[broken-one]" in proc.stdout


@pytest.mark.parametrize("layout", [
    "no corpus directory",
    "corpus directory with nothing in it",
    "corpus bucket with nothing in it",
    "corpus bucket whose directories hold no strategy.pine",
    "strategies in another bucket only",
])
def test_engine_without_corpus_strategies_fails_naming_the_path(tmp_path, layout):
    engine = _engine(tmp_path)
    corpus = engine / "corpus"
    if layout != "no corpus directory":
        corpus.mkdir()
    if layout == "corpus bucket with nothing in it":
        (corpus / BUCKET).mkdir()
    if layout == "corpus bucket whose directories hold no strategy.pine":
        _write(corpus / BUCKET / "probe-01" / "engine_trades.csv", "a,b\n")
    if layout == "strategies in another bucket only":
        _corpus(engine, ["draft-one"], bucket="draft-probes")

    proc = _pytest("-rs", cwd=tmp_path, include=engine / "include")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "1 failed" in proc.stdout
    assert "passed" not in proc.stdout and "skipped" not in proc.stdout
    assert str((corpus / BUCKET).resolve()) in proc.stdout


@pytest.mark.parametrize("include", [
    "an include directory that does not exist",
    "a directory that is no engine include",
])
def test_no_engine_checkout_is_one_visible_skip_with_a_reason(tmp_path, include):
    # tests/_compile.py reads a set PINEFORGE_ENGINE_INCLUDE that names no engine
    # include as no engine at all, as an unset one beside no sibling checkout.
    path = tmp_path / "missing" / "include"
    if include.startswith("a directory"):
        path.mkdir(parents=True)
    proc = _pytest("-rs", cwd=tmp_path, include=path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 skipped" in proc.stdout
    assert "failed" not in proc.stdout and "passed" not in proc.stdout
    assert "PINEFORGE_ENGINE_INCLUDE" in proc.stdout
