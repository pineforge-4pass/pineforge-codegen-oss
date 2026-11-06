"""One ``request.security`` context per symbol and timeframe reaching a helper.

A request's timeframe is registered before the first bar. When it reaches the
request through helper parameters (``h(tf) => g(tf)``, ``g(tf) =>
request.security(..., tf, ...)``), the analyzer resolved it from the
arguments of the innermost helper's own call sites only, and the codegen
registered the chart timeframe (``input_tf_``) with no diagnostic: two calls
of ``h`` with 60 and 240 lowered to one chart-timeframe context. A helper
called with two inputs registered their defaults, so an override was
ignored. ``security_contexts`` now resolves the context along every call
path, one context per distinct value. Same-symbol closed per-bar timeframes
select among registered streams; the analyzer's call-site clones register
an input as its getter.
TradingView's tapes of the nested shapes are replayed in
``test_e2e_security_nested_contexts.py``.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from pineforge_codegen import transpile, transpile_full
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


@pytest.mark.parametrize("body, streams, per_bar", [
    ('h(x) => g(x)\na = h(close > open ? "60" : "240")\n',
     2, True),
    ('h(x) =>\n    t = x\n    t := "15"\n    g(t)\na = h(tfA)\n',
     2, True),
    ('pick() => "60"\nh(x) => g(x)\na = h(pick())\n',
     1, False),
])
def test_closed_timeframe_context_is_registered_and_compiles(body, streams, per_bar):
    # Keep the original chart-selection, local-store and pure-call witnesses.
    src = HEAD + 'g(tf) => request.security(syminfo.tickerid, tf, close)\n' + body + \
        'b = close\n' + TAIL
    cpp = transpile(src)
    regs = _registrations(cpp)
    assert len(regs) == streams
    assert not any(re.match(r"\d+, input_tf_,", reg) for reg in regs)
    assert ("_pf_selected_tf" in cpp) == per_bar
    compile_cpp(cpp, label="closed-helper-context")


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


@pytest.mark.parametrize("declarations, source_sha256, getter_read", [
    pytest.param(
        'var choice = input.time(1735689600000, "Selection")\n'
        'mode = choice < 1751328000000 ? barmerge.lookahead_on : barmerge.lookahead_off\n',
        "3bfe9ec98fcb5a0b4bdb60cf426437fc004a17d1c97ba3c755c43416dc5f0611",
        'get_input_int64("Selection", 1735689600000)',
        id="persistent-time"),
    pytest.param(
        'varip choice = input.text_area("Enabled", "Selection")\n'
        'mode = choice == "Enabled" ? barmerge.lookahead_on : barmerge.lookahead_off\n',
        "506ba1e1b9cab8787a0340e529b99282200d1a2845fa37572ea6c05c7e35a6d9",
        'get_input_string("Selection", std::string("Enabled"))',
        id="persistent-text-area"),
])
def test_tv_accepted_persistent_lookahead_sources(
        declarations, source_sha256, getter_read):
    """Exact TV compile receipts; the plot-only tapes prove no values."""
    request = ('daily = request.security(syminfo.tickerid, "D", close[1], '
               'lookahead=mode)\n')
    src = (
        '//@version=6\nstrategy("lookahead refusal")\n'
        'useLookahead = input.bool(true, "Lookahead")\n'
        + declarations + request + 'plot(daily)\n')
    assert hashlib.sha256(src.encode("utf-8")).hexdigest() == source_sha256
    result = transpile_full(src, filename="persistent-selection.pine")
    (registration,) = _registrations(result["cpp"])
    assert getter_read in registration
    assert "static_cast<bool>" in registration
    assert all(name not in registration for name in ("choice", "mode", "useLookahead"))
    (warning,) = [d for d in result["diagnostics"] if d.code == "PF-W1524"]
    assert warning.location.file == "persistent-selection.pine"
    assert (warning.location.line, warning.location.col) == (
        src.splitlines().index(request.rstrip()) + 1, request.index("mode") + 1)
    assert warning.args == {}
    assert not [d for d in result["diagnostics"] if d.code in {"PF-E1085", "PF-W1085"}]
    compile_cpp(result["cpp"], label="persistent-lookahead-source")


@pytest.mark.parametrize("default", ["true", "false"])
@pytest.mark.parametrize("argument", [
    "lookahead=mode",
    "gaps=barmerge.gaps_off, lookahead=mode",
])
def test_input_derived_lookahead_transpiles_and_compiles(default, argument):
    cpp = transpile(
        '//@version=6\nstrategy("lookahead input")\n'
        f'useLookahead = input.bool({default}, "Lookahead")\n'
        'mode = useLookahead ? barmerge.lookahead_on : barmerge.lookahead_off\n'
        f'daily = request.security(syminfo.tickerid, "D", close[1], {argument})\n'
        'plot(daily)\n')
    compile_cpp(cpp, label="input-derived lookahead")


@pytest.mark.parametrize("kind, defaults, predicate, getter, cpp_defaults", [
    pytest.param("time", ("1735689600000", "1767225600000"),
                 "selection < 1751328000000", "get_input_int64",
                 ("1735689600000", "1767225600000"), id="time"),
    pytest.param("price", ("1.25", "2.75"), "selection < 2.0", "get_input_double",
                 ("1.25", "2.75"), id="price"),
    pytest.param("session", ('"0930-1600"', '"0000-2400"'),
                 'selection == "0930-1600"', "get_input_string",
                 ('std::string("0930-1600")', 'std::string("0000-2400")'), id="session"),
    pytest.param("text_area", ('"Enabled\\nMiXeD"', '"disabled\\nMiXeD"'),
                 'selection == "Enabled\\nMiXeD"', "get_input_string",
                 ('std::string("Enabled\\nMiXeD")', 'std::string("disabled\\nMiXeD")'),
                 id="text-area"),
])
@pytest.mark.parametrize("default_index", [0, 1], ids=["default-on", "default-off"])
@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
def test_fixed_primitive_input_lookahead_registers_typed_getter(
        kind, defaults, predicate, getter, cpp_defaults, default_index, named):
    """Exercise the real pipeline, including warning provenance and C++ setup."""
    arguments = (f'defval={defaults[default_index]}, title="Selection"' if named
                 else f'{defaults[default_index]}, "Selection"')
    request = ('daily = request.security(syminfo.tickerid, "D", close[1], '
               'lookahead=second)\n')
    src = (
        '//@version=6\nstrategy("fixed primitive lookahead")\n'
        f'rawChoice = input.{kind}({arguments})\n'
        'selection = rawChoice\n'
        f'first = {predicate} ? barmerge.lookahead_on : barmerge.lookahead_off\n'
        'second = first\n' + request
        + 'if daily > close\n    strategy.entry("L", strategy.long)\n')
    result = transpile_full(src, filename="fixed-input.pine")
    (registration,) = _registrations(result["cpp"])
    assert f'{getter}("Selection", {cpp_defaults[default_index]})' in registration
    assert "static_cast<bool>" in registration
    assert all(name not in registration for name in ("rawChoice", "selection", "first", "second"))
    warnings = [d for d in result["diagnostics"] if d.code == "PF-W1524"]
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.location.file == "fixed-input.pine"
    assert (warning.location.line, warning.location.col) == (
        src.splitlines().index(request.rstrip()) + 1, request.index("second") + 1)
    assert warning.args == {}
    assert not [d for d in result["diagnostics"] if d.code in {"PF-E1085", "PF-W1085"}]
    compile_cpp(result["cpp"], label=f"fixed-{kind}-lookahead")


@pytest.mark.parametrize("declarations, call, predicate, getter_read", [
    pytest.param("", 'input.time(timestamp("2025-01-01T00:00:00+00:00"), "Selection")',
                 "selection < 1751328000000",
                 'get_input_int64("Selection", 1735689600000LL)', id="timestamp-default"),
    pytest.param("", 'input.price(1.0 + 0.25, "Selection")', "selection < 2.0",
                 'get_input_double("Selection", (1.0 + 0.25))', id="arithmetic-default"),
    pytest.param('const string seed = "0930-1600"\n',
                 'input.session(seed, "Selection")', 'selection == "0930-1600"',
                 'get_input_string("Selection", std::string("0930-1600"))', id="session-default"),
    pytest.param('const string seed = "Enabled\\nMiXeD"\n',
                 'input.text_area(seed, "Selection")', 'selection == "Enabled\\nMiXeD"',
                 'get_input_string("Selection", std::string("Enabled\\nMiXeD"))', id="text-default"),
])
def test_fixed_primitive_lookahead_defaults_keep_prebar_expression(
        declarations, call, predicate, getter_read):
    cpp = transpile(
        '//@version=6\nstrategy("fixed input defaults")\n'
        + declarations + f'selection = {call}\n'
        f'mode = {predicate} ? barmerge.lookahead_on : barmerge.lookahead_off\n'
        'daily = request.security(syminfo.tickerid, "D", close[1], lookahead=mode)\n'
        'plot(daily)\n')
    (registration,) = _registrations(cpp)
    assert getter_read in registration
    assert "selection" not in registration and "mode" not in registration
    compile_cpp(cpp, label="fixed-input-expression-default")


@pytest.mark.parametrize("payload", ["ta.ema(base[1], 5)", "ta.sma(base[2], 3)"])
def test_requested_value_history_inside_requested_ta_compiles(payload):
    cpp = transpile(
        '//@version=6\nstrategy("requested history")\n'
        'base = request.security(syminfo.tickerid, "D", close)\n'
        f'smooth = request.security(syminfo.tickerid, "240", {payload})\n'
        'plot(smooth)\n')
    compile_cpp(cpp, label="requested history inside requested TA")


@pytest.mark.parametrize("wrapper", [
    'fetch(sym, tf, expr) => request.security(sym, tf, expr)\n',
    'fetch(sym, tf, expr) => request.security(sym, tf, expr)\n'
    'forward(sym, tf, expr) => fetch(sym, tf, expr)\n',
])
def test_uncalled_request_payload_wrapper_compiles(wrapper):
    cpp = transpile(
        '//@version=6\nstrategy("unused request wrapper")\n'
        + wrapper
        + 'daily = request.security(syminfo.tickerid, "D", close[1])\n'
        'plot(daily)\n')
    compile_cpp(cpp, label="uncalled request payload wrapper")


@pytest.mark.parametrize("selection", [
    "barmerge.lookahead_on",
    "useLookahead ? barmerge.lookahead_on : barmerge.lookahead_off",
    "useLookahead ? barmerge.lookahead_off : barmerge.lookahead_on",
])
def test_lookahead_aliases_register_their_value(selection):
    cpp = transpile(
        '//@version=6\nstrategy("lookahead aliases")\n'
        'useLookahead = input.bool(true, "Lookahead")\n'
        f'first = {selection}\nsecond = first\n'
        'daily = request.security(syminfo.tickerid, "D", close[1], '
        'barmerge.gaps_off, second)\nplot(daily)\n')
    (registration,) = _registrations(cpp)
    assert "second" not in registration
    if "useLookahead" in selection:
        assert 'get_input_bool("Lookahead", true)' in registration
    else:
        assert registration == '0, "D", input_tf_, true, false'
    compile_cpp(cpp, label="lookahead alias registration")


@pytest.mark.parametrize("selection", [
    "close > open ? barmerge.lookahead_on : barmerge.lookahead_off",
    "input(close) > 0 ? barmerge.lookahead_on : barmerge.lookahead_off",
    "input.source(close) > 0 ? barmerge.lookahead_on : barmerge.lookahead_off",
    "true",
    "barmerge.gaps_on",
])
def test_lookahead_keeps_enum_and_simple_value_validation(selection):
    with pytest.raises(CompileError, match="lookahead must be barmerge"):
        transpile(
            '//@version=6\nstrategy("invalid lookahead")\n'
            f'mode = {selection}\n'
            'daily = request.security(syminfo.tickerid, "D", close, lookahead=mode)\n'
            'plot(daily)\n')


def test_repeated_requested_history_reads_share_requested_clock_storage():
    cpp = transpile(
        '//@version=6\nstrategy("requested history reads")\n'
        'base = request.security(syminfo.tickerid, "D", close)\n'
        'smooth = request.security(syminfo.tickerid, "240", '
        'ta.ema(base[1], 5) + base[1])\nplot(smooth)\n')
    compile_cpp(cpp, label="repeated requested history")


@pytest.mark.parametrize("offset", [0, 1, 2])
def test_requested_history_through_global_aliases_compiles(offset):
    cpp = transpile(
        '//@version=6\nstrategy("requested history aliases")\n'
        'base = request.security(syminfo.tickerid, "D", close)\n'
        'first = base\nsecond = first\n'
        f'smooth = request.security(syminfo.tickerid, "240", ta.ema(second[{offset}], 5))\n'
        'plot(smooth)\n')
    compile_cpp(cpp, label="requested history aliases")


def test_multiple_uncalled_forwarders_do_not_bind_request_payloads():
    cpp = transpile(
        '//@version=6\nstrategy("uncalled forwarders")\n'
        'fetch(sym, tf, expr) => request.security(sym, tf, expr)\n'
        'forwardA(expr) => fetch(syminfo.tickerid, "60", expr)\n'
        'forwardB(expr) => fetch(syminfo.tickerid, "240", expr)\n'
        'daily = request.security(syminfo.tickerid, "D", close[1])\n'
        'plot(daily)\n')
    compile_cpp(cpp, label="multiple uncalled request forwarders")


@pytest.mark.parametrize("default", [True, False])
def test_input_derived_lookahead_manifest_uses_the_input_default(default):
    result = transpile_full(
        '//@version=6\nstrategy("lookahead manifest")\n'
        'other = input.symbol("BINANCE:ETHUSDT", "Other")\n'
        f'useLookahead = input.bool({str(default).lower()}, "Lookahead")\n'
        'mode = useLookahead ? barmerge.lookahead_on : barmerge.lookahead_off\n'
        'daily = request.security(other, "D", close[1], lookahead=mode)\n'
        'if daily > close\n    strategy.entry("L", strategy.long)\n')
    (request,) = result["requests"]
    assert request["lookahead"] is default
    assert request["symbol"]["kind"] == "input"
    compile_cpp(result["cpp"], label="lookahead manifest")


@pytest.mark.parametrize("selection", [
    'mode = if useLookahead\n'
    '    barmerge.lookahead_on\n'
    'else\n'
    '    barmerge.lookahead_off\n',
    'mode = switch useLookahead\n'
    '    true => barmerge.lookahead_on\n'
    '    => barmerge.lookahead_off\n',
])
def test_lookahead_selection_statements_register_input_reads(selection):
    cpp = transpile(
        '//@version=6\nstrategy("lookahead selections")\n'
        'useLookahead = input.bool(true, "Lookahead")\n'
        + selection
        + 'daily = request.security(syminfo.tickerid, "D", close[1], lookahead=mode)\n'
        'plot(daily)\n')
    (registration,) = _registrations(cpp)
    assert 'get_input_bool("Lookahead", true)' in registration
    compile_cpp(cpp, label="lookahead selection statements")


def test_uncalled_request_wrapper_needs_no_requested_ta_constructor():
    cpp = transpile(
        '//@version=6\nstrategy("uncalled requested TA")\n'
        'fetch(sym, tf, expr, length) => '
        'request.security(sym, tf, ta.sma(expr, length))\n'
        'daily = request.security(syminfo.tickerid, "D", close[1])\n'
        'plot(daily)\n')
    compile_cpp(cpp, label="uncalled requested TA constructor")


def test_uncalled_request_wrapper_needs_no_persistent_payload_lowering():
    cpp = transpile(
        '//@version=6\nstrategy("uncalled persistent request")\n'
        'accumulate(src) =>\n'
        '    var total = 0.0\n'
        '    total += src\n'
        '    total\n'
        'fetch(sym, tf, expr) => '
        'request.security(sym, tf, expr > 0 ? accumulate(expr) : 0.0)\n'
        'daily = request.security(syminfo.tickerid, "D", close[1])\n'
        'plot(daily)\n')
    compile_cpp(cpp, label="uncalled persistent request payload")


@pytest.mark.parametrize("default", ["true", "false"])
@pytest.mark.parametrize("argument", ["lookahead={}", "barmerge.gaps_off, {}"],
                         ids=["keyword", "positional"])
@pytest.mark.parametrize("declarations, selection, warns", [
    pytest.param("", "barmerge.lookahead_on", True, id="literal-on"),
    pytest.param("", "barmerge.lookahead_off", False, id="literal-off"),
    pytest.param("", "useLookahead ? barmerge.lookahead_on : barmerge.lookahead_off",
                 True, id="input-on-off"),
    pytest.param("", "useLookahead ? barmerge.lookahead_off : barmerge.lookahead_on",
                 True, id="input-off-on"),
    pytest.param("", "useLookahead ? barmerge.lookahead_off : barmerge.lookahead_off",
                 False, id="input-off-only"),
    pytest.param("", "useLookahead ? barmerge.lookahead_on : barmerge.lookahead_on",
                 True, id="input-on-only"),
    pytest.param("first = barmerge.lookahead_on\nsecond = first\n", "second",
                 True, id="alias-on"),
    pytest.param("first = barmerge.lookahead_off\nsecond = first\n", "second",
                 False, id="alias-off"),
    pytest.param("first = useLookahead ? barmerge.lookahead_off : barmerge.lookahead_on\n"
                 "second = first\n", "second", True, id="alias-input-on"),
    pytest.param("first = useLookahead ? barmerge.lookahead_off : barmerge.lookahead_off\n"
                 "second = first\n", "second", False, id="alias-input-off-only"),
    pytest.param("mode = if useLookahead\n"
                 "    barmerge.lookahead_on\n"
                 "else\n"
                 "    barmerge.lookahead_off\n", "mode", True, id="if-input-on"),
    pytest.param("mode = if useLookahead\n"
                 "    barmerge.lookahead_off\n"
                 "else\n"
                 "    barmerge.lookahead_off\n", "mode", False, id="if-off-only"),
    pytest.param("mode = switch useLookahead\n"
                 "    true => barmerge.lookahead_off\n"
                 "    => barmerge.lookahead_on\n", "mode", True, id="switch-input-on"),
    pytest.param("mode = switch useLookahead\n"
                 "    true => barmerge.lookahead_off\n"
                 "    => barmerge.lookahead_off\n", "mode", False, id="switch-off-only"),
    pytest.param("", "true ? barmerge.lookahead_off : barmerge.lookahead_on",
                 False, id="constant-true-off"),
    pytest.param("", "false ? barmerge.lookahead_on : barmerge.lookahead_off",
                 False, id="constant-false-off"),
])
def test_lookahead_warning_follows_immutable_value(
        declarations, selection, warns, argument, default):
    result = transpile_full(
        '//@version=6\nstrategy("lookahead warnings")\n'
        f'useLookahead = input.bool({default}, "Lookahead")\n'
        + declarations
        + 'daily = request.security(syminfo.tickerid, "D", close[1], '
        + argument.format(selection) + ')\nplot(daily)\n')
    warnings = [d for d in result["diagnostics"] if d.code == "PF-W1524"]
    assert len(warnings) == int(warns), [d.message for d in result["diagnostics"]]


@pytest.mark.parametrize("declarations", [
    pytest.param('mode = barmerge.lookahead_off\nmode := barmerge.lookahead_on\n',
                 id="reassigned-enum"),
    pytest.param('var mode = barmerge.lookahead_on\n', id="persistent-enum"),
    pytest.param('choice = useLookahead\nchoice := false\n'
                 'mode = choice ? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="reassigned-condition"),
    pytest.param('mode = close > open ? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="bar-series"),
    pytest.param('mode = input(close) > 0 ? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="source-input"),
    pytest.param('mode = input.source(close) > 0 ? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="named-source-input"),
    pytest.param('choice = input.price(1.25, "Selection")\nchoice := 2.75\n'
                 'mode = choice < 2.0 ? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="reassigned-price"),
    pytest.param('choice = input.session("0930-1600", "Selection")\n'
                 'alias = choice\nalias := "0000-2400"\n'
                 'mode = alias == "0930-1600" ? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="reassigned-session-alias"),
    pytest.param('mode = input.price(close, "Selection") > 0 '
                 '? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="series-default"),
    pytest.param('mode = input.time(timestamp(2025, 1, 1, 0, bar_index), "Selection") > 0 '
                 '? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="series-timestamp-default"),
    pytest.param('timestamp = 1\n'
                 'mode = input.time(timestamp("2025-01-01"), "Selection") > 0 '
                 '? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="shadowed-timestamp"),
    pytest.param('input = 1\n'
                 'mode = input.price(1.25, "Selection") < 2.0 '
                 '? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="shadowed-input-namespace"),
    pytest.param('mode = input.text_area("Enabled", "Selection") == "Enabled" '
                 '? barmerge.lookahead_on : barmerge.gaps_off\n',
                 id="wrong-enum-family"),
    pytest.param('barmerge = 1\n'
                 'mode = useLookahead ? barmerge.lookahead_on : barmerge.lookahead_off\n',
                 id="shadowed-namespace"),
    pytest.param('mode = if useLookahead\n    barmerge.lookahead_on\n',
                 id="incomplete-if"),
    pytest.param('mode = switch useLookahead\n    true => barmerge.lookahead_on\n',
                 id="incomplete-switch"),
])
def test_lookahead_warning_preserves_selection_refusals(declarations):
    request = ('daily = request.security(syminfo.tickerid, "D", close[1], '
               'lookahead=mode)\n')
    src = (
        '//@version=6\nstrategy("lookahead refusal")\n'
        'useLookahead = input.bool(true, "Lookahead")\n'
        + declarations + request + 'plot(daily)\n')
    with pytest.raises(CompileError, match="lookahead must be barmerge") as err:
        transpile_full(src, filename="fixed-refusal.pine")
    (diagnostic,) = [d for d in err.value.diagnostics if d.code == "PF-E1085"]
    assert diagnostic.location.file == "fixed-refusal.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (
        src.splitlines().index(request.rstrip()) + 1, request.index("mode") + 1)
    assert diagnostic.args == {}
    assert not [d for d in err.value.diagnostics if d.code == "PF-W1524"]
