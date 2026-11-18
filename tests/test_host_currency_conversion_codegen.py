"""Issue #376: lower currency queries to the real host, with no FX feed.

Native cases use only synthetic numeric host inputs and the existing native
input checks. Run with PINEFORGE_REQUIRE_NATIVE=1 against SM's matching real
headers/archive: missing APIs are a build boundary, never the RED oracle.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.host_members import HOST_MEMBER_NAMES
from pineforge_codegen.errors import CompileError, Level, Phase
from tests import _compile as compile_env
from tests.test_request_currency_rate import _assignment, _compile_and_run


# Original issue bytes, including whitespace. B and NONE change only the
# reporter's specified expression/declaration; no observation code is appended.
REPORTER_A = """//@version=6
strategy("convert_to_account with another account currency", overlay = true, initial_capital = 10000, currency = currency.EUR)
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = strategy.convert_to_account(1000) / 100000)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""
REPORTER_B = REPORTER_A.replace(
    "strategy.convert_to_account(1000)",
    "1000 * request.currency_rate(syminfo.currency, strategy.account_currency)",
)
CONTROL_NONE = REPORTER_A.replace("currency = currency.EUR", "currency = currency.NONE")
_HEADER = '//@version=6\nstrategy("Host currency", currency = currency.EUR)\n'
_HOST_CALLS = (
    ("strategy.convert_to_account(close)", "strategy_convert_to_account"),
    ("strategy.convert_to_symbol(close)", "strategy_convert_to_symbol"),
    ("request.currency_rate(syminfo.currency, strategy.account_currency)",
     "request_currency_rate"),
)


@pytest.mark.parametrize("call, method", _HOST_CALLS)
@pytest.mark.parametrize("currency, account", [
    ("currency.NONE", "syminfo_.currency"),
    ("currency.EUR", 'std::string("EUR")'),
    ('"JPY"', 'std::string("JPY")'),
])
@pytest.mark.parametrize("requested", [False, True], ids=["chart", "requested"])
def test_host_call_shape_and_context_time(call, method, currency, account, requested):
    expression = (
        f'request.security(syminfo.tickerid, "5", {call})' if requested else call
    )
    source = _HEADER.replace("currency.EUR", currency) + f"observed = {expression}\n"
    cpp = transpile(source)
    assert f"const std::string _pf_currency_account = ({account});" in cpp
    clock = "bar.timestamp" if requested else "current_bar_.timestamp"
    time = f"const std::int64_t _pf_currency_time = ({clock});"
    assert time in cpp
    if requested:
        assert "const std::int64_t _pf_currency_time = (current_bar_.timestamp);" not in cpp
    args = (
        "_pf_currency_from, _pf_currency_to, _pf_currency_account, "
        "_pf_currency_time, _pf_currency_ignore"
        if method == "request_currency_rate" else
        "_pf_currency_value, _pf_currency_account, _pf_currency_time"
    )
    assert f"return this->{method}({args});" in cpp
    if method == "request_currency_rate":
        assert "const bool _pf_currency_ignore = (false);" in cpp


@pytest.mark.parametrize("origin, origin_cpp", [
    ("currency.USD", 'std::string("USD")'),
    ("na", '""'),
])
def test_kwargs_and_currency_constants(origin, origin_cpp):
    source = _HEADER + (
        "a = strategy.convert_to_account(value = close)\n"
        "b = strategy.convert_to_symbol(value = open)\n"
        "r = request.currency_rate(to = currency.EUR, "
        f"ignore_invalid_currency = true, from = {origin})\n"
    )
    cpp = transpile(source)
    assert "const double _pf_currency_value = (current_bar_.close);" in _assignment(cpp, "a")
    assert "const double _pf_currency_value = (current_bar_.open);" in _assignment(cpp, "b")
    request = _assignment(cpp, "r")
    assert f"const std::string _pf_currency_from = ({origin_cpp});" in request
    assert 'const std::string _pf_currency_to = (std::string("EUR"));' in request
    assert "const bool _pf_currency_ignore = (true);" in request
    assert request.index("_pf_currency_from =") < request.index("_pf_currency_to =")
    assert request.index("_pf_currency_to =") < request.index("_pf_currency_ignore =")


@pytest.mark.parametrize("arguments, name, argument_type, spelling", [
    ('1, "USD"', "from", "int", "1"),
    ('"EUR", 1', "to", "int", "1"),
    ('from = true, to = "USD"', "from", "bool", "true"),
    ('to = close, from = "EUR"', "to", "float", "close"),
    ('color.red, "USD"', "from", "color", "color.red"),
    ('array.from("EUR"), "USD"', "from", "array<string>", 'array.from("EUR")'),
    ('"EUR", "USD", 1', "ignore_invalid_currency", "int", "1"),
    ('from = "EUR", to = "USD", ignore_invalid_currency = "false"',
     "ignore_invalid_currency", "string", '"false"'),
    ('"EUR", "USD", na', "ignore_invalid_currency", "na", "na"),
])
def test_currency_rate_argument_types_have_located_diagnostics(
    arguments, name, argument_type, spelling,
):
    # These currently transpile into incompatible C++ or numeric-to-bool
    # coercions. The string/string/bool signature must reject them first.
    statement = f"observed = request.currency_rate({arguments})"
    with pytest.raises(CompileError) as caught:
        transpile(_HEADER + statement + "\n", filename="currency_types.pine")
    diagnostic, = [d for d in caught.value.diagnostics if d.level == Level.ERROR]
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2095"
    assert diagnostic.args == {
        "function_name": "request.currency_rate",
        "argument_type": argument_type,
        "parameter_type": "bool" if name == "ignore_invalid_currency" else "string",
        "name": name,
    }
    assert diagnostic.location.file == "currency_types.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (
        3, statement.index(spelling) + 1,
    )


def test_currency_rate_checks_the_written_helper_argument_type():
    # The untyped parameter stays open at definition time; its written call
    # proves it is numeric, so it cannot become the host's string argument.
    definition = 'pair(origin) => request.currency_rate(origin, "USD")'
    source = _HEADER + definition + "\nobserved = pair(1)\n"
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="currency_helper.pine")
    diagnostic, = [d for d in caught.value.diagnostics if d.level == Level.ERROR]
    assert diagnostic.code == "PF-E2095"
    assert diagnostic.args == {
        "function_name": "request.currency_rate", "argument_type": "int",
        "parameter_type": "string", "name": "from",
    }
    assert diagnostic.location.file == "currency_helper.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (
        3, definition.index("origin", definition.index("request.currency_rate")) + 1,
    )


@pytest.mark.parametrize("function", ["convert_to_account", "convert_to_symbol"])
@pytest.mark.parametrize("arguments, needle, at", [
    ("", "missing its argument 'value'", None),
    ("amount = close", "has no parameter 'amount'", "close"),
    ("close, open", "takes at most 1", "open"),
    ("close, value = open", "got 'value' twice", "open"),
])
@pytest.mark.parametrize("body", [
    "observed = {call}\n",
    "observed = switch bar_index\n    0 => {call}\n    => 0.0\n",
], ids=["direct", "switch-arm"])
def test_convert_calls_require_one_bound_value(function, arguments, needle, at, body):
    # Missing/unknown value bindings currently escape as KeyError, and extra
    # or duplicate values are silently discarded. Switch arms must also fail.
    call = f"strategy.{function}({arguments})"
    source = _HEADER + body.format(call=call)
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="currency_binding.pine")
    diagnostic, = [d for d in caught.value.diagnostics if d.level == Level.ERROR]
    assert diagnostic.code == "PF-E1088"
    assert f"strategy.{function}" in diagnostic.message
    assert needle in diagnostic.message
    line, text = next((line, text) for line, text in enumerate(source.splitlines(), 1)
                      if call in text)
    assert diagnostic.location.file == "currency_binding.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (
        line, text.index(at or call) + 1,
    )


@pytest.mark.parametrize("call, method", _HOST_CALLS)
def test_host_name_collision(call, method):
    source = _HEADER + (
        f"{method} = close + 1\n"
        f"observed = {call} + {method}\n"
    )
    cpp = transpile(source)
    assert method in HOST_MEMBER_NAMES
    assert f"this->{method}(" in cpp
    assert re.search(rf"^\s+pf_safe_{method}\s*=", cpp, re.M)
    assert not re.search(rf"^\s+{method}\s*=", cpp, re.M)


@pytest.mark.parametrize("call, method", _HOST_CALLS)
def test_host_failure_propagation_and_no_default_rate_literal(call, method):
    cpp = transpile(_HEADER + f"observed = {call}\n")
    statement = _assignment(cpp, "observed")
    assert f"return this->{method}(" in statement
    # The lowering has one host return, with no arithmetic, identity test,
    # rate fallback, guessed daily shift or rate lookup table of its own.
    assert statement.count("return ") == 1
    for shortcut in (" ? ", "if (", "0.9", "1.0", "1.2", "86400000",
                     "account_currency_fx", "unordered_map", "std::map"):
        assert shortcut not in statement
    # Propagate the engine's exception, code and arguments without relabeling.
    # Currency failures have no API for attaching the original Pine call site.
    assert "_PF_NO_DATA_STOP(" not in statement
    assert "try {" not in statement
    assert "catch (" not in statement


# Separate real-engine TU: its public C declarations cannot conflict with the
# generated library's Bar/ReportC ABI declarations. These functions only forward
# to the actual engine setters; they implement no host or conversion semantics.
_HOST_INPUT_CPP = r'''
#include <pineforge/pineforge.h>
#include <cstdint>
extern "C" int _pf_test_currency_scalar(void* strategy, double rate) {
    return strategy_set_account_currency_fx_scalar(
        static_cast<pf_strategy_t>(strategy), rate);
}
extern "C" int _pf_test_currency_series(void* strategy) {
    // At the first rising close (42 * 60000), the rate is 0.9; at the
    // next-bar fill it is 1.2. This distinguishes evaluation from fill time.
    const std::int64_t times[] = {60000, 2520000, 2580000};
    const double rates[] = {0.7, 0.9, 1.2};
    return strategy_set_account_currency_fx_series(
        static_cast<pf_strategy_t>(strategy), times, rates, 3);
}
'''
_DRIVER_HEADER = r'''
#include <cmath>
#include <iomanip>
#include <iostream>
extern "C" int _pf_test_currency_scalar(void*, double);
extern "C" int _pf_test_currency_series(void*);
struct CurrencyStrategy : GeneratedStrategy {
    explicit CurrencyStrategy(const char* quote = "USD") {
        syminfo_.currency = quote;
    }
};
'''


@pytest.mark.parametrize("call, method", _HOST_CALLS)
def test_missing_rate_preserves_host_failure_transport(call, method, tmp_path):
    compile_env.skip_if_no_engine_lib()
    host_arguments = (
        '"USD", "EUR", "EUR", at_ms, false'
        if method == "request_currency_rate" else '100.0, "EUR", at_ms'
    )
    driver = _DRIVER_HEADER + r'''
int main() {
    CurrencyStrategy strategy("USD");
    const std::int64_t at_ms = 1743465600000LL;
    ::pineforge::RunFailureValue expected;
    try {
''' + f"        strategy.{method}({host_arguments});\n" + r'''
        return 1;
    } catch (const ::pineforge::coded<std::runtime_error>& error) {
        expected = error.run_failure();
    }
    Bar bars[] = {Bar{100, 100, 100, 100, 1, at_ms}};
    ReportC report{};
    run_backtest_full(&strategy, bars, 1, "1", "1", 0, 4, 0, &report);
    report_free(&report);
    if (strategy.last_error().empty()) return 2;
    const auto actual = ::pineforge::run_failure_value_of(strategy);
    // Compare canonical argument bytes, not just selected JSON fields.
    if (actual.code != expected.code || !expected.args || !actual.args
        || *actual.args != *expected.args) return 3;
    std::cout << ::pineforge::run_failure_code_of(strategy) << '\n'
              << ::pineforge::run_failure_args_of(strategy) << '\n';
}
'''
    result = _compile_and_run(
        _HEADER + f"observed = {call}\n", driver, tmp_path, "missing_rate",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    code, arguments = result.stdout.splitlines()
    assert code == "currency_conversion_rate_unavailable"
    reverse = method == "strategy_convert_to_symbol"
    assert json.loads(arguments) == {
        "function": method.removeprefix("strategy_").removeprefix("request_"),
        "reason": "rate_unavailable",
        "from": "EUR" if reverse else "USD",
        "to": "USD" if reverse else "EUR",
        "account_currency": "EUR",
        "symbol_currency": "USD",
        "time_ms": 1743465600000,
    }


def _run(source: str, driver: str, directory: Path, label: str) -> str:
    compile_env.skip_if_no_engine_lib()
    bridge = directory / f"{label}_host_input.cpp"
    bridge.write_text(_HOST_INPUT_CPP)
    result = _compile_and_run(
        source, _DRIVER_HEADER + driver, directory, label, extra_sources=(bridge,),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    return result.stdout


_REPORTER_DRIVER = r'''
int main() {
    std::vector<Bar> bars;
    for (int i = 0; i < 120; ++i) {
        const double price = i < 40 ? 100.0 :
            (i < 80 ? 100.0 + (i - 40) : 140.0 - 2.0 * (i - 80));
        bars.push_back(Bar{price, price, price, price, 1.0,
                           (i + 1) * std::int64_t{60000}});
    }
    std::cout << std::setprecision(17);
    for (int mode = 0; mode < 3; ++mode) {
        CurrencyStrategy strategy("USDT");
        if (mode == 0 && _pf_test_currency_scalar(&strategy, 0.9) != 0) return 1;
        if (mode == 1 && _pf_test_currency_series(&strategy) != 0) return 2;
        ReportC report{};
        run_backtest_full(&strategy, bars.data(), static_cast<int>(bars.size()),
                          "1", "1", 0, 4, 0, &report);
        const std::string error = strategy.last_error();
        if (!error.empty()) {
            report_free(&report);
            if (mode != 2 || std::string(::pineforge::run_failure_code_of(strategy))
                != "currency_conversion_rate_unavailable") {
                std::cerr << error;
                return 3;
            }
            std::cout << "missing\n";
            continue;
        }
        if (report.trades_len != 1) {
            report_free(&report);
            return 4;
        }
        std::cout << std::abs(report.trades[0].qty) << '\n';
        report_free(&report);
    }
}
'''


@pytest.fixture(scope="module")
def reporter_runs(tmp_path_factory):
    directory = tmp_path_factory.mktemp("host_currency_reporter")
    results = {}

    def run(name):
        if name not in results:
            source = {"A": REPORTER_A, "B": REPORTER_B, "NONE": CONTROL_NONE}[name]
            results[name] = _run(
                source, _REPORTER_DRIVER, directory, f"reporter_{name}",
            ).splitlines()
            assert len(results[name]) == 3
        return results[name]

    return run


def test_reporter_A_scalar_and_series(reporter_runs):
    scalar, series, _ = reporter_runs("A")
    assert float(scalar) == pytest.approx(1000 * 0.9 / 100000)
    assert float(series) == pytest.approx(1000 * 0.9 / 100000)


def test_reporter_B_forward_equals_A(reporter_runs):
    a = reporter_runs("A")
    b = reporter_runs("B")
    assert list(map(float, b[:2])) == pytest.approx([1000 * 0.9 / 100000] * 2)
    assert b == a


def test_absent_rate_refuses_evaluated_convert(reporter_runs):
    assert reporter_runs("A")[2] == "missing"


def _values(source, names, tmp_path, *, setup="scalar", quote="USD"):
    install = {
        "scalar": "if (_pf_test_currency_scalar(&strategy, 0.9) != 0) return 1;",
        "one": "if (_pf_test_currency_scalar(&strategy, 1.0) != 0) return 1;",
        "absent": "",
    }[setup]
    output = " << ' ' << ".join(f"strategy.{name}" for name in names)
    driver = r'''
int main() {
''' + f'    CurrencyStrategy strategy("{quote}");\n    {install}\n' + r'''
    Bar bars[] = {
        Bar{100, 100, 100, 100, 1, 1743465600000LL},
        Bar{200, 200, 200, 200, 1, 1743465660000LL},
    };
    ReportC report{};
    run_backtest_full(&strategy, bars, 2, "1", "1", 0, 4, 0, &report);
    report_free(&report);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    std::cout << std::setprecision(17) << ''' + output + r''' << '\n';
}
'''
    return list(map(float, _run(source, driver, tmp_path, "values").split()))


def test_convert_to_symbol_and_reverse_request(tmp_path):
    values = _values(_HEADER + (
        "amount = strategy.convert_to_symbol(900)\n"
        "rate = request.currency_rate(strategy.account_currency, syminfo.currency)\n"
    ), ("amount", "rate"), tmp_path)
    assert values == pytest.approx([900 / 0.9, 1 / 0.9])


def test_NONE_and_explicit_same_currency_identity(reporter_runs, tmp_path):
    assert list(map(float, reporter_runs("NONE"))) == pytest.approx([1000 / 100000] * 3)
    source = _HEADER + (
        "amount = strategy.convert_to_account(37)\n"
        "back = strategy.convert_to_symbol(37)\n"
        "rate = request.currency_rate(syminfo.currency, strategy.account_currency)\n"
    )
    # Account and quote are EUR: identity even with a present non-one input.
    for setup in ("absent", "scalar"):
        assert _values(source, ("amount", "back", "rate"), tmp_path,
                       setup=setup, quote="EUR") == [37, 37, 1]
    # A present 1.0 remains supplied data when the identities differ.
    assert _values(source, ("amount", "back", "rate"), tmp_path, setup="one") == [37, 37, 1]


def test_generic_variable_helper_pairs(tmp_path):
    source = _HEADER + (
        'pair(string origin, string target) => request.currency_rate(origin, target)\n'
        'origin = input.string("USD", "Origin")\n'
        'target = strategy.account_currency\n'
        'forward = pair(origin, target)\n'
        'reverse = pair(target, origin)\n'
    )
    assert _values(source, ("forward", "reverse"), tmp_path) == pytest.approx([0.9, 1 / 0.9])


_ARGUMENT_EFFECTS = _HEADER + (
    "var calls = array.new_int(1, 0)\n"
    "amount(int digit, float value) =>\n"
    "    array.set(calls, 0, array.get(calls, 0) * 10 + digit)\n"
    "    value\n"
    "code(int digit, string value) =>\n"
    "    array.set(calls, 0, array.get(calls, 0) * 10 + digit)\n"
    "    value\n"
    "ignore() =>\n"
    "    array.set(calls, 0, array.get(calls, 0) * 10 + 5)\n"
    "    false\n"
    "array.set(calls, 0, 0)\n"
    "a = strategy.convert_to_account(value = amount(1, close))\n"
    "b = strategy.convert_to_symbol(value = amount(2, 900))\n"
    'r = request.currency_rate(ignore_invalid_currency = ignore(), '
    'to = code(4, "EUR"), from = code(3, "USD"))\n'
    "seen = array.get(calls, 0)\n"
)


def test_argument_effects_once_emission():
    # Match the parameter-order staging of other builtins (builtin_keywords,
    # array.new and color.from_gradient), independently of keyword spelling.
    cpp = transpile(_ARGUMENT_EFFECTS)
    for name, method in (("a", "strategy_convert_to_account"), ("b", "strategy_convert_to_symbol")):
        statement = _assignment(cpp, name)
        assert len(re.findall(r"\bamount(?:_cs\d+)?\(", statement)) == 1
        assert f"return this->{method}(" in statement
    request = _assignment(cpp, "r")
    assert len(re.findall(r"\bcode(?:_cs\d+)?\(", request)) == 2
    assert len(re.findall(r"\bignore(?:_cs\d+)?\(", request)) == 1
    assert (request.index("_pf_currency_from =") < request.index("_pf_currency_to =")
            < request.index("_pf_currency_ignore =") < request.index("this->request_currency_rate("))


def test_argument_effects_once(tmp_path):
    # The two conversion values append 1, 2. The request's declared slots are
    # from, to, ignore_invalid_currency, appending 3, 4, 5 despite written order.
    # This pins PineForge's builtin rule, not a measured TradingView outcome.
    assert _values(_ARGUMENT_EFFECTS, ("seen", "a", "b", "r"), tmp_path) == pytest.approx(
        [12345, 200 * 0.9, 900 / 0.9, 0.9],
    )


_SKIPPED_BRANCH = _HEADER + (
    "var calls = array.new_int(1, 0)\n"
    "amount() =>\n"
    "    array.set(calls, 0, array.get(calls, 0) + 1)\n"
    "    close\n"
    "a = close > 0 ? 7.0 : strategy.convert_to_account(amount())\n"
    "b = false ? strategy.convert_to_symbol(amount()) : 8.0\n"
    "if close < 0\n"
    "    strategy.entry('skipped', strategy.long, qty = strategy.convert_to_account(amount()))\n"
    "seen = array.get(calls, 0)\n"
)


def test_skipped_branch_no_lookup_emission():
    cpp = transpile(_SKIPPED_BRANCH)
    assert "this->strategy_convert_to_account(" in _assignment(cpp, "a").split(" : ", 1)[1]
    assert "this->strategy_convert_to_symbol(" in _assignment(cpp, "b").split(" : ", 1)[0]


def test_skipped_branch_no_lookup(tmp_path):
    assert _values(_SKIPPED_BRANCH, ("seen", "a", "b"), tmp_path, setup="absent") == [0, 7, 8]


def test_NA_amount_with_present_rate(tmp_path):
    source = _HEADER + (
        "float missing = na\n"
        "a = strategy.convert_to_account(missing)\n"
        "b = strategy.convert_to_symbol(missing)\n"
        "both_na = na(a) and na(b)\n"
    )
    assert _values(source, ("both_na",), tmp_path) == [1]


def test_ignored_unavailable_pair_returns_na(tmp_path):
    # Neither same-spelled input codes nor a present rate imply support for a
    # third currency pair. The real engine owns the visible warning as well.
    source = _HEADER + (
        'r = request.currency_rate("GBP", "JPY", ignore_invalid_currency = true)\n'
        "missing = na(r)\n"
        "completed = bar_index + 1\n"
    )
    assert _values(source, ("missing", "completed"), tmp_path) == [1, 2]
