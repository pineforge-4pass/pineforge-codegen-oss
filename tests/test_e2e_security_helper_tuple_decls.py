"""Tuple declarations inside ``request.security`` helpers.

A helper destructuring a tuple -- ``[m, s, h] = ta.macd(...)``,
``[_, dir] = ta.supertrend(...)``, ``[wtA, wtB] = f_wavetrend(...)`` -- was
refused with "request.security does not support multi-statement helpers with
control flow": the helper plan listed ``TupleAssign`` among the loops and
switches it cannot lower. It is a declaration, not control flow. The linear
emitter now evaluates the right side once in the requested context
(``auto _secN_f_K_tuple = ...``) and binds each named element to its own
local: a TA tuple result's field, a nested helper's ``std::get``; an element
read with history gets its helper series. A bare expression statement (an if
block's trailing value, ``lastHigh := ph`` followed by ``lastHigh``) is
admitted too, and the prepasses that size TA variants, requested-bar history
and mutable-global dependencies walk both.

TradingView's tape of ``sec2_tuple_assign`` (``fixtures/security2_tv``): a
``ta.bb`` tuple, a nested helper's tuple, ``_`` placeholders and a helper read
at ``[1]`` under ``lookahead_on``, beside the same helper on the chart. The
replay test reads the tape of the week of 2026-01-05, recorded on 2026-11-29,
which TradingView computed from 2025-10-01 00:00 UTC; the other probes of
that directory keep their April week.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import Build, closed_trades, skip_unless_e2e_env
from tests._security_tapes import TAPE_TZ, replay, source, tape_exits, tape_feed


# The 2026w02 tape of ``sec2_tuple_assign`` (fixtures/security2_tv/README.md).
TAPE_2026W02 = "sec2_tuple_assign_tv_trades_2026w02.csv"
BAR_MS = 900_000               # the chart's 15 minutes
RUN_START_MS = 1759276800000   # 2025-10-01 00:00 UTC: TradingView's first computed bar
WEEK_START_MS = 1767571200000  # 2026-01-05 00:00 UTC: the tape's requested range
WEEK_END_MS = 1768176000000    # 2026-01-12 00:00 UTC: its end, and the replay's
WEEK_BARS = 672                # TradingView loaded the week without a gap
# The fewest exit instants that must be on both sides: three quarters of the
# tape's 256. With fewer, the replay's equity path is not the tape's.
SHARED_FLOOR = 192
SIGNAL_FIELDS = ("bMid", "bW", "bR", "lastU", "cMid", "cW", "cR")


def _stamp(ms: int) -> str:
    """An exit instant as the tape spells it in its ``Date and time`` column."""
    return f"{dt.datetime.fromtimestamp(ms / 1000, TAPE_TZ):%Y-%m-%d %H:%M}"


def _field_differences(tv: str, engine: str) -> list[str]:
    """The Signal fields whose text differs, by name, with both texts."""
    tv_fields, engine_fields = tv.split("|"), engine.split("|")
    if not len(tv_fields) == len(engine_fields) == len(SIGNAL_FIELDS):
        return [f"Signal tv {tv!r} engine {engine!r}"]
    return [f"{name} tv {a} engine {b}"
            for name, a, b in zip(SIGNAL_FIELDS, tv_fields, engine_fields) if a != b]


def test_helper_tuple_declarations_match_the_tape(tmp_path_factory):
    """At every exit instant that is on both the tape and the replay, the
    seven Signal fields are equal as text, the chart band width ``cW`` among
    them.

    The replay starts on TradingView's first computed bar, 2025-10-01 00:00
    UTC, and ends with the week, at 2026-01-12 00:00 UTC. The comparison
    covers the exits strictly inside the week; an exit exactly at its first
    instant belongs to an entry before the week and is left out on both sides.
    The runner's range-end close of a position still open after the last
    replayed bar is no exit of the script and is left out too.

    The probe sizes at 100% of equity, so whether an entry is refused for
    margin depends on the equity path of the three months before the week. An
    exit instant that only one side has is therefore counted and printed by
    side, and does not fail the test by itself. So that it cannot pass on a
    handful, 192 instants (``SHARED_FLOOR``, 3/4 of the tape's 256) must be
    on both sides. A replay feed that is not the 15-minute bars TradingView
    computed, from its first bar to the week's end with none missing, fails
    as NOT COMPARED."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security2_tuple_assign")
    feed = tape_feed(engine, base, RUN_START_MS, WEEK_END_MS)
    stamps = [int(line.split(",", 1)[0]) for line in feed.read_text().splitlines()[1:]]
    computed = list(range(RUN_START_MS, WEEK_END_MS, BAR_MS))
    if stamps != computed:
        in_week = sum(WEEK_START_MS <= stamp for stamp in stamps)
        pytest.fail(
            "NOT COMPARED: the replay feed is not the bars TradingView computed: first bar "
            f"{stamps[0] if stamps else None} (TradingView's {RUN_START_MS}), {len(stamps)} "
            f"bars to the week's end (TradingView's {len(computed)}), {in_week} of them in "
            f"the week (TradingView's {WEEK_BARS})", pytrace=False)
    replay(engine, base, {"probe": Build(source("sec2_tuple_assign"))}, feed=feed)
    tape = tape_exits("sec2_tuple_assign", tape=TAPE_2026W02)
    assert len(tape) == 256
    assert all(WEEK_START_MS < ms < WEEK_END_MS for ms in tape)
    # The built probe's trade rows once more: they carry the range-end mark.
    week = {trade["exit_time"]: trade["exit_comment"]
            for trade in closed_trades(engine, base / "probe", feed)
            if WEEK_START_MS < trade["exit_time"] < WEEK_END_MS
            and not trade.get("open_at_end")}
    shared = sorted(tape.keys() & week.keys())
    one_side = {"tape only": sorted(tape.keys() - week.keys()),
                "engine only": sorted(week.keys() - tape.keys())}
    print(f"helper tuple declarations, week 2026w02: {len(shared)} exit instants on both "
          f"sides, {len(one_side['tape only'])} on the tape only, "
          f"{len(one_side['engine only'])} on the engine only (tape {len(tape)}, engine "
          f"{len(week)}; times in the tape's timezone)")
    for side, instants in one_side.items():
        print(f"  {side} ({len(instants)}): " + (", ".join(map(_stamp, instants)) or "none"))
    differing = [f"{_stamp(ms)} " + "; ".join(fields) for ms in shared
                 if (fields := _field_differences(tape[ms], week[ms]))]
    assert not differing, (
        f"{len(differing)} of {len(shared)} shared exit instants differ in a Signal field:\n"
        + "\n".join(differing[:10]))
    assert len(shared) >= SHARED_FLOOR, (
        f"NOT COMPARED: {len(shared)} exit instants are on both sides, the floor is "
        f"{SHARED_FLOOR} (tape {len(tape)}, engine {len(week)})")
    print(f"  all seven Signal fields equal TradingView's at the {len(shared)} shared instants")


def test_helper_tuple_declaration_binds_ta_fields_and_nested_tuples():
    cpp = transpile(source("sec2_tuple_assign"))
    body = cpp[cpp.index("void _eval_security_0("):cpp.index("void _eval_security_1(")]
    tuple_temps = re.findall(r"auto (_sec0_f_bands_\d+_tuple) = ", body)
    assert len(tuple_temps) == 2, body
    # ta.bb's result struct: its fields, not std::get.
    assert re.search(rf"auto _sec0_f_bands_\d+_mid = {tuple_temps[0]}\.middle;", body)
    assert re.search(rf"auto _sec0_f_bands_\d+_lo = {tuple_temps[0]}\.lower;", body)
    # The nested helper's std::tuple.
    assert re.search(rf"auto _sec0_f_bands_\d+_hh = std::get<0>\({tuple_temps[1]}\);", body)


def test_helper_bare_expression_statement_is_a_block_value():
    src = """//@version=6
strategy("helper block value")
f() =>
    ph = ta.pivothigh(high, 2, 2)
    var float last = na
    if not na(ph)
        last := ph
        last
    last
l = request.security(syminfo.tickerid, "60", f())
if close > l
    strategy.entry("L", strategy.long)
"""
    cpp = transpile(src)
    body = cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]
    assert "(void)" not in body  # the trailing ``last`` has no effect to keep


def test_helper_loops_lower_in_the_requested_context():
    # A loop in a helper runs on the requested bar
    # (tests/test_e2e_security_helper_loops.py, TradingView's tapes
    # te_sec_loop_*): close[i] reads the requested close i bars back.
    src = """//@version=6
strategy("helper loop")
f() =>
    float s = 0.0
    for i = 0 to 2
        s += close[i]
    s
x = request.security(syminfo.tickerid, "60", f())
plot(x)
"""
    cpp = transpile(src)
    evaluator = cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]
    assert "for (int _sec0_f_" in evaluator
    assert "_sec0_hist_close[_hidx - 1]" in evaluator


@pytest.mark.parametrize("statement", ["g()", "array.push(hist, close)"])
def test_helper_call_statements_stay_refused(statement):
    # A bare call statement could mutate chart state from the requested
    # context (a collection, a drawing): only a block value is admitted.
    src = f"""//@version=6
strategy("helper call statement")
var hist = array.new<float>()
g() => close
f() =>
    x = close
    {statement}
    x
v = request.security(syminfo.tickerid, "60", f())
plot(v)
"""
    with pytest.raises(CompileError, match="may only use local declarations"):
        transpile(src)


@pytest.mark.parametrize("helpers", [
    'g() => [close, close > open ? "up" : "dn"]',
    # The element is a parameter, bound to a string argument.
    'g(s) => [close, s]',
    # The element is a callee local.
    'g() =>\n    t = close > open ? "up" : "dn"\n    [close, t]',
    # The callee's final expression is another helper's tuple.
    'h() => [close, close > open ? "up" : "dn"]\ng() => h()',
])
def test_string_tuple_element_read_with_history_is_refused(helpers):
    # A helper series holds doubles: a string element read with history is
    # refused by name instead of reaching the C++ compile.
    call = "g(close > open ? \"up\" : \"dn\")" if "g(s)" in helpers else "g()"
    src = f"""//@version=6
strategy("string element history")
{helpers}
f() =>
    [c, s] = {call}
    s[1] == "up" ? c : 0.0
v = request.security(syminfo.tickerid, "60", f())
plot(v)
"""
    with pytest.raises(CompileError, match="string element read with history"):
        transpile(src)
