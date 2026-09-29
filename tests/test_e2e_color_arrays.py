"""``array.from`` of colors builds a ``std::vector<int64_t>``.

A color is a packed-ARGB ``int64_t``, and ``var array<color> A`` is declared
``std::vector<int64_t>``, but ``array.from`` inferred its element type from
its first argument, which it read as a float for a ``color.*`` constant, a
``color.new`` / ``color.rgb`` call, a color literal inside a conditional or a
color variable the analyzer types float (``c1 = color.new(...)``). The
constructor was ``std::vector<double>{...}``: assigning it to the member did
not compile, and bracing a ``color.new(...)`` into doubles is a narrowing
error, so every such script failed to compile (W11's alpha probe among them).

TradingView's tape of ``fixtures/silent2_tv/cgs2_color_arrays`` spells each
element's channels and transparency on every close.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_color_arrays"


def test_the_color_arrays_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"color arrays: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


@pytest.mark.parametrize("declaration, member", [
    ("var array<color> A = array.from(color.red, color.new(color.blue, 50), #00ff00)",
     "std::vector<int64_t> A;"),
    ("var A = array.from(color.new(#102030, 40), #405060)", "std::vector<int64_t> A;"),
    ("A = array.from(color.rgb(1, 2, 3, 40), #405060)", "std::vector<int64_t> A;"),
    ("A = array.from(close > open ? color.red : #0D0E0F, #405060)", "std::vector<int64_t> A;"),
    ("c1 = color.new(#102030, 20)\nA = array.from(c1, #405060)", "std::vector<int64_t> A;"),
])
def test_a_color_array_is_built_as_int64(declaration, member):
    cpp = transpile(
        "//@version=6\n"
        'strategy("color arrays")\n'
        f"{declaration}\n"
        "if color.t(A.get(0)) > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    assert member in cpp
    assert "std::vector<double>{" not in cpp
    compile_cpp(cpp)
