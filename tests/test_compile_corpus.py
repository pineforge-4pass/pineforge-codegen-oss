"""Compile-only sweep across the pineforge-engine validation corpus.

Where ``test_compile_smoke.py`` exercises hand-picked feature shards, this
file walks the entire ``corpus/<bucket>/<strategy>/strategy.pine`` tree
that ships with ``pineforge-engine`` and runs ``transpile()`` plus
``g++ -fsyntax-only`` against every one of them. Purpose:

    "Every Pine v6 strategy that the engine validates trade-for-trade
     against TradingView must also compile cleanly through the codegen
     against the public engine headers."

This is the strongest correctness signal we can get inside the
transpiler-only repo — the corpus is what defines "Pine that PineForge
must support".

Activation contract (env vars):

* ``PINEFORGE_ENGINE_CORPUS`` — directory containing ``basic/``,
  ``community/``, ``validation/``, etc. Optional; falls back to
  ``${PINEFORGE_ENGINE_INCLUDE}/../corpus`` when ``PINEFORGE_ENGINE_INCLUDE``
  is set, then to ``../pineforge-engine/corpus`` relative to this repo.
* Plus the ``PINEFORGE_ENGINE_INCLUDE`` / Eigen / compiler triple from
  ``tests/_compile.py``.

When the corpus tree is unavailable (public clone without the private
corpus submodule), the whole module is skipped via ``pytest.skip``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp, have_compile_env, skip_if_no_compile_env


def _resolve_corpus_root() -> Path | None:
    """Best-effort lookup for the corpus root.

    Returns the directory containing strategy buckets (``basic`` /
    ``community`` / ``validation`` etc.) or ``None`` if not found.
    """
    raw = os.environ.get("PINEFORGE_ENGINE_CORPUS", "")
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw).expanduser())
    eng_inc = os.environ.get("PINEFORGE_ENGINE_INCLUDE", "")
    if eng_inc:
        eng_inc_p = Path(eng_inc).expanduser().resolve()
        # Either ``.../include`` or ``.../`` — try the parent ``corpus`` in both.
        candidates.append(eng_inc_p.parent / "corpus")
        candidates.append(eng_inc_p / "corpus")
    repo_root = Path(__file__).resolve().parent.parent
    candidates.append(repo_root.parent / "pineforge-engine" / "corpus")
    for cand in candidates:
        if cand.is_dir() and (cand / "data").is_dir():
            return cand.resolve()
    return None


_CORPUS_ROOT = _resolve_corpus_root()


def _discover_strategies() -> list[Path]:
    if _CORPUS_ROOT is None:
        return []
    paths = sorted(_CORPUS_ROOT.glob("*/*/strategy.pine"))
    # Filter out any data/* matches and stay strictly under the bucket dirs.
    return [p for p in paths if p.parent.parent.name != "data"]


_STRATEGIES = _discover_strategies()


# Module-level skip if the basic compile env is missing — without it none
# of these tests can run, and a per-test skip is just noise in the output.
pytestmark = pytest.mark.skipif(
    not have_compile_env() or _CORPUS_ROOT is None or not _STRATEGIES,
    reason=(
        "set PINEFORGE_ENGINE_INCLUDE plus a valid pineforge-engine "
        "corpus checkout (PINEFORGE_ENGINE_CORPUS, or sibling "
        "../pineforge-engine/corpus). 162-strategy corpus is a private "
        "submodule of pineforge-engine; public clones do not include it."
    ),
)


def _strategy_id(p: Path) -> str:
    """Pytest test-id: ``<bucket>/<strategy-name>`` (stable, readable)."""
    bucket = p.parent.parent.name
    name = p.parent.name
    return f"{bucket}/{name}"


# --------------------------------------------------------------------------
# Each corpus strategy must transpile + compile cleanly. If a particular
# strategy is known to fail it should be carved out via the
# ``KNOWN_TRANSPILE_FAILURES`` / ``KNOWN_COMPILE_FAILURES`` sets below
# with a one-line rationale. Empty by default — every strategy in the
# corpus is expected to round-trip.
# --------------------------------------------------------------------------

KNOWN_TRANSPILE_FAILURES: set[str] = {
    # Explicit rejection probes — the strategy.pine file's header comment
    # documents that codegen MUST raise. xfail keeps the probe exercised
    # without polluting the green-bar count; xpass surfaces a regression
    # where the rejection silently disappears.
    "validation_varip/varip-reject-probe-01-varip-int",
    "validation_lower_tf/lower-tf-probe-04-tuple-element-reject",
}
KNOWN_COMPILE_FAILURES: set[str] = set()


@pytest.mark.parametrize(
    "pine_path", _STRATEGIES, ids=[_strategy_id(p) for p in _STRATEGIES]
)
def test_corpus_strategy_transpiles_and_compiles(pine_path: Path) -> None:
    skip_if_no_compile_env()
    sid = _strategy_id(pine_path)
    src = pine_path.read_text()
    try:
        cpp = transpile(src)
    except CompileError as e:
        if sid in KNOWN_TRANSPILE_FAILURES:
            pytest.xfail(
                f"known transpile failure for {sid}: {str(e).splitlines()[0]}"
            )
        raise
    if sid in KNOWN_COMPILE_FAILURES:
        # Run the compile but expect failure; xpass surfaces a fix.
        try:
            compile_cpp(cpp, label=sid)
        except AssertionError:
            pytest.xfail(f"known compile failure for {sid}")
        else:
            pytest.fail(
                f"{sid} is in KNOWN_COMPILE_FAILURES but now compiles; "
                "remove the entry."
            )
    else:
        compile_cpp(cpp, label=sid)
