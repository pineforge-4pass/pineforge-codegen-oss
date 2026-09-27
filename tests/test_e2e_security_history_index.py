"""A ``request.security`` TA history index computed from an input.

``ta.ema(close, len)[n15Bars]`` with ``n15Bars = math.min(290, math.max(1,
math.round(slopeNMin / 15)))`` was refused ("request.security() TA history
index must be a literal integer"): the requested-context history read
admitted a literal or a bare ``input.int`` chain only. It now admits any
bar-invariant int (``_security_stable_value_type``: literals, ``input.*``
values and math, casts, arithmetic, comparisons and ternaries over those,
through helper parameters and immutable globals, by their lexical binding)
and lowers it in the requested context. A series index, a float index, a
mutable global and a block local or loop variable sharing a global's name
stay refused.

``/`` in a payload divided two ints as C++ integers; Pine v6 divides them
as floats (the chart's ``_visit_binop`` casts both sides). TradingView's tape
of ``sec2_hist_index`` (``fixtures/security2_tv``) reads ``n`` = 4 with
``nMin`` 60, and ``sec2_hist_index_55`` -- ``nMin`` 55, where ``55 / 15`` is
3.67 and an integer division would give 3 -- books the same tape byte for
byte.

An input read while such an index -- or a helper's TA constructor argument
(``f(close, int(flen))``) -- is lowered is its override-aware getter:
``evaluate_security`` can run, and reset its TA objects, before ``on_bar``
has read the inputs into their members. A constructor argument read the
member ``flen`` there, built ``ta::SMA(0)`` and the run crashed.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


NMIN_30 = source("sec2_hist_index").replace(
    'nMin = input.int(60, "Lookback N (minutes)")', "nMin = 30")


def test_input_derived_history_index_matches_the_tape(tmp_path_factory):
    assert NMIN_30 != source("sec2_hist_index")
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security2_hist_index")
    exits = replay(
        engine, base,
        {"probe": Build(source("sec2_hist_index")),
         "nmin55": Build(source("sec2_hist_index_55")),
         "override30": Build(source("sec2_hist_index")),
         "literal30": Build(NMIN_30)},
        params={"override30": {"Lookback N (minutes)": 30}},
    )
    tape = tape_exits("sec2_hist_index")
    assert len(tape) == 265
    for key in ("probe", "nmin55"):
        missed = mismatches(tape, exits[key])
        assert not missed, f"[{key}] " + "\n".join(missed[:10])
    # An override reaches the requested-context index: 30 minutes read [2],
    # as the same script with nMin a literal 30 does.
    assert exits["override30"] == exits["literal30"]
    assert exits["override30"] != exits["probe"]
    print(f"input-derived TA history index: {len(tape)} of {len(tape)} exit Signals "
          "equal TradingView's with nMin 60 and 55; an override to 30 reads [2]")


@pytest.mark.parametrize("declaration, index", [
    ("", "bar_index % 3"),
    ('k = input.float(2.0, "Offset")', "k"),
    ('k = input.int(2, "Offset")', "k / 2"),
    ('k = input.int(2, "Offset")\nk := 3', "k + 1"),
])
def test_series_float_and_mutable_indices_stay_refused(declaration, index):
    src = f'''//@version=6
strategy("unsupported dynamic TA offset")
{declaration}
x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[{index}])
plot(x)
'''
    with pytest.raises(CompileError, match=r"TA history index must be a literal integer"):
        transpile(src)


@pytest.mark.parametrize("src, division", [
    ("""//@version=6
strategy("int parameter division")
f(int a) => a / 4
v = request.security(syminfo.tickerid, "60", f(5))
plot(v)
""", "((double)(5) / (double)(4))"),
    ("""//@version=6
strategy("int local division")
f() =>
    a = 5
    b = 2
    a / b
v = request.security(syminfo.tickerid, "60", f())
plot(v)
""", "/ (double)("),
    # The chart types f() float; the evaluator inlines its int argument.
    ("""//@version=6
strategy("int argument division")
f(float a) => a
v = request.security(syminfo.tickerid, "60", f(5) / 2)
plot(v)
""", "((double)(5) / (double)(2))"),
    # A float global the evaluator re-emits from its int initializer.
    ("""//@version=6
strategy("int initializer division")
float bias = close > open ? 1 : -1
v = request.security(syminfo.tickerid, "60", bias / 2)
plot(v)
""", "/ (double)(2)"),
    # A tuple element's local is ``auto``: here an int.
    ("""//@version=6
strategy("tuple element division")
g() => [bar_index, close]
f() =>
    [i, c] = g()
    i
v = request.security(syminfo.tickerid, "60", f() / 2)
plot(v)
""", "/ (double)(2)"),
])
def test_int_division_through_helper_bindings_is_a_float(src, division):
    body = transpile(src)
    body = body[body.index("void _eval_security_0("):]
    assert division in body


def test_history_index_reads_inputs_through_their_getters():
    # The evaluator can run before on_bar has set the input members.
    src = """//@version=6
strategy("index input getter")
k = input.float(2.0, "K")
k2 = int(k) + 1
x = request.security(syminfo.tickerid, "60", ta.sma(close, 5)[int(k)])
y = request.security(syminfo.tickerid, "60", ta.sma(close, 5)[k2])
plot(x + y)
"""
    cpp = transpile(src)
    indices = [line for line in cpp.splitlines() if "int _hidx = " in line]
    assert len(indices) == 2 and all('get_input_double("K", 2.0)' in line for line in indices)


_INPUT_LENGTH_PROBE = """//@version=6
strategy("input length through a helper")
flen = input.float(6.0, "FLen")
HELPER
v = request.security(syminfo.tickerid, "60", PAYLOAD, lookahead=barmerge.lookahead_on)
m = minute(time, "UTC")
if m == 0 or m == 30
    strategy.entry("L", strategy.long)
if m == 15 or m == 45
    strategy.close("L", comment = na(v) ? "na" : str.tostring(v))
"""


def test_helper_ta_length_reads_its_input_through_the_getter(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security2_input_ctor")
    builds = {
        name: Build(_INPUT_LENGTH_PROBE.replace("HELPER", helper).replace("PAYLOAD", payload))
        for name, helper, payload in (
            ("helper", "f(float x, int n) => ta.sma(x, n)", "f(close, int(flen))"),
            ("method", "method sm(float x, int n) => ta.sma(x, n)", "close.sm(int(flen))"),
            ("literal", "", "ta.sma(close, 6)"),
        )
    }
    for name in ("helper", "method"):
        cpp = transpile(builds[name].source)
        assert 'get_input_double("FLen", 6.0)' in cpp[cpp.index("void evaluate_security("):], name
    exits = replay(engine, base, builds)
    assert len(exits["literal"]) > 200
    assert exits["helper"] == exits["literal"]
    assert exits["method"] == exits["literal"]
