"""One ``request.security`` context per symbol and timeframe reaching a helper.

A request's timeframe is registered before the first bar. When it reaches the
request through helper parameters (``h(tf) => g(tf)``, ``g(tf) =>
request.security(..., tf, ...)``), the analyzer resolved it from the
arguments of the innermost helper's own call sites only, and the codegen
registered the chart timeframe (``input_tf_``) with no diagnostic: two calls
of ``h`` with 60 and 240 lowered to one chart-timeframe context. A helper
called with two inputs registered their defaults, so an override was
ignored. ``security_contexts`` now resolves the context along every call
path, one context per distinct value, and refuses one no registration can
compute; the analyzer's call-site clones register an input as its getter.
TradingView's tapes of the nested shapes are replayed in
``test_e2e_security_nested_contexts.py``.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp


HEAD = (
    '//@version=6\nstrategy("contexts", overlay=true)\n'
    'tfA = input.timeframe("60", "A")\ntfB = input.timeframe("240", "B")\n'
)
TAIL = 'if a > b\n    strategy.entry("L", strategy.long)\n'


def _registrations(cpp: str) -> list[str]:
    return re.findall(r"register_security(?:_lower_tf)?_eval\((.*)\);", cpp)


def test_nested_helper_with_one_timeframe_registers_it():
    cpp = transpile(HEAD + (
        'g(tf) => request.security(syminfo.tickerid, tf, close)\n'
        'h(tf) => g(tf)\n'
        'a = h("60")\nb = h("60")\n') + TAIL)
    assert _registrations(cpp) == ['0, "60", input_tf_, false, false']
    assert "__pfctx" not in cpp
    compile_cpp(cpp, label="nested-one-context")


def test_nested_helper_with_two_timeframes_gets_two_contexts():
    cpp = transpile(HEAD + (
        'g(tf) => request.security(syminfo.tickerid, tf, close)\n'
        'h(tf) => g(tf)\n'
        'a = h("60")\nb = h("240")\n') + TAIL)
    assert _registrations(cpp) == ['0, "60", input_tf_, false, false',
                                   '1, "240", input_tf_, false, false']
    # One copy of each helper per context: h__pfctx1 calls g__pfctx1.
    assert "a = h(std::string(\"60\"));" in cpp
    assert "b = h__pfctx1(std::string(\"240\"));" in cpp
    assert re.search(r"h__pfctx1\(std::string tf\) \{\s*return g__pfctx1\(tf\);", cpp)
    compile_cpp(cpp, label="nested-two-contexts")


def test_nested_helper_inputs_register_their_getters():
    """XSYM-DESIGN's census control: inputs through two helper levels."""
    cpp = transpile(HEAD + (
        'g(tf) => request.security(syminfo.tickerid, tf, close, lookahead=barmerge.lookahead_on)\n'
        'h(tf) => g(tf)\n'
        'a = h(tfA)\nb = h(tfB)\n') + TAIL)
    assert _registrations(cpp) == [
        '0, get_input_string("A", std::string("60")), input_tf_, true, false',
        '1, get_input_string("B", std::string("240")), input_tf_, true, false',
    ]
    compile_cpp(cpp, label="nested-inputs")


def test_paths_of_different_depths_share_a_context():
    """``k(tf) => h(tf)`` beside direct ``h`` calls: a context reached on
    two paths is one request; a literal "60" is not the input "A"."""
    cpp = transpile(HEAD + (
        'g(tf) => request.security(syminfo.tickerid, tf, ta.sma(close, 3))\n'
        'h(tf) => g(tf)\n'
        'k(tf) => h(tf)\n'
        'a = h(tfA)\nb = h(tfB)\nc = k("60")\nd = k(tfB)\n') + TAIL)
    assert _registrations(cpp) == [
        '0, get_input_string("A", std::string("60")), input_tf_, false, false',
        '1, get_input_string("B", std::string("240")), input_tf_, false, false',
        '2, "60", input_tf_, false, false',
    ]
    compile_cpp(cpp, label="nested-depths")


def test_helper_local_resolves_through_its_definition():
    cpp = transpile(HEAD + (
        'g(tf) => request.security(syminfo.tickerid, tf, close)\n'
        'h(x) =>\n'
        '    t = x == "" ? timeframe.period : x\n'
        '    g(t)\n'
        'a = h(tfA)\nb = close\n') + TAIL)
    (reg,) = _registrations(cpp)
    assert reg == ('0, (((get_input_string("A", std::string("60")) == std::string("")))'
                   ' ? (script_tf_) : (get_input_string("A", std::string("60")))),'
                   ' input_tf_, false, false'), reg
    compile_cpp(cpp, label="nested-local")


@pytest.mark.parametrize("body, reason", [
    ('h(x) => g(x)\na = h(close > open ? "60" : "240")\n',
     "on the call path h() -> g(): it reads 'close', a series"),
    ('h(x) =>\n    t = x\n    t := "15"\n    g(t)\na = h(tfA)\n',
     "on the call path h() -> g(): 't' is not a value fixed before the first bar"),
    ('pick() => "60"\nh(x) => g(x)\na = h(pick())\n',
     "on the call path h() -> g(): the user function call pick(...)"),
])
def test_unresolvable_context_is_refused(body, reason):
    src = HEAD + 'g(tf) => request.security(syminfo.tickerid, tf, close)\n' + body + \
        'b = close\n' + TAIL
    with pytest.raises(CompileError) as err:
        transpile(src)
    messages = [d.message for d in err.value.diagnostics]
    assert any(m.startswith("request.security timeframe cannot be resolved before "
                            "the first bar") and m.endswith(reason) for m in messages), messages


def test_one_level_inputs_register_their_getters():
    """Two inputs through one helper registered their defaults, "60" and
    "240", so an override of either was ignored."""
    cpp = transpile(HEAD + (
        'f(tf) => request.security(syminfo.tickerid, tf, close)\n'
        'a = f(tfA)\nb = f(tfB)\nc = f("60")\n') + TAIL)
    assert _registrations(cpp) == [
        '0, get_input_string("A", std::string("60")), input_tf_, false, false',
        '1, get_input_string("B", std::string("240")), input_tf_, false, false',
        '2, "60", input_tf_, false, false',
    ]
    compile_cpp(cpp, label="one-level-inputs")


def test_one_level_inputs_with_one_default_are_two_contexts():
    """Two inputs sharing a default are two contexts: an override may part
    them. Base registered one context and read it for both."""
    src = HEAD.replace('"240", "B"', '"60", "B"') + (
        'f(tf) => request.security(syminfo.tickerid, tf, close)\n'
        'a = f(tfA)\nb = f(tfB)\n') + TAIL
    cpp = transpile(src)
    assert _registrations(cpp) == [
        '0, get_input_string("A", std::string("60")), input_tf_, false, false',
        '1, get_input_string("B", std::string("60")), input_tf_, false, false',
    ]


def test_lower_tf_helper_timeframe_is_resolved():
    """``request.security_lower_tf`` in a helper registered the chart
    timeframe whatever its parameter held."""
    cpp = transpile(
        '//@version=6\nstrategy("lower", overlay=true)\n'
        'f(tf) => request.security_lower_tf(syminfo.tickerid, tf, close)\n'
        'arr = f("1")\n'
        'if array.size(arr) > 0 and array.get(arr, 0) > open\n'
        '    strategy.entry("L", strategy.long)\n')
    assert _registrations(cpp) == ['0, "1", input_tf_']
    compile_cpp(cpp, label="lower-tf-helper")


def test_dead_helper_keeps_the_chart_timeframe():
    """A helper reached only from a helper nothing calls never runs: it keeps
    the registration every earlier build gave it."""
    cpp = transpile(HEAD + (
        'g(tf) => request.security(syminfo.tickerid, tf, close)\n'
        'h(tf) => g(tf)\n'
        'a = close\nb = open\n') + TAIL)
    assert _registrations(cpp) == ['0, input_tf_, input_tf_, false, false']


def test_empty_timeframe_constant_keeps_the_chart_timeframe():
    """``tf = ""`` is the chart's timeframe: it registers as every earlier
    build did, through a helper too."""
    for body in ('tfX = ""\na = request.security(syminfo.tickerid, tfX, close)\n',
                 'tfX = ""\nf(tf) => request.security(syminfo.tickerid, tf, close)\na = f(tfX)\n'):
        cpp = transpile(HEAD + body + 'b = close\n' + TAIL)
        assert _registrations(cpp) == ['0, input_tf_, input_tf_, false, false']
