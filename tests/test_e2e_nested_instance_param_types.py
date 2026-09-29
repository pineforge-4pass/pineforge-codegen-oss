"""A nested helper's fresh instance takes its call's parameter types (lane
TAIL-E, item 1).

A wrapper called on two written paths gets a ``_csN`` clone per call and, for
the second path, a fresh ``__niN`` instance of each stateful helper it calls,
so each path keeps its own TA state. The ``_csN`` clones type an untyped
parameter from the argument the call passes; the fresh instances were emitted
with no call site and typed it ``double``, so an untyped string parameter
(``f_ma(maType, src, len)`` called with the wrapper's string) took a
``std::string`` into a ``double`` and the TU did not compile
(hary349veo3-tmo-dual-timeframe-strategy-hary, whose TMO helper is only
called inside request.security, still emits its chart clones). A fresh
instance now takes the parameter and return types of the written call it
runs, as that call's own ``_csN`` clone does.

TradingView's tape of ``te_nested_untyped`` (``fixtures/tail_e_tv``) spells
both paths' EMA / SMA values on every bar; it replays with the verifier's
``chart_ema_na_warmup`` run flag (TradingView's ta.ema seeds from the SMA of
its first values).
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import skip_unless_e2e_env
from tests._tail_e_tapes import (
    BAR_MS, DAY_MS, START_MS, build, engine_rows, feed, mismatches, source, tape_rows,
)

NAME = "te_nested_untyped"


def test_fresh_instances_take_the_call_types():
    cpp = transpile(source(NAME))
    instances = re.findall(r"double (f_ma__ni\d+)\(([^)]*)\)", cpp)
    assert len(instances) == 2, instances
    for name, params in instances:
        assert params.startswith("std::string maType"), (name, params)
    compile_cpp(cpp, label="nested fresh instance types")


def test_the_nested_untyped_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    tape = tape_rows(f"{NAME}_eth15")
    assert len(tape) == 192
    chart = feed(engine, tmp_path, START_MS + 2 * DAY_MS + BAR_MS)
    workdir = build(tmp_path, NAME, source(NAME))
    rows, _ = engine_rows(engine, workdir, chart, syminfo_metadata={"chart_ema_na_warmup": 1})
    missed = mismatches(tape, rows)
    assert not missed, f"{len(missed)} of {len(tape)} rows differ:\n" + "\n".join(missed[:5])
