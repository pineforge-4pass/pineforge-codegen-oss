"""Where the CGINT7 picks meet each other and codegen main's rules
(integration CGINT7).

- Lane TAIL-F computes an int product a ``%`` or ``/`` reads in 64 bits
  (``_wide_int_products``), and main's rule (CGINT6, CG-SILENT-2 item 3)
  computes every integer ``+ - *`` whose operands' bounds can leave int32 in
  64 bits, na-aware (``_int_arith_leaves_int32``, ``_wide_int_arith_cpp``). A
  product both rules reach keeps main's form: TAIL-F's cast wrapped it a
  second time (``_lower_binop``'s ``widened``). TAIL-F's form covers the
  products main's bounds do not see (an ``array.get`` operand) or that fit
  int32; main's form covers a product no ``%`` or ``/`` reads, which the lane
  left 32 bits wide, and keeps an na operand na where the lane's cast read
  ``na<int>()`` as -2147483648.
- Lane TAIL-A keeps the history of a global a ``request.security`` payload
  reads at an offset on the requested bars, typed from the global: a global
  holding a 64-bit integer (main's rule, or an epoch) keeps it in a double,
  as the same expression read inline does; the global's ``int`` wrapped it.
- Lane TAIL-E reads a ``ta.change`` / ``mom`` / ``roc`` in a top-level if
  block through the hold-last source clock, and lane TAIL-G pushes a pure
  call read at an offset below a lazy edge once per execution of its scope,
  before its statement: an if whose head reads such a history and whose body
  calls ``ta.roc`` takes both rules, as do a value-form if and a nested if
  whose heads call a function reading its own call's history below a lazy
  ``and``, and a ternary whose head reads one beside a lazy ``ta.change``.

TradingView's tapes of ``fixtures/cgint7_tv`` spell every value on every
sampled bar; both replay whole.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import TAPE_TZ, replay, source, tape_exits
from tests._tail_e_tapes import BAR_MS, DAY_MS, START_MS, build, engine_rows, feed, mismatches

FIXTURES = Path(__file__).parent / "fixtures" / "cgint7_tv"
PRODUCTS = "cgint7_int_products"
LAZY = "cgint7_lazy"
FIELDS = ("pm", "ag", "w", "r", "dv", "dd", "zz", "fl", "p", "q2", "pr")
HEAD = '//@version=6\nstrategy("cgint7")\n'


def _rows(name: str) -> dict[tuple[str, int], str]:
    """``(kind, fill instant UTC ms) -> Signal`` of every row of a tape,
    ``kind`` "E" for an entry and "X" for a close."""
    rows = {}
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if not row["Signal"]:
                continue  # the range-end close of a position left open
            ms = int(dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
                     .replace(tzinfo=TAPE_TZ).timestamp() * 1000)
            rows[("E" if row["Type"].startswith("Entry") else "X", ms)] = row["Signal"]
    return rows


@pytest.fixture(scope="module")
def product_exits(tmp_path_factory) -> dict[int, str]:
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgint7")
    return replay(engine, base, {PRODUCTS: Build(source(PRODUCTS, FIXTURES))})[PRODUCTS]


def test_the_int_products_tape_replays(product_exits):
    tape = tape_exits(PRODUCTS, FIXTURES)
    assert len(tape) == 312
    last = max(tape)
    inside = {ms: signal for ms, signal in product_exits.items() if ms <= last}
    assert set(inside) == set(tape)
    differ: dict[str, set] = {}
    for ms, signal in tape.items():
        tv_fields, pf_fields = signal.split("|"), inside[ms].split("|")
        assert len(tv_fields) == len(pf_fields) == len(FIELDS), (signal, inside[ms])
        for name, tv, pf in zip(FIELDS, tv_fields, pf_fields):
            if tv != pf:
                differ.setdefault(name, set()).add((tv, pf))
    assert not differ, {name: sorted(v)[:3] for name, v in differ.items()}
    # The tape's values leave int32 (w, p) and na stays na (zz).
    first = tape[min(tape)].split("|")
    assert int(first[FIELDS.index("w")]) > 2**31 and first[FIELDS.index("zz")] == "NaN"
    assert int(tape[last].split("|")[FIELDS.index("p")]) > 2**31


def test_a_product_both_rules_reach_keeps_mains_form():
    cpp = transpile(HEAD + 'var int n = 400\nn += 1\nr = (n * 7200000) % 1000000007\n'
                    'var st = array.new_int(1, 42)\nv = (array.get(st, 0) * 48271) % 2147483647\n'
                    'w = n * 7200000\nq = (n * 3) / 2\nx = r + v + w + q\n'
                    'if x > 0\n    strategy.entry("L", strategy.long)\n')
    lines = {line.strip().split(" = ", 1)[0]: line.strip() for line in cpp.splitlines()
             if line.strip()[:2] in ("r ", "v ", "w ", "q ")}
    # Main's na-aware 64-bit product, cast once, under the % and without one.
    for name in ("r", "w"):
        assert "static_cast<double>((static_cast<int64_t>(_pf_wide_l) * _pf_wide_r))" in lines[name]
        assert "(int64_t)(static_cast<int64_t>" not in lines[name]
    # TAIL-F's cast where main's bounds do not reach: an array element, and a
    # product that fits int32.
    assert "((int64_t)([&](auto&& __pf_array)" in lines["v"]
    assert "((int64_t)(n) * (3))" in lines["q"]


def test_a_wide_payload_global_keeps_its_history_in_a_double():
    def history(decl: str) -> str:
        cpp = transpile(HEAD + decl + 'p = request.security(syminfo.tickerid, "60", g[1])\n'
                        'if p > 0\n    strategy.entry("L", strategy.long)\n')
        (line,) = [ln.strip() for ln in cpp.splitlines() if "_sec0_expr_hist_0;" in ln
                   and ln.strip().startswith("Series<")]
        return line

    assert history("g = bar_index * 86400000\n") == "Series<double> _sec0_expr_hist_0;"
    assert history("var int k = 0\nk += 1\ng = k * 86400000\n") == "Series<double> _sec0_expr_hist_0;"
    # TAIL-A's own typing where the value fits int32.
    assert history("g = bar_index * 3\n") == "Series<int> _sec0_expr_hist_0;"


def test_the_lazy_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    tape = _rows(LAZY)
    assert len(tape) == 384
    chart = feed(engine, tmp_path, START_MS + 4 * DAY_MS + BAR_MS)
    rows, _ = engine_rows(engine, build(tmp_path, LAZY, source(LAZY, FIXTURES)), chart)
    missed = mismatches(tape, rows)
    assert not missed, f"{len(missed)} of {len(tape)} rows differ:\n" + "\n".join(missed[:5])
    # Both rules decide values on the tape: r and c are set (the head's
    # t(bar_index)[1] is bar_index - 1 on every third bar), the nested if ran.
    signals = [signal.split("|") for signal in tape.values()]
    assert any(fields[1] != "na" for fields in signals)
    assert int(signals[-1][-1]) >= 3


def test_the_lazy_compositions_take_both_rules():
    cpp = transpile(source(LAZY, FIXTURES))
    # TAIL-E: every change / mom / roc of the if blocks takes the hold-last clock.
    for n in (1, 2, 3, 4):
        assert f"_pf_lazy_src_clock_{n}." in cpp, n
    # TAIL-G: the if head's, the ternary's and isNew's reads are pushed before
    # their statements, once per execution.
    assert cpp.count("a call read at an offset runs on every execution") >= 3
