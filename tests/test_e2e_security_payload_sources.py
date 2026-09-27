"""A ``request.security`` payload reads its source series on the requested bar.

Three shapes read the chart's series instead (W9-CG-SECURITY-PAYLOAD-SOURCES):

- a name bound to ``input.source(ohlc4, ...)`` resolved to the generic
  visitor's ``get_input_source("title", _src_ohlc4_)[0]``, the chart's
  series, so ``ta.rsi(src, 3)`` in a payload (kenpachi's
  ``rsi_raw = ta.rsi(src, rsi_len)`` read as ``rsi_raw[1]``) ran on the
  chart's ohlc4, and ``nz(src)`` read the chart's member;
- ``src[1]`` was refused ("helper call history is only supported in the
  payload itself"; an earlier build emitted an undeclared
  ``_sec1_expr_hist_missing``);
- ``hl2[1]`` / ``hlc3[2]`` / ``ohlc4[1]`` read the chart's ``_s_hl2`` ...
  history, and ``hlcc4[1]`` its current value beside a chart push of
  ``current_bar_.hlcc4``, which does not compile.

TradingView evaluates the source input in the requested context like any
series (its override picks another native series there too) and keeps each
derived series' history on the requested clock. Its tapes
(``fixtures/security_sources_tv``) spell every value on each close.
"""

from __future__ import annotations

import re
from pathlib import Path

from pineforge_codegen import transpile
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "security_sources_tv"


def _replay_tape(tmp_path_factory, name: str, extra: dict[str, Build] | None = None,
                 params: dict[str, dict] | None = None) -> tuple[dict, dict]:
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp(name)
    builds = {"probe": Build(source(name, FIXTURES)), **(extra or {})}
    exits = replay(engine, base, builds, params=params)
    tape = tape_exits(name, FIXTURES)
    assert len(tape) == 265
    missed = mismatches(tape, exits["probe"])
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:10])
    return tape, exits


def test_source_input_payload_matches_the_tape_and_its_override(tmp_path_factory):
    name = "w9sec_src_payload"
    # The same payloads spelled with ``close`` (a global alias ``src = close``
    # would read the chart's ``src`` under ``nz``, a separate gap).
    literal_close = re.sub(r"\bsrc\b", "close", source(name, FIXTURES).replace(
        'src = input.source(ohlc4, "Source")\n', ""))
    assert "input.source" not in literal_close and "src" not in literal_close
    tape, exits = _replay_tape(
        tmp_path_factory, name,
        {"override": Build(source(name, FIXTURES)), "literal": Build(literal_close)},
        params={"override": {"Source": "close"}},
    )
    # The override selects the requested bar's close, as a literal close does.
    assert exits["override"] == exits["literal"]
    assert exits["override"] != exits["probe"]
    print(f"source input payload: {len(tape)} of {len(tape)} exit Signals equal "
          f"TradingView's; Source=close equals close on {len(exits['literal'])} exits")


def test_derived_series_history_payload_matches_the_tape(tmp_path_factory):
    tape, _ = _replay_tape(tmp_path_factory, "w9sec_derived_hist")
    print(f"derived history payload: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_hlcc4_history_matches_the_tape(tmp_path_factory):
    tape, _ = _replay_tape(tmp_path_factory, "w9sec_hlcc4_hist")
    print(f"hlcc4 history: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_payload_reads_no_chart_source_series():
    for name in ("w9sec_src_payload", "w9sec_derived_hist", "w9sec_hlcc4_hist"):
        cpp = transpile(source(name, FIXTURES))
        evaluators = cpp[cpp.index("void _eval_security_0"):cpp.index("void evaluate_security")]
        # The chart's value of a source input, the chart's derived series.
        assert not re.search(r"get_input_source\([^()]*\)\[", evaluators), name
        for chart in ("_s_hl2", "_s_hlc3", "_s_ohlc4", "_s_hlcc4", "current_bar_"):
            assert chart not in evaluators, (name, chart)


def test_a_source_input_beside_a_chart_global_reads_the_requested_bar():
    from pineforge_codegen import transpile_full

    # ``g`` keeps the chart's value (a global under a builtin); the source
    # input reads the requested bar, as ``close`` would, and inlines no call,
    # so the evaluator warns about none.
    result = transpile_full('''//@version=6
strategy("source beside a global")
src = input.source(close, "S")
g = close * 2
v = request.security(syminfo.tickerid, "60", nz(src, g) + nz(src) + nz(open, g))
plot(v)
''')
    cpp = result["cpp"]
    evaluators = cpp[cpp.index("void _eval_security_0"):cpp.index("void evaluate_security")]
    assert not re.search(r"get_input_source\([^()]*\)\[", evaluators)
    assert not re.search(r"\(src\)|\bsrc\b[^_(]", evaluators.replace("_pf_src", "")), evaluators
    assert not any("keeps its user calls" in d.message for d in result["diagnostics"])
