"""Untrusted Pine must fail at a Pine location or emit compilable C++."""

from __future__ import annotations

import sys

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.codegen.helpers import CPP_KEYWORDS
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import KEYWORDS
from pineforge_codegen.limits import (
    MAX_NESTING_DEPTH, MAX_SOURCE_CHARS, MAX_TRANSPILE_SECONDS,
    RECURSION_HEADROOM,
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


N = MAX_NESTING_DEPTH


def _limit_probe(which: str) -> tuple[str, str]:
    if which == "source":
        return (BASE + "//" + "x" * MAX_SOURCE_CHARS,
                f"Source size exceeds {MAX_SOURCE_CHARS} characters.")
    if which == "terms":
        return (BASE + "x = " + " + ".join(["1"] * 10000) + "\n",
                f"AST nesting depth exceeds {N} nodes.")
    if which == "delimiters":
        return (BASE + "x = " + "(" * 1000 + "1" + ")" * 1000 + "\n",
                f"Delimiter nesting depth exceeds {N} levels.")
    if which == "blocks":
        return (BASE + "".join("    " * i + "if true\n" for i in range(N + 2)),
                f"Block nesting depth exceeds {N} levels.")
    if which == "ast":
        return (BASE + "x = " + " + ".join(["1"] * (N + 2)) + "\n",
                f"AST nesting depth exceeds {N} nodes.")
    if which == "postfix":
        return (BASE + "a = array.from(1.0)\nb = a" + ".copy()" * N + "\n",
                f"AST nesting depth exceeds {N} nodes.")
    if which == "prefix":
        return (BASE + "x = " + "not " * (N + 1) + "true\n",
                f"Nesting depth exceeds {N} levels.")
    if which == "ternary":
        return (BASE + "x = " + "true ? 1 : " * (N + 1) + "0\n",
                f"Nesting depth exceeds {N} levels.")
    if which == "else_if":
        return (BASE + "x = 0\nif false\n    x := 1\n"
                + "else if false\n    x := 1\n" * (N + 1),
                f"Nesting depth exceeds {N} levels.")
    raise AssertionError(which)


@pytest.mark.parametrize("entrypoint", [transpile, transpile_full])
@pytest.mark.parametrize("which", [
    "source", "terms", "delimiters", "blocks", "ast", "postfix", "prefix",
    "ternary", "else_if",
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


def test_every_nesting_kind_at_once_is_a_located_error() -> None:
    # Loops, an else-if ladder, nested calls and prefix operators, each close
    # to the budget: the parser's recursion stays inside its headroom.
    depth = N - 12
    ind = "    "
    source = BASE + "x = 0\n" + "".join(
        ind * i + f"for i{i} = 0 to 0\n" for i in range(depth))
    source += ind * depth + "if false\n" + ind * (depth + 1) + "x := 1\n"
    source += (ind * depth + "else if false\n" + ind * (depth + 1) + "x := 1\n") * depth
    source += (ind * depth + "else if " + "math.abs(" * depth + "not " * depth
               + "true" + ")" * depth + "\n" + ind * (depth + 1) + "x := 2\n")
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="nesting.pine")
    assert caught.value.diagnostics[0].message == f"Nesting depth exceeds {N} levels."
    assert caught.value.diagnostics[0].location.line > depth


def test_recursion_headroom_is_raised_and_never_lowered() -> None:
    previous = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(1000)
        transpile(BASE)
        assert sys.getrecursionlimit() == RECURSION_HEADROOM
        sys.setrecursionlimit(RECURSION_HEADROOM + 1)
        transpile(BASE)
        assert sys.getrecursionlimit() == RECURSION_HEADROOM + 1
    finally:
        sys.setrecursionlimit(max(previous, RECURSION_HEADROOM))


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


def test_nesting_budget_admits_its_last_level() -> None:
    # The statement's own expression is a level; its brackets fill the rest.
    transpile(BASE + "x = " + "(" * (N - 1) + "1" + ")" * (N - 1) + "\n")


def _tradingview_limits_source() -> str:
    """TradingView: up to 1,000 variables in each scope, and a compilation
    request of at most 5MB.  Fill the global scope and one local block, then
    pad the source to the source budget."""
    code = BASE + "".join(f"float g{i} = close * {i + 1}\n" for i in range(1000))
    code += "if close > open\n"
    code += "".join(f"    float l{i} = g{i} - open\n" for i in range(1000))
    code += '    if l999 > l0\n        strategy.entry("L", strategy.long)\n'
    line = "// " + "x" * 96 + "\n"
    code += line * ((MAX_SOURCE_CHARS - len(code)) // len(line))
    rest = MAX_SOURCE_CHARS - len(code)
    return code + "//" + "x" * (rest - 3) + "\n"


@pytest.mark.parametrize("shape", ["ternary500", "elseif500", "array2000", "tradingview_limits"])
def test_tradingview_sized_inputs_transpile_and_compile(shape: str) -> None:
    if shape == "ternary500":
        source = BASE + "x = " + "true ? 1 : " * 500 + "501\n"
    elif shape == "elseif500":
        source = (BASE + "x = 0\nif false\n    x := 1\n" +
                  "else if false\n    x := 1\n" * 499 +
                  "else\n    x := 2\n")
    elif shape == "array2000":
        source = BASE + "a = array.from(" + ",".join(["1"] * 2000) + ")\n"
    else:
        source = _tradingview_limits_source()
        assert len(source) == MAX_SOURCE_CHARS
        with pytest.raises(CompileError, match="Source size exceeds"):
            transpile(source + "\n", filename=f"{shape}.pine")
    cpp = transpile(source, filename=f"{shape}.pine")
    compile_cpp(cpp, label=shape)


def test_short_ternary_chains_keep_their_nested_spelling() -> None:
    cpp = transpile(BASE + "c = close > open\nx = c ? 1 : c ? 2 : 0\n")
    assert "((c) ? (1) : (((c) ? (2) : (0))))" in cpp


def test_nested_calls_cost_linear_time(monkeypatch) -> None:
    # Each call's written argument order aliased its arguments, and the AST
    # walkers visited both: f(f(f(...))) cost 2**depth.
    import pineforge_codegen.limits as limits

    monkeypatch.setattr(limits, "MAX_TRANSPILE_SECONDS", 20)
    transpile(BASE + "x = " + "math.abs(" * 40 + "close" + ")" * 40 + "\n")
    transpile(BASE + "f(v) => v + 1\nx = " + "f(" * 40 + "close" + ")" * 40 + "\n")


@pytest.mark.parametrize("shape", ["method_chain", "var_declarations"])
def test_slow_passes_stop_at_the_time_budget(monkeypatch, shape: str) -> None:
    import time
    import pineforge_codegen.limits as limits

    monkeypatch.setattr(limits, "MAX_TRANSPILE_SECONDS", 2)
    if shape == "method_chain":
        # Type inference re-infers every receiver of a method chain.
        source = BASE + "a = array.from(1.0)\nb = a" + ".copy()" * 60 + "\n"
    else:
        # Each ``var`` member declaration rescans all of them; the passes
        # before code generation take well under a second.
        source = BASE + "".join(f"var float v{i} = 0.0\n" for i in range(20000))
    started = time.monotonic()
    try:
        transpile(source, filename="slow.pine")
    except CompileError as exc:
        assert str(exc).startswith("slow.pine:")
        assert exc.diagnostics[0].message == "Transpilation time exceeds 2 seconds."
    # Either the pass got faster or the budget stopped it; it never hangs.
    assert time.monotonic() - started < 2 + 10


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
