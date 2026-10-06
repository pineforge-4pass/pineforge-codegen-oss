"""A request's timeframe string is a Pine timeframe wherever the transpiler
resolves it from the script, and is emitted as an escaped C++ string literal.

``request.security`` / ``request.security_lower_tf`` register their
timeframe before the first bar. A timeframe written in the call is checked
by the support checker (``tests/test_security_tf_literal.py``); one the
codegen resolves -- a never-reassigned constant or its alias, a helper
parameter fed a literal (directly or through the request contexts of
``security_contexts``), a switch or ternary arm -- is refused with the
diagnostic the literal written in the call gets, and every timeframe the
C++ spells goes through the codegen's string escape.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.security import SecurityEmitter
from pineforge_codegen.errors import CompileError
from pineforge_codegen.pine_spelling import pine_string_literal

from pineforge_codegen.codegen.helpers import NamingHelper
from tests._cpp_tokens import assert_inert, assert_only_in_strings, string_values

PRELUDE = '//@version=6\nstrategy("t")\n'

# Timeframe strings that end a C++ literal or comment when pasted verbatim.
PAYLOADS = {
    "quote": '60" PFTF "',
    "backslash": '60\\" PFTF \\',
    "newline": "60\nPFTF",
    "comment_close": '60*/ PFTF " /*',
    "line_comment": '60" // PFTF',
}

SECURITY = "request.security"
LOWER_TF = "request.security_lower_tf"


def _shapes(fn: str) -> dict[str, str]:
    """Each way a script hands ``{tf}`` (a Pine string literal) to ``fn``."""
    use = "x" if fn == SECURITY else "array.size(x)"
    req = f"{fn}(syminfo.tickerid, {{arg}}, close)"
    return {
        "constant": f"tf = {{tf}}\nx = {req.format(arg='tf')}\nplot({use})\n",
        "const_keyword": f"const string tf = {{tf}}\nx = {req.format(arg='tf')}\nplot({use})\n",
        "alias": f"tf0 = {{tf}}\ntf = tf0\nx = {req.format(arg='tf')}\nplot({use})\n",
        "helper_param": f"f(t) => {req.format(arg='t')}\nx = f({{tf}})\nplot({use})\n",
        "nested_helper": (f"g(t) => {req.format(arg='t')}\nf(t) => g(t)\n"
                          f"x = f({{tf}})\nplot({use})\n"),
        "helper_two_contexts": (f"f(t) => {req.format(arg='t')}\n"
                                f"x = f({{tf}})\ny = f(\"240\")\nplot({use})\n"),
        "switch_arm_request": (f"m = input.int(1)\nx = switch m\n"
                               f"    1 => {req.format(arg='{tf}')}\n"
                               f"    => {req.format(arg=chr(34) + '240' + chr(34))}\n"
                               f"plot({use})\n"),
        "switch_value": (f"m = input.int(1)\ntf = switch m\n    1 => {{tf}}\n    => \"240\"\n"
                         f"x = {req.format(arg='tf')}\nplot({use})\n"),
        "ternary_value": (f"useA = input.bool(true)\ntf = useA ? {{tf}} : \"240\"\n"
                          f"x = {req.format(arg='tf')}\nplot({use})\n"),
    }


# The payload reads the requested timeframe: tf_multiplier, time_close and
# the timeframe.* members spell it again inside the evaluator.
PAYLOAD_READS = {
    "timeframe_multiplier": "timeframe.multiplier",
    "timeframe_period": "str.length(timeframe.period)",
    "timeframe_isintraday": "timeframe.isintraday ? 1 : 0",
    "timeframe_in_seconds": "timeframe.in_seconds()",
    "time_close": "time_close",
}


def _reach_source(expression: str, tf: str) -> str:
    return (PRELUDE + f"tf = {tf}\n"
            f"x = request.security(syminfo.tickerid, tf, {expression})\nplot(x)\n")


def _cases():
    for fn in (SECURITY, LOWER_TF):
        for shape, template in _shapes(fn).items():
            for name, payload in PAYLOADS.items():
                yield pytest.param(fn, template, payload, id=f"{fn}-{shape}-{name}")


def _literal_diagnostic(fn: str, payload: str):
    """The diagnostic of the same string written in the call."""
    use = "x" if fn == SECURITY else "array.size(x)"
    src = (PRELUDE + f"x = {fn}(syminfo.tickerid, {pine_string_literal(payload)}, close)\n"
           f"plot({use})\n")
    with pytest.raises(CompileError) as caught:
        transpile(src)
    (diagnostic,) = [d for d in caught.value.diagnostics if d.level.name == "ERROR"]
    return diagnostic


@pytest.mark.parametrize(("fn", "template", "payload"), _cases())
def test_resolved_timeframe_string_is_refused_as_the_literal_is(fn, template, payload):
    src = PRELUDE + template.format(tf=pine_string_literal(payload))
    expected = _literal_diagnostic(fn, payload)
    with pytest.raises(CompileError) as caught:
        transpile(src)
    errors = [d for d in caught.value.diagnostics if d.level.name == "ERROR"]
    assert [(d.code, d.message, d.hint) for d in errors] == [
        (expected.code, expected.message, expected.hint)]
    assert expected.code in {f"PF-E109{k}" for k in range(8)}


@pytest.mark.parametrize("payload", PAYLOADS.values(), ids=PAYLOADS.keys())
@pytest.mark.parametrize("expression", PAYLOAD_READS.values(), ids=PAYLOAD_READS.keys())
def test_timeframe_read_inside_the_payload_is_refused(expression, payload):
    with pytest.raises(CompileError) as caught:
        transpile(_reach_source(expression, pine_string_literal(payload)))
    errors = [d for d in caught.value.diagnostics if d.level.name == "ERROR"]
    assert [d.code for d in errors] == [_literal_diagnostic(SECURITY, payload).code]


@pytest.fixture
def unchecked_timeframes(monkeypatch):
    """The codegen without its timeframe check: what reaches the C++ is the
    string escape's alone (defense in depth)."""
    monkeypatch.setattr(SecurityEmitter, "_refuse_invalid_security_tf",
                        lambda self, value, node: None, raising=False)


@pytest.mark.parametrize("payload", PAYLOADS.values(), ids=PAYLOADS.keys())
@pytest.mark.parametrize("expression", ["close", *PAYLOAD_READS.values()],
                         ids=["close", *PAYLOAD_READS.keys()])
def test_every_timeframe_paste_is_escaped(unchecked_timeframes, expression, payload):
    cpp = transpile(_reach_source(expression, pine_string_literal(payload)))
    assert_inert(cpp, "PFTF")
    assert_only_in_strings(cpp, "PFTF")
    escaped = NamingHelper._cpp_string_escape(payload)
    assert f'register_security_eval(0, "{escaped}", input_tf_, false, false);' in cpp
    assert payload in string_values(cpp)


@pytest.mark.parametrize("payload", PAYLOADS.values(), ids=PAYLOADS.keys())
@pytest.mark.parametrize("shape", ["constant", "helper_param"])
def test_every_lower_timeframe_paste_is_escaped(unchecked_timeframes, shape, payload):
    template = _shapes(LOWER_TF)[shape]
    cpp = transpile(PRELUDE + template.format(tf=pine_string_literal(payload)))
    assert_inert(cpp, "PFTF")
    assert_only_in_strings(cpp, "PFTF")
    escaped = NamingHelper._cpp_string_escape(payload)
    assert f'(0, "{escaped}", input_tf_);' in cpp


VALID = ["1", "5", "15", "60", "240", "1440", "1S", "30S", "2H", "1D", "D",
         "1W", "W", "1M", "M", "3M", "12M"]


@pytest.mark.parametrize("tf", VALID)
@pytest.mark.parametrize("shape", ["constant", "const_keyword", "alias", "helper_param",
                                   "nested_helper"])
def test_valid_timeframe_registers_as_before(shape, tf):
    cpp = transpile(PRELUDE + _shapes(SECURITY)[shape].format(tf=f'"{tf}"'))
    assert f'register_security_eval(0, "{tf}", input_tf_, false, false);' in cpp
    assert_inert(cpp, "PFTF")


@pytest.mark.parametrize("tf", VALID)
def test_valid_timeframe_reads_in_the_payload_as_before(tf):
    cpp = transpile(_reach_source("timeframe.multiplier", f'"{tf}"'))
    assert f'tf_multiplier("{tf}")' in cpp
    cpp = transpile(_reach_source("time_close", f'"{tf}"'))
    assert f'pine_time_close(bar.timestamp, "{tf}", ' in cpp


@pytest.mark.parametrize("shape", ["switch_value", "ternary_value"])
def test_valid_timeframe_arms_still_transpile(shape):
    transpile(PRELUDE + _shapes(SECURITY)[shape].format(tf='"60"'))


def test_empty_constant_timeframe_still_reads_the_chart():
    # An empty string is the chart's timeframe: it registers input_tf_ as
    # before, and never reaches the C++ as a literal.
    cpp = transpile(PRELUDE + _shapes(SECURITY)["constant"].format(tf='""'))
    assert "register_security_eval(0, input_tf_, input_tf_, false, false);" in cpp


@pytest.mark.parametrize("tf", ["²", "1²", "①"])
@pytest.mark.parametrize("shape", ["literal", "constant"])
def test_non_decimal_digit_timeframe_is_refused(shape, tf):
    # A digit that is no decimal digit is no timeframe magnitude: refused,
    # where the magnitude's int() used to raise out of the transpiler.
    if shape == "literal":
        src = PRELUDE + f'x = request.security(syminfo.tickerid, "{tf}", close)\nplot(x)\n'
    else:
        src = PRELUDE + _shapes(SECURITY)["constant"].format(tf=f'"{tf}"')
    with pytest.raises(CompileError) as caught:
        transpile(src)
    errors = [d for d in caught.value.diagnostics if d.level.name == "ERROR"]
    assert [d.code for d in errors] == ["PF-E1091"]
