"""Located diagnostics for invalid proven operator type combinations."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp


def _script(body: str) -> str:
    return '//@version=6\nstrategy("operator checks")\n' + body


def _assert_diagnostic(
    source: str,
    code: str,
    message: str,
    arguments: dict,
    line: int,
    col: int,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="operator_checks.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == code
    assert diagnostic.message == message
    assert diagnostic.args == arguments
    assert diagnostic.location.file == "operator_checks.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize(
    "source, line, col, operand_type",
    [
        (
            _script(
                "float x = close - open\n"
                "value = not x > 5\n"
            ),
            4,
            13,  # x starts after "value = not ".
            "float",
        ),
        (
            _script(
                "float a = close\n"
                "float b = open\n"
                "value = not a == b\n"
            ),
            5,
            13,  # a is the operand of not.
            "float",
        ),
        (
            _script("value = not math.sign(close - open) > 0\n"),
            3,
            13,  # The operand starts with math, before the call's suffix.
            "float",
        ),
        (
            _script("value = not 1 > 0\n"),
            3,
            13,  # The integer literal is the operand of not.
            "int",
        ),
    ],
)
def test_not_rejects_proven_numeric_operands(
    source: str, line: int, col: int, operand_type: str
) -> None:
    _assert_diagnostic(
        source,
        "PF-E2048",
        f"The not operator requires a bool operand; found {operand_type}.",
        {"operand_type": operand_type},
        line,
        col,
    )


@pytest.mark.parametrize(
    "source, line, col, operator",
    [
        (
            _script(
                "float a = close\n"
                "float b = close\n"
                "float c = open\n"
                "value = a > b == c\n"
            ),
            6,
            18,  # Analyzer location: numeric c; TV locates the bool comparison.
            "==",
        ),
        (
            _script("value = close == open == close[1]\n"),
            3,
            26,  # The numeric history operand starts with close, before "[".
            "==",
        ),
        (
            _script("value = 1 == close > open\n"),
            3,
            9,  # 1 is the numeric left operand of the equality.
            "==",
        ),
    ],
)
def test_equality_rejects_proven_bool_numeric_pairs(
    source: str, line: int, col: int, operator: str
) -> None:
    _assert_diagnostic(
        source,
        "PF-E2049",
        f"Cannot compare a bool value with a numeric value using {operator}.",
        {"op": operator},
        line,
        col,
    )


def test_bool_ordering_in_the_original_comparison_chain_is_refused() -> None:
    # A bool left operand cannot fill >'s numeric slot, before checking ==.
    _assert_diagnostic(
        _script("bool a = true\nfloat b = close\nfloat c = open\nvalue = a > b == c\n"),
        "PF-E2066", "Operator '>' cannot combine bool and float.",
        {"op": ">", "left": "bool", "right": "float"}, 6, 9,
    )


def test_direct_na_equality_reports_the_value_operand() -> None:
    _assert_diagnostic(
        _script("value = close == na\n"),
        "PF-E2050",
        'Cannot compare a value to "na" directly. Use the "na()" function instead.',
        {},
        3,
        9,  # close is the value being compared to na.
    )


@pytest.mark.parametrize(
    "body, line, col",
    [
        (
            "value = close != na\n",
            3,
            9,  # Two header lines precede close, after the eight-character prefix.
        ),
        (
            "value = na == close\n",
            3,
            9,  # The comparison starts at its left operand, na.
        ),
        (
            "value = na != close\n",
            3,
            9,  # The comparison starts at its left operand, na.
        ),
        (
            "int count = 1\nvalue = count == na\n",
            4,
            9,  # The declared int is on the second body line after "value = ".
        ),
        (
            "int count = 1\nvalue = count != na\n",
            4,
            9,  # The declared int is on the second body line after "value = ".
        ),
    ],
)
def test_direct_numeric_na_comparisons_remain_refused(
    body: str, line: int, col: int
) -> None:
    # The comparison starts at its left operand and takes no template arguments.
    _assert_diagnostic(
        _script(body),
        "PF-E2050",
        'Cannot compare a value to "na" directly. Use the "na()" function instead.',
        {},
        line,
        col,
    )


@pytest.mark.parametrize(
    "body,line,col",
    [
        (
            'string sample = input.string("value")\nvalue = sample == na\n',
            4, 9,  # The string operand starts at sample.
        ),
        (
            'string sample = input.string("value")\nvalue = sample != na\n',
            4, 9,  # The string operand starts at sample.
        ),
        (
            'string sample = input.string("value")\nvalue = na == sample\n',
            4, 9,  # na is the left operand of the comparison.
        ),
        (
            'string sample = input.string("value")\nvalue = na != sample\n',
            4, 9,  # na is the left operand of the comparison.
        ),
        (
            "type TextState\n"
            "    string value\n"
            'state = TextState.new("value")\n'
            "value = state.value == na\n",
            6, 9,  # The field operand starts with its receiver.
        ),
        (
            "type TextState\n"
            "    string value\n"
            'state = TextState.new("value")\n'
            "value = state.value != na\n",
            6, 9,  # The field operand starts with its receiver.
        ),
        (
            "type TextState\n"
            "    string value\n"
            'state = TextState.new("value")\n'
            "value = na == state.value\n",
            6, 9,  # na is the left operand, before the field receiver.
        ),
        (
            "type TextState\n"
            "    string value\n"
            'state = TextState.new("value")\n'
            "value = na != state.value\n",
            6, 9,  # na is the left operand, before the field receiver.
        ),
        (
            "array<float> samples = array.from(close)\nvalue = samples == na\n",
            4, 9,  # The collection operand starts at samples.
        ),
        (
            "array<float> samples = array.from(close)\nvalue = samples != na\n",
            4, 9,  # The collection operand starts at samples.
        ),
        (
            "array<float> samples = array.from(close)\nvalue = na == samples\n",
            4, 9,  # na is the left operand, before the collection.
        ),
        (
            "array<float> samples = array.from(close)\nvalue = na != samples\n",
            4, 9,  # na is the left operand, before the collection.
        ),
    ],
)
def test_direct_nonnumeric_na_comparisons_are_refused(
    body: str, line: int, col: int,
) -> None:
    # Direct bare-na comparisons have the same restriction in every domain.
    _assert_diagnostic(
        _script(body), "PF-E2050",
        'Cannot compare a value to "na" directly. Use the "na()" function instead.',
        {}, line, col,
    )


def test_direct_na_comparison_does_not_require_a_known_call_type() -> None:
    source = _script(
        "read_value() => close\n"
        "value = read_value() == na\n"
        "plot(value ? 1 : 0)\n"
    )
    # The independent direct-na check points at read_value's first token.
    _assert_diagnostic(
        source, "PF-E2050",
        'Cannot compare a value to "na" directly. Use the "na()" function instead.',
        {}, 4, 9,
    )


def test_consecutive_not_reports_the_second_keyword() -> None:
    _assert_diagnostic(
        _script("bool choice = not not flip\n"),
        "PF-E2051",
        '"not" cannot be used as a variable or function name.',
        {},
        3,
        19,  # The second not begins after "bool choice = not ".
    )


@pytest.mark.parametrize(
    "body",
    [
        # Parentheses separate the two keywords; a bool input needs no coercion.
        "bool flip = input.bool(true)\nbool both = not (not flip)\n",
        # A comparison's bool binding also accepts a parenthesized inner not.
        "bool x = close > open\nbool both = not (not x)\n",
        # Parenthesizing a bool expression preserves both negations.
        "bool both = not (not (close > open))\n",
        # The accepted double negation stays local to the left logical operand.
        "bool flip = close > open\nbool both = not (not flip) and close > low\n",
    ],
)
def test_parenthesized_double_not_remains_accepted(body: str) -> None:
    cpp = transpile(_script(body + "plot(both ? 1 : 0)\n"))
    # Each bool not emits !(operand), so two negations retain the literal !(!( prefix.
    assert "!(!(" in cpp
    compile_cpp(cpp, label="parenthesized double not")


@pytest.mark.parametrize(
    "expression, col",
    [
        ("(not not flip)", 20),  # The second not follows "bool choice = (not ".
        ("not (not not flip)", 24),  # The inner pair follows "bool choice = not (not ".
        ("not not (flip)", 19),  # Parenthesizing flip does not separate the two not tokens.
    ],
)
def test_grouping_around_consecutive_not_keeps_the_refusal(
    expression: str, col: int
) -> None:
    # The bool declaration adds one body line, so the refused expression is on line 4.
    _assert_diagnostic(
        _script("bool flip = close > open\n" + f"bool choice = {expression}\n"),
        "PF-E2051",
        '"not" cannot be used as a variable or function name.',
        {},
        4,
        col,
    )


def test_string_ordering_reports_the_first_string_operand() -> None:
    _assert_diagnostic(
        _script(
            'string a = "a"\n'
            'string b = "b"\n'
            "value = a < b\n"
        ),
        "PF-E2052",
        "Cannot order string values with <.",
        {"op": "<"},
        5,
        9,  # a is the first operand of the ordered comparison.
    )


@pytest.mark.parametrize(
    "library_body",
    [
        # A typed numeric parameter still uses the existing v5 bool coercion.
        "export check(float x, string text) => not x\n",
        # A direct numeric-na comparison retains its v5 relational lowering.
        "export check(float x, string text) => x == na\n",
        # Nested unary not operators remain accepted inside a v5 function.
        "export check(float x, string text) => not not (x > 0)\n",
        # Typed strings retain their existing ordered-comparison lowering.
        'export check(float x, string text) => text < "z"\n',
        # A global initializer uses its definition's version outside a body.
        "bool cached = not 1\n"
        "export check(float x, string text) => cached\n",
        # The global definition also shields the second not from v6 checks.
        "bool cached = not not true\n"
        "export check(float x, string text) => cached\n",
    ],
)
def test_imported_v5_operator_definitions_keep_their_lowering(
    library_body: str,
) -> None:
    source = _script(
        "import examples/OperatorChecks/1 as legacy\n"
        'value = legacy.check(close, "a")\n'
        "plot(value ? 1 : 0)\n"
    )
    library = '//@version=5\nlibrary("OperatorChecks")\n' + library_body
    cpp = transpile(source, libraries={"examples/OperatorChecks/1": library})
    # Each exported function returns a comparison or an explicitly typed bool.
    assert "bool OperatorChecks_v1__check(" in cpp
    compile_cpp(cpp, label="v5 operator definition")


def test_imported_v5_bool_numeric_equality_keeps_its_existing_codegen_refusal() -> None:
    source = _script(
        "import examples/OperatorChecks/1 as legacy\n"
        "value = legacy.check(close)\n"
        "plot(value ? 1 : 0)\n"
    )
    library = (
        '//@version=5\nlibrary("OperatorChecks")\n'
        "export check(float x) => true == x\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source, libraries={"examples/OperatorChecks/1": library})
    (diagnostic,) = caught.value.diagnostics
    # A bool equality still reaches the existing v5 third-state codegen gate.
    assert diagnostic.phase is Phase.CODEGEN
    assert diagnostic.message == (
        "library 'examples/OperatorChecks/1' is //@version=5: '==' of a bool reads "
        "v5's third bool state (na), which PineForge's two-state bool does not keep"
    )


@pytest.mark.parametrize(
    "body",
    [
        (
            "float x = close - open\n"
            "value = not (x > 5)\n"
        ),
        "bool flip = close > open\nvalue = not flip\n",
        (
            "float a = close\n"
            "float b = open\n"
            "value = a > b and close < high\n"
        ),
        (
            "bool flag = close > open\n"
            "bool a = close > open\n"
            "bool b = open > low\n"
            "value = (a == b) == flag\n"
        ),
        (
            "float x = close\n"
            "float y = open\n"
            "value = x == y\n"
        ),
        (
            'string s = "a"\n'
            'value = s == "a"\n'
        ),
        "value = na(close)\n",
        (
            "float a = close\n"
            "float b = open\n"
            "value = a != b\n"
        ),
        "value = timeframe.multiplier * 1 > 0\n",
    ],
)
def test_valid_operator_controls_remain_accepted(body: str) -> None:
    cpp = transpile(_script(body))
    compile_cpp(cpp, label="accepted operator control")
