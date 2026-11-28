"""Issue #431: admit seconds-to-timeframe conversion and emit a string."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


_HEADER = '//@version=6\nstrategy("Timeframe conversion")\n'
_BASE = "9b17c40e074a1bcab7ec581b5e71f13b171deb42"

# TradingView, measured 2026-11-28, in the order measured; the trailing
# comments number the rows from zero. Pairs are (from_seconds input, decoded
# string) or (in_seconds input, decoded integer). Keep the repeated controls.
_MEASURED = (
    (30, "30S"), (900, "15"), (3600, "60"), (86_400, "1D"),          # 0–3
    (604_800, "1W"), (1, "1S"), (2, "5S"), (5, "5S"),               # 4–7
    (6, "10S"), (45, "1"), (46, "1"), (59, "1"),                    # 8–11
    (60, "1"), (61, "2"), (86_340, "1439"), (86_341, "1440"),        # 12–15
    (86_399, "1440"), (86_401, "2D"), (518_400, "6D"), (518_401, "7D"),
    (604_799, "7D"), (604_801, "8D"), (1_209_600, "2W"), (2_592_000, "30D"),
    (2_592_001, "31D"), (2_600_000, "31D"), (2_627_999, "31D"), (2_628_000, "31D"),
    (2_628_001, "31D"), (2_628_002, "31D"), (2_628_003, "1M"), (2_628_004, "31D"),
    (2_678_400, "31D"), (5_256_000, "61D"), (5_256_001, "61D"), (5_256_006, "2M"),
    (31_449_600, "52W"), (31_449_601, "365D"), (31_500_000, "365D"), (31_535_999, "365D"),
    (31_536_000, "12M"), (31_536_001, "12M"), (31_536_036, "12M"), (31_536_037, "12M"),
    (31_622_399, "12M"), (31_622_400, "12M"), (31_622_401, "12M"), (63_072_000, "12M"),
    ("1D", 86_400), ("1W", 604_800), ("1M", 2_628_003), ("2M", 5_256_006),  # 48–51
    ("12M", 31_536_036), ("365D", 31_536_000), ("52W", 31_449_600),           # 52–54
    # 55–59: substitute the TV integer in the outer call; the pinned engine
    # cannot supply the inner month duration. Offsets are +1 and -1 in 58–59.
    (2_628_003, "1M"), (5_256_006, "2M"), (31_536_036, "12M"),
    (2_628_004, "31D"), (2_628_002, "31D"),
    (900, "15"), (604_800, "1W"), ("60", 3600), (30, "30S"),         # 60–63
)


def _from_seconds_cases():
    for row, (argument, decoded) in enumerate(_MEASURED):
        if isinstance(argument, int):
            yield f"measured-row-{row}-input-{argument}", argument, decoded
        elif argument.endswith("M"):
            # 50–52 measured the other direction. Use TV's seconds as input;
            # this does not assert that the engine can produce those seconds.
            yield f"measured-row-{row}-seconds-of-{argument}", decoded, argument
    # TradingView, measured 2026-11-28: zero and minus one both give "1S".
    yield "measured-zero", 0, "1S"
    yield "measured-minus-one", -1, "1S"
    # Compatibility in gaps in the tape, not attributed to TradingView.
    yield "untaped-15", 15, "15S"
    yield "untaped-31", 31, "45S"
    yield "untaped-44", 44, "45S"
    # Not measured on TradingView either (as of 2026-11-28). Each expected
    # value is the output from before the measured rule, which may change
    # measured inputs only; these rows are not TradingView's answers.
    yield "unmeasured-1209599", 1_209_599, "2W"     # one second below 2 weeks
    yield "unmeasured-5255999", 5_255_999, "2M"     # below the measured 5,256,000
    yield "unmeasured-7884000", 7_884_000, "3M"     # 3 months of 2,628,000 seconds
    yield "unmeasured-7884009", 7_884_009, "92D"    # 3 months of 2,628,003 seconds
    yield "unmeasured-31449599", 31_449_599, "52W"  # one second below 52 weeks


_FROM_SECONDS = tuple(_from_seconds_cases())


@pytest.mark.parametrize("seconds,expected", [
    pytest.param(seconds, expected, id=slot)
    for slot, seconds, expected in _FROM_SECONDS if seconds >= 0
])
def test_literal_seconds_emit_a_timeframe_string(seconds, expected):
    cpp = transpile(_HEADER + f"tf = timeframe.from_seconds(seconds = {seconds})\n")
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
        + "tf = timeframe.from_seconds(close > open ? 604799 : 604800)\n"
        + "requested = request.security(syminfo.tickerid, tf, close)\n"
    )
    assert 'register_security_eval(0, "7D",' in cpp
    assert 'register_security_eval(1, "1W",' in cpp


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_timeframe_from_seconds_helper_compiles_and_runs(optimization):
    from tests._compile import run_emitted_tu

    # bar_index is zero on our one bar. The literal test above folds; these
    # calls reach the emitted helper for every row, regardless of the literal.
    rows = [
        (slot, f"timeframe.from_seconds(bar_index + ({seconds}))", expected)
        for slot, seconds, expected in _FROM_SECONDS
    ]
    rows.extend([
        # TradingView, measured 2026-11-28: a literal minus one, and int(na).
        ("measured-minus-one-literal", "timeframe.from_seconds(-1)", "1S"),
        ("measured-int-na", "timeframe.from_seconds(int(na))", "0D"),
        # Same supported missing argument without the cast; only int(na) was taped.
        ("bare-na", "timeframe.from_seconds(na)", "0D"),
    ])
    # Exercise the direct in_seconds controls that do not need the engine's month fix.
    for row, (argument, decoded) in enumerate(_MEASURED):
        if isinstance(argument, str) and not argument.endswith("M"):
            rows.append((
                f"measured-row-{row}-in-seconds-{argument}",
                f'timeframe.in_seconds("{argument}")',
                decoded,
            ))
    source = _HEADER + "".join(f"row{i} = {expr}\n" for i, (_, expr, _) in enumerate(rows))
    prints = [f'    std::cout << "{slot}=" << strategy.row{i} << "\\n";'
              for i, (slot, _, _) in enumerate(rows)]
    driver = r'''
#include <iomanip>
#include <iostream>
#include <limits>
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
    std::cout << std::setprecision(std::numeric_limits<double>::max_digits10);
''' + "\n".join(prints) + "\n}\n"
    output = run_emitted_tu(
        transpile(source),
        driver,
        opt=optimization,
        label="timeframe.from_seconds",
    )
    actual = output.splitlines()
    assert len(actual) == len(rows), (
        f"NOT COMPARED: expected {len(rows)} output rows, received {len(actual)}"
    )
    assert actual == [f"{slot}={expected}" for slot, _, expected in rows]


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
