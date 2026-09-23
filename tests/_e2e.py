"""Shared harness of the end-to-end tests: every case is driven from outside,
through the interfaces a client uses.

1. transpile: the ``transpile_json`` contract of pineforge-app's Pyodide glue
   (``gate/glue.py`` is its canonical copy), run as a subprocess. Its JSON
   carries the C++, the diagnostics the app shows, and the input manifest
   the app builds its override form from.
2. compile: the emitted TU as a strategy shared library linked against the
   built engine runtime (the engine's ``corpus/CMakeLists.txt`` recipe).
3. run: the engine's ``scripts/run_strategy.py`` C-ABI harness over the
   corpus's real ETH-USDT 1m feed, resampled to the 15m chart feed by the
   engine's own ``derive_corpus_feeds`` resampler into a temp dir (never
   into the engine checkout). Input overrides travel through
   ``inputs.json`` -> ``strategy_set_input``, keyed by the manifest title;
   per-bar values come back through ``--trace-json`` (``@pf-trace``), and
   the run's stderr -- where the engine writes ``log.*`` lines -- is kept.

Needs PINEFORGE_ENGINE_INCLUDE (+ Eigen) and a built runtime
(PINEFORGE_ENGINE_LIB), with the engine checkout's ``scripts/`` and corpus
feed beside the include dir. ``skip_unless_e2e_env`` skips cleanly otherwise,
like every compile test.
"""

from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests import _compile as compile_env


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

def engine_root() -> Path | None:
    inc = compile_env._ENGINE_INC
    return inc.parent if inc is not None else None


def skip_unless_e2e_env() -> Path:
    compile_env.skip_if_no_engine_lib()
    root = engine_root()
    assert root is not None
    if not (root / "scripts" / "run_strategy.py").is_file():
        pytest.skip(f"engine checkout {root} has no scripts/run_strategy.py")
    feed = root / "corpus" / "data" / "ohlcv_ETH-USDT-USDT_1m.csv"
    if not feed.is_file() or feed.stat().st_size < 1_000_000:
        pytest.skip(f"corpus 1m feed missing or an unsmudged LFS pointer: {feed}")
    return root


def derive_chart_feed(engine_root: Path, out: Path) -> Path:
    """The corpus's 15m chart feed, via the engine's own resampler."""
    spec = importlib.util.spec_from_file_location(
        "_pf_derive_corpus_feeds", engine_root / "scripts" / "derive_corpus_feeds.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with mod.SOURCE_1M.open() as fh:
        next(fh)
        buckets = mod._resample_15m(fh)
    out.write_text("\n".join([mod.HEADER, *map(mod._format_bucket, buckets)]) + "\n")
    return out


_GLUE_MAIN = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import glue\n"
    "sys.stdout.write(glue.transpile_json(open(sys.argv[2], encoding='utf-8').read()))\n"
)


def transpile_json(pine: Path, root: Path = REPO_ROOT) -> dict:
    """``transpile_json`` of the glue and codegen under ``root`` (this checkout
    by default, or a ``reference_codegen`` tree)."""
    env = dict(os.environ, PYTHONPATH=str(root))
    proc = subprocess.run(
        [sys.executable, "-c", _GLUE_MAIN, str(root / "gate"), str(pine)],
        capture_output=True, text=True, timeout=300, env=env, cwd=root)
    if proc.returncode != 0:
        raise RuntimeError(f"transpile_json crashed on {pine}:\n{proc.stderr}")
    return json.loads(proc.stdout)


_REFERENCE_TREES: dict[str, Path | None] = {}


def reference_codegen(commit: str) -> Path | None:
    """This repository's ``pineforge_codegen`` and ``gate`` as of ``commit``
    (``git archive``), extracted once per session, to transpile a script the
    way that commit did; None when git or the commit is unavailable."""
    if commit not in _REFERENCE_TREES:
        tree = None
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", commit,
             "pineforge_codegen", "gate"], capture_output=True, timeout=120)
        if proc.returncode == 0:
            tree = Path(tempfile.mkdtemp(prefix=f"pf-codegen-{commit[:12]}-"))
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(tree, filter="data")
                else:
                    tar.extractall(tree)
        _REFERENCE_TREES[commit] = tree
    return _REFERENCE_TREES[commit]


def build_strategy_library(cpp: str, workdir: Path) -> None:
    (workdir / "generated.cpp").write_text(cpp)
    lib = str(compile_env._ENGINE_LIB)
    if sys.platform == "darwin":
        link = [f"-Wl,-force_load,{lib}"]
    else:
        link = ["-Wl,--whole-archive", lib, "-Wl,--no-whole-archive"]
    cmd = [compile_env._COMPILER, "-std=c++17", "-O2", *compile_env.STRATEGY_FP_FLAGS,
           "-fPIC", "-shared", *compile_env._include_flags(isolate_headers=True),
           str(workdir / "generated.cpp"), *link, "-o", str(workdir / "strategy.so")]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"compile failed in {workdir}:\n"
                           + "\n".join(proc.stderr.splitlines()[:40]))


def run_strategy(engine_root: Path, workdir: Path, feed: Path,
                 overrides: dict | None, tag: str, trace: bool = False
                 ) -> tuple[bytes, list[dict] | None, str]:
    """The run's ``engine_trades.csv`` bytes, its trace records when
    ``trace`` asks for them, and its stderr."""
    out = workdir / f"engine_trades_{tag}.csv"
    cmd = [sys.executable, str(engine_root / "scripts" / "run_strategy.py"),
           str(workdir), "--ohlcv", str(feed), "--no-trim-output", "-o", str(out)]
    if overrides is not None:
        inputs = workdir / f"inputs_{tag}.json"
        inputs.write_text(json.dumps(overrides))
        cmd += ["--inputs-json", str(inputs)]
    trace_path = workdir / f"trace_{tag}.json"
    if trace:
        cmd += ["--trace-json", str(trace_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"run_strategy failed in {workdir}:\n{proc.stdout}\n{proc.stderr}")
    records = json.loads(trace_path.read_text())["trace"] if trace else None
    return out.read_bytes(), records, proc.stderr


def trade_count(trades_csv: bytes) -> int:
    rows = csv.DictReader(trades_csv.decode().splitlines())
    return len({row["Trade #"] for row in rows})


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def summary(blob: bytes) -> str:
    return f"{trade_count(blob)} trades sha256 {digest(blob)[:16]}"


# ---------------------------------------------------------------------------
# Execution: a test module collects every build its selected tests need,
# transpiles, compiles and runs them in parallel once per session, and each
# test then judges its own case.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Build:
    """One strategy source and the input overrides it runs under. Every build
    runs at its defaults; ``overrides`` adds a second run under them.
    ``codegen`` transpiles it with another codegen tree (``reference_codegen``)."""
    source: str
    overrides: dict | None = None
    trace: bool = False
    codegen: Path | None = None


@dataclass
class Outcome:
    build: Build
    transpiled: dict | None = field(default=None, repr=False)
    error: str | None = None
    trades: dict[str, bytes] = field(default_factory=dict, repr=False)
    traces: dict[str, list[dict]] = field(default_factory=dict, repr=False)
    logs: dict[str, str] = field(default_factory=dict, repr=False)


def execute(engine_root: Path, feed: Path, workdir: Path, build: Build) -> Outcome:
    outcome = Outcome(build=build)
    workdir.mkdir(parents=True, exist_ok=True)
    pine = workdir / "strategy.pine"
    pine.write_text(build.source, encoding="utf-8")
    try:
        outcome.transpiled = transpile_json(pine, build.codegen or REPO_ROOT)
        if not outcome.transpiled.get("ok"):
            outcome.error = "transpile_json refused it:\n" + json.dumps(
                outcome.transpiled.get("diagnostics"), indent=1, ensure_ascii=False)
            return outcome
        build_strategy_library(outcome.transpiled["cpp"], workdir)
        runs = [("default", None)]
        if build.overrides is not None:
            runs.append(("override", build.overrides))
        for tag, overrides in runs:
            trades, records, stderr = run_strategy(engine_root, workdir, feed, overrides,
                                                   tag, build.trace)
            outcome.trades[tag] = trades
            outcome.logs[tag] = stderr
            if records is not None:
                outcome.traces[tag] = records
    except Exception as exc:  # recorded and asserted by the case's own test
        outcome.error = str(exc)
    return outcome


def execute_all(engine_root: Path, feed: Path, base: Path,
                builds: dict[str, Build]) -> dict[str, Outcome]:
    """Run every build, keyed by its workdir below ``base``."""
    workers = max(2, min(8, (os.cpu_count() or 4) // 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {key: pool.submit(execute, engine_root, feed, base / key, b)
                for key, b in builds.items()}
        return {key: job.result() for key, job in jobs.items()}


def ok(outcomes: dict[str, Outcome], key: str) -> Outcome:
    """The outcome of ``key``; fails the calling test when it did not run."""
    outcome = outcomes[key]
    if outcome.error is not None:
        pytest.fail(f"[{key}] {outcome.error}\n--- source ---\n{outcome.build.source}",
                    pytrace=False)
    return outcome


def same(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or a == b


def per_bar_mismatches(subject: list[dict], reference: list[dict],
                       labels: tuple[str, str] = ("subject", "reference")
                       ) -> tuple[int, int, str | None]:
    """``(bars compared, mismatching bars, first mismatch)`` over the traced
    series both runs share, matched by trace name and bar."""
    by_name: dict[str, list[dict]] = {}
    for rec in reference:
        by_name.setdefault(rec["name"], []).append(rec)
    compared = mismatched = 0
    first = None
    positions: dict[str, int] = {}
    for rec in subject:
        name = rec["name"]
        i = positions.get(name, 0)
        positions[name] = i + 1
        refs = by_name.get(name, [])
        ref = refs[i] if i < len(refs) else None
        compared += 1
        if (ref is None or ref["timestamp"] != rec["timestamp"]
                or not same(rec["value"], ref["value"])):
            mismatched += 1
            if first is None:
                first = (f"{name} at bar {rec['bar_index']} (ts {rec['timestamp']}): "
                         f"{labels[0]} {rec['value']!r} vs {labels[1]} "
                         f"{None if ref is None else ref['value']!r}")
    if sum(len(v) for v in by_name.values()) != compared:
        mismatched += 1
        first = first or "the two runs traced different numbers of bars"
    return compared, mismatched, first
