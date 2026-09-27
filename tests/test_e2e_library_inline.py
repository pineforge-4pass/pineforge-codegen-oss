"""An inlined library runs exactly as the same code written into the script.

``fixtures/library_scripts/signals_import.pine`` imports the clean-room
``pftest/Signals/1`` (which imports ``pftest/Base/1``) and reads every
construct it exports: string signals with ``na``, keyword arguments, two call
sites of an export holding ``var`` state and an SMA, locals and parameters
named like the script's own globals and built-in series, a type with a
method called both ways, an enum, an ``export const``, the transitive import
and a private constant; its exit comments spell each value.
``signals_spelled.pine`` is the same script with both libraries written in as
user code, renamed by hand. The import build goes through the verifier's
path: ``transpile_json`` of the glue, which reads the libraries from the
environment through the script's own requests manifest
(``tests/_pine_libraries.py``). The two books must be one: every trade, its
prices and its exit comment.

TradingView's own tapes of scripts importing the open libraries the lane was
measured against are replayed outside this repository, where those sources
are pinned as evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._e2e import (
    Build, chart_feed_head, closed_trades, execute_all, ok, skip_unless_e2e_env,
)
from tests._pine_libraries import Layout

SCRIPTS = Path(__file__).parent / "fixtures" / "library_scripts"
BARS = 600


@pytest.fixture(scope="module")
def twin(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_library_twin")
    imported = (SCRIPTS / "signals_import.pine").read_text(encoding="utf-8")
    spelled = (SCRIPTS / "signals_spelled.pine").read_text(encoding="utf-8")
    layout = Layout(base / "env")
    env = layout.pin_all("signals-import", imported, "pftest/Signals/1", "pftest/Base/1")
    feed = chart_feed_head(engine, base, BARS)
    builds = {"import": Build(imported, env=tuple(env.items())),
              "spelled": Build(spelled)}
    return engine, base, feed, execute_all(engine, feed, base, builds)


def test_an_inlined_library_books_as_the_code_written_in(twin):
    engine, base, feed, runs = twin
    imported, spelled = ok(runs, "import"), ok(runs, "spelled")
    assert imported.trades["default"] == spelled.trades["default"]
    got = closed_trades(engine, base / "import", feed)
    want = closed_trades(engine, base / "spelled", feed)
    assert len(got) >= BARS // 2 - 2
    assert [(t["exit_time"], t["exit_comment"]) for t in got] == \
        [(t["exit_time"], t["exit_comment"]) for t in want]
    # Every construct produced a value on some bar.
    comments = {t["exit_comment"] for t in got}
    assert any(c.startswith("a BUY") or c.startswith("a SELL") for c in comments)
    assert any("s3:x,main,1" in c for c in comments)


def test_the_import_build_warns_as_the_script(twin):
    _engine, _base, _feed, runs = twin
    diagnostics = ok(runs, "import").transpiled["diagnostics"]
    assert all("Import" not in d["message"] for d in diagnostics)
