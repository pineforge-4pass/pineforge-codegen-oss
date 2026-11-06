"""A method must reference its receiver; other unused parameters stay valid."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import MethodDef
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.library_inline import inline_libraries
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


SOURCE = '''//@version=6
strategy("Independent method and local-alias helper requests", currency=currency.NONE, initial_capital=1000000)
// Public synthetic source: local aliases forward the receiver and timeframe.
method prism_read(int tag, string tf = timeframe.period) =>
    string method_span = tf
    request.security(syminfo.tickerid, method_span, bar_index)

through_local(int origin_tag, string selected_span) =>
    int alias_tag = origin_tag
    string alias_span = selected_span
    alias_tag.prism_read(alias_span)

int dispatch_tag = 19
string dynamic_span = high - close > close - low ? "420" : "480"
int observed_fold = dispatch_tag.prism_read() + 3 * through_local(dispatch_tag, dynamic_span)
qty_readout = 1 + observed_fold % 5
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("prism", strategy.long, qty=qty_readout)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("prism")
'''


def _assert_unused(
    source, name, line, *, filename="method.pine", expected_file=None, **kwargs,
):
    with pytest.raises(CompileError) as caught:
        transpile(source, filename=filename, **kwargs)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E5087"
    assert diagnostic.args == {"name": name}
    assert diagnostic.message == (
        f'The first method parameter "{name}" is never used in the method body.'
    )
    assert diagnostic.hint == "Use the first parameter or declare a function instead."
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == (expected_file or filename)
    assert diagnostic.location.line == line
    assert diagnostic.location.col > 1


@pytest.mark.parametrize("check_support", [True, False])
def test_unused_receiver_in_helper_requests_is_refused(check_support):
    _assert_unused(SOURCE, "tag", 4, check_support=check_support)


@pytest.mark.parametrize(
    "use",
    [
        pytest.param("    int consumed = tag + 1\n", id="arithmetic"),
        pytest.param("    int consumed = math.abs(tag)\n", id="call-argument"),
        pytest.param(
            "    int consumed = math.abs(int(tag))\n", id="nested-call-argument",
        ),
        pytest.param(
            "    array<int> values = array.from(1, 2, 3)\n"
            "    int consumed = values.get(tag)\n",
            id="collection-index",
        ),
        pytest.param("    float consumed = close[tag]\n", id="history-index"),
        pytest.param(
            "    int consumed = close > open ? tag : 0\n", id="ternary-arm",
        ),
        pytest.param(
            "    int consumed = 0\n"
            "    for cursor = 0 to tag\n"
            "        consumed += cursor\n",
            id="for-bound",
        ),
        pytest.param(
            "    int consumed = 0\n"
            "    if close > open\n"
            "        consumed := tag\n",
            id="conditional-body",
        ),
        pytest.param(
            "    string consumed = str.format(\"{0}\", tag)\n",
            id="formatted-string-argument",
        ),
    ],
)
@pytest.mark.parametrize("check_support", [True, False])
def test_receiver_read_anywhere_in_method_body_translates(use, check_support):
    source = SOURCE.replace(
        "    string method_span = tf\n", use + "    string method_span = tf\n",
    )
    assert transpile(source, check_support=check_support)


def test_receiver_style_call_counts_as_a_use():
    source = '''//@version=6
strategy("receiver calls")
method identity(int value) => value
method delegate(int receiver, int unused) => receiver.identity()
int value = 1
result = value.delegate(2)
'''
    assert transpile(source)


def test_unused_later_parameter_translates():
    assert transpile('''//@version=6
strategy("later parameter")
method identity(int receiver, int unused) => receiver + 1
int value = 1
result = value.identity(2)
''')


def test_unused_first_function_parameter_translates():
    assert transpile('''//@version=6
strategy("function parameter")
identity(int unused, int value) => value + 1
result = identity(1, 2)
''')


@pytest.mark.parametrize("name", ["receiver", "origin"])
@pytest.mark.parametrize("called", [False, True])
def test_user_type_method_requires_its_first_parameter(name, called):
    source = (
        '//@version=6\nstrategy("user type receiver")\n'
        'type Holder\n    int value\n'
        f'method read(Holder {name}) => 1\n'
    )
    if called:
        source += "holder = Holder.new(2)\nresult = holder.read()\n"
    _assert_unused(source, name, 5)


def test_user_type_receiver_field_read_translates():
    assert transpile('''//@version=6
strategy("user type field")
type Holder
    int value
method read(Holder receiver) => receiver.value
holder = Holder.new(2)
result = holder.read()
''')


@pytest.mark.parametrize("used", [False, True])
def test_exported_method_receiver(used):
    library = (
        '//@version=6\nlibrary("Receiver")\n'
        'export type Holder\n    int value\n'
        'export method read(Holder receiver) => '
        + ("receiver.value" if used else "1") + "\n"
    )
    source = (
        '//@version=6\nstrategy("imported receiver")\n'
        'import examples/Receiver/1 as R\n'
        'holder = R.Holder.new(2)\nresult = holder.read()\n'
    )
    libraries = {"examples/Receiver/1": library}
    if used:
        assert transpile(source, libraries=libraries)
    else:
        # Inlining qualifies parameter names; the diagnostic must name the
        # parameter that the analyzer actually receives.
        program = Parser(Lexer(source).tokenize(), source=source).parse()
        program = inline_libraries(program, source, libraries=libraries)
        method = next(node for node in program.body if isinstance(node, MethodDef))
        _assert_unused(
            source, method.params[0], 5, expected_file="examples/Receiver/1",
            libraries=libraries,
        )


def test_receiver_spelling_in_a_string_is_not_a_read():
    _assert_unused('''//@version=6
strategy("literal receiver spelling")
method read(int receiver) =>
    string text = "receiver"
    1
''', "receiver", 3)


def test_receiver_read_in_a_request_remains_a_use_after_lowering():
    assert transpile('''//@version=6
strategy("receiver request")
method read(float receiver) =>
    request.security("OTHER", "60", receiver)
plot(close.read())
''')


def test_nested_function_receiver_read_keeps_existing_scope_refusal():
    # Nested local functions already fail independently in v6. Their receiver
    # read must reach that existing check, rather than the unused-receiver error.
    source = '''//@version=6
strategy("nested function receiver")
method read(int receiver) =>
    inner() => receiver + 1
    inner()
int value = 1
result = value.read()
'''
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="nested.pine")
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2057"
    assert diagnostic.location.file == "nested.pine"
    assert diagnostic.location.line == 4
