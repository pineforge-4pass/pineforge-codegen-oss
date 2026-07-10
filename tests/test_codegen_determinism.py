"""Regression: transpiler output must be byte-identical across PYTHONHASHSEED.

Background
---------
``CodeGen.__init__`` once iterated several Python ``set`` collections whose
iteration order is SipHash-randomized by ``PYTHONHASHSEED``. That order flowed
into the emitted C++ member declarations for *call-site-cloned* function-scoped
series/var members (``orig_names`` -> ``func_var_originals`` ->
``_func_cs_var_remap``). The same input therefore transpiled to byte-different
C++ depending on the (random) hash seed of the interpreter. Fixed in
``fix(codegen): deterministic member-clone ordering`` by making those
collections ordered + de-duplicated and ``sorted()``-ing the set-valued
``func_series_vars`` at the two base.py consumption points.

This test locks that fix so it cannot silently regress.

Why subprocesses
----------------
``PYTHONHASHSEED`` is read once, at interpreter startup; the seed of the
*already-running* pytest process is fixed and cannot be changed in-process.
To probe multiple seeds we must spawn fresh ``python3`` children, each with a
different ``PYTHONHASHSEED`` in its environment, and compare their stdout.

The fixture exercises the exact path that varied pre-fix: a multi-call-site
function (``calc``, 4 sites) whose body uses its own local history-accessed
series vars (``m``/``n``/``c``) *and* pulls series from a sub-function
(``sub``, which also has its own series vars). That makes the codegen clone
function-scoped series members per call site and union series across the
parent + sub-function -- the ``all_func_scoped_series`` union the fix repaired.

Self-contained: pure transpile, no engine headers, no compiler, no network --
so it runs in default CI. (Verified locally that it produces 1 distinct hash
on the fixed tree and >1 on pre-fix ``origin/main``.)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Repo root = parent of this tests/ directory. Threaded into the child via
# PYTHONPATH so ``import pineforge_codegen`` resolves regardless of how/where
# pytest was launched.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# A strategy that drives the cloned-member path. ``calc`` is called from 4
# distinct sites (-> call-site cloning), uses its own local series vars, and
# pulls a series value from ``sub`` (which has its own series vars too). This
# is the shape that produced byte-different C++ across hash seeds pre-fix.
FIXTURE = """//@version=6
strategy("Determinism Fixture", overlay=true)

// Sub-function with its OWN history-accessed local series vars. Its series
// vars must be cloned into the parent's per-call-site members, exercising the
// parent <- sub-function series union the fix made deterministic.
sub(float x) =>
    a = ta.sma(x, 5)
    b = ta.ema(x, 8)
    c = a - b
    d = c[1] + c[2]
    a + b + c + d

// Parent: own local series vars (m, n) PLUS a pull from sub(). Called from 4
// sites below -> call-site cloning (cs1..cs3) clones these function-scoped
// series members, whose declaration order is what regressed across seeds.
calc(float src, int len) =>
    m = ta.sma(src, len)
    n = ta.rma(src, len)
    p = m[1] - n[2]
    q = sub(src)
    r = p + q + m[3] + n[4]
    r

s1 = calc(close, 10)
s2 = calc(open, 14)
s3 = calc(high, 20)
s4 = calc(low, 7)

plot(s1 + s2 + s3 + s4)
"""

# Child program: read the fixture on stdin, transpile, write the C++ to stdout
# verbatim. Any exception propagates as a non-zero exit + traceback on stderr.
_CHILD = (
    "import sys\n"
    "from pineforge_codegen import transpile\n"
    "sys.stdout.write(transpile(sys.stdin.read()))\n"
)

# Spread across the SipHash seed space: 0 disables randomization entirely;
# the rest are arbitrary fixed seeds. Pre-fix, this set yields >1 distinct
# output; post-fix it must yield exactly 1.
_SEEDS = [0, 1, 2, 7, 12345]


def _transpile_under_seed(seed: int, fixture: str = FIXTURE) -> str:
    """Transpile FIXTURE in a fresh interpreter pinned to ``PYTHONHASHSEED=seed``."""
    env = {
        **os.environ,
        "PYTHONHASHSEED": str(seed),
        # Prepend repo root so the child imports the in-tree package even if
        # an installed copy exists elsewhere on the path.
        "PYTHONPATH": os.pathsep.join(
            [str(_REPO_ROOT), os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep),
    }
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        input=fixture,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"transpile subprocess failed under PYTHONHASHSEED={seed} "
        f"(exit={proc.returncode}).\n--- stderr (tail) ---\n"
        + "\n".join((proc.stderr or "").splitlines()[-40:])
    )
    assert proc.stdout, f"transpile produced empty output under PYTHONHASHSEED={seed}"
    return proc.stdout


def test_transpile_byte_identical_across_hash_seeds() -> None:
    """All seeds must yield byte-identical C++ (locks the member-clone fix)."""
    outputs = [_transpile_under_seed(seed) for seed in _SEEDS]
    distinct = set(outputs)
    assert len(distinct) == 1, (
        "Transpiler output is NOT deterministic across PYTHONHASHSEED: "
        f"{len(distinct)} distinct outputs across seeds {_SEEDS}. "
        "This is the hash-seed member-clone-ordering regression "
        "(see fix(codegen): deterministic member-clone ordering). "
        "Likely a set is being iterated into emitted C++ again."
    )


_SYNTHETIC_HISTORY_FIXTURE = """//@version=6
strategy("Synthetic Determinism", calc_on_order_fills=true)
type Box
    float bias
passthrough(float src) => src
history_arg(float src) => src[1]
leaf(float src) => history_arg(src + 1.0)
left(float src) => leaf(src)
right(float src) => leaf(src)
wrapped(float src, int mode) =>
    switch mode
        1 => passthrough(src)[1]
        => src
method measure(Box self, float src) =>
    call_prev = passthrough(src + self.bias)[1]
    arg_prev = history_arg(src - self.bias)
    call_prev + arg_prev
var Box bx = Box.new(1.0)
a = left(close)
b = right(open)
c = wrapped(close, 1)
d = wrapped(open, 1)
e = bx.measure(close)
f = bx.measure(open)
"""


def test_synthetic_history_names_byte_identical_across_hash_seeds() -> None:
    outputs = [
        _transpile_under_seed(seed, _SYNTHETIC_HISTORY_FIXTURE)
        for seed in _SEEDS
    ]
    assert len(set(outputs)) == 1
