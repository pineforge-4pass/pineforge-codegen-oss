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

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase, SourceLocation

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


def test_non_ascii_history_names_are_refused() -> None:
    fixture = (
        '//@version=6\nstrategy("Unicode names")\n'
        "\u03b1(float \u03b2) =>\n"
        "    var float \u03b3 = \u03b2\n"
        "    \u03b3 := nz(\u03b3[1], \u03b2) + 1.0\n"
        "    \u03b3\n"
        "pf_utf8_ceb3 = high\npf_utf8_ceb3_2 = low\n"
        "left = \u03b1(close)\nright = \u03b1(open)\nplot(left + right)\n"
    )
    with pytest.raises(CompileError) as error:
        transpile(fixture, filename="unicode_names.pine")
    # The declaration, body and two calls contain ten non-ASCII identifier characters.
    assert len(error.value.diagnostics) == 10
    expected = [
        # Two header lines put the function's alpha at 3:1.
        ("no viable alternative at character '\u03b1'", 3, 1, 2),
        # "α(float " is eight code points, putting the parameter beta at 3:9.
        ("no viable alternative at character '\u03b2'", 3, 9, 10),
        # Four spaces and "var float " are fourteen characters before gamma.
        ("no viable alternative at character '\u03b3'", 4, 15, 16),
        # "    var float γ = " is eighteen code points before beta at 4:19.
        ("no viable alternative at character '\u03b2'", 4, 19, 20),
        # The assignment's four-space indent puts gamma at 5:5.
        ("no viable alternative at character '\u03b3'", 5, 5, 6),
        # "    γ := nz(" is twelve code points before the history gamma.
        ("no viable alternative at character '\u03b3'", 5, 13, 14),
        # "    γ := nz(γ[1], " is eighteen code points before beta at 5:19.
        ("no viable alternative at character '\u03b2'", 5, 19, 20),
        # The return's four-space indent puts gamma at 6:5.
        ("no viable alternative at character '\u03b3'", 6, 5, 6),
        # Two ASCII declarations occupy lines 7 and 8; "left = " puts alpha at 9:8.
        ("no viable alternative at character '\u03b1'", 9, 8, 9),
        # "right = " is eight characters before the next call's alpha at 10:9.
        ("no viable alternative at character '\u03b1'", 10, 9, 10),
    ]
    assert len(error.value.diagnostics) == len(expected)
    for diagnostic, (message, line, col, end_col) in zip(error.value.diagnostics, expected):
        assert diagnostic.code == "PF-E0044"
        assert diagnostic.message == message
        assert diagnostic.level == Level.ERROR
        assert diagnostic.phase == Phase.LEXER
        assert diagnostic.location == SourceLocation("unicode_names.pine", line, col, end_col)
        assert diagnostic.hint is None


def test_ascii_history_and_encoded_looking_names_are_identical_across_hash_seeds() -> None:
    fixture = (
        '//@version=6\nstrategy("ASCII names")\n'
        "history_calc(float price) =>\n"
        "    var float memory_value = price\n"
        "    memory_value := nz(memory_value[1], price) + 1.0\n"
        "    memory_value\n"
        "pf_utf8_ceb3 = high\npf_utf8_ceb3_2 = low\n"
        "left = history_calc(close)\nright = history_calc(open)\nplot(left + right)\n"
    )
    outputs = [_transpile_under_seed(seed, fixture) for seed in _SEEDS]
    assert all(cpp.isascii() for cpp in outputs)
    # The two call sites retain their separate ASCII history members under every seed.
    assert all("Series<double> memory_value" in cpp for cpp in outputs)
    assert all("Series<double> memory_value_cs1" in cpp for cpp in outputs)
    # These authored names already contain the encoder prefix and keep their spelling.
    assert all("double pf_utf8_ceb3 " in cpp for cpp in outputs)
    assert all("double pf_utf8_ceb3_2 " in cpp for cpp in outputs)
    assert len(set(outputs)) == 1


_PERTURBED_CHILD = """
import builtins
import dataclasses
import enum
import json
import random
import sys

generator = random.Random(int(sys.argv[2]))
retained = []

def perturb():
    for unused in range(generator.randrange(200, 18000)):
        size = generator.randrange(1, 512)
        retained.extend((bytearray(size), [None] * (size // 8), object()))

def encode(value):
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    raise TypeError(type(value).__name__)

perturb()
original_id = builtins.id
identity_records = {}
direction = int(sys.argv[3])

def ordered_id(value):
    address = original_id(value)
    if address not in identity_records:
        identity_records[address] = (value, len(identity_records) + 1)
    return direction * identity_records[address][1]

builtins.id = ordered_id
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[1] + "/gate")
from pineforge_codegen import transpile_full
from glue import transpile_json

source = sys.stdin.read()
perturb()
full = json.dumps(transpile_full(source), default=encode)
perturb()
glue = transpile_json(source)
sys.stdout.write(json.dumps({"full": full, "glue": glue}))
"""


@pytest.mark.parametrize("fixture", [
    "taila_nested_bucket.pine",
    "taila_nested_bucket_hull_lag.pine",
])
def test_security_history_envelopes_ignore_memory_layout(fixture: str) -> None:
    """Fixed hash seeds and opposite identity orders exercise both variations."""
    source = (_REPO_ROOT / "tests/fixtures/tail_a_tv" / fixture).read_text()
    outputs = []
    variations = [(seed, direction) for seed in _SEEDS[:4] for direction in (1, -1)]
    for seed, direction in variations:
        process = subprocess.run(
            [sys.executable, "-s", "-P", "-c", _PERTURBED_CHILD,
             str(_REPO_ROOT), str(seed), str(direction)],
            input=source,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
        )
        assert process.returncode == 0, process.stderr
        assert process.stdout
        outputs.append(process.stdout)
    assert len(set(outputs)) == 1


_COLLISION_FIXTURE = """//@version=6
strategy("Deterministic refusal")
scalar_history() =>
    alpha = close
    beta = open
    alpha[1] + beta[1]
persistent_maps() =>
    var map<string, float> alpha = map.new<string, float>()
    var map<string, float> beta = map.new<string, float>()
    alpha.size() + beta.size()
plot(scalar_history() + persistent_maps())
"""


def test_collision_error_envelope_byte_identical_across_hash_seeds() -> None:
    child = """
import sys
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[1] + "/gate")
from glue import transpile_json
sys.stdout.write(transpile_json(sys.stdin.read()))
"""
    outputs = []
    for seed in _SEEDS:
        process = subprocess.run(
            [sys.executable, "-s", "-P", "-c", child, str(_REPO_ROOT)],
            input=_COLLISION_FIXTURE,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
        )
        assert process.returncode == 0, process.stderr
        envelope = json.loads(process.stdout)
        assert envelope["ok"] is False
        assert (
            "History references on a scalar callable local named 'alpha'"
            in envelope["error"]
        )
        outputs.append(process.stdout)
    assert len(set(outputs)) == 1


def test_emitters_do_not_sort_identity_mapping_keys() -> None:
    """Guard direct identity-keyed stores, including stores in another mixin."""
    trees = {
        path: ast.parse(path.read_text())
        for path in (_REPO_ROOT / "pineforge_codegen/codegen").glob("*.py")
    }
    identity_mappings = {
        ast.unparse(node.value)
        for tree in trees.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
        and any(isinstance(part, ast.Call)
                and isinstance(part.func, ast.Name) and part.func.id == "id"
                for part in ast.walk(node.slice))
    }
    assert "self._security_expr_hist_by_node" in identity_mappings
    violations = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "sorted" and node.args):
                continue
            argument = node.args[0]
            if (isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Attribute)
                    and argument.func.attr in {"keys", "items"}):
                argument = argument.func.value
            if ast.unparse(argument) in identity_mappings:
                violations.append(f"{path.name}:{node.lineno}")
    assert not violations, violations
