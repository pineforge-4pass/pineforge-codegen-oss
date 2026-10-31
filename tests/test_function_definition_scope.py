"""Function definitions and variable reads obey their lexical scope."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp, run_emitted_tu


HEADER = '//@version=6\nstrategy("function scope")\n'


REFUSALS = [
    # Two header lines put the late global read at line 3, after "read() => ".
    pytest.param(
        "read() => later\nlater = close\nresult = read()\n",
        "PF-E2053",
        "Global variable 'later' is read before its declaration in a function.",
        3, 11,
        id="global-declared-later",
    ),
    # The block ends on line 5; "result = " occupies its first nine columns.
    pytest.param(
        "if close > open\n    float inside = close\nresult = inside\n",
        "PF-E2054",
        "Block-local variable 'inside' is read outside its block.",
        5, 10,
        id="expired-block-local",
    ),
    # The second float signature starts at line 4, column 1; parameter names do not distinguish it.
    pytest.param(
        "adjust(float value) => value\nadjust(float other) => other + 1.0\n"
        "result = adjust(close)\n",
        "PF-E2055",
        "Function 'adjust' has more than one definition with the same parameter types.",
        4, 1,
        id="duplicate-declared-signature",
    ),
    # "recur(int n) => n <= 0 ? 1.0 : " takes 31 columns before the self-call.
    pytest.param(
        "recur(int n) => n <= 0 ? 1.0 : recur(n - 1)\nresult = recur(2)\n",
        "PF-E2056",
        "Function 'recur' cannot call itself.",
        3, 32,
        id="direct-recursion-with-termination",
    ),
    # The inner definition starts at line 4, after four indentation spaces.
    pytest.param(
        "outer() =>\n    inner() => close\n    inner()\nresult = outer()\n",
        "PF-E2057",
        "Function 'inner' cannot be defined inside a function.",
        4, 5,
        id="nested-function-definition",
    ),
    # The conditional's function starts at line 4, after four spaces.
    pytest.param(
        "if close > open\n    adjust() => close\nresult = adjust()\n",
        "PF-E2058",
        "Function 'adjust' cannot be defined inside a local block.",
        4, 5,
        id="function-definition-in-conditional",
    ),
    # The resolved assignment target is the parameter at line 4, column 5.
    pytest.param(
        "adjust(float value) =>\n    value := value + 1.0\n    value\n"
        "result = adjust(close)\n",
        "PF-E2059",
        "Function parameter 'value' cannot be reassigned.",
        4, 5,
        id="parameter-reassignment",
    ),
    # "result = close." occupies 15 columns before the method name.
    pytest.param(
        "result = close.adjust()\nmethod adjust(float self) => self + 0.25\n",
        "PF-E2060",
        "Method 'adjust' is called before its definition.",
        3, 16,
        id="method-called-before-definition",
    ),
]


@pytest.mark.parametrize("body, code, message, line, column", REFUSALS)
def test_invalid_function_scope_has_a_located_diagnostic(
    body: str, code: str, message: str, line: int, column: int,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body, filename="scope.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == code
    assert diagnostic.message == message
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "scope.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, column)


ACCEPTED = [
    # A declaration above its call makes adjust(2.5) equal 3.5.
    pytest.param(
        "adjust(float value) => value + 1.0\nresult = adjust(2.5)\n",
        id="function-before-call",
    ),
    # An earlier global is visible when the body reads it, yielding 2.5.
    pytest.param(
        "earlier = 2.5\nread() => earlier\nresult = read()\n",
        id="global-before-function",
    ),
    # outer(2.5) calls the earlier inner and yields 3.5.
    pytest.param(
        "inner(float value) => value + 1.0\n"
        "outer(float value) => inner(value)\nresult = outer(2.5)\n",
        id="earlier-function-in-body",
    ),
    # The inside value remains in scope for its read in the same block.
    pytest.param(
        "result = 0.0\nif close > open\n    float inside = 2.5\n"
        "    result := inside\n",
        id="local-read-inside-block",
    ),
    # The builtin close remains an outer binding after its local shadow expires.
    pytest.param(
        "if true\n    float close = 1.0\nresult = close\n",
        id="builtin-survives-local-shadow",
    ),
    # A differently named local adds one to the parameter, yielding 3.5.
    pytest.param(
        "adjust(float value) =>\n    float localValue = value + 1.0\n"
        "    localValue\nresult = adjust(2.5)\n",
        id="different-local-name",
    ),
    # Two distinct names each retain their own definition.
    pytest.param(
        "first(float value) => value\nsecond(float value) => value + 1.0\n"
        "result = first(2.5) + second(2.5)\n",
        id="different-function-names",
    ),
    # The method is visible above its call and adds 0.25.
    pytest.param(
        "method adjust(float self) => self + 0.25\nresult = close.adjust()\n",
        id="method-after-definition",
    ),
    # Only the local changes; value remains the immutable parameter.
    pytest.param(
        "adjust(float value) =>\n    float localValue = value\n"
        "    localValue := localValue + 1.0\n    localValue\n"
        "result = adjust(2.5)\n",
        id="local-reassignment",
    ),
    # The parameter shadows the earlier global, so adjust(2.5) is 3.5.
    pytest.param(
        "float value = 40.0\nadjust(float value) => value + 1.0\n"
        "result = adjust(2.5)\n",
        id="parameter-shadows-global",
    ),
    # A user function called sma still adds one to its float argument.
    pytest.param(
        "sma(float value) => value + 1.0\nresult = sma(close)\n",
        id="builtin-function-name",
    ),
    # A variable named str remains a numeric binding in an arithmetic expression.
    pytest.param(
        "float str = close - open\nresult = str + 1.0\n",
        id="namespace-variable-name",
    ),
    # first(2) reaches second(1), then first(0), which returns 0.25.
    pytest.param(
        "first(int n) => n <= 0 ? 0.25 : second(n - 1)\n"
        "second(int n) => n <= 0 ? close - open : first(n - 1)\n"
        "signalValue = first(2)\n",
        id="mutual-recursion",
    ),
    # The distinct explicit types form overloads rather than duplicate signatures.
    pytest.param(
        "adjust(float value) => value\nadjust(int value) => value + 1\n"
        "result = adjust(close)\n",
        id="distinct-declared-types",
    ),
    # Distinct qualifiers keep the existing overload path even with equal base types.
    pytest.param(
        "pick(simple int value) => value + 100\n"
        "pick(series int value) => value + 900\nresult = pick(7)\n",
        id="distinct-declared-qualifiers",
    ),
    # An inferred call result supplies no independent primitive proof for a new refusal.
    pytest.param(
        "identity(value) => value\nreceiver = identity(close)\n"
        "result = receiver.adjust()\nmethod adjust(float self) => self + 0.25\n",
        id="unproven-method-receiver",
    ),
    # A local with the parameter's spelling is a new symbol, so its write is local.
    pytest.param(
        "adjust(float value) =>\n    if value > 0\n"
        "        float value = 2.0\n        value := value + 1.0\n"
        "    value\nresult = adjust(2.5)\n",
        id="local-shadow-of-parameter",
    ),
]


@pytest.mark.parametrize("body", ACCEPTED)
def test_valid_function_scope_still_transpiles(body: str) -> None:
    assert "class GeneratedStrategy" in transpile(HEADER + body)


COLLISION = HEADER + """adjust(float value) => value + 1.0
flip = bar_index % 2 == 0
float localTotal = 0.0
float pf_safe_adjust = 99.0
if flip
    float adjust = close - open
    localTotal := adjust * 2.0
signalValue = adjust(close - open)
occupiedValue = pf_safe_adjust
"""


DRIVER = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        {10.0, 15.0, 9.0, 12.5, 1.0, 0},
        {20.0, 25.0, 19.0, 24.0, 1.0, 900000},
    };
    for (int count : {1, 2}) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << strategy.signalValue << " " << strategy.localTotal
                  << " " << strategy.occupiedValue << "\n";
    }
}
'''


@pytest.mark.parametrize("name", ("adjust", "measure"))
def test_block_local_named_like_a_function_compiles_and_runs(name: str) -> None:
    cpp = transpile(COLLISION.replace("adjust", name))
    compile_cpp(cpp, label="local and callable namespaces")
    # 12.5-10+1=3.5 and 2*(12.5-10)=5; next bar gives 24-20+1=5 and no local write.
    assert run_emitted_tu(cpp, DRIVER, opt="-O0", label="local and callable namespaces") == (
        "3.5 5 99\n5 0 99\n"
    )


def test_local_collision_uses_the_shared_name_allocator() -> None:
    cpp = transpile(COLLISION)
    # pf_safe_adjust is authored, so the allocator's first free spelling has suffix 2.
    assert "pf_safe_adjust_2" in cpp
    assert "double adjust(double value)" in cpp


def test_buffered_local_keeps_the_existing_member_collision_path() -> None:
    cpp = transpile(HEADER + """adjust(float value) => value + 1.0
accumulate(float value) =>
    float adjust = 0.0
    adjust := nz(adjust[1]) + value
result = accumulate(close)
observed = adjust(result)
""")
    # The local's float history keeps its double Series and the callable's existing prefix.
    assert "Series<double> adjust;" in cpp
    assert "_fn_adjust(" in cpp
    assert "pf_safe_adjust" not in cpp


def test_function_variant_already_keeps_its_call_apart_from_a_local() -> None:
    cpp = transpile(HEADER + """adjust(float value) => ta.sma(value, 2)
if true
    float adjust = 0.0
result = adjust(close)
""")
    # One written stateful call uses adjust_cs0, which is already distinct from the local.
    assert "adjust_cs0(" in cpp
    assert "pf_safe_adjust" not in cpp
