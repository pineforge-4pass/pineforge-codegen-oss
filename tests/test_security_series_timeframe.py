"""Closed series timeframe choices register independent requested contexts."""

import csv
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.ast_nodes import BinOp
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.timeframe_values import TimeframeValues
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._timeframe_value_cases import CASES as SCALAR_CASES


FIXTURES = Path(__file__).parent / "fixtures" / "series_timeframe"
HEADER = '//@version=6\nstrategy("Selected timeframe", currency = currency.NONE)\n'


def source(name):
    return (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")


def registrations(cpp):
    return re.findall(r"^\s*register_security_eval\((.*)\);$", cpp, re.MULTILINE)


def member_body(cpp, signature):
    openings = list(re.finditer(
        rf"^    {re.escape(signature)} \{{\n", cpp, re.MULTILINE))
    assert len(openings) == 1, f"Expected one definition of {signature}"
    start = openings[0].end()
    depth = 1
    # Selector lambdas can use their own indentation. Count braces outside
    # quoted literals and comments so their blocks stay inside the member.
    for token in re.finditer(
            r"""//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[{}]""",
            cpp[start:], re.DOTALL):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                body = cpp[start:start + token.start()].rstrip(" \t")
                assert body.strip(), f"Expected a nonempty body for {signature}"
                return body
    raise AssertionError(f"Unterminated definition of {signature}")


def chart_body(cpp):
    return member_body(cpp, "void on_source_bar(const Bar& bar) override")


def requested_body(cpp, sec_id):
    return member_body(
        cpp, f"void _eval_security_{sec_id}(const Bar& bar, bool is_complete)")


def assert_selection_tail(cpp, *branches):
    # Each site is compared in order, followed immediately by the stop and close.
    expected = [f"    {branch}" for branch in branches]
    expected.extend((
        '    _PF_ENGINE_INVARIANT("Requested timeframe matched no registered timeframe", std::logic_error);',
        "}())",
    ))
    assert "\n".join(expected) in cpp


def export_rows(name):
    with (FIXTURES / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def test_body_helpers_extract_complete_members_without_neighboring_methods():
    cpp = r"""class ExtractionSample {
    void preceding_member() {
        preceding_counter += 1;
    }
    void on_source_bar(const Bar& bar) override {
        chart_counter += 1;
        selected_result = ([&]() {
    if (chart_counter > 0) {
        return chart_counter;
    }
    return 0;
}());
        const char* message_text = "a brace } and an escaped quote \" {";
        const char brace_character = '}';
        // } must not close the member.
        /* { Neither should a block comment's brace. */
        chart_snapshot = chart_counter;
    }
    void _eval_security_0(const Bar& bar, bool is_complete) {
        if (is_complete) {
            requested_counter_alpha += 1;
        }
        requested_result_alpha = requested_counter_alpha;
    }
    void _eval_security_1(const Bar& bar, bool is_complete) {
        requested_counter_beta += 1;
        requested_result_beta = requested_counter_beta;
    }
    void following_member() {
        following_counter += 1;
    }
};
"""
    assert chart_body(cpp) == r"""        chart_counter += 1;
        selected_result = ([&]() {
    if (chart_counter > 0) {
        return chart_counter;
    }
    return 0;
}());
        const char* message_text = "a brace } and an escaped quote \" {";
        const char brace_character = '}';
        // } must not close the member.
        /* { Neither should a block comment's brace. */
        chart_snapshot = chart_counter;
"""
    assert requested_body(cpp, 0) == """        if (is_complete) {
            requested_counter_alpha += 1;
        }
        requested_result_alpha = requested_counter_alpha;
"""
    assert requested_body(cpp, 1) == """        requested_counter_beta += 1;
        requested_result_beta = requested_counter_beta;
"""


@pytest.mark.parametrize("cpp", (
    "class ExtractionSample {};\n",
    "    void on_source_bar(const Bar& bar) override {\n        chart_counter += 1;\n",
    "    void on_source_bar(const Bar& bar) override {\n    }\n",
    "    void on_source_bar(const Bar& bar) override {\n        chart_counter += 1;\n    }\n"
    "    void on_source_bar(const Bar& bar) override {\n        chart_counter += 2;\n    }\n",
))
def test_chart_body_rejects_missing_unterminated_empty_or_duplicate_members(cpp):
    with pytest.raises(AssertionError):
        chart_body(cpp)


@pytest.mark.parametrize("name,timeframes", (
    ("if-price", ("240", "1D")),
    ("ternary-price", ("240", "1D")),
    ("if-parity", ("240", "1D")),
    ("hour-if-price", ("60", "240")),
    ("hour-if-parity", ("60", "240")),
    ("hour-ternary-price", ("60", "240")),
    ("sma-parity", ("60", "240")),
    ("history-parity", ("60", "240")),
))
def test_each_literal_arm_registers_independently(name, timeframes):
    # Arm traversal retains source order; each site gets one literal timeframe.
    cpp = transpile(source(name))
    assert registrations(cpp) == [
        f'{i}, "{tf}", input_tf_, false, false'
        for i, tf in enumerate(timeframes)
    ]
    assert all("current_bar_" not in line for line in registrations(cpp))
    assert "void _eval_security_0(" in cpp
    assert "void _eval_security_1(" in cpp


@pytest.mark.parametrize("name,condition", (
    ("if-price", "auto _pna_l = (current_bar_.close); auto _pna_r = (current_bar_.open);"),
    ("ternary-price", "auto _pna_l = (current_bar_.close); auto _pna_r = (current_bar_.open);"),
    ("if-parity", "std::fmod((double)(pine_bar_index()), (double)(2))"),
))
def test_chart_selection_reads_the_authored_timeframe_once(name, condition):
    # The normal chart assignment computes the condition; the request reads
    # that string once and chooses its independently updated arm's result.
    body = chart_body(transpile(source(name)))
    assert condition in body
    selected = re.search(
        r"const std::string _pf_selected_tf = ([^;]+);", body)
    assert selected is not None and "pick_tf" in selected.group(1)
    assert_selection_tail(
        body,
        'if (_pf_selected_tf == "240") return _req_sec_0;',
        'if (_pf_selected_tf == "1D") return _req_sec_1;',
    )
    assert body.index(condition) < selected.start()


@pytest.mark.parametrize("selection", (
    'close > open ? "75" : "180"',
    'if close > open\n        "75"\n    else\n        "180"',
    'switch close > open\n        true => "75"\n        => "180"',
))
@pytest.mark.parametrize("scope", ("block", "function"))
@pytest.mark.parametrize("lower_tf", (False, True))
def test_local_alias_registers_exact_declaration_arms(selection, scope, lower_tf):
    # The same-spelled global must contribute neither its input nor its arms.
    setup = 'chosen_local = input.timeframe("2D", "Outer frame")\n'
    request_name = "security_lower_tf" if lower_tf else "security"
    local = ('    chosen_local = ' + selection + '\n'
             '    alias_local = chosen_local\n'
             f'    got_local = request.{request_name}(syminfo.tickerid, alias_local, close)\n')
    result = "array.size(got_local)" if lower_tf else "got_local"
    if scope == "function":
        pine = (HEADER + setup + 'read_local() =>\n' + local
                + f'    {result}\nplot(read_local())\n')
    else:
        pine = (HEADER + setup + 'if bar_index > 0\n' + local
                + f'    strategy.entry("Long", strategy.long, qty = {result})\n')
    cpp = transpile(pine)
    if lower_tf:
        assert re.findall(
            r"^\s*register_security_lower_tf_eval\((.*)\);$", cpp, re.MULTILINE
        ) == ['0, "75", input_tf_', '1, "180", input_tf_']
    else:
        assert registrations(cpp) == [
            '0, "75", input_tf_, false, false',
            '1, "180", input_tf_, false, false',
        ]
    assert re.search(
        r"const std::string _pf_selected_tf = [^;\n]*alias_local(?:\[0\])?;",
        cpp)
    result_prefix = "_req_sec_lower_tf_" if lower_tf else "_req_sec_"
    assert_selection_tail(
        cpp,
        f'if (_pf_selected_tf == "75") return {result_prefix}0;',
        f'if (_pf_selected_tf == "180") return {result_prefix}1;',
    )


def test_sibling_local_aliases_keep_distinct_arm_sets():
    pine = (HEADER
            + 'if close > open\n'
            '    chosen_local = volume > 0 ? "75" : "180"\n'
            '    got_local = request.security(syminfo.tickerid, chosen_local, close)\n'
            '    strategy.entry("First", strategy.long, qty = got_local)\n'
            'if close < open\n'
            '    chosen_local = volume > 0 ? "30" : "2D"\n'
            '    got_local = request.security(syminfo.tickerid, chosen_local, open)\n'
            '    strategy.entry("Second", strategy.long, qty = got_local)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        '0, "75", input_tf_, false, false',
        '1, "30", input_tf_, false, false',
        '2, "180", input_tf_, false, false',
        '3, "2D", input_tf_, false, false',
    ]
    assert_selection_tail(
        cpp,
        'if (_pf_selected_tf == "75") return _req_sec_0;',
        'if (_pf_selected_tf == "180") return _req_sec_2;',
    )
    assert_selection_tail(
        cpp,
        'if (_pf_selected_tf == "30") return _req_sec_1;',
        'if (_pf_selected_tf == "2D") return _req_sec_3;',
    )


def test_request_in_loop_selects_at_each_iteration_before_retaining_last_value():
    pine = (HEADER + 'got_v = 0.0\n'
            'for step_idx = 0 to 1\n'
            '    pick_tf = if step_idx > 0\n'
            '        "240"\n    else\n        "1D"\n'
            '    got_v := request.security(syminfo.tickerid, pick_tf, bar_index)\n'
            'qty_v = 1 + int(got_v) % 5\n'
            'if bar_index > 6 and bar_index % 7 == 3\n'
            '    strategy.entry("Long", strategy.long, qty = qty_v)\n'
            'if bar_index > 6 and bar_index % 7 == 5\n'
            '    strategy.close("Long")\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        '0, "240", input_tf_, false, false',
        '1, "1D", input_tf_, false, false',
    ]
    setup = cpp.split("void configure_security_evaluators()", 1)[1].split(
        "\n    }\n", 1)[0]
    assert "pick_tf" not in setup and "step_idx" not in setup
    body = chart_body(cpp)
    selected = re.search(
        r"const std::string _pf_selected_tf = pick_tf(?:\[0\])?;", body)
    assert selected is not None
    assert body.index("for (") < selected.start() < body.index("qty_v")
    assert_selection_tail(
        body,
        'if (_pf_selected_tf == "240") return _req_sec_0;',
        'if (_pf_selected_tf == "1D") return _req_sec_1;',
    )
    for sec_id in (0, 1):
        assert f"_req_sec_{sec_id} = " in cpp
        assert f"void _eval_security_{sec_id}(" in cpp


@pytest.mark.parametrize("payload,element_type", (
    ("close", "double"), ("bar_index", "int"), ("close > open", "bool"),
))
@pytest.mark.parametrize("request_form", ("positional", "keyword"))
def test_lower_timeframe_array_selects_independent_accumulators(
        payload, element_type, request_form):
    arguments = f"syminfo.tickerid, pick_tf, {payload}"
    if request_form == "keyword":
        arguments = f"expression = {payload}, timeframe = pick_tf, symbol = syminfo.tickerid"
    pine = (HEADER + 'pick_tf = close > open ? "30" : "15"\n'
            + f'parts_v = request.security_lower_tf({arguments})\n'
            'qty_v = 1 + array.size(parts_v) % 5\n'
            'if bar_index > 6 and bar_index % 7 == 3\n'
            '    strategy.entry("Long", strategy.long, qty = qty_v)\n'
            'if bar_index > 6 and bar_index % 7 == 5\n'
            '    strategy.close("Long")\n')
    cpp = transpile(pine)
    assert re.findall(
        r"^\s*register_security_lower_tf_eval\((.*)\);$", cpp, re.MULTILINE
    ) == ['0, "30", input_tf_', '1, "15", input_tf_']
    assert registrations(cpp) == []
    body = chart_body(cpp)
    assert re.search(
        r"const std::string _pf_selected_tf = pick_tf(?:\[0\])?;", body)
    assert_selection_tail(
        body,
        'if (_pf_selected_tf == "30") return _req_sec_lower_tf_0;',
        'if (_pf_selected_tf == "15") return _req_sec_lower_tf_1;',
    )
    for sec_id in (0, 1):
        assert f"std::vector<{element_type}> _req_sec_lower_tf_{sec_id}{{}};" in cpp
        assert (
            f"if (security_lower_tf_sub_bar_index({sec_id}) == 0)"
            f" _req_sec_lower_tf_{sec_id}.clear();"
        ) in cpp
        assert f"_req_sec_lower_tf_{sec_id}.push_back(" in cpp
        assert f"case {sec_id}: _eval_security_{sec_id}(" in cpp


def test_loop_local_lower_timeframe_alias_selects_each_iterations_array():
    pine = (HEADER + 'got_count = 0\n'
            'for sample_index = 0 to 1\n'
            '    chosen_local = sample_index > 0 ? "30" : "15"\n'
            '    alias_local = chosen_local\n'
            '    parts_local = request.security_lower_tf(syminfo.tickerid, alias_local, close)\n'
            '    got_count := array.size(parts_local)\n'
            'strategy.entry("Long", strategy.long, qty = got_count + 1)\n')
    cpp = transpile(pine)
    assert re.findall(
        r"^\s*register_security_lower_tf_eval\((.*)\);$", cpp, re.MULTILINE
    ) == ['0, "30", input_tf_', '1, "15", input_tf_']
    setup = cpp.split("void configure_security_evaluators()", 1)[1].split(
        "\n    }\n", 1)[0]
    assert "sample_index" not in setup and "alias_local" not in setup
    body = chart_body(cpp)
    selected = re.search(
        r"const std::string _pf_selected_tf = alias_local(?:\[0\])?;", body)
    assert selected is not None and body.index("for (") < selected.start()
    assert_selection_tail(
        body,
        'if (_pf_selected_tf == "30") return _req_sec_lower_tf_0;',
        'if (_pf_selected_tf == "15") return _req_sec_lower_tf_1;',
    )


@pytest.mark.parametrize("lower_tf", (False, True))
def test_local_open_timeframe_arm_keeps_the_closed_set_refusal(lower_tf):
    request_name = "security_lower_tf" if lower_tf else "security"
    pine = (HEADER + 'if bar_index > 0\n'
            '    chosen_local = close > open ? str.tostring(bar_index) : "15"\n'
            f'    got_local = request.{request_name}(syminfo.tickerid, chosen_local, close)\n'
            '    strategy.entry("Long", strategy.long)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E3111"
    assert diagnostic.args == {"reason": "the timeframe set is not closed at compile time"}
    assert (diagnostic.location.line, diagnostic.location.col) == (4, 35)


@pytest.mark.parametrize("name", ("explicit-price", "explicit-parity",
                                 "hour-fixed-fast", "hour-fixed-slow"))
def test_fixed_requests_keep_the_original_emission_path(name, monkeypatch):
    # Preservation pin, green before and after: disabling only the new
    # expansion hook recreates the unchanged fixed-timeframe emitter.
    pine = source(name)
    actual = transpile(pine)
    with monkeypatch.context() as previous:
        previous.setattr(CodeGen, "_expand_security_series_timeframes",
                         lambda self: None, raising=False)
        assert actual == transpile(pine)
    assert "_pf_selected_tf" not in actual


@pytest.mark.parametrize("setup,tf", (
    ("", '"75"'),
    ('chosen_tf = input.timeframe("90", "Frame")\n', "chosen_tf"),
    ('const string chosen_tf = "2D"\n', "chosen_tf"),
    ("", "timeframe.period"),
    ('chosen_tf = input.bool(true) ? "90" : "180"\n', "chosen_tf"),
    ('chosen_tf = if input.bool(true)\n    "90"\nelse\n    "180"\n', "chosen_tf"),
    ('chosen_tf = switch input.bool(true)\n    true => "90"\n    => "180"\n',
     "chosen_tf"),
    ('choose_mode() => true\n'
     'chosen_tf = choose_mode() ? "90" : "180"\n', "chosen_tf"),
    ('choose_mode() => input.bool(true)\n'
     'chosen_tf = if choose_mode()\n    string saved_tf = "90"\n'
     '    saved_tf\nelse\n    "180"\n', "chosen_tf"),
))
def test_setup_timeframes_are_byte_identical_without_expansion(setup, tf, monkeypatch):
    # Preservation pin: the setup proof admits every condition, so expansion
    # adds neither sites nor text nor counter allocations. The order condition
    # keeps the request live for every row, including direct input conditions.
    pine = (HEADER + setup
            + f"got_v = request.security(syminfo.tickerid, {tf}, close)\n"
            + 'if got_v > close\n    strategy.entry("Long", strategy.long)\n')
    actual = transpile(pine)
    with monkeypatch.context() as previous:
        previous.setattr(CodeGen, "_expand_security_series_timeframes",
                         lambda self: None, raising=False)
        assert actual == transpile(pine)
    # Count call openings because setup-time lambdas can span multiple lines.
    assert actual.count("register_security_eval(") == 1


@pytest.mark.parametrize("selection", (
    'close > open ? "75" : (volume > 0 ? "180" : "2D")',
    'if close > open\n    "75"\nelse\n    if volume > 0\n        "180"\n    else\n        "2D"',
    'switch\n    close > open => "75"\n    volume > 0 => "180"\n    => "2D"',
    'switch bar_index % 3\n    0 => "75"\n    1 => "180"\n    => "2D"',
    'if close > open\n    arm_tf = "75"\n    arm_tf\nelse\n'
    '    arm_tf = volume > 0 ? "180" : "2D"\n    arm_tf',
    'if close > open\n    string arm_tf = "75"\n    arm_tf\nelse\n'
    '    string arm_tf = volume > 0 ? "180" : "2D"\n    arm_tf',
))
def test_nested_selections_and_statement_prefixes_have_a_closed_arm_set(selection):
    # Flattening only the arm inventory yields three sites; chart execution
    # still uses the authored selection, including its local declarations.
    pine = (HEADER + "chosen_tf = " + selection
            + "\ngot_v = request.security(syminfo.tickerid, chosen_tf, close)\nplot(got_v)\n")
    cpp = transpile(pine)
    assert registrations(cpp) == [
        f'{i}, "{tf}", input_tf_, false, false'
        for i, tf in enumerate(("75", "180", "2D"))
    ]
    assert "const std::string _pf_selected_tf = " in chart_body(cpp)
    assert_selection_tail(
        chart_body(cpp),
        'if (_pf_selected_tf == "75") return _req_sec_0;',
        'if (_pf_selected_tf == "180") return _req_sec_1;',
        'if (_pf_selected_tf == "2D") return _req_sec_2;',
    )


def test_repeated_arms_share_one_site_without_collapsing_other_sites():
    # The alias and the repeated literal denote one arm; the fixed request's
    # original ID remains 1, so the new second arm receives ID 2.
    pine = (HEADER + 'const string slow_tf = "180"\n'
            'chosen_tf = close > open ? "75" : (volume > 0 ? slow_tf : "180")\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\n'
            'fixed_v = request.security(syminfo.tickerid, "30", open)\n'
            'plot(got_v + fixed_v)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        '0, "75", input_tf_, false, false',
        '1, "30", input_tf_, false, false',
        '2, "180", input_tf_, false, false',
    ]
    assert_selection_tail(
        chart_body(cpp),
        'if (_pf_selected_tf == "75") return _req_sec_0;',
        'if (_pf_selected_tf == "180") return _req_sec_2;',
    )


def test_identical_series_arms_compare_the_single_registered_site():
    # Deduplication keeps site 0; even its sole return requires a match before the stop.
    pine = (HEADER + 'chosen_tf = close > open ? "75" : "75"\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\n'
            'plot(got_v)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == ['0, "75", input_tf_, false, false']
    assert_selection_tail(
        chart_body(cpp),
        'if (_pf_selected_tf == "75") return _req_sec_0;',
    )


def test_inline_selection_preserves_the_chart_condition_and_merge_flags():
    # The argument is evaluated once inside the selector, retaining its lazy
    # ternary and the same lookahead/gaps arguments on both registrations.
    pine = (HEADER
            + 'got_v = request.security(syminfo.tickerid, close > open ? "75" : "180", '
            'close, gaps = barmerge.gaps_on, lookahead = barmerge.lookahead_on)\n'
            'plot(got_v)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        '0, "75", input_tf_, true, true',
        '1, "180", input_tf_, true, true',
    ]
    assert re.search(
        r"const std::string _pf_selected_tf = .*current_bar_\.close.*"
        r'current_bar_\.open.*\? \(std::string\("75"\)\) : \(std::string\("180"\)\)',
        chart_body(cpp))
    assert 'if (_pf_selected_tf == "75") return _req_sec_0;' in chart_body(cpp)


def test_selection_temporary_does_not_shadow_a_script_binding():
    # The authored timeframe uses the preferred temporary's spelling; the
    # generated binding must differ so its initializer reads that timeframe.
    pine = (HEADER + '_pf_selected_tf = close > open ? "75" : "180"\n'
            'got_v = request.security(syminfo.tickerid, _pf_selected_tf, close)\n'
            'plot(got_v)\n')
    cpp = transpile(pine)
    assert re.search(
        r"const std::string _pf_selected_tf_ = [^;]*_pf_selected_tf",
        chart_body(cpp))
    assert 'if (_pf_selected_tf_ == "75") return _req_sec_0;' in chart_body(cpp)


def test_inputs_constants_and_timeframe_members_resolve_only_at_registration():
    # The input alias, literal constant and chart period are closed leaves;
    # only their selection depends on the current chart bar.
    pine = (HEADER + 'input_tf = input.timeframe("75", "Frame")\n'
            'const string slow_tf = "2D"\n'
            'chosen_tf = close > open ? input_tf : '
            '(volume > 0 ? slow_tf : timeframe.period)\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\n'
            'plot(got_v)\n')
    cpp = transpile(pine)
    lines = registrations(cpp)
    assert len(lines) == 3
    assert 'get_input_string("Frame", std::string("75"))' in lines[0]
    assert lines[1] == '1, "2D", input_tf_, false, false'
    assert lines[2] == '2, script_tf_, input_tf_, false, false'
    assert all("current_bar_" not in line for line in lines)


@pytest.mark.parametrize("setup,arm,expected_tf", (
    ('tf_slow = input.timeframe("240", "Slow frame")\n', "tf_slow",
     'get_input_string("Slow frame", std::string("240"))'),
    ("", "timeframe.period", "script_tf_"),
    ('const string tf_slow = "240"\n', "tf_slow", '"240"'),
    ("", '"240"', '"240"'),
))
@pytest.mark.parametrize("arm_first", (True, False))
def test_select_compares_the_registered_expression_for_each_arm_kind(
        setup, arm, expected_tf, arm_first):
    # Resolution supplies the same input read, chart member or quoted string
    # to registration and comparison; reversing arms also checks each last site.
    arms = (arm, '"60"') if arm_first else ('"60"', arm)
    expected_tfs = (expected_tf, '"60"') if arm_first else ('"60"', expected_tf)
    pine = (HEADER + setup
            + f'chosen_tf = close > open ? {arms[0]} : {arms[1]}\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\n'
            'plot(got_v)\n')
    cpp = transpile(pine)
    lines = registrations(cpp)
    assert len(lines) == 2
    body = chart_body(cpp)
    branches = []
    for sec_id, expected in enumerate(expected_tfs):
        registered = re.fullmatch(
            rf"{sec_id}, (.*), input_tf_, false, false", lines[sec_id])
        condition = f"_pf_selected_tf == {expected}"
        comparison_pattern = r"_pf_selected_tf == (.*)"
        if expected == "script_tf_":
            condition = f"(_pf_selected_tf.empty() || {condition})"
            comparison_pattern = r"\(_pf_selected_tf\.empty\(\) \|\| _pf_selected_tf == (.*)\)"
        compared = re.search(
            rf"^    if \({comparison_pattern}\) return _req_sec_{sec_id};$",
            body, re.MULTILINE)
        assert registered is not None
        assert compared is not None
        assert registered.group(1) == compared.group(1) == expected
        branches.append(f"if ({condition}) return _req_sec_{sec_id};")
    assert_selection_tail(body, *branches)


@pytest.mark.parametrize("expression", (
    '"7" + "5"',
    'str.format("{0}", 75)',
    'frame_name()',
))
@pytest.mark.parametrize("template", (
    'close > open ? {arm} : "180"\n',
    'if close > open\n    {arm}\nelse\n    "180"\n',
    'switch close > open\n    true => {arm}\n    => "180"\n',
))
def test_computed_constant_arms_register_their_values(expression, template):
    pine = (HEADER + 'frame_name() => "75"\n'
            + 'tf_v = ' + template.format(arm=expression)
            + 'got_v = request.security(syminfo.tickerid, tf_v, close)\nplot(got_v)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        '0, "75", input_tf_, false, false',
        '1, "180", input_tf_, false, false',
    ]
    assert_selection_tail(
        chart_body(cpp),
        'if (_pf_selected_tf == "75") return _req_sec_0;',
        'if (_pf_selected_tf == "180") return _req_sec_1;',
    )
    assert re.search(r"const std::string _pf_selected_tf = [^;]*tf_v",
                     chart_body(cpp))


@pytest.mark.parametrize("setup,expression,timeframes", (
    ("", 'close > open ? "2" + "40" : "1" + "D"', ("240", "1D")),
    ('minutes_v = close > open ? 240 : 1440\n',
     'str.tostring(minutes_v)', ("240", "1440")),
    ('minutes_v = close > open ? 240 : 1440\n',
     'str.tostring(value = minutes_v)', ("240", "1440")),
    ('minutes_v = close > open ? 240.0 : 1440.0\n',
     'str.tostring(minutes_v)', ("240", "1440")),
    ("", 'str.tostring((close > open ? 4 : 24) * 60)', ("240", "1440")),
    ("", 'str.tostring((close > open ? 480 : 2880) / 2)', ("240", "1440")),
    ("", 'str.format("{0}", close > open ? 75 : 180)', ("75", "180")),
    ("", '(close > open ? "2" : "1") + (volume > 0 ? "40" : "D")',
     ("240", "2D", "140", "1D")),
    ('frame_text(int amount_v) => str.tostring(amount_v)\n'
     'minutes_v = close > open ? 240 : 1440\n',
     'frame_text(minutes_v)', ("240", "1440")),
    ('frame_text(int amount_v) => str.tostring(amount_v)\n'
     'minutes_v = close > open ? 240 : 1440\n',
     'frame_text(amount_v = minutes_v)', ("240", "1440")),
    ('frame_text() => close > open ? "240" : "1D"\n',
     'frame_text()', ("240", "1D")),
    ('frame_text() =>\n    if close > open\n        "2" + "40"\n'
     '    else\n        "1" + "D"\n',
     'frame_text()', ("240", "1D")),
    ('frame_text() =>\n    switch close > open\n'
     '        true => "2" + "40"\n        => "1" + "D"\n',
     'frame_text()', ("240", "1D")),
    ('join_text(string first_piece, string last_piece) =>\n'
     '    string joined_piece = first_piece + last_piece\n    joined_piece\n',
     'join_text(close > open ? "2" : "1", "D")', ("2D", "1D")),
    ('join_text(string first_piece, string last_piece = "D") =>\n'
     '    first_piece + last_piece\n',
     'join_text(close > open ? "2" : "1")', ("2D", "1D")),
    ('amount_v = 15\n'
     'frame_text(int amount_v) => str.tostring(amount_v)\n'
     'minutes_v = close > open ? 240 : 1440\n',
     'frame_text(minutes_v)', ("240", "1440")),
    ('base_piece = "2"\n'
     'frame_text() =>\n    first_piece = base_piece\n'
     '    string base_piece = "1"\n    first_piece + "D"\n',
     'close > open ? frame_text() : "1D"', ("2D", "1D")),
    ('frame_text() => "2" + "40"\n',
     'close > open ? frame_text() : "240"', ("240",)),
))
def test_values_beneath_pure_operations_register_every_possible_result(
        setup, expression, timeframes):
    pine = (HEADER + setup + f'chosen_tf = {expression}\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\n'
            'plot(got_v)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        f'{index}, "{value}", input_tf_, false, false'
        for index, value in enumerate(timeframes)
    ]
    body = chart_body(cpp)
    assert re.search(r"const std::string _pf_selected_tf = [^;]*chosen_tf", body)
    assert_selection_tail(body, *(
        f'if (_pf_selected_tf == "{value}") return _req_sec_{index};'
        for index, value in enumerate(timeframes)
    ))
    assert all("current_bar_" not in line for line in registrations(cpp))


@pytest.mark.parametrize("lower_tf", (False, True))
def test_setup_controlled_block_can_gain_a_closed_computed_arm(lower_tf):
    # A setup-only selector does not make its arm's price-dependent value
    # fixed. Successful finite-value interpretation must extend the old setup
    # proof even though that proof refuses the arm's chart read.
    request_name = "security_lower_tf" if lower_tf else "security"
    result = "array.size(got_v)" if lower_tf else "got_v"
    pine = (HEADER + 'pick_v = input.bool(true)\n'
            'tf_v = if pick_v\n'
            '    amount_v = close > open ? 75 : 180\n'
            '    str.tostring(amount_v)\n'
            'else\n    "30"\n'
            f'got_v = request.{request_name}(syminfo.tickerid, tf_v, close)\n'
            f'plot({result})\n')
    cpp = transpile(pine)
    if lower_tf:
        assert re.findall(
            r"^\s*register_security_lower_tf_eval\((.*)\);$", cpp, re.MULTILINE
        ) == ['0, "75", input_tf_', '1, "180", input_tf_', '2, "30", input_tf_']
    else:
        assert registrations(cpp) == [
            '0, "75", input_tf_, false, false',
            '1, "180", input_tf_, false, false',
            '2, "30", input_tf_, false, false',
        ]
    result_prefix = "_req_sec_lower_tf_" if lower_tf else "_req_sec_"
    assert_selection_tail(
        chart_body(cpp),
        f'if (_pf_selected_tf == "75") return {result_prefix}0;',
        f'if (_pf_selected_tf == "180") return {result_prefix}1;',
        f'if (_pf_selected_tf == "30") return {result_prefix}2;',
    )


def test_imported_pure_string_helper_has_the_same_value_inventory():
    libraries = {"pftest/FrameText/1": (
        '//@version=6\nlibrary("FrameText")\n'
        'export frame_text(int amount_v) => str.tostring(amount_v)\n'
    )}
    pine = (HEADER + 'import pftest/FrameText/1 as frames_lib\n'
            'minutes_v = close > open ? 240 : 1440\n'
            'chosen_tf = frames_lib.frame_text(minutes_v)\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\n'
            'plot(got_v)\n')
    cpp = transpile(pine, libraries=libraries)
    assert registrations(cpp) == [
        '0, "240", input_tf_, false, false',
        '1, "1440", input_tf_, false, false',
    ]
    assert_selection_tail(
        chart_body(cpp),
        'if (_pf_selected_tf == "240") return _req_sec_0;',
        'if (_pf_selected_tf == "1440") return _req_sec_1;',
    )


@pytest.mark.parametrize("expression", (
    'str.tostring(bar_index)',
    'unknown_frame',
))
@pytest.mark.parametrize("kind,template,line,column", (
    ("ternary", 'close > open ? {arm} : "180"\n', 4, 23),
    ("if", 'if close > open\n    {arm}\nelse\n    "180"\n', 5, 5),
    ("switch", 'switch close > open\n    true => {arm}\n    => "180"\n', 5, 13),
))
def test_computed_string_arm_has_a_located_closed_set_refusal(
        expression, kind, template, line, column):
    # A bar counter is unbounded and the unknown name has no value proof.
    # Expansion locates the arm before the scalar-if refusal.
    pine = (HEADER + 'frame_name() => "75"\n'
            + 'tf_v = ' + template.format(arm=expression)
            + 'got_v = request.security(syminfo.tickerid, tf_v, close)\nplot(got_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E3111"
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.args == {"reason": "the timeframe set is not closed at compile time"}
    assert (diagnostic.location.line, diagnostic.location.col) == (line, column)


@pytest.mark.parametrize("setup,expression", (
    ("", "str.tostring(bar_index)"),
    ("", 'str.tostring(close > open ? 75.25 : 180.0)'),
    ("", 'str.tostring(close > open ? 75 : 180, "#.0")'),
    ("", """str.format("'{0}'", close > open ? 75 : 180)"""),
    ('minutes_v = close > open ? 240 : 1440\nminutes_v := bar_index\n',
     'str.tostring(minutes_v)'),
    ('frame_text() =>\n    var string saved_tf = "75"\n    saved_tf\n',
     'close > open ? frame_text() : "180"'),
    ('frame_text() =>\n    array.push(array.new_int(), 75)\n    "75"\n',
     'close > open ? frame_text() : "180"'),
))
def test_unbounded_stateful_and_unproved_formatting_values_stay_open(setup, expression):
    pine = (HEADER + setup + f'chosen_tf = {expression}\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\n'
            'plot(got_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert caught.value.diagnostics[0].code == "PF-E3111"
    assert caught.value.diagnostics[0].args == {
        "reason": "the timeframe set is not closed at compile time"}


def test_value_set_limit_refuses_instead_of_truncating_the_inventory():
    from pineforge_codegen.limits import MAX_SECURITY_TIMEFRAME_VALUES

    # One more distinct result than the configured capacity, all valid minute
    # strings. Expected failure is fixed; it is never derived by the interpreter.
    arms = "\n".join(
        f'    {index} => str.tostring({index + 1})'
        for index in range(MAX_SECURITY_TIMEFRAME_VALUES))
    pine = (HEADER + 'chosen_tf = switch bar_index\n' + arms
            + f'\n    => str.tostring({MAX_SECURITY_TIMEFRAME_VALUES + 1})\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\n'
            'plot(got_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert caught.value.diagnostics[0].code == "PF-E3111"
    assert caught.value.diagnostics[0].args == {
        "reason": "the timeframe set is not closed at compile time"}


@pytest.mark.parametrize("persistent", ("var", "varip"))
@pytest.mark.parametrize("selection", (
    'close > open ? "{fast_tf}" : "{slow_tf}"\n',
    'if close > open\n    "{fast_tf}"\nelse\n    "{slow_tf}"\n',
    'switch close > open\n    true => "{fast_tf}"\n    => "{slow_tf}"\n',
))
@pytest.mark.parametrize("helper,counter,fast_tf,slow_tf", (
    ("payload", "saved_v", "75", "180"),
    ("advance_count", "tally_v", "45", "2D"),
))
def test_persistent_requested_expression_has_independent_bar_counters(
        persistent, selection, helper, counter, fast_tf, slow_tf):
    # Counter semantics: each requested bar carries its own prior value,
    # adds one, and recomputations restore that prior value before adding.
    pine = (HEADER + f"{helper}() =>\n    {persistent} int {counter} = 0\n"
            f"    {counter} += 1\n    {counter}\n"
            + 'tf_v = ' + selection.format(fast_tf=fast_tf, slow_tf=slow_tf)
            + f'got_v = request.security(syminfo.tickerid, tf_v, {helper}())\nplot(got_v)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        f'{i}, "{tf}", input_tf_, false, false'
        for i, tf in enumerate((fast_tf, slow_tf))
    ]
    keys = []
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        (key,) = set(re.findall(
            rf'_security_helper_series_\["(_sec{sec_id}_{helper}_\d+_{counter})"\]',
            body))
        keys.append(key)
        assert_counter_protocol(body, sec_id, key)
        ref = f'_security_helper_series_["{key}"]'
        assert f"_req_sec_{sec_id} = {ref}[0];" in body
        assert ref not in chart_body(cpp)
        assert not re.search(rf"\b{helper}(?:_cs\d+)?\(", body)
        assert f"case {sec_id}: _eval_security_{sec_id}(bar, is_complete); break;" in cpp
    assert keys[0] != keys[1]
    assert keys[0] not in requested_body(cpp, 1)
    assert keys[1] not in requested_body(cpp, 0)
    assert_selection_tail(
        cpp,
        f'if (_pf_selected_tf == "{fast_tf}") return _req_sec_0;',
        f'if (_pf_selected_tf == "{slow_tf}") return _req_sec_1;',
    )


def assert_counter_protocol(body, sec_id, key):
    ref = f'_security_helper_series_["{key}"]'
    seed = f'_security_helper_series_["{key}@var_seed"]'
    assert (
        f"        if ({ref}.size() == 0) {{\n"
        f"            {seed}.push(0);\n"
        f"            {ref}.push({seed}[0]);\n"
        f"        }} else if (security_series_slot_is_new({sec_id})) {{\n"
        f"            {ref}.push({ref}[0]);\n"
        f"        }} else {{\n"
        f"            {ref}.update({ref}.size() > 1 ? {ref}[1] : {seed}[0]);\n"
        f"        }}\n"
        f"        {ref}.update({ref}[0] + 1);"
    ) in body
    assert body.count(f"{ref}.update({ref}[0] + 1);") == 1


@pytest.mark.parametrize("persistent", ("var", "varip"))
@pytest.mark.parametrize("selection", (
    'close > open ? "75" : "180"\n',
    'if close > open\n    "75"\nelse\n    "180"\n',
    'switch close > open\n    true => "75"\n    => "180"\n',
))
@pytest.mark.parametrize("expression", ("saved_v", "payload()", "saved_v[1]"))
def test_global_persistent_requested_expression_uses_each_sites_replay(
        persistent, selection, expression):
    pine = (HEADER + f"{persistent} float saved_v = na\n"
            "payload() =>\n    local_v = saved_v\n    local_v\n"
            + 'tf_v = ' + selection
            + f'got_v = request.security(syminfo.tickerid, tf_v, {expression})\nplot(got_v)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        '0, "75", input_tf_, false, false',
        '1, "180", input_tf_, false, false',
    ]
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        state = f"_sec{sec_id}_saved_v"
        assert f"if (!{state}_initialized)" in body
        assert f"{state}_initialized = true;" in body
        assert f"_sec{1 - sec_id}_saved_v" not in body
        assert not re.search(r"\bsaved_v\b", body)
        assert not re.search(r"\bpayload(?:_cs\d+)?\(", body)
    assert "_sec0_saved_v" not in chart_body(cpp)
    assert "_sec1_saved_v" not in chart_body(cpp)


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_reassigned_alias_uses_the_replayed_global_counter(persistent):
    pine = (HEADER + f"{persistent} int saved_v = 0\n"
            "saved_v += 1\nalias_v = 0\nalias_v := saved_v\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, alias_v)\nplot(got_v)\n')
    cpp = transpile(pine)
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        assert_global_counter_protocol(body, sec_id, "saved_v")
        assert f"_sec{sec_id}_alias_v = 0;" in body
        assert f"_sec{sec_id}_alias_v = _sec{sec_id}_saved_v;" in body
        assert f"_req_sec_{sec_id} = _sec{sec_id}_alias_v;" in body
        assert body.index(f"_sec{sec_id}_saved_v += 1;") < body.index(
            f"_sec{sec_id}_alias_v = _sec{sec_id}_saved_v;")
        assert not re.search(r"\b(?:saved_v|alias_v)\b", body)


def assert_global_counter_protocol(body, sec_id, counter):
    state = f"_sec{sec_id}_{counter}"
    assert (
        f"        if (!{state}_initialized) {{\n"
        f"            {state} = 0;\n"
        f"            {state}_initialized = true;\n"
        f"        }}\n"
    ) in body
    assert body.count(f"{state} += 1;") == 1


@pytest.mark.parametrize("persistent", ("var", "varip"))
@pytest.mark.parametrize("counter,fast_tf,slow_tf", (
    ("count_v", "45", "2D"),
    ("tally_v", "90", "3D"),
))
@pytest.mark.parametrize("expression", ("{counter}", "read_count()", "{counter}[1]"))
def test_outside_counter_chain_is_independent_of_the_chart_and_other_arm(
        persistent, counter, fast_tf, slow_tf, expression):
    pine = (HEADER + f"{persistent} int {counter} = 0\n{counter} += 1\n"
            f"read_count() =>\n    local_v = {counter}\n    local_v\n"
            f'tf_v = close > open ? "{fast_tf}" : "{slow_tf}"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, '
            + expression.format(counter=counter) + ')\n'
            + f'chart_v = {counter}\nplot(got_v + chart_v)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        f'{i}, "{tf}", input_tf_, false, false'
        for i, tf in enumerate((fast_tf, slow_tf))
    ]
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        state = f"_sec{sec_id}_{counter}"
        assert f"if (!{state}_initialized)" in body
        assert f"{state}_initialized = true;" in body
        assert f"_sec{1 - sec_id}_{counter}" not in body
        assert not re.search(rf"\b{counter}\b", body)
        assert not re.search(r"\bread_count(?:_cs\d+)?\(", body)
        if expression == "{counter}[1]":
            assert f"{state}.push(0);" in body
            assert f"security_series_slot_is_new({sec_id})" in body
            assert f"{state}.push({state}[0]);" in body
            assert body.count(f"{state}.update({state}[0] + 1);") == 1
            assert f"_req_sec_{sec_id} = {state}[1];" in body
        else:
            assert_global_counter_protocol(body, sec_id, counter)
    chart = chart_body(cpp)
    assert not re.search(rf"\b_sec\d+_{counter}\b", chart)
    assert re.search(rf"\bchart_v\s*=\s*{counter}(?:\[0\])?;", chart)


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_conditionally_written_counter_replays_the_requested_bar_condition(persistent):
    pine = (HEADER + f"{persistent} int count_v = 0\n"
            "if close > open\n    count_v += 1\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, count_v)\n'
            'plot(got_v + count_v)\n')
    cpp = transpile(pine)
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        assert_global_counter_protocol(body, sec_id, "count_v")
        assert re.search(
            rf"if \([^\n]*bar\.close[^\n]*bar\.open[^\n]*\) \{{\n"
            rf"            _sec{sec_id}_count_v \+= 1;\n"
            r"        \}", body)
        assert "current_bar_" not in body
        assert not re.search(r"\bcount_v\b", body)


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_two_requests_get_four_global_counter_chains_and_keep_chart_storage(persistent):
    pine = (HEADER + f"{persistent} int count_v = 0\ncount_v += 1\n"
            'first_tf = close > open ? "45" : "2D"\n'
            'second_tf = close < open ? "75" : "180"\n'
            'first_v = request.security(syminfo.tickerid, first_tf, count_v)\n'
            'second_v = request.security(syminfo.tickerid, second_tf, count_v)\n'
            'chart_v = count_v\nplot(first_v + second_v + chart_v)\n')
    cpp = transpile(pine)
    assert registrations(cpp) == [
        '0, "45", input_tf_, false, false',
        '1, "75", input_tf_, false, false',
        '2, "2D", input_tf_, false, false',
        '3, "180", input_tf_, false, false',
    ]
    for sec_id in range(4):
        body = requested_body(cpp, sec_id)
        assert_global_counter_protocol(body, sec_id, "count_v")
        assert f"_req_sec_{sec_id} = _sec{sec_id}_count_v;" in body
        assert set(re.findall(r"\b_sec\d+_count_v\b", body)) == {
            f"_sec{sec_id}_count_v"}
        assert not re.search(r"\bcount_v\b", body)
    chart = chart_body(cpp)
    assert "chart_v = count_v;" in chart
    assert not re.search(r"\b_sec\d+_count_v\b", chart)
    assert_selection_tail(
        cpp, 'if (_pf_selected_tf == "45") return _req_sec_0;',
        'if (_pf_selected_tf == "2D") return _req_sec_2;')
    assert_selection_tail(
        cpp, 'if (_pf_selected_tf == "75") return _req_sec_1;',
        'if (_pf_selected_tf == "180") return _req_sec_3;')


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_global_replay_and_helper_owned_counter_both_belong_to_the_site(persistent):
    pine = (HEADER + f"{persistent} int count_v = 0\ncount_v += 1\n"
            f"payload() =>\n    {persistent} int tally_v = 0\n"
            "    tally_v += 1\n    count_v + tally_v\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, payload())\nplot(got_v)\n')
    cpp = transpile(pine)
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        assert_global_counter_protocol(body, sec_id, "count_v")
        (key,) = set(re.findall(
            rf'_security_helper_series_\["(_sec{sec_id}_payload_\d+_tally_v)"\]', body))
        assert_counter_protocol(body, sec_id, key)
        assert not re.search(r"\b(?:count_v|tally_v)\b", body)


def test_outside_counter_value_probe_has_two_replayed_chains():
    cpp = transpile(source("outside-counter-selected"))
    assert registrations(cpp) == [
        '0, "240", input_tf_, false, false',
        '1, "1D", input_tf_, false, false',
    ]
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        assert_global_counter_protocol(body, sec_id, "runs_v")
        assert f"_req_sec_{sec_id} = _sec{sec_id}_runs_v;" in body
        assert not re.search(r"\bruns_v\b", body)
    assert not re.search(r"\b_sec\d+_runs_v\b", chart_body(cpp))


@pytest.mark.parametrize("persistent", ("var", "varip"))
@pytest.mark.parametrize("mutation,code", (
    ("for index_v = 0 to 1\n    count_v += 1\n", "PF-E3041"),
    ("switch\n    close > open => count_v += 1\n    => count_v += 2\n", "PF-E3040"),
))
def test_unreplayable_global_writes_keep_the_existing_series_refusal(
        persistent, mutation, code):
    prefix = (HEADER + f"{persistent} int count_v = 0\n" + mutation
              + 'tf_v = close > open ? "45" : "2D"\n')
    suffix = "count_v)\nplot(got_v)\n"
    fixed = prefix + 'got_v = request.security(syminfo.tickerid, "45", ' + suffix
    with pytest.raises(CompileError) as caught:
        transpile(fixed)
    assert caught.value.diagnostics[0].code == code
    expanded = prefix + 'got_v = request.security(syminfo.tickerid, tf_v, ' + suffix
    with pytest.raises(CompileError) as caught:
        transpile(expanded)
    assert caught.value.diagnostics[0].code == "PF-E3111"
    assert caught.value.diagnostics[0].args == {
        "reason": "persistent requested expression state is not yet supported"}
    assert (caught.value.diagnostics[0].location.line,
            caught.value.diagnostics[0].location.col) == (
                prefix.count("\n"), 8)


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_state_in_the_requesting_helper_stays_outside_the_payload(persistent):
    pine = (HEADER + f"read_value() =>\n    {persistent} int saved_v = 0\n"
            "    saved_v += 1\n"
            '    tf_v = close > open ? "45" : "2D"\n'
            '    request.security(syminfo.tickerid, tf_v, saved_v)\n'
            'plot(read_value())\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert caught.value.diagnostics[0].code == "PF-E3111"
    assert caught.value.diagnostics[0].args == {
        "reason": "persistent requested expression state is not yet supported"}


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_requesting_helpers_state_cannot_borrow_a_same_named_globals_replay(persistent):
    pine = (HEADER + "var int saved_v = 100\n"
            f"read_value() =>\n    {persistent} int saved_v = 0\n"
            "    saved_v += 1\n"
            '    tf_v = close > open ? "45" : "2D"\n'
            '    request.security(syminfo.tickerid, tf_v, saved_v)\n'
            'plot(read_value())\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert caught.value.diagnostics[0].code == "PF-E3111"
    assert caught.value.diagnostics[0].args == {
        "reason": "persistent requested expression state is not yet supported"}


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_method_payload_cannot_hide_a_global_persistent_read(persistent):
    pine = (HEADER + f"{persistent} float saved_v = 0.0\n"
            "method read_saved(float receiver_v) =>\n    receiver_v + saved_v\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, close.read_saved())\n'
            'plot(got_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert caught.value.diagnostics[0].code == "PF-E3111"
    assert caught.value.diagnostics[0].args == {
        "reason": "persistent requested expression state is not yet supported"}


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_global_counter_read_through_a_chart_fallback_keeps_its_refusal(persistent):
    prefix = (HEADER + f"{persistent} int count_v = 0\ncount_v += 1\n"
              "read_count() =>\n    local_v = count_v\n    local_v\n"
              'tf_v = close > open ? "45" : "2D"\n')
    suffix = "nz(read_count()))\nplot(got_v)\n"
    fixed = transpile_full(
        prefix + 'got_v = request.security(syminfo.tickerid, "45", ' + suffix)
    assert any("read_count" in diagnostic.message and "chart" in diagnostic.message
               for diagnostic in fixed["diagnostics"])
    with pytest.raises(CompileError) as caught:
        transpile(prefix + 'got_v = request.security(syminfo.tickerid, tf_v, ' + suffix)
    assert caught.value.diagnostics[0].code == "PF-E3111"
    assert caught.value.diagnostics[0].args == {
        "reason": "persistent requested expression state is not yet supported"}


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_requested_selection_owns_its_persistent_declaration(persistent):
    pine = (HEADER + "chosen_v = if true\n"
            f"    {persistent} int saved_v = 0\n"
            "    saved_v := saved_v + 1\n    saved_v\nelse\n    0\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, chosen_v)\nplot(got_v)\n')
    cpp = transpile(pine)
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        (key,) = set(re.findall(
            rf'_security_helper_series_\["(_sec{sec_id}_selection_\d+_saved_v)"\]', body))
        ref = f'_security_helper_series_["{key}"]'
        seed = f'_security_helper_series_["{key}@var_seed"]'
        assert f"{seed}.push(0);" in body
        assert f"security_series_slot_is_new({sec_id})" in body
        assert f"{ref}.push({ref}[0]);" in body
        assert f"{ref}.update({ref}.size() > 1 ? {ref}[1] : {seed}[0]);" in body
        assert ref not in requested_body(cpp, 1 - sec_id)


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_nested_helper_counter_shadows_unused_global_state(persistent):
    pine = (HEADER + "var int saved_v = 100\n"
            f"payload() =>\n    {persistent} int saved_v = 0\n"
            "    saved_v += 1\n    saved_v\n"
            "forward_count() =>\n    payload()\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, forward_count())\nplot(got_v)\n')
    cpp = transpile(pine)
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        (key,) = set(re.findall(
            rf'_security_helper_series_\["(_sec{sec_id}_payload_\d+_saved_v)"\]', body))
        assert_counter_protocol(body, sec_id, key)
        assert "forward_count(" not in body and "payload(" not in body


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_each_helper_call_in_one_payload_keeps_its_own_counter(persistent):
    pine = (HEADER + f"payload() =>\n    {persistent} int saved_v = 0\n"
            "    saved_v += 1\n    saved_v\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, payload() + payload())\n'
            'plot(got_v)\n')
    cpp = transpile(pine)
    all_keys = set()
    for sec_id in (0, 1):
        body = requested_body(cpp, sec_id)
        keys = set(re.findall(
            rf'_security_helper_series_\["(_sec{sec_id}_payload_\d+_saved_v)"\]', body))
        assert len(keys) == 2
        for key in keys:
            assert_counter_protocol(body, sec_id, key)
        assert all_keys.isdisjoint(keys)
        all_keys.update(keys)
    assert len(all_keys) == 4


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_stateful_payload_cannot_fall_back_to_a_chart_helper_call(persistent):
    pine = (HEADER + f"payload() =>\n    {persistent} int saved_v = 0\n"
            "    saved_v += 1\n    saved_v\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, nz(payload()))\nplot(got_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert caught.value.diagnostics[0].code == "PF-E3111"
    assert caught.value.diagnostics[0].args == {
        "reason": "persistent requested expression state is not yet supported"}


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_conditional_helper_persistence_keeps_its_rollback_refusal(persistent):
    pine = (HEADER + "payload() =>\n    result_v = 0\n    if close > open\n"
            f"        {persistent} int saved_v = 0\n"
            "        saved_v += 1\n        result_v := saved_v\n    result_v\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, payload())\nplot(got_v)\n')
    with pytest.raises(CompileError, match="conditional control flow"):
        transpile(pine)


def test_fixed_request_of_same_varip_helper_keeps_its_refusal():
    pine = (HEADER + "payload() =>\n    varip int saved_v = 0\n"
            "    saved_v += 1\n    saved_v\n"
            'tf_v = close > open ? "45" : "2D"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, payload())\n'
            'fixed_v = request.security(syminfo.tickerid, "30", payload())\n'
            'plot(got_v + fixed_v)\n')
    with pytest.raises(CompileError, match="helper-local varip state"):
        transpile(pine)


@pytest.mark.parametrize("selection", (
    'close > open ? "{first_tf}" : "180"',
    'if close > open\n    "{first_tf}"\nelse\n    "180"',
    'switch close > open\n    true => "{first_tf}"\n    => "180"',
))
@pytest.mark.parametrize("first_tf", ("75", "banana", ""))
def test_other_symbol_with_series_timeframe_preserves_missing_data_lowering(
        selection, first_tf, monkeypatch):
    # The series condition prevents feed registration; lower_no_data_requests
    # replaces the request before analysis, so even invalid arms go unvalidated.
    # Preservation pin against the original missing-data warning and stop on read.
    pine = (HEADER + "tf_v = " + selection.format(first_tf=first_tf) + "\n"
            'got_v = request.security("NASDAQ:AAPL", tf_v, close)\n'
            'if got_v > close\n    strategy.entry("Long", strategy.long)\n')
    actual = transpile_full(pine)
    with monkeypatch.context() as previous:
        previous.setattr(CodeGen, "_expand_security_series_timeframes",
                         lambda self: None, raising=False)
        expected = transpile_full(pine)
    assert actual["cpp"] == expected["cpp"]
    assert actual["diagnostics"] == expected["diagnostics"]
    assert [d.message for d in actual["diagnostics"]] == [
        d.message for d in expected["diagnostics"]]
    (warning,) = [d for d in actual["diagnostics"] if "no data is pinned" in d.message]
    assert warning.level == Level.WARNING
    assert warning.message.endswith(
        ": no data is pinned for this request; the run stops with an error "
        "where its value is read.")
    assert "registration computes its timeframe before the first bar" in warning.hint
    assert "strategy.entry" in warning.hint
    assert "register_security_eval" not in actual["cpp"]
    assert "_pf_symbol" not in actual["cpp"]
    assert "_PF_OTHER_SYMBOL_STOP(" in actual["cpp"]


@pytest.mark.parametrize("name", ("sma-parity", "history-parity"))
def test_requested_history_and_ta_have_separate_storage_per_arm(name):
    # The same requested expression is emitted twice, with sec_id in every
    # requested history or TA member name.
    cpp = transpile(source(name))
    for sec_id in (0, 1):
        if name == "sma-parity":
            assert re.search(rf"_sec{sec_id}__ta_sma_\d+\.compute\(bar\.close\)", cpp)
        else:
            assert f"_sec{sec_id}_hist_close" in cpp
            assert f"_sec{sec_id}_hist_close.push(" in cpp
    assert "_pf_selected_tf" in chart_body(cpp)


@pytest.mark.parametrize("payload,slots", (
    ("[close, open]", 2),
    ("ta.macd(close, 3, 6, 2)", 3),
))
def test_selected_tuple_values_retain_the_existing_storage_shape(payload, slots):
    # Literal tuple payloads own element members; TA tuple payloads own one
    # result struct per site. Selection returns exactly that existing value.
    names = ", ".join(f"part_{i}" for i in range(slots))
    pine = (HEADER + 'tf_v = close > open ? "75" : "180"\n'
            f'[{names}] = request.security(syminfo.tickerid, tf_v, {payload})\n'
            'plot(part_0)\n')
    cpp = transpile(pine)
    assert len(registrations(cpp)) == 2
    if slots == 2:
        assert_selection_tail(
            cpp,
            'if (_pf_selected_tf == "75") return std::make_tuple(_req_sec_0_0, _req_sec_0_1);',
            'if (_pf_selected_tf == "180") return std::make_tuple(_req_sec_1_0, _req_sec_1_1);',
        )
    else:
        assert_selection_tail(
            cpp,
            'if (_pf_selected_tf == "75") return _req_sec_0;',
            'if (_pf_selected_tf == "180") return _req_sec_1;',
        )
        assert "_req_sec_0_0" not in cpp
        assert "_req_sec_1_0" not in cpp


@pytest.mark.parametrize("name", ("sma-parity", "history-parity"))
def test_fixture_self_consistency_state_values_follow_the_selected_fixed_arm(name):
    """Fixture self-consistency: exported selected values equal the fixed arm."""
    # Data preservation pin: even signal indices choose 60, odd ones 240;
    # every selected exported quantity equals that arm's fixed quantity.
    rows = [row for row in export_rows("tv_state_entries.tsv") if row["source"] == name]
    assert rows
    for row in rows:
        arm = "60" if int(row["signal_bar_index"]) % 2 == 0 else "240"
        assert row["arm"] == arm
        assert row["selected"] == row[f"fixed_{arm}"]


def test_fixture_self_consistency_price_forms_have_the_same_entry_quantities():
    """Fixture self-consistency: the two exported price forms agree."""
    # Data preservation pin: the if and ternary exports use the same chart
    # condition and agree on every recorded entry quantity.
    rows = {row["source"]: row for row in export_rows("tv_bar_quantities.tsv")}
    assert rows["if-price"]["entry_quantities"] == rows["ternary-price"]["entry_quantities"]
    assert rows["if-price"]["sha256"] == rows["ternary-price"]["sha256"]


def test_fixture_self_consistency_hour_forms_have_the_same_entry_quantities():
    """Fixture self-consistency: exported hour selections agree by fill time."""
    # Data preservation pin: the if and ternary price conditions select the
    # same hour values at matching fill times; fixed controls remain separate.
    rows = export_rows("tv_hour_entries.tsv")
    values = {}
    for row in rows:
        values.setdefault(row["source"], []).append(
            (row["entry_time_utc8"], row["quantity"]))
    assert values["hour-if-price"] == values["hour-ternary-price"]
    assert values["hour-fixed-fast"] != values["hour-fixed-slow"]


@pytest.mark.parametrize("case", SCALAR_CASES, ids=lambda case: case.name)
def test_authored_scalar_inventory_rows_without_the_engine(case):
    from tests._timeframe_value_cases import inventory, values

    evaluator, expression = inventory(case.source())
    assert values(evaluator, expression) == case.expected
    if case.request:
        assert registrations(transpile(case.source())) == [
            f'{index}, "{value}", input_tf_, false, false'
            for index, value in enumerate(case.expected)
        ]


@pytest.mark.parametrize("case_name", ("andor", "less", "boolcast"))
def test_literal_only_conditions_register_only_the_taken_values(case_name):
    from tests._timeframe_value_cases import BY_NAME

    # Dead 1-minute and zero arms used to stop these otherwise valid sources.
    cpp = transpile(BY_NAME[case_name].source())
    assert registrations(cpp) == [
        '0, "240", input_tf_, false, false',
        '1, "1440", input_tf_, false, false',
    ]


@pytest.mark.parametrize("mutation", ("disabled", "wrong_way"))
@pytest.mark.parametrize("fast,slow,disabled_values", (
    pytest.param(
        "str.tostring((true and false) ? 480 : 240)",
        "str.tostring((false or true) ? 1440 : 480)",
        ("480", "240", "1440"), id="andor"),
    pytest.param(
        "str.tostring(240 < 1440 ? 240 : 480)",
        "str.tostring(240 > 1440 ? 480 : 1440)",
        ("240", "480", "1440"), id="compare"),
    pytest.param(
        "str.tostring(bool(2) ? 240 : 480)",
        "str.tostring(bool(2) ? 1440 : 480)",
        ("240", "480", "1440"), id="boolcast"),
))
def test_registration_comparison_detects_a_condition_pruning_mutation(
        fast, slow, disabled_values, mutation, monkeypatch):
    # Every dead arm is a valid timeframe, so a bad prune reaches the exact
    # registration assertion rather than failing literal validation first.
    pine = (HEADER
            + f'chosen_tf = bar_index % 2 == 0 ? {fast} : {slow}\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\nplot(got_v)\n')

    def assert_registered_values(actual):
        assert actual == [
            '0, "240", input_tf_, false, false',
            '1, "1440", input_tf_, false, false',
        ], "registered timeframes differ from authored values"

    assert_registered_values(registrations(transpile(pine)))
    original = TimeframeValues._constant_bool

    def mutant(self, node):
        chosen = original(self, node)
        if mutation == "disabled":
            return None
        return None if chosen is None else not chosen

    monkeypatch.setattr(TimeframeValues, "_constant_bool", mutant)
    actual = registrations(transpile(pine))
    if mutation == "disabled":
        assert actual == [
            f'{index}, "{value}", input_tf_, false, false'
            for index, value in enumerate(disabled_values)
        ]
    else:
        assert actual == ['0, "480", input_tf_, false, false']
    with pytest.raises(
            AssertionError, match="registered timeframes differ from authored values"):
        assert_registered_values(actual)


@pytest.mark.parametrize("setup,condition", (
    ("", "close > open"),
    ("", "close[1] > open[1]"),
    ("", "true or close > open"),
    ('pick_v = input.bool(true)\n', "pick_v"),
    ('pick_v = true\n', "pick_v"),
    ('pick_frame() => true\n', "pick_frame()"),
))
def test_conditions_with_names_history_inputs_or_helper_calls_keep_both_arms(
        setup, condition, monkeypatch):
    pine = (HEADER + setup
            + f'chosen_tf = bar_index % 2 == 0 ? str.tostring({condition} ? 240 : 480) : "1440"\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\nplot(got_v)\n')

    def assert_registered_values(actual):
        assert actual == [
            '0, "240", input_tf_, false, false',
            '1, "480", input_tf_, false, false',
            '2, "1440", input_tf_, false, false',
        ], "registered timeframes differ from authored values"

    assert_registered_values(registrations(transpile(pine)))
    original = TimeframeValues._constant_bool

    def mutant(self, node):
        chosen = original(self, node)
        # Preserve the outer chart-parity split; only the inner condition
        # under test is incorrectly treated as a proven true condition.
        if isinstance(node, BinOp) and node.op == "==":
            return chosen
        return True if chosen is None else chosen

    monkeypatch.setattr(TimeframeValues, "_constant_bool", mutant)
    actual = registrations(transpile(pine))
    assert actual == [
        '0, "240", input_tf_, false, false',
        '1, "1440", input_tf_, false, false',
    ]
    with pytest.raises(
            AssertionError, match="registered timeframes differ from authored values"):
        assert_registered_values(actual)


@pytest.mark.parametrize("selection", (
    'if true and false\n        str.tostring(1)\n    else\n        str.tostring(240)\n',
    'switch\n        true and false => str.tostring(1)\n'
    '        true => str.tostring(240)\n        => str.tostring(0)\n',
    'switch true\n        false => str.tostring(1)\n'
    '        true => str.tostring(240)\n        => str.tostring(0)\n',
))
def test_literal_only_boolean_block_selections_inside_helpers_prune_dead_arms(selection):
    pine = (HEADER + 'frame_text() =>\n    ' + selection
            + 'chosen_tf = bar_index % 2 == 0 ? frame_text() : "1440"\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\nplot(got_v)\n')
    assert registrations(transpile(pine)) == [
        '0, "240", input_tf_, false, false',
        '1, "1440", input_tf_, false, false',
    ]


@pytest.mark.parametrize("arm,code,args,hint,column", (
    ('str.format("{0}", 1440)', "PF-E1091",
     {"timeframe": "1,440", "digits": "1,440"}, "non-integer timeframe magnitude '1,440'", 48),
    ("str.tostring(0)", "PF-E1092",
     {"timeframe": "0"}, "timeframe magnitude must be positive", 50),
))
def test_unreached_invalid_computed_arm_is_refused_during_translation(
        arm, code, args, hint, column):
    # bar_index is never negative in a run; this is still not a literal-only
    # condition. The early inventory validates the unselected arm.
    pine = (HEADER + f'chosen_tf = bar_index >= 0 ? "240" : {arm}\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\nplot(got_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == code
    assert diagnostic.args == args
    assert diagnostic.hint == hint
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    # Computed literal candidates retain the original call's '(' location.
    assert (diagnostic.location.line, diagnostic.location.col) == (3, column)
    assert f"invalid timeframe literal '{args['timeframe']}'" in diagnostic.message


@pytest.mark.parametrize("count", (65, 128))
def test_large_closed_inventory_registers_all_values_before_the_first_bar(count):
    from tests._timeframe_value_cases import quota_source

    # An authored inventory size, not the implementation's configured cap.
    cpp = transpile(quota_source(count))
    assert registrations(cpp) == [
        f'{index}, "{index + 1}D", input_tf_, false, false'
        for index in range(count)
    ]


def test_pure_but_unevaluated_string_call_has_a_located_open_set_refusal():
    from pineforge_codegen.external_requests import _Taint

    assert _Taint._is_pure("str", "upper")
    pine = (HEADER + 'chosen_tf = close > open ? str.upper("240") : "1440"\n'
            'got_v = request.security(syminfo.tickerid, chosen_tf, close)\nplot(got_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E3111"
    assert diagnostic.args == {"reason": "the timeframe set is not closed at compile time"}
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    assert (diagnostic.location.line, diagnostic.location.col) == (3, 28)


@pytest.mark.parametrize("name", (
    "if-price", "ternary-price", "if-parity", "hour-if-price", "hour-if-parity",
    "hour-ternary-price", "sma-parity", "history-parity",
))
def test_chart_dependent_fixture_cpp_is_unchanged_by_constant_condition_pruning(
        name, monkeypatch):
    from pineforge_codegen.codegen.timeframe_values import TimeframeValues

    actual = transpile(source(name))
    monkeypatch.setattr(TimeframeValues, "_constant_bool", lambda self, node: None)
    assert actual == transpile(source(name))


def test_series_timeframe_catalog_lists_only_the_two_remaining_refusal_reasons():
    from pineforge_codegen import diagnostics_catalog

    reason = diagnostics_catalog()["codes"]["PF-E3111"]["args"]["reason"]
    assert reason == {"kind": "vocab", "values": [
        "the timeframe set is not closed at compile time",
        "persistent requested expression state is not yet supported",
    ]}
