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
  reads at an offset on the requested bars, typed from the global: it is
  typed as the same expression read inline is (an operator expression's is a
  double, whose na reads na), and a global holding a 64-bit integer (main's
  rule, or an epoch) keeps it in a double; the global's ``int`` wrapped it.
  TAIL-A lowers a global bound to a user call once per evaluator: a read
  outside the helper block its locals were emitted in lowers it again (it
  named locals out of scope, where main compiled).
- Lane TAIL-E runs a ``request.security`` helper's ``for`` loop on the
  requested bar: its counter is a counted loop's binder for main's 64-bit
  rule, as on the chart (a helper-bound name was left 32-bit), and what the
  loop refuses -- a TA call, a user call -- it refuses through a helper
  parameter or a global the loop reads too (such a name is expanded where it
  is read: ``h(ta.sma(close, 3), n)`` computed the SMA once per iteration).
- Lane TAIL-E reads a ``ta.change`` / ``mom`` / ``roc`` in a top-level if
  block through the hold-last source clock, and lane TAIL-G pushes a pure
  call read at an offset below a lazy edge once per execution of its scope,
  before its statement: an if whose head reads such a history and whose body
  calls ``ta.roc`` takes both rules, as do a value-form if and a nested if
  whose heads call a function reading its own call's history below a lazy
  ``and``, and a ternary whose head reads one beside a lazy ``ta.change``.

TradingView's tapes of ``fixtures/cgint7_tv`` spell every value on every
sampled bar; all three replay whole.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import TAPE_TZ, replay, source, tape_exits
from tests._tail_e_tapes import BAR_MS, DAY_MS, START_MS, build, engine_rows, feed, mismatches

FIXTURES = Path(__file__).parent / "fixtures" / "cgint7_tv"
PRODUCTS = "cgint7_int_products"
LAZY = "cgint7_lazy"
LOOPS = "cgint7_loop_products"
FIELDS = ("pm", "ag", "w", "r", "dv", "dd", "zz", "fl", "p", "q2", "pr")
LOOP_FIELDS = ("s", "w", "q", "v")
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
def replays(tmp_path_factory) -> dict[str, dict[int, str]]:
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgint7")
    return replay(engine, base, {PRODUCTS: Build(source(PRODUCTS, FIXTURES)),
                                 LOOPS: Build(source(LOOPS, FIXTURES))})


def _field_differences(name: str, fields: tuple, exits: dict[int, str]) -> dict[str, set]:
    """Per field, the (TradingView, engine) pairs that differ; up to the
    tape's last exit the engine exits at exactly the tape's instants."""
    tape = tape_exits(name, FIXTURES)
    assert len(tape) == 312
    last = max(tape)
    inside = {ms: signal for ms, signal in exits.items() if ms <= last}
    assert set(inside) == set(tape)
    differ: dict[str, set] = {}
    for ms, signal in tape.items():
        tv_fields, pf_fields = signal.split("|"), inside[ms].split("|")
        assert len(tv_fields) == len(pf_fields) == len(fields), (signal, inside[ms])
        for field, tv, pf in zip(fields, tv_fields, pf_fields):
            if tv != pf:
                differ.setdefault(field, set()).add((tv, pf))
    return differ


def test_the_int_products_tape_replays(replays):
    differ = _field_differences(PRODUCTS, FIELDS, replays[PRODUCTS])
    assert not differ, {name: sorted(v)[:3] for name, v in differ.items()}
    tape = tape_exits(PRODUCTS, FIXTURES)
    last = max(tape)
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


def _history_series(decl: str, read: str = "g[1]") -> str:
    cpp = transpile(HEAD + decl + f'p = request.security(syminfo.tickerid, "60", {read})\n'
                    'if p > 0\n    strategy.entry("L", strategy.long)\n')
    (line,) = [ln.strip() for ln in cpp.splitlines() if "_sec0_expr_hist_0;" in ln
               and ln.strip().startswith("Series<")]
    return line


def test_a_payload_global_history_is_a_double():
    double, integer = "Series<double> _sec0_expr_hist_0;", "Series<int> _sec0_expr_hist_0;"
    assert _history_series("g = bar_index * 86400000\n") == double
    assert _history_series("var int k = 0\nk += 1\ng = k * 86400000\n") == double
    # An int global's history is a double too: its na reads na (a
    # Series<int> read na<int>() as -2147483648 on the first requested bar),
    # as an operator expression's read inline does.
    assert _history_series("g = bar_index * 3\n") == double
    assert _history_series("f() => bar_index * 3\ng = f()\n") == double
    assert _history_series("", "(bar_index * 3)[1]") == double
    # Main's inline call history is left as it was.
    assert _history_series("f() => bar_index * 3\n", "f()[1]") == integer


SM = "sm(len) =>\n    a = ta.sma(close, len)\n    a * 1\nrng = sm(5)\n"
METHOD_SM = ("method sm(float x, int len) => ta.sma(x, len) * 1\n"
             "f() =>\n    a = close.sm(5)\n    a * 1\nrng = f()\n")


def _pick(decl: str, body: str) -> str:
    return (HEAD + decl + "pick(c) =>\n    float s = 0.0\n" + body
            + 'p = request.security(syminfo.tickerid, "240", pick(close > open))\n'
            'if p > 0\n    strategy.entry("L", strategy.long)\n')


@pytest.mark.parametrize("decl, body", [
    (SM, "    if c\n        s := rng[1]\n    s + rng\n"),
    (SM, "    if c\n        s := rng\n    s + rng[1]\n"),
    (METHOD_SM, "    if c\n        s := rng[1]\n    s + rng\n"),
])
def test_a_stateful_global_read_with_history_is_lowered_where_it_runs_once(decl, body):
    """A history read on the requested clock and a read in another block
    that runs on the same bar would lower the call twice, its SMA advancing
    twice a bar; main refused the history read."""
    with pytest.raises(CompileError) as err:
        transpile(_pick(decl, body))
    assert "reads 'rng' with history and outside the helper block" in str(err.value)


@pytest.mark.parametrize("body", [
    # A read under a builtin keeps the chart's series, as on main.
    "    if c\n        s := rng\n    s + rng + nz(rng[1])\n",
    "    if c\n        s := rng\n    s + rng + (na(rng[1]) ? 1.0 : 0.0)\n",
    # Two arms of one if never run on the same bar.
    "    if c\n        s := rng[1]\n    else\n        s := rng\n    s\n",
])
def test_a_stateful_global_lowered_twice_where_main_compiled_or_never_twice_a_bar(body):
    compile_cpp(transpile(_pick(SM, body)), label="stateful global lowered twice")


@pytest.mark.parametrize("payload", [
    "nz(g[1], 0) / 2",
    "k(nz(g[1], 0))",
])
def test_a_history_read_under_a_builtin_keeps_mains_lowering(payload):
    """A read under a builtin keeps the chart's int series: the payload's
    ``/`` still divides in floating point (a double mark on it made it C++
    integer division)."""
    cpp = transpile(HEAD + "f() => bar_index * 3\ng = f()\nk(x) => x / 2\n"
                    f'p = request.security(syminfo.tickerid, "60", {payload})\n'
                    'if p > 0\n    strategy.entry("L", strategy.long)\n')
    (line,) = [ln for ln in cpp.splitlines()
               if ln.strip().startswith("_req_sec_0 = ") and "_nz_v" in ln]
    assert line.strip().startswith("_req_sec_0 = ((double)(") and "/ (double)(2))" in line


def test_a_string_global_history_on_the_requested_clock_is_refused():
    with pytest.raises(CompileError) as err:
        transpile(HEAD + "f() => str.tostring(bar_index)\ng = f()\n"
                  'p = request.security(syminfo.tickerid, "60", g[1])\n'
                  'if str.length(p) > 1\n    strategy.entry("L", strategy.long)\n')
    assert "reads the string global 'g' with history" in str(err.value)


def test_an_int_slot_narrows_a_global_history_na_preserving():
    cpp = transpile(HEAD + "f() => bar_index * 3\ng = f()\n"
                    "h() =>\n    int x = g[1]\n    na(x) ? -1 : x\n"
                    'p = request.security(syminfo.tickerid, "60", h())\n'
                    '[a, b] = request.security(syminfo.tickerid, "60", [g[1], close])\n'
                    'if p > 0 and a > 0\n    strategy.entry("L", strategy.long)\n')
    local = [ln for ln in cpp.splitlines() if "int _sec0_h_1_x = " in ln]
    assert local and "is_na(_pf_v) ? na<int>()" in local[0]
    element = [ln for ln in cpp.splitlines() if "_req_sec_1_0 = [&]" in ln]
    assert element and "is_na(_pf_v) ? na<int>()" in element[0]
    compile_cpp(cpp, label="int slots of a global history")


def test_a_shared_payload_global_read_outside_its_block_compiles():
    """TAIL-A lowers a global bound to a user call once per evaluator; its
    first read inside a helper's if branch emitted the call's locals there,
    and the read outside it named them out of scope (main compiled it)."""
    cpp = transpile(HEAD + "rangeOf(len) =>\n    hi = ta.highest(high, len)\n"
                    "    lo = ta.lowest(low, len)\n    hi - lo\nrng = rangeOf(20)\n"
                    "pick(up) =>\n    float out = 0.0\n    if up\n        out := rng\n    out\n"
                    'p = request.security(syminfo.tickerid, "240", pick(close > open) + rng)\n'
                    'if p > 0\n    strategy.entry("L", strategy.long)\n')
    compile_cpp(cpp, label="shared global read outside its block")


@pytest.mark.parametrize("decl, helper, call, via", [
    ("sm(x) =>\n    a = x * 2\n    a + 1\ncs = sm(close)\n",
     "h(n) =>\n    float s = 0.0\n    for i = 0 to n\n        s += cs\n    s\n",
     "h(3)", "a user function call (through the global 'cs')"),
    ("",
     "h(v, n) =>\n    float s = 0.0\n    for i = 0 to n\n        s += v\n    s\n",
     "h(ta.sma(close, 3), 3)", "a TA call (through the parameter 'v')"),
    ("",
     "h(v, n) =>\n    float s = 0.0\n    for i = 0 to n\n        s += v\n    s\n",
     "h(ta.accdist, 3)", "a TA call (through the parameter 'v')"),
    ("sm(x) =>\n    a = ta.sma(x, 3)\n    a + 1\ncs = sm(close)\n",
     "h(n) =>\n    float s = 0.0\n    for i = 0 to n\n        s += cs\n        cs = 1.0\n"
     "        s += cs\n    s\n",
     "h(3)", "a user function call (through the global 'cs')"),
    # A block's local ends at its block, and an initializer reads the
    # enclosing name.
    ("sm(x) =>\n    a = ta.sma(x, 3)\n    a + 1\ncs = sm(close)\n",
     "h(n) =>\n    float s = 0.0\n    for i = 0 to n\n        if i > 100\n            cs = 1.0\n"
     "            s += cs\n        s += cs\n    s\n",
     "h(3)", "a user function call (through the global 'cs')"),
    ("",
     "h(v, n) =>\n    float s = 0.0\n    for i = 0 to n\n        v = v + 0.0\n        s += v\n    s\n",
     "h(ta.sma(close, 3), 3)", "a TA call (through the parameter 'v')"),
])
def test_a_helper_loop_refuses_state_reached_through_a_name(decl, helper, call, via):
    with pytest.raises(CompileError) as err:
        transpile(HEAD + decl + helper
                  + f'p = request.security(syminfo.tickerid, "60", {call})\n'
                  'if p > 0\n    strategy.entry("L", strategy.long)\n')
    assert f"request.security helper loops cannot hold {via}" in str(err.value)


@pytest.mark.parametrize("decl, call", [
    ("g = close * 2\n", "h(open, 3)"),
    # A global's own TA sites are computed once, before the payload.
    ("g = ta.sma(close, 3)\n", "h(open, 3)"),
    ("g = ta.obv\n", "h(open, 3)"),
    ("g = ta.sma(close, 3)\n", "h(g, 3)"),
])
def test_a_helper_loop_reads_globals_computed_before_it(decl, call):
    cpp = transpile(HEAD + decl
                    + "h(src, n) =>\n    float s = 0.0\n    for i = 0 to n\n        s += g + src\n    s\n"
                    + f'p = request.security(syminfo.tickerid, "60", {call})\n'
                    'if p > 0\n    strategy.entry("L", strategy.long)\n')
    compile_cpp(cpp, label="a global computed before a helper loop")


def test_the_loop_products_tape_replays(replays):
    differ = _field_differences(LOOPS, LOOP_FIELDS, replays[LOOPS])
    assert not differ, {name: sorted(v)[:3] for name, v in differ.items()}
    # The payload's counter products are 64-bit, as the chart's.
    cpp = transpile(source(LOOPS, FIXTURES))
    assert "_sec0_h_1_s += (static_cast<int64_t>(_sec0_h_3_i) * 86400000);" in cpp
    tape = tape_exits(LOOPS, FIXTURES)
    assert max(int(signal.split("|")[0]) for signal in tape.values()) > 2**31


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
    # TAIL-E: every change / mom / roc of the if blocks takes the hold-last
    # clock (r, c, v, m), as the ternary arm's ta.change does (b).
    for n in (1, 2, 3, 4, 5):
        assert f"_pf_lazy_src_clock_{n}." in cpp, n
    assert "_pf_lazy_src_clock_6" not in cpp
    # TAIL-G: the if head's, the ternary's and isNew's reads (once per body,
    # in both of its per-call-site variants) are pushed before their
    # statements, once per execution.
    assert cpp.count("a call read at an offset runs on every execution") == 4
