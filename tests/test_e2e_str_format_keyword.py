"""A named str.format(formatString=...) reaches an order ID on the engine run."""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, derive_chart_feed, execute_all, ok, skip_unless_e2e_env, summary,
)


HEADER = '//@version=6\nstrategy("c6-format-keyword", overlay=true)\n'
TAIL = ('if bar_index == 0\n'
        '    strategy.entry(label, strategy.long)\n'
        'if bar_index == 2\n'
        '    strategy.close(label)\n'
        '// @pf-trace label_len=str.length(label)\n')


@pytest.fixture(scope="session")
def keyword_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("c6_format_keyword")
    feed = derive_chart_feed(engine, base / "chart.csv")
    runs = execute_all(engine, feed, base, {
        "named": Build(HEADER + 'label = str.format(formatString="keyword-id")\n' + TAIL,
                       trace=True),
        "reference": Build(HEADER + 'label = "keyword-id"\n' + TAIL, trace=True),
    })
    return ok(runs, "named"), ok(runs, "reference")


def test_named_format_string_books_the_reference_order(keyword_runs):
    named, reference = keyword_runs
    actual = named.trades["default"]
    expected = reference.trades["default"]
    reference_values = [r["value"] for r in reference.traces["default"]
                        if r["name"] == "label_len"]
    actual_values = [r["value"] for r in named.traces["default"]
                     if r["name"] == "label_len"]
    assert reference_values and all(value == 10 for value in reference_values)
    assert actual_values == reference_values, (f"label lengths: "
                                               f"named {actual_values[:3]}, "
                                               f"reference {reference_values[:3]}")
    assert actual == expected, f"named {summary(actual)}; reference {summary(expected)}"
    print(f"str.format keyword: {len(actual_values)} label traces equal 10; "
          f"named {summary(actual)} = reference {summary(expected)}")
