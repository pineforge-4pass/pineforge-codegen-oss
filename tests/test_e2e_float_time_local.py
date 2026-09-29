"""A function-local ``float x = time`` keeps float's history.

``float x = time`` in a function body declares a float: TradingView's
``x - x[1]`` is na on the first bar, where ``x[1]`` does not exist yet.
PineForge declared the local's history ``Series<int64_t>``: the epoch
provenance keyed by spelling made the name wide, and ``_series_type_for``
read its declared type from the global scope, which does not hold a
function's local, so the float declaration never counted. Its missing value
was the integer sentinel, and ``x - x[1]`` read a garbage number on the first
bar instead of na.

TradingView's tape of ``fixtures/silent2_tv/cgs2_float_time_local`` spells
the first bar's value (na) and the bar spacing on every close, and
``cgs2_float_time_method`` the same of a method's locals (the method scope is
``method_<Type>_<name>``).
"""

from __future__ import annotations

from pathlib import Path

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_float_time_local"


def test_the_float_time_local_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336
    assert all(s.startswith("na|-1|") for s in tape.values())
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"float time local: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_a_declared_float_local_keeps_a_float_series():
    cpp = transpile(
        "//@version=6\n"
        'strategy("float local")\n'
        "f() =>\n"
        "    float x = time\n"
        "    x - x[1]\n"
        "g() =>\n"
        "    y = time\n"
        "    y - y[1]\n"
        "if f() > 0 and g() > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    assert "Series<double> x;" in cpp
    # An untyped local holding the epoch keeps its 64-bit series.
    assert "Series<int64_t> y;" in cpp
    compile_cpp(cpp)


def test_the_float_time_method_tape_replays(tmp_path):
    name = "cgs2_float_time_method"
    exits = replay(skip_unless_e2e_env(), tmp_path,
                   {name: Build(source(name, FIXTURES))})[name]
    tape = tape_exits(name, FIXTURES)
    assert len(tape) == 336
    assert all(s.startswith("na|-1|") for s in tape.values())
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])


def test_a_method_float_local_keeps_a_float_series():
    cpp = transpile(
        "//@version=6\n"
        'strategy("float method local")\n'
        "type U\n"
        "    float v\n"
        "method dt(U this) =>\n"
        "    float xm = time\n"
        "    xm - xm[1] + this.v * 0\n"
        "if U.new(1.0).dt() > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    assert "Series<double> xm;" in cpp
    compile_cpp(cpp)
