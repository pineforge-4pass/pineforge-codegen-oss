"""Top-level lazy-edge ``ta.*`` sites advance every bar (TradingView rule).

Pinned 2026-09-03 with ``lab tv`` on NYSE:F 1D (campaign scratch tapes
``out-pin-ring-lazyand`` / ``out-pin-lazyand-sma`` / ``out-pin-lazyand-ema`` /
``out-pin-ring-ternary``; feed ``lab bars NYSE:F 1D --around 2025-10-15``):

* ``c = bar_index % 7 == 3 and close > ta.highest(high, 5)[1]`` -- the
  every-bar model reproduces TV's 9/9 entries; the per-call model is wrong on
  8 of them.
* ``... and close > ta.sma(close, 5)[1]`` -- every-bar 25/25 (per-call 28).
* ``... and close > ta.ema(close, 5)[1]`` -- every-bar 23/23 (per-call 27).
* ``v = bar_index % 7 == 3 ? ta.highest(high, 5)[1] : na`` -- every-bar 38/39
  (ring / previous-execution model 2/39).

Rule: a stateful ``ta.*`` call inside ANY expression operand of a top-level
statement -- the RHS of a Pine-v6 lazy ``and``/``or``, either ternary arm,
nested comparisons -- is evaluated on EVERY bar. Short-circuiting and branch
selection gate only the value, never the built-in's state, and ``[1]`` on such
a call is the previous BAR's value. A stateful call inside an ``if`` / local
block / function body that does not execute every bar stays execution-gated
(pinned separately; the in-block compute + ``_hist_call_*`` push is untouched).

Production instance: robmagnaye14 ``bullMSS = setupAlive and dir == 1 and
close > ta.highest(high, 10)[1]`` (+ BOS / bear variants), which diverged on
~12 lanes because the engine's window spanned setups days apart.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env


_HEADER = (
    '//@version=6\n'
    'strategy("lazy-edge", initial_capital=1000000000, pyramiding=1, '
    'default_qty_type=strategy.fixed, default_qty_value=1)\n'
)


def _cpp(body: str) -> str:
    return transpile(_HEADER + body + "\n")


def _on_bar(cpp: str) -> str:
    return cpp.split("void on_bar(", 1)[1].split("\n    }\n", 1)[0]


def _lines(cpp: str) -> list[str]:
    return cpp.splitlines()


def _stmt(cpp: str, prefix: str) -> str:
    return next(ln for ln in _lines(cpp) if ln.strip().startswith(prefix))


def _hoist(cpp: str, n: int) -> str:
    return _stmt(cpp, f"const auto _pf_every_bar_ta_{n} = ")


def _index(cpp: str, line: str) -> int:
    return _lines(cpp).index(line)


# ---------------------------------------------------------------------------
# The four lab tv pins (verbatim strategy.pine bodies) + the production shape
# ---------------------------------------------------------------------------

PIN_RING_LAZYAND = (
    "// v6 lazy `and`: the RHS ta.highest is only evaluated on bars where the LHS holds\n"
    "c = bar_index % 7 == 3 and close > ta.highest(high, 5)[1]\n"
    "if c\n"
    "    strategy.entry(\"L\", strategy.long)\n"
    "if bar_index % 7 == 4\n"
    "    strategy.close(\"L\")"
)
PIN_LAZYAND_SMA = (
    "c = bar_index % 7 == 3 and close > ta.sma(close, 5)[1]\n"
    "if c\n"
    "    strategy.entry(\"L\", strategy.long)\n"
    "if bar_index % 7 == 4\n"
    "    strategy.close(\"L\")"
)
PIN_LAZYAND_EMA = (
    "c = bar_index % 7 == 3 and close > ta.ema(close, 5)[1]\n"
    "if c\n"
    "    strategy.entry(\"L\", strategy.long)\n"
    "if bar_index % 7 == 4\n"
    "    strategy.close(\"L\")"
)
PIN_RING_TERNARY = (
    "v = bar_index % 7 == 3 ? ta.highest(high, 5)[1] : na\n"
    "if not na(v)\n"
    "    strategy.entry(\"L\", strategy.long, qty=math.round(v*100))\n"
    "if bar_index % 7 == 4\n"
    "    strategy.close(\"L\")"
)
ROBMAGNAYE_SHAPE = (
    "var bool setupAlive = false\n"
    "var int dir = 0\n"
    "var bool mssFound = false\n"
    "if bar_index % 5 == 0\n"
    "    setupAlive := true\n"
    "    dir := close > open ? 1 : -1\n"
    "bullMSS = setupAlive and dir == 1 and close > ta.highest(high, 10)[1]\n"
    "bearMSS = setupAlive and dir == -1 and close < ta.lowest(low, 10)[1]\n"
    "if bullMSS or bearMSS\n"
    "    mssFound := true\n"
    "bullBOS = setupAlive and dir == 1 and mssFound and close > ta.highest(high, 20)[1]\n"
    "bearBOS = setupAlive and dir == -1 and mssFound and close < ta.lowest(low, 20)[1]\n"
    "if bullBOS or bearBOS\n"
    "    strategy.entry(\"L\", strategy.long)\n"
    "    mssFound := false"
)


def _assert_every_bar_history_read(cpp: str, n: int, member: str, var: str,
                                   ta_member: str, compute_arg: str,
                                   offset: int = 1) -> None:
    """The lazy-edge ``ta.x(...)[k]`` shape: compute + push before, read in."""
    hoist = _hoist(cpp, n)
    push = f"        if (history_advances_new_bar()) {member}.push(_pf_every_bar_ta_{n});"
    update = f"        else {member}.update(_pf_every_bar_ta_{n});"
    stmt = _stmt(cpp, f"{var} = (")
    lines = _lines(cpp)
    assert hoist.startswith("        const auto"), hoist
    assert f"{ta_member}.compute({compute_arg})" in hoist
    assert f"{ta_member}.recompute({compute_arg})" in hoist
    assert push in lines and update in lines
    assert _index(cpp, hoist) < lines.index(push) < lines.index(update) < _index(cpp, stmt)
    assert f"{member}[(int)({offset})]" in stmt
    assert ".compute(" not in stmt and ".push(" not in stmt
    # Exactly one step of the indicator per bar, in the hoist only.
    on_bar = _on_bar(cpp)
    assert on_bar.count(f"{ta_member}.compute(") == 1
    assert on_bar.count(f"{member}.push(") == 1


def test_pin_ring_lazyand_highest_advances_every_bar():
    cpp = _cpp(PIN_RING_LAZYAND)
    _assert_every_bar_history_read(
        cpp, 1, "_hist_call_1", "c", "_ta_highest_1", "current_bar_.high"
    )
    c_line = _stmt(cpp, "c = (")
    assert "&&" in c_line and "_hist_call_1[(int)(1)]" in c_line


def test_pin_lazyand_sma_advances_every_bar():
    cpp = _cpp(PIN_LAZYAND_SMA)
    _assert_every_bar_history_read(
        cpp, 1, "_hist_call_1", "c", "_ta_sma_1", "current_bar_.close"
    )


def test_pin_lazyand_ema_advances_every_bar():
    cpp = _cpp(PIN_LAZYAND_EMA)
    _assert_every_bar_history_read(
        cpp, 1, "_hist_call_1", "c", "_ta_ema_1", "current_bar_.close"
    )


def test_pin_ring_ternary_highest_advances_every_bar():
    cpp = _cpp(PIN_RING_TERNARY)
    _assert_every_bar_history_read(
        cpp, 1, "_hist_call_1", "v", "_ta_highest_1", "current_bar_.high"
    )
    v_line = _stmt(cpp, "v = (")
    assert "? (_hist_call_1[(int)(1)]) : (na<double>())" in v_line


def test_robmagnaye_mss_bos_windows_advance_every_bar():
    cpp = _cpp(ROBMAGNAYE_SHAPE)
    _assert_every_bar_history_read(
        cpp, 1, "_hist_call_1", "bullMSS", "_ta_highest_1", "current_bar_.high"
    )
    _assert_every_bar_history_read(
        cpp, 2, "_hist_call_2", "bearMSS", "_ta_lowest_2", "current_bar_.low"
    )
    _assert_every_bar_history_read(
        cpp, 3, "_hist_call_3", "bullBOS", "_ta_highest_3", "current_bar_.high"
    )
    _assert_every_bar_history_read(
        cpp, 4, "_hist_call_4", "bearBOS", "_ta_lowest_4", "current_bar_.low"
    )
    # Each hoist sits directly before its own statement, after the setup block.
    lines = _lines(cpp)
    setup_close = next(
        i for i, ln in enumerate(lines) if ln.strip().startswith("dir = ")
    )
    assert setup_close < _index(cpp, _hoist(cpp, 1)) < _index(cpp, _stmt(cpp, "bullMSS = ("))
    assert _index(cpp, _stmt(cpp, "bullMSS = (")) < _index(cpp, _hoist(cpp, 2))


def test_hoist_is_independent_of_run_mode():
    """Both ``_use_precalc`` branches live in the hoist: dynamic mode (bar
    magnifier / input_tf / script_tf, ``_use_precalc = false``) steps the
    indicator inline every bar; static mode reads the equivalent precalc."""
    cpp = _cpp(PIN_RING_LAZYAND)
    hoist = _hoist(cpp, 1)
    assert "_use_precalc ? _precalc__ta_highest_1[bar_index_] :" in hoist
    assert "history_advances_new_bar() ? _ta_highest_1.compute(current_bar_.high)" in hoist
    assert "if (needs_dynamic) {\n            _use_precalc = false;" in cpp


# ---------------------------------------------------------------------------
# Shapes: or-RHS, ternary arms, nesting depth, if conditions, expression stmts
# ---------------------------------------------------------------------------

def test_or_rhs_and_not_and_nested_comparison_depth():
    cpp = _cpp(
        "pred = close > open\n"
        "a = pred or close > ta.sma(close, 5)\n"
        "b = not (pred and (close > open or ta.lowest(low, 14) < close))\n"
        "c = pred and (bar_index > 5 and (close - ta.ema(close, 9)) > 0)\n"
        "plot(a or b or c ? 1 : 0)"
    )
    for n, member, arg in (
        (1, "_ta_sma_1", "current_bar_.close"),
        (2, "_ta_lowest_2", "current_bar_.low"),
        (3, "_ta_ema_3", "current_bar_.close"),
    ):
        assert f"{member}.compute({arg})" in _hoist(cpp, n)
    for var, n in (("a", 1), ("b", 2), ("c", 3)):
        line = _stmt(cpp, f"{var} = ")
        assert f"_pf_every_bar_ta_{n}" in line and ".compute(" not in line
    assert _stmt(cpp, "b = ").strip().startswith("b = !((pred &&")


def test_ternary_both_arms_and_condition_stays_eager():
    cpp = _cpp(
        "pred = close > open\n"
        "x = pred ? ta.sma(close, 5) : ta.ema(close, 5)\n"
        "y = ta.rsi(close, 14) > 50 ? close : open\n"
        "plot(x + y)"
    )
    assert "_ta_sma_1.compute" in _hoist(cpp, 1)
    assert "_ta_ema_2.compute" in _hoist(cpp, 2)
    x_line = _stmt(cpp, "x = (")
    assert "_pf_every_bar_ta_1" in x_line and "_pf_every_bar_ta_2" in x_line
    # The ternary condition is an eager operand: evaluated every bar already.
    y_line = _stmt(cpp, "y = (")
    assert "_ta_rsi_3.compute" in y_line and "_pf_every_bar_ta_" not in y_line
    assert len(re.findall(r"const auto _pf_every_bar_ta_\d+ = ", cpp)) == 2


def test_nested_ta_hoists_inner_before_outer_and_history_push_in_between():
    cpp = _cpp(
        "pred = close > open\n"
        "s = pred and ta.sma(ta.highest(high, 5)[1], 3) > close\n"
        "plot(s ? 1 : 0)"
    )
    inner = _hoist(cpp, 1)
    outer = _hoist(cpp, 2)
    push = "        if (history_advances_new_bar()) _hist_call_1.push(_pf_every_bar_ta_1);"
    assert "_ta_highest_1.compute(current_bar_.high)" in inner
    assert "_ta_sma_2.compute(_hist_call_1[(int)(1)])" in outer
    lines = _lines(cpp)
    assert _index(cpp, inner) < lines.index(push) < _index(cpp, outer) < _index(cpp, _stmt(cpp, "s = ("))
    assert "_pf_every_bar_ta_2" in _stmt(cpp, "s = (")


def test_top_level_if_condition_hoists_but_else_if_condition_does_not():
    cpp = _cpp(
        "pred = close > open\n"
        "if pred and close > ta.highest(high, 5)[1]\n"
        "    strategy.entry(\"L\", strategy.long)\n"
        "else if pred and close < ta.lowest(low, 5)[1]\n"
        "    strategy.close(\"L\")"
    )
    hoist = _hoist(cpp, 1)
    assert "_ta_highest_1.compute(current_bar_.high)" in hoist
    conds = [ln for ln in _lines(cpp) if ln.strip().startswith("if ((pred &&")]
    assert len(conds) == 2
    if_line, else_if = conds
    assert "_hist_call_1[(int)(1)]" in if_line and ".compute(" not in if_line
    assert _index(cpp, hoist) < _index(cpp, if_line)
    # ``else if`` is an ``if`` inside the else local block: execution-gated,
    # so it keeps the in-block compute + call-local history push.
    assert _lines(cpp)[_index(cpp, else_if) - 1].strip() == "} else"
    assert "_ta_lowest_2.compute(current_bar_.low)" in else_if
    assert "_hist_call_2.push(_hv)" in else_if
    assert "_pf_every_bar_ta_2" not in cpp


def test_expression_statement_and_strategy_call_arguments_hoist():
    cpp = _cpp(
        "pred = close > open\n"
        "plot(pred ? ta.sma(close, 5) : na)\n"
        "if pred\n"
        "    strategy.entry(\"L\", strategy.long)\n"
        "strategy.close(\"L\", when = pred and ta.ema(close, 9) > close)"
    )
    assert "_ta_sma_1.compute(current_bar_.close)" in _hoist(cpp, 1)
    assert "_ta_ema_2.compute(current_bar_.close)" in _hoist(cpp, 2)
    on_bar = _on_bar(cpp)
    assert on_bar.count("_ta_sma_1.compute(") == 1
    assert on_bar.count("_ta_ema_2.compute(") == 1


def test_assignment_and_tuple_assign_values_hoist():
    cpp = _cpp(
        "pred = close > open\n"
        "x = 0.0\n"
        "x := pred ? ta.sma(close, 5) : x\n"
        "plot(x)"
    )
    assert "_ta_sma_1.compute(current_bar_.close)" in _hoist(cpp, 1)
    x_line = _stmt(cpp, "x = ((pred)")
    assert "_pf_every_bar_ta_1" in x_line and ".compute(" not in x_line


# ---------------------------------------------------------------------------
# Not hoisted: block bodies, UDF bodies, var initializers, pinned exceptions
# ---------------------------------------------------------------------------

def test_if_body_and_loop_body_sites_keep_execution_gated_lowering():
    cpp = _cpp(
        "pred = close > open\n"
        "x = 0.0\n"
        "if pred\n"
        "    x := close > open and close > ta.highest(high, 5)[1] ? 1.0 : 0.0\n"
        "for i = 0 to 1\n"
        "    x += pred and ta.sma(close, 5) > close ? 1.0 : 0.0\n"
        "plot(x)"
    )
    assert "_pf_every_bar_ta_" not in cpp
    on_bar = _on_bar(cpp)
    # In-block compute + call-local history push, reached only when executed.
    assert "_ta_highest_1.compute(current_bar_.high)" in on_bar
    assert "if (history_advances_new_bar()) _hist_call_1.push(_hv)" in on_bar
    assert "_ta_sma_2.compute(current_bar_.close)" in on_bar


def test_user_function_body_sites_are_not_hoisted():
    cpp = _cpp(
        "f(p) =>\n"
        "    p and close > ta.highest(high, 5)[1]\n"
        "pred = close > open\n"
        "s = f(pred)\n"
        "plot(s ? 1 : 0)"
    )
    assert "_pf_every_bar_ta_" not in cpp
    assert "_ta_highest_1.compute(current_bar_.high)" in cpp


def test_var_initializer_is_not_hoisted():
    cpp = _cpp(
        "pred = close > open\n"
        "var float seed = pred ? ta.sma(close, 5) : close\n"
        "plot(seed)"
    )
    assert "_pf_every_bar_ta_" not in cpp


# Per-family clocks below a lazy edge, pinned 2026-09-03 with cadence-7 ternary
# probes on NYSE:F 1D (``v = bar_index % 7 == 3 ? <call> : na``, value exposed
# through the entry size) plus lazy-``and`` probes:
#   hold-last source   roc 38/38 (+39/39 entries), change 39/39, mom 39/39
#                      (every-bar 0/38..0/39; ring-of-executions 0..1/39)
#   per-execution      cum, barssince, valuewhen, cross, crossover, rising 39/39,
#                      math.sum 39/39 (every-bar 0..31/39)

def test_source_clock_families_are_not_hoisted_and_use_held_source_history():
    cpp = _cpp(
        "gate = close > open\n"
        "longish = gate and ta.roc(close, 3) > 0\n"
        "ch = gate ? ta.change(close, 3) : na\n"
        "mo = gate or ta.mom(close, 3) > 0\n"
        "plot(longish ? ch : mo ? 1 : 0)"
    )
    assert "_pf_every_bar_ta_" not in cpp
    assert "struct _PFLazySourceClock {" in cpp
    assert "_pf_lazy_src_clock_1.roc(current_bar_.close, _pf_lazy_src_hist_1[2])" in _stmt(cpp, "longish = (")
    assert "_pf_lazy_src_clock_2.change(current_bar_.close, _pf_lazy_src_hist_2[2])" in _stmt(cpp, "ch = (")
    assert "_pf_lazy_src_clock_3.change(current_bar_.close, _pf_lazy_src_hist_3[2])" in _stmt(cpp, "mo = (")
    for n in (1, 2, 3):
        assert f"std::vector<double> _precalc__ta_" not in cpp or f"_precalc__ta_roc_{n}" not in cpp
        assert f"Series<double> _pf_lazy_src_hist_{n}{{4}};" in cpp
    assert "_precalc__ta_roc" not in cpp
    assert "_precalc__ta_change" not in cpp
    assert "_precalc__ta_mom" not in cpp


def test_per_execution_families_keep_inline_compute_and_never_precalc():
    cpp = _cpp(
        "sma5 = ta.sma(close, 5)\n"
        "gate = bar_index % 7 == 3\n"
        "a = gate ? ta.cum(close) : na\n"
        "b = gate ? ta.barssince(close > sma5) : na\n"
        "c = gate ? ta.valuewhen(close > sma5, close, 0) : na\n"
        "d = gate and ta.crossover(close, sma5)\n"
        "e = gate and ta.cross(close, sma5)\n"
        "f = gate and ta.rising(close, 3)\n"
        "g = gate ? math.sum(close, 3) : na\n"
        "plot(a + b + c + g + (d or e or f ? 1 : 0))"
    )
    assert "_pf_every_bar_ta_" not in cpp
    assert "_PFLazySourceClock" not in cpp
    on_bar = _on_bar(cpp)
    for var, member in (
        ("a", "_ta_cum_2"), ("b", "_ta_barssince_3"), ("c", "_ta_valuewhen_4"),
        ("d", "_ta_crossover_5"), ("e", "_ta_cross_6"), ("f", "_ta_rising_7"),
        ("g", "_ta_sum_8"),
    ):
        line = _stmt(cpp, f"{var} = (")
        assert f"{member}.compute(" in line, (var, line)
        assert "_use_precalc" not in line
        assert f"_precalc_{member}" not in cpp
    # The unconditional sma5 is untouched (eager, precalc-eligible).
    assert "std::vector<double> _precalc__ta_sma_1" in cpp
    assert on_bar.count("_ta_sma_1.compute(") == 1


def test_security_payload_sites_are_not_hoisted():
    cpp = _cpp(
        "pred = close > open\n"
        "htf = request.security(syminfo.tickerid, \"D\", pred and close > ta.sma(close, 5))\n"
        "plot(htf ? 1 : 0)"
    )
    assert "_pf_every_bar_ta_" not in cpp
    assert "_sec0__ta_sma_1.compute(bar.close)" in cpp


def test_unpinned_families_keep_their_existing_lowering():
    """Allow-list: a family without an every-bar tape is neither hoisted nor
    re-routed (2026-09-04 Cloud Run measurement of the broad hoist: -170
    tiers / 30 hard lanes). Inline compute when reached; precalc as before."""
    cpp = _cpp(
        "pred = close > open\n"
        "a = pred and ta.rsi(close, 14) > 50\n"
        "b = pred ? ta.atr(14) : na\n"
        "c = pred or ta.stdev(close, 20) > 1\n"
        "d = pred and ta.wma(close, 9) > close\n"
        "plot(a or c or d ? b : 0)"
    )
    assert "_pf_every_bar_ta_" not in cpp
    assert "_PFLazySourceClock" not in cpp
    for var, member in (("a", "_ta_rsi_1"), ("b", "_ta_atr_2"), ("c", "_ta_stdev_3"), ("d", "_ta_wma_4")):
        assert f"{member}.compute(" in _stmt(cpp, f"{var} = ("), var
    # Static sites keep main's precalc eligibility (static mode only).
    assert "std::vector<double> _precalc__ta_rsi_1" in cpp


def test_eager_top_level_sites_are_unchanged():
    cpp = _cpp(
        "m = ta.sma(close, 5)\n"
        "h = ta.highest(high, 5)[1]\n"
        "c = close > ta.ema(close, 9) and close > open\n"
        "plot(m + h + (c ? 1 : 0))"
    )
    assert "_pf_every_bar_ta_" not in cpp


def test_hoist_plan_is_deterministic():
    assert _cpp(ROBMAGNAYE_SHAPE) == _cpp(ROBMAGNAYE_SHAPE)


# ---------------------------------------------------------------------------
# Compile-only (engine headers) and executable synthetic-bars checks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label, body",
    [
        ("ring-lazyand", PIN_RING_LAZYAND),
        ("lazyand-sma", PIN_LAZYAND_SMA),
        ("lazyand-ema", PIN_LAZYAND_EMA),
        ("ring-ternary", PIN_RING_TERNARY),
        ("robmagnaye-shape", ROBMAGNAYE_SHAPE),
    ],
)
def test_pins_compile_against_engine_headers(label, body):
    compile_env.compile_cpp(_cpp(body), label=f"lazy-edge {label}")


_RUNTIME_PINE = _HEADER + """var int andHits = 0
var int ternaryHits = 0
var int smaHits = 0
c = bar_index % 7 == 3 and close > ta.highest(high, 5)[1]
v = bar_index % 7 == 3 ? ta.highest(high, 5)[1] : na
s = bar_index % 7 == 3 and close > ta.sma(close, 5)[1]
if c
    andHits += 1
if not na(v) and close > v
    ternaryHits += 1
if s
    smaHits += 1
"""

# 30 synthetic bars (high == close). Under the every-bar model the lazy RHS
# windows are fully warm at every ``bar_index % 7 == 3`` bar from 10 on:
#   bar 10: close 120 > highest/sma(bars 5..9)  -> hit
#   bar 17: close 100 < highest/sma(bars 12..16) -> no hit
#   bar 24: close 101 > highest/sma(bars 19..23) = 100 -> hit
# so every counter is 2. Under the per-call model the indicator has seen at
# most four samples (bars 3, 10, 17, 24) and ``[1]`` is still na: 0 hits.
_RUNTIME_CLOSES = (
    [100.0] * 5
    + [110.0, 111.0, 112.0, 113.0, 114.0]
    + [120.0]
    + [130.0, 129.0, 128.0, 127.0, 126.0, 125.0]
    + [100.0]
    + [100.0] * 6
    + [101.0]
    + [100.0] * 5
)
assert len(_RUNTIME_CLOSES) == 30

_RUNTIME_DRIVER = r"""
#include <iostream>

static Bar make_bar(double close, int64_t timestamp) {
    return Bar{close, close, close, close, 1.0, timestamp};
}

int main() {
    const double closes[] = {%(closes)s};
    const int n = sizeof(closes) / sizeof(closes[0]);
    Bar bars[30];
    for (int i = 0; i < n; ++i) {
        bars[i] = make_bar(closes[i], 1000 + static_cast<int64_t>(i) * 60000);
    }

    GeneratedStrategy precalc;
    precalc.run(bars, n);                 // static mode: _use_precalc path

    GeneratedStrategy dynamic;
    dynamic.run(bars, n, "1", "1");       // dynamic mode: inline every-bar step

    std::cout << precalc.andHits << ' ' << precalc.ternaryHits << ' '
              << precalc.smaHits << ' ' << dynamic.andHits << ' '
              << dynamic.ternaryHits << ' ' << dynamic.smaHits << '\n';
    return 0;
}
"""


def _find_engine_library() -> Path | None:
    explicit = os.environ.get("PINEFORGE_ENGINE_LIB")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    engine_inc = compile_env._ENGINE_INC
    if engine_inc is None:
        return None
    candidates: list[Path] = []
    for pattern in ("build*/lib/libpineforge.a", "build*/lib/libpineforge.dylib"):
        candidates.extend(sorted(engine_inc.parent.glob(pattern)))
    return candidates[0].resolve() if candidates else None


def _compile_and_run(source: str) -> str:
    compile_env.skip_if_no_compile_env()
    engine_lib = _find_engine_library()
    if engine_lib is None:
        pytest.skip("set PINEFORGE_ENGINE_LIB to a built PineForge engine library")
    compiler = compile_env._COMPILER
    engine_inc = compile_env._ENGINE_INC
    eigen_inc = compile_env._EIGEN_INC
    assert compiler is not None and engine_inc is not None and eigen_inc is not None
    with tempfile.TemporaryDirectory(prefix="pineforge-lazy-edge-") as tmp:
        cpp = Path(tmp) / "lazy_edge.cpp"
        exe = Path(tmp) / "lazy_edge"
        cpp.write_text(source)
        command = [compiler, "-std=c++17", "-O0", "-I", str(engine_inc), "-I", str(eigen_inc)]
        if compile_env._GENERATED_INC is not None:
            command += ["-I", str(compile_env._GENERATED_INC)]
        command += [str(cpp), str(engine_lib), "-pthread", "-o", str(exe)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if built.returncode != 0:
            raise AssertionError(built.stderr or built.stdout)
        ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        if ran.returncode != 0:
            raise AssertionError(ran.stderr or ran.stdout)
        return ran.stdout.strip()


def test_synthetic_bars_lazy_edge_windows_are_every_bar_in_both_run_modes():
    """Executable check on synthetic bars (no feed): the per-call model would
    print ``0 0 0 0 0 0``; the every-bar rule gives 2 hits per shape in the
    precalc run and in the dynamic (input_tf/script_tf) run alike."""
    driver = _RUNTIME_DRIVER % {
        "closes": ", ".join(f"{c:.1f}" for c in _RUNTIME_CLOSES)
    }
    assert _compile_and_run(transpile(_RUNTIME_PINE) + driver) == "2 2 2 2 2 2"
