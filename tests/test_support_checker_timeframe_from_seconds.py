"""Issue #431: admit seconds-to-timeframe conversion and emit a string."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


_HEADER = '//@version=6\nstrategy("Timeframe conversion")\n'
_BASE = "9b17c40e074a1bcab7ec581b5e71f13b171deb42"

# TradingView captures are pending for these rule-only groups. The expected
# values below come from the documented rounding rule, not from a tape.
RULE_ONLY_CASES = [
    pytest.param(
        "tf2 = timeframe.from_seconds(2)\n",
        {"tf2": "5S"},
        id="rule-only-2",
    ),
    pytest.param(
        "tf46 = timeframe.from_seconds(46)\n",
        {"tf46": "1"},
        id="rule-only-46",
    ),
    pytest.param(
        "tf61 = timeframe.from_seconds(61)\n",
        {"tf61": "2"},
        id="rule-only-61",
    ),
    pytest.param(
        "tf86401 = timeframe.from_seconds(86401)\n",
        {"tf86401": "2D"},
        id="rule-only-86401",
    ),
    pytest.param(
        "tf_month = timeframe.from_seconds(2628000)\n",
        {"tf_month": "1M"},
        id="rule-only-month",
    ),
    pytest.param(
        "tf_month_before = timeframe.from_seconds(2627999)\n"
        "tf_month_after = timeframe.from_seconds(2628001)\n",
        {"tf_month_before": "1M", "tf_month_after": "31D"},
        id="rule-only-month-ties",
    ),
    pytest.param(
        "tf_year_before = timeframe.from_seconds(31535999)\n"
        "tf_year_exact = timeframe.from_seconds(31536000)\n"
        "tf_year_after = timeframe.from_seconds(31536001)\n",
        {
            "tf_year_before": "12M",
            "tf_year_exact": "12M",
            "tf_year_after": "12M",
        },
        id="rule-only-year-boundaries",
    ),
    pytest.param(
        "tf_zero = timeframe.from_seconds(0)\n"
        "tf_negative = timeframe.from_seconds(-1)\n",
        {"tf_zero": "1S", "tf_negative": "1S"},
        id="rule-only-nonpositive",
    ),
]


@pytest.mark.parametrize(("body", "expected"), RULE_ONLY_CASES)
def test_rule_only_timeframe_from_seconds_rows(body, expected):
    cpp = transpile(_HEADER + body)
    for name, value in expected.items():
        assert (
            f'{name} = std::string("{value}");' in cpp
            or f"{name} = ::_pf_timeframe_from_seconds(" in cpp
        )


@pytest.mark.parametrize(("argument", "expected"), [
    pytest.param("30", "30S", id="seconds"),
    pytest.param("900", "15", id="reporter"),
    pytest.param("seconds = 3600", "60", id="named-hour-as-minutes"),
    pytest.param("86400", "1D", id="day"),
    pytest.param("604800", "1W", id="week"),
    # Nominal-month conversion; a TradingView capture of this input is pending.
    pytest.param("2628000", "1M", id="nominal-month"),
])
def test_literal_seconds_emit_a_timeframe_string(argument, expected):
    cpp = transpile(_HEADER + f"tf = timeframe.from_seconds({argument})\n")
    assert f'tf = std::string("{expected}");' in cpp


def test_series_seconds_are_lowered_to_a_string():
    cpp = transpile(
        _HEADER + "tf = timeframe.from_seconds(seconds = bar_index + 1)\n"
        'withUnit = tf + "!"\n'
    )
    assert "std::string tf" in cpp
    _, call = cpp.split("tf = ::_pf_timeframe_from_seconds(", 1)
    assert "pine_bar_index()" in call.split(";\n", 1)[0]


def test_computed_request_registers_both_converted_timeframes():
    cpp = transpile(
        _HEADER
        + "tf = timeframe.from_seconds(close > open ? 900 : 3600)\n"
        + "requested = request.security(syminfo.tickerid, tf, close)\n"
    )
    assert 'register_security_eval(0, "15",' in cpp
    assert 'register_security_eval(1, "60",' in cpp


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_timeframe_from_seconds_helper_compiles_and_runs(optimization):
    from tests._compile import run_emitted_tu

    source = _HEADER + (
        "tf2 = timeframe.from_seconds(2)\n"
        "tf46 = timeframe.from_seconds(46)\n"
        "tf61 = timeframe.from_seconds(61)\n"
        "tf86401 = timeframe.from_seconds(86401)\n"
        "tfMonth = timeframe.from_seconds(2628000)\n"
        "tfMonthBefore = timeframe.from_seconds(2627999)\n"
        "tfMonthAfter = timeframe.from_seconds(2628001)\n"
        "tfYearBefore = timeframe.from_seconds(31535999)\n"
        "tfYearExact = timeframe.from_seconds(31536000)\n"
        "tfYearAfter = timeframe.from_seconds(31536001)\n"
        "tfZero = timeframe.from_seconds(0)\n"
        "tfNegative = timeframe.from_seconds(-1)\n"
        "tfNa = timeframe.from_seconds(na)\n"
    )
    driver = r'''
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars(1);
    bars[0].open = bars[0].high = bars[0].low = bars[0].close = 100.0;
    bars[0].volume = 1.0;
    bars[0].timestamp = 1704067200000LL;
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "60", "60");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 1;
    }
    std::cout
        << strategy.tf2 << "\n"
        << strategy.tf46 << "\n"
        << strategy.tf61 << "\n"
        << strategy.tf86401 << "\n"
        << strategy.tfMonth << "\n"
        << strategy.tfMonthBefore << "\n"
        << strategy.tfMonthAfter << "\n"
        << strategy.tfYearBefore << "\n"
        << strategy.tfYearExact << "\n"
        << strategy.tfYearAfter << "\n"
        << strategy.tfZero << "\n"
        << strategy.tfNegative << "\n"
        << "[" << strategy.tfNa << "]\n";
}
'''
    output = run_emitted_tu(
        transpile(source),
        driver,
        opt=optimization,
        label="timeframe.from_seconds",
    )
    assert output.splitlines() == [
        "5S", "1", "2", "2D", "1M", "1M", "31D",
        "12M", "12M", "12M", "1S", "1S", "[]",
    ]


@pytest.mark.parametrize(("arguments", "message"), [
    ("", "is missing its argument 'seconds'"),
    ("900, 1800", "takes at most 1 arguments"),
    ("timeframe = 900", "has no parameter 'timeframe'"),
    ('"900"', "with an argument of type string for int parameter 'seconds'"),
    ("900.5", "with an argument of type float for int parameter 'seconds'"),
])
def test_invalid_seconds_call_names_the_signature_problem(arguments, message):
    with pytest.raises(CompileError) as caught:
        transpile(_HEADER + f"tf = timeframe.from_seconds({arguments})\n")
    error, = caught.value.diagnostics
    assert error.code.startswith("PF-E")
    assert error.location.line == 3
    assert "timeframe.from_seconds" in error.message
    assert message in error.message


def test_script_without_from_seconds_keeps_base_cpp_bytes(tmp_path):
    from tests._e2e import reference_codegen, transpile_json

    pine = tmp_path / "strategy.pine"
    pine.write_text(
        _HEADER
        + 'secondsCount = timeframe.in_seconds(timeframe = "15")\n'
        + 'if timeframe.change("1D") and secondsCount > 0\n'
        + '    strategy.entry("L", strategy.long)\n',
        encoding="utf-8",
    )
    reference = reference_codegen(_BASE)
    assert reference is not None, (
        f"NOT COMPARED: reference commit unavailable; base={_BASE}; candidate=not read"
    )
    before = transpile_json(pine, root=reference)
    after = transpile_json(pine)
    assert before.get("ok") and after.get("ok") and before.get("cpp") and after.get("cpp"), (
        f"NOT COMPARED: missing successful C++ output; base={before!r}; candidate={after!r}"
    )
    assert after["cpp"].encode("utf-8") == before["cpp"].encode("utf-8")
