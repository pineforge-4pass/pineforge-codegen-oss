"""A keyword argument that skips a parameter keeps the later arguments in their own slots.

`str.format_time(time, timezone = "Asia/Tokyo")` skips the `format` parameter and
names the timezone. The merged argument list dropped the skipped position, so the
timezone moved into the format slot: the call formatted the bar's time with the
pattern "Asia/Tokyo" and ignored the timezone, and the script read a string that
is not a date at all.

Each row reads, on every chart bar, the calendar date (yyyymmdd) that
`str.format_time` printed for the bar's open time in a named timezone, and holds
it to the date Python's own timezone database gives for that instant. The oracle
is the bar's timestamp (traced by the same script) and `zoneinfo`; nothing is
read from the code under test. The positional spelling of the same call is the
control: it must read the same dates before and after the change.
"""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests._e2e import Build, chart_feed_head, execute_all, skip_unless_e2e_env

HEADER = '//@version=6\nstrategy("keyword hole")\n'
TRACE = "// @pf-trace t=time\n// @pf-trace day=day\n"
ZONE = "Asia/Tokyo"
# The date part of whatever the call printed, as a number: yyyy-MM-dd -> yyyymmdd.
# `str.substring(.., 0, 10)` takes the date whether the default pattern is the
# date alone or a date followed by a time.
READ = ('float day = str.tonumber(str.replace_all(str.substring(s, 0, 10), "-", ""))\n')

FORMS = {
    # the hole: `format` is skipped, `timezone` is named
    "keyword-after-a-hole": f'string s = str.format_time(time, timezone = "{ZONE}")\n',
    # controls: no hole
    "positional": f'string s = str.format_time(time, "yyyy-MM-dd", "{ZONE}")\n',
    "both-named": f'string s = str.format_time(time, format = "yyyy-MM-dd", timezone = "{ZONE}")\n',
}


def _oracle(timestamp_ms: float) -> float:
    local = datetime.fromtimestamp(timestamp_ms / 1000.0, ZoneInfo(ZONE))
    return float(local.strftime("%Y%m%d"))


def test_a_keyword_after_a_hole_reaches_its_own_parameter(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 1500)
    builds = {name: Build(HEADER + body + READ + TRACE, trace=True)
              for name, body in FORMS.items()}
    runs = execute_all(engine, feed, tmp_path / "runs", builds)
    problems = []
    for name in FORMS:
        outcome = runs[name]
        if outcome.error:
            problems.append(f"[{name}] {outcome.error[:400]}")
            continue
        times, days = {}, {}
        for record in outcome.traces["default"]:
            bucket = times if record["name"] == "t" else days if record["name"] == "day" else None
            if bucket is not None:
                bucket[record["bar_index"]] = record["value"]
        assert len(times) == 1500 and len(days) == 1500, (name, len(times), len(days))
        wrong = [(bar, days[bar], _oracle(times[bar])) for bar in sorted(times)
                 if not (days[bar] == _oracle(times[bar]))
                 or (isinstance(days[bar], float) and math.isnan(days[bar]))]
        # Non-vacuity: the dates move over the 1,500 bars (about 15 days of 15-minute bars).
        assert len({_oracle(t) for t in times.values()}) > 10, name
        if wrong:
            problems.append(f"[{name}] {len(wrong)} of 1500 chart bars differ from the date "
                            f"{ZONE} shows; first (bar, engine, expected): {wrong[:3]}")
    assert not problems, "\n".join(problems)
