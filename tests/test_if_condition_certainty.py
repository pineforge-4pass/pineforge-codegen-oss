"""Numeric condition checks require evidence beyond inferred fallback types."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp


_BOOL_CASES = [
    pytest.param(
        "", "ta.crossover(close, open)",
        # Crossover returns bool even when the analyzer stores FLOAT.
        id="crossover",
    ),
    pytest.param(
        "", "ta.crossunder(close, open)",
        # Crossunder returns bool even when the analyzer stores FLOAT.
        id="crossunder",
    ),
    pytest.param(
        "", "ta.rising(close, 2)",
        # Rising returns bool, so its numeric arguments do not make it numeric.
        id="rising",
    ),
    pytest.param(
        "", "session.isfirstbar",
        # A session flag is bool even when its inferred type is INT.
        id="session_flag",
    ),
    pytest.param(
        "", 'request.security(syminfo.tickerid, "60", close > open)',
        # A requested comparison remains bool on the requested clock.
        id="requested_comparison",
    ),
    pytest.param(
        "", "bool(close)",
        # An explicit bool conversion supplies a bool condition.
        id="bool_conversion",
    ),
    pytest.param(
        "flag = ta.crossover(close, open)\n", "flag",
        # Binding the bool call must not turn the FLOAT fallback into proof.
        id="bound_call",
    ),
    pytest.param(
        "flag = ta.crossover(close, open)\n", "flag[1]",
        # History of a bool call is still a bool condition.
        id="bound_call_history",
    ),
    pytest.param(
        "crossed() => ta.crossover(close, open)\n", "crossed()",
        # The helper returns the bool call rather than its inferred FLOAT.
        id="helper_call",
    ),
]


@pytest.mark.parametrize("setup, condition", _BOOL_CASES)
def test_bool_conditions_translate_and_compile(setup: str, condition: str) -> None:
    source = (
        '//@version=6\nstrategy("Bool condition")\n'
        + setup
        + f'if {condition}\n    strategy.entry("L", strategy.long)\n'
    )
    compile_cpp(transpile(source), label="bool_condition")


def test_bool_calls_compose_with_comparisons_logic_and_history() -> None:
    source = """//@version=6
strategy("Composed bool conditions")
compare(float lhs, float rhs) => lhs > rhs
if ta.crossover(close, open)
    strategy.entry("L", strategy.long)
flag = close > open
if flag
    strategy.close("L")
a = close > low
b = high > close
if a and b
    strategy.entry("L", strategy.long)
if a or b
    strategy.close("L")
x = close < open
if not x
    strategy.entry("L", strategy.long)
if compare(close, open)
    strategy.close("L")
if barstate.islast
    strategy.close("L")
values = array.from(close)
if array.size(values) > 0
    strategy.entry("L", strategy.long)
if flag[1]
    strategy.close("L")
"""
    # Every condition is bool; the first crossover exposed the fallback refusal.
    compile_cpp(transpile(source), label="composed_bool_conditions")


_NUMERIC_CASES = [
    pytest.param("open", "float", id="open"),  # The builtin table declares open as float.
    pytest.param("high", "float", id="high"),  # The builtin table declares high as float.
    pytest.param("low", "float", id="low"),  # The builtin table declares low as float.
    pytest.param("close", "float", id="close"),  # The builtin table declares close as float.
    pytest.param("volume", "float", id="volume"),  # The builtin table declares volume as float.
    pytest.param("hl2", "float", id="hl2"),  # The builtin table declares hl2 as float.
    pytest.param("hlc3", "float", id="hlc3"),  # The builtin table declares hlc3 as float.
    pytest.param("hlcc4", "float", id="hlcc4"),  # The builtin table declares hlcc4 as float.
    pytest.param("ohlc4", "float", id="ohlc4"),  # The builtin table declares ohlc4 as float.
    pytest.param("bar_index", "int", id="bar_index"),  # The builtin table declares bar_index as int.
    pytest.param("time", "int", id="time"),  # The builtin table declares time as int.
    pytest.param("time_close", "int", id="time_close"),  # The builtin table declares time_close as int.
    pytest.param("last_bar_index", "int", id="last_bar_index"),  # The final bar index is an int.
    pytest.param("last_bar_time", "int", id="last_bar_time"),  # The final bar timestamp is an int.
    pytest.param("timenow", "int", id="timenow"),  # The current timestamp is an int.
    pytest.param("time_tradingday", "int", id="time_tradingday"),  # The trading-day timestamp is an int.
    pytest.param("hour", "int", id="hour"),  # The builtin table declares hour as int.
    pytest.param("minute", "int", id="minute"),  # The builtin table declares minute as int.
    pytest.param("second", "int", id="second"),  # The builtin table declares second as int.
    pytest.param("dayofmonth", "int", id="dayofmonth"),  # The day of the month is an int.
    pytest.param("dayofweek", "int", id="dayofweek"),  # The bare day-of-week variable is an int.
    pytest.param("month", "int", id="month"),  # The builtin table declares month as int.
    pytest.param("year", "int", id="year"),  # The builtin table declares year as int.
    pytest.param("weekofyear", "int", id="weekofyear"),  # The week of the year is an int.
    pytest.param("1.0", "float", id="float_literal"),  # The decimal point makes this a float.
    pytest.param("1", "int", id="integer_literal"),  # The integer literal has int type.
    pytest.param("close + open", "float", id="addition"),  # Two proven floats add to a float.
    pytest.param("close - open", "float", id="subtraction"),  # Two proven floats subtract to a float.
    pytest.param("close * 2", "float", id="multiplication"),  # Float times int produces float.
    pytest.param("bar_index / 2", "float", id="division"),  # Division produces float in Pine v6.
    pytest.param("bar_index % 2", "int", id="remainder"),  # Remainder of two ints remains int.
    pytest.param("-close", "float", id="negation"),  # Unary minus preserves the float operand.
]


@pytest.mark.parametrize("condition, condition_type", _NUMERIC_CASES)
def test_numeric_refusal_follows_an_accepted_bool_call(
    condition: str, condition_type: str,
) -> None:
    source = (
        '//@version=6\nstrategy("Numeric condition after bool")\n'
        'if ta.rising(close, 2)\n    strategy.entry("L", strategy.long)\n'
        f'if {condition}\n    strategy.close("L")\n'
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2046"
    assert diagnostic.args == {"condition_type": condition_type}
    # The bool call on line 3 is accepted; the numeric condition begins at 5:4.
    assert (diagnostic.location.line, diagnostic.location.col) == (5, 4)


def test_single_numeric_binding_retains_the_refusal_after_a_bool_call() -> None:
    source = """//@version=6
strategy("Numeric binding after bool")
if ta.rising(close, 2)
    strategy.entry("L", strategy.long)
spread = close - open
quantity = -spread
if quantity
    strategy.close("L")
"""
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2046"
    assert diagnostic.args == {"condition_type": "float"}
    # Both bindings have proven float operands; quantity is the condition at 7:4.
    assert (diagnostic.location.line, diagnostic.location.col) == (7, 4)


def test_local_reassignment_does_not_hide_a_numeric_builtin() -> None:
    source = """//@version=6
strategy("Numeric builtin and local binding")
local_price() =>
    float close = 1.0
    close := 2.0
    close
if ta.rising(close, 2)
    strategy.entry("L", strategy.long)
if close
    strategy.close("L")
"""
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2046"
    assert diagnostic.args == {"condition_type": "float"}
    # The local close has another symbol; the builtin condition remains at 9:4.
    assert (diagnostic.location.line, diagnostic.location.col) == (9, 4)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "value = close\nvalue := open\n"
            'if value\n    strategy.entry("L", strategy.long)\n',
            # Reassignment excludes value from the single-binding proof.
            id="earlier_reassignment",
        ),
        pytest.param(
            'value = close\nif value\n    strategy.entry("L", strategy.long)\n'
            "value := open\n",
            # A later reassignment excludes the same binding before it is visited.
            id="later_reassignment",
        ),
        pytest.param(
            "value = close\nalias = value\nvalue := open\n"
            'if alias\n    strategy.entry("L", strategy.long)\n',
            # An alias cannot inherit numeric proof from a reassigned binding.
            id="alias_of_reassigned_binding",
        ),
    ],
)
def test_reassigned_bindings_keep_their_legacy_lowering(body: str) -> None:
    source = '//@version=6\nstrategy("Reassigned condition")\n' + body
    compile_cpp(transpile(source), label="reassigned_condition")
