"""A bool cannot be na at explicit bool value boundaries in Pine v6."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp
from tests._e2e import (
    Build, chart_feed_head, closed_trades, execute_all, ok, skip_unless_e2e_env,
)


HEADER = '//@version=6\nstrategy("Boolean values")\n'
MISSING_ELSE = """//@version=6
strategy("Boolean conditional return", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

flagFor(bool selected) =>
    if selected
        true

bool flag = flagFor(bar_index % 20 < 10)
units = flag ? 34 : 1
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
"""


@pytest.mark.parametrize(
    "source,line,col,end_col",
    [
        pytest.param(
            """//@version=6
strategy("Boolean reassignment", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

bool b = true
b := na
units = b ? 33 : 1
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
            5, 1, 8,  # b := na spans columns 1 through 7; end_col is exclusive.
            id="reassignment",
        ),
        pytest.param(
            """//@version=6
strategy("Untyped missing return", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

flagFor() =>
    bool t = true
    na

units = flagFor() ? 35 : 1
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
            6, 5, 7,  # The bool local is not returned; the untyped tail na is at 6:5.
            id="untyped-function-condition",
        ),
        pytest.param(
            """//@version=6
strategy("Boolean parameter default", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

pick(bool s = na) => s ? 36 : 1

units = pick()
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
            4, 15, 17,  # pick(bool s = occupies 14 columns before the default na.
            id="parameter-default",
        ),
        pytest.param(
            """//@version=6
strategy("Boolean argument", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

pick(bool s) => s ? 37 : 1

units = pick(na)
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
            6, 14, 16,  # units = pick( occupies 13 columns before the argument na.
            id="parameter-argument",
        ),
        pytest.param(
            """//@version=6
strategy("Boolean array initialization", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

array<bool> flags = array.new<bool>(3, na)
units = array.get(flags, 0) ? 38 : 1
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
            4, 40, 42,  # The generic array's initial value starts at column 40.
            id="array-initial-value",
        ),
        pytest.param(
            """//@version=6
strategy("Boolean input default", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

bool flag = input.bool(na, "Flag")
units = flag ? 39 : 1
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
            4, 24, 26,  # bool flag = input.bool( occupies 23 columns before na.
            id="input-default",
        ),
        pytest.param(
            HEADER + "ready = close > open\nif bar_index > 0\n"
            "    ready := na\nplot(ready ? 1 : 0)\n",
            5, 5, 16,  # Four spaces precede the inferred bool's 11-column assignment.
            id="indented-inferred-reassignment",
        ),
        pytest.param(
            HEADER + "choose() =>\n    bool ready = true\n"
            "    ready := na\n    ready\nx = choose()\nplot(x ? 1 : 0)\n",
            5, 5, 16,  # The local assignment starts at column 5 and ends after column 15.
            id="local-reassignment",
        ),
        pytest.param(
            HEADER + "method clear(bool self) =>\n    self := na\n    self\n"
            "b = close > open\nx = b.clear()\nplot(x ? 1 : 0)\n",
            4, 5, 15,  # The bool receiver's 10-column assignment follows four spaces.
            id="method-reassignment",
        ),
        pytest.param(
            HEADER + "var bool enabled = true\nif close > open\n"
            "    enabled := na\nplot(enabled ? 1 : 0)\n",
            5, 5, 18,  # The persistent bool's 13-column assignment follows four spaces.
            id="persistent-reassignment",
        ),
        pytest.param(
            HEADER + "varip bool enabled = true\nif close > open\n"
            "    enabled := na\nplot(enabled ? 1 : 0)\n",
            5, 5, 18,  # varip changes storage, leaving the same assignment span.
            id="intrabar-persistent-reassignment",
        ),
        pytest.param(
            HEADER + "empty() => na\nx = empty() ? 1 : 0\nplot(x)\n",
            3, 12, 14,  # empty() => occupies 11 columns before the untyped return na.
            id="single-expression-return",
        ),
        pytest.param(
            HEADER + "method empty(float self) =>\n    bool present = true\n    na\n"
            "v = close\nx = v.empty() ? 1 : 0\nplot(x)\n",
            5, 5, 7,  # The method's untyped na tail follows four spaces.
            id="method-untyped-return",
        ),
        pytest.param(
            HEADER + "empty(bool c) =>\n    if c\n        na\n"
            "    else\n        na\nx = empty(true) ? 1 : 0\nplot(x)\n",
            5, 9, 11,  # Both return paths are untyped na; the first starts at column 9.
            id="only-untyped-return-paths",
        ),
        pytest.param(
            HEADER + "choose(bool s = na) =>\n    s\nx = choose()\nplot(x ? 1 : 0)\n",
            3, 17, 19,  # choose(bool s = occupies 16 columns before the default na.
            id="indented-function-default",
        ),
        pytest.param(
            HEADER + "choose(simple bool s = na) => s\nx = choose()\nplot(x ? 1 : 0)\n",
            3, 24, 26,  # The simple qualifier adds seven columns before the default na.
            id="qualified-parameter-default",
        ),
        pytest.param(
            HEADER + "method choose(float self, bool enabled = na) => enabled\n"
            "v = close\nx = v.choose()\nplot(x ? 1 : 0)\n",
            3, 42, 44,  # The method's second parameter default begins at column 42.
            id="method-default",
        ),
        pytest.param(
            HEADER + "choose(float value, bool enabled) => enabled\n"
            "x = choose(enabled = na, value = 1.0)\nplot(x ? 1 : 0)\n",
            4, 22, 24,  # Keyword binding puts the na at column 22 into the bool slot.
            id="keyword-argument",
        ),
        pytest.param(
            HEADER + "choose(bool enabled) => enabled\nouter() =>\n"
            "    choose(na)\nx = outer()\nplot(x ? 1 : 0)\n",
            5, 12, 14,  # Four spaces plus choose( put the argument na at column 12.
            id="indented-argument",
        ),
        pytest.param(
            HEADER + "method choose(float self, bool enabled) => enabled\n"
            "v = close\nx = v.choose(na)\nplot(x ? 1 : 0)\n",
            5, 14, 16,  # x = v.choose( occupies 13 columns before the bool argument na.
            id="method-argument",
        ),
        pytest.param(
            HEADER + "a = array.new<bool>(initial_value = na, size = 3)\n"
            "plot(array.get(a, 0) ? 1 : 0)\n",
            3, 37, 39,  # The initial_value keyword's na starts at column 37.
            id="array-keyword-initial-value",
        ),
        pytest.param(
            HEADER + "make() =>\n    array.new<bool>(3, na)\na = make()\n"
            "plot(array.get(a, 0) ? 1 : 0)\n",
            4, 24, 26,  # The indented generic constructor's second argument starts at 24.
            id="indented-array-initial-value",
        ),
        pytest.param(
            HEADER + 'b = input.bool(defval = na, title = "Flag")\nplot(b ? 1 : 0)\n',
            3, 25, 27,  # b = input.bool(defval = occupies 24 columns before na.
            id="input-keyword-default",
        ),
        pytest.param(
            HEADER + 'make() =>\n    input.bool(na, "Flag")\nb = make()\n'
            "plot(b ? 1 : 0)\n",
            4, 16, 18,  # Four spaces plus input.bool( put the default na at column 16.
            id="indented-input-default",
        ),
    ],
)
def test_explicit_bool_values_refuse_bare_na(
    source: str, line: int, col: int, end_col: int,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="boolean-values.pine")
    # Each source has one prohibited bool value and therefore one analysis error.
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2044"
    assert diagnostic.args == {}
    assert diagnostic.message == "Invalid bool value: a bool cannot be na in Pine v6."
    assert diagnostic.location.file == "boolean-values.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    assert diagnostic.location.end_col == end_col


def test_bare_na_true_arm_of_bool_ternary_is_located() -> None:
    source = """//@version=6
strategy("Boolean true arm", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

enabledFor(bool selected) => selected ? na : true

enabled = enabledFor(bar_index % 20 < 10)
units = enabled ? 40 : 1
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
"""
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="boolean-values.pine")
    # enabledFor(bool selected) => selected ? occupies 40 columns before na.
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2043"
    assert (diagnostic.location.line, diagnostic.location.col) == (4, 41)
    assert diagnostic.location.end_col == 43


@pytest.mark.parametrize(
    "source",
    [
        # A missing else retains the bool type supplied by the true branch.
        pytest.param(MISSING_ELSE, id="missing-else"),
        # A concrete bool branch also types an explicitly missing opposite branch.
        pytest.param(
            HEADER + "choose(bool c) =>\n    if c\n        true\n"
            "    else\n        na\nx = choose(close > open)\nplot(x ? 1 : 0)\n",
            id="bool-and-na-return-paths",
        ),
        # Reversing the branches still provides a concrete bool return type.
        pytest.param(
            HEADER + "choose(bool c) =>\n    if c\n        na\n"
            "    else\n        true\nx = choose(close > open)\nplot(x ? 1 : 0)\n",
            id="na-and-bool-return-paths",
        ),
        # A bool method tail with an implicit missing else retains its type.
        pytest.param(
            HEADER + "method choose(bool self) =>\n    if self\n        true\n"
            "b = close > open\nx = b.choose()\nplot(x ? 1 : 0)\n",
            id="method-missing-else",
        ),
        # A float parameter permits both a missing default and a bare na argument.
        pytest.param(
            HEADER + "choose(float v = na) => v\nx = choose()\ny = choose(na)\n"
            "plot(na(x) and na(y) ? 1 : 0)\n",
            id="float-default-and-argument",
        ),
        # An int parameter permits both a missing default and a bare na argument.
        pytest.param(
            HEADER + "choose(int v = na) => v\nx = choose()\ny = choose(na)\n"
            "plot(na(x) and na(y) ? 1 : 0)\n",
            id="int-default-and-argument",
        ),
        # A string parameter permits both a missing default and a bare na argument.
        pytest.param(
            HEADER + "choose(string v = na) => v\nx = choose()\ny = choose(na)\n"
            "plot(na(x) and na(y) ? 1 : 0)\n",
            id="string-default-and-argument",
        ),
        # Non-bool method defaults and arguments keep the same binding path.
        pytest.param(
            HEADER + "method choose(float self, int v = na) => v\n"
            "v = close\nx = v.choose()\ny = v.choose(na)\n"
            "plot(na(x) and na(y) ? 1 : 0)\n",
            id="method-int-default-and-argument",
        ),
        # A float local supplies a concrete return type despite its missing value.
        pytest.param(
            HEADER + "choose() =>\n    float v = na\n    v\n"
            "x = choose() ? 1 : 0\nplot(x)\n",
            id="typed-nullable-return",
        ),
        # A typed declaration tail remains typed rather than a bare na return.
        pytest.param(
            HEADER + "method choose(float self) =>\n    float v = na\n"
            "v = close\nx = v.choose() ? 1 : 0\nplot(x)\n",
            id="typed-method-declaration-return",
        ),
        # An untyped missing return outside a bool condition keeps its lowering.
        pytest.param(
            HEADER + "empty() => na\nx = empty()\nplot(nz(x))\n",
            id="untyped-return-outside-condition",
        ),
        # An explicit bool cast remains outside the bare-na rule.
        pytest.param(
            HEADER + "choose() => bool(na)\nx = choose() ? 1 : 0\nplot(x)\n",
            id="bool-cast-return",
        ),
        # A bool parameter can have false as its default and true as its argument.
        pytest.param(
            HEADER + "choose(bool v = false) => v\nx = choose()\ny = choose(true)\n"
            "plot(x or y ? 1 : 0)\n",
            id="bool-default-and-argument",
        ),
        # A nullable float array keeps its explicit missing initial value.
        pytest.param(
            HEADER + "a = array.new<float>(3, na)\nplot(nz(array.get(a, 0)))\n",
            id="float-array-initial-value",
        ),
        # A nullable int array keeps its explicit missing initial value.
        pytest.param(
            HEADER + "a = array.new<int>(3, na)\nplot(nz(array.get(a, 0)))\n",
            id="int-array-initial-value",
        ),
        # A nullable string array keeps its explicit missing initial value.
        pytest.param(
            HEADER + "a = array.new<string>(3, na)\n"
            "plot(na(array.get(a, 0)) ? 0 : str.length(array.get(a, 0)))\n",
            id="string-array-initial-value",
        ),
        # Omitted bool array initialization retains its existing false default.
        pytest.param(
            HEADER + "a = array.new<bool>(3)\nplot(array.get(a, 0) ? 1 : 0)\n",
            id="bool-array-implicit-initial-value",
        ),
        # Explicit false remains a valid bool array initial value.
        pytest.param(
            HEADER + "a = array.new<bool>(3, false)\nplot(array.get(a, 0) ? 1 : 0)\n",
            id="bool-array-false",
        ),
        # The specialized bool array spelling keeps its existing lowering.
        pytest.param(
            HEADER + "a = array.new_bool(3, na)\nplot(array.get(a, 0) ? 1 : 0)\n",
            id="specialized-bool-array",
        ),
        # A bool matrix initializer retains its existing lowering.
        pytest.param(
            HEADER + "m = matrix.new<bool>(1, 1, na)\nplot(m.get(0, 0) ? 1 : 0)\n",
            id="bool-matrix-initial-value",
        ),
        # A false default remains a valid input.bool value.
        pytest.param(
            HEADER + 'b = input.bool(false, "Flag")\nplot(b ? 1 : 0)\n',
            id="bool-input-false",
        ),
        # A constructor argument for a bool field retains its existing behavior.
        pytest.param(
            HEADER + "type Box\n    bool flag\nb = Box.new(na)\nplot(b.flag ? 1 : 0)\n",
            id="bool-field-constructor",
        ),
        # A user type's bool field default is outside parameter declarations.
        pytest.param(
            HEADER + "type Box\n    bool flag = na\nb = Box.new()\nplot(b.flag ? 1 : 0)\n",
            id="bool-field-default",
        ),
        # A call to na on a bool stays outside every bare-na boundary check.
        pytest.param(
            HEADER + "bool b = false\nx = na(b)\nplot(x ? 1 : 0)\n",
            id="na-call-on-bool",
        ),
        # A call to nz on a bool keeps its existing lowering.
        pytest.param(
            HEADER + "bool b = false\nx = nz(b)\nplot(x ? 1 : 0)\n",
            id="nz-call-on-bool",
        ),
        # A bare na condition keeps its existing numeric truth conversion.
        pytest.param(
            HEADER + "x = na ? 1 : 0\nplot(x)\n",
            id="bare-na-condition",
        ),
    ],
)
def test_other_missing_values_still_compile(
    source: str, request: pytest.FixtureRequest,
) -> None:
    cpp = transpile(source)
    compile_cpp(cpp, label=request.node.name)


@pytest.mark.parametrize(
    "library_body,call",
    [
        # A v5 function keeps both its nullable bool default and explicit argument.
        pytest.param(
            "export choose(bool enabled = na) => enabled\n", "values.choose(na)",
            id="v5-default-and-argument",
        ),
        # A v5 local bool keeps its nullable reassignment and returned value.
        pytest.param(
            "export choose(bool enabled) =>\n    bool value = true\n"
            "    value := na\n    value\n", "values.choose(true)",
            id="v5-reassignment",
        ),
    ],
)
def test_imported_v5_bool_values_keep_their_lowering(
    library_body: str, call: str, request: pytest.FixtureRequest,
) -> None:
    library_source = '//@version=5\nlibrary("BooleanValues")\n' + library_body
    source = (
        HEADER + "import examples/BooleanValues/1 as values\n"
        f"x = {call}\nplot(x ? 1 : 0)\n"
    )
    cpp = transpile(source, libraries={"examples/BooleanValues/1": library_source})
    compile_cpp(cpp, label=request.node.name)


def test_missing_else_bool_controls_values_and_entry_quantity(tmp_path) -> None:
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 40)
    source = MISSING_ELSE + "// @pf-trace flag=flag\n// @pf-trace units=units\n"
    outcome = ok(execute_all(engine, feed, tmp_path, {
        "conditional": Build(source, trace=True),
    }), "conditional")
    values = {
        (record["bar_index"], record["name"]): record["value"]
        for record in outcome.traces["default"]
    }
    expected = [
        (0, 1.0, 34.0),   # 0 % 20 < 10, so the returned true selects 34.
        (5, 1.0, 34.0),   # The first entry bar is in the true half of the cycle.
        (9, 1.0, 34.0),   # The final bar before the missing-else path is still true.
        (10, 0.0, 1.0),   # At 10 the missing else becomes false and selects 1.
        (15, 0.0, 1.0),   # The first exit bar is in the missing-else half.
        (19, 0.0, 1.0),   # The final bar in the first cycle still has no true arm.
        (20, 1.0, 34.0),  # The second cycle resets the remainder to zero.
        (25, 1.0, 34.0),  # The second entry bar is in the true half of the cycle.
        (29, 1.0, 34.0),  # Remainder 9 still reaches the bool-valued arm.
        (30, 0.0, 1.0),   # Remainder 10 again takes the implicit missing value.
        (35, 0.0, 1.0),   # The second exit bar also selects 1.
        (39, 0.0, 1.0),   # Remainder 19 finishes in the missing-else half.
    ]
    for bar, flag, units in expected:
        assert values[(bar, "flag")] == flag
        assert values[(bar, "units")] == units
    trades = closed_trades(engine, tmp_path / "conditional", feed)
    # Entries at 5 and 25 both select 34; exits at 15 and 35 close both trades.
    assert [trade["qty"] for trade in trades] == [34.0, 34.0]
