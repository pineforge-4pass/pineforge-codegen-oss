from __future__ import annotations

import re

from pineforge_codegen import transpile


def _cpp(body: str, *, header: str = "") -> str:
    return transpile(f'//@version=6\nstrategy("lazy roc"{header})\n{body}\n')


def test_plain_and_rhs_direct_close_literal_three_gets_one_clock_per_callsite():
    cpp = _cpp(
        "gate = close > open\n"
        "longish = gate and ta.roc(close, 3) > 0\n"
        "shortish = gate and ta.roc(close, 3) < 0\n"
        "plot(longish ? 1 : shortish ? -1 : 0)"
    )

    assert "struct _PFLazySaturatedROC3Clock" in cpp
    clocks = re.findall(
        r"^    _PFLazySaturatedROC3Clock (_pf_lazy_saturated_roc3_clock_\d+);$",
        cpp,
        re.MULTILINE,
    )
    assert clocks == [
        "_pf_lazy_saturated_roc3_clock_1",
        "_pf_lazy_saturated_roc3_clock_2",
    ]
    assert "std::vector<double> _precalc__ta_roc" not in cpp
    assert "Series<double> _pf_lazy_saturated_roc3_close_history{4};" in cpp
    assert "_pf_lazy_saturated_roc3_close_history[3]" in cpp
    assert cpp.count(
        ".evaluate(current_bar_.close, "
        "_pf_lazy_saturated_roc3_close_history[3], bar_index_)"
    ) == 2


def test_clock_has_saturated_q1_eager_fallback_and_same_bar_base_contract():
    cpp = _cpp("x = close > open and ta.roc(close, 3) > 0")
    helper = cpp.split("struct _PFLazySaturatedROC3Clock", 1)[1].split("};", 1)[0]

    assert "if (working_bar != bar)" in helper
    assert "bar_base_source = committed_source;" in helper
    assert "bar_base_bar = committed_bar;" in helper
    assert "bar - bar_base_bar >= 3" in helper
    assert "saturated ? bar_base_source : eager_previous" in helper
    assert "committed_source = source;" in helper
    assert "committed_bar = bar;" in helper
    assert "void reset()" in helper
    for reset in (
        "committed_source = na<double>();",
        "committed_bar = -1;",
        "bar_base_source = na<double>();",
        "bar_base_bar = -1;",
        "working_bar = -1;",
    ):
        assert reset in helper


def test_clock_member_is_automatically_checkpointed_for_coof():
    cpp = _cpp(
        "x = close > open and ta.roc(close, 3) > 0",
        header=", calc_on_order_fills=true",
    )
    match = re.search(
        r"decltype\(GeneratedStrategy::(_pf_lazy_saturated_roc3_clock_1)\) "
        r"_pf_value_(\d+);",
        cpp,
    )
    assert match is not None
    member, index = match.groups()
    assert re.search(rf"^            {member},$", cpp, re.MULTILINE)
    assert (
        f"this->{member} = _pf_script_state_checkpoint_->_pf_value_{index};"
        in cpp
    )
    history_match = re.search(
        r"decltype\(GeneratedStrategy::"
        r"(_pf_lazy_saturated_roc3_close_history)\) _pf_value_(\d+);",
        cpp,
    )
    assert history_match is not None
    history, history_index = history_match.groups()
    assert re.search(rf"^            {history},$", cpp, re.MULTILINE)
    assert (
        f"this->{history} = "
        f"_pf_script_state_checkpoint_->_pf_value_{history_index};"
        in cpp
    )


def test_on_bar_resets_clocks_and_fallback_history_before_first_push():
    cpp = _cpp("x = close > open and ta.roc(close, 3) > 0")
    on_bar = cpp.split("void on_bar(const Bar& bar) override {", 1)[1].split(
        "\n    }", 1
    )[0]
    reset_guard = "if (history_advances_new_bar() && bar_index_ == 0) {"
    assert reset_guard in on_bar
    assert "_pf_lazy_saturated_roc3_clock_1.reset();" in on_bar
    assert "_pf_lazy_saturated_roc3_close_history.clear();" in on_bar
    history_push = "_pf_lazy_saturated_roc3_close_history.push(current_bar_.close)"
    assert history_push in on_bar
    assert on_bar.index(reset_guard) < on_bar.index(history_push)


def test_non_oracle_shapes_keep_existing_precalc_route():
    cases = {
        "eager": "x = ta.roc(close, 3) > 0",
        "other_source": "x = close > open and ta.roc(open, 3) > 0",
        "other_length": "x = close > open and ta.roc(close, 4) > 0",
        "or_shape": "x = close > open and (high > low or ta.roc(close, 3) > 0)",
        "ternary_shape": "x = close > open and (high > low ? ta.roc(close, 3) : 0) > 0",
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
    }
    for label, source in cases.items():
        cpp = _cpp(source)
        assert "_PFLazySaturatedROC3Clock" not in cpp, label
        assert "ta::ROC _ta_roc" in cpp, label
        assert "_pf_lazy_saturated_roc3_close_history[3]" not in cpp, label


def test_named_length_is_not_silently_widened_into_literal_shape():
    cpp = _cpp("gate = close > open\nx = gate and ta.roc(source=close, length=3) > 0")
    assert "_PFLazySaturatedROC3Clock" not in cpp
    assert "std::vector<double> _precalc__ta_roc" in cpp


def test_user_shadowed_close_stays_on_existing_eager_route():
    cpp = _cpp(
        "float close = open\n"
        "gate = bar_index == 0 or bar_index == 5\n"
        "signal = gate and ta.roc(close, 3) > 0"
    )
    assert "_PFLazySaturatedROC3Clock" not in cpp
    assert "std::vector<double> _precalc__ta_roc" in cpp


def test_generated_type_clock_and_history_names_avoid_pine_collisions():
    cpp = _cpp(
        "type _PFLazySaturatedROC3Clock\n"
        "    float value\n"
        "float _pf_lazy_saturated_roc3_clock_1 = 0.0\n"
        "float _pf_lazy_saturated_roc3_close_history = 0.0\n"
        "gate = close > open\n"
        "signal = gate and ta.roc(close, 3) > 0"
    )
    assert cpp.count("struct _PFLazySaturatedROC3Clock {") == 1
    assert "struct _PFLazySaturatedROC3Clock_2 {" in cpp
    assert (
        "_PFLazySaturatedROC3Clock_2 "
        "_pf_lazy_saturated_roc3_clock_1_2;"
    ) in cpp
    assert (
        "Series<double> _pf_lazy_saturated_roc3_close_history_2{4};"
        in cpp
    )
    assert (
        "_pf_lazy_saturated_roc3_clock_1_2.evaluate(current_bar_.close, "
        "_pf_lazy_saturated_roc3_close_history_2[3], bar_index_)"
        in cpp
    )


def test_generated_clock_name_avoids_emitted_udf_method_name():
    cpp = _cpp(
        "_pf_lazy_saturated_roc3_clock_1() => 1.0\n"
        "other = _pf_lazy_saturated_roc3_clock_1()\n"
        "signal = close > open and ta.roc(close, 3) > 0"
    )
    assert "double _pf_lazy_saturated_roc3_clock_1()" in cpp
    assert (
        "_PFLazySaturatedROC3Clock "
        "_pf_lazy_saturated_roc3_clock_1_2;"
    ) in cpp
    assert (
        "_pf_lazy_saturated_roc3_clock_1_2.evaluate(current_bar_.close, "
        "_pf_lazy_saturated_roc3_close_history[3], bar_index_)"
        in cpp
    )
