"""Regression tests for codegen bugs found by pinescript-scrapper validation.

Covers five fix families:
  1. drawing-handle ``na`` reset/assignment (Box{}/Line{}/... not na<double>()),
     plus typed ``na`` for string/int/bool declaration init.
  2. void drawing setter used as a UDF's last expression / if-branch value.
  3. ``request.security`` whose timeframe is a UDF parameter (resolved from the
     function's call sites; dead-code UDFs fall back to the chart timeframe).
  4. parser handling of ``T[]`` array-typed function parameters (``float[] arr``,
     ``line[] ln``) — previously the whole function was dropped.
  5. typed drawing array constructors ``array.new_line/box/label/linefill``.
"""

from pineforge_codegen import transpile


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


def test_security_param_tf_mixed_literals_rejected():
    # Called with two different literal tfs -> a single evaluator cannot serve
    # both; the analyzer now rejects loudly instead of silently collapsing onto
    # the chart timeframe (the masayanfx multi-time-score root cause).
    import pytest
    with pytest.raises(Exception, match="multiple distinct literal timeframes"):
        _cpp(
            "f(tf) =>\n"
            "    request.security(syminfo.tickerid, tf, close)\n"
            "a = f(\"5\")\n"
            "b = f(\"15\")\n"
            "plot(a + b)"
        )


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


def test_security_param_tf_six_distinct_literals_rejected():
    # The masayanfx shape: one param-tf security UDF called with six different
    # literals. Must reject with the mixed-literals error.
    import pytest
    with pytest.raises(Exception, match="multiple distinct literal timeframes"):
        _cpp(
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
    assert "_fvinit_f" in cpp
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
    assert "if (!_fvinit_f_cs0)" in cpp
    assert "if (!_fvinit_f_cs1)" in cpp
    assert "topLine = pf_line_new(" in cpp
    assert "topLine_cs1 = pf_line_new(" in cpp
    assert "bool _fvinit_f_cs0 = false;" in cpp
    assert "bool _fvinit_f_cs1 = false;" in cpp


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

