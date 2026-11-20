"""Issue #376: exercise the generated calls with real host FX configuration."""

import json
import math

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


_SCALAR = "if (!strategy.set_account_currency_fx_scalar(0.5)) return 1;"
_SERIES = """
        const int64_t times[] = {bars[0].timestamp - 1, bars[2].timestamp};
        const double rates[] = {0.25, 4.0};
        if (!strategy.set_account_currency_fx_series(times, rates, 2)) return 1;
"""
_LATE_SERIES = """
        const int64_t start = bars[2].timestamp;
        const double rate = 2.0;
        if (!strategy.set_account_currency_fx_series(&start, &rate, 1)) return 1;
"""
_CODE = "currency_conversion_rate_unavailable"
_MESSAGE = "request.currency_rate needs currency conversion data that PineForge does not have."


def _run(body, fields, *, install="", quote="USD"):
    source = f'//@version=6\nstrategy("FX", currency=currency.EUR)\n{body}\n'
    # Observe each bar by running a fresh strategy over each successive prefix.
    # Stream the engine's diagnostics separately from the script's numeric fields.
    outputs = "".join(f" << strategy.{field} << ' '" for field in fields.split())
    driver = r"""
#include <iostream>
int main() {
    std::vector<Bar> bars;
    for (int i = 0; i < 4; ++i)
        bars.push_back(Bar{100., 100., 100., 100., 1., 1704067200000LL + i * 60000});
    for (int n = 1; n <= 4; ++n) {
        GeneratedStrategy strategy;
        SymInfo symbol;
        symbol.currency = QUOTE;
        INSTALL
        try {
            strategy.run(bars.data(), n, "1", "1", {}, symbol);
        } catch (const std::exception& e) {
            std::cout << "stop\t" << run_failure_code_name(classify_run_failure(e).code)
                      << '\t' << e.what() << '\n';
            continue;
        }
        if (!strategy.last_error().empty()) {
            std::cout << "stop\t" << run_failure_code_of(strategy) << '\t'
                      << strategy.last_error() << '\n';
            continue;
        }
        std::cout << "ok\t" OUTPUTS << '\t' << strategy_get_run_warnings(&strategy) << '\n';
    }
}
""".replace("QUOTE", json.dumps(quote)).replace("INSTALL", install).replace("OUTPUTS", outputs)
    raw = run_emitted_tu(transpile(source), driver, opt="-O0", label="currency conversion")
    rows = [line.split("\t") for line in raw.splitlines()]
    assert len(rows) == 4, raw
    return rows


def _success(row):
    status, fields, warnings = row
    assert status == "ok", row
    return list(map(float, fields.split())), json.loads(warnings)


@pytest.mark.parametrize("install,quote,rates,warn", [
    pytest.param(_SCALAR, "USD", [0.5] * 4, False, id="scalar"),
    pytest.param(_SERIES, "USD", [0.25, 0.25, 4, 4], False, id="series-change-bar"),
    pytest.param(_LATE_SERIES, "USD", [1, 1, 2, 2], True, id="no-rate-before-first-point"),
    pytest.param(_SCALAR, "EUR", [1] * 4, False, id="same-codes-with-rate"),
    pytest.param("", "EUR", [1] * 4, False, id="same-codes-without-rate"),
    pytest.param("", "USD", [1] * 4, True, id="no-rate"),
    pytest.param(_SCALAR, "?", [1] * 4, True, id="invalid-code-with-rate"),
])
def test_strategy_values_effects_and_warnings(install, quote, rates, warn):
    body = """countCalls() =>
    var int count = 0
    count += 1
    count
amount = strategy.convert_to_account(1000)
quantity = strategy.convert_to_symbol(500)
accumulated = strategy.convert_to_account(ta.cum(1))
counted = strategy.convert_to_symbol(countCalls())"""
    rows = _run(body, "amount quantity accumulated counted", install=install, quote=quote)
    for n, (row, rate) in enumerate(zip(rows, rates), 1):
        values, warnings = _success(row)
        assert values == [1000 * rate, 500 / rate, n * rate, n / rate]
        assert [w["code"] for w in warnings] == ([_CODE] * 4 if warn else [])


@pytest.mark.parametrize("install,quote,rates", [
    pytest.param(_SCALAR, "USD", [0.5] * 4, id="scalar"),
    pytest.param(_SERIES, "USD", [0.25, 0.25, 4, 4], id="series-both-directions"),
    pytest.param("", "EUR", [1] * 4, id="same-codes-no-rate-needed"),
])
def test_request_reads_the_pair_in_either_order(install, quote, rates):
    rows = _run(
        "forwardRate = request.currency_rate(syminfo.currency, strategy.account_currency)\n"
        "reverseRate = request.currency_rate(strategy.account_currency, syminfo.currency)",
        "forwardRate reverseRate", install=install, quote=quote,
    )
    for row, rate in zip(rows, rates):
        assert _success(row) == ([rate, 1 / rate], [])


def test_request_evaluates_dynamic_pair_and_flag_once_only_in_taken_branch():
    body = """var digits = array.new_int(1, 0)
markCode(int digit, string code) =>
    array.set(digits, 0, array.get(digits, 0) * 10 + digit)
    code
markFlag() =>
    array.set(digits, 0, array.get(digits, 0) * 10 + 3)
    false
rate = bar_index == 1 ? request.currency_rate(markCode(1, syminfo.currency), markCode(2, strategy.account_currency), ignore_invalid_currency=markFlag()) : 7.0
seen = array.get(digits, 0)"""
    rows = _run(body, "rate seen", install=_SCALAR)
    assert [_success(row) for row in rows] == [
        ([7, 0], []), ([0.5, 123], []), ([7, 123], []), ([7, 123], []),
    ]


_PAIR = "syminfo.currency, strategy.account_currency"


@pytest.mark.parametrize("call,install,quote,outcome", [
    pytest.param(_PAIR, "", "USD", _CODE,
                 id="missing-rate-coded-stop"),
    pytest.param(_PAIR + ", ignore_invalid_currency=true", "", "USD", "na",
                 id="missing-rate-ignored"),
    pytest.param(_PAIR, _SCALAR, "?", _CODE, id="invalid-code-with-rate-stops"),
    pytest.param(_PAIR + ", true", _SCALAR, "?", "na", id="invalid-code-with-rate-ignored"),
    pytest.param('"GBP", "JPY", true', _SCALAR, "USD", "no_data_request",
                 id="unrelated-pair-stop"),
])
def test_request_refusal(call, install, quote, outcome):
    for row in _run(f"rate = request.currency_rate({call})", "rate", install=install, quote=quote):
        if outcome == "na":
            (value,), warnings = _success(row)
            assert math.isnan(value)
            assert [w["code"] for w in warnings] == [_CODE]
        else:
            assert row[:2] == ["stop", outcome], row
            if outcome == "no_data_request":
                assert row[2] == _MESSAGE


# The five tests below answer the review's "not verified" list (CGI, after the lane).

def test_explicit_host_rate_of_one_converts_with_no_warning():
    # The engine counts an explicit 1.0 as host data: the value is v * 1, nothing is recorded.
    install = "if (!strategy.set_account_currency_fx_scalar(1.0)) return 1;"
    rows = _run(
        "amount = strategy.convert_to_account(1000)\n"
        "quantity = strategy.convert_to_symbol(500)\n"
        "forward = request.currency_rate(syminfo.currency, strategy.account_currency)",
        "amount quantity forward", install=install,
    )
    for row in rows:
        assert _success(row) == ([1000.0, 500.0, 1.0], [])


def test_non_binary_rate_is_applied_in_both_directions():
    install = "if (!strategy.set_account_currency_fx_scalar(0.9)) return 1;"
    rows = _run(
        "amount = strategy.convert_to_account(1000)\n"
        "quantity = strategy.convert_to_symbol(900)\n"
        "forward = request.currency_rate(syminfo.currency, strategy.account_currency)\n"
        "reverse = request.currency_rate(strategy.account_currency, syminfo.currency)",
        "amount quantity forward reverse", install=install,
    )
    for row in rows:
        values, warnings = _success(row)
        assert values == pytest.approx([900.0, 1000.0, 0.9, 1 / 0.9], rel=1e-5)
        assert warnings == []


def test_conversions_inside_a_function_and_a_loop_use_the_rate():
    body = """scaled(float x) =>
    strategy.convert_to_account(x)
total = 0.0
for i = 1 to 3
    total += strategy.convert_to_account(i * 100.0)
inside = scaled(1000)"""
    for row in _run(body, "inside total", install=_SCALAR):
        assert _success(row) == ([500.0, 300.0], [])


def test_conversion_inside_a_security_expression_builds_and_runs():
    body = 'viaSecurity = request.security(syminfo.tickerid, "1", strategy.convert_to_account(1000))'
    rows = _run(body, "viaSecurity", install=_SCALAR)
    values, warnings = _success(rows[-1])
    assert values == [500.0]
    assert _CODE not in [w["code"] for w in warnings]


def test_currency_constants_are_accepted_as_request_arguments():
    for row in _run("rate = request.currency_rate(currency.USD, currency.EUR)", "rate", install=_SCALAR):
        assert _success(row) == ([0.5], [])
