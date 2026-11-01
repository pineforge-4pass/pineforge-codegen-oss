"""Located refusals for source forms TradingView rejects."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp


HEADER = '//@version=6\nstrategy("syntax checks")\n'


def _assert_refusal(
    source: str,
    code: str,
    message: str,
    arguments: dict,
    phase: Phase,
    line: int,
    column: int,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="syntax_checks.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is phase
    assert diagnostic.code == code
    assert diagnostic.message == message
    assert diagnostic.args == arguments
    assert diagnostic.location.file == "syntax_checks.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, column)


REFUSALS = [
    pytest.param(
        HEADER + "value = #abc\n",
        "PF-E0043",
        "Syntax error at input '#abc'",
        {"value": "#abc"},
        Phase.LEXER,
        3,
        9,  # The short color begins after "value = ".
        id="short-color",
    ),
    pytest.param(
        HEADER + "value = ٢.0\n",
        "PF-E0044",
        "no viable alternative at character '٢'",
        # The lexer interpolates ch, the non-ASCII digit.
        {"ch": "٢"},
        Phase.LEXER,
        3,
        9,  # The non-ASCII digit begins after "value = ".
        id="unicode-digit",
    ),
    pytest.param(
        HEADER + "float π = close - open\n",
        "PF-E0044",
        "no viable alternative at character 'π'",
        # The lexer interpolates ch, the non-ASCII identifier character.
        {"ch": "π"},
        Phase.LEXER,
        3,
        7,  # The non-ASCII identifier begins after "float ".
        id="unicode-identifier",
    ),
    pytest.param(
        (
            HEADER
            + "float gate = 2.0\n"
            + "while gate\n"
            + "    gate -= 1.0\n"
        ),
        "PF-E2062",
        'The condition of the "while" statement must evaluate to a "bool" value.',
        # The complete condition message is literal text.
        {},
        Phase.ANALYZER,
        4,
        7,  # gate is the condition after "while ".
        id="numeric-while-condition",
    ),
    pytest.param(
        (
            HEADER
            + "int remaining = 2\n"
            + "while remaining\n"
            + "    remaining -= 1\n"
        ),
        "PF-E2062",
        'The condition of the "while" statement must evaluate to a "bool" value.',
        # The complete condition message is literal text.
        {},
        Phase.ANALYZER,
        4,
        7,  # remaining stays int after reassignment and starts after "while ".
        id="reassigned-integer-while-condition",
    ),
    pytest.param(
        (
            HEADER
            + "value = switch true\n"
            + "    => 1.0\n"
            + "    false => 2.0\n"
        ),
        "PF-E0045",
        "Mismatched input '=>' expecting set 'a case expression'.",
        # Both the arrow and the expected case text are literal.
        {},
        Phase.PARSER,
        4,
        5,  # The default arrow is the first arm after four spaces.
        id="default-arm-first",
    ),
    pytest.param(
        (
            HEADER
            + "value = switch true\n"
            + '    true => "one"\n'
            + "    => close - open\n"
        ),
        "PF-E2063",
        'Return type of one of the "if" or "switch" blocks is not compatible '
        "with return type of other block(s) (string, float).",
        # The first arm is string; the later incompatible arm is float.
        {"first_type": "string", "certain_type": "float"},
        Phase.ANALYZER,
        5,
        8,  # close is the second arm's proven numeric return expression.
        id="mixed-switch-return-types",
    ),
    pytest.param(
        (
            HEADER
            + "type TextBox\n"
            + "    string value\n"
            + "method text(TextBox self) => self.value\n"
            + 'holder = TextBox.new("ok")\n'
            + "condition = close > open\n"
            + 'string result = condition ? holder.text() : "fallback"\n'
        ),
        "PF-E2064",
        '"text" cannot be used as a variable or function name.',
        # The method declaration's node.name is text.
        {"name": "text"},
        Phase.ANALYZER,
        5,
        1,  # Two header lines and two type-declaration lines precede the method.
        id="reserved-method-name",
    ),
    pytest.param(
        (
            HEADER
            + "type Item\n"
            + "    float value\n"
            + "box = Item.new(close - open)\n"
            + "previous = box[1]\n"
            + "value = na(previous) ? 0.0 : previous.value\n"
        ),
        "PF-E2065",
        "Invalid object name: box. Namespaces of built-ins cannot be used.",
        {"name": "box"},
        Phase.ANALYZER,
        5,
        1,  # box is the object declaration whose history is read below.
        id="reserved-object-name",
    ),
]


@pytest.mark.parametrize(
    "source, code, message, arguments, phase, line, column",
    REFUSALS,
)
def test_refused_forms_have_one_located_diagnostic(
    source: str,
    code: str,
    message: str,
    arguments: dict,
    phase: Phase,
    line: int,
    column: int,
) -> None:
    _assert_refusal(source, code, message, arguments, phase, line, column)


CONTROLS = [
    pytest.param(
        "valid-six-digit-color",
        HEADER + "value = #aabbcc\n",
        # Six hexadecimal digits replace the refused three-digit spelling.
        id="six-digit-color",
    ),
    pytest.param(
        "valid-eight-digit-color",
        HEADER + "value = #aabbccdd\n",
        # Eight hexadecimal digits are the other valid color-literal length.
        id="eight-digit-color",
    ),
    pytest.param(
        "ascii-digit",
        HEADER + "value = 2.0\n",
        # The ASCII digit keeps the same numeric expression valid.
        id="ascii-digit",
    ),
    pytest.param(
        "integer-digit",
        HEADER + "value = 2\n",
        # An integer ASCII digit is the other nearest accepted number spelling.
        id="integer-digit",
    ),
    pytest.param(
        "ascii-identifier",
        HEADER + "float value = close - open\n",
        # An ASCII identifier replaces the refused Unicode identifier.
        id="ascii-identifier",
    ),
    pytest.param(
        "ascii-price-identifier",
        HEADER + "float price = close - open\n",
        # A second ordinary identifier keeps the identifier rule non-specific.
        id="ascii-price-identifier",
    ),
    pytest.param(
        "numeric-comparison-while",
        (
            HEADER
            + "float gate = 2.0\n"
            + "while gate > 0\n"
            + "    gate -= 1.0\n"
        ),
        # A comparison supplies the bool condition required by while.
        id="numeric-comparison-while",
    ),
    pytest.param(
        "series-comparison-while",
        (
            HEADER
            + "float gate = 2.0\n"
            + "while close > open\n"
            + "    gate -= 1.0\n"
        ),
        # A comparison of chart series is also a bool condition.
        id="series-comparison-while",
    ),
    pytest.param(
        "default-after-false-case",
        (
            HEADER
            + "value = switch true\n"
            + "    false => 2.0\n"
            + "    => 1.0\n"
        ),
        # Moving the default below a case produces the accepted switch order.
        id="default-after-false-case",
    ),
    pytest.param(
        "default-after-true-case",
        (
            HEADER
            + "value = switch true\n"
            + "    true => 1.0\n"
            + "    => 2.0\n"
        ),
        # A true case followed by the default is the nearest valid ordering.
        id="default-after-true-case",
    ),
    pytest.param(
        "matching-string-arms",
        (
            HEADER
            + "value = switch true\n"
            + '    true => "one"\n'
            + '    => "two"\n'
        ),
        # Both arms return strings, so the selection has one proven type.
        id="matching-string-arms",
    ),
    pytest.param(
        "matching-number-arms",
        (
            HEADER
            + "value = switch true\n"
            + "    true => close - open\n"
            + "    => open - close\n"
        ),
        # Both arms return numeric expressions, so the selection remains valid.
        id="matching-number-arms",
    ),
    pytest.param(
        "renamed-method",
        (
            HEADER
            + "type TextBox\n"
            + "    string value\n"
            + "method renderText(TextBox self) => self.value\n"
            + 'holder = TextBox.new("ok")\n'
            + "condition = close > open\n"
            + 'string result = condition ? holder.renderText() : "fallback"\n'
        ),
        # A user method name outside the built-in namespace remains accepted.
        id="renamed-method",
    ),
    pytest.param(
        "another-renamed-method",
        (
            HEADER
            + "type TextBox\n"
            + "    string value\n"
            + "method formatText(TextBox self) => self.value\n"
            + 'holder = TextBox.new("ok")\n'
            + "condition = close > open\n"
            + 'string result = condition ? holder.formatText() : "fallback"\n'
        ),
        # A different ordinary method name keeps the same receiver shape valid.
        id="another-renamed-method",
    ),
    pytest.param(
        "renamed-object",
        (
            HEADER
            + "type Item\n"
            + "    float value\n"
            + "item = Item.new(close - open)\n"
            + "previous = item[1]\n"
            + "value = na(previous) ? 0.0 : previous.value\n"
        ),
        # Renaming the object removes only the namespace collision.
        id="renamed-object",
    ),
    pytest.param(
        "scalar-namespace-name",
        HEADER + "box = 1.0\nvalue = box[1]\n",
        # A scalar named box is outside the object-name restriction.
        id="scalar-namespace-name",
    ),
]


@pytest.mark.parametrize("label, source", CONTROLS)
def test_nearest_valid_forms_compile(label: str, source: str) -> None:
    compile_cpp(transpile(source, filename=f"{label}.pine"), label=label)


V5_CASES = [
    pytest.param(
        "export choose() => #abc\n",
        "legacy.choose()",
        # The v5 library keeps its existing color-literal behavior.
        id="v5-short-color",
    ),
    pytest.param(
        "export choose() => ٢.0\n",
        "legacy.choose()",
        # The v5 library keeps its existing Unicode-digit behavior.
        id="v5-unicode-digit",
    ),
    pytest.param(
        "export choose(float π) => π\n",
        "legacy.choose(2.0)",
        # The v5 library keeps its existing Unicode-identifier behavior.
        id="v5-unicode-identifier",
    ),
    pytest.param(
        (
            "export choose(float gate) =>\n"
            "    while gate\n"
            "        gate -= 1.0\n"
            "    gate\n"
        ),
        "legacy.choose(2.0)",
        # v5 keeps numeric truthiness in a library while condition.
        id="v5-numeric-while",
    ),
    pytest.param(
        (
            "export choose(bool flag) =>\n"
            "    switch flag\n"
            "        => 1.0\n"
            "        true => 2.0\n"
        ),
        "legacy.choose(true)",
        # v5 keeps the library's accepted default-first switch lowering.
        id="v5-default-first",
    ),
    pytest.param(
        (
            "export choose(bool flag) =>\n"
            '    switch flag\n'
            '        true => "one"\n'
            "        => close - open\n"
        ),
        "legacy.choose(close > open)",
        # v5 keeps the library's existing mixed-arm lowering.
        id="v5-mixed-switch",
    ),
    pytest.param(
        "export method text(string self) => self\n",
        '"legacy".text()',
        # v5 keeps a library method whose name is reserved in v6.
        id="v5-reserved-method",
    ),
    pytest.param(
        (
            "type Item\n"
            "    float value\n"
            "export make() =>\n"
            "    box = Item.new(close - open)\n"
            "    previous = box[1]\n"
            "    previous.value\n"
        ),
        "legacy.make()",
        # v5 keeps the library object name and historical field access.
        id="v5-reserved-object",
    ),
]


@pytest.mark.parametrize("library_body, call", V5_CASES)
def test_imported_v5_definitions_keep_their_existing_lowering(
    library_body: str, call: str,
) -> None:
    source = (
        HEADER
        + "import local/Legacy/1 as legacy\n"
        + f"result = {call}\n"
    )
    library = (
        '//@version=5\n'
        'library("Legacy")\n'
        + library_body
    )
    cpp = transpile(
        source,
        filename="v5_import.pine",
        libraries={"local/Legacy/1": library},
    )
    assert cpp
