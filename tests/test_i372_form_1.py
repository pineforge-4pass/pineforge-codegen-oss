"""History arguments keep the primitive family of their enclosing call."""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok,
    skip_unless_e2e_env,
)


LOCAL_SCRIPT = """//@version=6
strategy("untyped history parameter, local argument", overlay = true, initial_capital = 10000)
change(src) =>
    src - src[1]
wrapper(x) =>
    s = x
    change(s)
v = wrapper(close)
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
"""

LOCAL_CONTROL = """//@version=6
strategy("untyped history parameter, local argument", overlay = true, initial_capital = 10000)
change(float src) =>
    src - src[1]
wrapper(x) =>
    s = x
    change(s)
v = wrapper(close)
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
"""

BUILTIN_SCRIPT = """//@version=6
strategy("untyped history parameter, local argument", overlay = true, initial_capital = 10000)
change(src) =>
    src - src[1]
wrapper(x) =>
    change(ta.sma(x, 3))
v = wrapper(close)
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
"""

BUILTIN_CONTROL = """//@version=6
strategy("untyped history parameter, local argument", overlay = true, initial_capital = 10000)
change(float src) =>
    src - src[1]
wrapper(x) =>
    change(ta.sma(x, 3))
v = wrapper(close)
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
"""

USER_CALL_SCRIPT = """//@version=6
strategy("untyped history parameter, local argument", overlay = true, initial_capital = 10000)
change(src) =>
    src - src[1]
smooth(src) => ta.ema(src, 3)
wrapper(x) =>
    change(smooth(x))
v = wrapper(close)
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
"""

USER_CALL_CONTROL = """//@version=6
strategy("untyped history parameter, local argument", overlay = true, initial_capital = 10000)
change(float src) =>
    src - src[1]
smooth(src) => ta.ema(src, 3)
wrapper(x) =>
    change(smooth(x))
v = wrapper(close)
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
"""

TYPED_CALLER_CONTROL = """//@version=6
strategy("untyped history parameter, local argument", overlay = true, initial_capital = 10000)
change(src) =>
    src - src[1]
wrapper(float x) =>
    s = x
    change(s)
v = wrapper(close)
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
"""

TYPED_LOCAL_CONTROL = """//@version=6
strategy("untyped history parameter, local argument", overlay = true, initial_capital = 10000)
change(src) =>
    src - src[1]
wrapper(x) =>
    float s = x
    change(s)
v = wrapper(close)
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
"""

GLOBAL_CONTROL = """//@version=6
strategy("untyped history parameter, local argument", overlay = true, initial_capital = 10000)
change(src) =>
    src - src[1]
s = close
v = change(s)
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
"""

PAIRS = [
    pytest.param(LOCAL_SCRIPT, LOCAL_CONTROL, id="local"),
    pytest.param(BUILTIN_SCRIPT, BUILTIN_CONTROL, id="builtin"),
    pytest.param(USER_CALL_SCRIPT, USER_CALL_CONTROL, id="user_call"),
]


@pytest.mark.parametrize("script,control", PAIRS)
def test_derived_history_arguments_match_explicit_types(script, control):
    """A derived float history argument emits the complete typed control."""
    assert transpile(script) == transpile(control)


def _renamed(source):
    """Rename every authored function, parameter and variable consistently."""
    names = {
        "change": "difference", "src": "sample", "wrapper": "relay",
        "x": "value", "s": "saved", "v": "observed", "m": "average",
        "smooth": "filtered",
    }
    return re.sub(
        r"\b(?:change|src|wrapper|x|s|v|m|smooth)\b",
        lambda match: names[match.group()], source,
    )


@pytest.mark.parametrize("script,control", PAIRS)
def test_renamed_history_arguments_match_explicit_types(script, control):
    """All three derivations infer types independently of authored names."""
    assert transpile(_renamed(script)) == transpile(_renamed(control))


@pytest.mark.parametrize(
    "argument,primitive,history_expression",
    [
        pytest.param("bar_index", "int", "src - src[1]", id="int"),
        pytest.param("close > open", "bool", "src != src[1]", id="bool"),
    ],
)
def test_local_argument_preserves_primitive_family(
    argument, primitive, history_expression,
):
    """An immutable alias preserves int and bool without float coercion."""
    source = """//@version=6
strategy("primitive local history")
change(src) =>
    src - src[1]
wrapper(x) =>
    s = x
    change(s)
v = wrapper(close)
"""
    source = source.replace("src - src[1]", history_expression).replace(
        "wrapper(close)", f"wrapper({argument})",
    )
    control = source.replace("change(src)", f"change({primitive} src)")
    assert transpile(_renamed(source)) == transpile(_renamed(control))


@pytest.mark.parametrize("average", ["ema", "rma", "wma"])
def test_moving_average_arguments_share_fixed_result_inference(average):
    """Registered moving averages all infer the declared float family."""
    source = BUILTIN_SCRIPT.replace("ta.sma(x, 3)", f"ta.{average}(x, 3)")
    control = BUILTIN_CONTROL.replace("ta.sma(x, 3)", f"ta.{average}(x, 3)")
    assert transpile(_renamed(source)) == transpile(_renamed(control))


@pytest.mark.parametrize(
    "expression,primitive",
    [
        pytest.param("ta.highestbars(value, 3)", "int", id="int"),
        pytest.param("ta.crossover(value, open)", "bool", id="bool"),
        pytest.param("math.sqrt(value)", "float", id="math"),
    ],
)
def test_fixed_builtin_arguments_keep_their_registered_family(
    expression, primitive,
):
    """Fixed builtin results retain their semantic primitive family."""
    source = (
        '//@version=6\nstrategy("fixed builtin history")\n'
        "previous(sample) => sample[1]\n"
        f"relay(value) => previous({expression})\n"
        "observed = relay(close)\n"
    )
    control = source.replace(
        "previous(sample)", f"previous({primitive} sample)",
    )
    assert transpile(source) == transpile(control)


@pytest.mark.parametrize(
    "expression,argument,primitive",
    [
        pytest.param("value[1]", "bar_index", "int", id="inherited_int"),
        pytest.param("value[1]", "close > open", "bool", id="inherited_bool"),
    ],
)
def test_user_call_arguments_keep_the_exact_variant_return(
    expression, argument, primitive,
):
    """A nested stateful call contributes its own int or bool result."""
    source = (
        '//@version=6\nstrategy("nested primitive history")\n'
        "previous(sample) => sample[1]\n"
        f"measured(value) => {expression}\n"
        "relay(value) => previous(measured(value))\n"
        f"observed = relay({argument})\n"
    )
    control = source.replace(
        "previous(sample)", f"previous({primitive} sample)",
    )
    assert transpile(source) == transpile(control)


def test_chained_local_initializers_follow_declaration_bindings():
    """A second immutable alias follows the first alias's variant type."""
    source = LOCAL_SCRIPT.replace("    s = x\n", "    first = x\n    s = first\n")
    control = LOCAL_CONTROL.replace("    s = x\n", "    first = x\n    s = first\n")
    assert transpile(source) == transpile(control)


def _assert_refusal(source, message, line, column):
    """Pin the whole refusal and its authored source position."""
    with pytest.raises(
        CompileError,
        match="^" + re.escape(f"typing.pine:{line}:{column}: {message}") + "$",
    ) as caught:
        transpile(source, filename="typing.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.message == message
    assert diagnostic.location.file == "typing.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, column)


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param("    saved = value\n    saved := value\n", id="reassigned"),
        pytest.param("    var saved = value\n", id="persistent"),
        pytest.param("    varip saved = value\n", id="intrabar_persistent"),
    ],
)
def test_unsafe_local_arguments_keep_the_located_refusal(declaration):
    """A reassigned or persistent alias supplies no new primitive proof."""
    source = (
        '//@version=6\nstrategy("unsafe local history")\n'
        "previous(sample) => sample[1]\n"
        "relay(value) =>\n"
        + declaration
        + "    previous(saved)\nobserved = relay(close)\n"
    )
    _assert_refusal(
        source,
        "Cannot infer the per-callsite primitive type of history parameter "
        "'sample' in callable 'previous' variant cs0; declare its type explicitly.",
        3, 1,
    )


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    previous(math.abs(value))\n", id="polymorphic_builtin"),
        pytest.param(
            "    saved = transformed(value)\n    previous(saved)\n",
            id="unknown_initializer",
        ),
    ],
)
def test_unknown_argument_families_keep_the_located_refusal(body):
    """A nonfixed builtin or unresolved pure-call alias stays unknown."""
    source = (
        '//@version=6\nstrategy("unknown primitive history")\n'
        "previous(sample) => sample[1]\n"
        "transformed(value) => value\n"
        "relay(value) =>\n"
        + body
        + "observed = relay(close)\n"
    )
    _assert_refusal(
        source,
        "Cannot infer the per-callsite primitive type of history parameter "
        "'sample' in callable 'previous' variant cs0; declare its type explicitly.",
        3, 1,
    )


@pytest.mark.parametrize(
    "body,line,column",
    [
        pytest.param("relay(value) => previous(value)\n", 4, 25, id="direct"),
        pytest.param(
            "relay(value) =>\n    saved = value\n    previous(saved)\n",
            6, 13, id="local",
        ),
    ],
)
def test_profiles_cannot_collapse_distinct_primitive_families(body, line, column):
    """Direct forwarding and local aliases both reject a shared int/float body."""
    source = (
        '//@version=6\nstrategy("primitive profile collision")\n'
        "previous(sample) => sample[1]\n"
        + body
        + "other(value) => previous(value)\n"
        "integer = relay(bar_index)\n"
        "decimal = relay(close)\n"
        "timestamp = other(time)\n"
    )
    _assert_refusal(
        source,
        "Cannot safely specialize untyped parameter 'sample' of callable "
        "'previous': distinct primitive types collapse onto the same written-call "
        "variant cs0. Inline the calls or declare an explicit parameter type.",
        line, column,
    )


@pytest.mark.parametrize(
    "control",
    [
        pytest.param(LOCAL_CONTROL, id="typed_history"),
        pytest.param(BUILTIN_CONTROL, id="typed_builtin_history"),
        pytest.param(USER_CALL_CONTROL, id="typed_user_call_history"),
        pytest.param(TYPED_CALLER_CONTROL, id="typed_caller"),
        pytest.param(TYPED_LOCAL_CONTROL, id="typed_local"),
        pytest.param(GLOBAL_CONTROL, id="global_argument"),
        pytest.param(
            '//@version=6\nstrategy("settled history profile")\n'
            "previous(sample) => sample[1]\n"
            "relay(float value) => previous(ta.crossover(value, open))\n"
            "observed = relay(close)\n",
            id="settled_profile",
        ),
    ],
)
def test_previously_accepted_controls_still_translate(control):
    """An explicit type or a top-level argument remains accepted."""
    assert "class GeneratedStrategy" in transpile(control)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Build both inferred strategies and controls on one shared feed."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("derived_history")
    feed = chart_feed_head(engine, base, 600)
    traces = "// @pf-trace v=v\n// @pf-trace m=m\n"
    return execute_all(
        engine, feed, base,
        {
            "local": Build(LOCAL_SCRIPT + traces, trace=True),
            "local_control": Build(LOCAL_CONTROL + traces, trace=True),
            "user_call": Build(USER_CALL_SCRIPT + traces, trace=True),
            "user_call_control": Build(USER_CALL_CONTROL + traces, trace=True),
        },
    )


@pytest.mark.parametrize("name", ["local", "user_call"])
def test_inferred_history_runs_like_its_explicit_control(name, runs):
    """The inferred local and nested-call strategies replay like their controls."""
    subject = ok(runs, name)
    reference = ok(runs, name + "_control")
    assert subject.trades
    assert reference.trades
    assert_same_runs(subject, reference)
