"""``bar_index`` under a builtin call kept on the chart's terms is the requested bar's.

A request.security payload's ``bar_index`` is the count of requested bars before
the one evaluated: an evaluator that reads it keeps a counter
(``_sec<N>_bar_index_``) advanced where it opens a requested slot
(tests/test_e2e_security_bar_index.py). A builtin call the builder does not
inline on the requested bar (``array.new``) is rendered by the expression
visitor with every node of its subtree on the chart's terms, and there
``bar_index`` was spelled ``pine_bar_index()``, the chart host's index at the
moment the evaluator runs, while the ``close`` beside it was already the
requested bar's. The counter now answers ``bar_index`` under such a call too,
as it does bare, under ``nz`` and through a helper.
"""

import pytest

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("payload bar_index", overlay=true, initial_capital=100000)\n'


def _evaluator(cpp: str, sec_id: int = 0) -> str:
    start = cpp.index(f"void _eval_security_{sec_id}")
    body = cpp[start:]
    return body[:body.index("\n    }\n")]


PAYLOAD = (
    "payload() =>\n"
    "    a = array.new<float>(size=3, initial_value=float(bar_index + 2))\n"
    "    a.size() == 0 ? -1000.0 : a.sum()\n"
)


@pytest.mark.parametrize(
    "timeframe",
    [
        pytest.param("timeframe.period", id="chart-timeframe"),
        pytest.param('"60"', id="hourly"),
    ],
)
def test_an_array_fill_reads_the_requested_bar_index(timeframe):
    cpp = transpile(
        HEADER + PAYLOAD
        + f"float x = request.security(syminfo.tickerid, {timeframe}, payload())\n"
        "if x > 0\n    strategy.entry(\"L\", strategy.long)\n"
    )
    body = _evaluator(cpp)
    assert "(double)((_sec0_bar_index_ + 2))" in body, body
    assert "pine_bar_index()" not in body, body


def test_an_array_size_reads_the_requested_bar_index():
    cpp = transpile(
        HEADER
        + "payload() =>\n"
        "    a = array.new<float>(bar_index % 3 + 1, 1.0)\n"
        "    a.sum()\n"
        "float x = request.security(syminfo.tickerid, \"60\", payload())\n"
        "if x > 0\n    strategy.entry(\"L\", strategy.long)\n"
    )
    body = _evaluator(cpp)
    assert body.count("_sec0_bar_index_") >= 2, body
    assert "pine_bar_index()" not in body, body


def test_the_chart_keeps_its_own_bar_index():
    cpp = transpile(
        HEADER + PAYLOAD
        + "float x = request.security(syminfo.tickerid, \"60\", payload())\n"
        "if bar_index > 6 and x > 0\n    strategy.entry(\"L\", strategy.long)\n"
    )
    body = _evaluator(cpp)
    chart = cpp.replace(body, "")
    assert "pine_bar_index()" not in body, body
    assert "pine_bar_index()" in chart


def test_a_script_variable_named_bar_index_is_not_the_counter():
    cpp = transpile(
        HEADER
        + "bar_index = close * 2\n"
        "float x = request.security(syminfo.tickerid, \"60\", "
        "array.sum(array.new<float>(3, bar_index)))\n"
        "if x > 0\n    strategy.entry(\"L\", strategy.long)\n"
    )
    assert "_sec0_bar_index_" not in cpp


def test_an_array_that_does_not_read_bar_index_keeps_no_counter():
    cpp = transpile(
        HEADER
        + "payload() =>\n"
        "    a = array.new<float>(size=3, initial_value=close)\n"
        "    a.sum()\n"
        "float x = request.security(syminfo.tickerid, \"60\", payload())\n"
        "if x > 0\n    strategy.entry(\"L\", strategy.long)\n"
    )
    assert "_sec0_bar_index_" not in cpp
    assert "pine_bar_index()" not in _evaluator(cpp)
