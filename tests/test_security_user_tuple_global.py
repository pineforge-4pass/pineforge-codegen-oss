"""A global that is an element of a user function's tuple can feed request.security.

TradingView evaluates a tuple declaration `[_, g] = pair(x)` on the REQUESTED
bar when a request.security payload reads `g`, directly or through a function
that reads the global: the global is a plain value of the requested context. A
function with state (a ta.* call, a `var`, a request) advances that state once
per evaluation, so reading its tuple once per element would advance it twice;
that case, and the history of an element, stay refused.

The value rows hold the requested series to numbers written here from the
source and the 1-day alignment of a 15-minute chart (96 chart bars per day),
read at bars inside a day so that the day boundary itself is not at stake, and
to the same payload written inline, which has always lowered.
"""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import (
    Build, chart_feed_head, execute_all, per_bar_mismatches, skip_unless_e2e_env,
)

HEADER = '//@version=6\nstrategy("Tuple global request payload")\n'
REQUEST = 'request.security(syminfo.tickerid, "{tf}", {payload}, gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_off)'
PAIR = "pair(float value) => [value, value + 7.0]\nfloat x = 100.0 + 13.0 * bar_index\n[_, g] = pair(x)\n"


def _source(declarations: str, observed_payload: str, expected_payload: str, tf: str) -> str:
    return (
        HEADER + declarations
        + f"float observed = {REQUEST.format(tf=tf, payload=observed_payload)}\n"
        + f"float expected = {REQUEST.format(tf=tf, payload=expected_payload)}\n"
        + "plot(observed)\nplot(expected)\n"
        + "// @pf-trace observed=observed\n// @pf-trace expected=expected\n"
    )


# (id, declarations, payload that reads the element, the same value written inline)
FORMS = [
    ("through-a-reader", PAIR + "readGlobal() => g\n", "readGlobal()", "107.0 + 13.0 * bar_index"),
    ("direct-read", PAIR, "g", "107.0 + 13.0 * bar_index"),
    ("function-of-builtins",
     "pair() => [close, open]\n[a, b] = pair()\n", "a - b", "close - open"),
    ("both-elements",
     "pair() => [close, open]\n[a, b] = pair()\n", "a + b", "close + open"),
]


@pytest.mark.parametrize("name,declarations,observed,expected", FORMS,
                         ids=[form[0] for form in FORMS])
def test_a_pure_user_tuple_element_translates_in_a_payload(name, declarations, observed, expected):
    cpp = transpile(_source(declarations, observed, expected, "1D"))
    assert "_req_sec_0" in cpp and "_req_sec_1" in cpp


STATEFUL = [
    ("ta-in-the-function",
     "pair(float value) => [ta.sma(value, 3), value]\n[a, b] = pair(close)\n", "a"),
    ("var-in-the-function",
     "pair(float value) =>\n    var float total = 0.0\n    total += value\n    [total, value]\n"
     "[a, b] = pair(close)\n", "a"),
]


@pytest.mark.parametrize("name,declarations,payload", STATEFUL, ids=[row[0] for row in STATEFUL])
def test_a_stateful_user_tuple_element_stays_refused(name, declarations, payload):
    with pytest.raises(CompileError, match="an element of a tuple declaration it cannot re-evaluate") as caught:
        transpile(_source(declarations, payload, "close", "60"))
    assert [d.code for d in caught.value.diagnostics] == ["PF-E3044"]


def _column(outcome, name):
    return {r["bar_index"]: r["value"] for r in outcome.traces["default"] if r["name"] == name}


def test_the_probe_shapes_read_the_requested_bar(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 1500)
    builds = {
        name: Build(_source(declarations, observed, expected, "1D" if name in ("through-a-reader", "direct-read") else "60"),
                    trace=True)
        for name, declarations, observed, expected in FORMS
    }
    runs = execute_all(engine, feed, tmp_path / "runs", builds)
    for name, outcome in runs.items():
        assert outcome.error is None, f"[{name}] {outcome.error}"
        compared, mismatched, first = per_bar_mismatches(
            [r for r in outcome.traces["default"] if r["name"] == "observed"],
            [dict(r, name="observed") for r in outcome.traces["default"] if r["name"] == "expected"],
            ("observed", "inline"),
        )
        assert compared > 0, f"[{name}] no trace records"
        assert mismatched == 0, f"[{name}] {first}"
    # The 15-minute chart has 96 bars per day, the first bar at 00:00. Bars inside
    # a day read the last COMPLETED day's value: day k completes at bar 96k + 95
    # and g = (100 + 13 k) + 7 on requested bar k.
    for name in ("through-a-reader", "direct-read"):
        observed = _column(runs[name], "observed")
        assert math.isnan(observed[50]), (name, observed[50])
        assert observed[150] == 107.0, (name, observed[150])
        assert observed[250] == 120.0, (name, observed[250])
        assert observed[400] == 146.0, (name, observed[400])
    # Non-vacuity for the builtin forms: the values move.
    for name in ("function-of-builtins", "both-elements"):
        values = {v for v in _column(runs[name], "observed").values() if not math.isnan(v)}
        assert len(values) > 10, (name, len(values))
