"""Pine v6 refuses bare na in bool declarations, values and ternary arms."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp


HEADER = '//@version=6\nstrategy("Boolean values")\n'


@pytest.mark.parametrize(
    "source,line,col",
    [
        pytest.param(
            """//@version=6
strategy("Boolean selection", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

enabledFor(bool selected) => selected ? true : na

enabled = enabledFor(bar_index % 20 < 10)
units = enabled ? 33 : 1
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
            4, 48,  # The single-line function's false arm starts at column 48.
            id="function-result",
        ),
        pytest.param(
            """//@version=6
strategy("Typed boolean selection", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

enabledFor(bool selected) => selected ? true : na

bool enabled = enabledFor(bar_index % 20 < 10)
units = enabled ? 33 : 1
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
            4, 48,  # A typed use leaves the error at the function's na token.
            id="typed-function-result",
        ),
        pytest.param(
            HEADER + "choose(bool c) => c ? na : true\nx = choose(true)\nplot(close)\n",
            3, 23,  # The reversed single-line function's true arm starts at column 23.
            id="reversed-function-arms",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? true : na\nplot(close)\n",
            4, 16,  # The global true/na selection's na token starts at column 16.
            id="global-true-arm",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? false : na\nplot(close)\n",
            4, 17,  # The extra letter in false moves the na token to column 17.
            id="global-false-arm",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? na : false\nplot(close)\n",
            4, 9,  # The global reversed selection's na token starts at column 9.
            id="global-reversed-arms",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? false : (c ? na : true)\nplot(close)\n",
            4, 22,  # The inner selection's na token follows the second question mark.
            id="nested-na-arm",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? na : (c ? true : false)\nplot(close)\n",
            4, 9,  # The outer na token is paired with an inner bool selection.
            id="nested-bool-arm",
        ),
        pytest.param(
            HEADER + "choose(bool c) =>\n    c ? true : na\nx = choose(true)\nplot(close)\n",
            4, 16,  # Four spaces put the indented function's na token at column 16.
            id="indented-function",
        ),
        pytest.param(
            HEADER + "method choose(bool self) =>\n    self ? true : na\n"
            "c = close > open\nx = c.choose()\nplot(close)\n",
            4, 19,  # Four spaces and self put the method's na token at column 19.
            id="method-body",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? close > open : na\nplot(close)\n",
            4, 24,  # The comparison is bool and the following na starts at column 24.
            id="comparison-arm",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? na : close > open\nplot(close)\n",
            4, 9,  # Reversing the comparison leaves na at the true arm's column 9.
            id="reversed-comparison-arm",
        ),
        pytest.param(
            HEADER + "c = close > open\nflag = close < open\nx = c ? flag : na\nplot(close)\n",
            5, 16,  # flag resolves to bool; the next line's na starts at column 16.
            id="bool-variable-arm",
        ),
        pytest.param(
            HEADER + "isUp() => close > open\nc = close > open\n"
            "x = c ? isUp() : na\nplot(close)\n",
            5, 18,  # isUp() returns bool; its peer na starts at column 18.
            id="bool-function-call-arm",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? na(close) : na\nplot(close)\n",
            4, 21,  # na(close) returns bool; only the bare na at column 21 is invalid.
            id="bool-builtin-call-arm",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? bool(close) : na\nplot(close)\n",
            4, 23,  # The explicit bool cast's peer na starts at column 23.
            id="bool-cast-arm",
        ),
        pytest.param(
            HEADER + "c = close > open\nx = c ? na : bool(close)\nplot(close)\n",
            4, 9,  # The reversed explicit bool cast leaves na at column 9.
            id="reversed-bool-cast-arm",
        ),
        pytest.param(
            HEADER + "choose(bool c) =>\n    values = array.from(true)\n"
            "    c ? values.shift() : na\nx = choose(true)\nplot(close)\n",
            5, 26,  # array<bool>.shift() returns bool; its bare peer starts at column 26.
            id="bool-array-element-arm",
        ),
        pytest.param(
            HEADER + "choose(value) => close > open ? value : na\n"
            "x = choose(true)\nplot(close)\n",
            3, 41,  # The bool argument types value at its call; na starts at column 41.
            id="inferred-bool-parameter",
        ),
        pytest.param(
            HEADER + "choose(value) => close > open ? na : value\n"
            "x = choose(false)\nplot(close)\n",
            3, 33,  # The reversed selection's na token starts at column 33.
            id="reversed-inferred-bool-parameter",
        ),
    ],
)
def test_bool_ternary_refuses_bare_na(source: str, line: int, col: int) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="boolean.pine")
    # One invalid bool/na pair produces one located analysis error.
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2043"
    assert diagnostic.args == {}
    assert diagnostic.message == "Invalid ternary operator ?: a bool cannot be na in Pine v6."
    assert diagnostic.location.file == "boolean.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize(
    "source,line,col,end_col",
    [
        pytest.param(
            """//@version=6
strategy("Boolean initialization", overlay = true, initial_capital = 1000000000, process_orders_on_close = true)

bool b = na
units = b ? 33 : 1
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = units)
if bar_index % 20 == 15
    strategy.close("L")
""",
            4, 1, 12,  # bool b = na spans columns 1 through 11; end_col is exclusive.
            id="plain-declaration",
        ),
        pytest.param(
            HEADER + "var bool b = na\nplot(close)\n",
            3, 1, 16,  # var bool b = na spans columns 1 through 15.
            id="var-declaration",
        ),
        pytest.param(
            HEADER + "varip bool b = na\nplot(close)\n",
            3, 1, 18,  # varip bool b = na spans columns 1 through 17.
            id="varip-declaration",
        ),
        pytest.param(
            HEADER + "choose() =>\n    bool b = na\n    b\nx = choose()\nplot(close)\n",
            4, 5, 16,  # Four spaces put bool b = na at columns 5 through 15.
            id="function-declaration",
        ),
        pytest.param(
            HEADER + "if close > open\n    bool b = na\n"
            '    if b\n        strategy.entry("L", strategy.long)\n',
            4, 5, 16,  # The branch's bool b = na also spans columns 5 through 15.
            id="block-declaration",
        ),
    ],
)
def test_bool_declaration_refuses_bare_na(
    source: str, line: int, col: int, end_col: int,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="boolean.pine")
    # A bare na initializer produces one error at the declaration's start.
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2042"
    assert diagnostic.args == {}
    assert diagnostic.message == "Invalid bool declaration: a bool cannot be na in Pine v6."
    assert diagnostic.location.file == "boolean.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    assert diagnostic.location.end_col == end_col


@pytest.mark.parametrize(
    "body,line,col,end_col",
    [
        pytest.param(
            "bool b = false\nb := na\nplot(b ? 1 : 0)\n",
            4, 1, 8,  # b := na spans columns 1 through 7 on the second body line.
            id="bool-reassignment",
        ),
        pytest.param(
            "choose(bool b = na) => b\nx = choose()\nplot(x ? 1 : 0)\n",
            3, 17, 19,  # The parameter's na default spans columns 17 through 18.
            id="bool-parameter-default",
        ),
        pytest.param(
            'map<string, bool> m = map.new<string, bool>()\nmap.put(m, "k", na)\n'
            "plot(map.get(m, \"k\") ? 1 : 0)\n",
            4, 17, 19,  # The value argument of map.put spans columns 17 through 18.
            id="bool-map-put-namespace",
        ),
        pytest.param(
            'm = map.new<string, bool>()\nm.put("k", na)\nplot(m.get("k") ? 1 : 0)\n',
            4, 12, 14,  # The value argument of m.put spans columns 12 through 13.
            id="bool-map-put-method",
        ),
        pytest.param(
            'map<string, bool> m = map.new<string, bool>()\nmap.put(m, "k", value = na)\n'
            "plot(map.get(m, \"k\") ? 1 : 0)\n",
            4, 25, 27,  # The keyword value spans columns 25 through 26.
            id="bool-map-put-keyword-value",
        ),
    ],
)
def test_bool_value_refuses_bare_na(body: str, line: int, col: int, end_col: int) -> None:
    # A bool cannot be na in Pine v6, including assignments and parameter defaults.
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body, filename="boolean.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2044"
    assert diagnostic.args == {}
    assert diagnostic.message == "Invalid bool value: a bool cannot be na in Pine v6."
    assert diagnostic.location.file == "boolean.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    assert diagnostic.location.end_col == end_col


@pytest.mark.parametrize(
    "body",
    [
        # A string arm gives bare na its nullable string type.
        pytest.param(
            'choose(bool c) => c ? "up" : na\ns = choose(close > open)\n'
            "plot(na(s) ? 0 : str.length(s))\n",
            id="string-na",
        ),
        # An explicit string cast is a string expression rather than a bare na arm.
        pytest.param(
            'c = close > open\ns = c ? "up" : string(na)\n'
            "plot(na(s) ? 0 : str.length(s))\n",
            id="string-cast-na",
        ),
        # A typed string variable supplies the other arm's nullable string type.
        pytest.param(
            'string value = "up"\nc = close > open\ns = c ? value : na\n'
            "plot(na(s) ? 0 : str.length(s))\n",
            id="typed-value-first",
        ),
        # A bare na before an int still joins to int.
        pytest.param(
            "choose(bool c) => c ? na : 7\nx = choose(close > open)\nplot(nz(x))\n",
            id="na-first-int",
        ),
        # An explicitly typed int value also supplies the nullable int type.
        pytest.param(
            "int value = 7\nc = close > open\nx = c ? value : na\nplot(nz(x))\n",
            id="typed-int-first",
        ),
        # An untyped parameter called with an int retains its int/na selection.
        pytest.param(
            "choose(value) => close > open ? value : na\n"
            "x = choose(7)\nplot(nz(x))\n",
            id="inferred-int-parameter",
        ),
        # A float arm still gives bare na its float type.
        pytest.param(
            "c = close > open\nx = c ? 1.5 : na\nplot(nz(x))\n",
            id="float-na",
        ),
        # A nullable user handle can be read after its na guard.
        pytest.param(
            "type Sample\n    float value\nc = close > open\n"
            "Sample x = c ? Sample.new(close) : na\n"
            'if not na(x)\n    strategy.entry("L", strategy.long, qty = x.value)\n',
            id="guarded-user-type",
        ),
        # An explicit float declaration permits a bare na initializer.
        pytest.param("float x = na\nplot(nz(x))\n", id="float-declaration"),
        # An explicit int declaration permits a bare na initializer.
        pytest.param("int y = na\nplot(nz(y))\n", id="int-declaration"),
        # An explicit string declaration permits a bare na initializer.
        pytest.param("string s = na\nplot(na(s) ? 0 : str.length(s))\n", id="string-declaration"),
        # A color remains nullable in declarations and selections.
        pytest.param(
            "color x = na\nc = close > open\nx := c ? color.red : na\n"
            "plot(na(x) ? 0 : 1)\n",
            id="color-na",
        ),
        # A false initializer is an ordinary bool value.
        pytest.param("bool b = false\nplot(b ? 1 : 0)\n", id="bool-false"),
        # Two bool arms infer bool without any bare na.
        pytest.param(
            "cond = close > open\nb = cond ? true : false\nplot(b ? 1 : 0)\n",
            id="inferred-bool-selection",
        ),
        # Two bool arms also fit an explicit bool declaration.
        pytest.param(
            "cond = close > open\nbool b = cond ? true : false\nplot(b ? 1 : 0)\n",
            id="typed-bool-selection",
        ),
        # na(close) is a bool call, so pairing it with false has two bool arms.
        pytest.param(
            "cond = close > open\nbool b = cond ? na(close) : false\nplot(b ? 1 : 0)\n",
            id="na-call-bool-arm",
        ),
        # float(na) is a cast expression, outside the bare-na ternary rule.
        pytest.param(
            "cond = close > open\nx = cond ? true : float(na)\nplot(nz(x))\n",
            id="cast-float-arm",
        ),
        # bool(na) remains an explicit cast rather than a bare initializer.
        pytest.param("bool b = bool(na)\nplot(b ? 1 : 0)\n", id="bool-cast-initializer"),
        # A bool map takes a bool value, and a float map still takes na.
        pytest.param(
            'map<string, bool> m = map.new<string, bool>()\nmap.put(m, "k", true)\n'
            'plot(map.get(m, "k") ? 1 : 0)\n',
            id="bool-map-put-bool",
        ),
        pytest.param(
            'map<string, float> m = map.new<string, float>()\nmap.put(m, "k", na)\n'
            'plot(nz(map.get(m, "k")))\n',
            id="float-map-put-na",
        ),
        # Testing a bool with na() stays outside the two refusal rules.
        pytest.param("bool b = false\nx = na(b)\nplot(x ? 1 : 0)\n", id="na-of-bool"),
        # Replacing a bool with nz() stays outside the two refusal rules.
        pytest.param("bool b = false\nx = nz(b)\nplot(x ? 1 : 0)\n", id="nz-of-bool"),
    ],
)
def test_other_na_and_bool_shapes_compile(body: str, request: pytest.FixtureRequest) -> None:
    cpp = transpile(HEADER + body)
    compile_cpp(cpp, label=request.node.name)


@pytest.mark.parametrize(
    "library_body",
    [
        # A v5 library's bool/na ternary retains its original version's lowering.
        pytest.param("export choose(bool c) => c ? true : na\n", id="v5-ternary"),
        # A local bool declaration in a v5 library still permits bare na.
        pytest.param(
            "export choose(bool c) =>\n    bool b = na\n    c ? b : false\n",
            id="v5-local-declaration",
        ),
        # A v5 global declaration keeps its version when inlined into the v6 script.
        pytest.param(
            "bool b = na\nexport choose(bool c) => c ? b : false\n",
            id="v5-global-declaration",
        ),
        # Call-site bool inference in a v5 library keeps the library's version.
        pytest.param(
            "pick(value) => close > open ? value : na\n"
            "export choose(bool c) => pick(c)\n",
            id="v5-inferred-bool-parameter",
        ),
    ],
)
def test_imported_v5_bool_na_keeps_its_lowering(
    library_body: str, request: pytest.FixtureRequest,
) -> None:
    library_source = '//@version=5\nlibrary("BooleanValues")\n' + library_body
    source = (
        HEADER + "import examples/BooleanValues/1 as values\n"
        "b = values.choose(close > open)\nplot(b ? 1 : 0)\n"
    )
    cpp = transpile(source, libraries={"examples/BooleanValues/1": library_source})
    compile_cpp(cpp, label=request.node.name)
