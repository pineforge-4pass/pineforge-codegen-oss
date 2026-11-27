"""A series ``ta.sma`` length answers for the declaration it reads, however that is spelled.

A chart ``ta.sma`` whose length the script computes on each bar is lowered
onto the engine's series class, and a script-level ``var`` or ``varip`` among
the names such a length reads does not change that: the call reads that
declaration's storage, as it reads any other name. A length that reads a
script-level ``var`` or ``varip`` and is not shown to vary by bar is not
lowered: that binding is the stable-var path's, which sizes the constructor
from it (an admitted literal, an input) or leaves the constructor guard's
refusal.

The lowering puts that question to the declaration each name of the length is
bound to. The analyzer's type visit records the declaration of a read, except
for a read spelled like a built-in namespace (``size``, ``session``,
``text``), which it leaves to its namespace handling; the lexical binder,
which runs before it, binds every authored read. So a persistent declaration
spelled like a namespace must answer as the same declaration spelled ``p``
does: the ordinary unsupported-length refusal, at the call, or the series
class where the length varies by bar. Each namespace
spelling also runs where it is valid, as a per-bar length and as the literal
the stable-var path admits, against the constant-length windows of
``tests/_series_sma_oracle.py``.
"""

from __future__ import annotations

import math
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.analyzer.value_bindings import ValueBindings
from pineforge_codegen.ast_nodes import Identifier, VarDecl
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.limits import iter_ast_nodes
from pineforge_codegen.parser import Parser
from tests import _series_sma_oracle as oracle
from tests._compile import run_emitted_tu

HEADER = '//@version=6\nstrategy("series sma binding")\n'
FILENAME = "series-sma-binding.pine"
HINT = "Use a literal, an input.*() value, or arithmetic over those for TA lengths."

PLAIN = "p"
# Script variables spelled like a built-in namespace.
NAMESPACE_NAMED = ("size", "session", "text")

# How a length comes to read the script-level ``var`` / ``varip`` ``{v}``, and
# the argument the call then spells. The stable-var path sizes none of these:
# ``varip`` has no literal admission, a user function fences ``var``'s, and
# the admission is of the bare name at the call, not of a declaration that
# reads it. Only the last one varies by bar (``bar_index``).
PERSISTENT_LENGTHS = {
    "varip": (
        "varip int {v} = 5\n"
        "float out = ta.sma(close, {v})\n", "{v}"),
    "varip-by-keyword": (
        "varip int {v} = 5\n"
        "float out = ta.sma(close, length = {v})\n", "{v}"),
    "var-beside-a-function": (
        "unused(int q) => q\n"
        "var int {v} = 5\n"
        "float out = ta.sma(close, {v})\n", "{v}"),
    "var-through-a-declaration": (
        "var int {v} = 5\n"
        "int n = {v}\n"
        "float out = ta.sma(close, n)\n", "n"),
    "var-in-a-per-bar-declaration": (
        "var int {v} = 5\n"
        "int n = {v} + bar_index % 5\n"
        "float out = ta.sma(close, n)\n", "n"),
}

PER_BAR_SHAPE = "var-in-a-per-bar-declaration"

PERSISTENT_CASES = [
    pytest.param(shape, name, id=f"{shape}-{name}")
    for shape in PERSISTENT_LENGTHS
    for name in (PLAIN, *NAMESPACE_NAMED)
]
# Nothing in these lengths varies by bar.
FIXED_PERSISTENT_CASES = [
    pytest.param(shape, name, id=f"{shape}-{name}")
    for shape in PERSISTENT_LENGTHS if shape != PER_BAR_SHAPE
    for name in (PLAIN, *NAMESPACE_NAMED)
]
PER_BAR_PERSISTENT_CASES = [
    pytest.param(name, id=f"{PER_BAR_SHAPE}-{name}")
    for name in (PLAIN, *NAMESPACE_NAMED)
]


def _persistent_source(shape: str, name: str) -> tuple[str, str]:
    body, argument = PERSISTENT_LENGTHS[shape]
    return HEADER + body.replace("{v}", name), argument.replace("{v}", name)


@pytest.mark.parametrize("shape, name", PERSISTENT_CASES)
def test_every_spelling_of_a_persistent_length_is_bound_to_its_declaration(
        shape: str, name: str) -> None:
    source, argument = _persistent_source(shape, name)
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    context = Analyzer(program, filename=FILENAME).analyze()

    [declaration] = [
        statement for statement in program.body
        if isinstance(statement, VarDecl) and statement.name == name
    ]
    assert declaration.is_var or declaration.is_varip
    # The script reads the name once.
    [read] = {
        id(node): node for node, _depth in iter_ast_nodes(program)
        if isinstance(node, Identifier) and node.name == name
    }.values()
    # The lexical binder ties the read to the script-level declaration.
    assert ValueBindings.declaration_of(read) == (id(declaration), name)
    if name == PLAIN:
        # So does the analyzer's own first-visit record of a plain name.
        assert context.identifier_binding_nodes[id(read)] is declaration

    # The call's length is the argument a refusal names.
    [site] = [
        site for site in context.ta_call_sites if site.class_name == "ta::SMA"
    ]
    assert site.ctor_args == [argument]
    [length] = site.ctor_nodes
    assert isinstance(length, Identifier) and length.name == argument


@pytest.mark.parametrize("shape, name", FIXED_PERSISTENT_CASES)
def test_a_persistent_length_not_shown_to_vary_by_bar_keeps_the_refusal(
        shape: str, name: str) -> None:
    source, argument = _persistent_source(shape, name)
    call_line = next(
        number for number, line in enumerate(source.splitlines(), start=1)
        if "ta.sma(" in line
    )

    with pytest.raises(CompileError) as caught:
        transpile(source, filename=FILENAME)

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.CODEGEN
    assert diagnostic.code == "PF-E5092"
    assert diagnostic.message == (
        f"Unsupported TA constructor length '{argument}' for ta.sma: it is "
        "neither a compile-time constant nor derived from an input, so "
        "PineForge cannot size the indicator buffer. TradingView accepts a "
        "series length for ta.sma(); PineForge does not support it yet."
    )
    assert diagnostic.hint == HINT
    assert (diagnostic.location.file, diagnostic.location.line) == (
        FILENAME, call_line)


@pytest.mark.parametrize("name", PER_BAR_PERSISTENT_CASES)
def test_a_per_bar_length_that_reads_a_script_level_var_takes_the_series_class(
        name: str) -> None:
    source, argument = _persistent_source(PER_BAR_SHAPE, name)
    # The length ``n`` reads ``bar_index`` beside the persistent declaration,
    # so it varies by bar. It used to be refused for that declaration.
    cpp = transpile(source, filename=FILENAME)

    [member] = re.findall(r"^\s*_PFSeriesSma\s+(\w+);$", cpp, re.M)
    call = next(
        line for line in cpp.splitlines()
        if re.search(rf"\b{re.escape(member)}\.compute\(", line)
    )
    # Each call passes its close and the length its argument holds then; no
    # constructor is sized from the declaration's literal.
    assert "current_bar_.close" in call
    assert f"pineforge::source::ta_number({argument})" in call
    assert not re.search(r"_ta_sma_\d+\(\d", cpp)


def _per_bar(cpp: str, members, label: str):
    return oracle.per_bar_rows(
        run_emitted_tu(cpp, oracle.per_bar_driver(members), opt="-O0", label=label),
        len(members))


@pytest.fixture(scope="module")
def constant_windows():
    """Each bar's ``ta.sma(close, <constant>)`` for the windows 1 to 13."""
    return _per_bar(
        transpile(oracle.EVERY_BAR_REFERENCE), oracle.EVERY_BAR_MEMBERS,
        "constant_length_reference")


# ``bar_index % 4 + 2`` is 2, 3, 4, 5, repeating. The first four bars each ask
# for one close more than they have, so they are na. A row is (length, the sum
# of the last ``length`` closes).
PER_BAR_WINDOWS = (
    (2, None), (3, None), (4, None), (5, None),
    (2, 29), (3, 47), (4, 60), (5, 77),
    (2, 37), (3, 53), (4, 75), (5, 94),
    (2, 44), (3, 65), (4, 89), (5, 117),
    (2, 51), (3, 78),
)
# A window of five on every bar: the first mean is bar 4's.
LITERAL_WINDOWS = (
    (5, None), (5, None), (5, None), (5, None),
    (5, 62), (5, 70), (5, 71), (5, 77), (5, 82), (5, 84), (5, 88),
    (5, 94), (5, 102), (5, 103), (5, 111), (5, 117), (5, 121), (5, 123),
)


@pytest.mark.parametrize("name", NAMESPACE_NAMED)
def test_a_namespace_spelled_per_bar_length_is_the_scripts_variable(
        name: str, constant_windows) -> None:
    oracle.assert_per_bar(
        oracle.scheduled(constant_windows, PER_BAR_WINDOWS), PER_BAR_WINDOWS,
        "ta.sma(close, <constant length>)")
    source = (
        HEADER
        + f"{name} = bar_index % 4 + 2\n"
        + f"float out = ta.sma(close, {name})\n"
    )
    observed = _per_bar(transpile(source), ("out",), f"per_bar_{name}")
    oracle.assert_per_bar(
        oracle.column(observed), PER_BAR_WINDOWS, f"ta.sma(close, {name})")


@pytest.mark.parametrize("name", NAMESPACE_NAMED)
def test_a_namespace_spelled_var_literal_keeps_its_constructor(
        name: str, constant_windows) -> None:
    oracle.assert_per_bar(
        oracle.scheduled(constant_windows, LITERAL_WINDOWS), LITERAL_WINDOWS,
        "ta.sma(close, 5)")
    source = (
        HEADER
        + f"var int {name} = 5\n"
        + f"float out = ta.sma(close, {name})\n"
    )
    cpp = transpile(source)
    # The stable-var path sized the constructor; the series class is not used.
    assert re.search(r"_ta_sma_\d+\(5\)", cpp)
    assert "_PFSeriesSma" not in cpp
    observed = _per_bar(cpp, ("out",), f"var_literal_{name}")
    oracle.assert_per_bar(
        oracle.column(observed), LITERAL_WINDOWS, f"ta.sma(close, {name})")


# ``5 + bar_index % 5`` is 5, 6, 7, 8, 9, repeating. The first five bars each
# ask for more closes than they have, so they are na. A row is (length, the
# sum of the last ``length`` closes).
PERSISTENT_PER_BAR_WINDOWS = (
    (5, None), (6, None), (7, None), (8, None), (9, None),
    (5, 70), (6, 83), (7, 100), (8, 120), (9, 136),
    (5, 88), (6, 107), (7, 132), (8, 153), (9, 177),
    (5, 117), (6, 140), (7, 167),
)


@pytest.mark.parametrize("name", PER_BAR_PERSISTENT_CASES)
def test_a_per_bar_length_that_reads_a_script_level_var_follows_each_bar(
        name: str, constant_windows) -> None:
    oracle.assert_per_bar(
        oracle.scheduled(constant_windows, PERSISTENT_PER_BAR_WINDOWS),
        PERSISTENT_PER_BAR_WINDOWS, "ta.sma(close, <constant length>)")
    source, argument = _persistent_source(PER_BAR_SHAPE, name)
    observed = _per_bar(
        transpile(source), ("out",), f"persistent_per_bar_{name}")
    oracle.assert_per_bar(
        oracle.column(observed), PERSISTENT_PER_BAR_WINDOWS,
        f"ta.sma(close, {argument})")


# Six invented closes. ``bar_index % 2`` is 0, 1, repeating, so the length of
# both scripts below is 2, 3, 2, 3, 2, 3: na while it asks for more closes
# than the chart has, then (6 + 4) / 2, (6 + 4 + 8) / 3, (8 + 12) / 2 and
# (8 + 12 + 10) / 3. Every sum and mean is a small whole number, so the
# comparison is exact.
SIX_CLOSES = (2, 6, 4, 8, 12, 10)
SIX_MEANS = (None, None, 5.0, 6.0, 10.0, 10.0)


def _six_bar_means(cpp: str, label: str) -> list[float]:
    driver = oracle.per_bar_driver(("out",), closes=SIX_CLOSES)
    rows = oracle.per_bar_rows(
        run_emitted_tu(cpp, driver, opt="-O0", label=label), 1,
        bars=len(SIX_CLOSES))
    return oracle.column(rows)


def _assert_six_means(actual, what: str) -> None:
    assert len(actual) == len(SIX_MEANS), (what, actual)
    for bar, (value, mean) in enumerate(zip(actual, SIX_MEANS)):
        if mean is None:
            assert math.isnan(value), (
                f"{what}: bar {bar} is {value!r}; na is expected")
        else:
            assert value == mean, (
                f"{what}: bar {bar} is {value!r}; {mean} is expected")


def test_a_persistent_base_of_a_per_bar_length_answers_as_its_literal_twin() -> None:
    persistent = (
        HEADER
        + "var int base = 2\n"
        + "n = base + bar_index % 2\n"
        + "float out = ta.sma(close, n)\n"
    )
    literal = (
        HEADER
        + "n = 2 + bar_index % 2\n"
        + "float out = ta.sma(close, n)\n"
    )
    # Transpiled before anything is compiled or run: this length was refused,
    # for the persistent declaration its ``n`` reads.
    persistent_cpp = transpile(persistent, filename=FILENAME)
    _assert_six_means(
        _six_bar_means(transpile(literal), "per_bar_literal_base"),
        "ta.sma(close, 2 + bar_index % 2)")
    _assert_six_means(
        _six_bar_means(persistent_cpp, "per_bar_persistent_base"),
        "ta.sma(close, base + bar_index % 2)")
