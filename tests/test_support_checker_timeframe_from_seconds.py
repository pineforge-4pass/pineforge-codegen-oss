"""Issue #431: admit seconds-to-timeframe conversion and emit a string."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


_HEADER = '//@version=6\nstrategy("Timeframe conversion")\n'
_BASE = "9b17c40e074a1bcab7ec581b5e71f13b171deb42"


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
