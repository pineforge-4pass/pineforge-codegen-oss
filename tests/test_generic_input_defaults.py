"""Generic input overloads agree across analyzer, manifest and native settings."""

import json

import pytest

from pineforge_codegen import transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType
from tests._compile import compile_cpp, run_emitted_tu
from tests.test_string_settings_constants import RECEIPT_DRIVER


SCALARS = [
    ("bool", "true", True, "bool"),
    ("int", "123456789", 123456789, "int"),
    ("float", "2.75", 2.75, "float"),
    ("string", '"color.lime"', "color.lime", "string"),
]
COLORS = [
    ("named-color", "color.blue", 0xFF2962FF),
    ("color-new", "color.new(transp=50, color=color.red)", 0x80F23645),
    ("color-rgb", "color.rgb(12, 34, 56, 10.5)", 0xE40C2238),
    ("hex-opaque", "#12AB34", 0xFF12AB34),
    ("hex-alpha", "#12345640", 0x40123456),
    ("hex-transparent-black", "#00000000", 0),
]
HEADER = '//@version=6\nstrategy("Generic input defaults")\n'


def _input(expr, title, named, family=""):
    args = f'defval={expr}, title="{title}"' if named else f'{expr}, "{title}"'
    return f"input{family}({args})"


@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
@pytest.mark.parametrize("kind,expr,expected,receipt_type",
                         SCALARS + [("color", expr, value, "int") for _, expr, value in COLORS],
                         ids=[kind for kind, *_ in SCALARS] + [name for name, *_ in COLORS])
def test_static_default_selects_its_scalar_overload(kind, expr, expected, receipt_type, named):
    # Getter/storage for ordinary scalar inputs also have focused coverage in
    # test_codegen_input_getters; cover manifest and analyzer agreement here.
    use = "plot(close, color=selected)" if kind == "color" else "plot(close)"
    source = HEADER + f"selected = {_input(expr, 'Choice', named)}\n{use}\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    symbol = Analyzer(ast).analyze().symbols.resolve("selected")
    assert symbol.pine_type is PineType(kind)
    if kind == "color":
        assert symbol.type_spec.name == "color"
    full = transpile_full(source)
    entry, = full["inputs"]
    assert entry == {"title": "Choice", "type": kind, "default": expected, "supported": True}
    assert type(entry["default"]) is type(expected)
    if kind == "color":
        assert "int64_t selected" in full["cpp"]
        assert 'selected = get_input_int64("Choice",' in full["cpp"]
    assert f'{{"Choice", "{receipt_type}",' in full["cpp"]


def test_generic_and_explicit_color_defaults_match_the_native_receipt():
    declarations = []
    for index, (name, expr, _) in enumerate(COLORS):
        for mode, family in (("plain", ""), ("typed", ".color")):
            title = f"{mode}-{name}"
            declarations.append(
                f"{mode}{index} = {_input(expr, title, index % 2 != 0, family)}")
    full = transpile_full(HEADER + "\n".join(declarations) + "\nplot(close)\n")
    manifest = {entry["title"]: entry for entry in full["inputs"]}
    receipt = json.loads(run_emitted_tu(full["cpp"], RECEIPT_DRIVER, opt="-O0",
                                       label="generic color defaults"))
    native = {entry["name"]: entry for entry in receipt["inputs"]}
    assert len(manifest) == len(native) == len(declarations)
    for name, _, expected in COLORS:
        generic, typed = (f"{mode}-{name}" for mode in ("plain", "typed"))
        for title in (generic, typed):
            assert manifest[title]["default"] == expected
            assert native[title]["type"] == "int"
            assert native[title]["default"] == native[title]["effective_value"] == str(expected)
        assert {key: value for key, value in native[generic].items() if key != "name"} == {
            key: value for key, value in native[typed].items() if key != "name"}


def test_generic_source_and_unclassified_expressions_keep_their_paths():
    source = HEADER + """
src = input(defval=close, title="Source")
previous = src[1]
expr = input(2.0 * 3.0, "Expression")
signed = input(-5, "Signed")
unfoldable = input(color.new(color.red, close), "Series color builder")
plot(previous + expr + signed)
plot(close, color=unfoldable)
"""
    full = transpile_full(source)
    entries = {entry["title"]: entry for entry in full["inputs"]}
    # A bare source still has the historical public string tag.
    assert entries["Source"]["type"] == "string"
    assert entries["Source"]["default"] == "close"
    assert "high" in entries["Source"]["options"]
    assert 'get_input_source("Source", _src_close_)[0]' in full["cpp"]
    assert "Series<double> src" in full["cpp"]
    assert entries["Expression"]["type"] == "string"
    assert entries["Expression"]["default"] is None
    assert entries["Signed"]["type"] == "int"
    assert entries["Signed"]["default"] == -5
    assert entries["Series color builder"]["type"] == "string"
    assert entries["Series color builder"]["default"] is None
    for name in ("Expression", "Signed", "Series color builder"):
        assert f'get_input_double("{name}",' in full["cpp"]
    # The preservation case is also compiled: changing a source read to a
    # scalar must not leave history indexing on a non-series value.
    compile_cpp(full["cpp"], label="generic input series and expression fallbacks")
