"""A keyword argument that skips a parameter keeps the later arguments in their own slots.

`str.format_time(time, timezone = "Asia/Tokyo")` skips the `format` parameter and
names the timezone. The merged argument list dropped the skipped position, so the
timezone moved into the format slot: the call formatted the bar's time with the
pattern "Asia/Tokyo" and ignored the timezone, and the script read a string that
is not a date at all.

The keyword-hole case reads, on every chart bar, the calendar date (yyyymmdd) that
`str.format_time` printed for the bar's open time in a named timezone, and holds
it to the date Python's own timezone database gives for that instant. The oracle
is the bar's timestamp (traced by the same script) and `zoneinfo`; nothing is
read from the code under test. The positional spelling of the same call is the
control: it must read the same dates before and after the change.

Omitted format and timezone arguments also follow the documented defaults.
Those cases log full strings and trace their lengths on fixed timestamps.
Every expected string is derived by hand from the documented format and
time-zone rules and from the calendar and zone offsets of the fixed
timestamps; none was measured on TradingView.
"""

from __future__ import annotations

import difflib
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, reference_codegen,
    skip_unless_e2e_env, transpile_json,
)

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


# Pine User Manual, Time / Formatting dates and times: omitted format is
# yyyy-MM-dd'T'HH:mm:ssZ; omitted timezone is the exchange's syminfo.timezone.
ISO_PATTERN = "yyyy-MM-dd'T'HH:mm:ssZ"
IDENTITY_BASE = "7d7681dcef6918f0cb304831320e5d8e5b545285"


@pytest.mark.parametrize("arguments,named,expected", (
    ("time", "time = time",
     '''pine_str_format_time(current_bar_.timestamp, "yyyy-MM-dd'T'HH:mm:ssZ", syminfo_.timezone)'''),
    ('time, "yyyy"', 'time = time, format = "yyyy"',
     'pine_str_format_time(current_bar_.timestamp, std::string("yyyy"), syminfo_.timezone)'),
    ('time, timezone = "Asia/Tokyo"', 'time = time, timezone = "Asia/Tokyo"',
     '''pine_str_format_time(current_bar_.timestamp, "yyyy-MM-dd'T'HH:mm:ssZ", std::string("Asia/Tokyo"))'''),
), ids=("both-omitted", "timezone-omitted", "format-omitted"))
def test_format_time_omitted_slots_emit_documented_defaults(arguments, named, expected):
    # Documented defaults fill omitted slots; explicit strings keep std::string wrappers.
    def emitted(args):
        return transpile(HEADER + f"string s = str.format_time({args})\n")

    cpp = emitted(arguments)
    assert expected in cpp
    assert cpp == emitted(named)


@pytest.mark.parametrize("context,body", (
    ("user-function",
     "stamp(t) =>\n"
     "    str.format_time(t)\n"
     "string s = stamp(time)\n"),
    ("method",
     "type TextBox\n"
     "    string value\n"
     "method renderText(TextBox self) => self.value + str.format_time(time)\n"
     'holder = TextBox.new("ok")\n'
     "string s = holder.renderText()\n"),
    ("security-payload",
     'float length = request.security(syminfo.tickerid, "60", '
     "str.length(str.format_time(time)))\n"),
), ids=("user-function", "method", "security-payload"))
def test_format_time_omitted_slots_compile_in_nested_contexts(context, body):
    cpp = transpile(HEADER + body)
    # The documented defaults apply in each context: ISO format and the exchange zone.
    assert '''"yyyy-MM-dd'T'HH:mm:ssZ", syminfo_.timezone)''' in cpp
    compile_cpp(cpp, label=f"format_time defaults in {context}")


@pytest.mark.parametrize("body", (
    'string s = str.format_time(time, "yyyy-MM-dd", "UTC")\n',
    'string s = str.format_time(time, format = "yyyy-MM-dd", timezone = "UTC")\n',
    'string s = str.format_time(time, "yyyy-MM-dd", timezone = "UTC")\n',
    "plot(close)\n",
    'plot(hour(time))\nstring s = str.tostring(close, "#.##")\n',
    'string s = str.format("{0}", year(time))\n',
), ids=("explicit-positional", "explicit-keywords", "explicit-mixed",
        "price-only", "calendar-and-string", "string-format"))
def test_format_time_preserves_explicit_calls_and_unrelated_cpp(body, tmp_path):
    reference = reference_codegen(IDENTITY_BASE)
    if reference is None:
        pytest.fail(f"required C++ identity reference {IDENTITY_BASE} unavailable; restore git history")
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + body, encoding="utf-8")
    now, before = transpile_json(pine), transpile_json(pine, reference)
    assert now["ok"] and before["ok"], (now["diagnostics"], before["diagnostics"])
    # Supplying both optional slots, or never using them, leaves format_time lines unchanged.
    changed = [
        line for line in difflib.unified_diff(
            before["cpp"].splitlines(), now["cpp"].splitlines(), lineterm="", n=0,
        )
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    assert not any("format_time" in line for line in changed), "\n".join(changed)
    if "str.format_time" in body:
        # Explicit Pine arguments map directly to the same three C++ argument slots.
        call = ('pine_str_format_time(current_bar_.timestamp, std::string("yyyy-MM-dd"), '
                'std::string("UTC"))')
        assert call in now["cpp"] and call in before["cpp"]
        positional = transpile(HEADER + 'string s = str.format_time(time, "yyyy-MM-dd", "UTC")\n')
        assert now["cpp"] == positional
    else:
        assert "pine_str_format_time" not in now["cpp"]
        assert "pine_str_format_time" not in before["cpp"]


# 1706745600000 ms is 2024-02-01 00:00 UTC; bars advance by 900000 ms (15 minutes).
FORMAT_STAMPS = (1706745600000, 1706746500000, 1706747400000)
# yyyy-MM-dd + literal T + HH:mm:ss + signed HHMM offset: UTC adds +0000.
# Expected strings are hand-derived from the documented rules, not measured on TradingView.
UTC_FORMATTED = (
    "2024-02-01T00:00:00+0000",
    "2024-02-01T00:15:00+0000",
    "2024-02-01T00:30:00+0000",
)
# New York in February is UTC-5: midnight UTC is January 31 at 19:00, offset -0500.
# Expected strings are hand-derived from the documented rules, not measured on TradingView.
NEW_YORK_FORMATTED = (
    "2024-01-31T19:00:00-0500",
    "2024-01-31T19:15:00-0500",
    "2024-01-31T19:30:00-0500",
)


@pytest.fixture(scope="module")
def format_time_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("format_time_defaults")
    feed = base / "chart.csv"
    feed.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(f"{stamp},100,101,99,100.5,1\n" for stamp in FORMAT_STAMPS),
        encoding="utf-8",
    )
    forms = {
        "both-omitted": ("time", "syminfo.timezone"),
        "format-omitted": ('time, timezone = "UTC"', '"UTC"'),
        "timezone-omitted": (f'time, "{ISO_PATTERN}"', "syminfo.timezone"),
    }
    builds = {}
    for name, (arguments, zone) in forms.items():
        source = (
            HEADER
            + f"string s = str.format_time({arguments})\n"
            + f'string documented = str.format_time(time, "{ISO_PATTERN}", {zone})\n'
            + "float length = str.length(s)\n"
            + "float agrees = s == documented ? 1.0 : 0.0\n"
            + 'log.info("format-time=" + s)\n'
            + "// @pf-trace t=time\n"
            + "// @pf-trace length=length\n"
            + "// @pf-trace agrees=agrees\n"
        )
        # The existing runner accepts the exchange zone through runtime overrides.
        builds[name] = Build(
            source, trace=True,
            overrides={"input_tf": "15", "script_tf": "15",
                       "runtime_overrides": {"timezone": "America/New_York"}},
        )
    return execute_all(engine, feed, base, builds)


def _assert_format_time_readout(outcome, tag, expected):
    prefix = "[INFO] format-time="
    printed = tuple(line[len(prefix):] for line in outcome.logs[tag].splitlines()
                    if line.startswith(prefix))
    assert printed == expected
    records = outcome.traces[tag]
    assert len(records) == 9  # Three numeric traces on each of three feed bars.
    assert {record["name"] for record in records} == {"t", "length", "agrees"}
    for name, values in (
        ("t", FORMAT_STAMPS),
        ("length", (24, 24, 24)),  # 10 date + 1 T + 8 clock + 5 offset characters.
        ("agrees", (1, 1, 1)),  # Omitted arguments equal the explicit documented defaults.
    ):
        column = [(record["bar_index"], record["value"]) for record in records
                  if record["name"] == name]
        assert column == list(enumerate(values)), (tag, name, column)


@pytest.mark.parametrize("tag,expected", (
    ("default", UTC_FORMATTED),
    ("override", NEW_YORK_FORMATTED),
), ids=("utc", "new-york"))
def test_format_time_without_optional_arguments_prints_iso_in_the_exchange_zone(
        format_time_runs, tag, expected):
    """Omitting both slots uses the ISO format in the exchange zone.

    The expected strings are hand-derived from the documented format and
    time-zone rules, not TradingView-measured.
    """
    _assert_format_time_readout(ok(format_time_runs, "both-omitted"), tag, expected)


def test_format_time_timezone_keyword_without_format_prints_full_iso(format_time_runs):
    """An explicit timezone with an omitted format prints full ISO strings.

    The expected strings are hand-derived from the documented format and
    time-zone rules, not TradingView-measured.
    """
    outcome = ok(format_time_runs, "format-omitted")
    # Explicit UTC stays UTC even when the exchange zone is New York.
    for tag in ("default", "override"):
        _assert_format_time_readout(outcome, tag, UTC_FORMATTED)


def test_format_time_explicit_pattern_without_timezone_uses_exchange_offset(format_time_runs):
    """An explicit pattern with an omitted timezone uses the exchange offset.

    The expected strings are hand-derived from the documented format and
    time-zone rules, not TradingView-measured.
    """
    outcome = ok(format_time_runs, "timezone-omitted")
    _assert_format_time_readout(outcome, "default", UTC_FORMATTED)
    _assert_format_time_readout(outcome, "override", NEW_YORK_FORMATTED)
