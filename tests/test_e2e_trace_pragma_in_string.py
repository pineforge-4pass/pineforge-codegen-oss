"""Text inside a multiline Pine string cannot turn into a trace pragma."""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, derive_chart_feed, execute_all, ok, skip_unless_e2e_env, summary,
)


HEADER = '//@version=6\nstrategy("c6-trace-string", overlay=true)\n'
TAIL = ('n = str.length(s)\n'
        'if bar_index == 0\n'
        '    strategy.entry("L", strategy.long)\n'
        'if bar_index == 2\n'
        '    strategy.close("L")\n'
        '// @pf-trace visible=n\n')
SUBJECT = HEADER + 's = """alpha\n// @pf-trace phantom=7\nomega"""\n' + TAIL
REFERENCE = HEADER + 's = "alpha\\n// @pf-trace phantom=7\\nomega"\n' + TAIL


@pytest.fixture(scope="session")
def string_pragma_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("c6_trace_pragma_string")
    full_feed = derive_chart_feed(engine, base / "chart_full.csv")
    feed = base / "chart.csv"
    with full_feed.open() as inp, feed.open("w") as out:
        for _, line in zip(range(21), inp):
            out.write(line)
    runs = execute_all(engine, feed, base, {
        "subject": Build(SUBJECT, trace=True),
        "reference": Build(REFERENCE, trace=True),
    })
    return ok(runs, "subject"), ok(runs, "reference")


def test_only_lexical_comments_produce_traces(string_pragma_runs):
    subject, reference = string_pragma_runs
    actual = subject.traces["default"]
    expected = reference.traces["default"]
    assert len(expected) == 20
    assert {record["name"] for record in expected} == {"visible"}
    assert actual == expected, ({record["name"] for record in actual},
                                {record["name"] for record in expected})
    assert subject.trades["default"] == reference.trades["default"]
    print(f"trace string: {len(actual)} visible traces; "
          f"trades {summary(subject.trades['default'])} = "
          f"reference {summary(reference.trades['default'])}")
