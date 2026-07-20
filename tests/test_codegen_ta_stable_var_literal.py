"""TA constructor lengths backed by immutable persistent literals.

Pine permits a top-level ``var int`` initialized from a literal to feed a TA
length when that binding is never reassigned.  The value is fixed for the
entire run, so it has the same constructor-sizing semantics as an ordinary
literal alias.  Keep the admission deliberately narrower than the general
stable-runtime reset path: mutable, history-promoted, and dynamic persistent
bindings must continue to fail closed.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp


def _constructor_initializers(cpp: str) -> str:
    match = re.search(r"explicit GeneratedStrategy\(\) : ([^\n]+)", cpp)
    assert match is not None, "generated strategy constructor not found"
    return match.group(1)


def _assert_exact_pivot_ctor_rejection(
    source: str,
    *,
    filename: str,
    expected_line: int,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename=filename)

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.CODEGEN
    assert (
        diagnostic.location.file,
        diagnostic.location.line,
        diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (filename, expected_line, 17, 18)
    assert diagnostic.message == (
        "Unsupported TA constructor length 'p' for ta::PivotHigh: it is "
        "neither a compile-time constant nor derived from an input, so "
        "PineForge cannot size the indicator buffer."
    )
    assert diagnostic.hint == (
        "Use a literal, an input.*() value, or arithmetic over those for TA "
        "lengths."
    )


def _assert_parse_recovery_fences_sma(source: str, *, expected_line: int) -> None:
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    assert (program.annotations or {}).get("parse_recovery_count") == 1

    with pytest.raises(CompileError) as caught:
        transpile(source, filename="stable-var-parse-recovery.pine")

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.phase is Phase.CODEGEN
    assert diagnostic.location.line == expected_line
    assert diagnostic.message == (
        "Unsupported TA constructor length 'p' for ta::SMA: it is neither a "
        "compile-time constant nor derived from an input, so PineForge cannot "
        "size the indicator buffer."
    )


@pytest.mark.parametrize(
    "declaration",
    [
        "int pivotLength = 5",
        "var int pivotLength = 5",
    ],
)
def test_literal_alias_sizes_both_pivot_sides(declaration: str) -> None:
    source = f"""//@version=6
strategy("stable literal pivot lengths")
{declaration}
highPivot = ta.pivothigh(high, pivotLength, pivotLength)
lowPivot = ta.pivotlow(low, pivotLength, pivotLength)
"""

    cpp = transpile(source)
    initializers = _constructor_initializers(cpp)
    assert re.search(r"_ta_pivothigh_\d+\(5, 5\)", initializers)
    assert re.search(r"_ta_pivotlow_\d+\(5, 5\)", initializers)
    assert "ta::PivotHigh(1, 1)" not in cpp
    assert "ta::PivotLow(1, 1)" not in cpp
    compile_cpp(cpp, label=f"stable-var-pivots-{declaration.startswith('var')}")


@pytest.mark.parametrize(
    "prefix",
    [
        "x = ta.pivothigh(high, p, p)\n",
        "prior = p\nvar int p = 5\nx = ta.pivothigh(high, p, p)\n",
    ],
    ids=["ta-use", "ordinary-use"],
)
def test_literal_declaration_cannot_retroactively_authorize_earlier_use(
    prefix: str,
) -> None:
    if prefix.startswith("x ="):
        body = prefix + "var int p = 5\n"
        expected_line = 3
    else:
        body = prefix
        expected_line = 5
    source = '//@version=6\nstrategy("predecl")\n' + body

    _assert_exact_pivot_ctor_rejection(
        source,
        filename="stable-var-predecl.pine",
        expected_line=expected_line,
    )


@pytest.mark.parametrize(
    "hidden_history",
    [
        "prior = p[1]",
        "read(int prior = p[1]) => prior",
        "type Sample\n    int prior = p",
        "type Sample\n    int prior = p[1]",
        "enum Choice\n    First = p[1]",
        (
            "type Sample\n"
            "    int value = 0\n"
            "method read(Sample self, int prior = p[1]) => prior"
        ),
        "for i = p[1] to 2\n    plot(i)",
        "for i = 0 to p[1]\n    plot(i)",
        "for i = 0 to 2 by p[1]\n    plot(i)",
        "for item in array.from(p[1])\n    plot(item)",
        "// @pf-trace prior=p[1]",
        "history(int q = p) => q[1]\nprior = history()",
        "method history(int self) => self[1]\nprior = p.history()",
        "leaf(int r) => r[1]\nouter(int q) => leaf(q)\nprior = outer(p)",
        (
            "leaf(int s) => s[1]\n"
            "outer(int r) =>\n"
            "    int q = r\n"
            "    leaf(q)\n"
            "prior = outer(p)"
        ),
        (
            "method history(int self) => self[1]\n"
            "outer(int q) => q.history()\n"
            "prior = outer(p)"
        ),
        "identity(int q = p) => q\nprior = identity()",
        "history(int q) => q[1]\n// @pf-trace prior=history(p)",
    ],
    ids=[
        "ordinary-ast",
        "function-param-default-annotation",
        "type-field-plain-default",
        "type-field-default",
        "enum-member-value",
        "method-param-default-annotation",
        "for-start",
        "for-end",
        "for-step",
        "for-in-iterable",
        "post-analyzer-pf-trace",
        "default-param-transitive-history",
        "primitive-method-receiver-transitive-history",
        "nested-helper-transitive-history",
        "local-alias-sensitive-callee",
        "nested-method-receiver-transitive-history",
        "nonhistory-default-mask",
        "pf-trace-helper-transitive-history",
    ],
)
def test_any_hidden_history_use_fences_stable_literal_admission(
    hidden_history: str,
) -> None:
    source = (
        '//@version=6\nstrategy("hidden history")\nvar int p = 5\n'
        + hidden_history
        + "\nx = ta.pivothigh(high, p, p)\n"
    )
    expected_line = source.splitlines().index(
        "x = ta.pivothigh(high, p, p)"
    ) + 1

    _assert_exact_pivot_ctor_rejection(
        source,
        filename="stable-var-hidden-history.pine",
        expected_line=expected_line,
    )


@pytest.mark.parametrize(
    "declaration",
    [
        "unused(int q) => q",
        "type Sample\n    int value = 0",
        "enum Choice\n    First",
    ],
    ids=["function", "type", "enum"],
)
def test_any_user_declaration_fences_narrow_literal_admission(
    declaration: str,
) -> None:
    source = (
        '//@version=6\nstrategy("declaration fence")\nvar int p = 5\n'
        + declaration
        + "\nx = ta.pivothigh(high, p, p)\n"
    )
    expected_line = source.splitlines().index(
        "x = ta.pivothigh(high, p, p)"
    ) + 1

    _assert_exact_pivot_ctor_rejection(
        source,
        filename="stable-var-declaration-fence.pine",
        expected_line=expected_line,
    )


def test_dropped_primitive_method_declaration_fences_unrelated_receiver_mask() -> None:
    source = '''//@version=6
strategy("parse recovery receiver mask")
method id(int self) => self
x = 1
y = x.id()
var int p = 5
z = ta.sma(close, p)
'''

    _assert_parse_recovery_fences_sma(source, expected_line=7)


def test_dropped_collection_method_cannot_collide_with_builtin_semantics() -> None:
    source = '''//@version=6
strategy("parse recovery builtin collision")
method push(array<int> self, int x) => array.unshift(self, x)
var array<int> a = array.new<int>()
a.push(1)
var int p = 5
z = ta.sma(close, p)
'''

    _assert_parse_recovery_fences_sma(source, expected_line=7)


@pytest.mark.parametrize(
    "bad_fragment",
    [
        "var int = 1",
        "if close > open\n    var int = 1",
    ],
    ids=["top-level", "nested-block"],
)
def test_any_unrelated_parse_recovery_fences_literal_admission(
    bad_fragment: str,
) -> None:
    source = (
        '//@version=6\nstrategy("parse recovery fence")\n'
        + bad_fragment
        + "\nvar int p = 5\nz = ta.sma(close, p)\n"
    )
    expected_line = source.splitlines().index("z = ta.sma(close, p)") + 1

    _assert_parse_recovery_fences_sma(source, expected_line=expected_line)


def test_clean_direct_source_has_no_parse_recovery_annotation() -> None:
    source = '''//@version=6
strategy("lossless parse control")
var int p = 5
z = ta.sma(close, p)
'''
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    assert not program.annotations

    cpp = transpile(source)
    assert re.search(r"_ta_sma_\d+\(5\)", _constructor_initializers(cpp))
    compile_cpp(cpp, label="stable-var-lossless-parse-control")


@pytest.mark.parametrize(
    "push_call",
    [
        "array.push(a, p)",
        "a.push(p)",
    ],
    ids=["namespace", "known-array-receiver"],
)
def test_supported_array_calls_do_not_create_a_recovery_fence(
    push_call: str,
) -> None:
    source = f'''//@version=6
strategy("supported array control")
var int p = 5
var array<int> a = array.new<int>()
{push_call}
z = ta.sma(close, p)
'''
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    assert not program.annotations

    cpp = transpile(source)
    assert re.search(r"_ta_sma_\d+\(5\)", _constructor_initializers(cpp))
    compile_cpp(cpp, label=f"stable-var-array-{push_call.startswith('array.')}")


def test_any_recursive_callable_invocation_fences_stable_literal() -> None:
    source = '''//@version=6
strategy("recursive callable control")
identity(q) => identity(q)
var int p = 5
unused = identity(p)
x = ta.sma(close, p)
'''

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


def test_even_immutable_helper_boundary_fails_closed() -> None:
    source = '''//@version=6
strategy("immutable helper parameter")
requested(int q) => ta.sma(close, q)
var int p = 5
observed = requested(p)
'''

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


@pytest.mark.parametrize(
    "reassignment",
    [
        "q := 7",
        "q := int(close)",
    ],
    ids=["literal-reassignment", "series-reassignment"],
)
def test_callable_parameter_reassignment_fences_literal_specialization(
    reassignment: str,
) -> None:
    source = f'''//@version=6
strategy("reassigned helper parameter")
var int p = 5
requested(int q) =>
    {reassignment}
    ta.sma(close, q)
observed = requested(p)
'''

    with pytest.raises(CompileError) as caught:
        transpile(source)

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.phase is Phase.CODEGEN
    assert diagnostic.message == (
        "Unsupported TA constructor length 'p' for ta::SMA: it is neither a "
        "compile-time constant nor derived from an input, so PineForge cannot "
        "size the indicator buffer."
    )


@pytest.mark.parametrize(
    "helper_body",
    [
        (
            "    float out = na\n"
            "    for q = 1 to 3\n"
            "        out := ta.sma(close, q)\n"
            "    out"
        ),
        (
            "    float out = na\n"
            "    if close > open\n"
            "        int q = 7\n"
            "        out := ta.sma(close, q)\n"
            "    out"
        ),
        "    [q, z] = [7, 8]\n    ta.sma(close, q)",
    ],
    ids=["loop-binder", "block-local", "tuple-binder"],
)
def test_callable_parameter_rebinding_fences_literal_specialization(
    helper_body: str,
) -> None:
    source = (
        '//@version=6\nstrategy("rebound helper parameter")\n'
        "var int p = 5\n"
        "requested(int q) =>\n"
        + helper_body
        + "\nobserved = requested(p)\n"
    )

    with pytest.raises(CompileError) as caught:
        transpile(source)

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.phase is Phase.CODEGEN
    assert diagnostic.message == (
        "Unsupported TA constructor length 'p' for ta::SMA: it is neither a "
        "compile-time constant nor derived from an input, so PineForge cannot "
        "size the indicator buffer."
    )


@pytest.mark.parametrize(
    "binding",
    [
        "var int pivotLength = 5\npivotLength := close > open ? 6 : 7",
        "var int pivotLength = int(close)",
        "var int pivotLength = 5\npreviousLength = pivotLength[1]",
        "var pivotLength = 5.0",
        "var float pivotLength = 5",
    ],
    ids=[
        "reassigned",
        "dynamic-initializer",
        "history-promoted",
        "inferred-float-literal",
        "declared-float",
    ],
)
def test_nonimmutable_persistent_lengths_remain_rejected(binding: str) -> None:
    source = f"""//@version=6
strategy("persistent length guard")
{binding}
highPivot = ta.pivothigh(high, pivotLength, pivotLength)
"""

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


def test_same_spelled_callable_var_does_not_capture_global_literal() -> None:
    source = """//@version=6
strategy("persistent length shadow guard")
var int pivotLength = 5
readPivot() =>
    var int pivotLength = 7
    ta.pivothigh(high, pivotLength, pivotLength)
highPivot = readPivot()
"""

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


def test_same_spelled_block_local_does_not_capture_global_literal() -> None:
    source = """//@version=6
strategy("persistent block length shadow guard")
var int pivotLength = 5
if close > open
    int pivotLength = int(close)
    highPivot = ta.pivothigh(high, pivotLength, pivotLength)
"""

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


def test_same_spelled_loop_binder_does_not_capture_global_literal() -> None:
    source = """//@version=6
strategy("persistent loop length shadow guard")
var int pivotLength = 5
for pivotLength = 1 to int(close)
    highPivot = ta.pivothigh(high, pivotLength, pivotLength)
"""

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


@pytest.mark.parametrize(
    "request_call",
    [
        'request.security(syminfo.tickerid, "60", ta.sma(close, L))',
        'request.security_lower_tf(syminfo.tickerid, "1", ta.sma(close, L))',
    ],
    ids=["direct", "lower-tf"],
)
def test_requested_context_admits_only_proven_stable_var_literal_ctor(
    request_call: str,
) -> None:
    source = (
        '//@version=6\nstrategy("requested stable literal")\n'
        + "var int L = 5\n"
        + f"requested = {request_call}\n"
    )

    cpp = transpile(source)
    initializers = _constructor_initializers(cpp)
    assert re.search(r"_ta_sma_\d+\(5\)", initializers)
    assert re.search(r"_sec\d+__ta_sma_\d+\(5\)", initializers)
    assert "ta::SMA(1)" not in cpp
    assert not re.search(r"_ta_sma_\d+\(1\)", cpp)
    assert not re.search(r"_sec\d+__ta_sma_\d+\(1\)", cpp)
    compile_cpp(
        cpp,
        label=(
            "stable-var-requested-"
            f"{request_call.startswith('request.security_lower_tf')}"
        ),
    )


def test_requested_helper_boundary_fails_closed() -> None:
    source = '''//@version=6
strategy("requested stable literal helper fence")
requestedSma(src, length) => ta.sma(src, length)
var int L = 5
requested = request.security(
    syminfo.tickerid, "60", requestedSma(close, L))
'''

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


@pytest.mark.parametrize(
    "boundary",
    [
        "var int L = 5\nL := close > open ? 6 : 7",
        "var int L = 5\nprior = L[1]",
        "shadow(int L) => L\nvar int L = 5",
    ],
    ids=["reassigned", "history", "same-spelled-parameter"],
)
def test_requested_context_does_not_exempt_unadmitted_literal(boundary: str) -> None:
    source = f'''//@version=6
strategy("requested stable literal negative")
{boundary}
requested = request.security(syminfo.tickerid, "60", ta.sma(close, L))
'''

    with pytest.raises(CompileError) as caught:
        transpile(source)

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.phase is Phase.CODEGEN
    assert diagnostic.message == (
        "Unsupported TA constructor length 'L' for ta::SMA: it is neither a "
        "compile-time constant nor derived from an input, so PineForge cannot "
        "size the indicator buffer."
    )


def test_input_backed_parameter_keeps_override_aware_reset() -> None:
    source = """//@version=6
strategy("input parameter boundary")
readPivot(int length) =>
    ta.pivothigh(high, length, length)
pivotLength = input.int(5, "Pivot Length", minval=1)
highPivot = readPivot(pivotLength)
"""

    cpp = transpile(source)
    initializers = _constructor_initializers(cpp)
    assert re.search(r"_ta_pivothigh_\d+\(5, 5\)", initializers)
    assert (
        'ta::PivotHigh(get_input_int("Pivot Length", 5), '
        'get_input_int("Pivot Length", 5))'
    ) in cpp
    compile_cpp(cpp, label="stable-var-input-parameter-boundary")


def test_literal_parameter_keeps_existing_static_constructor_path() -> None:
    source = """//@version=6
strategy("literal parameter boundary")
readPivot(int length) =>
    ta.pivotlow(low, length, length)
lowPivot = readPivot(5)
"""

    cpp = transpile(source)
    assert re.search(
        r"_ta_pivotlow_\d+\(5, 5\)", _constructor_initializers(cpp)
    )
    assert "get_input_int(" not in cpp
    compile_cpp(cpp, label="stable-var-literal-parameter-boundary")
