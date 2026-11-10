"""String inputs carry immutable global literals and refuse unproven variables."""

import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


NONDEFAULT_BRANCH = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
var DEFAULT_MODE = "Long only"
mode = input.string(DEFAULT_MODE, "Mode", options = ["Long only", "Long and short"])
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    if mode == "Long and short"
        strategy.entry("Short", strategy.short, qty = 0.01)
    else
        strategy.close("Long")
"""

DEFAULT_BRANCH = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
var DEFAULT_MODE = "Long only"
mode = input.string(DEFAULT_MODE, "Mode", options = ["Long only", "Long and short"])
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    if mode == "Long only"
        strategy.close("Long")
    else
        strategy.entry("Short", strategy.short, qty = 0.01)
"""

DEFAULT_CONTROL = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
DEFAULT_MODE = "Long only"
mode = input.string(DEFAULT_MODE, "Mode", options = ["Long only", "Long and short"])
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    if mode == "Long only"
        strategy.close("Long")
    else
        strategy.entry("Short", strategy.short, qty = 0.01)
"""

VARIABLE_OPTIONS = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
var LONG_ONLY = "Long only"
var LONG_SHORT = "Long and short"
mode = input.string("Long only", "Mode", options = [LONG_ONLY, LONG_SHORT])
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    if mode == "Long and short"
        strategy.entry("Short", strategy.short, qty = 0.01)
    else
        strategy.close("Long")
"""

OPTIONS_CONTROL = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
LONG_ONLY = "Long only"
LONG_SHORT = "Long and short"
mode = input.string("Long only", "Mode", options = [LONG_ONLY, LONG_SHORT])
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    if mode == "Long and short"
        strategy.entry("Short", strategy.short, qty = 0.01)
    else
        strategy.close("Long")
"""

NONDEFAULT_CONTROL = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
DEFAULT_MODE = "Long only"
mode = input.string(DEFAULT_MODE, "Mode", options = ["Long only", "Long and short"])
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    if mode == "Long and short"
        strategy.entry("Short", strategy.short, qty = 0.01)
    else
        strategy.close("Long")
"""

LITERAL_WORKAROUND = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
mode = input.string("Long only", "Mode", options = ["Long only", "Long and short"])
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    if mode == "Long only"
        strategy.close("Long")
    else
        strategy.entry("Short", strategy.short, qty = 0.01)
"""

BOOL_DEFAULT = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
var FLAG = true
flag = input.bool(FLAG, "Flag")
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow) and flag
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

BOOL_CONTROL = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
FLAG = true
flag = input.bool(FLAG, "Flag")
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow) and flag
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

FLOAT_DEFAULT = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
var BAND = 0.002
band = input.float(BAND, "Band")
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if fast > slow * (1 + band)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

FLOAT_CONTROL = """//@version=6
strategy("input.string default from a var variable", overlay = true, initial_capital = 10000)
BAND = 0.002
band = input.float(BAND, "Band")
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if fast > slow * (1 + band)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

SIBLING_DEFAULTS = {
    "timeframe": """//@version=6
strategy("sibling timeframe default from a var variable", overlay = true, initial_capital = 10000)
var TF = "60"
tf = input.timeframe(TF, "Frame")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("Long", strategy.long, qty = 0.01)
""",
    "symbol": """//@version=6
strategy("sibling symbol default from a var variable", overlay = true, initial_capital = 10000)
var SYM = "BINANCE:BTCUSDT"
sym = input.symbol(SYM, "Other symbol")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("Long", strategy.long, qty = 0.01)
""",
    "session": """//@version=6
strategy("sibling session default from a var variable", overlay = true, initial_capital = 10000)
var SESS = "0930-1600"
sess = input.session(SESS, "Hours")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("Long", strategy.long, qty = 0.01)
""",
    "text_area": """//@version=6
strategy("sibling text area default from a var variable", overlay = true, initial_capital = 10000)
var TXT = "hello"
txt = input.text_area(TXT, "Notes")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("Long", strategy.long, qty = 0.01)
""",
}

SIBLING_CONTROLS = {
    "timeframe": """//@version=6
strategy("sibling timeframe default from a var variable", overlay = true, initial_capital = 10000)
TF = "60"
tf = input.timeframe(TF, "Frame")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("Long", strategy.long, qty = 0.01)
""",
    "symbol": """//@version=6
strategy("sibling symbol default from a var variable", overlay = true, initial_capital = 10000)
SYM = "BINANCE:BTCUSDT"
sym = input.symbol(SYM, "Other symbol")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("Long", strategy.long, qty = 0.01)
""",
    "session": """//@version=6
strategy("sibling session default from a var variable", overlay = true, initial_capital = 10000)
SESS = "0930-1600"
sess = input.session(SESS, "Hours")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("Long", strategy.long, qty = 0.01)
""",
    "text_area": """//@version=6
strategy("sibling text area default from a var variable", overlay = true, initial_capital = 10000)
TXT = "hello"
txt = input.text_area(TXT, "Notes")
if ta.crossover(ta.ema(close, 10), ta.ema(close, 30))
    strategy.entry("Long", strategy.long, qty = 0.01)
""",
}

HEAD = '//@version=6\nstrategy("string choices")\n'
MESSAGE = (
    "The default of a string input is a variable that PineForge "
    "cannot resolve to one constant string when it reads the input."
)
HINT = (
    "Declare the variable once with a string literal and never assign to it, "
    "or write the literal in the call."
)


@pytest.mark.parametrize("source", [DEFAULT_BRANCH, DEFAULT_CONTROL])
def test_global_default_reaches_getter_descriptor_and_manifest(source):
    """A global default literal survives persistent storage and metadata emission."""
    full = transpile_full(source)
    assert 'get_input_string("Mode", std::string("Long only"))' in full["cpp"]
    assert 'get_input_string("Mode", std::string(""))' not in full["cpp"]
    assert ('{"Mode", "string", std::string("Long only"), '
            '{std::string("Long only"), std::string("Long and short")}') in full["cpp"]
    assert full["inputs"] == [{
        "title": "Mode", "type": "string", "default": "Long only",
        "options": ["Long only", "Long and short"], "supported": True,
    }]


@pytest.mark.parametrize("source", [VARIABLE_OPTIONS, OPTIONS_CONTROL])
def test_global_options_reach_descriptor_and_manifest(source):
    """Each immutable option variable supplies its literal to checked settings."""
    full = transpile_full(source)
    assert 'get_input_string("Mode", std::string("Long only"))' in full["cpp"]
    assert 'get_input_string("Mode", std::string(""))' not in full["cpp"]
    assert ('{"Mode", "string", std::string("Long only"), '
            '{std::string("Long only"), std::string("Long and short")}') in full["cpp"]
    assert full["inputs"] == [{
        "title": "Mode", "type": "string", "default": "Long only",
        "options": ["Long only", "Long and short"], "supported": True,
    }]


@pytest.mark.parametrize("declaration", [
    'var chosenText = "Amber"',
    'chosenText = "Amber"',
    'var chosenText = "Am" + "ber"',
])
def test_other_names_and_literal_expressions_are_carried(declaration):
    """The proof follows the binding and literal expression rather than its spelling."""
    full = transpile_full(
        HEAD + declaration + '\nvalue = input.string(defval=chosenText, title="Tint", '
        'options=[chosenText, "Blue"])\n')
    assert 'get_input_string("Tint", std::string("Amber"))' in full["cpp"]
    assert full["inputs"] == [{
        "title": "Tint", "type": "string", "default": "Amber",
        "options": ["Amber", "Blue"], "supported": True,
    }]


def test_carried_text_uses_string_literal_escaping():
    """Carried defaults and options preserve quotes, backslashes and Unicode."""
    full = transpile_full(
        HEAD + 'var text = "雪\\"\\\\path"\n'
        'value = input.string(text, "Escaped", options=[text])\n')
    assert r'get_input_string("Escaped", std::string("\351\233\252\"\\path"))' in full["cpp"]
    assert full["inputs"] == [{
        "title": "Escaped", "type": "string", "default": '雪"\\path',
        "options": ['雪"\\path'], "supported": True,
    }]


@pytest.mark.parametrize("body,line,col,control", [
    pytest.param(
        'varip D = "a"\nvalue = input.string(D, "Choice")\n',
        4, 22, 'var D = "a"\nvalue = input.string(D, "Choice")\n',
        id="varip"),
    pytest.param(
        'var D = "a"\nvalue = input.string(D, "Choice")\nD := "b"\n',
        4, 22, 'var D = "a"\nvalue = input.string(D, "Choice")\n',
        id="later-write"),
    pytest.param(
        'var D = "a"\nvalue = input.string(D, "Choice")\n'
        'if close > open\n    D := "b"\n',
        4, 22, 'var D = "a"\nvalue = input.string(D, "Choice")\n',
        id="branch-write"),
    pytest.param(
        'var D = "a"\nvalue = input.string(D, "Choice")\n'
        'for i = 0 to 1\n    D := "b"\n',
        4, 22, 'var D = "a"\nvalue = input.string(D, "Choice")\n',
        id="loop-write"),
    pytest.param(
        'var D = "a"\nvalue = input.string(D, "Choice")\n'
        'change() =>\n    D := "b"\n    D\n',
        4, 22, 'var D = "a"\nvalue = input.string(D, "Choice")\n',
        id="function-write"),
    pytest.param(
        'var D = str.tostring(1)\nvalue = input.string(D, "Choice")\n',
        4, 22, 'var D = "1"\nvalue = input.string(D, "Choice")\n',
        id="call-initializer"),
    pytest.param(
        'var D = syminfo.ticker\nvalue = input.string(D, "Choice")\n',
        4, 22, 'var D = "a"\nvalue = input.string(D, "Choice")\n',
        id="runtime-member"),
    pytest.param(
        'choose() =>\n    var D = "a"\n    input.string(D, "Choice")\n'
        'value = choose()\n',
        5, 18, 'var D = "a"\nchoose() =>\n    input.string(D, "Choice")\n'
        'value = choose()\n',
        id="function-local"),
    pytest.param(
        'choose(string D) =>\n    input.string(D, "Choice")\n'
        'value = choose("a")\n',
        4, 18, 'var D = "a"\nchoose() =>\n    input.string(D, "Choice")\n'
        'value = choose()\n',
        id="parameter"),
    pytest.param(
        'D = input.string("a", "First")\nvalue = input.string(D, "Choice")\n',
        4, 22, 'var D = "a"\nvalue = input.string(D, "Choice")\n',
        id="input-initializer"),
])
def test_unproven_variables_are_located_refusals(body, line, col, control):
    """Unproven defaults fail at their identifier instead of becoming empty."""
    expected = f"choice.pine:{line}:{col}: {MESSAGE}"
    with pytest.raises(CompileError, match="^" + re.escape(expected) + "$") as refused:
        transpile(HEAD + body, filename="choice.pine")
    diagnostic, = refused.value.diagnostics
    assert diagnostic.message == MESSAGE
    assert diagnostic.hint == HINT
    assert diagnostic.code == "PF-E5096"
    assert diagnostic.args == {}
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    cpp = transpile(HEAD + control)
    expected_call = ('get_input_string("Choice", std::string("1"))'
                     if 'var D = "1"' in control else
                     'get_input_string("Choice", std::string("a"))')
    assert expected_call in cpp


def test_duplicate_persistent_declaration_stops_before_the_default():
    """The existing declaration check already stops this shape before the default."""
    expected = (
        "choice.pine:4:1: Persistent bindings named 'D' still share generated "
        "storage across distinct lexical declarations; this declaration shape "
        "is not supported yet."
    )
    with pytest.raises(CompileError, match="^" + re.escape(expected) + "$"):
        transpile(
            HEAD + 'var D = "a"\nvar D = "b"\n'
            'value = input.string(D, "Choice")\n',
            filename="choice.pine",
        )


def test_settings_refusals_preserve_the_first_body_error():
    """Later metadata failures cannot hide an earlier refusal in a function body."""
    source = (HEAD + 'var D = str.tostring(1)\nchoose() =>\n'
              '    input.string(D, "Earlier")\n'
              'value = input.string(D, "Later")\n'
              'other = choose()\n')
    expected = f"choice.pine:5:18: {MESSAGE}"
    with pytest.raises(CompileError, match="^" + re.escape(expected) + "$") as refused:
        transpile(source, filename="choice.pine")
    diagnostic, = refused.value.diagnostics
    assert diagnostic.code == "PF-E5096"
    assert diagnostic.hint == HINT


def test_a_local_write_does_not_taint_another_global_binding():
    """A reassigned local with the same spelling cannot invalidate a global literal."""
    full = transpile_full(
        HEAD + 'var D = "a"\nchoose() =>\n    D = "b"\n    D := "c"\n    D\n'
        'value = input.string(D, "Choice")\nother = choose()\n')
    assert 'get_input_string("Choice", std::string("a"))' in full["cpp"]
    assert full["inputs"] == [{
        "title": "Choice", "type": "string", "default": "a", "supported": True,
    }]


@pytest.mark.parametrize("declaration", ['D = "a"', 'const string D = "a"'])
def test_existing_plain_and_const_defaults_keep_their_literals(declaration):
    """Previously folded plain and explicit constant defaults retain their exact call."""
    full = transpile_full(HEAD + declaration + '\nvalue = input.string(D, "Choice")\n')
    assert 'get_input_string("Choice", std::string("a"))' in full["cpp"]
    assert full["inputs"] == [{
        "title": "Choice", "type": "string", "default": "a", "supported": True,
    }]


def test_literal_default_and_options_are_unchanged():
    """Literal string defaults and options keep their existing getter and manifest."""
    full = transpile_full(HEAD + 'value = input.string("a", "Choice", options=["a", "b"])\n')
    assert 'get_input_string("Choice", std::string("a"))' in full["cpp"]
    assert full["inputs"] == [{
        "title": "Choice", "type": "string", "default": "a",
        "options": ["a", "b"], "supported": True,
    }]


def test_a_variable_holding_a_string_constant_carries_it():
    """A variable holding a built-in string constant keeps its getter and manifest value."""
    full = transpile_full(
        HEAD + 'var CUR = currency.USD\nvalue = input.string(CUR, "Currency")\n')
    control = transpile_full(
        HEAD + 'value = input.string(currency.USD, "Currency")\n')
    assert 'get_input_string("Currency", std::string("USD"))' in full["cpp"]
    assert full["inputs"] == control["inputs"] == [{
        "title": "Currency", "type": "string", "default": "USD", "supported": True,
    }]


@pytest.mark.parametrize("body", [
    'value = input.string(size.small, "Choice")\n',
    'var D = size.small\nvalue = input.string(D, "Choice")\n',
])
def test_visual_constants_keep_their_empty_default(body):
    """Inert visual constants retain the safe legacy coercion and unsupported descriptor."""
    full = transpile_full(HEAD + body)
    assert 'get_input_string("Choice", std::string(""))' in full["cpp"]
    assert full["inputs"] == [{
        "title": "Choice", "type": "string", "default": "",
        "options": [], "supported": False,
    }]


@pytest.mark.parametrize("kind,title,literal", [
    ("timeframe", "Frame", "60"),
    ("symbol", "Other symbol", "BINANCE:BTCUSDT"),
    ("session", "Hours", "0930-1600"),
    ("text_area", "Notes", "hello"),
])
def test_sibling_string_defaults_match_plain_controls(kind, title, literal):
    """Every string getter carries its immutable default into the call and manifest."""
    full = transpile_full(SIBLING_DEFAULTS[kind])
    control = transpile_full(SIBLING_CONTROLS[kind])
    assert f'get_input_string("{title}", std::string("{literal}"))' in full["cpp"]
    assert f'get_input_string("{title}", std::string(""))' not in full["cpp"]
    expected = {
        "title": title, "type": "string", "default": literal, "supported": True,
    }
    if kind == "symbol":
        expected["kind"] = "symbol"
    assert full["inputs"] == control["inputs"] == [expected]


def test_sibling_variable_refusals_are_located():
    """String getter defaults refuse writes and carry the immutable control."""
    body = 'var D = "60"\nvalue = input.timeframe(D, "Frame")\nD := "120"\n'
    line, col = 4, 25
    control = 'var D = "60"\nvalue = input.timeframe(D, "Frame")\n'
    expected_input = {
        "title": "Frame", "type": "string", "default": "60", "supported": True,
    }
    expected_call = 'get_input_string("Frame", std::string("60"))'
    expected = f"choice.pine:{line}:{col}: {MESSAGE}"
    with pytest.raises(CompileError, match="^" + re.escape(expected) + "$") as refused:
        transpile(HEAD + body, filename="choice.pine")
    diagnostic, = refused.value.diagnostics
    assert diagnostic.message == MESSAGE
    assert diagnostic.code == "PF-E5096"
    assert diagnostic.hint == HINT
    assert diagnostic.args == {}
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    full = transpile_full(HEAD + control)
    assert expected_call in full["cpp"]
    assert full["inputs"] == [expected_input]


@pytest.mark.parametrize("body,expected_call,expected_input,expected_descriptor", [
    pytest.param(
        'var D = "a"\n'
        'value = input.string("a", "Choice", options=["a", D])\nD := "b"\n',
        'get_input_string("Choice", std::string("a"))',
        {"title": "Choice", "type": "string", "default": "a",
         "options": [], "supported": False},
        '{"Choice", "string", std::string("a"), {}, '
        'std::numeric_limits<double>::quiet_NaN(), '
        'std::numeric_limits<double>::quiet_NaN(), '
        'std::numeric_limits<double>::quiet_NaN(), 32, false, {}, "string"}',
        id="string"),
    pytest.param(
        'var D = "a"\n'
        'value = input.session("0930-1600", "Hours", options=["a", D])\nD := "b"\n',
        'get_input_string("Hours", std::string("0930-1600"))',
        {"title": "Hours", "type": "string", "default": "0930-1600",
         "options": [], "supported": False},
        '{"Hours", "string", std::string("0930-1600"), {}, '
        'std::numeric_limits<double>::quiet_NaN(), '
        'std::numeric_limits<double>::quiet_NaN(), '
        'std::numeric_limits<double>::quiet_NaN(), 32, false, {}, "string"}',
        id="session"),
])
def test_unprovable_options_keep_the_unsupported_manifest(
        body, expected_call, expected_input, expected_descriptor):
    """Unproven options retain literal defaults and unsupported metadata without refusal."""
    full = transpile_full(HEAD + body)
    assert expected_call in full["cpp"]
    assert expected_descriptor in full["cpp"]
    assert full["inputs"] == [expected_input]


@pytest.mark.parametrize("source,getter", [
    pytest.param(BOOL_DEFAULT, "get_input_bool(", id="bool"),
    pytest.param(FLOAT_DEFAULT, "get_input_double(", id="float"),
])
def test_boolean_and_float_defaults_still_translate(source, getter):
    """Persistent boolean and float defaults retain their getter without a new refusal."""
    assert getter in transpile(source)


def test_a_folded_plain_alias_keeps_its_existing_literal():
    """An already folded plain alias remains usable as a default and option."""
    full = transpile_full(
        HEAD + 'original = "a"\nalias = original\n'
        'value = input.string(alias, "Choice", options=[alias])\n')
    assert 'get_input_string("Choice", std::string("a"))' in full["cpp"]
    assert full["inputs"] == [{
        "title": "Choice", "type": "string", "default": "a",
        "options": ["a"], "supported": True,
    }]


@pytest.fixture(scope="module")
def strategy_runs(tmp_path_factory):
    """Build each strategy once and replay defaults and the explicit alternate choice."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("string_default_runs")
    feed = chart_feed_head(engine, base, 20000)
    return execute_all(engine, feed, base, {
        "default": Build(DEFAULT_BRANCH),
        "default_control": Build(DEFAULT_CONTROL),
        "nondefault": Build(NONDEFAULT_BRANCH, {"Mode": "Long and short"}),
        "nondefault_control": Build(NONDEFAULT_CONTROL, {"Mode": "Long and short"}),
        "workaround": Build(LITERAL_WORKAROUND),
    })


def test_default_branch_runs_like_its_plain_control(strategy_runs):
    """An unset variable default makes the same trades as its plain declaration."""
    subject = ok(strategy_runs, "default")
    control = ok(strategy_runs, "default_control")
    assert set(subject.trades) == set(control.trades) == {"default"}
    assert_same_runs(subject, control)


def test_nondefault_branch_runs_like_its_control_with_both_settings(strategy_runs):
    """The alternate branch agrees at the default and with an explicit alternate input."""
    subject = ok(strategy_runs, "nondefault")
    control = ok(strategy_runs, "nondefault_control")
    assert set(subject.trades) == set(control.trades) == {"default", "override"}
    assert_same_runs(subject, control)


def test_literal_workaround_runs_like_the_plain_control(strategy_runs):
    """Writing the default literal in the call preserves the plain control's trades."""
    subject = ok(strategy_runs, "workaround")
    control = ok(strategy_runs, "default_control")
    assert set(subject.trades) == set(control.trades) == {"default"}
    assert_same_runs(subject, control)


@pytest.fixture(scope="module")
def scalar_strategy_runs(tmp_path_factory):
    """Build the boolean and float pairs once on the same default chart bars."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("scalar_default_runs")
    feed = chart_feed_head(engine, base, 8000)
    return execute_all(engine, feed, base, {
        "bool": Build(BOOL_DEFAULT),
        "bool_control": Build(BOOL_CONTROL),
        "float": Build(FLOAT_DEFAULT),
        "float_control": Build(FLOAT_CONTROL),
    })


def test_boolean_default_runs_like_its_plain_control(scalar_strategy_runs):
    """A persistent boolean default produces the same trades as its plain control."""
    subject = ok(scalar_strategy_runs, "bool")
    control = ok(scalar_strategy_runs, "bool_control")
    assert set(subject.trades) == set(control.trades) == {"default"}
    assert_same_runs(subject, control)


def test_float_default_runs_like_its_plain_control(scalar_strategy_runs):
    """A persistent float default produces the same trades as its plain control."""
    subject = ok(scalar_strategy_runs, "float")
    control = ok(scalar_strategy_runs, "float_control")
    assert set(subject.trades) == set(control.trades) == {"default"}
    assert_same_runs(subject, control)
