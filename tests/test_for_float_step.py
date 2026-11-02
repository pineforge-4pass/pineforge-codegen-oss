"""TradingView loop-step type boundaries and integer-loop controls."""

import hashlib
import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests import _compile as compile_env
from tests._e2e import reference_codegen, transpile_json
from tests._for_counter_header_cases import MAIN_COMMIT as LOOP_CPP_REFERENCE
from tests._for_float_step import (
    DIVISION_STARTS, DIVISION_VALUE_STARTS, DIVISION_VALUE_SUMS,
    assert_float_step_refused, loop_step_source,
)

# TradingView refuses this form.
COUNTER_DEPENDENT_STEP = """//@version=6
strategy("Counter-dependent float step", initial_capital = 1000000, currency = currency.NONE)
float acc = 0.0
for i = 0 to 4 by (i == 0 ? 1.0 : 2.0)
    acc := acc + i + 1
// 15 if TradingView accepts a float step for the int counter; a refusal otherwise
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = acc)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
"""

# TradingView refuses this form.
VARIABLE_STEP = """//@version=6
strategy("Loop step reading a variable the body changes", initial_capital = 1000000, currency = currency.NONE)
float stride = 1.0
float acc = 0.0
for i = 0 to 6 by stride
    stride := stride + 1.0
    acc := acc + 1.0
// acc is 7 if the step is read once before the loop (0,1,2,3,4,5,6), 3 if it is read again before every iteration (0,1,3,6)
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = acc)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
"""

# TradingView refuses a float literal step for an int counter.
FLOAT_LITERAL_STEP = """//@version=6
strategy("Float literal step for an int counter", initial_capital = 1000000, currency = currency.NONE)
float acc = 0.0
for i = 0 to 4 by 1.0
    acc := acc + i + 1
// 15 if TradingView accepts a float literal step for the int counter; a refusal otherwise
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = acc)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
"""

# TradingView accepts this form with quantity 15.
FLOAT_COUNTER_FLOAT_STEP = """//@version=6
strategy("Float start and float step", initial_capital = 1000000, currency = currency.NONE)
float acc = 0.0
for i = 0.0 to 4.0 by 1.0
    acc := acc + i + 1
// 15 if TradingView accepts a float counter with a float step; a refusal otherwise
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = acc)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
"""

# TradingView accepts this form with quantity 15.
INT_COUNTER_FLOAT_END = """//@version=6
strategy("Int start, float end, int step", initial_capital = 1000000, currency = currency.NONE)
float acc = 0.0
for i = 0 to 4.0 by 1
    acc := acc + i + 1
// 15 if TradingView accepts a float end bound for the int counter with an int step; a refusal otherwise
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = acc)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
"""

# TradingView accepts this form with quantity 5.
FRACTIONAL_FLOAT_STEP = """//@version=6
strategy("Fractional float step", initial_capital = 1000000, currency = currency.NONE)
float acc = 0.0
for x = 0.0 to 1.0 by 0.25
    acc := acc + 1.0
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = acc)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
"""

MEASURED_REFUSALS = (
    pytest.param(COUNTER_DEPENDENT_STEP, 4, 19, 39, id="counter-dependent"),
    pytest.param(VARIABLE_STEP, 5, 19, 25, id="variable"),
    pytest.param(FLOAT_LITERAL_STEP, 4, 19, 22, id="float-literal"),
)

MEASURED_ACCEPTANCES = (
    pytest.param(FLOAT_COUNTER_FLOAT_STEP, id="float-counter-float-step"),
    pytest.param(INT_COUNTER_FLOAT_END, id="int-counter-float-end"),
    pytest.param(FRACTIONAL_FLOAT_STEP, id="fractional-float-step"),
)


def _assert_step_diagnostic(source: str, line: int, col: int, end_col: int) -> None:
    assert_float_step_refused(source, location=(line, col, end_col))


@pytest.mark.parametrize("source,line,col,end_col", MEASURED_REFUSALS)
def test_float_step_is_refused_at_the_step_expression(
    source: str, line: int, col: int, end_col: int,
) -> None:
    _assert_step_diagnostic(source, line, col, end_col)


@pytest.mark.parametrize("source", MEASURED_ACCEPTANCES)
def test_accepted_step_types_do_not_raise_float_step_diagnostic(source: str) -> None:
    assert transpile(source)


def _in_helper(source: str, requested: bool) -> str:
    lines = source.splitlines()
    # Keep the declaration and loop body together in the callable's scope.
    body = lines[2:lines.index("if bar_index > 6 and bar_index % 7 == 3")]
    result = 'request.security(syminfo.tickerid, "60", walk())' if requested else "walk()"
    return (
        "\n".join(lines[:2]) + "\nwalk() =>\n"
        + "\n".join("    " + line for line in body) + "\n    acc\n"
        + f"plot({result})\n"
    )


@pytest.mark.parametrize("requested", [False, True], ids=["function", "security"])
@pytest.mark.parametrize("source,line,col,end_col", MEASURED_REFUSALS)
def test_float_step_is_refused_in_helper_bodies(
    source: str, line: int, col: int, end_col: int, requested: bool,
) -> None:
    _assert_step_diagnostic(_in_helper(source, requested), line + 1, col + 4, end_col + 4)


def _script(body: str, version: int = 6) -> str:
    return (
        f"//@version={version}\n"
        'strategy("Loop step types", currency=currency.NONE)\n' + body
    )


INTEGER_LOOPS = (
    pytest.param(_script(
        "float acc = 0.0\n"
        "for i = 0 to 4 by 1\n"
        "    acc += i + 1\n"
        "plot(acc)\n"
    ), "1", id="int-literal"),
    pytest.param(_script(
        "int stride = 1\n"
        "float acc = 0.0\n"
        "for i = 0 to 6 by stride\n"
        "    stride += 1\n"
        "    acc += i + 1\n"
        "plot(acc)\n"
    ), "stride", id="body-modified-int-variable"),
    pytest.param(_script(
        "float acc = 0.0\n"
        "for i = 0 to 4 by (i == 0 ? 1 : 2)\n"
        "    acc += i + 1\n"
        "plot(acc)\n"
    ), "(i == 0 ? 1 : 2)", id="counter-dependent-int"),
    pytest.param(_script(
        "float acc = 0.0\n"
        "for i = 0 to 4\n"
        "    acc += i + 1\n"
        "plot(acc)\n"
    ), None, id="implicit-step"),
    pytest.param(_script(
        "walk(stride) =>\n"
        "    float acc = 0.0\n"
        "    for i = 0 to 4 by stride\n"
        "        acc += i + 1\n"
        "    acc\n"
        "plot(walk(1))\n"
    ), "stride", id="untyped-step-parameter"),
    pytest.param(_script(
        "stride = int(1.0)\n"
        "float acc = 0.0\n"
        "for i = 0 to 4 by stride\n"
        "    acc += i + 1\n"
        "plot(acc)\n"
    ), "stride", id="inferred-int-cast"),
    pytest.param(_script(
        "stride = math.min(1, 2)\n"
        "float acc = 0.0\n"
        "for i = 0 to 4 by stride\n"
        "    acc += i + 1\n"
        "plot(acc)\n"
    ), "stride", id="inferred-int-overload"),
)


def _assert_integer_loop_translates(source: str, step: str | None) -> None:
    result = transpile_full(source)
    assert result["cpp"]
    assert all(d.code != "PF-E2079" for d in result["diagnostics"])
    if step is not None:
        grouped_source = source.replace(f" by {step}\n", f" by ({step})\n")
        assert grouped_source != source
        grouped_result = transpile_full(grouped_source)
        assert all(d.code != "PF-E2079" for d in grouped_result["diagnostics"])
        assert result["cpp"] == grouped_result["cpp"]


@pytest.mark.parametrize("source,step", INTEGER_LOOPS)
def test_integer_loops_still_translate(
    source: str, step: str | None,
) -> None:
    _assert_integer_loop_translates(source, step)


@pytest.mark.parametrize("step", [
    "int(1.0)", "math.min(1, 2)", "math.max(1, 2)", "math.floor(1.5)",
    "math.round(1.5)", "nz(1)",
])
def test_integer_step_expressions_still_translate(step: str) -> None:
    source = _script(
        "float acc = 0.0\n"
        f"for i = 0 to 4 by {step}\n"
        "    acc += i + 1\n"
        "plot(acc)\n"
    )
    _assert_integer_loop_translates(source, step)


@pytest.mark.parametrize("prefix,start,double_counter", [
    ("", "0.0", True),
    ("", "float(0)", True),
    ("", "4 / 2.0", True),
    ("", "math.max(0, 0.0)", True),
    ("", "math.round(0, 2)", True),
    ("", "(true ? na : 0.0)", True),
    ("first = math.sqrt(0)\n", "first", True),
    ("float first = 0\n", "first", True),
    ("", "0", False),
    ("", "int(0.0)", False),
    ("", "math.max(0, 0)", False),
    ("", "math.round(0)", False),
    ("", "(true ? na : 0)", False),
    ("first = math.min(0, 1)\n", "first", False),
])
def test_float_step_refusal_uses_the_same_counter_family_as_emission(
    prefix, start, double_counter,
):
    # The literal int start with float end and step is measured in
    # tradingview_quantities.json: int_start_float_end_float_step.
    # The other start expressions are derived controls.
    source = _script(
        prefix + "float acc = 0.0\n"
        f"for i = {start} to 1.0 by 0.25\n"
        "    acc += 1\nplot(acc)\n"
    )
    if double_counter:
        cpp = transpile(source)
        assert re.search(r"\bfor \(double i = ", cpp)
    else:
        assert_float_step_refused(source)


@pytest.mark.parametrize("name", sorted(DIVISION_STARTS))
def test_int_division_start_still_refuses_a_float_step(name):
    prefix, start = DIVISION_STARTS[name]
    assert_float_step_refused(loop_step_source(start, prefix=prefix))


@pytest.mark.parametrize("name", sorted(DIVISION_STARTS))
def test_division_start_emits_a_float_counter_with_default_step(name):
    prefix, start = DIVISION_STARTS[name]
    cpp = transpile(loop_step_source(start, prefix=prefix, step=None))
    assert re.search(r"\bfor \(double i = ", cpp), cpp
    assert not re.search(r"\bfor \(int i = ", cpp), cpp
    for slot in ("start", "end", "step"):
        assert re.search(rf"\bdouble _for_{slot}_\d+ = ", cpp), cpp
    assert cpp.count("static constexpr double kFloatStepTolerance =") == 1
    assert cpp.count("::kFloatStepTolerance") == 2


@pytest.mark.parametrize("start,end,step", [
    pytest.param("0", "4", "1.0", id="int-literal"),
    pytest.param("1 + 1", "6", "0.5", id="int-addition"),
    pytest.param("4 - 2", "6", "0.5", id="int-subtraction"),
    pytest.param("2 * 1", "6", "0.5", id="int-multiplication"),
    pytest.param("7 % 4", "6", "0.5", id="int-remainder"),
])
def test_integer_start_arithmetic_still_refuses_a_float_step(start, end, step):
    assert_float_step_refused(loop_step_source(start, end=end, step=step))


@pytest.mark.parametrize("name", sorted(DIVISION_VALUE_STARTS))
def test_division_start_without_step_matches_a_float_start(name):
    prefix, start, end, twin, twin_end = DIVISION_VALUE_STARTS[name]
    sources = [
        loop_step_source(start, prefix=prefix, end=end, step=None),
        loop_step_source(twin, prefix=prefix, end=twin_end, step=None),
    ]
    normalized = []
    for source in sources:
        cpp = transpile(source)
        # Compare all C++ apart from the authored bound expressions that
        # differ. Header types, step, body and tolerance must match.
        slots = [("start", 1)]
        if end != twin_end:
            slots.append(("end", 2))
        for slot, count_expected in slots:
            cpp, count = re.subn(
                rf"(\b(?:double )?_for_{slot}_\d+ = )[^;\n]+(?=;|\) \{{)",
                rf"\1<{slot}>", cpp,
            )
            assert count == count_expected, cpp
        normalized.append(cpp)
    assert normalized[0] == normalized[1]


@pytest.mark.parametrize("start,end", [
    pytest.param("2", "6", id="int-literal"),
    pytest.param("1 + 1", "6", id="int-addition"),
    pytest.param("2 * 1", "6", id="int-multiplication"),
    pytest.param("7 % 4", "6", id="int-remainder"),
    pytest.param("0", "7 / 2", id="division-only-in-end"),
])
def test_integer_counter_keeps_frozen_cpp(start, end, tmp_path):
    source = loop_step_source(start, end=end, step=None)
    cpp = transpile(source)
    assert re.search(r"\bfor \(int i = ", cpp), cpp
    assert "kFloatStepTolerance" not in cpp
    # Use the loop inventory's immutable reference, without replacing any
    # stored hash or excluding any emitted text from the digest.
    reference = reference_codegen(LOOP_CPP_REFERENCE)
    assert reference is not None, "Required loop C++ reference unavailable; restore git history."
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before["diagnostics"]
    assert hashlib.sha256(cpp.encode("utf-8")).hexdigest() == hashlib.sha256(
        before["cpp"].encode("utf-8"),
    ).hexdigest()


@pytest.mark.parametrize("name", sorted(DIVISION_VALUE_STARTS))
def test_division_start_preserves_counter_values(name):
    prefix, start, end, _twin, _twin_end = DIVISION_VALUE_STARTS[name]
    expected = ",".join(str(value) for value in DIVISION_VALUE_SUMS[name])
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    Bar bars[] = {
        {10.0, 10.0, 10.0, 10.0, 1.0, 60000},
        {10.0, 10.0, 10.0, 10.0, 1.0, 120000},
    };
    GeneratedStrategy first_bar, second_bar;
    first_bar.run(bars, 1, "1", "1");
    second_bar.run(bars, 2, "1", "1");
    assert(first_bar.last_error().empty());
    assert(second_bar.last_error().empty());
    std::cout << first_bar.acc << "," << second_bar.acc;
}
'''
    assert compile_env.run_emitted_tu(
        transpile(loop_step_source(start, prefix=prefix, end=end, step=None)),
        driver, opt="-O2", label=name,
    ) == expected


@pytest.mark.parametrize("argument", ["0", "0.0", "4 / 2"])
def test_untyped_parameter_start_keeps_documented_call_argument_gap(argument):
    # TradingView refuses the measured int call and accepts the float call.
    # Shared inference leaves the parameter unknown even for a division
    # argument, so each call keeps the float-step route here.
    source = _script(
        "measure(first) =>\n"
        "    float acc = 0.0\n"
        "    for i = first to 1.0 by 0.25\n"
        "        acc += 1.0\n"
        "    acc\n"
        f"plot(measure({argument}))\n"
    )
    cpp = transpile(source)
    assert re.search(r"\bfor \(double i = ", cpp)


# A version-5 loop header does not see its own counter, so only the variable
# step (which does not read the counter) is a valid version-5 source.
@pytest.mark.parametrize("source", [VARIABLE_STEP])
def test_float_steps_in_v5_library_bodies_still_translate(source: str) -> None:
    helper = _in_helper(source, requested=False).splitlines()
    library = "\n".join([
        "//@version=5",
        'library("LoopSteps")',
        "export walk() =>",
        *helper[3:-1],
    ]) + "\n"
    caller = _script(
        "import examples/LoopSteps/1 as steps\n"
        "plot(steps.walk())\n"
    )
    result = transpile_full(caller, libraries={"examples/LoopSteps/1": library})
    assert result["cpp"]
    assert all(d.code != "PF-E2079" for d in result["diagnostics"])


@pytest.mark.parametrize("source", [COUNTER_DEPENDENT_STEP, VARIABLE_STEP])
def test_top_level_v5_keeps_its_existing_version_diagnostic(source: str) -> None:
    # Only library bodies accept v5; top-level scripts already require v6.
    with pytest.raises(CompileError) as caught:
        transpile(source.replace("//@version=6", "//@version=5"))
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code != "PF-E2079"
    assert diagnostic.message == "PineForge supports PineScript v6 only (found //@version=5)."


@pytest.mark.parametrize("tail", ["", "plot(acc)\n"], ids=["omitted", "stopped"])
@pytest.mark.parametrize("finite_loop_at", [None, "before", "after"])
def test_removed_float_loops_do_not_request_a_tolerance(tail, finite_loop_at):
    removed = (
        "float acc = 0.0\n"
        "for i = 0.0 to i by 0.1\n"
        "    acc += 1\n"
        + tail
    )
    finite = (
        "float count = 0.0\n"
        "for j = 0.0 to 0.3 by 0.1\n"
        "    count += 1\n"
        "plot(count)\n"
    )
    body = (
        finite + removed if finite_loop_at == "before"
        else removed + finite if finite_loop_at == "after"
        else removed
    )
    cpp = transpile(_script(body))
    assert not re.search(r"\bfor \(double i\b", cpp)
    expected_definitions = int(finite_loop_at is not None)
    assert cpp.count("static constexpr double kFloatStepTolerance =") == expected_definitions
    assert ("Loop takes too long" in cpp) == bool(tail)


def test_variable_step_does_not_scale_the_emitted_allowance():
    cpp = transpile(_script(
        'stride = input.float(0.1, "Stride")\n'
        'limit = input.float(0.3, "Limit")\n'
        "float acc = 0.0\n"
        "for x = 0.0 to limit by stride\n"
        "    acc += 1\n"
        "plot(acc)\n"
    ))
    loop = re.search(r"\bfor \(double x = \w+; (?P<condition>[^;]+);", cpp)
    assert loop is not None, cpp
    # Each allowance is only the constant: neither step nor end scales it.
    assert re.search(
        r"\(\w+ \? \(x >= \w+ - ::kFloatStepTolerance\)"
        r" : \(x <= \w+ \+ ::kFloatStepTolerance\)\)$",
        loop["condition"],
    ), loop["condition"]
