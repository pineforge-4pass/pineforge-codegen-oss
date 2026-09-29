"""A UDT built each bar from ``var`` arrays holds them instead of copies.

``FeatureArrays.new(f1Array, f2Array, ...)`` on every bar stored a copy of
each growing ``var`` array in the record, and the record arena keeps every
record: memory grew with the square of the bar count (the ai-distribution
15-minute probes reached 8.7 GB at 20k bars). TradingView holds the arrays
themselves: a push through ``h.xs`` reaches ``a``.

TradingView keeps a ``var`` array's value per bar, though: a record kept
from an earlier bar reads the array as it was on that bar. So a type whose
objects never outlive their bar (``_udt_bar_local_types``: no ``var``
declaration, collection, field or history read holds one) aliases a stable
``var`` array it is built from (``_PFArrayField<T>::alias``), exact and
without a copy; a type that is kept keeps the copy every earlier build
stored.

TradingView's tape of ``fixtures/silent2_tv/cgs2_udt_array_fields`` spells
both on every close.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_udt_array_fields"


def test_the_udt_array_fields_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"UDT array fields: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


_FEATURES = (
    "//@version=6\n"
    'strategy("features")\n'
    "type FeatureArrays\n"
    "    array<float> f1\n"
    "    array<float> f2\n"
    "var f1Array = array.new_float()\n"
    "var f2Array = array.new_float()\n"
    "f1Array.push(close)\n"
    "f2Array.push(open)\n"
    "fa = FeatureArrays.new(f1Array, f2Array)\n"
    "d = array.get(fa.f1, 0) + fa.f2.size()\n"
    "if d > 0\n"
    '    strategy.entry("L", strategy.long)\n'
)


def test_a_bar_local_record_aliases_its_var_arrays():
    cpp = transpile(_FEATURES)
    assert "_PFArrayField<double> f1 = _PFArrayField<double>();" in cpp
    assert (".f1 = _PFArrayField<double>::alias(f1Array), "
            ".f2 = _PFArrayField<double>::alias(f2Array)") in cpp
    assert "(*_pf_udt_FeatureArrays.get(fa).f1)" in cpp
    compile_cpp(cpp)


@pytest.mark.parametrize("extra, kept_type", [
    # Kept in a var collection: the record keeps its copy.
    ("var keep = array.new<FeatureArrays>()\nkeep.push(fa)\n", True),
    # Held by another type's field.
    ("type Box\n    FeatureArrays inner\nbx = Box.new(fa)\n", True),
    # Held by a var.
    ("var FeatureArrays first = na\nfirst := fa\n", True),
])
def test_a_kept_record_keeps_its_copies(extra, kept_type):
    source_text = _FEATURES.replace('if d > 0\n', extra + 'if d > 0\n')
    cpp = transpile(source_text)
    assert ("std::vector<double> f1 = " in cpp) is kept_type
    assert "_PFArrayField" not in cpp
    compile_cpp(cpp)


def test_a_rebound_var_array_is_copied():
    # ``f1Array := ...`` gives the name a new array: a record keeps its copy
    # of the old one rather than alias the member the name now names.
    source_text = _FEATURES.replace(
        "f2Array.push(open)\n",
        "f2Array.push(open)\nif f1Array.size() > 100\n    f1Array := array.new_float()\n")
    cpp = transpile(source_text)
    assert "_PFArrayField<double>::alias(f1Array)" not in cpp
    assert "_PFArrayField<double>(f1Array)" in cpp
    compile_cpp(cpp)


def test_vars_that_hold_no_object_keep_the_alias():
    # ai-distribution's shape: a var of a negated literal and a var of a
    # user function returning a table hold no user object, so they do not
    # make the record type escape its bar.
    source_text = _FEATURES.replace(
        "fa = FeatureArrays.new",
        "var hi = -1e10\n"
        "mk() => table.new(position.top_right, 1, 1)\n"
        "if barstate.islast\n    var tbl = mk()\n"
        "fa = FeatureArrays.new")
    cpp = transpile(source_text)
    assert "_PFArrayField<double>::alias(f1Array)" in cpp
    compile_cpp(cpp)


def test_a_var_holding_an_untyped_helper_result_keeps_the_copy():
    # pick(o) => o returns its argument, an object: a var holding its result
    # keeps H past its bar, so H keeps the copy every earlier build stored
    # (it was taken for a helper returning no object and aliased ``a``).
    cpp = transpile(
        "//@version=6\n"
        'strategy("untyped helper result")\n'
        "type H\n"
        "    array<float> xs\n"
        "var a = array.new_float()\n"
        "a.push(close)\n"
        "pick(o) => o\n"
        "var kept = pick(H.new(a))\n"
        "if bar_index > 5\n"
        '    strategy.entry("L", strategy.long)\n')
    assert "_PFArrayField" not in cpp
    assert "std::vector<double> xs" in cpp
