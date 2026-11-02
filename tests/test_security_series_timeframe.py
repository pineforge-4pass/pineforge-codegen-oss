"""Closed series timeframe choices register independent requested contexts."""

import csv
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError, Level, Phase


FIXTURES = Path(__file__).parent / "fixtures" / "series_timeframe"
HEADER = '//@version=6\nstrategy("Selected timeframe", currency = currency.NONE)\n'


def source(name):
    return (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")


def registrations(cpp):
    return re.findall(r"^\s*register_security_eval\((.*)\);$", cpp, re.MULTILINE)


def chart_body(cpp):
    return cpp.split("void on_source_bar(", 1)[1]


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
    'str.tostring(bar_index)',
    'frame_name()',
    'unknown_frame',
))
def test_computed_string_arm_has_a_located_closed_set_refusal(expression):
    # The first arm starts at 4:23; its computed string leaves an open set,
    # even when an individual call could return a familiar timeframe.
    pine = (HEADER + 'frame_name() => "75"\n'
            f'tf_v = close > open ? {expression} : "180"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, close)\nplot(got_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E3111"
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.args == {"reason": "the timeframe set is not closed at compile time"}
    assert (diagnostic.location.line, diagnostic.location.col) == (4, 23)


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_persistent_requested_expression_keeps_a_located_refusal(persistent):
    # The requested helper owns persistent storage, unlike independent TA
    # or history buffers; this unmeasured state retains an explicit refusal.
    pine = (HEADER + f"payload() =>\n    {persistent} float saved_v = na\n"
            "    saved_v := nz(saved_v) + close\n    saved_v\n"
            'tf_v = close > open ? "75" : "180"\n'
            'got_v = request.security(syminfo.tickerid, tf_v, payload())\nplot(got_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E3111"
    assert diagnostic.args == {
        "reason": "persistent requested expression state is not yet supported"}
    assert (diagnostic.location.line, diagnostic.location.col) == (7, 8)


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
def test_exported_state_values_follow_the_signal_bars_selected_fixed_arm(name):
    # Data preservation pin: even signal indices choose 60, odd ones 240;
    # every selected exported quantity equals that arm's fixed quantity.
    rows = [row for row in export_rows("tv_state_entries.tsv") if row["source"] == name]
    assert rows
    for row in rows:
        arm = "60" if int(row["signal_bar_index"]) % 2 == 0 else "240"
        assert row["arm"] == arm
        assert row["selected"] == row[f"fixed_{arm}"]


def test_exported_price_forms_have_the_same_entry_quantities():
    # Data preservation pin: the if and ternary exports use the same chart
    # condition and agree on every recorded entry quantity.
    rows = {row["source"]: row for row in export_rows("tv_bar_quantities.tsv")}
    assert rows["if-price"]["entry_quantities"] == rows["ternary-price"]["entry_quantities"]
    assert rows["if-price"]["sha256"] == rows["ternary-price"]["sha256"]


def test_exported_hour_forms_have_the_same_entry_quantities():
    # Data preservation pin: the if and ternary price conditions select the
    # same hour values at matching fill times; fixed controls remain separate.
    rows = export_rows("tv_hour_entries.tsv")
    values = {}
    for row in rows:
        values.setdefault(row["source"], []).append(
            (row["entry_time_utc8"], row["quantity"]))
    assert values["hour-if-price"] == values["hour-ternary-price"]
    assert values["hour-fixed-fast"] != values["hour-fixed-slow"]
