"""A helper whose last expression reads an element of an array that no variable holds returns that element's value.

`array.get(make_values(), 0)` and its spellings read the first element of a
temporary array: a copy, a slice, the result of a call. The helper's result is
the element, a string here, whatever the receiver is spelled as: a string
function in Pine, which TradingView compiles. The translation tests beside this
file (test_array_terminal_returns.py) read the helper's declared type; these
rows run the script on the engine and read the value back: whether the returned
string equals "x" and its length, which is 1.

The last row is the one whose body also declares a scalar local named `array`
before `array.get(global_values, 0)`: it is held to the same value.
"""

from __future__ import annotations

import pytest

from tests._e2e import Build, chart_feed_head, execute_all, skip_unless_e2e_env

BARS = 600

TEMPORARY_TERMINALS = (
    "array.get(make_values(), 0)",
    "array.copy(make_values()).get(0)",
    "array.copy(id=make_values()).get(0)",
    "make_values().copy().get(0)",
    "array.get(array.slice(values, 0, 1), 0)",
)

ROWS = {terminal: f"    {terminal}\n" for terminal in TEMPORARY_TERMINALS}
ROWS["int array = 1; array.get(values, 0)"] = (
    "    int array = 1\n    array.get(values, 0)\n")


def script(body: str) -> str:
    return (
        '//@version=6\nstrategy("Terminal element of an array")\n'
        'values = array.from("x")\n'
        "make_values() =>\n    array.copy(values)\n"
        f"blocked() =>\n{body}"
        "observed = blocked()\n"
        'is_x = observed == "x" ? 1 : 0\n'
        "length = str.length(observed)\n"
        "// @pf-trace is_x=is_x\n// @pf-trace length=length\n"
    )


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("array_terminal_temporaries")
    feed = chart_feed_head(engine, base, BARS)
    return execute_all(
        engine, feed, base / "runs",
        {f"t{index}": Build(script(body), trace=True)
         for index, body in enumerate(ROWS.values())})


@pytest.mark.parametrize("index", range(len(ROWS)), ids=list(ROWS))
def test_the_helper_returns_the_element_the_array_holds(runs, index):
    label = list(ROWS)[index]
    outcome = runs[f"t{index}"]
    assert outcome.error is None, f"[{label}] {outcome.error}"
    for name in ("is_x", "length"):
        column = {r["bar_index"]: r["value"] for r in outcome.traces["default"] if r["name"] == name}
        assert len(column) == BARS, (name, len(column))
        wrong = [(bar, column[bar]) for bar in sorted(column) if not column[bar] == 1.0]
        assert not wrong, f"[{label}] {name}: {len(wrong)} of {BARS} bars are not 1; first {wrong[:3]}"
