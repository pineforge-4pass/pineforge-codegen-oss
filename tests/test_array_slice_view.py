"""Native value tests for slice element sharing.

The recorded TradingView tape establishes bidirectional element sharing and detached
copy. Resize rows below explicitly pin PineForge compatibility, not TV behavior.
All behavioral rows compile the complete emitted TU against the paired engine.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from pineforge_codegen import transpile, transpile_full
from tests import _compile as compile_env


PRELUDE = '//@version=6\nstrategy("Slice view")\n'
BASE = "1e2e72e37047ea22a4fdfc189584668e9567fdee"
CALLABLE_ONLY_SLICES = [
    pytest.param(
        '//@version=6\nstrategy("T")\n'
        "method bump(array<int> xs) =>\n"
        "    v = xs.slice(0, 1)\n"
        "    v.set(0, 99)\n"
        "    xs.get(0)\n"
        "a = array.from(1, 2, 3)\n"
        "observed = a.bump()\n",
        id="method_parameter",
    ),
    pytest.param(
        '//@version=6\nstrategy("T")\n'
        "f() =>\n"
        "    local = array.from(1, 2, 3)\n"
        "    v = local.slice(0, 1)\n"
        "    v.set(0, 99)\n"
        "    local.get(0)\n"
        "observed = f()\n",
        id="callable_local",
    ),
]


@pytest.mark.parametrize("pine", CALLABLE_ONLY_SLICES)
def test_callable_only_slice_opens_view_gate(pine):
    result = transpile_full(pine)
    assert "class _PFSliceArray" in result["cpp"]
    assert not any(d.code == "PF-W5003" for d in result["diagnostics"])


@pytest.mark.parametrize(("receiver_type", "initializer", "body"), [
    pytest.param("string", '"text"', "str.length(receiver)", id="string"),
    pytest.param("float", "close", "receiver", id="series"),
    pytest.param("map<string,int>", "map.new<string,int>()", "map.size(receiver)", id="map"),
    pytest.param("matrix<float>", "matrix.new<float>(1, 1, 0.0)", "matrix.rows(receiver)", id="matrix"),
    pytest.param("Holder", "Holder.new(1)", "receiver.value", id="udt"),
])
def test_non_array_callable_local_slice_keeps_authored_method(receiver_type, initializer, body):
    result = transpile_full(PRELUDE + (
        "type Holder\n"
        "    int value\n"
        f"method slice({receiver_type} receiver) =>\n"
        f"    {body}\n"
        "read() =>\n"
        f"    local = {initializer}\n"
        "    local.slice()\n"
        "observed = read()\n"
    ))
    assert "_PFSliceArray" not in result["cpp"]
    assert not any(d.code == "PF-W5003" for d in result["diagnostics"])


def test_slice_no_longer_warns_that_it_is_a_detached_copy():
    result = transpile_full(PRELUDE + "a = array.from(1, 2)\nv = a.slice(0, 1)\n")
    assert not any("array.slice returns a copy" in d.message for d in result["diagnostics"])


@pytest.mark.parametrize("authored_slice", [False, True])
def test_slice_free_array_cpp_is_byte_identical_to_base(authored_slice):
    # Compare raw whole-TU text, including helpers, settings and checkpoint
    # support. No normalization or legacy_cpp projection may hide changes.
    from tests._e2e import reference_codegen, transpile_json

    reference = reference_codegen(BASE)
    assert reference is not None, f"required byte oracle commit {BASE} is missing"
    pine = (
        PRELUDE
        + "var a = array.from(1, 2, 3)\n"
        + "a.set(1, 7)\n"
        + "b = a.copy()\n"
        + "b.push(9)\n"
        + "observed = a.sum() + b.sum()\n"
        + 'plot(observed, title="std::vector<int> array.slice text")\n'
    )
    if authored_slice:
        pine = (
            PRELUDE
            + "method slice(array<int> xs) =>\n"
            + "    xs.size()\n"
            + "read(array<int> xs) =>\n"
            + "    xs.slice()\n"
            + "a = array.from(1, 2, 3)\n"
            + "observed = read(a)\n"
        )
    with tempfile.TemporaryDirectory(prefix="pf_slice_free_") as directory:
        path = Path(directory) / "strategy.pine"
        path.write_text(pine)
        original = transpile_json(path, root=reference)
    assert original["ok"], original
    assert transpile(pine) == original["cpp"]


