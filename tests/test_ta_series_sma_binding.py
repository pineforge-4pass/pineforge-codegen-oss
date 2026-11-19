"""A series ``ta.sma`` length answers for the declaration it reads, however that is spelled.

A chart ``ta.sma`` whose length the script computes on each bar is lowered
onto the engine's series class. A length that reads a script-level ``var`` or
``varip`` is not: that binding is the stable-var path's, which sizes the
constructor from it (an admitted literal, an input) or leaves the constructor
guard's refusal.

The lowering puts that question to the declaration each name of the length is
bound to. The analyzer's type visit records the declaration of a read, except
for a read spelled like a built-in namespace (``size``, ``session``,
``text``), which it leaves to its namespace handling; the lexical binder,
which runs before it, binds every authored read. So a persistent declaration
spelled like a namespace must answer as the same declaration spelled ``p``
does: the ordinary unsupported-length refusal, at the call. Each namespace
spelling also runs where it is valid, as a per-bar length and as the literal
the stable-var path admits, against the constant-length windows of
``tests/_series_sma_oracle.py``.
"""

from __future__ import annotations

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
# reads it.
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

PERSISTENT_CASES = [
    pytest.param(shape, name, id=f"{shape}-{name}")
    for shape in PERSISTENT_LENGTHS
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

    # The call's length is the argument the refusal will name.
    [site] = [
        site for site in context.ta_call_sites if site.class_name == "ta::SMA"
    ]
    assert site.ctor_args == [argument]
    [length] = site.ctor_nodes
    assert isinstance(length, Identifier) and length.name == argument


@pytest.mark.parametrize("shape, name", PERSISTENT_CASES)
def test_a_length_that_reads_a_script_level_var_keeps_the_refusal(
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
