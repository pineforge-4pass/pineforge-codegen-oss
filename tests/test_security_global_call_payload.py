"""Requested-clock history for numeric globals read in security payloads."""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import Level, Phase, CompileError
from tests._e2e import Build, chart_feed_head, execute_all, ok, same, skip_unless_e2e_env


BARS = 160
HEADER = '//@version=6\nstrategy("Global call payload")\n'
REFUSAL = "request.security helper call history is only supported in the payload itself"


def _single(payload: str, declarations: str = "", trace: str = "v") -> str:
    return (
        HEADER
        + declarations
        + f'result = request.security(syminfo.tickerid, "60", {payload})\n'
        + f"// @pf-trace {trace}=result\n"
    )


def _values(runs, build: str, trace: str) -> dict[tuple[int, int], float]:
    return {
        (row["bar_index"], row["timestamp"]): row["value"]
        for row in ok(runs, build).traces["default"]
        if row["name"] == trace
    }


@pytest.fixture(scope="module")
def chart(tmp_path_factory):
    engine = skip_unless_e2e_env()
    return engine, chart_feed_head(engine, tmp_path_factory.mktemp("global_call_payload"), BARS)


@pytest.mark.parametrize("name", ("open", "hl2"), ids=("open", "hl2"))
def test_numeric_global_history_directly_in_payload(chart, tmp_path, name):
    """The staged tree is red: the direct payload read gets PF-E5005."""
    engine, feed = chart
    call = "math.max(close, 1.0)"
    runs = execute_all(engine, feed, tmp_path, {
        "subject": Build(
            _single(f"{name}[1]", f"float {name} = {call}\n"),
            trace=True,
        ),
        "control": Build(_single("math.max(close[1], 1.0)"), trace=True),
        "builtin": Build(_single(f"{name}[1]"), trace=True),
    })
    subject = _values(runs, "subject", "v")
    control = _values(runs, "control", "v")
    builtin = _values(runs, "builtin", "v")
    assert len(subject) == BARS
    assert subject.keys() == control.keys() == builtin.keys()
    for bar, actual in subject.items():
        assert same(actual, control[bar]), (bar, actual, control[bar])
    finite = [value for value in subject.values() if math.isfinite(value)]
    assert len(set(finite)) > 1
    assert any(
        math.isfinite(value) and math.isfinite(builtin[bar]) and value != builtin[bar]
        for bar, value in subject.items()
    )


def test_numeric_global_history_in_conditional_payload_positions(chart, tmp_path):
    """The staged tree is red: both direct conditional reads get PF-E5005."""
    engine, feed = chart
    subject = (
        HEADER
        + "float bodySize = math.abs(close - open)\n"
        'v1 = request.security(syminfo.tickerid, "60", '
        "bar_index % 2 == 0 ? bodySize[1] : na)\n"
        'v2 = request.security(syminfo.tickerid, "60", '
        "bar_index % 3 == 0 and bodySize[1] > 0 ? 1.0 : 0.0)\n"
        "// @pf-trace v1=v1\n"
        "// @pf-trace v2=v2\n"
    )
    control = (
        HEADER
        + 'v1 = request.security(syminfo.tickerid, "60", '
        "bar_index % 2 == 0 ? math.abs(close[1] - open[1]) : na)\n"
        'v2 = request.security(syminfo.tickerid, "60", '
        "bar_index % 3 == 0 and math.abs(close[1] - open[1]) > 0 ? 1.0 : 0.0)\n"
        "// @pf-trace v1=v1\n"
        "// @pf-trace v2=v2\n"
    )
    runs = execute_all(engine, feed, tmp_path, {
        "subject": Build(subject, trace=True),
        "control": Build(control, trace=True),
    })
    for trace in ("v1", "v2"):
        actual = _values(runs, "subject", trace)
        expected = _values(runs, "control", trace)
        assert len(actual) == BARS
        assert actual.keys() == expected.keys()
        for bar, value in actual.items():
            assert same(value, expected[bar]), (trace, bar, value, expected[bar])


def _conditional_helper_source(position: str) -> str:
    # An `if` cannot be written inline in the argument of a call, so that position is the
    # body of a second helper that the payload calls on every bar.
    extra = ""
    if position == "ternary":
        payload = "bar_index % 2 == 0 ? h() : na"
    elif position == "and":
        payload = "bar_index % 2 == 0 and h() > 0"
    else:
        payload = "g()"
        extra = "g() =>\n    if bar_index % 2 == 0\n        h()\n    else\n        na\n"
    return (
        HEADER
        + "float bodySize = math.abs(close - open)\n"
        "h() => bodySize[1]\n"
        + extra
        + f'result = request.security(syminfo.tickerid, "60", {payload})\n'
    )


@pytest.mark.parametrize("position", ("ternary", "and", "if"), ids=("ternary", "and", "if"))
def test_conditional_helper_call_history_is_refused(position):
    source = _conditional_helper_source(position)
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.code == "PF-E5005"
    assert diagnostic.message == REFUSAL


@pytest.mark.parametrize("source", [
    _single("h()", "float bodySize = math.abs(close - open)\nh() => bodySize[1]\n"),
    _single(
        "h2()",
        "float bodySize = math.abs(close - open)\n"
        "h2() => bar_index % 3 != 1 ? bodySize[1] : na\n",
    ),
    _single(
        "h1() + h1()",
        "float bodySize = math.abs(close - open)\n"
        "h1() => bodySize[1]\n",
    ),
], ids=("unconditional-helper", "conditional-read-inside-helper", "same-helper-twice"))
def test_existing_numeric_global_helper_shapes_keep_their_staged_behavior(source):
    diagnostics = transpile_full(source)["diagnostics"]
    assert not [d for d in diagnostics if d.code == "PF-E5005"], diagnostics


def test_unconditional_helper_and_conditional_read_keep_requested_values(chart, tmp_path):
    engine, feed = chart
    runs = execute_all(engine, feed, tmp_path, {
        "unconditional": Build(
            _single(
                "h()",
                "float bodySize = math.abs(close - open)\n"
                "h() => bodySize[1]\n",
            ),
            trace=True,
        ),
        "unconditional_control": Build(
            _single("math.abs(close[1] - open[1])"),
            trace=True,
        ),
        "conditional_read": Build(
            _single(
                "h2()",
                "float bodySize = math.abs(close - open)\n"
                "h2() => bar_index % 3 != 1 ? bodySize[1] : na\n",
            ),
            trace=True,
        ),
        "conditional_control": Build(
            _single("bar_index % 3 != 1 ? math.abs(close[1] - open[1]) : na"),
            trace=True,
        ),
    })
    for subject, control in (
        ("unconditional", "unconditional_control"),
        ("conditional_read", "conditional_control"),
    ):
        actual = _values(runs, subject, "v")
        expected = _values(runs, control, "v")
        assert len(actual) == BARS
        assert actual.keys() == expected.keys()
        for bar, value in actual.items():
            assert same(value, expected[bar]), (subject, bar, value, expected[bar])


def test_math_random_global_history_is_not_sampled():
    source = _single(
        "h()",
        "float randomValue = math.random()\n"
        "h() => randomValue[1]\n",
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E5005"
    assert diagnostic.message == REFUSAL


@pytest.mark.parametrize("declarations", (
    "float spread = close - open\n",
    "mk() => close - open\nfloat spread = mk()\n",
), ids=("operator-expression-global", "helper-bound-global"))
def test_conditional_helper_reading_another_kind_of_global_is_not_refused(declarations):
    """Only a global bound to a pure numeric builtin call is refused in a
    conditional helper position: TradingView advances the history of any
    global read inside a helper once per bar in which the helper is called,
    but the other kinds are not changed by this follow-up."""
    source = _single(
        "bar_index % 2 == 0 ? h() : na",
        declarations + "h() => spread[1]\n",
    )
    diagnostics = transpile_full(source)["diagnostics"]
    assert not [d for d in diagnostics if d.code == "PF-E5005"], diagnostics
