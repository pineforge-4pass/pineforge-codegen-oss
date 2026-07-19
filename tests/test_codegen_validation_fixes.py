"""Regression tests for codegen bugs found by pinescript-scrapper validation.

Covers ten fix families:
  1. drawing-handle ``na`` reset/assignment (Box{}/Line{}/... not na<double>()),
     plus typed ``na`` for string/int/bool declaration init.
  2. void drawing setter used as a UDF's last expression / if-branch value.
  3. ``request.security`` whose timeframe is a UDF parameter (resolved from the
     function's call sites; dead-code UDFs fall back to the chart timeframe).
  4. parser handling of ``T[]`` array-typed function parameters (``float[] arr``,
     ``line[] ln``) — previously the whole function was dropped.
  5. typed drawing array constructors ``array.new_line/box/label/linefill``.
  6. Pine v6 bool casts that must treat ``na`` as ``false`` instead of C++'s
     truthy NaN conversion.
  7. ``input.source`` series replayed during TA precompute must be cleared
     before the real run.
  8. Pine ``for`` loops infer direction even when an explicit positive ``by``
     step is supplied.
  9. Numeric ternaries promote an ``int`` literal branch to ``double`` when the
     other branch is floating-point arithmetic.
 10. Array methods materialize a duplicated receiver expression once, so
     nested temporary-producing calls cannot form cross-temporary iterators.
"""

import re

from pineforge_codegen import transpile
from tests._compile import compile_cpp


def _cpp(body: str) -> str:
    return transpile('//@version=6\nstrategy("t")\n' + body + "\n")


# ---------------------------------------------------------------------------
# 1. drawing-handle na reset + typed scalar na init
# ---------------------------------------------------------------------------
def test_drawing_handle_na_reset_emits_brace_init():
    cpp = _cpp(
        "var box riskBox = na\n"
        "var line stopLine = na\n"
        "var label lb = na\n"
        "if bar_index == 0\n"
        "    riskBox := box.new(bar_index, high, bar_index + 1, low)\n"
        "    stopLine := line.new(bar_index, low, bar_index + 1, low)\n"
        "if bar_index == 10\n"
        "    riskBox := na\n"
        "    stopLine := na\n"
        "    lb := na\n"
        "plot(close)"
    )
    # Resets lower to the handle's na default, NOT na<double>().
    assert "riskBox = Box{}" in cpp
    assert "stopLine = Line{}" in cpp
    assert "lb = Label{}" in cpp
    # The bad form must never appear for a handle reset.
    assert "riskBox = na<double>()" not in cpp
    assert "stopLine = na<double>()" not in cpp


def test_string_and_bool_na_init_uses_typed_na():
    cpp = _cpp(
        "f() =>\n"
        "    string tag = na\n"
        "    bool flag = na\n"
        "    tag := \"x\"\n"
        "    tag\n"
        "_z = f()\n"
        "plot(close)"
    )
    assert "std::string tag = na<std::string>()" in cpp
    assert "bool flag = na<bool>()" in cpp
    assert "std::string tag = na<double>()" not in cpp


# ---------------------------------------------------------------------------
# 2. void drawing setter as last expression / branch value
# ---------------------------------------------------------------------------
def test_void_setter_as_last_udf_expr_does_not_assign_to_retval():
    cpp = _cpp(
        "var label lb = na\n"
        "setTag(string t) =>\n"
        "    label.set_text(lb, t)\n"
        "if bar_index == 0\n"
        "    lb := label.new(bar_index, close, \"x\")\n"
        "setTag(\"hello\")\n"
        "plot(close)"
    )
    # The void setter is emitted as a statement; the function does NOT assign
    # the void call to its return slot. The broken
    # ``_func_ret = pf_label_set_text(...)`` must NOT appear.
    assert "pf_label_set_text(_pf_labels_, lb" in cpp
    assert "_func_ret = pf_label_set_text" not in cpp
    assert "= pf_label_set_text" not in cpp


def test_void_setter_in_if_branch_value_does_not_assign_to_target():
    cpp = _cpp(
        "var label lb = na\n"
        "f(bool c) =>\n"
        "    x = if c\n"
        "        label.set_text(lb, \"a\")\n"
        "    else\n"
        "        label.set_text(lb, \"b\")\n"
        "    x\n"
        "_z = f(true)\n"
        "plot(close)"
    )
    # Branch's void setter is a statement, not ``__ret = pf_label_set_text(...)``.
    assert "__ret = pf_label_set_text" not in cpp
    assert "= pf_label_set_text" not in cpp or "pf_label_set_text(_pf_labels_" in cpp


# ---------------------------------------------------------------------------
# 3. request.security with a UDF-parameter timeframe
# ---------------------------------------------------------------------------
def test_security_param_tf_single_call_resolves_from_callsite():
    # The tf param is an input member at the (single) call site -> resolved.
    cpp = _cpp(
        "htf = input.timeframe(\"240\", \"HTF\")\n"
        "f(string tf) =>\n"
        "    request.security(syminfo.tickerid, tf, close)\n"
        "b = f(htf)\n"
        "plot(b)"
    )
    # The evaluator registers with the resolved member, not input_tf_ fallback
    # only, and crucially does not raise "Unknown variable 'tf'".
    assert "register_security_eval" in cpp
    assert "Unknown variable" not in cpp


def test_security_param_tf_dead_code_falls_back_to_chart_tf():
    # The UDF is never called -> dead code; registration must not crash.
    cpp = _cpp(
        "f(tf) =>\n"
        "    request.security(syminfo.tickerid, tf, close)\n"
        "plot(close)"
    )
    assert "register_security_eval" in cpp
    assert "Unknown variable" not in cpp


def test_security_input_backed_timeframe_alias_expands_at_registration():
    # NicoCashFx shape: request.security receives a global timeframe alias whose
    # value is assigned on_bar from input-backed operands. Registration happens
    # before on_bar, so emitting the alias member itself registers an empty tf.
    cpp = _cpp(
        'useChart = input.bool(false, "Use chart")\n'
        'fixedTf = input.string("30", "Fixed TF", options=["15", "30", "60"])\n'
        "tf = useChart ? timeframe.period : fixedTf\n"
        'htf = request.security(syminfo.tickerid, tf, close)\n'
        "plot(htf)"
    )
    assert 'register_security_eval(0, tf, input_tf_, false, false)' not in cpp
    assert 'get_input_bool("Use chart", false)' in cpp
    assert 'get_input_string("Fixed TF", std::string("30"))' in cpp
    assert 'register_security_eval(0, ((get_input_bool("Use chart", false)) ? (script_tf_) : (get_input_string("Fixed TF", std::string("30")))), input_tf_, false, false)' in cpp


def test_bar_index_builtin_uses_public_offset_helper():
    cpp = _cpp(
        "fire = bar_index % 200 == 0\n"
        "if fire\n"
        "    strategy.entry(\"L\", strategy.long)\n"
        "plot(close)"
    )
    assert "std::fmod((double)(pine_bar_index()), (double)(200))" in cpp


def test_bar_index_history_series_is_pushed_from_offset_helper():
    cpp = _cpp(
        "past = bar_index[6]\n"
        "span = bar_index - past\n"
        "if span >= 6\n"
        "    strategy.entry(\"L\", strategy.long)\n"
        "plot(close)"
    )
    assert "Series<int> bar_index" in cpp
    assert (
        "if (history_advances_new_bar()) bar_index.push(pine_bar_index());" in cpp
    )
    assert "else bar_index.update(pine_bar_index());" in cpp
    assert "(pine_bar_index() - past)" in cpp


def test_security_param_tf_mixed_with_non_literal_callsite_rejected():
    # Two distinct literal tfs PLUS a third call site whose tf isn't a
    # compile-time literal (a ternary the const-folder can't resolve) ->
    # can't pin every clone to a concrete timeframe, so the original
    # deterministic rejection still applies rather than guessing or silently
    # dropping the non-literal site.
    import pytest
    with pytest.raises(Exception, match="multiple distinct literal timeframes"):
        _cpp(
            "f(tf) =>\n"
            "    request.security(syminfo.tickerid, tf, close)\n"
            "a = f(\"5\")\n"
            "b = f(\"15\")\n"
            "c = f(close > 0 ? \"5\" : \"60\")\n"
            "plot(a + b + c)"
        )


def test_security_param_tf_mixed_literals_cloned_per_callsite():
    # Called with two different literal tfs -> a single evaluator cannot serve
    # both, so the analyzer clones one SecurityCallInfo per call site (each
    # pinned to that site's literal tf) and the codegen emits one specialized
    # function body per call site (f_cs0/f_cs1), each reading its own
    # register_security_eval'd evaluator. No nested ta.* call here, so this
    # function has no TA/series state of its own — exercises the
    # func_security_clone_only backfill path (the per-call-site UDF-cloning
    # machinery otherwise only triggers for has_ta/has_series functions).
    cpp = _cpp(
        "f(tf) =>\n"
        "    request.security(syminfo.tickerid, tf, close)\n"
        "a = f(\"5\")\n"
        "b = f(\"15\")\n"
        "plot(a + b)"
    )
    assert cpp.count("register_security_eval") == 2
    assert 'register_security_eval(0, "5", input_tf_, false, false)' in cpp
    assert 'register_security_eval(1, "15", input_tf_, false, false)' in cpp
    assert "f_cs0" in cpp and "f_cs1" in cpp
    assert "a = f_cs0(std::string(\"5\"))" in cpp
    assert "b = f_cs1(std::string(\"15\"))" in cpp


def test_security_param_tf_same_literal_across_callsites_accepted():
    # Same literal tf at every call site -> unambiguous, single evaluator OK.
    cpp = _cpp(
        "f(tf) =>\n"
        "    request.security(syminfo.tickerid, tf, close)\n"
        "a = f(\"60\")\n"
        "b = f(\"60\")\n"
        "plot(a + b)"
    )
    assert "register_security_eval" in cpp


def test_security_param_tf_six_distinct_literals_cloned_per_callsite():
    # The masayanfx shape: one param-tf security UDF called with six different
    # literals (no nested ta.* call, mirroring the test above). Each call site
    # gets its own clone + evaluator instead of silently collapsing onto the
    # chart timeframe.
    cpp = _cpp(
        "scoreFromRange(tf) =>\n"
        "    request.security(syminfo.tickerid, tf, close)\n"
        "a = scoreFromRange(\"5\")\n"
        "b = scoreFromRange(\"15\")\n"
        "c = scoreFromRange(\"60\")\n"
        "d = scoreFromRange(\"240\")\n"
        "e = scoreFromRange(\"D\")\n"
        "g = scoreFromRange(\"W\")\n"
        "plot(a + b + c + d + e + g)"
    )
    assert cpp.count("register_security_eval") == 6
    for cs_idx, tf in enumerate(("5", "15", "60", "240", "D", "W")):
        assert f'register_security_eval({cs_idx}, "{tf}", input_tf_, false, false)' in cpp
        assert f"scoreFromRange_cs{cs_idx}" in cpp


def test_security_param_tf_nested_ta_six_distinct_literals_cloned_per_callsite():
    # The EXACT masayanfx shape: a nested ta.* call inside request.security's
    # expression makes this function has_ta=True, so per-call-site cloning is
    # ALREADY triggered by the pre-existing TA-isolation mechanism — exercises
    # the "reuse func_call_cs_map's existing numbering" path rather than the
    # backfill path the two tests above exercise.
    cpp = _cpp(
        "indexPeriod = 14\n"
        "scoreFromRange(tf) =>\n"
        "    tfHigh = request.security(syminfo.tickerid, tf, ta.highest(high, indexPeriod)[1], barmerge.gaps_off, barmerge.lookahead_off)\n"
        "    tfLow = request.security(syminfo.tickerid, tf, ta.lowest(low, indexPeriod)[1], barmerge.gaps_off, barmerge.lookahead_off)\n"
        "    tfHigh - tfLow\n"
        "a = scoreFromRange(\"5\")\n"
        "b = scoreFromRange(\"15\")\n"
        "c = scoreFromRange(\"60\")\n"
        "d = scoreFromRange(\"240\")\n"
        "e = scoreFromRange(\"D\")\n"
        "g = scoreFromRange(\"W\")\n"
        "plot(a + b + c + d + e + g)"
    )
    # 2 request.security calls x 6 call sites = 12 evaluators.
    assert cpp.count("register_security_eval") == 12
    for cs_idx, tf in enumerate(("5", "15", "60", "240", "D", "W")):
        assert f"scoreFromRange_cs{cs_idx}" in cpp


# ---------------------------------------------------------------------------
# 4. T[] array-typed function parameters
# ---------------------------------------------------------------------------
def test_float_array_param_not_dropped():
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser
    src = ('strategy("t")\n'
           "f(float[] arr) =>\n"
           "    x = array.size(arr)\n"
           "    x\n"
           "plot(close)")
    ast = Parser(Lexer(src).tokenize(), source=src).parse()
    funcs = [(s.name, s.params) for s in ast.body if type(s).__name__ == "FuncDef"]
    assert funcs == [("f", ["arr"])]


def test_drawing_array_param_not_dropped():
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser
    src = ('strategy("t")\n'
           "f(line[] ln, label[] lb, box[] bx) =>\n"
           "    n = array.size(ln)\n"
           "    n\n"
           "plot(close)")
    ast = Parser(Lexer(src).tokenize(), source=src).parse()
    funcs = [(s.name, s.params) for s in ast.body if type(s).__name__ == "FuncDef"]
    assert funcs == [("f", ["ln", "lb", "bx"])]


def test_array_param_function_preserves_following_global_var():
    # The original bug: a T[]-param function was dropped AND swallowed the
    # following global var declaration (the "longActive" regression).
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser
    src = ('strategy("t")\n'
           "f(float[] a) =>\n"
           "    array.size(a)\n"
           "var bool longActive = false\n"
           "var bool shortActive = false\n"
           "plot(close)")
    ast = Parser(Lexer(src).tokenize(), source=src).parse()
    names = [s.name for s in ast.body if type(s).__name__ == "VarDecl"]
    assert "longActive" in names
    assert "shortActive" in names


# ---------------------------------------------------------------------------
# 5. typed drawing array constructors
# ---------------------------------------------------------------------------
def test_drawing_array_constructors_emit_typed_vectors():
    cpp = _cpp(
        "var line[] lns = array.new_line()\n"
        "var label[] lbs = array.new_label(3)\n"
        "var box[] bxs = array.new_box()\n"
        "var linefill[] lfs = array.new_linefill()\n"
        "plot(array.size(lns))"
    )
    assert "std::vector<Line> lns" in cpp
    assert "lns = std::vector<Line>()" in cpp
    assert "std::vector<Label> lbs" in cpp
    # size + default-init element: Label{}
    assert "std::vector<Label>((size_t)(3), Label{})" in cpp
    assert "std::vector<Box> bxs" in cpp
    assert "std::vector<Linefill> lfs" in cpp


def test_drawing_array_constructor_default_value_arg():
    cpp = _cpp(
        "var box[] bxs = array.new_box(2, box.new(bar_index, high, bar_index + 1, low))\n"
        "plot(array.size(bxs))"
    )
    assert "std::vector<Box>((size_t)(2)" in cpp


def test_untyped_var_drawing_array_constructor_emits_typed_member():
    cpp = _cpp(
        "var boxes = array.new_box()\n"
        "if bar_index == 0\n"
        "    b = box.new(bar_index, high, bar_index + 1, low)\n"
        "    array.push(boxes, b)\n"
        "plot(array.size(boxes))"
    )
    assert "std::vector<Box> boxes;" in cpp
    assert "std::vector<double> boxes;" not in cpp


def test_comma_separated_statements_and_array_fill_emit_all_side_effects():
    cpp = _cpp(
        "var float a = na\n"
        "var float b = na\n"
        "var float[] xs = array.new_float(3, na)\n"
        "var int[] ys = array.new_int(2, na)\n"
        "var label[] lbs = array.new_label(2, na)\n"
        "if true\n"
        "    a := 1, b := 2\n"
        "    array.fill(xs, na), array.set(xs, 1, 7)\n"
        "    array.fill(ys, na), ys.set(1, na)\n"
        "    array.fill(lbs, na)\n"
        "plot(a + b + array.get(xs, 1))"
    )
    assert "a = 1;" in cpp
    assert "b = 2;" in cpp
    assert "xs = std::vector<double>((size_t)(3), na<double>());" in cpp
    assert "ys = std::vector<int>((size_t)(2), na<int>());" in cpp
    assert "lbs = std::vector<Label>((size_t)(2), Label{});" in cpp
    assert "std::fill(xs.begin(), xs.end(), na<double>());" in cpp
    assert "}((7)); }((1)); }((xs));" in cpp
    assert "std::fill(ys.begin(), ys.end(), na<int>());" in cpp
    assert "}((na<int>())); }((1)); }((ys));" in cpp
    assert "std::fill(lbs.begin(), lbs.end(), Label{});" in cpp
    assert "std::vector<double>((size_t)(3), 0.0)" not in cpp


def test_bool_cast_numeric_na_is_false_not_cpp_nan_truthy():
    cpp = _cpp(
        "var bool isUp = bool(na)\n"
        "fromClose = bool(close)\n"
        "fromZero = bool(0)\n"
        "plot(isUp ? close : open)\n"
    )
    assert "(bool)(na<double>())" not in cpp
    assert "is_na(_pf_v) ? false : (bool)_pf_v" in cpp
    assert "var bool" not in cpp
    assert "bool isUp" in cpp
    assert "bool fromClose" in cpp
    assert "bool fromZero" in cpp


def test_precalc_clears_replayed_input_source_series():
    cpp = _cpp(
        "src = input.source(high, \"High source\")\n"
        "ph = ta.pivothigh(src, 5, 5)\n"
        "x = src[5]\n"
        "plot(na(ph) ? x : ph)"
    )
    assert "src.push(get_input_source" in cpp
    assert cpp.count("src.clear();") >= 2
    assert "_src_high_.clear()" in cpp


def test_str_contains_udf_infers_bool_return_type():
    cpp = _cpp(
        "hasXau() =>\n"
        "    str.contains(str.upper(syminfo.ticker), \"XAU\")\n"
        "isGold = hasXau()\n"
        "plot(isGold ? close : open)"
    )
    assert "bool hasXau()" in cpp
    assert "std::string hasXau()" not in cpp


def test_input_source_passed_to_history_udf_is_series_arg():
    cpp = _cpp(
        "src = input.source(close, \"Source\")\n"
        "lagged(_src, _len) =>\n"
        "    lag = math.floor((_len - 1) / 2)\n"
        "    _src + (_src - _src[lag])\n"
        "z = lagged(src, 10)\n"
        "plot(z)"
    )
    assert "Series<double> src" in cpp
    assert "src.push(get_input_source" in cpp
    assert "src.update(get_input_source" in cpp
    assert "src = get_input_source" not in cpp
    assert "lagged_cs0(src, 10)" in cpp or "lagged(src, 10)" in cpp
    assert "lagged_cs0(src[0], 10)" not in cpp
    assert "lagged(src[0], 10)" not in cpp
    compile_cpp(cpp, label="input-source-indirect-history-series")


def test_ta_value_passed_to_history_udf_keeps_exact_series_declaration():
    cpp = _cpp(
        "lagged(series float value) => value[1]\n"
        "vol = ta.atr(14)\n"
        "prior = lagged(vol)\n"
        "scaled = vol * 2.0\n"
        "plot(prior + scaled)"
    )
    assert "Series<double> vol" in cpp
    assert "vol.push(" in cpp
    assert "vol.update(" in cpp
    assert "vol = (history_advances_new_bar()" not in cpp
    assert "vol[0] * 2.0" in cpp
    compile_cpp(cpp, label="ta-indirect-history-series")


def test_general_value_passed_to_history_udf_keeps_exact_series_declaration():
    cpp = _cpp(
        "lagged(series float value) => value[1]\n"
        "flow = close * volume\n"
        "prior = lagged(flow)\n"
        "scaled = flow * 2.0\n"
        "plot(prior + scaled)"
    )
    assert "Series<double> flow" in cpp
    assert "flow.push(" in cpp
    assert "flow.update(" in cpp
    assert "flow = (current_bar_.close * current_bar_.volume)" not in cpp
    assert "flow[0] * 2.0" in cpp
    compile_cpp(cpp, label="general-indirect-history-series")


def test_only_exact_persistent_sibling_passed_to_history_udf_is_series():
    cpp = _cpp(
        "lagged(series float value) => value[1]\n"
        "if close > open\n"
        "    var float x = close\n"
        "    scaled = x * 2.0\n"
        "if close < open\n"
        "    var float x = open\n"
        "    prior = lagged(x)\n"
        "plot(close)"
    )
    assert "double x = na<double>();" in cpp
    assert "Series<double> x__blk1" in cpp
    assert "Series<double> x;" not in cpp
    assert "if (!_pf_var_init_x)" in cpp
    assert "x = current_bar_.close;" in cpp
    assert "if (!_pf_var_init_x__blk1)" in cpp
    assert "x__blk1.update(current_bar_.open);" in cpp
    assert "x__blk1.push(" in cpp
    assert "x__blk1[0]" in cpp
    compile_cpp(cpp, label="exact-persistent-sibling-history-series")


def test_callable_local_history_bridge_can_shadow_promoted_global_series():
    cpp = _cpp(
        "lag(series float value) => value[1]\n"
        "x = close * volume\n"
        "globalPrior = lag(x)\n"
        "wrap(float src) =>\n"
        "    x = src * 2.0\n"
        "    lag(x)\n"
        "a = wrap(close)\n"
        "b = wrap(open)\n"
        "plot(globalPrior + a + b)"
    )
    assert "Series<double> x" in cpp
    assert "double x = (src * 2.0)" in cpp
    assert "double x_cs1 = (src * 2.0)" in cpp
    assert "x = (src * 2.0);" not in cpp.replace(
        "double x = (src * 2.0);", ""
    )
    compile_cpp(cpp, label="callable-local-shadows-promoted-global-series")


def test_callable_persistent_siblings_keep_exact_clone_storage():
    cpp = _cpp(
        "lag(series float value) => value[1]\n"
        "wrap(float src) =>\n"
        "    if src > open\n"
        "        var float x = src\n"
        "        scaled = x * 2.0\n"
        "    if src < open\n"
        "        var float x = open\n"
        "        prior = lag(x)\n"
        "    src\n"
        "a = wrap(close)\n"
        "b = wrap(open)\n"
        "plot(a + b)"
    )
    assert "double x;" in cpp
    assert "double x__blk1;" in cpp
    assert "double x_cs1;" in cpp
    assert "double x__blk1_cs1;" in cpp
    assert "scaled = (x * 2.0)" in cpp
    assert "scaled = (x_cs1 * 2.0)" in cpp
    assert "double _sv = (x__blk1)" in cpp
    assert "double _sv = (x__blk1_cs1)" in cpp
    compile_cpp(cpp, label="callable-persistent-sibling-clone-storage")


def test_series_parameter_shadowing_global_is_not_remapped_to_clone_member():
    cpp = _cpp(
        "lag(series float value) => value[1]\n"
        "x = close * volume\n"
        "globalPrior = lag(x)\n"
        "wrap(float x) => lag(x)\n"
        "a = wrap(close)\n"
        "b = wrap(open)\n"
        "plot(globalPrior + a + b)"
    )
    assert "double wrap_cs0(const Series<double>& x)" in cpp
    assert "double wrap_cs1(const Series<double>& x)" in cpp
    assert cpp.count("return lag_cs1(x);") == 2
    assert "return lag_cs1(x_cs1);" not in cpp
    compile_cpp(cpp, label="series-parameter-shadow-global-clone-remap")


def test_counted_loop_binder_shadows_global_series_and_uses_history_bridge():
    cpp = _cpp(
        "lag(series float value) => value[1]\n"
        "i = close * volume\n"
        "globalPrior = lag(i)\n"
        "total = 0.0\n"
        "for i = 0 to 2\n"
        "    total += i + lag(i)\n"
        "plot(globalPrior + total)"
    )
    assert "total += (i + lag_cs1(" in cpp
    assert "double _sv = (i)" in cpp
    assert "total += (i[0]" not in cpp
    compile_cpp(cpp, label="counted-loop-binder-shadows-global-series")


def test_for_in_binder_shadows_global_series_as_scalar():
    cpp = _cpp(
        "lag(series float value) => value[1]\n"
        "i = close * volume\n"
        "globalPrior = lag(i)\n"
        "values = array.from(1.0, 2.0)\n"
        "total = 0.0\n"
        "for i in values\n"
        "    total += i\n"
        "plot(globalPrior + total)"
    )
    assert "for (auto i : values)" in cpp
    assert "total += i;" in cpp
    assert "total += i[0];" not in cpp
    compile_cpp(cpp, label="for-in-binder-shadows-global-series")


def test_block_tuple_binding_shadows_global_series_as_scalar():
    cpp = _cpp(
        "lag(series float value) => value[1]\n"
        "x = close * volume\n"
        "globalPrior = lag(x)\n"
        "pair() => [1.0, 2.0]\n"
        "if close > open\n"
        "    [x, y] = pair()\n"
        "    combined = x + y\n"
        "plot(globalPrior)"
    )
    assert "auto [x, y] = pair();" in cpp
    assert "combined = (x + y);" in cpp
    assert "combined = (x[0] + y);" not in cpp
    compile_cpp(cpp, label="block-tuple-binding-shadows-global-series")


def test_callable_tuple_binding_shadows_global_series_as_scalar():
    cpp = _cpp(
        "lag(series float value) => value[1]\n"
        "x = close * volume\n"
        "globalPrior = lag(x)\n"
        "pair() => [1.0, 2.0]\n"
        "wrap() =>\n"
        "    [x, y] = pair()\n"
        "    x + y\n"
        "a = wrap()\n"
        "b = wrap()\n"
        "plot(globalPrior + a + b)"
    )
    assert cpp.count("auto [x, y] = pair();") == 1
    assert "return (x + y);" in cpp
    assert "return (x[0] + y);" not in cpp
    compile_cpp(cpp, label="callable-tuple-binding-shadows-global-series")


def test_counted_loop_dynamic_end_uses_outer_same_named_series():
    cpp = _cpp(
        "lag(series float value) => value[1]\n"
        "i = close + 2\n"
        "prior = lag(i)\n"
        "total = 0.0\n"
        "for i = 0 to i\n"
        "    total += i\n"
        "plot(prior + total)"
    )
    assert "auto _for_end_eval_0 = [&]() { return (i[0]); };" in cpp
    assert "int _for_end_0 = _for_end_eval_0();" in cpp
    assert "_for_end_0 = _for_end_eval_0()" in cpp
    assert "_for_end_0 = (i)" not in cpp
    compile_cpp(cpp, label="counted-loop-dynamic-end-outer-series")


def test_udf_tuple_history_binding_uses_exact_call_site_series_storage():
    cpp = _cpp(
        "pair(float src) => [src, src * 2.0]\n"
        "wrap(float src) =>\n"
        "    [x, y] = pair(src)\n"
        "    x[1] + y\n"
        "a = wrap(close)\n"
        "b = wrap(open)\n"
        "plot(a + b)"
    )
    assert "auto _tuple_result_0 = pair(src);" in cpp
    assert "x.push(std::get<0>(_tuple_result_0))" in cpp
    assert "auto _tuple_result_1 = pair(src);" in cpp
    assert "x_cs1.push(std::get<0>(_tuple_result_1))" in cpp
    assert "return (x_cs1[1] + y);" in cpp
    compile_cpp(cpp, label="udf-tuple-history-exact-call-site-storage")


def test_ta_tuple_history_binding_writes_second_call_site_clone():
    cpp = _cpp(
        "wrap(float src) =>\n"
        "    [st, dir] = ta.supertrend(3.0, 10)\n"
        "    dir[1] + src * 0.0\n"
        "a = wrap(close)\n"
        "b = wrap(open)\n"
        "plot(a + b)"
    )
    assert "dir.push(_result__ta_supertrend_1.direction)" in cpp
    assert "dir_cs1.push(_result__ta_supertrend_1_cs1.direction)" in cpp
    assert "return (dir_cs1[1] + (src * 0.0));" in cpp
    assert "dir.push(_result__ta_supertrend_1_cs1.direction)" not in cpp
    compile_cpp(cpp, label="ta-tuple-history-second-call-site-clone")


def test_scalar_tuple_binding_tombstones_inherited_series_storage_remap():
    cpp = _cpp(
        "pair(float src) => [src, src * 2.0]\n"
        "state(float src) =>\n"
        "    x = src\n"
        "    x[1]\n"
        "use(float src) =>\n"
        "    [x, y] = pair(src)\n"
        "    ta.sma(src, 2) + x + y\n"
        "s = state(close)\n"
        "a = use(close)\n"
        "b = use(open)\n"
        "plot(s + a + b)"
    )
    assert cpp.count("auto [x, y] = pair(src);") == 2
    assert cpp.count(") + x) + y);") == 2
    assert ") + x_cs1) + y);" not in cpp
    compile_cpp(cpp, label="scalar-tuple-tombstones-inherited-series-remap")


def test_syminfo_pointvalue_infers_numeric_udf_return():
    cpp = _cpp(
        "pointValue = syminfo.pointvalue\n"
        "dollarsToPoints(dollars) =>\n"
        "    dollars / pointValue\n"
        "x = dollarsToPoints(100.0)\n"
        "plot(x)"
    )
    assert "double dollarsToPoints(double dollars)" in cpp
    assert "std::string dollarsToPoints" not in cpp


def test_timestamp_timezone_variable_uses_tz_overload():
    cpp = _cpp(
        "tz = input.string(\"America/New_York\", \"Timezone\")\n"
        "nyYear = year(time, tz)\n"
        "rangeStart = timestamp(tz, nyYear, 1, 2, 9, 30)\n"
        "plot(rangeStart)"
    )
    assert "std::string _tz = pineforge::normalize_timezone_for_posix((tz))" in cpp
    assert "int _yr = (tz)" not in cpp
    assert "t.tm_isdst = -1" in cpp
    assert "mktime(&t)" in cpp


def test_ta_stdev_biased_arg_goes_to_constructor_not_compute():
    cpp = _cpp(
        "x = ta.stdev(close, 3, false)\n"
        "plot(x)"
    )
    assert "ta::StdDev(3, false)" in cpp
    assert ".compute(current_bar_.close, false)" not in cpp
    assert ".recompute(current_bar_.close, false)" not in cpp


def test_ta_precalc_skips_user_series_alias_source():
    cpp = _cpp(
        "ha_close = close\n"
        "bbLen = input.int(20, \"BB Length\")\n"
        "dev = ta.stdev(ha_close, bbLen) * 2.0\n"
        "plot(dev)"
    )
    assert "std::vector<double> _precalc__ta_stdev" not in cpp
    assert "_use_precalc ? _precalc__ta_stdev" not in cpp
    assert "_ta_stdev_1.compute(ha_close)" in cpp


def test_ta_precalc_skips_short_circuit_and_rhs_call_sites():
    """Dual ta.sma under ``and`` must not use full-history precalc (lazy-stale).

    Campaign pin: pf-probe-oliver-dual-vol-sma — TV dual-callsite volume SMAs
    disagree with a hoisted SMA; eager precalc erased that independence.
    """
    cpp = _cpp(
        "stPred = close > open\n"
        "a = stPred and (volume < ta.sma(volume, 20))\n"
        "b = stPred and (volume > ta.sma(volume, 20) * 1.2)\n"
        "v = ta.sma(volume, 20)\n"
        "c = stPred and (volume < v)\n"
        "plot(close)"
    )
    # Hoisted always-evaluated SMA may still precalc; dual sites under `and` must not.
    assert "_use_precalc ? _precalc__ta_sma_1" not in cpp
    assert "_use_precalc ? _precalc__ta_sma_2" not in cpp
    assert "stPred &&" in cpp
    assert "_ta_sma_1.compute(current_bar_.volume)" in cpp
    assert "_ta_sma_2.compute(current_bar_.volume)" in cpp
    # Hoisted third SMA still eligible for precalc
    assert "_precalc__ta_sma_3" in cpp


def test_ta_precalc_skips_nested_sma_below_and_rhs():
    """The SMA keeps the enclosing lazy context through an outer ta.change."""
    cpp = _cpp(
        "base = ta.mom(close, 20) > 0 and ta.change(ta.sma(close, 50)) > 0\n"
        "plot(base ? 1 : 0)"
    )
    assert "std::vector<double> _precalc__ta_sma" not in cpp
    assert "_use_precalc ? _precalc__ta_sma" not in cpp
    assert "_ta_sma_" in cpp and ".compute(current_bar_.close)" in cpp


def test_ta_precalc_lazy_scope_routes_recursive_ema_only():
    """EMA follows Pine-v6 lazy edges while unrelated TA stays precalculated."""
    cpp = _cpp(
        "pred = close > open\n"
        "a = pred and ta.ema(close, 20) > close\n"
        "b = pred and ta.roc(close, 3) > 0\n"
        "c = pred and ta.lowest(close, 3) > low\n"
        "d = pred and ta.highest(close, 3) < high\n"
        "e = pred or close > ta.sma(close, 5)\n"
        "f = pred ? ta.sma(close, 7) : close\n"
        "g = pred or ta.ema(close, 21) > close\n"
        "h = pred ? ta.ema(close, 22) : close\n"
        "i = ta.ema(close, 23)\n"
        "plot((a or b or c or d or e or g) ? f + h + i : close)"
    )
    for name in ("lowest", "highest"):
        assert f"std::vector<double> _precalc__ta_{name}_" in cpp
        assert f"_use_precalc ? _precalc__ta_{name}_" in cpp
    assert "std::vector<double> _precalc__ta_roc_" not in cpp
    assert "_PFLazySaturatedROC3Clock" in cpp
    assert (
        ".evaluate(current_bar_.close, "
        "_pf_lazy_saturated_roc3_close_history[3], bar_index_)"
    ) in cpp
    assert len(re.findall(r"std::vector<double> _precalc__ta_ema_", cpp)) == 1
    assert len(re.findall(r"std::vector<double> _precalc__ta_sma_", cpp)) == 2
    assert len(re.findall(r"_use_precalc \? _precalc__ta_sma_", cpp)) >= 2


def test_precalculated_extrema_direct_history_uses_chart_bar_clock():
    """``ta.highest/lowest(...)[k]`` must index their precalculated series.

    The surrounding Pine-v6 boolean remains lazy. Only the already-computed
    direct-call history changes from a sparse evaluation clock to the chart-bar
    clock used by the same TA site's ``_precalc_*`` values.
    """
    cpp = _cpp(
        "pred = close > open\n"
        "lo = pred and close < ta.lowest(low, 20)[1]\n"
        "hi = pred and close > ta.highest(high, 10)[2]\n"
        "plot((lo or hi) ? 1 : 0)"
    )

    assert "pred &&" in cpp
    assert "std::vector<double> _precalc__ta_lowest_1" in cpp
    assert "std::vector<double> _precalc__ta_highest_2" in cpp
    assert "static_cast<std::size_t>(bar_index_) - _pf_hist_offset" in cpp
    assert "_pf_hist_offset_numeric < 0.0L" in cpp
    assert "static_cast<long double>(bar_index_)" in cpp
    assert "return _precalc__ta_lowest_1[(std::size_t)_pf_hist_bar]" in cpp
    assert "return _precalc__ta_highest_2[(std::size_t)_pf_hist_bar]" in cpp
    # Dynamic/non-precalculated execution retains the rollback-safe synthetic
    # history fallback rather than silently becoming eager.
    assert cpp.count("if (history_advances_new_bar()) _hist_call_") >= 2


def test_nonprecalculated_extrema_direct_history_keeps_evaluation_clock():
    """Unsafe extrema inputs must retain their call-local history clock."""
    cpp = _cpp(
        "src = close\n"
        "pred = close > open\n"
        "signal = pred and close > ta.highest(src, 20)[1]\n"
        "plot(signal ? 1 : 0)"
    )

    assert "std::vector<double> _precalc__ta_highest" not in cpp
    assert "_pf_hist_bar" not in cpp
    assert "_ta_highest_1.compute(src)" in cpp
    assert "if (history_advances_new_bar()) _hist_call_" in cpp


def test_recursive_ema_lazy_edges_preserve_eager_operand_positions():
    """Only OR RHS / ternary arms opt out; eager positions still precalc."""
    cpp = _cpp(
        "pred = close > open\n"
        "orLeft = ta.ema(close, 10) > close or pred\n"
        "orRight = pred or ta.ema(close, 11) > close\n"
        "ternaryCond = ta.ema(close, 12) > close ? close : open\n"
        "ternaryTrue = pred ? ta.ema(close, 13) : close\n"
        "ternaryFalse = pred ? close : ta.ema(close, 14)\n"
        "plot(orLeft ? ternaryCond + ternaryTrue + ternaryFalse "
        ": orRight ? close : open)"
    )
    for idx in (1, 3):
        assert f"std::vector<double> _precalc__ta_ema_{idx}" in cpp
        assert f"_use_precalc ? _precalc__ta_ema_{idx}" in cpp
    for idx in (2, 4, 5):
        assert f"ta::EMA _ta_ema_{idx};" in cpp
        assert f"std::vector<double> _precalc__ta_ema_{idx}" not in cpp
        assert f"_use_precalc ? _precalc__ta_ema_{idx}" not in cpp
        assert f"_ta_ema_{idx}.compute(current_bar_.close)" in cpp


def test_lazy_chart_ema_does_not_reclassify_security_evaluator_ema():
    """Security payload EMA keeps evaluator-local state, not chart scope."""
    cpp = _cpp(
        "pred = close > open\n"
        "chart = pred or ta.ema(close, 3) > close\n"
        "sec = request.security(syminfo.tickerid, \"60\", "
        "close > open or ta.ema(close, 4) > close)\n"
        "plot(chart ? sec : close)"
    )
    assert "std::vector<double> _precalc__ta_ema_1" not in cpp
    assert "_ta_ema_1.compute(current_bar_.close)" in cpp
    security_body = cpp.split("void _eval_security_0", 1)[1].split("\n    }", 1)[0]
    assert "ta::EMA _sec0__ta_ema_2;" in cpp
    assert "_sec0__ta_ema_2.compute(bar.close)" in security_body
    assert " || " in security_body


def test_lazy_udf_ema_uses_callsite_state_without_precalc():
    """A lazy UDF call keeps a distinct EMA clock from an eager sibling."""
    cpp = _cpp(
        "f() =>\n"
        "    ta.ema(close, 3)\n"
        "pred = close > open\n"
        "lazy = pred and f() > close\n"
        "eager = f()\n"
        "plot(lazy ? eager : close)"
    )
    assert "std::vector<double> _precalc__ta_ema" not in cpp
    assert "ta::EMA _ta_ema_1;" in cpp
    assert "ta::EMA _ta_ema_1_cs1;" in cpp
    lazy_body = cpp.split("double f_cs0()", 1)[1].split("\n    }", 1)[0]
    eager_body = cpp.split("double f_cs1()", 1)[1].split("\n    }", 1)[0]
    assert "_ta_ema_1.compute(current_bar_.close)" in lazy_body
    assert "_ta_ema_1_cs1.compute(current_bar_.close)" in eager_body
    assert "lazy = (pred &&" in cpp and "f_cs0()" in cpp
    assert "eager = f_cs1();" in cpp


def test_ta_precalc_keeps_security_context_and_rhs_sma():
    """Security-local TA is distinct; a referenced chart expression is not."""
    cpp = _cpp(
        "pred = close > open\n"
        "chart = pred and close > ta.sma(close, 3)\n"
        "sec = request.security(syminfo.tickerid, \"60\", "
        "close > open and close > ta.sma(close, 4))\n"
        "secChart = request.security(syminfo.tickerid, \"60\", chart)\n"
        "plot(chart ? sec + secChart : close)"
    )
    assert "std::vector<double> _precalc__ta_sma_1" not in cpp
    assert "std::vector<double> _precalc__ta_sma_2" in cpp


def test_ta_precalc_skips_and_rhs_sma_in_tuple_assignment():
    """Top-level TupleAssign/TupleLiteral payloads participate in classification."""
    cpp = _cpp(
        "pred = close > open\n"
        "[a, b] = [pred and ta.sma(close, 3) > close, close]\n"
        "plot(a ? b : close)"
    )
    assert "std::vector<double> _precalc__ta_sma" not in cpp
    assert "_use_precalc ? _precalc__ta_sma" not in cpp
    assert "ta::SMA _ta_sma_1;" in cpp


def test_ta_precalc_skips_and_rhs_sma_in_assignment_subscript_target():
    """An assignment target can contain evaluated expressions in its index."""
    cpp = _cpp(
        "pred = close > open\n"
        "xs = array.new_float(2, 0.0)\n"
        "xs[pred and ta.sma(close, 3) > close ? 0 : 1] := close\n"
        "plot(array.get(xs, 0))"
    )
    assert "std::vector<double> _precalc__ta_sma" not in cpp
    assert "_use_precalc ? _precalc__ta_sma" not in cpp
    assert "_ta_sma_1.compute(current_bar_.close)" in cpp


def test_ta_precalc_walks_nested_func_call_keyword_values_under_and_rhs():
    """Lazy context propagates through keyword values and nested calls."""
    cpp = _cpp(
        "pred = close > open\n"
        "x = pred and label.get_x(id = "
        "label.new(bar_index, ta.sma(close, 3))) > 0\n"
        "plot(x ? 1 : 0)"
    )
    assert "std::vector<double> _precalc__ta_sma" not in cpp
    assert "_use_precalc ? _precalc__ta_sma" not in cpp
    assert "ta::SMA _ta_sma_1;" in cpp


def test_ta_precalc_walks_chained_call_receiver_under_and_rhs():
    """An evaluated call receiver inherits its enclosing lazy context."""
    cpp = _cpp(
        "pred = close > open\n"
        "x = pred and label.new(x = bar_index, "
        "y = ta.sma(close, 3)).get_y() > close\n"
        "plot(x ? 1 : 0)"
    )
    assert "std::vector<double> _precalc__ta_sma" not in cpp
    assert "_use_precalc ? _precalc__ta_sma" not in cpp
    assert "_ta_sma_1.compute(current_bar_.close)" in cpp


def test_ta_precalc_security_positional_skips_only_expression_payload():
    """Security symbol/timeframe are chart context; its payload is not."""
    cpp = _cpp(
        "pred = close > open\n"
        "sec = request.security("
        "(pred and ta.sma(close, 3) > close) ? syminfo.tickerid : syminfo.tickerid, "
        "(pred and ta.sma(close, 4) > close) ? \"60\" : \"60\", "
        "pred and ta.sma(close, 5) > close)\n"
        "plot(sec ? 1 : 0)"
    )
    assert "std::vector<double> _precalc__ta_sma_1" not in cpp
    assert "std::vector<double> _precalc__ta_sma_2" not in cpp
    assert "std::vector<double> _precalc__ta_sma_3" in cpp


def test_ta_precalc_security_lower_tf_keywords_skip_only_expression_payload():
    """Keyword-bound lower-TF payload has the same evaluator boundary."""
    cpp = _cpp(
        "pred = close > open\n"
        "sec = request.security_lower_tf("
        "symbol = (pred and ta.sma(close, 3) > close) "
        "? syminfo.tickerid : syminfo.tickerid, "
        "timeframe = (pred and ta.sma(close, 4) > close) ? \"15\" : \"15\", "
        "expression = pred and ta.sma(close, 5) > close)\n"
        "plot(array.size(sec))"
    )
    assert "std::vector<double> _precalc__ta_sma_1" not in cpp
    assert "std::vector<double> _precalc__ta_sma_2" not in cpp
    assert "std::vector<double> _precalc__ta_sma_3" in cpp


def test_ta_precalc_walks_type_field_defaults():
    """Accepted UDT field defaults participate in chart classification."""
    cpp = _cpp(
        "type State\n"
        "    bool ready = close > open and ta.sma(close, 3) > close\n"
        "s = State.new()\n"
        "plot(s.ready ? 1 : 0)"
    )
    assert "std::vector<double> _precalc__ta_sma" not in cpp
    assert "_use_precalc ? _precalc__ta_sma" not in cpp
    assert "ta::SMA _ta_sma_1;" in cpp


def test_text_align_wrapper_param_infers_string():
    cpp = _cpp(
        "var table dash = table.new(position.top_right, 1, 1)\n"
        "cell(alignMode) =>\n"
        "    table.cell(dash, 0, 0, \"x\", text_halign = alignMode)\n"
        "cell(text.align_right)\n"
        "plot(close)"
    )
    assert "cell(std::string alignMode)" in cpp
    assert "cell(int alignMode)" not in cpp


# ---------------------------------------------------------------------------
# Round 2: tuple-element type retention + UDF param/return type inference
# (jevondijefferson / thulashimohanr blockers)
# ---------------------------------------------------------------------------
def test_tuple_return_infers_local_string_var_from_hint():
    # jevon's wyckoff_displayStructure: ``string bullTag = na`` then the tuple
    # ``[..., bullTag, bearTag]``. The tuple element must infer std::string
    # (not double) from the explicit ``string`` hint on the local decl.
    cpp = _cpp(
        "f() =>\n"
        "    string bullTag = na\n"
        "    string bearTag = na\n"
        "    bullTag := \"BOS\"\n"
        "    [bullTag, bearTag]\n"
        "_a = f()\n"
        "plot(close)"
    )
    # The function's tuple return type includes std::string for both elements.
    assert "std::tuple<std::string, std::string>" in cpp


def test_tuple_return_infers_local_string_from_nested_decl():
    # The local may be declared inside a nested if/for block; the inference
    # walks the whole function body, not just the top level.
    cpp = _cpp(
        "f(bool c) =>\n"
        "    if c\n"
        "        string tag = \"a\"\n"
        "        tag\n"
        "    else\n"
        "        string tag = \"b\"\n"
        "        tag\n"
        "_a = f(true)\n"
        "plot(close)"
    )
    assert "std::string" in cpp


def test_udt_param_emits_struct_reference():
    # jevon's f_get(... pivot hi, pivot lo): the param must emit as ``pivot&``
    # (not ``double``) so member access + reference semantics work.
    cpp = _cpp(
        "type pivot\n"
        "    float level\n"
        "var pivot p = pivot.new(0.0)\n"
        "f(pivot lo) =>\n"
        "    lo.level := close\n"
        "    lo.level\n"
        "_z = f(p)\n"
        "plot(close)"
    )
    assert "f(pivot& lo)" in cpp
    assert "f(double lo)" not in cpp


def test_untyped_param_infers_string_from_callsite():
    # thulashimohanr's getLineStyle(s) where s is used as a string: the call
    # site (inside drawLevels) passes drawLevels' ``string styleStr`` param, so
    # s emits as std::string. Callee defined before caller (matches the real
    # strategy: getLineStyle at line 87, drawLevels at 129).
    cpp = _cpp(
        "inner(s) =>\n"
        "    s == \"solid\"\n"
        "outer(string styleStr) =>\n"
        "    inner(styleStr)\n"
        "_z = outer(\"solid\")\n"
        "plot(close)"
    )
    assert "inner(std::string s)" in cpp


def test_global_tuple_assign_targets_become_members():
    # thulashimohanr's ``[pdH, pdL] = request.security(...)`` — the tuple-assign
    # targets must be declared as class members (else "undeclared identifier").
    cpp = _cpp(
        "[pdH, pdL] = request.security(syminfo.tickerid, \"D\", [high[1], low[1]])\n"
        "plot(pdH)"
    )
    # Both targets are declared as members and assigned from the security tuple.
    assert "double pdH" in cpp
    assert "double pdL" in cpp


def test_array_returning_function_emits_vector():
    # thulashimohanr's buildPDLevels() => array.from(...): the return type is
    # std::vector<double>, and a caller's local infers the same.
    cpp = _cpp(
        "build() =>\n"
        "    array.from(1.0, 2.0, 3.0)\n"
        "allLevels = build()\n"
        "plot(array.size(allLevels))"
    )
    assert "std::vector<double> build()" in cpp
    assert "std::vector<double> allLevels" in cpp


def test_void_array_mutator_as_last_expr_is_statement():
    # thulashimohanr's clearORSet ends in array.clear(): a void Pine array
    # mutator must emit as a statement, not ``return arr.clear();``.
    cpp = _cpp(
        "var float[] a = array.new<float>()\n"
        "clear() =>\n"
        "    array.clear(a)\n"
        "_z = clear()\n"
        "plot(close)"
    )
    assert "a.clear();" in cpp
    assert "return a.clear()" not in cpp


def test_str_tonumber_and_length_typed_numeric():
    # thulashimohanr's revHour/revMinute from str.tonumber(...) arithmetic:
    # the result must be numeric (double/int), not std::string.
    cpp = _cpp(
        "rt = input.string(\"1111\")\n"
        "h = str.tonumber(rt) - 5\n"
        "n = str.length(rt)\n"
        "plot(h)"
    )
    assert "double h" in cpp
    assert "int n" in cpp


def test_input_string_unresolvable_default_is_safe():
    # input.string(size.tiny, ...) — size.tiny is unresolved; the default must
    # NOT pass a null char* to get_input_string (strlen crash). It coerces to
    # an empty string.
    cpp = _cpp(
        "sz = input.string(size.tiny, \"Label Size\", options=[size.tiny])\n"
        "plot(close)"
    )
    assert 'get_input_string("Label Size", std::string(""))' in cpp
    assert 'get_input_string("Label Size", 0)' not in cpp


def test_per_call_site_clone_of_drawing_var_is_handle_not_double():
    # jevon's ``var line topLine`` inside a multi-call-site function: the
    # per-call-site clones must be ``Line`` (not ``double``) so they can hold a
    # real handle id.
    cpp = _cpp(
        "type pivot\n"
        "    float lvl\n"
        "f() =>\n"
        "    var line ln = line.new(bar_index, close, bar_index + 1, close)\n"
        "    line.set_x2(ln, bar_index)\n"
        "    close\n"
        "_a = f()\n"
        "_b = f()\n"
        "plot(_a)"
    )
    # No clone of ``ln`` should be declared ``double``.
    import re
    bad = [l for l in cpp.split("\n") if re.search(r"\bln(_cs\d+)?\b", l) and l.strip().startswith("double ")]
    assert not bad, f"drawing var clone typed double: {bad}"
    assert "Line ln" in cpp


# ---------------------------------------------------------------------------
# Round 3: function-scoped ``var`` one-shot initializer semantics
# (jevondijefferson "drawing access on na handle" + counter root cause)
# ---------------------------------------------------------------------------
def test_function_scoped_var_numeric_init_runs_once():
    # Minimal counter: ``var int c = 5`` inside a function must initialize to 5
    # on the first call (NOT default 0). Previously the initializer was dropped
    # entirely and the member started at its default-constructed value.
    cpp = _cpp(
        "counter() =>\n"
        "    var int c = 5\n"
        "    c := c + 1\n"
        "    c\n"
        "_z = counter()\n"
        "plot(_z)"
    )
    # The initializer must be emitted, guarded by a per-function init flag.
    assert "_fvinit_counter" in cpp
    assert "c = 5" in cpp
    assert "bool _fvinit_counter" in cpp  # the flag member exists
    # The init is gated (runs once), not unconditionally each call.
    assert "if (!_fvinit_counter" in cpp


def test_function_scoped_var_drawing_handle_init_runs_once():
    # ``var line topLine = line.new(...)`` inside a function must actually call
    # pf_line_new on the first call. Before the fix the handle stayed na and
    # later set_x2 threw "drawing access on na handle".
    cpp = _cpp(
        "f() =>\n"
        "    var line topLine = line.new(bar_index, close, bar_index + 1, close)\n"
        "    line.set_x2(topLine, bar_index)\n"
        "    close\n"
        "_a = f()\n"
        "plot(_a)"
    )
    assert "pf_line_new(" in cpp
    assert "if (!this->_pf_var_init_topLine)" in cpp
    # The constructor must NOT eagerly initialize the handle (no bar values
    # available at construction time).
    assert "topLine(pf_line_new" not in cpp


def test_function_scoped_var_drawing_handle_per_clone_init():
    # A multi-call-site function clones the var member; each clone must be
    # initialized independently with its own init flag.
    cpp = _cpp(
        "f() =>\n"
        "    var line topLine = line.new(bar_index, close, bar_index + 1, close)\n"
        "    line.set_x2(topLine, bar_index)\n"
        "    close\n"
        "_a = f()\n"
        "_b = f()\n"
        "plot(_a)"
    )
    # cs0 inits topLine, cs1 inits topLine_cs1 — each with its own flag.
    assert "if (!this->_pf_var_init_topLine)" in cpp
    assert "if (!this->_pf_var_init_topLine_cs1)" in cpp
    assert "topLine = pf_line_new(" in cpp
    assert "topLine_cs1 = pf_line_new(" in cpp
    assert "bool _pf_var_init_topLine = false;" in cpp
    assert "bool _pf_var_init_topLine_cs1 = false;" in cpp


def test_function_scoped_var_udt_init_runs_once():
    # ``var pivot p = pivot.new(0.0)`` inside a function must lower the UDT
    # constructor on first call (was previously dropped, leaving p at default).
    cpp = _cpp(
        "type pivot\n"
        "    float lvl\n"
        "f() =>\n"
        "    var pivot p = pivot.new(0.0)\n"
        "    p.lvl := close\n"
        "    p.lvl\n"
        "_z = f()\n"
        "plot(_z)"
    )
    assert "_fvinit_f" in cpp
    # The UDT constructor expression is lowered inside the guarded init block.
    assert "p = pivot{" in cpp or "p = pivot(" in cpp


def test_function_scoped_var_na_drawing_handle_skips_assignment():
    # ``var line x = na`` (no constructor): the default-constructed member is
    # already the na sentinel, so the init block must NOT emit a type-mismatched
    # ``x = na<double>()`` assignment.
    cpp = _cpp(
        "f() =>\n"
        "    var line x = na\n"
        "    x := line.new(bar_index, close, bar_index + 1, close)\n"
        "    line.set_x2(x, bar_index)\n"
        "    close\n"
        "_a = f()\n"
        "plot(_a)"
    )
    assert "x = na<double>()" not in cpp
    assert "bool _fvinit_f" in cpp


def test_function_scoped_var_not_in_constructor_init_list():
    # Function-scoped var members are initialized once-per-call in the function
    # body, NOT in the constructor initializer list (avoid double-init + allows
    # bar-dependent initializers).
    cpp = _cpp(
        "counter() =>\n"
        "    var int c = 5\n"
        "    c := c + 1\n"
        "    c\n"
        "_z = counter()\n"
        "plot(_z)"
    )
    # The constructor must not carry ``c(5)``.
    import re
    m = re.search(r"GeneratedStrategy\(\)\s*:([^\n]*)", cpp)
    assert m is None or "c(5)" not in m.group(0)


def test_strategy_exit_profit_loss_passes_relative_ticks_to_engine():
    # strategy.exit(profit/loss) can be issued while its entry is still pending.
    # Codegen must not convert the tick offsets to absolute prices using
    # position_entry_price_ at call time, because the actual entry fill may not
    # exist until the next bar.
    cpp = _cpp(
        "if bar_index == 0\n"
        "    strategy.entry(\"L\", strategy.long)\n"
        "    strategy.exit(\"X\", \"L\", profit=40, loss=20)\n"
        "plot(close)"
    )
    assert "position_entry_price_ +" not in cpp
    assert "position_entry_price_ -" not in cpp
    assert 'strategy_exit(std::string("X"), std::string("L"), na<double>(), na<double>()' in cpp
    assert ", 100.0, \"\", na<double>(), \"\", 40, 20);" in cpp


def test_string_concat_preserves_top_level_local_string_types():
    cpp = _cpp(
        "if barstate.islast\n"
        "    role_txt = close > open ? \"run\" : \"next\"\n"
        "    status_icon = close > open ? \"ok\" : \"  \"\n"
        "    row_label = status_icon + \"DCA-\" + str.tostring(bar_index) + role_txt\n"
        "    label.new(bar_index, close, row_label)\n"
        "plot(close)"
    )
    assert "std::to_string(status_icon)" not in cpp
    assert "std::to_string(role_txt)" not in cpp
    assert "std::string row_label" in cpp


def test_string_concat_preserves_udt_for_in_field_string_type():
    cpp = _cpp(
        "type Level\n"
        "    string name\n"
        "    float price\n"
        "var levels = array.new<Level>()\n"
        "if bar_index == 0\n"
        "    array.push(levels, Level.new(\"PDH\", high))\n"
        "for lvl in levels\n"
        "    label.new(bar_index, lvl.price, \"hit \" + lvl.name)\n"
        "plot(close)"
    )
    assert "std::to_string(lvl.name)" not in cpp
    assert 'std::string("hit ") + lvl.name' in cpp


def test_for_loop_with_explicit_by_infers_descending_direction():
    cpp = _cpp(
        "limit = input.int(3)\n"
        "var vals = array.new<int>()\n"
        "if bar_index == 0\n"
        "    for i = limit to 0 by 1\n"
        "        array.push(vals, i)\n"
        "plot(close)"
    )
    assert "const bool _for_down_" in cpp
    assert "int _for_step_" in cpp
    assert "i += (_for_down_" in cpp
    assert "_for_end_" in cpp and "_for_end_0 = (0)" in cpp
    assert "for (int i = limit; i <= 0; i += 1)" not in cpp


def test_numeric_ternary_with_int_literal_and_float_branch_declares_double():
    cpp = _cpp(
        "rng = high - low\n"
        "pressure = rng == 0 ? 0 : (close - low) / rng\n"
        "plot(pressure)"
    )
    assert "double pressure" in cpp
    assert "int pressure" not in cpp


# ---------------------------------------------------------------------------
# 10. duplicated array receivers are evaluated once
# ---------------------------------------------------------------------------
def test_nested_array_slice_aggregates_materialize_one_receiver():
    cpp = _cpp(
        "a = array.from(1.0, 3.0, 2.0)\n"
        "mx = array.max(array.slice(a, 0, 2))\n"
        "mn = array.min(array.slice(a, 1, 3))\n"
        "plot(mx + mn)"
    )

    mx_line = next(line for line in cpp.splitlines() if line.strip().startswith("mx = "))
    mn_line = next(line for line in cpp.splitlines() if line.strip().startswith("mn = "))

    # Before the fix, each constructor appeared twice: max/min_element took
    # begin() from one temporary vector and end() from another (undefined
    # behaviour).  Each source slice must now produce one vector constructor,
    # and each iterator pair must use the same named receiver binding.
    assert mx_line.count("std::vector<double>(") == 1
    assert mn_line.count("std::vector<double>(") == 1
    assert re.search(
        r"std::max_element\((__pf_array_receiver_\d+)\.begin\(\),\1\.end\(\)\)",
        mx_line,
    )
    assert re.search(
        r"std::min_element\((__pf_array_receiver_\d+)\.begin\(\),\1\.end\(\)\)",
        mn_line,
    )
