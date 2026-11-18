"""Host currency requests retain branch laziness and coded host failures."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.codegen.run_stops import RUN_STOP_SHIMS_CPP
from pineforge_codegen.errors import CompileError
from tests import _compile as compile_env


_PRELUDE = "//@version=6\nstrategy('Currency branch')\n"
_CALL = "request.currency_rate('EUR', 'USD')"
_MESSAGE = "currency_conversion_rate_unavailable"
_NO_DATA_CALL = (
    'request.financial(syminfo.tickerid, close > open ? "FQ_X" : "FQ_Y", "FQ")'
)
_REPORTER = _PRELUDE + f"rate = true ? 1.0 : {_CALL}\nplot(rate)\n"
_NEVER_TRADE = _PRELUDE + (
    "rate = 1.0\n"
    f"if false\n    strategy.entry('L', strategy.long, qty = 1 * {_CALL})\n"
    "plot(rate)\n"
)
_EVALUATED = _PRELUDE + f"x = {_CALL}\n"
_SIDE_CALL = (
    "request.currency_rate(bump(1, 'GBP'), bump(2, 'JPY'), "
    "ignore_invalid_currency = ignoreCurrency())"
)
_SIDE_EFFECTS = _PRELUDE + (
    "var calls = array.new_int(1, 0)\n"
    "bump(int digit, string code) =>\n"
    "    array.set(calls, 0, array.get(calls, 0) * 10 + digit)\n"
    "    code\n"
    "ignoreCurrency() =>\n"
    "    array.set(calls, 0, array.get(calls, 0) * 10 + 3)\n"
    "    false\n"
    f"rate = true ? 1.0 : {_SIDE_CALL}\n"
    "seen = array.get(calls, 0)\n"
    "plot(rate)\n"
)


def _translated(source: str) -> str:
    result = transpile_full(source)
    assert not {
        diagnostic.code for diagnostic in result["diagnostics"]
    } & {"PF-E1025", "PF-W1025"}
    return result["cpp"]


def _assignment(cpp: str, name: str) -> str:
    statements = [
        line.strip() for line in cpp.splitlines()
        if re.match(rf"\s*{re.escape(name)}\s*=", line)
    ]
    assert len(statements) == 1, statements
    return statements[0]


def _request_host_call(cpp: str) -> str:
    # The host's coded exception already carries the conversion arguments.
    # A catch here must not replace it with the sibling no-data request stop.
    call = (
        "return this->request_currency_rate("
        "_pf_currency_from, _pf_currency_to, _pf_currency_account, "
        "_pf_currency_time, _pf_currency_ignore);"
    )
    body = cpp.split("void on_source_bar(", 1)[1]
    assert body.count(call) == 1
    statement, = [line for line in body.splitlines() if call in line]
    assert "_PF_NO_DATA_STOP(" not in statement
    assert "try {" not in statement
    assert "catch (" not in statement
    return call


def test_reporter_ternary_keeps_host_lookup_in_untaken_arm():
    cpp = _translated(_REPORTER)
    literal = transpile(_REPORTER.replace(_CALL, "1.0"))
    before, untaken = _assignment(cpp, "rate").split(" : ", 1)
    literal_before, _ = _assignment(literal, "rate").split(" : ", 1)
    call = _request_host_call(cpp)
    assert before == literal_before
    assert call not in before and call in untaken
    assert "this->request_currency_rate(" not in before
    assert "this->request_currency_rate(" in untaken
    assert cpp.count(call) == 1
    assert cpp.index(call) > cpp.index("void on_source_bar(")


def test_never_taken_trade_branch_keeps_host_lookup_in_branch():
    cpp = _translated(_NEVER_TRADE)
    literal = transpile(_NEVER_TRADE.replace(_CALL, "1.0"))
    marker = next(line for line in literal.splitlines() if "if (false) {" in line)
    before, branch = cpp.split(marker, 1)
    branch, after = branch.split("\n" + marker.split("if", 1)[0] + "}", 1)
    call = _request_host_call(cpp)
    assert call in branch and "strategy_entry(" in branch
    assert call not in before + after
    assert "this->request_currency_rate(" in branch
    assert "this->request_currency_rate(" not in before + after


@pytest.mark.parametrize("call", [
    _CALL,
    "request.currency_rate('GBP', 'JPY', true)",
    "request.currency_rate('GBP', 'JPY', ignore_invalid_currency = false)",
])
def test_evaluated_call_lowers_at_its_assignment(call):
    source = _EVALUATED.replace(_CALL, call)
    cpp = _translated(source)
    literal = transpile(source.replace(call, "1.0"))
    statement = _assignment(cpp, "x")
    assert statement.split(" = ", 1)[0] == _assignment(literal, "x").split(" = ", 1)[0]
    host_call = _request_host_call(cpp)
    assert host_call in statement
    assert cpp.count(host_call) == 1
    assert cpp.index(host_call) > cpp.index("void on_source_bar(")


def test_switch_arm_no_longer_emits_the_legacy_warning():
    source = _PRELUDE + f"rate = switch\n    true => {_CALL}\n    => 1.0\nplot(rate)\n"
    cpp = _translated(source)
    literal = transpile(source.replace(_CALL, "1.0"))
    assert "void on_source_bar(" in literal
    assert _request_host_call(cpp) in cpp.split("void on_source_bar(", 1)[1]


def test_argument_effects_stay_in_the_untaken_expression():
    cpp = _translated(_SIDE_EFFECTS)
    literal = transpile(_SIDE_EFFECTS.replace(_SIDE_CALL, "1.0"))
    before, untaken = _assignment(cpp, "rate").split(" : ", 1)
    assert before == _assignment(literal, "rate").split(" : ", 1)[0]
    call = _request_host_call(cpp)
    assert call in untaken
    assert "bump" in untaken and "ignoreCurrency" in untaken
    assert "bump" not in before and "ignoreCurrency" not in before
    assert untaken.count("const std::string _pf_currency_from =") == 1
    assert untaken.count("const std::string _pf_currency_to =") == 1
    assert untaken.count("const bool _pf_currency_ignore =") == 1
    assert (untaken.index('"GBP"') < untaken.index('"JPY"')
            < untaken.index("ignoreCurrency")
            < untaken.index("_pf_currency_account =")
            < untaken.index("_pf_currency_time =") < untaken.index(call))


@pytest.mark.parametrize("arguments", [
    "",
    "'EUR'",
    "'EUR', 'USD', false, true",
    "'EUR', 'USD', missing = true",
    "'EUR', 'USD', from = 'GBP'",
])
def test_malformed_calls_still_fail_signature_validation(arguments):
    # The host lookup does not relax ordinary Pine signature validation.
    _translated(_EVALUATED)
    source = _EVALUATED.replace(_CALL, f"request.currency_rate({arguments})")
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostics = caught.value.diagnostics
    assert diagnostics
    assert all(d.code not in {"PF-E1025", "PF-W1025"} for d in diagnostics)
    assert any("TradingView:" in d.message and "request.currency_rate" in d.message
               for d in diagnostics)
    assert all("nz or fixnan" not in d.user_message for d in diagnostics)


def test_skipped_call_arguments_are_still_analyzed():
    argument = 'input.string("EUR", "From currency")'
    source = _REPORTER.replace("'EUR'", argument)
    cpp = _translated(source)
    assert _request_host_call(cpp) in _assignment(cpp, "rate")
    # The same nested input under an ordinary string call has the same
    # analyzed descriptor, even though neither false arm executes.
    control = _REPORTER.replace(_CALL, f"str.length({argument})")
    inputs = transpile_full(source)["inputs"]
    assert inputs and inputs == transpile_full(control)["inputs"]


_TRADE_TAIL = """
if bar_index == 0
    strategy.entry('control', strategy.long, qty = rate)
if bar_index == 2
    strategy.close('control')
entryPrice = strategy.closedtrades.entry_price(0)
exitPrice = strategy.closedtrades.exit_price(0)
entryTime = strategy.closedtrades.entry_time(0)
exitTime = strategy.closedtrades.exit_time(0)
quantity = strategy.closedtrades.size(0)
profit = strategy.closedtrades.profit(0)
"""

_BARS = r"""
    Bar bars[] = {
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 60000},
        Bar{101.0, 101.0, 101.0, 101.0, 1.0, 120000},
        Bar{102.0, 102.0, 102.0, 102.0, 1.0, 180000},
        Bar{103.0, 103.0, 103.0, 103.0, 1.0, 240000},
        Bar{104.0, 104.0, 104.0, 104.0, 1.0, 300000},
    };
"""

_SUCCESS_DRIVER = r"""
#include <iomanip>
#include <iostream>
#include <type_traits>
int main() {
    static_assert(std::is_same_v<decltype(GeneratedStrategy::rate), double>);
""" + _BARS + r"""
    bool saw_trade = false;
    std::cout << std::setprecision(17);
    for (bool dynamic : {false, true}) {
        // Each prefix observes the value passed to plot on that final bar.
        for (int n = 1; n <= 5; ++n) {
            GeneratedStrategy strategy;
            if (dynamic) strategy.run(bars, n, "1", "1");
            else strategy.run(bars, n);
            if (!strategy.last_error().empty()) {
                std::cerr << strategy.last_error();
                return 2;
            }
            saw_trade = saw_trade || strategy.trade_count() > 0;
            std::cout << strategy.rate << ' ' << strategy.trade_count() << ' '
                      << strategy.entryPrice << ' ' << strategy.exitPrice << ' '
                      << strategy.entryTime << ' ' << strategy.exitTime << ' '
                      << strategy.quantity << ' ' << strategy.profit << '\n';
        }
    }
    return saw_trade ? 0 : 3;
}
"""

_STOP_DRIVER = r"""
#include <iostream>
int main() {
""" + _BARS + r"""
    GeneratedStrategy strategy;
    ReportC report{};
    run_backtest_full(&strategy, bars, 5, "", "", 0, 4, 0, &report);
    report_free(&report);
    std::cout << strategy.script_bars_processed() << '\n';
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
#ifdef PINEFORGE_HAS_RUN_FAILURE_CODES_V1
        std::cerr << '\n' << run_failure_code_of(strategy)
                  << '\n' << run_failure_args_of(strategy);
#endif
        return 2;
    }
    return 0;
}
"""


def _compile_and_run(source: str, driver: str, directory: Path,
                     label: str, *, legacy: bool = False,
                     extra_sources: tuple[Path, ...] = ()) -> subprocess.CompletedProcess:
    compile_env.skip_if_no_engine_lib()
    cpp = _translated(source)
    if legacy:
        assert cpp.count(RUN_STOP_SHIMS_CPP) == 1
        cpp = cpp.replace(
            RUN_STOP_SHIMS_CPP,
            "#undef PINEFORGE_HAS_RUN_FAILURE_CODES_V1\n" + RUN_STOP_SHIMS_CPP,
        )
    assert compile_env._COMPILER is not None and compile_env._ENGINE_LIB is not None
    path = directory / f"{label}.cpp"
    executable = directory / label
    path.write_text(cpp + driver)
    command = [
        compile_env._COMPILER, "-std=c++17", "-O0",
        *compile_env.STRATEGY_FP_FLAGS,
        *compile_env._include_flags(isolate_headers=False),
        str(path), *(str(extra) for extra in extra_sources),
        str(compile_env._ENGINE_LIB), "-pthread", "-o", str(executable),
    ]
    built = subprocess.run(command, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr or built.stdout
    return subprocess.run([str(executable)], capture_output=True, text=True)


@pytest.mark.parametrize("source, call", [
    (_REPORTER, _CALL),
    (_NEVER_TRADE, _CALL),
    (_NEVER_TRADE.replace("if false", "if close < 0"), _CALL),
    (_SIDE_EFFECTS, _SIDE_CALL),
], ids=["reporter", "false-trade-branch", "never-true-trade-branch", "argument-effects"])
def test_skipped_calls_run_like_literal_replacements(source, call, tmp_path):
    driver = _SUCCESS_DRIVER
    if source == _SIDE_EFFECTS:
        driver = driver.replace("<< strategy.rate", "<< strategy.seen << ' ' << strategy.rate")
    source += _TRADE_TAIL
    subject = _compile_and_run(source, driver, tmp_path, "subject")
    control = _compile_and_run(source.replace(call, "1.0"), driver, tmp_path, "control")
    assert subject.returncode == control.returncode == 0, subject.stderr + control.stderr
    assert subject.stderr == control.stderr == ""
    assert subject.stdout == control.stdout
    assert len(subject.stdout.splitlines()) == 10


@pytest.mark.parametrize("body", [
    f"x = {_CALL}\n",
    f"if true\n    x = {_CALL}\n",
    f"if bar_index == 2\n    x = {_CALL}\n",
], ids=["top-level", "taken-branch", "later-bar"])
@pytest.mark.parametrize("legacy", [False, True], ids=["engine-shims", "legacy-shims"])
def test_evaluated_calls_stop_like_runtime_error(body, legacy, tmp_path):
    source = _PRELUDE + body
    reference = source.replace(f"x = {_CALL}", f'runtime.error("{_MESSAGE}")')
    no_data = source.replace(
        f"x = {_CALL}",
        f"strategy.entry('no data', strategy.long, qty = {_NO_DATA_CALL})",
    )
    subject = _compile_and_run(source, _STOP_DRIVER, tmp_path, "subject", legacy=legacy)
    control = _compile_and_run(reference, _STOP_DRIVER, tmp_path, "runtime_error",
                               legacy=legacy)
    sibling = _compile_and_run(no_data, _STOP_DRIVER, tmp_path, "no_data", legacy=legacy)
    literal = _compile_and_run(source.replace(_CALL, "1.0"),
                               _STOP_DRIVER, tmp_path, "literal", legacy=legacy)
    assert literal.returncode == 0 and literal.stderr == ""
    assert subject.returncode == control.returncode == sibling.returncode != 0
    assert subject.stdout == control.stdout == sibling.stdout
    subject_message, *subject_failure = subject.stderr.splitlines()
    control_message, *control_failure = control.stderr.splitlines()
    sibling_message, *sibling_failure = sibling.stderr.splitlines()
    # The engine now supplies currencies and evaluation time in the message;
    # runtime.error still supplies the progress/failure-transport control.
    assert "request.currency_rate" in subject_message
    assert _MESSAGE in control_message
    assert "EUR" in subject_message and "USD" in subject_message
    evaluation_time = 180000 if "bar_index == 2" in body else 60000
    assert str(evaluation_time) in subject_message
    assert "request.financial" in sibling_message
    assert len(subject_failure) == len(control_failure) == len(sibling_failure)
    if subject_failure:
        assert len(subject_failure) == 2
        # The conversion failure is distinct from both no-data requests and
        # runtime.error, even when runtime.error spells its code as text.
        assert subject_failure[0] == _MESSAGE
        assert sibling_failure[0] == "no_data_request"
        assert control_failure[0] == "strategy_runtime_error"
        args = json.loads(subject_failure[1])
        assert args == {
            "function": "currency_rate",
            "reason": "unsupported_pair",
            "from": "EUR",
            "to": "USD",
            "account_currency": "USD",
            "symbol_currency": "USD",
            "time_ms": evaluation_time,
        }
        assert json.loads(sibling_failure[1])["function"] == "request.financial"
