"""Requested copies retain missing values and their original replay boundary."""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import run_emitted_tu, skip_if_no_engine_lib
from tests._e2e import reference_codegen, transpile_json
from tests._numeric_expected import integer_to_double
from tests.test_security_helper_local_var import _eval_body
from tests.test_security_var_alias_history import HOUR_MS, REPORTER, chart_bars


MISSING_INTEGER_COPY = """//@version=6
strategy("requested scalar value copies")
var int cnt = na
float x = cnt
if close > open
    cnt := bar_index
htf = request.security(syminfo.tickerid, "240", x)
"""

LATE_LOOP_UPDATE = """//@version=6
strategy("requested scalar value copies")
var float acc = 0.0
accAlias = acc
acc += close - open
float level = close
getLevel() => level
htf = request.security(syminfo.tickerid, "240", accAlias + getLevel())
for i = 0 to 1
    level += 1
plot(htf)
"""

LATE_SOURCE_INPUT_UPDATE = """//@version=6
strategy("t")
var float acc = 0.0
float level = close
accAlias = acc
acc += level
htf = request.security(syminfo.tickerid, "240", accAlias)
for i = 0 to 1
    level += 1
plot(htf)
"""


LATE_COPIED_SOURCE_UPDATE = """//@version=6
strategy("t")
float acc = close
accAlias = acc
htf = request.security(syminfo.tickerid, "240", accAlias)
for i = 0 to 1
    acc += 1
plot(htf)
"""


def test_copy_capture_adds_only_its_sources_to_requests_that_read_it():
    source = LATE_LOOP_UPDATE.replace(
        "for i = 0 to 1",
        'other = request.security(syminfo.tickerid, "60", getLevel())\n'
        "for i = 0 to 1",
    )
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    context = Analyzer(program).analyze()
    assert len(context.security_calls) == 2
    assert context.security_calls[0].mutable_globals == ("acc", "accAlias")
    assert context.security_calls[1].mutable_globals == ()


def test_copy_capture_preserves_the_call_time_boundary_of_source_updates():
    program = Parser(
        Lexer(LATE_SOURCE_INPUT_UPDATE).tokenize(), source=LATE_SOURCE_INPUT_UPDATE,
    ).parse()
    context = Analyzer(program).analyze()
    assert len(context.security_calls) == 1
    assert context.security_calls[0].mutable_globals == ("acc", "accAlias")


@pytest.mark.parametrize("chain", [False, True], ids=["copy", "chain"])
def test_copy_capture_does_not_add_a_source_first_reassigned_after_the_call(chain):
    source = LATE_COPIED_SOURCE_UPDATE
    if chain:
        source = source.replace("accAlias = acc\n", "accAlias = acc\nnextAlias = accAlias\n")
        source = source.replace('"240", accAlias)', '"240", nextAlias)')
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    context = Analyzer(program, budget=None).analyze()
    # The last, unchanged link can expand to the retained earlier copy.
    assert context.security_calls[0].mutable_globals == ("accAlias",)
    body = _eval_body(transpile(source), 0, None)
    assert "_sec0_acc " not in body
    assert "_sec0_accAlias = acc;" in body


@pytest.mark.parametrize("assignment, integer_type", [
    ("bar_index", "int"),
    ("int(close)", "int64_t"),
])
def test_requested_float_copy_emits_defined_integer_na_and_float_conversion(
    assignment, integer_type,
):
    source = MISSING_INTEGER_COPY.replace("cnt := bar_index", f"cnt := {assignment}")
    cpp = transpile(source)
    body = _eval_body(cpp, 0, None)
    assert f"_sec0_cnt = na<{integer_type}>();" in body
    assert f"_sec0_x = {integer_to_double('_sec0_cnt')};" in body


def test_requested_state_updates_discover_the_requested_bar_index():
    cpp = transpile(MISSING_INTEGER_COPY)
    body = _eval_body(cpp, 0, None)
    assert "int _sec0_bar_index_ = -1;" in cpp
    assert "if (security_series_slot_is_new(0)) ++_sec0_bar_index_;" in body
    assert "_sec0_cnt = _sec0_bar_index_;" in body
    assert "pine_bar_index()" not in body


def test_requested_counter_discovery_keeps_later_chart_only_updates_outside():
    source = LATE_COPIED_SOURCE_UPDATE.replace("acc += 1", "acc += bar_index")
    cpp = transpile(source)
    assert "_sec0_bar_index_" not in cpp
    assert "_sec0_accAlias = acc;" in _eval_body(cpp, 0, None)


def test_requested_alias_history_emits_its_series_replay_and_read():
    cpp = transpile(REPORTER)
    assert "Series<double> _sec0_accAlias;" in cpp
    body = _eval_body(cpp, 0, None)
    replay = (
        "        if (security_series_slot_is_new(0)) {\n"
        "            _sec0_accAlias.push(_sec0_acc);\n"
        "        } else {\n"
        "            _sec0_accAlias.update(_sec0_acc);\n"
        "        }\n"
    )
    assert body.count(replay) == 1
    assert body.count("_req_sec_0 = (_sec0_accAlias[0] - _sec0_accAlias[1]);") == 1


def observe_requested(source, *, cpp=None, bars=None, opt="-O0"):
    bars = list(chart_bars()) if bars is None else bars
    literals = ",\n".join(
        "        Bar{" + ", ".join(repr(value) for value in bar) + "}"
        for bar in bars
    )
    driver = r"""
#include <iomanip>
#include <iostream>
struct ValueCopyObserver : GeneratedStrategy {
    void evaluate_security(int sec_id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(sec_id, bar, complete);
        if (sec_id == 0)
            std::cout << bar.timestamp << ' ' << complete << ' '
                      << _req_sec_0 << '\n';
    }
};
int main() {
    std::cout << std::setprecision(17);
    ValueCopyObserver strategy;
    Bar bars[] = {
BARS
    };
    strategy.run(bars, COUNT, "60", "60");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << '\n';
        return 1;
    }
    return 0;
}
""".replace("BARS", literals).replace("COUNT", str(len(bars)))
    output = run_emitted_tu(
        transpile(source) if cpp is None else cpp,
        driver, opt=opt, label="requested scalar value copy",
    )
    print(output, end="")
    rows = []
    for line in output.splitlines():
        parts = line.split()
        assert len(parts) == 3 and parts[1] in {"0", "1"}, line
        rows.append((int(parts[0]), parts[1] == "1", float(parts[2])))
    completed = [(timestamp, value) for timestamp, complete, value in rows if complete]
    assert [timestamp for timestamp, _ in completed] == [
        bar[-1] for bar in bars[::4]
    ]
    return bars, completed


def missing_integer_bars():
    # Begin with an unassigned requested candle, then alternate assignments
    # and held values so missingness and finite copy chronology are distinct.
    bars = []
    open_ = 100.0
    closes = (99.0, 98.0, 97.0, 96.0, 97.0, 98.0, 99.0, 100.0,
              99.0, 98.0, 97.0, 96.0, 97.0, 98.0, 99.0, 100.0)
    for index, close in enumerate(closes):
        bars.append((open_, max(open_, close), min(open_, close), close,
                     float(index + 1), index * HOUR_MS))
        open_ = close
    return bars


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_requested_float_copy_preserves_integer_na_before_later_assignment(opt):
    skip_if_no_engine_lib()
    bars = missing_integer_bars()
    _, completed = observe_requested(MISSING_INTEGER_COPY, bars=bars, opt=opt)
    previous = math.nan
    for index, (timestamp, observed) in enumerate(completed):
        # The copy precedes this candle's possible store to the integer.
        if math.isnan(previous):
            assert math.isnan(observed), (timestamp, observed)
        else:
            assert observed == previous, (timestamp, observed, previous)
        group = bars[index * 4:(index + 1) * 4]
        if group[-1][3] > group[0][0]:
            previous = float(index)


@pytest.mark.parametrize("source, source_reads_level", [
    pytest.param(LATE_LOOP_UPDATE, False, id="helper-reads-chart"),
    pytest.param(LATE_SOURCE_INPUT_UPDATE, True, id="source-update-reads-chart"),
])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_later_loop_update_keeps_its_existing_chart_value_boundary(
    tmp_path, source, source_reads_level, opt,
):
    skip_if_no_engine_lib()
    base = reference_codegen("ecbbfe392f993e8ce6d4cf12067f44b1b95dcf51")
    assert base is not None, "reference commit is not in this checkout's history"
    pine = tmp_path / "value-copy.pine"
    pine.write_text(source)
    result = transpile_json(pine, root=base)
    assert result["ok"], result.get("diagnostics")
    _, base_completed = observe_requested(
        source, cpp=result["cpp"], opt=opt,
    )
    bars, completed = observe_requested(source, opt=opt)
    accumulator = 0.0
    assert [timestamp for timestamp, _ in base_completed] == [
        timestamp for timestamp, _ in completed
    ]
    for index, ((timestamp, observed), (_, base_value)) in enumerate(
        zip(completed, base_completed)
    ):
        # The mutable source is replayed in the request. The separate helper
        # retains the chart value from the preceding chart evaluation, before
        # this requested close's on_source_bar runs.
        group = bars[index * 4:(index + 1) * 4]
        preceding_chart = bars[index * 4 + 2]
        chart_value = preceding_chart[3] + 2.0
        delta = chart_value if source_reads_level else group[-1][3] - group[0][0]
        chart_operand = 0.0 if source_reads_level else chart_value
        expected = accumulator + chart_operand
        assert timestamp == index * 4 * HOUR_MS
        assert observed == expected, (timestamp, observed, expected)
        # Base expanded the copy after its source's update. Preserve its
        # separate chart contribution while correcting the copy chronology.
        assert base_value == accumulator + delta + chart_operand
        accumulator += delta


def assert_requested_values(rows, expected):
    assert len(rows) == len(expected)
    for (timestamp, actual), wanted in zip(rows, expected):
        if math.isnan(wanted):
            assert math.isnan(actual), (timestamp, actual, wanted)
        else:
            assert actual == pytest.approx(wanted), (timestamp, actual, wanted)


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("chain", [False, True], ids=["copy", "chain"])
def test_late_copied_source_loop_keeps_the_original_boundary(tmp_path, chain, opt):
    skip_if_no_engine_lib()
    source = LATE_COPIED_SOURCE_UPDATE
    if chain:
        source = source.replace("accAlias = acc\n", "accAlias = acc\nnextAlias = accAlias\n")
        source = source.replace('"240", accAlias)', '"240", nextAlias)')
    base = reference_codegen("ecbbfe392f993e8ce6d4cf12067f44b1b95dcf51")
    assert base is not None, "reference commit is not in this checkout's history"
    pine = tmp_path / "late-source-copy.pine"
    pine.write_text(source)
    result = transpile_json(pine, root=base)
    assert result["ok"], result.get("diagnostics")
    bars, observed = observe_requested(source, opt=opt)
    _, previous = observe_requested(source, cpp=result["cpp"], bars=bars, opt=opt)
    control = source.replace('"240", nextAlias)', '"240", acc)').replace(
        '"240", accAlias)', '"240", acc)',
    )
    _, direct = observe_requested(control, bars=bars, opt=opt)
    expected = [bars[start + 2][3] + 2.0 for start in range(0, len(bars), 4)]
    assert_requested_values(observed, expected)
    assert_requested_values(previous, expected)
    assert_requested_values(direct, expected)
