"""A requested helper reads the history of the numeric global it is bound to."""

import math
import re

import pytest

from pineforge_codegen import transpile
from tests._e2e import Build, chart_feed_head, execute_all, ok, same, skip_unless_e2e_env


BARS = 160  # 15m chart, 60m requests
HEADER = '//@version=6\nstrategy("Global call history")\n'


def _script(payload, declarations="", result="v"):
    return (HEADER + declarations
            + f'{result} = request.security(syminfo.tickerid, "60", {payload})\n'
            + f"// @pf-trace v={result}\n")


def _subject(name, call, when=""):
    read = f"{name}[1]"
    if when:
        read = f"{when} ? {read} : na"
    return _script("h()", f"float {name} = {call}\nh() => {read}\n")


@pytest.fixture(scope="module")
def chart(tmp_path_factory):
    engine = skip_unless_e2e_env()
    return engine, chart_feed_head(engine, tmp_path_factory.mktemp("global_call_feed"), BARS)


@pytest.mark.parametrize("name,call,previous,field,when", [
    ("open", "math.max(close, 1.0)", "math.max(close[1], 1.0)", "open", ""),
    ("hl2", "math.max(close, 1.0)", "math.max(close[1], 1.0)", "hl2", ""),
    # Skip a read, not a sample: the next read must still be one hour back.
    ("bodySize", "math.abs(close - open)", "math.abs(close[1] - open[1])", "open",
     "bar_index % 3 != 1"),
], ids=["open", "hl2", "bodySize"])
def test_requested_helper_reads_global_call_history(chart, tmp_path, name, call, previous, field, when):
    engine, feed = chart
    if when:
        previous = f"{when} ? {previous} : na"
    runs = execute_all(engine, feed, tmp_path, {
        "subject": Build(_subject(name, call, when), trace=True),
        "previous": Build(_script(previous, result="c"), trace=True),
        "field": Build(_script(f"{field}[1]"), trace=True),
    })

    def values(build):
        return {(r["bar_index"], r["timestamp"]): r["value"]
                for r in ok(runs, build).traces["default"] if r["name"] == "v"}

    subject, previous_bar, builtin = map(values, ("subject", "previous", "field"))
    assert len(subject) == BARS
    assert subject.keys() == previous_bar.keys() == builtin.keys()
    for bar, actual in subject.items():
        assert same(actual, previous_bar[bar]), (bar, actual, previous_bar[bar])
    defined = [v for v in subject.values() if math.isfinite(v)]
    assert len(defined) >= BARS // 2 and len(set(defined)) > 1
    assert any(math.isfinite(v) and math.isfinite(builtin[bar]) and v != builtin[bar]
               for bar, v in subject.items())


def test_global_named_open_does_not_read_builtin_history_member():
    read = r"\b_sec\d+_hist_open\b"
    assert re.search(read + r"\[0\]", transpile(_script("open[1]")))
    assert not re.search(read, transpile(_subject("open", "math.max(close, 1.0)")))
