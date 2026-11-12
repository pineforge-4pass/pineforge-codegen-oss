"""Located type field default errors and complete-C++ controls."""

from __future__ import annotations

from textwrap import dedent

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._e2e import reference_codegen, transpile_json
from tests._older_codegen import with_utf16_units


CPP_REFERENCE = "7d7681dcef6918f0cb304831320e5d8e5b545285"
HEADER = '//@version=6\nstrategy("Field defaults")\n'


def _pair(name, field, default, accepted_default, *, prelude=""):
    declaration = f"    {field} = {default}"
    source = (
        HEADER + dedent(prelude).lstrip()
        + f"type Book\n{declaration}\nbook = Book.new()\n"
    )
    control = source.replace(declaration, f"    {field} = {accepted_default}", 1)
    line = source.splitlines().index(declaration) + 1
    column = len(f"    {field} = ") + 1
    return pytest.param(source, control, (line, column), id=name)


_CASES = [
    _pair("array_constructor", "array<int> stamps", "array.new<int>()", "na"),
    _pair(
        "map_constructor", "map<string, int> stamps",
        "map.new<string, int>()", "na",
    ),
    _pair("array_from", "array<int> stamps", "array.from(1, 2)", "na"),
    _pair(
        "user_function", "int total", "seed()", "7",
        prelude="seed() => 7\n",
    ),
    _pair(
        "global_variable", "int total", "counter", "3",
        prelude="int counter = 3\n",
    ),
    _pair(
        "arithmetic_variable", "int total", "1 + counter", "1 + 2",
        prelude="int counter = 3\n",
    ),
    _pair("matrix_constructor", "matrix<int> stamps", "matrix.new<int>()", "na"),
    _pair(
        "receiver_method", "int total", "anchor.successor()", "8",
        prelude="""
        method successor(int receiver) => receiver + 1
        int anchor = 7
        """,
    ),
    _pair(
        "arithmetic_call", "int total", "1 + seed()", "1 + 2",
        prelude="seed() => 7\n",
    ),
    _pair(
        "comparison_variable", "bool active", "0 < counter", "0 < 2",
        prelude="int counter = 3\n",
    ),
    _pair(
        "logical_variable", "bool active", "true and enabled", "true and false",
        prelude="bool enabled = true\n",
    ),
    _pair(
        "ternary_variable", "int total", "true ? 0 : counter", "true ? 0 : 2",
        prelude="int counter = 3\n",
    ),
    _pair(
        "unary_variable", "int total", "-counter", "-1",
        prelude="int counter = 3\n",
    ),
]


@pytest.mark.parametrize("source,control,position", _CASES)
def test_refusals_are_coded_and_located(source, control, position):
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="strategy.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2087"
    assert diagnostic.args == {}
    assert diagnostic.message == (
        "The default value cannot be a function, variable or calculation."
    )
    assert diagnostic.hint is None
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "strategy.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == position
    assert render_diagnostic(diagnostic.code, diagnostic.args) == (
        diagnostic.message, diagnostic.hint,
    )


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    if tree is None:
        pytest.skip("The C++ reference commit is unavailable")
    return tree


def _assert_unchanged(source, reference, tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    # The reference counted str.length in bytes, through no unit helper.
    assert transpile(source) == with_utf16_units(before["cpp"])


@pytest.mark.parametrize("source,control,position", _CASES)
def test_refused_sources_were_accepted_by_reference(
    source, control, position, reference, tmp_path,
):
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before


@pytest.mark.parametrize("source,control,position", _CASES)
def test_paired_controls_keep_complete_cpp(
    source, control, position, reference, tmp_path,
):
    _assert_unchanged(control, reference, tmp_path)


@pytest.mark.parametrize("body", [
    pytest.param(
        """
        type Book
            int total = 2
            float price = 1.5
            float missing = na
            int offset = -1
            bool active = true
            bool closed = false
            string title = "record"
            float omitted
        book = Book.new()
        plot(book.price + book.total + book.offset)
        """,
        id="literal_and_omitted_defaults",
    ),
    pytest.param(
        """
        type Book
            string title = "array.new<int>() + counter"
        book = Book.new()
        plot(str.length(book.title))
        """,
        id="literal_spelled_like_a_calculation",
    ),
    pytest.param(
        """
        type Book
            color tint = color.red
            float angle = math.pi
            string symbol = syminfo.tickerid
        book = Book.new()
        plot(book.angle)
        """,
        id="builtin_members",
    ),
    pytest.param(
        """
        type Book
            array<int> stamps
        book = Book.new(stamps = array.new<int>())
        plot(na(book.stamps) ? 1 : 0)
        """,
        id="named_constructor_argument",
    ),
    pytest.param(
        """
        type Book
            int total = 0
        book = Book.new()
        if bar_index > 0
            array<int> local = array.new<int>()
            book.total := local.size()
        plot(book.total)
        """,
        id="block_local_array_initializer",
    ),
    pytest.param(
        """
        type Book
            array<int> stamps
        build() =>
            array<int> local = array.new<int>()
            Book.new(stamps = local)
        book = build()
        plot(na(book.stamps) ? 1 : 0)
        """,
        id="function_local_array_initializer",
    ),
    pytest.param(
        """
        type Book
            int total = 0
        method count(Book receiver) =>
            array<int> local = array.new<int>()
            receiver.total + local.size()
        book = Book.new()
        plot(book.count())
        """,
        id="method_local_array_initializer",
    ),
])
def test_accepted_controls_keep_complete_cpp(body, reference, tmp_path):
    _assert_unchanged(HEADER + dedent(body).lstrip(), reference, tmp_path)
