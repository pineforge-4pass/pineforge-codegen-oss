"""Helper request timeframes versus top-level fixed requests and value selection.

The native rows compare requested-value traces and trades on the same corpus
feed. Structural rows use the same subjects without requiring native inputs.
Literal and input controls select outside the helper, leaving each helper
call's requested timeframe fixed throughout the run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import CodeGen
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok,
    per_bar_mismatches, skip_unless_e2e_env, trade_count,
)
from tests.test_security_series_timeframe import (
    assert_selection_tail, member_body, registrations,
)


BARS = 3000
HEADER = (
    '//@version=6\n'
    'strategy("Helper timeframe values", currency = currency.NONE, '
    'initial_capital = 1000000)\n'
)
SCALAR_HELPER = (
    'sec_val(tf) => request.security(syminfo.tickerid, tf, bar_index)\n'
)
FIXED_REQUESTS = """\
slow_v = request.security(syminfo.tickerid, "240", bar_index)
day_v = request.security(syminfo.tickerid, "1D", bar_index)
"""
SELECTED_VALUE = "v = close > open ? slow_v : day_v\n"
INPUTS = """\
slow_tf = input.timeframe("240", "Slow frame")
day_tf = input.timeframe("1D", "Day frame")
"""
TAIL = """\
qty_v = 1 + math.abs(nz(v)) % 5
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = qty_v)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
"""


@dataclass(frozen=True)
class Case:
    name: str
    subject: str
    reference: str
    # Exact registration arguments, including the separate site with a
    # timeframe already used at another site.
    frames: tuple[str, ...] = ('"240"', '"1D"')
    selections: tuple[tuple[int, ...], ...] = ((0, 1),)
    values: tuple[str, ...] = ("v",)
    choices: tuple[tuple[str, str], ...] = (("chose_slow", "close > open"),)
    tuple_slots: int = 0
    overrides: tuple[tuple[str, str], ...] = ()


CASES = (
    Case(
        "parameter",
        SCALAR_HELPER + 'v = sec_val(close > open ? "240" : "1D")\n',
        FIXED_REQUESTS + SELECTED_VALUE,
    ),
    Case(
        "alias",
        SCALAR_HELPER + 'pick_tf = close > open ? "240" : "1D"\n'
        'v = sec_val(pick_tf)\n',
        FIXED_REQUESTS + SELECTED_VALUE,
    ),
    Case(
        "tuple",
        'sec_pair(tf) => request.security(syminfo.tickerid, tf, [close, bar_index])\n'
        '[c, n] = sec_pair(close > open ? "240" : "1D")\n'
        'v = c + n\n',
        '[sc, sn] = request.security(syminfo.tickerid, "240", [close, bar_index])\n'
        '[dc, dn] = request.security(syminfo.tickerid, "1D", [close, bar_index])\n'
        'c = close > open ? sc : dc\n'
        'n = close > open ? sn : dn\n'
        'v = c + n\n',
        values=("v", "c", "n"),
        tuple_slots=2,
    ),
    Case(
        "two_sites",
        SCALAR_HELPER + 'first_v = sec_val(close > open ? "240" : "1D")\n'
        'second_v = sec_val(close > open[1] ? "60" : "240")\n'
        'v = first_v + 2 * second_v\n',
        FIXED_REQUESTS
        + 'hour_v = request.security(syminfo.tickerid, "60", bar_index)\n'
        'first_v = close > open ? slow_v : day_v\n'
        'second_v = close > open[1] ? hour_v : slow_v\n'
        'v = first_v + 2 * second_v\n',
        frames=('"240"', '"60"', '"1D"', '"240"'),
        selections=((0, 2), (1, 3)),
        values=("v", "first_v", "second_v"),
        choices=(("chose_slow", "close > open"),
                 ("chose_hour", "close > open[1]")),
    ),
    Case(
        "mixed_sites",
        SCALAR_HELPER + 'fixed_v = sec_val("240")\n'
        'selected_v = sec_val(close > open ? "240" : "1D")\n'
        'v = fixed_v + 2 * selected_v\n',
        'fixed_v = request.security(syminfo.tickerid, "240", bar_index)\n'
        'day_v = request.security(syminfo.tickerid, "1D", bar_index)\n'
        'selected_v = close > open ? fixed_v : day_v\n'
        'v = fixed_v + 2 * selected_v\n',
        frames=('"240"', '"240"', '"1D"'),
        selections=((1, 2),),
        values=("v", "fixed_v", "selected_v"),
    ),
    Case(
        "nested",
        'inner(tf) => request.security(syminfo.tickerid, tf, bar_index)\n'
        'outer(tf2) => inner(tf2) + 1\n'
        'v = outer(close > open ? "240" : "1D")\n',
        FIXED_REQUESTS + 'v = (close > open ? slow_v : day_v) + 1\n',
    ),
    Case(
        "local_choice",
        'fetch_v() =>\n'
        '    local_tf = close > open ? "240" : "1D"\n'
        '    request.security(syminfo.tickerid, local_tf, bar_index)\n'
        'v = fetch_v()\n',
        'pick_v(slow_in, day_in) => close > open ? slow_in : day_in\n'
        + FIXED_REQUESTS + 'v = pick_v(slow_v, day_v)\n',
    ),
    Case(
        "literal_control",
        SCALAR_HELPER + 'slow_v = sec_val("240")\n'
        'day_v = sec_val("1D")\n' + SELECTED_VALUE,
        FIXED_REQUESTS + SELECTED_VALUE,
        selections=(),
    ),
    Case(
        "input_control",
        INPUTS + SCALAR_HELPER + 'slow_v = sec_val(slow_tf)\n'
        'day_v = sec_val(day_tf)\n' + SELECTED_VALUE,
        INPUTS
        + 'slow_v = request.security(syminfo.tickerid, slow_tf, bar_index)\n'
        'day_v = request.security(syminfo.tickerid, day_tf, bar_index)\n'
        + SELECTED_VALUE,
        frames=('get_input_string("Slow frame", std::string("240"))',
                'get_input_string("Day frame", std::string("1D"))'),
        selections=(),
        overrides=(("Slow frame", "60"), ("Day frame", "240")),
    ),
)


def source(case: Case, body: str) -> str:
    choices = "".join(f"{name} = {condition} ? 1 : 0\n"
                      for name, condition in case.choices)
    names = (*case.values, "qty_v", *(name for name, _ in case.choices))
    traces = "".join(f"// @pf-trace {name}={name}\n" for name in names)
    return HEADER + body + choices + TAIL + traces


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_helper_timeframe_registers_each_site_and_arm_once(case, monkeypatch):
    pine = source(case, case.subject)
    cpp = transpile(pine)
    assert registrations(cpp) == [
        f"{index}, {frame}, input_tf_, false, false"
        for index, frame in enumerate(case.frames)
    ]
    setup = member_body(cpp, "void configure_security_evaluators() override")
    assert "_pf_selected_tf" not in setup
    assert "current_bar_" not in setup
    assert cpp.count("const std::string _pf_selected_tf = ") == len(case.selections)
    for sites in case.selections:
        branches = []
        for sec_id in sites:
            if case.tuple_slots:
                result = "std::make_tuple(" + ", ".join(
                    f"_req_sec_{sec_id}_{slot}" for slot in range(case.tuple_slots)
                ) + ")"
            else:
                result = f"_req_sec_{sec_id}"
            branches.append(
                f"if (_pf_selected_tf == {case.frames[sec_id]}) return {result};")
        assert_selection_tail(cpp, *branches)
    if not case.selections:
        # Preserve the fixed helper path, including override-aware input getters.
        with monkeypatch.context() as previous:
            previous.setattr(CodeGen, "_expand_security_series_timeframes",
                             lambda self: None)
            assert cpp == transpile(pine)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security_helper_timeframe")
    feed = chart_feed_head(engine, base, BARS)
    builds = {
        f"{case.name}_{role}": Build(
            source(case, body),
            overrides=dict(case.overrides) if case.overrides else None,
            trace=True,
        )
        for case in CASES
        for role, body in (("subject", case.subject), ("reference", case.reference))
    }
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_helper_timeframe_runs_like_top_level_fixed_requests(case, runs):
    subject = ok(runs, f"{case.name}_subject")
    reference = ok(runs, f"{case.name}_reference")
    for role, outcome in (("subject", subject), ("reference", reference)):
        assert outcome.traces.keys() == outcome.trades.keys()
        for tag, records in outcome.traces.items():
            expected_names = {
                *case.values, "qty_v", *(name for name, _ in case.choices),
            }
            assert {record["name"] for record in records} == expected_names
            by_name = {name: [record for record in records if record["name"] == name]
                       for name in expected_names}
            assert all(len(series) == BARS for series in by_name.values())
            choices = []
            for name, _ in case.choices:
                # Wait past the first daily bar; both choices must still be
                # exercised when both requested contexts can publish values.
                selected = [record["value"] for record in by_name[name]
                            if record["bar_index"] >= 96]
                choices.append(selected)
                counts = Counter(selected)
                assert set(counts) == {0, 1}, (case.name, role, tag, name, counts)
                assert counts[0] > 0 and counts[1] > 0
            if len(choices) > 1:
                assert any(len(set(arms)) > 1 for arms in zip(*choices, strict=True)), (
                    case.name, role, tag, "the call sites never chose different arms")
            for name in (*case.values, "qty_v"):
                values = [record["value"] for record in by_name[name]
                          if math.isfinite(record["value"])]
                assert values and min(values) < max(values), (
                    case.name, role, tag, name, "requested values did not change")
            assert trade_count(outcome.trades[tag]) > 0, (case.name, role, tag)
    print(case.name, assert_same_runs(subject, reference))
    if case.overrides:
        compared, changed, first = per_bar_mismatches(
            subject.traces["default"], subject.traces["override"])
        assert compared > 0 and changed > 0, (
            case.name, "input override did not affect requested values", first)
