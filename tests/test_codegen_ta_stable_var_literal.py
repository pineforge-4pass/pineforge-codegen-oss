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
from pineforge_codegen.ast_nodes import MethodDef
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests import _series_sma_oracle as oracle
from tests._compile import compile_cpp, run_emitted_tu


def _constructor_initializers(cpp: str) -> str:
    match = re.search(r"explicit GeneratedStrategy\(\) : ([^\n]+)", cpp)
    assert match is not None, "generated strategy constructor not found"
    return match.group(1)


# The call the fences below are read through: ``ta.wma`` sizes its buffer in
# the constructor and has no series-length lowering, so a length the literal
# admission does not take is refused there. ``ta.pivothigh`` was this call
# until a series leftbars / rightbars was lowered per bar
# (tests/test_codegen_ta_dynamic_length.py): an unadmitted ``var`` strength is
# now read on every call instead of being refused.
_FENCED_CALL = "x = ta.wma(close, p)"


def _assert_exact_ctor_rejection(
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
    # The call's opening parenthesis: column 11 of ``x = ta.wma(close, p)``.
    assert (
        diagnostic.location.file,
        diagnostic.location.line,
        diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (filename, expected_line, 11, 12)
    assert diagnostic.message == (
        "Unsupported TA constructor length 'p' for ta::WMA: it is "
        "neither a compile-time constant nor derived from an input, so "
        "PineForge cannot size the indicator buffer."
    )
    assert diagnostic.hint == (
        "Use a literal, an input.*() value, or arithmetic over those for TA "
        "lengths."
    )


def _assert_parse_error_precedes_sma(source: str, *, expected_line: int) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="stable-var-parse-recovery.pine")

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.phase is Phase.PARSER
    assert diagnostic.location.line == expected_line
    assert "Expected IDENT" in diagnostic.message


def _assert_method_declaration_does_not_fence_sma(source: str, control: str) -> None:
    # A method declaration used to close the whole-script admission (issue #422:
    # TradingView accepts both scripts and computes the 5-bar average, as
    # captured). It no longer does:
    # the literal length is folded exactly as in the script without the method.
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    assert (program.annotations or {}).get("parse_recovery_count", 0) == 0
    assert any(isinstance(node, MethodDef) for node in program.body)

    cpp = transpile(source, filename="stable-var-method-declaration.pine")
    twin = transpile(control, filename="stable-var-method-free-control.pine")
    pattern = r"_ta_sma_\d+\([^)]*\)"
    folded = re.search(pattern, _constructor_initializers(cpp))
    expected = re.search(pattern, _constructor_initializers(twin))
    assert folded is not None and expected is not None
    assert folded.group(0) == expected.group(0)
    compile_cpp(cpp, label="stable-var-method-declaration")


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
        expected_col = 24
    else:
        body = prefix
        expected_col = 9
    source = '//@version=6\nstrategy("predecl")\n' + body

    # name_resolution/ref_top_global (CE10272): a later declaration cannot bind
    # the first authored read, including an argument to a TA constructor.
    filename = "stable-var-predecl.pine"
    with pytest.raises(CompileError) as caught:
        transpile(source, filename=filename)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == "PF-E2075"
    assert diagnostic.args == {"name": "p"}
    assert diagnostic.message == (
        "Variable 'p' is not declared in the current lexical scope before this use."
    )
    assert diagnostic.hint is None
    assert (
        diagnostic.location.file,
        diagnostic.location.line,
        diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (filename, 3, expected_col, expected_col + 1)


@pytest.mark.parametrize(
    "hidden_history",
    [
        "prior = p[1]",
        "read(int prior = p[1]) => prior",
        # A variable is not a valid record field default (TradingView CE10133), so the
        # variable is read through the constructor instead.
        "type Sample\n    int prior = 0\nsample = Sample.new(prior = p)",
        "type Sample\n    int prior = 0\nsample = Sample.new(prior = p[1])",
        "enum Choice\n    First = p[1]",
        (
            "type Sample\n"
            "    int value = 0\n"
            "method read(Sample self, int prior = p[1]) => self, prior"
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
        + f"\n{_FENCED_CALL}\n"
    )
    expected_line = source.splitlines().index(_FENCED_CALL) + 1

    _assert_exact_ctor_rejection(
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
        + f"\n{_FENCED_CALL}\n"
    )
    expected_line = source.splitlines().index(_FENCED_CALL) + 1

    _assert_exact_ctor_rejection(
        source,
        filename="stable-var-declaration-fence.pine",
        expected_line=expected_line,
    )


def test_primitive_method_declaration_does_not_fence_narrow_literal_admission() -> None:
    source = '''//@version=6
strategy("parse recovery receiver mask")
method id(int self) => self
x = 1
y = x.id()
var int p = 5
z = ta.sma(close, p)
'''
    control = '''//@version=6
strategy("parse recovery receiver mask")
var int p = 5
z = ta.sma(close, p)
'''

    _assert_method_declaration_does_not_fence_sma(source, control)


def test_collection_method_declaration_does_not_fence_narrow_literal_admission() -> None:
    source = '''//@version=6
strategy("parse recovery builtin collision")
method push(array<int> self, int x) => array.unshift(self, x)
var array<int> a = array.new<int>()
a.push(1)
var int p = 5
z = ta.sma(close, p)
'''
    control = '''//@version=6
strategy("parse recovery builtin collision")
var int p = 5
z = ta.sma(close, p)
'''

    _assert_method_declaration_does_not_fence_sma(source, control)


@pytest.mark.parametrize(
    "bad_fragment",
    [
        "var int = 1",
        "if close > open\n    var int = 1",
    ],
    ids=["top-level", "nested-block"],
)
def test_any_unrelated_parse_error_precedes_literal_admission(
    bad_fragment: str,
) -> None:
    source = (
        '//@version=6\nstrategy("parse recovery fence")\n'
        + bad_fragment
        + "\nvar int p = 5\nz = ta.sma(close, p)\n"
    )
    expected_line = [line.strip() for line in source.splitlines()].index("var int = 1") + 1

    _assert_parse_error_precedes_sma(source, expected_line=expected_line)


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
identity(q) => identity_next(q)
identity_next(q) => identity(q)
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
        "q += 7",
        "q -= 7",
        "q *= 7",
        "q /= 7",
        "q %= 7",
    ],
    ids=[
        "literal-reassignment",
        "series-reassignment",
        "add-compound-reassignment",
        "subtract-compound-reassignment",
        "multiply-compound-reassignment",
        "divide-compound-reassignment",
        "modulo-compound-reassignment",
    ],
)
def test_callable_ta_length_rejects_parameter_reassignment(
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
        transpile(source, filename="reassigned-length.pine")

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2059"
    assert diagnostic.message == "Function parameter 'q' cannot be reassigned."
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "reassigned-length.pine"
    # The header, p and requested take four lines; four spaces precede q.
    assert (diagnostic.location.line, diagnostic.location.col) == (5, 5)


def _per_bar(cpp: str, members, label: str):
    return oracle.per_bar_rows(
        run_emitted_tu(cpp, oracle.per_bar_driver(members), opt="-O0", label=label),
        len(members))


# What ``requested(p)`` answers under each rebinding of ``q`` below, on the
# eighteen bars of tests/_series_sma_oracle.py: the constant-length reference
# that shares the call's clock, its member, how its rows give the column, and
# the table. A row is (window length, sum of the values in the window), with
# no sum where the call answers na. No table is the call site's literal: a
# window of ``p`` = 5 first answers where five values are held, and averages
# five of them.
REBOUND_LENGTHS = {
    # ``for q = 1 to 3`` calls ta.sma three times on every bar, and each call
    # is given the bar's close. The value kept is the third call's (q = 3):
    # the mean of that close three times over.
    "loop-binder": (
        oracle.LOOP_REFERENCE, ("picked",), lambda rows, table: oracle.column(rows),
        ((3, 30), (3, 36), (3, 33), (3, 45), (3, 42), (3, 54),
         (3, 39), (3, 51), (3, 60), (3, 48), (3, 66), (3, 57),
         (3, 75), (3, 63), (3, 72), (3, 84), (3, 69), (3, 81)),
    ),
    # ``int q = 7`` inside ``if close > open``: the call runs on the rising
    # bars only (0, 1, 3, 5, 7, 8, 10, 12, 14, 15, 17) and is given their
    # closes (10, 12, 15, 18, 17, 20, 22, 25, 24, 28, 27). The seventh of them
    # is bar 10; every other bar leaves ``out`` na.
    "block-local": (
        oracle.RISING_BAR_REFERENCE, ("gated",), lambda rows, table: oracle.column(rows),
        ((7, None), (7, None), (7, None), (7, None), (7, None), (7, None),
         (7, None), (7, None), (7, None), (7, None), (7, 114), (7, None),
         (7, 129), (7, None), (7, 141), (7, 154), (7, None), (7, 163)),
    ),
}


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
        "    [q, z] = if true\n        [7, 8]\n    else\n        [8, 7]\n    ta.sma(close, q)",
    ],
    ids=["loop-binder", "block-local", "tuple-binder"],
)
def test_callable_parameter_rebinding_fences_literal_specialization(
    helper_body: str, request: pytest.FixtureRequest,
) -> None:
    source = (
        '//@version=6\nstrategy("rebound helper parameter")\n'
        "var int p = 5\n"
        "requested(int q) =>\n"
        + helper_body
        + "\nobserved = requested(p)\n"
    )

    case = request.node.callspec.id
    if case == "tuple-binder":
        with pytest.raises(CompileError) as caught:
            transpile(source, filename="tuple-binder.pine")

        [diagnostic] = caught.value.diagnostics
        assert diagnostic.code == "PF-E2096"
        assert diagnostic.message == (
            "Tuple declaration for 'q' collides with a parameter in the same callable scope."
        )
        assert diagnostic.level is Level.ERROR
        assert diagnostic.phase is Phase.ANALYZER
        assert diagnostic.location.file == "tuple-binder.pine"
        assert (diagnostic.location.line, diagnostic.location.col) == (5, 5)
        return

    reference, members, column, windows = REBOUND_LENGTHS[case]
    # The table first, against a constant-length call on the same clock.
    fixed = _per_bar(transpile(reference), members, f"constant_length_{case}")
    oracle.assert_per_bar(
        column(fixed, windows), windows, "the constant-length reference")

    observed = _per_bar(transpile(source), ("observed",), f"rebound_{case}")
    oracle.assert_per_bar(oracle.column(observed), windows, "requested(p)")


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
    # ``ta.wma``: a constructor-sized call with no series-length lowering
    # (``_FENCED_CALL``). The binding keeps its spelling.
    source = f"""//@version=6
strategy("persistent length guard")
{binding}
average = ta.wma(close, pivotLength)
"""

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


def test_same_spelled_callable_var_does_not_capture_global_literal() -> None:
    source = """//@version=6
strategy("persistent length shadow guard")
var int pivotLength = 5
readAverage() =>
    var int pivotLength = 7
    ta.wma(close, pivotLength)
average = readAverage()
"""

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


def test_same_spelled_block_local_does_not_capture_global_literal() -> None:
    source = """//@version=6
strategy("persistent block length shadow guard")
var int pivotLength = 5
if close > open
    int pivotLength = int(close)
    average = ta.wma(close, pivotLength)
"""

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(source)


def test_same_spelled_loop_binder_does_not_capture_global_literal() -> None:
    source = """//@version=6
strategy("persistent loop length shadow guard")
var int pivotLength = 5
for pivotLength = 1 to int(close)
    average = ta.wma(close, pivotLength)
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
        "Unsupported TA constructor length 'L' for ta.sma: it is neither a "
        "compile-time constant nor derived from an input, so PineForge cannot "
        "size the indicator buffer. TradingView accepts a series length for "
        "ta.sma(); PineForge does not support it yet."
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
