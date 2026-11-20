"""A UDT field declaration does not bind a bare bar_index in a requested payload."""

import math

from tests._e2e import Build, chart_feed_head, execute_all, ok, same, skip_unless_e2e_env


def test_requested_bar_index_with_same_named_field(tmp_path):
    engine = skip_unless_e2e_env()
    bars = 80
    feed = chart_feed_head(engine, tmp_path, bars)
    header = '//@version=6\nstrategy("Requested bar index")\n'
    body = ('v = request.security(syminfo.tickerid, "60", bar_index)\n'
            'chartIndex = bar_index\n'
            '// @pf-trace requested=v\n'
            '// @pf-trace chart=chartIndex\n')
    runs = execute_all(engine, feed, tmp_path, {
        "subject": Build(header + "type Sample\n    int bar_index\n" + body, trace=True),
        "control": Build(header + body, trace=True),
    })

    def column(build, name):
        return {(r["bar_index"], r["timestamp"]): r["value"]
                for r in ok(runs, build).traces["default"] if r["name"] == name}

    subject = column("subject", "requested")
    control = column("control", "requested")
    chart = column("subject", "chart")
    assert len(subject) == bars
    assert subject.keys() == control.keys() == chart.keys()
    for bar, actual in subject.items():
        assert same(actual, control[bar]), (bar, actual, control[bar])
        assert chart[bar] == bar[0]
    defined = [bar for bar in subject if math.isfinite(subject[bar])]
    assert len({subject[bar] for bar in defined}) > 1
    assert any(subject[bar] != chart[bar] for bar in defined)
