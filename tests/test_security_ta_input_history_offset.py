"""Requested-context TA history with immutable ``input.int`` offsets.

The TA value and its history must both live on the request.security clock.  A
runtime input offset is legal Pine v6, but it still needs the same declaration,
completion-gated push, and reset lifecycle as a literal positive offset.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests.test_security_helper_local_var import _compile_and_run


SOURCE = """//@version=6
strategy("security dynamic TA offset")
k = input.int(2, "Offset")
x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[k], lookahead=barmerge.lookahead_off)
if not na(x) and close > x
    strategy.entry("L", strategy.long)
"""


def _eval_body(cpp: str) -> str:
    start = cpp.index("void _eval_security_0(")
    end = cpp.index("void evaluate_security(", start)
    return cpp[start:end]


def test_input_int_ta_offset_uses_complete_htf_history_and_reset():
    cpp = transpile(SOURCE)
    body = _eval_body(cpp)

    # A: admit the override-aware integer offset and lower 0/current vs past.
    assert 'int _hidx = (int)(get_input_int("Offset", 2));' in body
    # get_input_int can represent Pine's int-na sentinel (INT_MIN) through a
    # host override, so guard it explicitly before ``_hidx - 1`` can overflow.
    assert "if (is_na(_hidx) || _hidx < 0) return na<double>();" in body
    assert "return (_hidx == 0) ? _secval_0 : _sec0__ta_ema_1_hist[_hidx - 1];" in body

    # Existing invariant: read prior history before one completed HTF value is
    # committed.  A chart-bar/tick clock would silently shift every offset.
    assign = body.index("_req_sec_0 = ([&]() -> double")
    guard = body.index("if (is_complete) {", assign)
    push = body.index("_sec0__ta_ema_1_hist.push(_secval_0);", guard)
    assert assign < guard < push
    assert "is_first_tick_" not in body
    assert "_hist_call" not in body

    # B: direct dynamic-site registration owns declared storage and reset. The
    # bare constructor intentionally retains the engine's finite default depth
    # (500); a source max_bars_back directive is covered separately below.
    assert "Series<double> _sec0__ta_ema_1_hist;" in cpp
    assert "_sec0__ta_ema_1_hist.clear();" in cpp


def test_input_int_ta_offset_cpp_compiles_against_engine_headers():
    compile_cpp(transpile(SOURCE), label="security_ta_input_history_offset")


def test_dynamic_offsets_match_literal_offsets_on_requested_context_clock():
    src = """//@version=6
strategy("dynamic versus literal requested-context offsets")
k0 = input.int(0, "K0")
k1 = input.int(1, "K1")
k2 = input.int(2, "K2")
d0 = request.security(syminfo.tickerid, "2", ta.ema(close, 5)[k0])
l0 = request.security(syminfo.tickerid, "2", ta.ema(close, 5)[0])
d1 = request.security(syminfo.tickerid, "2", ta.ema(close, 5)[k1])
l1 = request.security(syminfo.tickerid, "2", ta.ema(close, 5)[1])
d2 = request.security(syminfo.tickerid, "2", ta.ema(close, 5)[k2])
l2 = request.security(syminfo.tickerid, "2", ta.ema(close, 5)[2])
outD0 = d0
outL0 = l0
outD1 = d1
outL1 = l1
outD2 = d2
outL2 = l2
plot(outD0)
"""
    driver = r"""
#include <iomanip>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},
        Bar{4.0, 4.0, 4.0, 4.0, 1.0, 120000},
        Bar{8.0, 8.0, 8.0, 8.0, 1.0, 180000},
        Bar{16.0, 16.0, 16.0, 16.0, 1.0, 240000},
        Bar{32.0, 32.0, 32.0, 32.0, 1.0, 300000},
        Bar{64.0, 64.0, 64.0, 64.0, 1.0, 360000},
        Bar{128.0, 128.0, 128.0, 128.0, 1.0, 420000},
    };
    strategy.run(bars, 8, "1", "1");
    std::cout << std::setprecision(17)
              << strategy.outD0 << " " << strategy.outL0 << " "
              << strategy.outD1 << " " << strategy.outL1 << " "
              << strategy.outD2 << " " << strategy.outL2 << "\n";
    return 0;
}
"""
    values = tuple(
        float(value)
        for value in _compile_and_run(transpile(src) + driver).split()
    )
    assert values[0] == values[1]
    assert values[2] == values[3]
    assert values[4] == values[5]


def test_input_int_ta_offset_honors_explicit_max_bars_back_over_500():
    cpp = transpile(SOURCE.replace(
        'strategy("security dynamic TA offset")',
        'strategy("security dynamic TA offset", max_bars_back=2048)',
    ))
    assert "Series<double> _sec0__ta_ema_1_hist{2048};" in cpp


def test_input_int_ta_offset_follows_immutable_alias_chain():
    cpp = transpile(SOURCE.replace(
        'x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[k],',
        'offsetAlias = k\n'
        'x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[offsetAlias],',
    ))
    body = _eval_body(cpp)
    assert 'get_input_int("Offset", 2)' in body
    assert "_sec0__ta_ema_1_hist[_hidx - 1]" in body


def test_input_int_ta_offset_inside_helper_gets_full_history_lifecycle():
    # A+B+C compose: runtime admission, dynamic registration, and helper reach.
    src = """//@version=6
strategy("helper dynamic TA offset")
requestedEma(int idx) =>
    ta.ema(close, 5)[idx]
k = input.int(2, "Offset")
x = request.security(syminfo.tickerid, "60", requestedEma(k))
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert "Series<double> _sec0__ta_ema_1_hist;" in cpp
    assert 'int _hidx = (int)(get_input_int("Offset", 2));' in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body
    assert "_sec0__ta_ema_1_hist.clear();" in cpp


def test_helper_literal_ta_offset_gets_full_history_lifecycle_and_compiles():
    # C is independently useful even with A/B off: the old declaration prepass
    # did not enter helper bodies, so this already-supported literal form
    # transpiled to an undeclared ``_hist`` member and failed native compile.
    src = """//@version=6
strategy("helper literal TA offset")
requestedEma() =>
    ta.ema(close, 5)[1]
x = request.security(syminfo.tickerid, "60", requestedEma())
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert "Series<double> _sec0__ta_ema_1_hist;" in cpp
    assert "_req_sec_0 = _sec0__ta_ema_1_hist[0];" in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body
    assert "_sec0__ta_ema_1_hist.clear();" in cpp
    compile_cpp(cpp, label="security_helper_literal_ta_history_offset")


def test_input_int_ta_offset_through_global_expression_alias_gets_storage():
    src = """//@version=6
strategy("global expression dynamic TA offset")
k = input.int(2, "Offset")
shifted = ta.ema(close, 5)[k]
x = request.security(syminfo.tickerid, "60", shifted)
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert "Series<double> _sec0__ta_ema_1_hist;" in cpp
    assert "_sec0__ta_ema_1_hist[_hidx - 1]" in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body


def test_helper_literal_zero_does_not_allocate_unused_history():
    src = """//@version=6
strategy("helper zero TA offset")
requestedEma(int idx) =>
    ta.ema(close, 5)[idx]
x = request.security(syminfo.tickerid, "60", requestedEma(0))
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert "_req_sec_0 = _secval_0;" in body
    assert "_sec0__ta_ema_1_hist" not in cpp


def test_global_alias_is_not_captured_by_same_named_helper_parameter():
    src = """//@version=6
strategy("global alias lexical provenance")
k = input.int(2, "Offset")
offsetAlias = k
requestedEma(int k) =>
    ta.ema(close, 5)[offsetAlias]
x = request.security(syminfo.tickerid, "60", requestedEma(0))
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert 'int _hidx = (int)(get_input_int("Offset", 2));' in body
    assert "_sec0__ta_ema_1_hist[_hidx - 1]" in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body


def test_global_ta_expression_is_not_captured_by_helper_parameter():
    src = """//@version=6
strategy("global TA expression lexical provenance")
k = input.int(2, "Offset")
shifted = ta.ema(close, 5)[k]
requested(int k) =>
    shifted
x = request.security(syminfo.tickerid, "60", requested(0))
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert 'int _hidx = (int)(get_input_int("Offset", 2));' in body
    assert "_sec0__ta_ema_1_hist[_hidx - 1]" in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body
    compile_cpp(cpp, label="security_global_ta_expr_lexical_provenance")


def test_nested_helper_global_input_is_not_captured_by_outer_parameter():
    src = """//@version=6
strategy("nested helper lexical provenance")
k = input.int(2, "Offset")
inner() =>
    ta.ema(close, 5)[k]
outer(int k) =>
    inner()
x = request.security(syminfo.tickerid, "60", outer(0))
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert 'int _hidx = (int)(get_input_int("Offset", 2));' in body
    assert "_sec0__ta_ema_1_hist[_hidx - 1]" in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body
    compile_cpp(cpp, label="security_nested_helper_global_input_provenance")


def test_nested_helper_global_ta_series_is_not_captured_by_outer_parameter():
    src = """//@version=6
strategy("nested helper global TA series provenance")
v = ta.ema(close, 5)
inner() =>
    v[1]
outer(float v) =>
    inner()
x = request.security(syminfo.tickerid, "60", outer(close))
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert "_req_sec_0 = _sec0__ta_ema_1_hist[0];" in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body
    assert "_req_sec_0 = _sec_close_hist_0[0];" not in body
    compile_cpp(cpp, label="security_nested_helper_global_ta_provenance")


def test_global_ta_alias_keeps_helper_local_index_lifecycle():
    src = """//@version=6
strategy("global TA alias helper-local offset")
v = ta.ema(close, 5)
requested(int idx) =>
    v[idx]
chartControl = requested(1)
k = input.int(2, "Offset")
x = request.security(syminfo.tickerid, "60", requested(k))
plot(x + chartControl)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert 'int _hidx = (int)(get_input_int("Offset", 2));' in body
    assert "_sec0__ta_ema_1_hist[_hidx - 1]" in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body
    compile_cpp(cpp, label="security_global_ta_alias_helper_local_offset")


def test_nested_helper_global_scalar_is_not_captured_by_outer_parameter():
    src = """//@version=6
strategy("nested helper global scalar provenance")
g = 7
inner() =>
    g
outer(float g) =>
    inner()
x = request.security(syminfo.tickerid, "60", outer(close))
plot(x)
"""
    body = _eval_body(transpile(src))
    assert "_req_sec_0 = 7;" in body
    assert "_req_sec_0 = bar.close;" not in body


def test_containing_udf_parameter_cannot_capture_same_named_global_input():
    src = """//@version=6
strategy("containing helper parameter shadow")
k = input.int(2, "Global offset")
requested(int k) =>
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[k])
x = requested(bar_index)
plot(x)
"""
    with pytest.raises(
        CompileError,
        match=r"TA history index must be a literal integer",
    ):
        transpile(src)


def test_containing_udf_parameter_resolves_unique_input_callsite():
    src = """//@version=6
strategy("containing helper input offset")
requested(int idx) =>
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
k = input.int(2, "Offset")
x = requested(k)
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert 'int _hidx = (int)(get_input_int("Offset", 2));' in body
    assert "_sec0__ta_ema_1_hist[_hidx - 1]" in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body
    compile_cpp(cpp, label="security_containing_udf_input_history_offset")


def test_containing_udf_parameter_resolves_uniform_input_callsites():
    src = """//@version=6
strategy("uniform containing helper input offset")
requested(int idx) =>
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
k = input.int(2, "Offset")
x = requested(k)
y = requested(k)
plot(x + y)
"""
    cpp = transpile(src)
    assert cpp.count('get_input_int("Offset", 2)') >= 2
    assert "_sec0__ta_ema_1_hist[_hidx - 1]" in cpp
    compile_cpp(cpp, label="security_uniform_udf_input_history_offset")


def test_containing_udf_parameter_resolves_keyword_input_callsite():
    src = """//@version=6
strategy("keyword containing helper input offset")
requested(int idx) =>
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
k = input.int(2, "Offset")
x = requested(idx=k)
plot(x)
"""
    cpp = transpile(src)
    assert 'int _hidx = (int)(get_input_int("Offset", 2));' in _eval_body(cpp)
    compile_cpp(cpp, label="security_keyword_udf_input_history_offset")


def test_containing_udf_mixed_callsites_fail_closed():
    src = """//@version=6
strategy("mixed containing helper input offset")
requested(int idx) =>
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
k = input.int(2, "Offset")
x = requested(k)
y = requested(1)
plot(x + y)
"""
    with pytest.raises(
        CompileError,
        match=r"TA history index must be a literal integer",
    ):
        transpile(src)


@pytest.mark.parametrize(
    "reassignment",
    ["idx := 1", "idx := int(close)"],
    ids=["literal-reassignment", "series-reassignment"],
)
def test_containing_udf_parameter_reassignment_fails_closed(
    reassignment: str,
):
    src = f'''//@version=6
strategy("reassigned containing helper offset")
requested(int idx) =>
    {reassignment}
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
k = input.int(2, "Offset")
x = requested(k)
plot(x)
'''
    with pytest.raises(
        CompileError,
        match=r"TA history index must be a literal integer",
    ):
        transpile(src)


def test_containing_udf_parameter_reassignment_after_request_fails_closed():
    # Conservative on purpose: the parameter has a function-local lifecycle,
    # so call-site substitution is disabled once any write exists, even after
    # the request expression in authored statement order.
    src = """//@version=6
strategy("post-use reassigned containing helper offset")
requested(int idx) =>
    out = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
    idx := 1
    out
k = input.int(2, "Offset")
x = requested(k)
plot(x)
"""
    with pytest.raises(
        CompileError,
        match=r"TA history index must be a literal integer",
    ):
        transpile(src)


@pytest.mark.parametrize(
    "shadow",
    [
        "int idx = 1",
        "[idx, other] = [1, 2]",
    ],
    ids=["block-local", "tuple-local"],
)
def test_containing_udf_parameter_local_shadow_fails_closed(shadow: str):
    src = f'''//@version=6
strategy("shadowed containing helper offset")
requested(int idx) =>
    float out = na
    if close > open
        {shadow}
        out := request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
    out
k = input.int(2, "Offset")
x = requested(k)
plot(x)
'''
    with pytest.raises(
        CompileError,
        match=r"TA history index must be a literal integer",
    ):
        transpile(src)


@pytest.mark.parametrize(
    "body",
    [
        '''if close > open
    k = bar_index
    x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[k])''',
        '''for k = 0 to 1
    x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[k])''',
    ],
    ids=["block-local", "loop-iterator"],
)
def test_local_shadow_cannot_capture_same_named_global_input(body: str):
    src = f'''//@version=6
strategy("local offset shadow")
k = input.int(2, "Global offset")
{body}
'''
    with pytest.raises(
        CompileError,
        match=r"TA history index must be a literal integer",
    ):
        transpile(src)


@pytest.mark.parametrize("index", ["idx", "1"], ids=["dynamic", "literal"])
def test_multistatement_helper_ta_history_remains_loudly_fail_closed(index: str):
    src = f'''//@version=6
strategy("linear helper TA history")
requestedEma(int idx) =>
    unrelated = 1
    ta.ema(close, 5)[{index}]
k = input.int(2, "Offset")
x = request.security(syminfo.tickerid, "60", requestedEma(k))
plot(x)
'''
    with pytest.raises(
        CompileError,
        match=r"multi-statement helper TA history is not supported",
    ):
        transpile(src)


@pytest.mark.parametrize(
    "declaration,index",
    [
        ("", "bar_index"),
        ('k = input.float(2.0, "Offset")', "k"),
        ('var int k = input.int(2, "Offset")', "k"),
        ('k = input.int(2, "Offset")\nk := 3', "k"),
        ('k = input.int(2, "Offset")', "k + 1"),
    ],
)
def test_nonliteral_ta_offset_remains_fail_closed(declaration: str, index: str):
    src = f'''//@version=6
strategy("unsupported dynamic TA offset")
{declaration}
x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[{index}])
plot(x)
'''
    with pytest.raises(
        CompileError,
        match=r"TA history index must be a literal integer",
    ):
        transpile(src)


def test_negative_literal_ta_offset_is_rejected_loudly():
    # D: fail closed before ``idx_lit - 1`` emits an undeclared negative read.
    src = """//@version=6
strategy("negative TA offset")
x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[-1])
plot(x)
"""
    with pytest.raises(CompileError, match="must be non-negative"):
        transpile(src)


def test_excellent_control_keeps_literal_security_ema_path():
    # Source-faithful core of Concordance Execution Mandate [JOAT], whose
    # idxHigher folds to 0 in the requested-context barstate approximation.
    src = """//@version=6
strategy("Concordance control")
f_secure_ema(string tf, int length, int idxHigher, int idxCurrent) =>
    request.security(syminfo.tickerid, tf, ta.ema(close, length)[idxHigher])[idxCurrent]
idxHigher = barstate.isrealtime ? 1 : 0
idxCurrent = barstate.isrealtime ? 0 : 1
primaryTf = input.timeframe("240", "Primary Bias Timeframe")
crossFastLen = input.int(21, "Crossframe Fast EMA", minval=2, maxval=100)
primaryFast = f_secure_ema(primaryTf, crossFastLen, idxHigher, idxCurrent)
plot(primaryFast)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert "_req_sec_0 = _secval_0;" in body
    assert "_sec0__ta_ema_1_hist" not in cpp
    assert "int _hidx" not in body
