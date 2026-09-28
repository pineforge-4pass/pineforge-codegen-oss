"""A function-local series may share its name with another function.

Pine keeps functions and variables apart: ``fHist`` may keep a local series
``float f`` while ``f(x) => str.tostring(x)`` is another function.
PineForge's symbol table holds one name per scope, the global scope holds
the function ``f`` typed by what it returns, and ``_series_type_for``
resolved the local's history member there: ``Series<std::string> f`` took
fHist's doubles and the TU did not compile (lane W2's f04_tails_b probe,
which TradingView runs). The member now takes the type of the local in the
scope of the function that keeps the series.

Two TradingView tapes (lab tv --no-note) are replayed: lane W2's
``f04_tails_b`` (``fixtures/open_items_tv/tails_b``, fHist's series ``f``
beside a string function ``f`` defined after it) and
``func_local_name`` (a float series beside a string and an int function,
and an int series beside a float function defined after it).
"""

from __future__ import annotations

import pytest

from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests._tv_tapes import FIXTURES, exit_misses, tape, window_feed


TAPES = ("tails_b", "func_local_name")


@pytest.fixture(scope="module")
def tape_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("func_local_name_tapes")
    feed = window_feed(engine, base)
    runs = execute_all(engine, feed, base, {
        name: Build((FIXTURES / f"{name}.pine").read_text()) for name in TAPES})
    return engine, base, feed, runs


@pytest.mark.parametrize("name", TAPES)
def test_the_name_sharing_tape_replays(tape_runs, name):
    engine, base, feed, runs = tape_runs
    ok(runs, name)
    trades = tape(name)
    assert len(trades) == 7
    misses = exit_misses(engine, base / name, feed, trades)
    assert not misses, misses[:3]


def test_each_local_series_keeps_its_own_type(tape_runs):
    _engine, _base, _feed, runs = tape_runs
    cpp = ok(runs, "func_local_name").transpiled["cpp"]
    assert "Series<double> g;" in cpp
    assert "Series<double> k;" in cpp
    assert "Series<int> m;" in cpp
    assert "Series<double> f;" in ok(runs, "tails_b").transpiled["cpp"]
