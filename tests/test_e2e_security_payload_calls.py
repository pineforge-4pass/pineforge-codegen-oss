"""User calls under builtin calls in a ``request.security`` payload read the
requested bar (CG-ISMARKET finding F4).

``_build_security_expr`` inlines a user function it meets, but a node it has
no case for (``nz(...)``, ``na(...)``, ``str.tostring(...)``, a user method
call) went to the chart's expression visitor whole. Everything below that
node was then lowered on the chart's terms: ``nz(f())`` called the chart
method ``f()``, which reads ``current_bar_``; ``close.g()`` called
``_udt_float_g(bar.close)``, whose body adds the chart bar's ``open``;
``nz(ta.sma(close, 3))`` read the chart's SMA member (its precalculated
series); ``nz(src[1])`` in a helper read the chart's ``_s_close``. The
builder now hands every such node back to itself while the visitor renders
the wrapper, and inlines a typed user method like any helper.

TradingView's tape of ``sec2_payload_udf`` (``fixtures/security2_tv``) reads
each of those shapes on a 60-minute payload: all 265 exit Signals now match;
the pre-lane build matched none of them.

All or nothing, per evaluator: when a builtin call also reads something else
on the chart's terms (a global, history the builder does not own,
``bar_index``) beside a call the evaluator inlines, or the evaluator refuses
such a call (a helper a bare payload refuses, a method it cannot inline), the
evaluator keeps the lowering every earlier build gave it, its calls on the
chart, and warns -- a requested ``f()`` beside the chart's ``g``, or a
requested ``mom`` beside the chart's ``mom[1]``, would mix two bars.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests import _compile as compile_env
from tests._e2e import Build, reference_codegen, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

PRE_LANE = "7a39cb3"  # cg/popfix, this lane's base


def test_payload_calls_read_the_requested_bar_like_the_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security2_payload_udf")
    builds = {"probe": Build(source("sec2_payload_udf"))}
    legacy = reference_codegen(PRE_LANE)
    if legacy is not None:
        builds["pre_lane"] = Build(source("sec2_payload_udf"), codegen=legacy)
    exits = replay(engine, base, builds)
    tape = tape_exits("sec2_payload_udf")
    assert len(tape) == 265
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    if "pre_lane" not in exits:
        pytest.skip(f"the pre-lane codegen ({PRE_LANE}) is not in this checkout's history")
    # The pre-lane build read the chart bar in a1, a3, a4 and a5.
    assert len(mismatches(tape, exits["pre_lane"])) == len(tape)
    print(f"payload calls: {len(tape)} of {len(tape)} exit Signals equal TradingView's "
          "(pre-lane build: 0)")


def _eval_body(cpp: str) -> str:
    start = cpp.index("void _eval_security_0(")
    return cpp[start:cpp.index("\n    }\n", start)]


def test_payload_calls_are_inlined_on_the_requested_bar():
    cpp = transpile(source("sec2_payload_udf"))
    evaluators = cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]
    # No chart method, chart TA member or chart close series is reached from
    # an evaluator.
    for chart_read in (r"\bf\(\)", r"\bk_cs\d", r"\b_udt_float_g\(",
                       r"(?<!\w)_ta_sma_1\.", r"\b_precalc_", r"\b_s_close\b"):
        assert not re.search(chart_read, evaluators), chart_read
    assert "is_na(_nz_v) ? (0.0) : _nz_v" in _eval_body(cpp)


def _eval_bodies(cpp: str) -> str:
    return cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]


def _transpiled(src: str) -> dict:
    from pineforge_codegen import transpile_full
    return transpile_full(src)


def test_a_method_with_statements_keeps_the_chart_call():
    # The evaluator writes a multi-statement body ahead of the payload, where
    # a ternary arm or an and/or operand would run it on every requested bar:
    # only a single-expression method is inlined.
    src = """//@version=6
strategy("method with statements")
method m(float x) =>
    y = x * 2
    y + 1
method e(float x) => x * 2 + 1
v = request.security(syminfo.tickerid, "60", close.m())
w = request.security(syminfo.tickerid, "60", close.e())
plot(v + w)
"""
    result = _transpiled(src)
    body = _eval_bodies(result["cpp"])
    assert re.search(r"_req_sec_0 = _udt_float_m(_cs\d+)?\(bar\.close\);", body), body
    assert "_req_sec_1 = ((bar.close * 2) + 1);" in body, body
    assert any("calls method 'm' on the chart's bar" in d.message
               for d in result["diagnostics"])
    compile_env.compile_cpp(result["cpp"], label="security-method-with-statements")


@pytest.mark.parametrize("name, src", [
    ("loop", """//@version=6
strategy("method loop")
method sumBack(float src, int n) =>
    s = 0.0
    for i = 0 to n - 1
        s += src[i]
    s
v = request.security(syminfo.tickerid, "D", close.sumBack(3))
plot(v)
"""),
    ("udt", """//@version=6
strategy("udt method")
type Acc
    float total = 0.0
method value(Acc this) => this.total
acc = Acc.new()
acc.total := close
v = request.security(syminfo.tickerid, "D", acc.value())
plot(v)
"""),
    ("mutable global", """//@version=6
strategy("method mutable")
var float acc = 0.0
acc += close
method m(float x) => x + acc
v = request.security(syminfo.tickerid, "60", close.m())
plot(v)
"""),
])
def test_other_methods_keep_the_chart_call_and_warn(name, src):
    # A method the payload cannot inline on the requested bar keeps the chart
    # call every earlier build emitted, and says so.
    result = _transpiled(src)
    assert re.search(r"_req_sec_0 = _udt_\w+\(", _eval_bodies(result["cpp"])), name
    assert any("calls method" in d.message and "chart's bar" in d.message
               for d in result["diagnostics"]), name
    compile_env.compile_cpp(result["cpp"], label=f"security-chart-method-{name.replace(' ', '-')}")


def test_method_default_binds_in_the_declaration_scope():
    src = """//@version=6
strategy("method default")
LEN = 3
method g(float x, int n = LEN) => x * n
f(float LEN) => close.g() + LEN * 0
v = request.security(syminfo.tickerid, "60", f(10))
plot(v)
"""
    assert "_req_sec_0 = ((bar.close * 3) + (10 * 0));" in _eval_bodies(transpile(src))


@pytest.mark.parametrize("name, src", [
    ("compound history", """//@version=6
strategy("global history")
x = high - low
a = request.security(syminfo.tickerid, "60", nz(x[1]))
plot(a)
"""),
    ("switch and if values", """//@version=6
strategy("global selection")
x = switch
    close > open => 1.0
    => -1.0
y = if close > open
    2.0
else
    3.0
v = request.security(syminfo.tickerid, "60", nz(x) + nz(y))
plot(v)
"""),
    ("tuple and request bindings", """//@version=6
strategy("global tuple")
d = request.security(syminfo.tickerid, "D", close)
w = request.security(syminfo.tickerid, "W", nz(d))
[a, b] = request.security(syminfo.tickerid, "D", [close, open])
w2 = request.security(syminfo.tickerid, "W", nz(a))
plot(w + w2)
"""),
])
def test_globals_the_builder_cannot_redraw_keep_the_chart_member(name, src):
    # Before this lane each of these compiled with the chart's member; the
    # requested context has no faithful route for them, so they keep it.
    compile_env.compile_cpp(transpile(src), label=f"security-chart-global-{name.replace(' ', '-')}")


def test_collection_argument_is_not_redrawn():
    src = """//@version=6
strategy("collection arg")
arr = array.new<float>()
arr.push(close)
f(a) => nz(array.size(a) > 0 ? array.get(a, 0) : na, -1)
x = request.security(syminfo.tickerid, "60", f(arr))
plot(x)
"""
    with pytest.raises(CompileError, match="Unknown variable 'a'"):
        transpile(src)


def test_a_builtin_rendering_an_argument_twice_emits_the_helper_once():
    src = """//@version=6
strategy("format helper")
f() => ta.sma(close, 3) * 2
x = request.security(syminfo.tickerid, "60", str.format("{0}", f()))
plot(close)
"""
    body = _eval_bodies(transpile(src))
    assert body.count(".compute(bar.close)") == 1, body
    assert not re.search(r"\bf(_cs\d+)?\(\)", body), body

@pytest.mark.parametrize("name, src, chart_call", [
    ("collection argument", """//@version=6
strategy("collection argument")
arr = array.from(1.0, 2.0, 3.0)
first(a) => array.get(a, 0)
v = request.security(syminfo.tickerid, "60", nz(first(arr)) + close * 0)
plot(v)
""", r"\bfirst\(arr\)"),
    ("TA history in a multi-statement helper", """//@version=6
strategy("helper TA history")
f() =>
    s = ta.sma(close, 5)
    s - ta.sma(close, 5)[1]
v = request.security(syminfo.tickerid, "60", nz(f()))
plot(v)
""", r"\bf_cs0\(\)"),
    ("series TA history index", """//@version=6
strategy("series TA index")
g(float x, int k) => ta.sma(x, 5)[k]
v = request.security(syminfo.tickerid, "60", nz(g(close, bar_index % 2)))
plot(v)
""", r"\bg_cs0\(bar\.close, "),
    ("collection var state", """//@version=6
strategy("helper var collection")
f() =>
    var a = array.new<float>(3, 1.0)
    n = a.size()
    n * close
v = request.security(syminfo.tickerid, "60", nz(f()))
plot(v)
""", r"\bf_cs0\(\)"),
    ("a call in its own argument", """//@version=6
strategy("nested call")
f(x) => x * 3
v = request.security(syminfo.tickerid, "60", nz(f(f(close))))
plot(v)
""", r"\bf\(f\(bar\.close\)\)"),
    ("method TA history", """//@version=6
strategy("method TA history")
method m2(float x) =>
    s = ta.sma(x, 5)
    s - ta.sma(x, 5)[1]
v = request.security(syminfo.tickerid, "60", close.m2())
plot(v)
""", r"\b_udt_float_m2_cs0\(bar\.close\)"),
    ("method series TA index", """//@version=6
strategy("method series index")
method lagged(float x, int k) => ta.sma(x, 5)[k]
v = request.security(syminfo.tickerid, "60", close.lagged(bar_index % 3))
plot(v)
""", r"\b_udt_float_lagged_cs0\(bar\.close, "),
    ("chained method", """//@version=6
strategy("chained method")
method sc(float x, int n = 3) => x * n
v = request.security(syminfo.tickerid, "60", close.sc().sc())
plot(v)
""", r"\b_udt_float_sc\(_udt_float_sc\(bar\.close, 3\), 3\)"),
    ("method var initializer", """//@version=6
strategy("method var initializer")
g() =>
    var int c = 0
    c += 1
    c
method m1(float x) =>
    var float first = g()
    x + first
v = request.security(syminfo.tickerid, "60", close.m1())
plot(v)
"""
     , r"\b_udt_float_m1_cs0\(bar\.close\)"),
])
def test_calls_the_evaluator_refuses_keep_the_chart_call_and_warn(name, src, chart_call):
    # A bare payload refuses these helpers; under a builtin, or as a method
    # (which no earlier build inlined), each compiled with the chart call.
    # They keep it, with every argument on the chart, and say so.
    result = _transpiled(src)
    body = _eval_bodies(result["cpp"])
    assert re.search(chart_call, body), (name, body)
    assert any("on the chart's bar" in d.message for d in result["diagnostics"]), name
    compile_env.compile_cpp(result["cpp"], label=f"security-chart-call-{name.replace(' ', '-')}")


def test_a_global_and_its_history_stay_on_one_clock():
    # The builder does not re-evaluate a global under a builtin: its history
    # would still be the chart's (``mom - mom[1]`` read the requested bar
    # minus the chart's previous one). History the builder owns is the
    # requested TA's, never a requested value pushed on the chart's clock.
    src = """//@version=6
strategy("one clock")
mom = close - open
a = request.security(syminfo.tickerid, "D", nz(mom - mom[1]))
b = request.security(syminfo.tickerid, "D", nz(ta.sma(close, 5)[1]))
x = ta.sma(close, 5)
k = bar_index % 3
c = request.security(syminfo.tickerid, "D", nz(x[k]))
plot(a + b + c)
"""
    cpp = transpile(src)
    body = _eval_bodies(cpp)
    first = body[:body.index("void _eval_security_1(")]
    assert "mom[0] - mom[1]" in first and "bar.open" not in first
    second = body[body.index("void _eval_security_1("):body.index("void _eval_security_2(")]
    assert "_sec1__ta_sma_1_hist[0]" in second and "_hist_call" not in second
    compile_env.compile_cpp(cpp, label="security-one-clock")


def test_a_call_beside_a_chart_read_keeps_the_chart_call():
    # ``g[1]`` stays the chart series under a builtin, so the evaluator keeps
    # ``f(g)`` beside it the chart call; ``g`` itself is re-evaluated on the
    # requested bar under a builtin as in a bare payload (P6), so ``f(g) - g``
    # and ``f(g)`` alone are inlined.
    src = """//@version=6
strategy("call beside a global")
g = close - open
f(x) => x * 2
a = request.security(syminfo.tickerid, "60", nz(f(g) - g[1]))
b = request.security(syminfo.tickerid, "60", nz(f(g)))
c = request.security(syminfo.tickerid, "60", nz(f(g) - g))
plot(a + b + c)
"""
    result = _transpiled(src)
    body = _eval_bodies(result["cpp"])
    first = body[:body.index("void _eval_security_1(")]
    second = body[body.index("void _eval_security_1("):body.index("void _eval_security_2(")]
    third = body[body.index("void _eval_security_2("):]
    assert re.search(r"\bf\(g(\[0\])?\) - g\[1\]", first), first
    assert "((bar.close - bar.open) * 2)" in second, second
    assert "((bar.close - bar.open) * 2) - (bar.close - bar.open)" in third, third
    assert [d.message for d in result["diagnostics"]
            if "function 'f' on the chart's bar" in d.message], result["diagnostics"]
    compile_env.compile_cpp(result["cpp"], label="security-call-beside-global")


def test_a_refused_call_leaves_no_minted_site_behind():
    # Rendering an attempt mints ids and names (``math.random`` call sites
    # seed its generator); a refused attempt gives them back, so the ids stay
    # consecutive, as every earlier build numbered them.
    src = """//@version=6
strategy("refused attempt ids")
f() =>
    r = math.random(0, 1, 7)
    s = ta.sma(close, 5)
    r + s - ta.sma(close, 5)[1]
v = request.security(syminfo.tickerid, "60", nz(f()))
w = request.security(syminfo.tickerid, "60", math.random(0, 1, 42))
plot(v + w)
"""
    cpp = transpile(src)
    ids = sorted(int(m) for m in re.findall(r"pine_random\([^,]+, (\d+)u,", cpp))
    assert ids == list(range(len(ids))), ids
    compile_env.compile_cpp(cpp, label="security-refused-attempt-ids")


@pytest.mark.parametrize("declaration, read", [
    ("g = close - open", "g[1]"),
    # Per run, yet a requested context reads its own timeframe.
    ("tfm = timeframe.multiplier * 1", "tfm"),
    ("", "(session.isfirstbar ? 1 : 0)"),
    ("", "math.random(0, 1, 3)"),
])
def test_other_chart_reads_keep_the_builtin_on_the_chart(declaration, read):
    # Whatever else a builtin reads on the chart's terms keeps the evaluator's
    # calls on the chart too, as every earlier build lowered them.
    src = f"""//@version=6
strategy("chart read beside a call")
{declaration}
f(x) => x * 2
v = request.security(syminfo.tickerid, "60", nz(f(close) * {read}))
plot(v)
"""
    result = _transpiled(src)
    body = _eval_bodies(result["cpp"])
    assert re.search(r"\bf(_cs0)?\(bar\.close\)", body), body
    assert any("function 'f' on the chart's bar" in d.message
               for d in result["diagnostics"]), read
    compile_env.compile_cpp(result["cpp"], label="security-chart-read-beside-call")


def test_an_evaluator_mixing_bars_keeps_every_earlier_lowering():
    # A method inlined at the payload's top level beside a builtin that reads
    # the chart's ``g[1]`` would subtract the chart's value from a requested
    # one: the whole evaluator keeps the lowering every earlier build emitted.
    src = """//@version=6
strategy("mixed evaluator")
float g = close - open
method m(float x) => x * 2
v = request.security(syminfo.tickerid, "60", g.m() - nz(g[1]))
w = request.security(syminfo.tickerid, "60", g.m() - g)
plot(v + w)
"""
    result = _transpiled(src)
    body = _eval_bodies(result["cpp"])
    first = body[:body.index("void _eval_security_1(")]
    second = body[body.index("void _eval_security_1("):]
    assert re.search(r"_udt_float_m(_cs\d+)?\(g(\[0\])?\)", first), first
    assert "((bar.close - bar.open) * 2) - (bar.close - bar.open)" in second, second
    assert any("keeps its user calls under builtin calls and its methods on the chart's bar"
               in d.message for d in result["diagnostics"])
    compile_env.compile_cpp(result["cpp"], label="security-mixed-evaluator")


def test_ta_tuple_elements_read_their_field_of_the_requested_result():
    src = """//@version=6
strategy("TA tuple element")
[macdLine, signalLine, histLine] = ta.macd(close, 12, 26, 9)
macdBias() => macdLine > signalLine ? 1.0 : -1.0
v = request.security(syminfo.tickerid, "D", nz(macdBias()))
plot(v)
"""
    cpp = transpile(src)
    body = _eval_bodies(cpp)
    assert ".macd" in body and ".signal" in body, body
    compile_env.compile_cpp(cpp, label="security-ta-tuple-element")


@pytest.mark.parametrize("name, payload", [
    ("method beside the helper", "f() * 2 - f() + nz(close.m()) * 0 + bar_index * 0"),
    ("kept method", "f() * 2 - f() + nz(close.m()) * 0"),
    ("plain helper", "f() * 2 - f() + g()"),
])
def test_a_ta_site_a_helper_also_computes_is_computed_once(name, payload):
    # A multi-statement helper computes its TA where it is inlined; the same
    # site read elsewhere in the payload is computed once in the prologue, not
    # once per read (every earlier build fed the SMA twice per requested bar
    # for the plain helper).
    src = f"""//@version=6
strategy("TA computed once")
f() => ta.sma(close, 5)
g() =>
    y = close + f()
    y
method m(float x) =>
    y = x + f()
    y
v = request.security(syminfo.tickerid, "60", {payload})
plot(v)
"""
    cpp = transpile(src)
    body = _eval_bodies(cpp)
    prologue = re.findall(r"auto (_secval_\w+) = security_series_slot_is_new\(0\) \? (\w+)\.compute", body)
    assert prologue, body
    value, member = prologue[0]
    assert body.count(f"{member}.compute(") == 1, body
    assert f"(({value} * 2) - {value})" in body, body
    compile_env.compile_cpp(cpp, label=f"security-ta-once-{name.replace(' ', '-')}")


def test_a_method_ta_length_the_evaluator_cannot_reset_keeps_the_chart_call():
    # The method's length reads a local's history: no per-run value to
    # construct the requested TA with. Every earlier build called the method
    # on the chart, and still does.
    src = """//@version=6
strategy("method unstable length")
method sm(float x, int n) =>
    k = n * 2
    d = k[1]
    ta.sma(x, k) + nz(d)
v = request.security(syminfo.tickerid, "60", close.sm(5))
plot(v)
"""
    result = _transpiled(src)
    assert re.search(r"_req_sec_0 = _udt_float_sm(_cs\d+)?\(bar\.close, 5\);", _eval_bodies(result["cpp"]))
    assert any("calls method 'sm' on the chart's bar" in d.message for d in result["diagnostics"])
    compile_env.compile_cpp(result["cpp"], label="security-method-unstable-length")


@pytest.mark.parametrize("declaration, element", [
    ("pair() => [close, open]\n[a, b] = pair()", "a"),
    ("[a, b, c] = ta.macd(close, 12, 26, 9)", "a"),
    ('[a, b] = request.security(syminfo.tickerid, "D", [close, open])', "a"),
])
def test_tuple_element_history_is_refused_where_it_is_read(declaration, element):
    # Its value is the whole tuple: every earlier build emitted the tuple's
    # history, which did not compile.
    src = f"""//@version=6
strategy("tuple element history")
{declaration}
x = request.security(syminfo.tickerid, "60", {element}[1])
plot(x)
"""
    with pytest.raises(CompileError, match=rf"<input>:\d+:\d+: .*history of '{element}', "
                                           r"an element of a tuple declaration") as caught:
        transpile(src)
    assert "<input>:1:1" not in str(caught.value)


@pytest.mark.parametrize("declaration", [
    "[a, b, c] = ta.macd(close, 12, 26, 9)",
    "[a, b, c] = ta.bb(close, 20, 2)",
])
def test_ta_tuple_element_history_under_a_builtin_is_refused_too(declaration):
    # The expression visitor would read the chart's series (every earlier
    # build pushed the whole TA result into a double history, which did not
    # compile): refused where it is read, like the bare form.
    src = f"""//@version=6
strategy("tuple element history under a builtin")
{declaration}
x = request.security(syminfo.tickerid, "60", nz(a[1]))
plot(x)
"""
    with pytest.raises(CompileError, match=r"<input>:4:\d+: .*history of 'a', an element of a tuple"):
        transpile(src)


@pytest.mark.parametrize("helper, payload", [
    ("f2(x) =>\n    y = x + 1\n    y * 2", "nz(f2(a[1]))"),
    ("f() =>\n    y = nz(a[1])\n    y", "f()"),
])
def test_ta_tuple_element_history_an_earlier_build_compiled_keeps_the_chart_series(helper, payload):
    # A multi-statement helper reaches the tuple: every earlier build
    # computed it inside the helper and pushed no history for it, so the TU
    # compiled, reading the chart's series. Warned, not refused.
    src = f"""//@version=6
strategy("TA tuple element history through a helper")
[a, b, c] = ta.macd(close, 12, 26, 9)
{helper}
v = request.security(syminfo.tickerid, "60", {payload})
plot(v)
"""
    result = _transpiled(src)
    assert any("history of 'a', an element of a tuple declaration, on the chart's bar"
               in d.message for d in result["diagnostics"])
    compile_env.compile_cpp(result["cpp"], label="security-ta-tuple-history-helper")


@pytest.mark.parametrize("globals_, payload", [
    ("h = a[1]", "nz(h)"),
    ("h = a[1] * 2\nh2 = h + 1", "nz(h2)"),
])
def test_ta_tuple_element_history_through_a_global_is_refused(globals_, payload):
    # The visitor reads the global's chart value; every earlier build pushed
    # the whole TA result into a double history for it, which did not compile.
    src = f"""//@version=6
strategy("tuple element history through a global")
[a, b, c] = ta.bb(close, 20, 2)
{globals_}
x = request.security(syminfo.tickerid, "60", {payload})
plot(x)
"""
    with pytest.raises(CompileError, match=r"<input>:4:\d+: .*history of 'a', an element of a tuple"):
        transpile(src)


def test_user_tuple_element_history_through_a_global_keeps_the_chart_series():
    src = """//@version=6
strategy("user tuple element history through a global")
pair() => [close, open]
[a, b] = pair()
h = a[1]
x = request.security(syminfo.tickerid, "60", nz(h))
plot(x)
"""
    result = _transpiled(src)
    assert any("history of 'a', an element of a tuple declaration, on the chart's bar"
               in d.message for d in result["diagnostics"])
    compile_env.compile_cpp(result["cpp"], label="security-user-tuple-history-global")


def test_user_tuple_element_history_under_a_builtin_keeps_the_chart_series():
    src = """//@version=6
strategy("user tuple element history")
pair() => [close, open]
[a, b] = pair()
x = request.security(syminfo.tickerid, "60", nz(a[1]))
plot(x)
"""
    result = _transpiled(src)
    assert "_nz_v = (a[1])" in _eval_bodies(result["cpp"])
    assert any("history of 'a', an element of a tuple declaration, on the chart's bar"
               in d.message for d in result["diagnostics"])
    compile_env.compile_cpp(result["cpp"], label="security-user-tuple-history")


def test_a_call_reading_a_global_a_multi_statement_helper_computes_keeps_the_chart_call():
    # Inlining f0 would re-emit g0 -- and feed its SMA -- once more for the
    # global read beside it; f0 keeps the chart call, gv is computed once.
    src = """//@version=6
strategy("global computed by a helper")
g0(float x) =>
    y = ta.sma(x, 4) + 1
    y
gv = g0(close)
f0() => gv * 2
v = request.security(syminfo.tickerid, "60", nz(f0()) + gv)
plot(v)
"""
    result = _transpiled(src)
    body = _eval_bodies(result["cpp"])
    assert re.search(r"\bf0(_cs\d+)?\(\)", body), body
    assert len(re.findall(r"_ta_sma_\w+\.compute\(", body)) == 1, body
    assert any("calls function 'f0' on the chart's bar" in d.message for d in result["diagnostics"])
    compile_env.compile_cpp(result["cpp"], label="security-global-helper-value")
