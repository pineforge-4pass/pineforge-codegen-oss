"""Statement conditions distinguish numeric TA results from bool overloads."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp, run_emitted_tu
from tests._e2e import reference_codegen, transpile_json
from tests.test_helper_builtin_named_locals import CPP_REFERENCE


FLOAT_CHANGE_SCRIPT = """//@version=6
strategy("t", overlay = true)
float t = ta.ema(close, 5)
if ta.change(t)
    label.new(bar_index, high, "x")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

POSITION_CHANGE_SCRIPT = """//@version=6
strategy("t", overlay = true)
if ta.change(strategy.position_size)
    label.new(bar_index, high, "x")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

BASE_SCRIPT = """//@version=6
strategy("t", overlay = true)
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 10), ta.ema(close, 30))
    strategy.close("L")
"""

_FLOAT_SETUP = "float t = ta.ema(close, 5)\n"
_BOOL_SETUP = "bool b = close > open\n"


@pytest.fixture(autouse=True)
def require_native_inputs(monkeypatch):
    # The native controls require compiler and runtime inputs.
    monkeypatch.setenv("PINEFORGE_REQUIRE_NATIVE", "1")


def _condition_source(condition, *, setup="", statement="if"):
    body = "break" if statement == "while" else 'strategy.entry("L", strategy.long)'
    return (
        '//@version=6\nstrategy("Condition types")\n'
        + setup + f"{statement} {condition}\n    {body}\n"
    )


def _assert_error(source, code, position, message, args):
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="condition.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == code
    assert diagnostic.message == message
    assert diagnostic.args == args
    assert diagnostic.location.file == "condition.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == position
    assert render_diagnostic(code, args) == (diagnostic.message, diagnostic.hint)


@pytest.mark.parametrize(
    "source, position, condition_type",
    [
        pytest.param(FLOAT_CHANGE_SCRIPT, (4, 4), "float", id="float_source_change"),
        pytest.param(POSITION_CHANGE_SCRIPT, (3, 4), "float", id="position_change"),
        pytest.param(
            _condition_source("-ta.change(t)", setup=_FLOAT_SETUP),
            (4, 4), "float", id="negated_change",
        ),
        pytest.param(
            _condition_source("ta.change(t) * 2", setup=_FLOAT_SETUP),
            (4, 4), "float", id="multiplied_change",
        ),
        pytest.param(
            _condition_source("ta.ema(close, 5)"),
            (3, 4), "float", id="fixed_float_result",
        ),
        pytest.param(
            _condition_source("strategy.position_size"),
            (3, 4), "float", id="float_member",
        ),
        pytest.param(
            _condition_source("strategy.closedtrades"),
            (3, 4), "int", id="int_member",
        ),
        pytest.param(
            _condition_source("ta.tr"),
            (3, 4), "float", id="numeric_ta_variable",
        ),
        pytest.param(
            _condition_source("ta.change(ta.change(t))", setup=_FLOAT_SETUP),
            (4, 4), "float", id="nested_numeric_source",
        ),
        pytest.param(
            _condition_source("ta.change(length = 2, source = t)", setup=_FLOAT_SETUP),
            (4, 4), "float", id="numeric_keyword_source",
        ),
        pytest.param(
            _condition_source("ta.valuewhen(b, close, 0)", setup=_BOOL_SETUP),
            (4, 4), "float", id="numeric_valuewhen",
        ),
        pytest.param(
            _condition_source("ta.highest(5)"),
            (3, 4), "float", id="length_only_overload",
        ),
        pytest.param(
            _condition_source("ta.highestbars(close, 5)"),
            (3, 4), "int", id="int_overloaded_result",
        ),
        pytest.param(
            _condition_source("ta.pivothigh(2, 2)"),
            (3, 4), "float", id="default_source_overload",
        ),
        pytest.param(
            _condition_source("ta.vwap(close)"),
            (3, 4), "float", id="scalar_vwap_overload",
        ),
    ],
)
def test_numeric_if_conditions_are_coded_and_located(source, position, condition_type):
    _assert_error(
        source, "PF-E2046", position,
        f"An if condition must have bool type; found {condition_type}.",
        {"condition_type": condition_type},
    )


@pytest.mark.parametrize(
    "condition, setup, position",
    [
        pytest.param("ta.change(t)", _FLOAT_SETUP, (4, 7), id="numeric_call"),
        pytest.param("strategy.position_size", "", (3, 7), id="numeric_member"),
    ],
)
def test_numeric_while_conditions_keep_the_existing_diagnostic(condition, setup, position):
    _assert_error(
        _condition_source(condition, setup=setup, statement="while"),
        "PF-E2062", position,
        'The condition of the "while" statement must evaluate to a "bool" value.',
        {},
    )


@pytest.mark.parametrize(
    "condition",
    [
        "ta.change(b)",
        "ta.change(b, 2)",
        "ta.change(length = 2, source = b)",
        "ta.change(ta.change(b))",
        "ta.crossover(close, open)",
        "ta.rising(close, 3)",
        "ta.change(t) != 0",
        "ta.change(t) > 0",
        "na(ta.change(t))",
        "ta.change(strategy.position_size) != 0",
        "b",
        "ta.valuewhen(b, b, 0)",
        "ta.valuewhen(occurrence = 0, source = b, condition = b)",
        "b ? ta.change(b) : ta.crossover(close, open)",
        "ta.change(b) and b",
        "ta.change(b) or b",
        "not ta.change(b)",
        "session.isfirstbar",
    ],
)
def test_bool_conditions_translate_and_compile(condition):
    source = _condition_source(condition, setup=_FLOAT_SETUP + _BOOL_SETUP)
    compile_cpp(transpile(source), label="bool_statement_condition")


def test_unknown_call_source_does_not_acquire_a_numeric_proof():
    source = _condition_source(
        "ta.change(isUp(close))",
        setup="isUp(float value) => value > open\n",
    )
    compile_cpp(transpile(source), label="unresolved_change_source")


def test_bool_call_is_accepted_as_a_while_condition():
    source = _condition_source(
        "ta.change(b)", setup=_BOOL_SETUP, statement="while",
    )
    compile_cpp(transpile(source), label="bool_while_condition")


def test_older_library_keeps_numeric_call_conditions():
    library = """//@version=5
library("Numeric conditions")
export changed(float value) =>
    if ta.change(value)
        1
    else
        0
"""
    source = """//@version=6
strategy("Imported numeric call")
import local/Numbers/1 as numbers
plot(numbers.changed(close))
"""
    cpp = transpile(source, libraries={"local/Numbers/1": library})
    compile_cpp(cpp, label="older_library_call_condition")


def test_bool_change_condition_executes_on_source_transitions():
    source = """//@version=6
strategy("Bool changes")
bool b = close > open
var int changes = 0
if ta.change(b)
    changes += 1
"""
    driver = r"""
#include <cstdio>
int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bars[] = {
        {100.0, 101.0, 98.0, 99.0, 1.0, 1743466500000LL},
        {100.0, 102.0, 99.0, 101.0, 1.0, 1743466560000LL},
        {100.0, 103.0, 99.0, 102.0, 1.0, 1743466620000LL},
        {100.0, 101.0, 97.0, 98.0, 1.0, 1743466680000LL},
    };
    strategy.run(bars, 4);
    std::printf("%lld\n", static_cast<long long>(strategy.changes));
}
"""
    output = run_emitted_tu(
        transpile(source), driver, opt="-O0", label="bool_change_condition",
    )
    assert output.strip() == "2"


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    assert tree is not None, "The pinned C++ reference commit is required"
    return tree


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(BASE_SCRIPT, id="unchanged_template"),
        pytest.param(
            _condition_source("ta.change(b)", setup=_BOOL_SETUP),
            id="unchanged_bool_overload",
        ),
        pytest.param(
            _condition_source("ta.change(t) != 0", setup=_FLOAT_SETUP),
            id="unchanged_comparison",
        ),
    ],
)
def test_accepted_scripts_keep_complete_cpp(source, reference, tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    assert transpile(source) == before["cpp"]
