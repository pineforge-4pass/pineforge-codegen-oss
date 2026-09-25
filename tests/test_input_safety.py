"""Untrusted Pine must fail at a Pine location or emit compilable C++."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.codegen.helpers import CPP_KEYWORDS
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import KEYWORDS
from pineforge_codegen.limits import (
    MAX_AST_DEPTH, MAX_BLOCK_DEPTH, MAX_DELIMITER_DEPTH,
    MAX_EXPRESSION_CHARS, MAX_EXPRESSION_TOKENS, MAX_SOURCE_CHARS,
    MAX_STATEMENTS, MAX_TRANSPILE_SECONDS,
)
from tests._compile import compile_cpp


BASE = '//@version=6\nstrategy("input safety")\n'

# Independent inventory: C++17 keywords/operator alternatives and C++20's
# additions.  The equality assertion prevents the compile table from silently
# shrinking when somebody edits the production escape set.
EXPECTED_CPP_KEYWORDS = frozenset("""
    alignas alignof and and_eq asm auto bitand bitor bool break case catch
    char char8_t char16_t char32_t class co_await co_return co_yield compl
    concept const consteval constexpr constinit const_cast continue decltype
    default delete do double dynamic_cast else enum explicit export extern
    false float for friend goto if inline int long mutable namespace new
    noexcept not not_eq nullptr operator or or_eq private protected public
    register reinterpret_cast requires return short signed sizeof static
    static_assert static_cast struct switch template this thread_local throw
    true try typedef typeid typename union unsigned using virtual void
    volatile wchar_t while xor xor_eq
""".split())


@pytest.mark.parametrize("name", sorted(EXPECTED_CPP_KEYWORDS))
def test_every_cpp_keyword_compiles_or_pine_rejects_it(name: str) -> None:
    assert CPP_KEYWORDS == EXPECTED_CPP_KEYWORDS
    source = BASE + f"{name} = 2\n"
    try:
        cpp = transpile(source, filename="keyword.pine")
    except CompileError as exc:
        # These spellings are Pine control/type keywords (or library-only
        # export), so no strategy can legally bind one as a variable.
        assert name in KEYWORDS or name == "export"
        assert exc.diagnostics
        assert exc.diagnostics[0].location.file == "keyword.pine"
        assert exc.diagnostics[0].location.line == 3
    else:
        assert f"pf_safe_{name}" in cpp or f"_{name}_" in cpp
        compile_cpp(cpp, label=f"cpp_keyword_{name}", standard="c++20")


@pytest.mark.parametrize("name", [
    "NULL", "NAN", "INFINITY", "EOF", "INT_MAX", "EXIT_SUCCESS",
    "stdin", "stdout", "stderr", "assert",
    "GeneratedStrategy", "Bar", "Series", "on_source_bar", "std", "ta",
    "__LINE__", "__FILE__",
])
def test_header_macro_and_emitter_name_collisions_compile(name: str) -> None:
    cpp = transpile(BASE + f"{name} = 2\n", filename="name.pine")
    assert f"pf_safe_{name}" in cpp
    compile_cpp(cpp, label=f"cpp_collision_{name}", standard="c++20")


@pytest.mark.parametrize("body", [
    "namespace = 2\npf_safe_namespace = 3\nx = namespace + pf_safe_namespace",
    "namespace(namespace) => namespace + 1\nx = namespace(2)",
    "type Point\n    int namespace\np = Point.new(2)\nx = p.namespace",
    "type namespace\n    int field = 1\nx = namespace.new()",
    "[namespace, class] = ta.macd(close, 1, 2, 3)\nx = namespace + class",
])
def test_escaped_names_remain_distinct_in_all_emitted_positions(body: str) -> None:
    cpp = transpile(BASE + body + "\n", filename="name.pine")
    compile_cpp(cpp, label="escaped_name_positions", standard="c++20")


def test_previously_escaped_accessor_keeps_its_emitted_spelling() -> None:
    cpp = transpile(BASE + "net_profit = 2\nx = strategy.netprofit\n")
    assert "int _net_profit_" in cpp
    compile_cpp(cpp, label="legacy_accessor_escape")


def _limit_probe(which: str) -> tuple[str, str]:
    if which == "source":
        return (BASE + "//" + "x" * MAX_SOURCE_CHARS,
                f"Source size exceeds {MAX_SOURCE_CHARS} characters.")
    if which == "tokens":
        return (BASE + "x = " + " + ".join(["1"] * 10000) + "\n",
                f"Expression size exceeds {MAX_EXPRESSION_TOKENS} tokens "
                "in one logical statement.")
    if which == "characters":
        return (BASE + "x = " + "1" * (MAX_EXPRESSION_CHARS + 1) + "\n",
                f"Expression size exceeds {MAX_EXPRESSION_CHARS} characters "
                "in one logical statement.")
    if which == "delimiters":
        return (BASE + "x = " + "(" * 1000 + "1" + ")" * 1000 + "\n",
                f"Delimiter nesting depth exceeds {MAX_DELIMITER_DEPTH} levels.")
    if which == "blocks":
        return (BASE + "".join("    " * i + "if true\n" for i in range(40)),
                f"Block nesting depth exceeds {MAX_BLOCK_DEPTH} levels.")
    if which == "ast":
        return (BASE + "x = " + " + ".join(["1"] * 80) + "\n",
                f"AST nesting depth exceeds {MAX_AST_DEPTH} nodes.")
    if which == "prefix":
        return (BASE + "x = " + "not " * 100 + "true\n",
                f"Expression nesting depth exceeds {MAX_DELIMITER_DEPTH} levels.")
    if which == "statements":
        return (BASE + "".join(f"x{i} = 1\n" for i in range(MAX_STATEMENTS + 1)),
                f"Statement count exceeds {MAX_STATEMENTS}.")
    raise AssertionError(which)


@pytest.mark.parametrize("entrypoint", [transpile, transpile_full])
@pytest.mark.parametrize("which", [
    "source", "tokens", "characters", "delimiters", "blocks", "ast",
    "prefix", "statements",
])
def test_size_and_depth_budgets_have_stable_located_errors(entrypoint, which: str) -> None:
    source, message = _limit_probe(which)
    with pytest.raises(CompileError) as caught:
        entrypoint(source, filename="limits.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.message == message
    assert diagnostic.location.file == "limits.pine"
    assert diagnostic.location.line >= 1
    assert diagnostic.location.col >= 1
    assert str(caught.value).startswith(
        f"limits.pine:{diagnostic.location.line}:{diagnostic.location.col}: "
    )


def test_elapsed_time_guard_is_a_located_compile_error(monkeypatch) -> None:
    import pineforge_codegen.limits as limits

    ticks = iter([0, MAX_TRANSPILE_SECONDS + 1])
    monkeypatch.setattr(limits, "monotonic", lambda: next(ticks))
    with pytest.raises(CompileError) as caught:
        transpile(BASE, filename="time.pine")
    assert str(caught.value) == (
        f"time.pine:1:1: Transpilation time exceeds {MAX_TRANSPILE_SECONDS} seconds."
    )


@pytest.mark.parametrize("expression", ["(1", "1 2"])
def test_malformed_trace_expression_has_source_location(expression: str) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(BASE + f"// @pf-trace x={expression}\n", filename="trace.pine")
    assert caught.value.diagnostics[0].location.file == "trace.pine"
    assert caught.value.diagnostics[0].location.line == 3


@pytest.mark.parametrize("literal", ["9" * 30, "1e9999"])
def test_numeric_literals_that_broke_cpp_are_located_errors(literal: str) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(BASE + f"x = {literal}\n", filename="number.pine")
    assert str(caught.value) == (
        "number.pine:3:5: Numeric literal exceeds the generated C++ range."
    )


def test_exact_delimiter_budget_is_accepted() -> None:
    transpile(BASE + "x = " + "(" * MAX_DELIMITER_DEPTH + "1" +
              ")" * MAX_DELIMITER_DEPTH + "\n")


def test_existing_security_rejections_and_string_escaping_still_work() -> None:
    for body in (
        "import ../../private",
        'x = request.security("NASDAQ:AAPL", "D", close)',
    ):
        with pytest.raises(CompileError) as caught:
            transpile(BASE + body + "\n", filename="security.pine")
        assert caught.value.diagnostics[0].location.line == 3
    cpp = transpile(BASE + 'msg = "\\\"; system(\\\"bad\\\"); //"\n')
    compile_cpp(cpp, label="escaped_string_injection")
