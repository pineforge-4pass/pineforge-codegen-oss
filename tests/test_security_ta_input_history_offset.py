"""Requested-context TA history with immutable ``input.int`` offsets.

The TA value and its history must both live on the request.security clock.  A
runtime input offset is legal Pine v6, but it still needs the same declaration,
completion-gated push, and reset lifecycle as a literal positive offset.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
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


def _assert_checked_input_history_read(body: str) -> None:
    # Match the entire request assignment: the getter must feed the invoked
    # guard, whose result must feed the current/prior requested TA read.
    assignments = [
        line.strip() for line in body.splitlines()
        if line.lstrip().startswith("_req_sec_0 = ")
    ]
    assert len(assignments) == 1, assignments
    before_message = (
        "_req_sec_0 = ([&]() -> double { "
        "int _hidx = ([&]() -> int { "
        'auto _pf_history_offset = (get_input_int("Offset", 2)); '
        "using _pf_history_offset_type = "
        "std::decay_t<decltype(_pf_history_offset)>; "
        "if constexpr (!std::is_same_v<_pf_history_offset_type, bool>) { "
        "if (is_na(_pf_history_offset)) return na<int>(); } "
        'if (_pf_history_offset < 0) pine_runtime_error("'
    )
    after_message = (
        '"); '
        "if (static_cast<long double>(_pf_history_offset) >= 2147483648.0L) "
        "return na<int>(); "
        "return ([&](){ auto _pf_idx_v = (_pf_history_offset); "
        "using _pf_idx_t = std::decay_t<decltype(_pf_idx_v)>; "
        "if constexpr (std::is_same_v<_pf_idx_t, bool>) "
        "return (int)_pf_idx_v; "
        "else return is_na(_pf_idx_v) ? na<int>() : (int)_pf_idx_v; }()); }()); "
        "if (is_na(_hidx)) return na<double>(); "
        "return (_hidx == 0) ? _secval_0 : "
        "_sec0__ta_ema_1_hist[_hidx - 1]; }());"
    )
    # Only the authored location varies between these nine sources. Native
    # negative-offset witnesses separately pin the exact first-token location.
    message = (
        r"History offset must be non-negative at line [1-9][0-9]*, "
        r"column [1-9][0-9]* in <input>"
    )
    assert re.fullmatch(
        re.escape(before_message) + message + re.escape(after_message),
        assignments[0],
    ), assignments[0]
    assert body.count('get_input_int("Offset", 2)') == 1


def test_input_int_ta_offset_uses_complete_htf_history_and_reset():
    cpp = transpile(SOURCE)
    body = _eval_body(cpp)

    # A: admit the override-aware integer offset and lower 0/current vs past.
    _assert_checked_input_history_read(body)
    # Preserve the input's int-na sentinel before testing its sign; a genuine
    # negative must stop before narrowing or evaluating ``_hidx - 1``.
    assert "if (is_na(_hidx)) return na<double>();" in body
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
    _assert_checked_input_history_read(body)
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
    _assert_checked_input_history_read(body)
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
    _assert_checked_input_history_read(body)
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
    _assert_checked_input_history_read(body)
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
    _assert_checked_input_history_read(body)
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
    _assert_checked_input_history_read(body)
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
    _assert_checked_input_history_read(_eval_body(cpp))
    compile_cpp(cpp, label="security_keyword_udf_input_history_offset")


def test_containing_udf_mixed_callsites_get_a_request_each():
    # Each call's offset reaches a request of its own (security_contexts),
    # the input declared after the helper too: it was refused, as the
    # analyzer's one binding of the parameter cannot hold both.
    src = """//@version=6
strategy("mixed containing helper input offset")
requested(int idx) =>
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
k = input.int(2, "Offset")
x = requested(k)
y = requested(1)
plot(x + y)
"""
    cpp = transpile(src)
    _assert_checked_input_history_read(_eval_body(cpp))
    second = cpp[cpp.index("void _eval_security_1("):cpp.index("void evaluate_security(")]
    assert "_req_sec_1 = _sec1__ta_ema_2_hist[0];" in second
    assert 'get_input_int("Offset", 2)' not in second
    assert "int _hidx" not in second
    compile_cpp(cpp, label="security_mixed_udf_input_history_offset")


@pytest.mark.parametrize(
    "reassignment",
    ["idx := 1", "idx := int(close)"],
    ids=["literal-reassignment", "series-reassignment"],
)
def test_containing_udf_offset_rejects_parameter_reassignment(
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
    with pytest.raises(CompileError) as caught:
        transpile(src, filename="reassigned-offset.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2059"
    assert diagnostic.message == "Function parameter 'idx' cannot be reassigned."
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "reassigned-offset.pine"
    # Two header lines and the signature precede idx after four spaces.
    assert (diagnostic.location.line, diagnostic.location.col) == (4, 5)


def test_containing_udf_offset_rejects_parameter_reassignment_after_request():
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
    with pytest.raises(CompileError) as caught:
        transpile(src, filename="post-use-offset.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2059"
    assert diagnostic.message == "Function parameter 'idx' cannot be reassigned."
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "post-use-offset.pine"
    # The request occupies line 4; the following write has four leading spaces.
    assert (diagnostic.location.line, diagnostic.location.col) == (5, 5)


@pytest.mark.parametrize(
    "shadow",
    [
        "int idx = 1",
        "[idx, other] = if true\n            [1, 2]\n        else\n            [2, 1]",
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


def test_input_arithmetic_ta_offset_is_lowered_in_the_requested_context():
    # A bar-invariant int over an input is a legal Pine history offset
    # (tests/test_e2e_security_history_index.py replays TradingView's tape).
    src = """//@version=6
strategy("input arithmetic TA offset")
k = input.int(2, "Offset")
x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[k + 1])
plot(x)
"""
    cpp = transpile(src)
    body = _eval_body(cpp)
    assert 'get_input_int("Offset", 2) + 1' in body
    assert "_sec0__ta_ema_1_hist[_hidx - 1]" in body
    assert "_sec0__ta_ema_1_hist.push(_secval_0);" in body
    compile_cpp(cpp, label="security_input_arithmetic_history_offset")


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


# Check the same nine input-binding sources independently of the guarded
# initializer assertions, against separately compiled literal controls.
_NATIVE_INPUT_HISTORY_SOURCES = [
    pytest.param(SOURCE, (), id="direct"),
    pytest.param(
        """//@version=6
strategy("helper dynamic TA offset")
requestedEma(int idx) =>
    ta.ema(close, 5)[idx]
k = input.int(2, "Offset")
x = request.security(syminfo.tickerid, "60", requestedEma(k))
plot(x)
""",
        (),
        id="helper-parameter",
    ),
    pytest.param(
        """//@version=6
strategy("global alias lexical provenance")
k = input.int(2, "Offset")
offsetAlias = k
requestedEma(int k) =>
    ta.ema(close, 5)[offsetAlias]
x = request.security(syminfo.tickerid, "60", requestedEma(0))
plot(x)
""",
        (),
        id="global-alias-shadow",
    ),
    pytest.param(
        """//@version=6
strategy("global TA expression lexical provenance")
k = input.int(2, "Offset")
shifted = ta.ema(close, 5)[k]
requested(int k) =>
    shifted
x = request.security(syminfo.tickerid, "60", requested(0))
plot(x)
""",
        (),
        id="global-expression-shadow",
    ),
    pytest.param(
        """//@version=6
strategy("nested helper lexical provenance")
k = input.int(2, "Offset")
inner() =>
    ta.ema(close, 5)[k]
outer(int k) =>
    inner()
x = request.security(syminfo.tickerid, "60", outer(0))
plot(x)
""",
        (),
        id="nested-helper-shadow",
    ),
    pytest.param(
        """//@version=6
strategy("global TA alias helper-local offset")
v = ta.ema(close, 5)
requested(int idx) =>
    v[idx]
chartControl = requested(1)
k = input.int(2, "Offset")
x = request.security(syminfo.tickerid, "60", requested(k))
plot(x + chartControl)
""",
        (("chartControl", 4),),
        id="global-ta-helper-index",
    ),
    pytest.param(
        """//@version=6
strategy("containing helper input offset")
requested(int idx) =>
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
k = input.int(2, "Offset")
x = requested(k)
plot(x)
""",
        (),
        id="containing-helper",
    ),
    pytest.param(
        """//@version=6
strategy("keyword containing helper input offset")
requested(int idx) =>
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
k = input.int(2, "Offset")
x = requested(idx=k)
plot(x)
""",
        (),
        id="containing-helper-keyword",
    ),
    pytest.param(
        """//@version=6
strategy("mixed containing helper input offset")
requested(int idx) =>
    request.security(syminfo.tickerid, "60", ta.ema(close, 5)[idx])
k = input.int(2, "Offset")
x = requested(k)
y = requested(1)
plot(x + y)
""",
        (("y", 1),),
        id="containing-helper-mixed",
    ),
]


_LITERAL_INPUT_HISTORY_CONTROLS = """//@version=6
strategy("literal requested-history binding controls")
out0 = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[0], lookahead=barmerge.lookahead_off)
out1 = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[1], lookahead=barmerge.lookahead_off)
out2 = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[2], lookahead=barmerge.lookahead_off)
chart0 = ta.ema(close, 5)
chart1 = chart0[1]
chart2 = chart0[2]
plot(out0 + out1 + out2 + chart0 + chart1 + chart2)
"""


def _native_input_history_rows(source, fields, offsets=(None,)):
    """Compile once; observe real chart callbacks for each host input override.

    The source's 60-minute requests are unchanged. Sixty-four varying 15-minute
    bars supply sixteen requested bars, including four child evaluations per
    requested bar. This is native equivalence evidence, not a TradingView tape.
    """
    outputs = "".join(
        f' << " " << observed_value({field})' for field in fields
    )
    overrides = ", ".join(
        "nullptr" if offset is None else f'"{offset}"'
        for offset in offsets
    )
    driver = rf"""
#include <iomanip>
#include <iostream>
#include <vector>

class InputHistoryProbe : public GeneratedStrategy {{
public:
    int trial = 0;
    int seen = 0;

    template<typename T>
    static T observed_value(const T& value) {{
        return value;
    }}

    template<typename T>
    static T observed_value(const pineforge::Series<T>& value) {{
        return value.current();
    }}

    void on_source_bar(const Bar& bar) override {{
        GeneratedStrategy::on_source_bar(bar);
        std::cout << trial << " " << seen++{outputs} << '\n';
    }}
}};

int main() {{
    std::cout << std::setprecision(17);
    std::vector<Bar> bars;
    for (int i = 0; i < 64; ++i) {{
        const double close = 100.0 + i * (i + 1);
        bars.push_back(Bar{{close - 1.0, close + 1.0, close - 2.0,
                           close, 100.0, 1735689600000LL + i * 900000LL}});
    }}
    const char* offsets[] = {{{overrides}}};
    for (int trial = 0; trial < int(sizeof(offsets) / sizeof(offsets[0])); ++trial) {{
        InputHistoryProbe strategy;
        strategy.trial = trial;
        if (offsets[trial] != nullptr)
            strategy.set_input("Offset", offsets[trial]);
        strategy.run(bars.data(), int(bars.size()), "15", "15");
        if (!strategy.last_error().empty()) {{
            std::cerr << strategy.last_error() << '\n';
            return 1;
        }}
        if (strategy.seen != int(bars.size())) {{
            std::cerr << "incomplete chart observation: " << strategy.seen << '\n';
            return 2;
        }}
    }}
    return 0;
}}
"""
    rows = [
        line.split()
        for line in _compile_and_run(transpile(source) + driver).splitlines()
    ]
    assert len(rows) == 64 * len(offsets), rows
    for position, row in enumerate(rows):
        assert len(row) == len(fields) + 2, row
        assert (int(row[0]), int(row[1])) == divmod(position, 64), row
    return [
        [tuple(float(value) for value in row[2:])
         for row in rows[trial * 64:(trial + 1) * 64]]
        for trial in range(len(offsets))
    ]


def _assert_native_history_values(actual, expected):
    import math

    assert len(actual) == len(expected)
    for bar, (value, control) in enumerate(zip(actual, expected)):
        assert (math.isnan(value) and math.isnan(control)) or value == control, (
            bar, value, control,
        )


@pytest.fixture(scope="module")
def literal_input_history_controls():
    import math

    # Compiled independently of every input/helper source. The requested
    # offsets are literal AST nodes and never consume the Offset input.
    controls = _native_input_history_rows(
        _LITERAL_INPUT_HISTORY_CONTROLS,
        ("out0", "out1", "out2", "chart0", "chart1", "chart2"),
    )[0]
    tail = controls[32:]
    for column in range(6):
        values = [row[column] for row in tail]
        assert all(math.isfinite(value) for value in values), (column, values)
        assert len(set(values)) >= 4, (column, values)
    for row in tail:
        assert len(set(row[:3])) == 3, row
        assert all(row[offset] != row[offset + 3] for offset in range(3)), row
    for offset in range(3):
        changes = [
            bar for bar in range(33, 64)
            if controls[bar][offset] != controls[bar - 1][offset]
        ]
        # A requested history advances less often than its chart-clock
        # counterpart. Finite, changing, distinct controls rule out all-na,
        # ignored-offset and wrong-clock comparisons passing vacuously.
        assert 4 <= len(changes) <= 8, (offset, changes)
    return controls


@pytest.mark.parametrize("source,companions", _NATIVE_INPUT_HISTORY_SOURCES)
def test_native_input_history_binding_matches_independent_literal_controls(
    source, companions, literal_input_history_controls,
):
    offsets = (None, 0, 1, 2)
    fields = ("x",) + tuple(name for name, _ in companions)
    runs = _native_input_history_rows(source, fields, offsets)
    for override, rows in zip(offsets, runs):
        offset = 2 if override is None else override
        _assert_native_history_values(
            [row[0] for row in rows],
            [row[offset] for row in literal_input_history_controls],
        )
        # The mixed helper's literal request and the global TA's chart
        # request must keep their own binding when Offset changes.
        for column, (_, control_column) in enumerate(companions, start=1):
            _assert_native_history_values(
                [row[column] for row in rows],
                [row[control_column] for row in literal_input_history_controls],
            )
    _assert_native_history_values(
        [row[0] for row in runs[0]],
        [row[0] for row in runs[3]],
    )
    for left, right in ((1, 2), (1, 3), (2, 3)):
        assert any(
            runs[left][bar][0] != runs[right][bar][0]
            for bar in range(32, 64)
        ), (offsets[left], offsets[right], runs[left], runs[right])
