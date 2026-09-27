"""A Pine ``int`` holding an epoch a ``request.security`` reads keeps 64 bits
(W9-CG-EPOCH-INT64).

The wide-integer provenance (``types._wide_int_provenance``) followed
``time``, copies, casts and user-function returns, but not a
``request.security`` value or the tuple element it binds: ``var int last :=
request.security(t, "60", time)`` and ``f_ok(int t)`` fed ``t2`` of ``[t0, t1,
t2] = request.security(t, "60", f_get())`` stayed 32-bit and reached them
through ``(int)<double>``. That conversion is undefined for an epoch:
x86-64 (Cloud Run) gives ``INT_MIN``, the ``na`` sentinel, arm64 saturates
to ``INT_MAX``. job-2868-mithrill35-oink-test's FVG latch read ``na`` after
its first write and drjproduction's zones were never created: ten probes
booked no trade on Cloud Run. A floating value outside ``int`` now narrows to
``na<int>()`` on every platform (x86-64's answer).

TradingView's tapes (``fixtures/security_epoch_tv``, BINANCE:ETHUSDT.P 15):
``w9sec_epoch_security`` spells the latched epoch, its change count, ``f_ok``
of a tuple element and an ``int``-parameter age on 265 closes; the draft's
two witness scripts (``w9sec_epoch_var_int_daily``, a ``var int`` counted per
UTC day; ``w9sec_epoch_int_param_tuple``, a typed ``int`` parameter fed a
security-tuple epoch) book 7 and 18 trades.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._e2e import Build, closed_trades, execute_all, ok, skip_unless_e2e_env
from tests._security_tapes import TAPE_TZ, mismatches, source, tape_exits, tape_feed

FIXTURES = Path(__file__).parent / "fixtures" / "security_epoch_tv"
REPO = Path(__file__).resolve().parent.parent


def _tape_trades(name: str) -> list[tuple[int, int | None]]:
    """(entry, exit) instants (UTC ms) of every trade on a tape; the exit is
    None where TradingView closed the trade at its range's end (no Signal)."""
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    def ms(text: str) -> int:
        stamp = dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=TAPE_TZ)
        return int(stamp.timestamp() * 1000)

    trades: dict[str, dict[str, int | None]] = {}
    for row in rows:
        entry = row["Type"].startswith("Entry")
        trades.setdefault(row["Trade number"], {})["entry" if entry else "exit"] = (
            ms(row["Date and time"]) if entry or row["Signal"] else None)
    return sorted((t["entry"], t["exit"]) for t in trades.values())


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security_epoch")
    feed = tape_feed(engine, base)
    names = ("w9sec_epoch_security", "w9sec_epoch_var_int_daily",
             "w9sec_epoch_int_param_tuple")
    outcomes = execute_all(engine, feed, base,
                           {name: Build(source(name, FIXTURES)) for name in names})
    return engine, feed, base, outcomes


def test_security_epochs_read_like_the_tape(runs):
    engine, feed, base, outcomes = runs
    name = "w9sec_epoch_security"
    ok(outcomes, name)
    exits = {t["exit_time"]: t["exit_comment"] for t in closed_trades(engine, base / name, feed)}
    tape = tape_exits(name, FIXTURES)
    assert len(tape) == 265
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:10])
    print(f"security epochs: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


@pytest.mark.parametrize("name", ["w9sec_epoch_var_int_daily", "w9sec_epoch_int_param_tuple"])
def test_witness_trades_equal_the_tape(runs, name):
    engine, feed, base, outcomes = runs
    ok(outcomes, name)
    tape = _tape_trades(name)
    engine_trades = sorted((t["entry_time"], t["exit_time"])
                           for t in closed_trades(engine, base / name, feed))
    # The run's feed ends after the tape's range: compare the tape's trades,
    # all but an exit TradingView made at its range's end.
    assert [
        (entry, exit_ if tape_exit is not None else None)
        for (entry, exit_), (_, tape_exit) in zip(engine_trades, tape)
    ] == tape
    print(f"{name}: {len(tape)} of {len(tape)} trades equal TradingView's")


def test_every_security_epoch_slot_is_int64():
    cpp = transpile(source("w9sec_epoch_security", FIXTURES))
    for declaration in ("int64_t last;", "bool f_ok(int64_t t)", "double f_age(int64_t t)"):
        assert declaration in cpp, declaration
    assert "int changes;" in cpp  # a counter no epoch reaches stays int
    cpp = transpile(source("w9sec_epoch_var_int_daily", FIXTURES))
    assert "int64_t last_htf_t;" in cpp and "int n;" in cpp
    cpp = transpile(source("w9sec_epoch_int_param_tuple", FIXTURES))
    assert "bool f_ok(int64_t t)" in cpp


# ---------------------------------------------------------------------------
# Census: no epoch a request.security (or the bar) supplies is narrowed to int.
# ---------------------------------------------------------------------------

_INT_NARROWING = re.compile(
    r"auto _pf_v = \((?P<op>.*?)\); "
    r"(?:if constexpr \(std::is_floating_point_v<decltype\(_pf_v\)>\)"
    r"|return is_na\(_pf_v\) \? na<int>\(\) : \(int\)_pf_v;)"
)
_EPOCH_CPP = ("bar.timestamp", "current_bar_.timestamp", "_hist_time[")
_BATTERY = {
    "var_int_security_time": '''//@version=6
strategy("s1")
var int last_t = 0
t_cur = request.security(syminfo.tickerid, "15", time, lookahead = barmerge.lookahead_off)
if t_cur != last_t
    last_t := t_cur
plot(last_t)
''',
    "typed_int_param_security_tuple": '''//@version=6
strategy("s4")
f_get() => [time, time[2]]
[t0, t2] = request.security(syminfo.tickerid, "60", f_get(), lookahead = barmerge.lookahead_off)
f(int t) => na(t) ? 0 : 1
plot(f(t2))
''',
    "untyped_param_nested_security_time": '''//@version=6
strategy("s5")
inner(t) => na(t) ? 0 : 1
outer(int t) => inner(t) + 1
htf = request.security(syminfo.tickerid, "240", time[1])
plot(outer(htf))
''',
    "copy_of_security_time_close": '''//@version=6
strategy("s6")
tc = request.security(syminfo.tickerid, "D", time_close)
int copy = 0
copy := tc
plot(copy)
''',
}


def _epoch_narrowings(cpp: str) -> list[str]:
    """Operands of ``int`` narrowings that carry an epoch: the bar's time, or
    a name bound to a request.security value (or tuple element) the
    evaluator fills from the requested bar's time."""
    epoch_secs: set[str] = set()
    epoch_elements: set[tuple[str, int]] = set()
    for sec, rhs in re.findall(r"\b(_req_sec_\d+) = (.+);", cpp):
        if rhs.startswith("std::make_tuple(") and rhs.endswith(")"):
            depth, part, parts = 0, "", []
            for ch in rhs[len("std::make_tuple("):-1]:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                if ch == "," and depth == 0:
                    parts.append(part)
                    part = ""
                else:
                    part += ch
            parts.append(part)
            for index, element in enumerate(parts):
                if any(epoch in element for epoch in _EPOCH_CPP):
                    epoch_elements.add((sec, index))
        elif any(epoch in rhs for epoch in _EPOCH_CPP):
            epoch_secs.add(sec)
    tuples = dict(re.findall(r"auto (_tuple_result_\d+) = (_req_sec_\d+);", cpp))
    epoch_names = {name for name, sec in re.findall(r"\b(\w+) = (_req_sec_\d+);", cpp)
                   if sec in epoch_secs}
    epoch_names |= {
        name for name, index, result in
        re.findall(r"\b(\w+) = std::get<(\d+)>\((_tuple_result_\d+)\);", cpp)
        if (tuples.get(result), int(index)) in epoch_elements
    }
    return [
        op for op in (m.group("op").strip() for m in _INT_NARROWING.finditer(cpp))
        if op in epoch_names or op in _EPOCH_CPP[:2]
    ]


def test_no_security_epoch_is_narrowed_to_int():
    battery = dict(_BATTERY)
    for pine in sorted(FIXTURES.glob("*.pine")):
        battery[pine.stem] = pine.read_text(encoding="utf-8")
    flagged = {name: _epoch_narrowings(transpile(pine)) for name, pine in battery.items()}
    assert not any(flagged.values()), flagged
    # The detector sees the shapes it guards: a probe spelled with the epoch
    # narrowed on purpose is flagged.
    assert _epoch_narrowings(
        "_req_sec_1 = bar.timestamp;\nhtf = _req_sec_1;\n"
        "last = [&](){ auto _pf_v = (htf); return is_na(_pf_v) ? na<int>() : (int)_pf_v; }();"
    ) == ["htf"]


def test_no_public_source_narrows_a_security_epoch_to_int():
    sources = sorted((REPO / "tests" / "gate-corpus").rglob("*.pine"))
    sources += sorted((REPO / "tests" / "fixtures").rglob("*.pine"))
    engine = None
    try:
        engine = skip_unless_e2e_env()
    except pytest.skip.Exception:
        pass
    if engine is not None:
        sources += sorted((engine / "corpus").rglob("strategy.pine"))
    flagged = {}
    for path in sources:
        try:
            cpp = transpile(path.read_text(encoding="utf-8"))
        except Exception:  # a refused source narrows nothing
            continue
        hits = _epoch_narrowings(cpp)
        if hits:
            flagged[str(path.relative_to(path.anchor))] = hits
    assert not flagged, flagged


def test_a_float_or_bool_an_epoch_reaches_keeps_its_type():
    # A request.security value is a float in the analyzer (a double holds an
    # epoch exactly); ``/`` makes a float and a comparison a bool, and a
    # same-spelled local elsewhere does not widen a float global.
    cpp = transpile('''//@version=6
strategy("float and bool epochs")
f() =>
    [t, v] = request.security(syminfo.tickerid, "D", [time, volume])
    t
[a, b] = request.security(syminfo.tickerid, "D", [time / 86400000.0, close])
d = request.security(syminfo.tickerid, "D", (time - time[1]) / 3600000)
[late, px] = request.security(syminfo.tickerid, "D", [time > timestamp(2025, 4, 3), close])
t = close * 0.5
x = t[1] + a[1] + d[1] + f()
wasLate = late[1]
plot(x)
''')
    for declaration in ("Series<double> a;", "Series<double> d;", "Series<double> t;",
                        "Series<bool> late;"):
        assert declaration in cpp, declaration
    for name in ("a", "d", "t", "late"):
        assert f"Series<int64_t> {name};" not in cpp, name
