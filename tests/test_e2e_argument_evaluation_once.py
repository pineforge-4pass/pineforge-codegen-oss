"""Every argument of a lowered builtin is evaluated once per execution.

Pine evaluates each call argument exactly once (only ``and`` / ``or`` and the
``?:`` arms are lazy). The fixnan audit (lane W2, F03) ran every argument slot
of the builtins below through a counting probe and found lowering templates
that re-ran an argument's C++, reading it twice or once per loop iteration,
or skipped it:

- ``nz(x, y)`` evaluated ``y`` only on the bars whose ``x`` was na;
- ``math.round(x, precision)`` read the precision twice;
- ``str.startswith`` / ``endswith`` / ``substring`` / ``replace`` /
  ``replace_all`` / ``repeat`` read the prefix, suffix, source, begin
  position, target, replacement or count twice or once per iteration;
- ``array.lastindexof`` / ``binary_search*`` / ``join`` / ``concat`` read the
  value, separator or second array twice or once per iteration; ``concat`` of
  a temporary array took ``begin()`` and ``end()`` of two temporaries.

A stateful call in such an argument (a ``ta.*`` compute(), a user function
with state) then ran more or fewer times per bar than on TradingView. An
argument that is not a plain read is now bound once, left to right; plain
reads keep their lowering byte for byte.

TradingView's own tape of the counting probe (``fixtures/w2_trio_tv/
eval_counts``) shows every slot evaluated once per bar and ``str.repeat("ab",
3, ",")`` spelling ``ab,ab,ab``; it is replayed on the corpus ETH 15m feed.
Each slot also runs as its own case, and stateful twins compare the shapes
with their argument hoisted into a variable.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, closed_trades, derive_chart_feed,
    execute_all, ok, skip_unless_e2e_env,
)


FIXTURES = Path(__file__).parent / "fixtures" / "w2_trio_tv"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone

HEAD = ('//@version=6\nstrategy("w2 argument evaluation", overlay = true)\n'
        "var cnt = array.new_int(64, 0)\n")
PROBES = """pf(int k, float v) =>
    array.set(cnt, k, array.get(cnt, k) + 1)
    v
pi(int k, int v) =>
    array.set(cnt, k, array.get(cnt, k) + 1)
    v
ps(int k, string v) =>
    array.set(cnt, k, array.get(cnt, k) + 1)
    v
pa(int k) =>
    array.set(cnt, k, array.get(cnt, k) + 1)
    array.from(1.0, 2.0, 3.0)
srt = array.from(1.0, 2.0, 3.0)
astr = array.from("a", "b", "c")
a3 = array.from(1.0, 2.0, 3.0)
"""
# One counting probe per shape ("K" is its slot); "!" marks a statement.
SLOTS = [
    ("nz_replacement", "nz(close, pf(K, 0.0))"),
    ("nz_source", "nz(pf(K, close), 0.0)"),
    ("math_round_precision", "math.round(close, pi(K, 2))"),
    ("math_round_number", "math.round(pf(K, close), 2)"),
    ("str_startswith_prefix", 'str.startswith("abc", ps(K, "a"))'),
    ("str_endswith_source", 'str.endswith(ps(K, "abc"), "c")'),
    ("str_endswith_suffix", 'str.endswith("abc", ps(K, "c"))'),
    ("str_substring_source", 'str.substring(ps(K, "abcdef"), 1, 3)'),
    ("str_substring_begin", 'str.substring("abcdef", pi(K, 1), 3)'),
    ("str_substring_end", 'str.substring("abcdef", 1, pi(K, 3))'),
    ("str_replace_target", 'str.replace("aXa", ps(K, "X"), "Y")'),
    ("str_replace_replacement", 'str.replace("aXa", "X", ps(K, "Y"))'),
    ("str_replace_all_target", 'str.replace_all("aXaX", ps(K, "X"), "Y")'),
    ("str_replace_all_replacement", 'str.replace_all("aXaX", "X", ps(K, "Y"))'),
    ("str_repeat_source", 'str.repeat(ps(K, "ab"), 3)'),
    ("str_repeat_count", 'str.repeat("ab", pi(K, 3))'),
    ("str_repeat_separator", 'str.repeat("ab", 3, ps(K, ","))'),
    ("array_lastindexof_value", "array.lastindexof(srt, pf(K, 1.0))"),
    ("array_lastindexof_string", 'array.lastindexof(astr, ps(K, "a"))'),
    ("array_lastindexof_method", "srt.lastindexof(pf(K, 1.0))"),
    ("array_binary_search_value", "array.binary_search(srt, pf(K, 2.0))"),
    ("array_binary_search_leftmost_value", "array.binary_search_leftmost(srt, pf(K, 2.0))"),
    ("array_binary_search_rightmost_value", "array.binary_search_rightmost(srt, pf(K, 2.0))"),
    ("array_join_separator", 'array.join(astr, ps(K, ","))'),
    ("array_join_method", 'astr.join(ps(K, ","))'),
    ("array_concat_array", "!array.concat(a3, pa(K))"),
    ("array_concat_method", "!a3.concat(pa(K))"),
]
BARS = 120


def _slot_script() -> str:
    src = HEAD + PROBES
    for k, (_, tpl) in enumerate(SLOTS, start=1):
        body = tpl.replace("K", str(k))
        src += body[1:] + "\n" if body.startswith("!") else f"s{k} = {body}\n"
    for k in range(1, len(SLOTS) + 1):
        src += f"// @pf-trace c{k}=array.get(cnt, {k})\n"
    return src


@pytest.fixture(scope="module")
def slot_counts(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_arg_slots")
    feed = chart_feed_head(engine, base, BARS)
    run = ok(execute_all(engine, feed, base, {"slots": Build(_slot_script(), trace=True)}),
             "slots")
    last: dict[int, float] = {}
    bars = 0
    for rec in run.traces["default"]:
        slot = int(rec["name"][1:])
        last[slot] = rec["value"]
        bars = max(bars, rec["bar_index"] + 1)
    return last, bars


@pytest.mark.parametrize("slot", range(1, len(SLOTS) + 1),
                         ids=[name for name, _ in SLOTS])
def test_each_argument_is_evaluated_once_per_bar(slot_counts, slot):
    last, bars = slot_counts
    assert bars == BARS
    assert last.get(slot) == bars, (
        f"{SLOTS[slot - 1][1]}: evaluated {last.get(slot)} times in {bars} bars")


def _tape(name: str) -> list[dict]:
    trades: dict[str, dict] = {}
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            side = "entry" if row["Type"].startswith("Entry") else "exit"
            when = dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
            trades.setdefault(row["Trade number"], {})[side] = (
                int(when.replace(tzinfo=TAPE_TZ).timestamp() * 1000), row["Signal"])
    return list(trades.values())


# Fields 22 and 23 are the right side of an ``and`` whose left side is false
# and an untaken ``?:`` arm: lazy on both, so negative, by each run's own bar
# count. Field 24 counts color.from_gradient's first argument: PineForge
# lowers that visual-only builtin to a default color and warns, so it
# evaluates none of its arguments (support_checker.COSMETIC_COLOR_FUNC).
LAZY_FIELDS = (22, 23)
COSMETIC_FIELD = 24


def test_the_tradingview_count_tape_replays(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_arg_tape")
    feed = derive_chart_feed(engine, base / "chart.csv")
    ok(execute_all(engine, feed, base, {
        "tape": Build((FIXTURES / "eval_counts.pine").read_text())}), "tape")
    by_entry = {t["entry_time"]: t for t in closed_trades(engine, base / "tape", feed)}
    tape = _tape("eval_counts")
    assert len(tape) == 7
    for trade in tape:
        want = trade["exit"][1].split("|")
        assert want[:21] == ["0"] * 21 and want[23:26] == ["0"] * 3
        assert want[26:] == ["ab,ab,ab", "6"]
        twin = by_entry.get(trade["entry"][0])
        assert twin is not None and twin["exit_time"] == trade["exit"][0], trade
        got = twin["exit_comment"].split("|")
        assert len(got) == len(want)
        for field in (*LAZY_FIELDS, COSMETIC_FIELD):
            assert int(got[field - 1]) < 0, (field, got)
            got[field - 1] = want[field - 1]
        assert all(int(want[field - 1]) < 0 for field in LAZY_FIELDS), want
        assert got == want, f"tape {trade['exit'][1]!r}, engine {twin['exit_comment']!r}"
    print(f"argument evaluation: {len(tape)}/{len(tape)} TradingView exit comments")


TWIN_HEAD = '//@version=6\nstrategy("w2 argument evaluation twins", overlay = true)\n'
TWIN_TAIL = """// @pf-trace x=x
if ta.crossover(close, x)
    strategy.entry("L", strategy.long)
if ta.crossunder(close, x)
    strategy.close("L")
"""
# (shape, reference): the reference hoists the stateful argument into a
# variable, which Pine evaluates once per bar by construction. Each twin sits
# in a user function: a top-level static ta.* site reads its precalculated
# series, where a second read is idempotent.
TWINS = {
    "nz": ("g(src) =>\n    nz(src > open ? src : na, ta.sma(src, 3))\nx = g(close)",
           "g(src) =>\n    y = ta.sma(src, 3)\n    nz(src > open ? src : na, y)\nx = g(close)"),
    "math_round": ("g(src) =>\n    math.round(src, int(ta.cum(1.0) % 3))\nx = g(close)",
                   "g(src) =>\n    p = int(ta.cum(1.0) % 3)\n    math.round(src, p)\n"
                   "x = g(close)"),
    "str_substring": ('g(src) =>\n    s = str.substring("abcdefgh", int(ta.cum(1.0) % 4), 6)\n'
                      "    ta.sma(src, 10) + str.length(s) - 3\nx = g(close)",
                      "g(src) =>\n    b = int(ta.cum(1.0) % 4)\n"
                      '    s = str.substring("abcdefgh", b, 6)\n'
                      "    ta.sma(src, 10) + str.length(s) - 3\nx = g(close)"),
    "array_lastindexof": ("g(src) =>\n    lvl = array.from(1.0, 2.0, 3.0, 1.0)\n"
                          "    ta.sma(src, 10) + array.lastindexof(lvl, ta.cum(1.0) % 3 + 1) - 1.5\n"
                          "x = g(close)",
                          "g(src) =>\n    lvl = array.from(1.0, 2.0, 3.0, 1.0)\n"
                          "    v = ta.cum(1.0) % 3 + 1\n"
                          "    ta.sma(src, 10) + array.lastindexof(lvl, v) - 1.5\nx = g(close)"),
    "array_concat": ("mk() => array.from(1.0, 2.0)\na = array.from(3.0)\n"
                     "array.concat(a, mk())\nx = ta.sma(close, 10) + array.size(a) - 3",
                     "mk() => array.from(1.0, 2.0)\na = array.from(3.0)\nb = mk()\n"
                     "array.concat(a, b)\nx = ta.sma(close, 10) + array.size(a) - 3"),
}


@pytest.fixture(scope="module")
def twin_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_arg_twins")
    feed = chart_feed_head(engine, base, 3000)
    builds = {}
    for name, (shape, reference) in TWINS.items():
        builds[f"{name}-shape"] = Build(TWIN_HEAD + shape + "\n" + TWIN_TAIL, trace=True)
        builds[f"{name}-reference"] = Build(TWIN_HEAD + reference + "\n" + TWIN_TAIL, trace=True)
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("name", list(TWINS))
def test_a_stateful_argument_runs_like_its_hoisted_twin(twin_runs, name):
    print(name, assert_same_runs(ok(twin_runs, f"{name}-shape"),
                                 ok(twin_runs, f"{name}-reference")))


@pytest.mark.parametrize("cpp, plain", [
    ("2", True), ("-1.5e3", True), ("(3)", True), ("current_bar_.close", True),
    ("this->x", True), ('std::string("a,b")', True), ("na<double>()", True),
    ("_s_close[1]", True), ("pi(4, 2)", False), ("(a) + (b)", False),
    ("_s_close[i]", False), ("(int)(2)", False),
])
def test_plain_reads_are_recognised(cpp, plain):
    from pineforge_codegen.codegen.helpers import cpp_is_plain_read
    assert cpp_is_plain_read(cpp) is plain
