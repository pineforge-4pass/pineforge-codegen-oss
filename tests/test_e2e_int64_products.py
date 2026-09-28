"""Integer products that leave int32 keep Pine's 64 bits.

Pine's ``int`` is 64-bit. ``days * 86400000`` over ``days = input.int(30)``
is 2592000000 on TradingView, and ``bar_index * 7200000`` passes int32 at
bar 299. PineForge emitted both as C++ ``int`` arithmetic, which overflows
(``time - days * 86400000`` was a window 25 days off). A constant past int32
held in a tuple element or a method's result was stored in a 32-bit slot.

An integer ``+ - *`` whose operands are both 32-bit C++ ints is computed in
64 bits when its magnitude can leave int32 (``_int_arith_leaves_int32``:
literals and constants exactly, an input by its declared range, a counted
loop's binder and any other runtime integer up to 2**24), and its value is
wide wherever it is stored; an operand that can be na makes it na. A
``for ... in`` binder (an element, a map value: often a double) is none of
those. A ``map<..., int>`` value, ``matrix<int>`` element or element of an
int array no epoch widens keeps its 32 bits (its type also types the
handle's parameters and array views) and warns. TradingView's tape of
``fixtures/silent2_tv/cgs2_int64_products`` spells each value on every close.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from tests._compile import compile_cpp
from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_int64_products"


def test_the_int64_products_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336
    assert max(int(s.split("|")[3]) for s in tape.values()) == 4831200000
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"int64 products: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_the_int64_followup_tape_replays(tmp_path):
    # A float element or map value times a constant keeps its fraction (4500,
    # 63), an int that is na makes a 64-bit product na and not negative (the
    # global x, a double slot, and g's int64_t span), and a requested product
    # of a global re-evaluated as a double keeps its na.
    followup = "cgs2_int64_followup"
    exits = replay(skip_unless_e2e_env(), tmp_path,
                   {followup: Build(source(followup, FIXTURES))})[followup]
    tape = tape_exits(followup, FIXTURES)
    assert len(tape) == 336
    assert all(s.startswith("4500|63|") for s in tape.values())
    assert any(s.split("|")[2:5] == ["na", "nonneg", "na"] for s in tape.values())
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])


def test_the_int64_followup2_tape_replays(tmp_path):
    # An int for-in element and map value times 10**9 (15, 4), a function's
    # declared int local times a day (30: its result type is decided before
    # its locals are registered), a helper's int result read in a function
    # whose parameter of that name is a float (1.5 over 3e9), and a requested
    # int script variable times 10**9 (the requested hour).
    name = "cgs2_int64_followup2"
    exits = replay(skip_unless_e2e_env(), tmp_path,
                   {name: Build(source(name, FIXTURES))})[name]
    tape = tape_exits(name, FIXTURES)
    assert len(tape) == 336
    assert all(s.startswith("15|4|30|1.5|") for s in tape.values())
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])


def _script(body: str) -> str:
    return ('//@version=6\nstrategy("int64 products")\n' + body
            + '\nif bar_index > 0\n    strategy.entry("L", strategy.long)\n')


def test_a_product_that_can_leave_int32_is_computed_in_64_bits():
    cpp = transpile(_script(
        'days = input.int(30, "days")\n'
        "a = days * 86400000\n"
        "b = bar_index * 7200000\n"
        "g(int n) =>\n"
        "    int span = n * 86400000\n"
        "    span\n"
        "e = g(days)\n"
        "plot(a + b + e)\n"))
    assert "(static_cast<int64_t>(days) * 86400000)" in cpp
    assert "(static_cast<int64_t>(pine_bar_index()) * 7200000)" in cpp
    # The int slot holding such a value is 64-bit, and so is the result.
    # A parameter can be na, and so is the product then: a double the
    # 64-bit slot narrows na-preserving.
    assert ("int64_t span = [&](){ auto _pf_v = ([&]() -> double { "
            "auto _pf_wide_l = (n); auto _pf_wide_r = (86400000); "
            "return (is_na(_pf_wide_l) || is_na(_pf_wide_r)) ? na<double>() : "
            "static_cast<double>((static_cast<int64_t>(_pf_wide_l) * _pf_wide_r)); "
            "}()); return is_na(_pf_v) ? na<int64_t>() : (int64_t)_pf_v; }();") in cpp
    assert "int64_t g(int n)" in cpp
    compile_cpp(cpp)


@pytest.mark.parametrize("body, spelling", [
    # A product that stays inside int32 keeps its spelling byte for byte.
    ('len = input.int(14, "len")\nx = len * 2\nplot(x)', "(len * 2)"),
    ("x = bar_index * 100\nplot(x)", "(pine_bar_index() * 100)"),
    # An input bounded by maxval stays narrow when its range fits.
    ('n = input.int(7, "n", minval = 1, maxval = 3650)\nx = n * 60000\nplot(x)',
     "(n * 60000)"),
    # A double operand already computes in double.
    ("x = close * 86400000\nplot(x)", "(current_bar_.close * 86400000)"),
])
def test_products_inside_int32_keep_their_spelling(body, spelling):
    assert spelling in transpile(_script(body))


def test_an_input_range_past_int32_is_computed_in_64_bits():
    cpp = transpile(_script(
        'n = input.int(7, "n", minval = 1, maxval = 3650)\n'
        "x = n * 24 * 60 * 60 * 1000\nplot(x)"))
    assert "(static_cast<int64_t>((((n * 24) * 60) * 60)) * 1000)" in cpp
    compile_cpp(cpp)


def test_constants_past_int32_keep_64_bit_slots():
    cpp = transpile(_script(
        "tup() => [3000000000, 5]\n"
        "[p, q] = tup()\n"
        "type U\n    int v = 3000000000\n"
        "method big(U this) => this.v * 2\n"
        "method konst(U this) => na(this.v) ? 0 : 3000000000\n"
        "u = U.new()\n"
        "plot(p + u.big() + u.konst())\n"))
    assert "std::tuple<int64_t, int> tup()" in cpp
    assert "int64_t _udt_U_big(" in cpp
    assert "int64_t _udt_U_konst(" in cpp
    compile_cpp(cpp)


def test_an_int_map_or_matrix_holding_a_wide_value_warns():
    # A map<..., int> value and a matrix<int> element keep the C++ int they
    # compile with: that type also types their parameters and values() /
    # row() arrays. A value past int32 stored in one warns instead.
    full = transpile_full(_script(
        "var m = map.new<string, int>()\n"
        'm.put("k", 3000000000)\n'
        "mx = matrix.new<int>(1, 1, 3000000000)\n"
        "mx.set(0, 0, bar_index * 7200000)\n"
        'plot(m.get("k") + mx.get(0, 0))\n'))
    messages = [d.message for d in full["diagnostics"]]
    assert sum("map<..., int> value is stored as a 32-bit int" in m for m in messages) == 1
    assert sum("matrix<int> element is stored as a 32-bit int" in m for m in messages) == 2
    assert "PineMap<std::string, int> m" in full["cpp"]
    compile_cpp(full["cpp"])


def test_a_for_in_binder_is_no_32_bit_int():
    # An element of a float array or a map's float value is a double: the
    # product computes in double (a cast truncated 1.25 * 1000 to 1000).
    cpp = transpile(_script(
        "var a = array.from(0.5, 1.25, 2.75)\n"
        "float s1 = 0.0\n"
        "for v in a\n"
        "    s1 += v * 1000\n"
        "var m = map.new<string, float>()\n"
        'm.put("a", close / 1000.0)\n'
        "float ms = 0.0\n"
        "for [k, w] in m\n"
        "    ms += w * 252\n"
        "float s3 = 0.0\n"
        "for i = 0 to 3\n"
        "    s3 += i * 1000000000\n"
        "plot(s1 + ms + s3)\n"))
    assert "s1 += (v * 1000);" in cpp
    assert "ms += (w * 252);" in cpp
    # A counted loop's binder is an int.
    assert "s3 += (static_cast<int64_t>(i) * 1000000000);" in cpp
    compile_cpp(cpp)


def test_an_untyped_int_parameter_widens_its_result():
    # year(time) carries no TypeSpec to the untyped x: the emitter types it
    # int, and so does the width rule deciding toMs's result, which read
    # the parameter as unknown and returned int, na past int32.
    cpp = transpile(_script(
        "toMs(x) => x * 86400000\n"
        "b = toMs(year(time)) / 86400000\n"
        "plot(b)\n"))
    assert "int64_t toMs(int x)" in cpp
    compile_cpp(cpp)


def test_a_double_product_in_an_epoch_array_initializer_narrows():
    # time + n * 900000 over an int that can be na is a double: a braced
    # std::vector<int64_t> initializer does not narrow it implicitly (it did
    # not compile with AppleClang), so it narrows na-preserving.
    cpp = transpile(_script(
        "var int n = na\n"
        "if bar_index >= 3\n"
        "    n := bar_index\n"
        "exits = array.from(time, time + n * 900000)\n"
        "plot(exits.size())\n"))
    assert "std::vector<int64_t>{current_bar_.timestamp, [&](){ auto _pf_v" in cpp
    compile_cpp(cpp)


def test_an_int_array_only_an_epoch_widens():
    # A value wide only by arithmetic keeps the array's C++ int: a declared
    # array<int> parameter, result or field (here the parameter and the
    # method receiver) binds it, which a std::vector<int64_t> did not.
    full = transpile_full(_script(
        "f(array<int> xs) => xs.size()\n"
        "method total(array<int> xs) => xs.sum()\n"
        "var bars = array.new_int()\n"
        "bars.push(bar_index * 1000)\n"
        "ids = array.from(bar_index * 7200000, 1)\n"
        "plot(f(bars) + bars.total() + f(ids))\n"))
    cpp = full["cpp"]
    assert "std::vector<int> bars" in cpp
    assert "std::vector<int64_t>" not in cpp
    messages = [d.message for d in full["diagnostics"]]
    assert sum("An array<int> element is stored as a 32-bit int" in m
               for m in messages) == 2
    compile_cpp(cpp)


def test_a_payload_over_a_double_global_keeps_its_double():
    # The request re-evaluates n from its expression, a double: the product
    # stays a double and keeps na (an int64_t cast of NaN is undefined).
    cpp = transpile(_script(
        "int n = math.round(ta.sma(close, 20))\n"
        'x = request.security(syminfo.tickerid, "60", n * 1000000)\n'
        "plot(x)\n"))
    assert "(std::round(_secval_0) * 1000000)" in cpp
    assert "static_cast<int64_t>(std::round" not in cpp
    compile_cpp(cpp)


@pytest.fixture(scope="module")
def value_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgs2_int64_values")
    feed = chart_feed_head(engine, base, 200)
    runs = execute_all(engine, feed, base, {
        "values": Build(
            '//@version=6\nstrategy("int64 values")\n'
            "var a = array.from(0.5, 1.25, 2.75)\n"
            "float s1 = 0.0\n"
            "for v in a\n"
            "    s1 += v * 1000\n"
            "var m = map.new<string, float>()\n"
            'm.put("a", 0.25)\n'
            "float ms = 0.0\n"
            "for [k, w] in m\n"
            "    ms += w * 252\n"
            "g(int p) =>\n"
            "    int span = p * 86400000\n"
            "    span\n"
            "var int n = na\n"
            "if bar_index >= 3\n"
            "    n := 7\n"
            "x = n * 1000000000\n"
            "e = g(n)\n"
            "int r = math.round(ta.sma(close, 20))\n"
            'y = request.security(syminfo.tickerid, "60", r * 1000000)\n'
            "toMsY(v) => v * 86400000\n"
            "yr = year(time)\n"
            "ym = toMsY(year(time)) / 86400000\n"
            "if bar_index == 1\n"
            '    strategy.entry("L", strategy.long)\n'
            "// @pf-trace s1=s1\n"
            "// @pf-trace ms=ms\n"
            "// @pf-trace x_na=na(x) ? 1 : 0\n"
            "// @pf-trace x_neg=x < 0 ? 1 : 0\n"
            "// @pf-trace x=x\n"
            "// @pf-trace e_na=na(e) ? 1 : 0\n"
            "// @pf-trace e=e\n"
            "// @pf-trace y_na=na(y) ? 1 : 0\n"
            "// @pf-trace yr=yr\n"
            "// @pf-trace ym=ym\n",
            trace=True),
    })
    return ok(runs, "values").traces["default"]


def _trace(records, name):
    return [r["value"] for r in records if r["name"] == name]


def test_loop_elements_keep_their_fractions(value_runs):
    assert set(_trace(value_runs, "s1")) == {4500.0}
    assert set(_trace(value_runs, "ms")) == {63.0}


def test_an_na_operand_makes_a_64_bit_product_na(value_runs):
    # TradingView: na * 1000000000 is na (and not below 0); 7 * 1000000000
    # is 7000000000. The global x is a double slot, span an int64_t one.
    assert _trace(value_runs, "x_na")[:4] == [1, 1, 1, 0]
    assert _trace(value_runs, "x_neg")[:4] == [0, 0, 0, 0]
    assert _trace(value_runs, "x")[3] == 7000000000
    assert _trace(value_runs, "e_na")[:4] == [1, 1, 1, 0]
    assert _trace(value_runs, "e")[3] == 7 * 86400000


def test_an_untyped_parameter_product_keeps_its_value(value_runs):
    # toMsY(year(time)) is the year in milliseconds (an int64_t result),
    # which read na past int32 when the result was typed int.
    years = _trace(value_runs, "yr")
    assert years and _trace(value_runs, "ym") == years


def test_a_requested_double_product_keeps_na(value_runs):
    # The 60-minute SMA(20) is na until its 20th requested bar completes
    # (80 bars of the 15-minute chart); a cast read NaN as 0 before that.
    y_na = _trace(value_runs, "y_na")
    assert y_na[:76] == [1] * 76
    assert 0 in y_na
    assert not any(math.isnan(v) for v in _trace(value_runs, "s1"))
