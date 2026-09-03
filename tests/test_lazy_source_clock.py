"""Hold-last source clock for ``ta.change`` / ``ta.mom`` / ``ta.roc`` below a lazy edge.

Pinned 2026-09-03 with ``lab tv`` on NYSE:F 1D (range 2025-04-01..2026-05-01,
cadence-7 probes, value exposed through the entry size). TradingView computes
these three from the CALL'S OWN ``source[length]`` history: the source is
written only on bars where the call executes, the last executed value is held
on the bars it skips, and the history is na before the first execution:

* ``v = bar_index % 7 == 3 ? ta.roc(close, 3) : na``   -> 38/38 by value
  (``... and ta.roc(close, 3) > 0`` -> 39/39 entries); every-bar 0/38,
  ring-of-executions 0/38.
* ``ta.change(close, 3)`` / ``ta.mom(close, 3)`` -> 39/39 each (every-bar 0,
  ring 1); call 1 (bar 3, the first execution) has no TV entry: na.

This generalises the former ``_PFLazySaturatedROC3Clock`` (#64), which pinned
the same clock for the literal ``ta.roc(close, 3)`` under a plain ``and`` RHS
but fell back to the eager chart ``close[3]`` before the first execution and
between executions closer than the length. The tapes' call 1 refutes the
first fallback (na). The second regime is not distinguished by any tape, so
the #64 eager chart read is kept there for chart-builtin sources (a paired
``_pf_lazy_src_chart_N`` Series); other sources read the held history.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile


def _cpp(body: str, *, header: str = "") -> str:
    return transpile(f'//@version=6\nstrategy("lazy source clock"{header})\n{body}\n')


def _stmt(cpp: str, prefix: str) -> str:
    return next(ln for ln in cpp.splitlines() if ln.strip().startswith(prefix))


def test_one_clock_and_held_history_per_callsite():
    cpp = _cpp(
        "gate = close > open\n"
        "longish = gate and ta.roc(close, 3) > 0\n"
        "shortish = gate and ta.roc(close, 3) < 0\n"
        "plot(longish ? 1 : shortish ? -1 : 0)"
    )
    assert "struct _PFLazySourceClock {" in cpp
    clocks = re.findall(
        r"^    _PFLazySourceClock (_pf_lazy_src_clock_\d+);$", cpp, re.MULTILINE
    )
    assert clocks == ["_pf_lazy_src_clock_1", "_pf_lazy_src_clock_2"]
    hists = re.findall(
        r"^    Series<double> (_pf_lazy_src_hist_\d+)\{4\};$", cpp, re.MULTILINE
    )
    assert hists == ["_pf_lazy_src_hist_1", "_pf_lazy_src_hist_2"]
    charts = re.findall(
        r"^    Series<double> (_pf_lazy_src_chart_\d+)\{4\};$", cpp, re.MULTILINE
    )
    assert charts == ["_pf_lazy_src_chart_1", "_pf_lazy_src_chart_2"]
    assert "std::vector<double> _precalc__ta_roc" not in cpp
    assert "_pf_lazy_src_clock_1.roc(current_bar_.close, _pf_lazy_src_clock_1.previous_source(_pf_lazy_src_hist_1[2], _pf_lazy_src_chart_1[3], 3, bar_index_))" in _stmt(cpp, "longish = (")
    assert "_pf_lazy_src_clock_2.roc(current_bar_.close, _pf_lazy_src_clock_2.previous_source(_pf_lazy_src_hist_2[2], _pf_lazy_src_chart_2[3], 3, bar_index_))" in _stmt(cpp, "shortish = (")


def test_clock_contract_hold_last_base_and_na_guards():
    cpp = _cpp("x = close > open and ta.roc(close, 3) > 0")
    helper = cpp.split("struct _PFLazySourceClock", 1)[1].split("};", 1)[0]
    assert "if (working_bar != bar)" in helper
    assert "bar_base_source = committed_source;" in helper
    assert "bar_base_bar = committed_bar;" in helper
    assert "void begin_bar(int bar)" in helper
    assert "double previous_source(double held, double eager, int length," in helper
    # na before the first execution (tape call 1); held once the previous
    # execution is at least ``length`` bars back; #64's eager chart read in
    # between.
    assert "if (bar_base_bar < 0 || length < 1) {" in helper
    assert "return bar - bar_base_bar >= length ? held : eager;" in helper
    assert "double change(double source, double previous)" in helper
    assert "double roc(double source, double previous)" in helper
    assert helper.count("committed_source = source;") == 2
    assert helper.count("committed_bar = working_bar;") == 2
    assert "if (is_na(source) || is_na(previous) || previous == 0.0)" in helper
    assert "return (source - previous) / previous * 100.0;" in helper
    assert "return source - previous;" in helper
    for reset in (
        "committed_source = na<double>();",
        "committed_bar = -1;",
        "bar_base_source = na<double>();",
        "bar_base_bar = -1;",
        "working_bar = -1;",
    ):
        assert reset in helper


def test_on_bar_resets_then_records_the_held_source_before_statements():
    cpp = _cpp("x = close > open and ta.roc(close, 3) > 0")
    on_bar = cpp.split("void on_bar(const Bar& bar) override {", 1)[1].split(
        "\n    }", 1
    )[0]
    reset_guard = "if (history_advances_new_bar() && bar_index_ == 0) {"
    assert reset_guard in on_bar
    assert "_pf_lazy_src_clock_1.reset();" in on_bar
    assert "_pf_lazy_src_hist_1.clear();" in on_bar
    assert "_pf_lazy_src_chart_1.clear();" in on_bar
    begin = "_pf_lazy_src_clock_1.begin_bar(bar_index_);"
    push = "if (history_advances_new_bar()) _pf_lazy_src_hist_1.push(_pf_lazy_src_clock_1.bar_base_source);"
    update = "else _pf_lazy_src_hist_1.update(_pf_lazy_src_clock_1.bar_base_source);"
    chart_push = "if (history_advances_new_bar()) _pf_lazy_src_chart_1.push(current_bar_.close);"
    assert on_bar.index(reset_guard) < on_bar.index(begin) < on_bar.index(push) < on_bar.index(update)
    assert on_bar.index(update) < on_bar.index(chart_push) < on_bar.index("x = (")


def test_clock_and_history_members_are_automatically_checkpointed_for_coof():
    cpp = _cpp(
        "x = close > open and ta.roc(close, 3) > 0",
        header=", calc_on_order_fills=true",
    )
    for member in ("_pf_lazy_src_clock_1", "_pf_lazy_src_hist_1", "_pf_lazy_src_chart_1"):
        match = re.search(
            rf"decltype\(GeneratedStrategy::({member})\) _pf_value_(\d+);", cpp
        )
        assert match is not None, member
        name, index = match.groups()
        assert re.search(rf"^            {name},$", cpp, re.MULTILINE)
        assert (
            f"this->{name} = _pf_script_state_checkpoint_->_pf_value_{index};"
            in cpp
        )


def test_change_mom_and_roc_route_in_every_top_level_lazy_position():
    cpp = _cpp(
        "gate = close > open\n"
        "a = gate and ta.change(close, 3) > 0\n"
        "b = gate or ta.mom(close, 2) > 0\n"
        "c = gate ? ta.roc(close, 3) : na\n"
        "d = gate ? 0.0 : ta.change(close)\n"
        "e = gate and ta.roc(source = close, length = 3) > 0\n"
        "plot((a or b or e) ? c + d : 0)"
    )
    assert "_pf_lazy_src_clock_1.change(current_bar_.close, _pf_lazy_src_clock_1.previous_source(_pf_lazy_src_hist_1[2], _pf_lazy_src_chart_1[3], 3, bar_index_))" in _stmt(cpp, "a = (")
    assert "_pf_lazy_src_clock_2.change(current_bar_.close, _pf_lazy_src_clock_2.previous_source(_pf_lazy_src_hist_2[1], _pf_lazy_src_chart_2[2], 2, bar_index_))" in _stmt(cpp, "b = (")
    assert "_pf_lazy_src_clock_3.roc(current_bar_.close, _pf_lazy_src_clock_3.previous_source(_pf_lazy_src_hist_3[2], _pf_lazy_src_chart_3[3], 3, bar_index_))" in _stmt(cpp, "c = (")
    # ``ta.change(source)`` defaults to length 1: previous is the held value
    # as of the previous bar (== the previous execution's source).
    assert "_pf_lazy_src_clock_4.change(current_bar_.close, _pf_lazy_src_clock_4.previous_source(_pf_lazy_src_hist_4[0], _pf_lazy_src_chart_4[1], 1, bar_index_))" in _stmt(cpp, "d = (")
    assert "Series<double> _pf_lazy_src_hist_4{2};" in cpp
    assert "Series<double> _pf_lazy_src_chart_4{2};" in cpp
    assert "_pf_lazy_src_clock_5.roc(current_bar_.close, _pf_lazy_src_clock_5.previous_source(_pf_lazy_src_hist_5[2], _pf_lazy_src_chart_5[3], 3, bar_index_))" in _stmt(cpp, "e = (")
    for family in ("change", "mom", "roc"):
        assert f"std::vector<double> _precalc__ta_{family}" not in cpp
    assert "_pf_every_bar_ta_" not in cpp


def test_runtime_length_reads_the_held_history_at_length_minus_one():
    cpp = _cpp(
        "len = input.int(3, \"len\")\n"
        "gate = close > open\n"
        "x = gate and ta.roc(close, len) > 0\n"
        "plot(x ? 1 : 0)"
    )
    x_line = _stmt(cpp, "x = (")
    assert (
        "_pf_lazy_src_clock_1.roc(current_bar_.close, _pf_lazy_src_clock_1.previous_source("
        "(((int)(len)) >= 1 ? _pf_lazy_src_hist_1[((int)(len)) - 1] : na<double>()), "
        "_pf_lazy_src_chart_1[(int)(len)], (int)(len), bar_index_))"
    ) in x_line
    assert re.search(r"^    Series<double> _pf_lazy_src_hist_1;$", cpp, re.MULTILINE)
    assert re.search(r"^    Series<double> _pf_lazy_src_chart_1;$", cpp, re.MULTILINE)


def test_shadowed_close_and_other_sources_route_too():
    """A user-bound ``close`` and a computed source have no chart series: the
    in-between regime reads the held history instead of the eager chart."""
    cpp = _cpp(
        "float close = open\n"
        "gate = bar_index == 0 or bar_index == 5\n"
        "signal = gate and ta.roc(close, 3) > 0\n"
        "other = gate and ta.change(hl2, 2) > 0\n"
        "rsiv = ta.rsi(open, 14)\n"
        "third = gate and ta.change(rsiv, 3) > 0\n"
        "plot(signal or other or third ? 1 : 0)"
    )
    signal_line = _stmt(cpp, "signal = (")
    assert "_pf_lazy_src_clock_1.roc(" in signal_line
    assert "_pf_lazy_src_clock_1.previous_source(_pf_lazy_src_hist_1[2], _pf_lazy_src_hist_1[2], 3, bar_index_)" in signal_line
    assert "_pf_lazy_src_chart_1" not in cpp
    other_line = _stmt(cpp, "other = (")
    assert (
        "_pf_lazy_src_clock_2.change(((current_bar_.high + current_bar_.low) / 2.0), "
        "_pf_lazy_src_clock_2.previous_source(_pf_lazy_src_hist_2[1], _pf_lazy_src_chart_2[2], 2, bar_index_))"
    ) in other_line
    assert "Series<double> _pf_lazy_src_hist_2{3};" in cpp
    assert "Series<double> _pf_lazy_src_chart_2{3};" in cpp
    third_line = _stmt(cpp, "third = (")
    assert "_pf_lazy_src_clock_3.change(rsiv, _pf_lazy_src_clock_3.previous_source(_pf_lazy_src_hist_3[2], _pf_lazy_src_hist_3[2], 3, bar_index_))" in third_line
    assert "_pf_lazy_src_chart_3" not in cpp
    assert "std::vector<double> _precalc__ta_roc" not in cpp


def test_non_top_level_and_eager_shapes_keep_the_existing_route():
    cases = {
        "eager": "x = ta.roc(close, 3) > 0",
        "eager_ternary_condition": "x = ta.change(close, 3) > 0 ? 1 : 0",
        "udf": (
            "f() =>\n"
            "    close > open and ta.roc(close, 3) > 0\n"
            "x = f()"
        ),
        "security": (
            'x = close > open and request.security(syminfo.tickerid, "60", '
            "close > open and ta.roc(close, 3) > 0)"
        ),
        "if_body": (
            "x = false\n"
            "if close > open\n"
            "    x := high > low and ta.roc(close, 3) > 0"
        ),
        "loop_body": (
            "x = false\n"
            "for i = 0 to 1\n"
            "    x := high > low and ta.roc(close, 3) > 0"
        ),
        "var_init": "var float x = close > open ? ta.roc(close, 3) : 0.0",
        "bool_source": "x = close > open and ta.change(close > open) != 0",
    }
    for label, source in cases.items():
        cpp = _cpp(source)
        assert "_PFLazySourceClock" not in cpp, label
        assert "_pf_lazy_src_hist_" not in cpp, label
        assert "_pf_every_bar_ta_" not in cpp, label
        assert "ta::ROC _ta_roc" in cpp or "ta::Change _ta_change" in cpp, label


def test_generated_type_clock_and_history_names_avoid_pine_collisions():
    cpp = _cpp(
        "type _PFLazySourceClock\n"
        "    float value\n"
        "float _pf_lazy_src_clock_1 = 0.0\n"
        "float _pf_lazy_src_hist_1 = 0.0\n"
        "float _pf_lazy_src_chart_1 = 0.0\n"
        "gate = close > open\n"
        "signal = gate and ta.roc(close, 3) > 0"
    )
    assert cpp.count("struct _PFLazySourceClock {") == 1
    assert "struct _PFLazySourceClock_2 {" in cpp
    assert "_PFLazySourceClock_2 _pf_lazy_src_clock_1_2;" in cpp
    assert "Series<double> _pf_lazy_src_hist_1_2{4};" in cpp
    assert "Series<double> _pf_lazy_src_chart_1_2{4};" in cpp
    assert (
        "_pf_lazy_src_clock_1_2.roc(current_bar_.close, _pf_lazy_src_clock_1_2.previous_source("
        "_pf_lazy_src_hist_1_2[2], _pf_lazy_src_chart_1_2[3], 3, bar_index_))"
    ) in cpp


def test_generated_clock_name_avoids_emitted_udf_method_name():
    cpp = _cpp(
        "_pf_lazy_src_clock_1() => 1.0\n"
        "other = _pf_lazy_src_clock_1()\n"
        "signal = close > open and ta.roc(close, 3) > 0"
    )
    assert "double _pf_lazy_src_clock_1()" in cpp
    assert "_PFLazySourceClock _pf_lazy_src_clock_1_2;" in cpp
    assert (
        "_pf_lazy_src_clock_1_2.roc(current_bar_.close, _pf_lazy_src_clock_1_2.previous_source("
        "_pf_lazy_src_hist_1[2], _pf_lazy_src_chart_1[3], 3, bar_index_))"
    ) in cpp
