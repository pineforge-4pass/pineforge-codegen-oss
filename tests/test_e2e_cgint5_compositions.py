"""Where a lane integrated by CGINT5a meets a rule already on main, or changes
a function one of main's lanes changed, the integration keeps both rules
(integration lane CGINT5a: CG-SILENT items 1, 2 and 4, cut on 9361dbb, on
main's CGINT4 and TV-DEFAULTS; item 3 and CG-CHART-EMA-SEED are held). Each
case runs the composition end to end -- ``transpile_json``, the built
runtime, ``run_strategy.py`` -- or pins the C++ where the rules decide the
lowering. ``MAIN`` is the integration base, replayed beside a case to show
what it missed:

* ``ExprVisitor._visit_binop`` / ``_lower_binop`` and the payload builder's
  ``lower()`` (``SecurityEmitter._build_security_expr``): CG-SILENT item 1
  folds integer arithmetic over constants past int32 into a 64-bit literal
  (``_fold_int32_overflow_cpp``: a payload expands a global into its
  declaration); main's quirk 19 binds a binary operator's left operand first
  when the other can observe its effect (``_left_operand_first``), and XSYM-C
  refuses ``==`` / ``!=`` of a v5 library's bool. One expression that needs
  the order and holds such a product keeps both, on the chart and in a
  payload; a payload builtin's argument, which the chart's visitor renders
  (``nz(400 * MS)``), folds in ``_lower_binop``; and a v5 library body folds
  its constant's product while its bool comparison stays refused.
* A top-level constant's 64-bit slot and an if without else: CG-SILENT item
  1 declares the slot a top-level constant past int32 initializes
  ``int64_t`` (``_literal_wide_global``), and CG-OPEN-ITEMS (main) gives an
  if that runs no arm the na of its target's C++ type
  (``_visit_selection_value``): ``na<int64_t>()``, and the arm's value keeps
  64 bits.
* A history-read top-level ``var`` with a ``switch`` / ``if`` initializer:
  CG-XSYM-A2 (main) runs such a selection at the declaration of a primitive
  ``var``, but a top-level one read at ``[k]`` kept the first-bar preamble,
  whose analyzer spelling of a selection did not compile; CG-SILENT item 4
  sends a history-read ``var`` whose initializer is not a constant to its
  declaration, where the selection now runs into its element-typed local.
* A helper-local ``var`` TA length in a helper the payload reads a parameter
  of: XSYM-A (main) copies the request per parameter value, CG-SILENT item 2
  reads the local on the requested bars. Each copy counts its own requested
  bars and equals its spelled-out twin over the payload's ``bar_index``.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level
from tests._compile import compile_cpp
from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, reference_codegen, same,
    skip_unless_e2e_env, transpile_json,
)

# The integration base: codegen main before the CGINT5 picks.
MAIN = "78715160cde52b7d4d944aa78ed90d2ab936900e"
HEAD = ('//@version=6\nstrategy("cgint5 composition", overlay=true, '
        'default_qty_type=strategy.fixed, default_qty_value=1)\n')
BARS = 300
_BINDING = re.compile(r"\[&\]\{ (?:auto|bool) (__pf_binop_lhs_\d+) = \(")
WIDE = "static_cast<int64_t>(2880000000LL)"


def _values(records: list[dict], name: str) -> list[float]:
    return [rec["value"] for rec in records if rec["name"] == name]


def _nan(value) -> float:
    return math.nan if value is None else float(value)


def _chart_rows(feed: Path) -> list[dict[str, float]]:
    lines = feed.read_text().splitlines()
    header = lines[0].split(",")
    return [dict(zip(header, map(float, line.split(",")))) for line in lines[1:] if line]


def _main_cpp(tmp_path: Path, source: str) -> dict:
    main = reference_codegen(MAIN)
    if main is None:
        pytest.skip(f"codegen {MAIN} is not in this checkout's history")
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    return transpile_json(pine, main)


# ---------------------------------------------------------------------------
# One binary operator: 64-bit constant folding beside the left-first order
# (CG-SILENT item 1 x quirk 19 x XSYM-C's v5 bool comparison)
# ---------------------------------------------------------------------------

BINOP = HEAD + '''const int MS = 7200000
ints = array.from(5, 10, 20)
float mixed = ints.remove(-1) * 0 + ints.size() + 400 * MS
// @pf-trace mixed=mixed
'''


def test_an_ordered_operand_and_a_wide_product_share_one_expression():
    cpp = transpile(BINOP)
    line = next(row for row in cpp.splitlines() if row.strip().startswith("mixed ="))
    (token,) = _BINDING.findall(line)
    # The remove is bound before the size reads the array, and the product
    # is the 64-bit literal, not the C++ int product (400 * 7200000).
    after = line.index(f"return ({token} + ")
    assert ".erase(" in line[:after] and "ints.size()" in line[after:]
    assert WIDE in line and "(400 * 7200000)" not in line


def test_a_payload_orders_its_operands_and_folds_the_global_it_expands():
    cpp = transpile(HEAD + '''int step = 2 * 60 * 60 * 1000
ints = array.from(5, 10, 20)
p = request.security(syminfo.tickerid, "60", ints.remove(-1) * 0 + ints.size() + 400 * step)
if p > 0
    strategy.entry("L", strategy.long)
''')
    payload = next(row for row in cpp.splitlines() if row.strip().startswith("_req_sec_0 ="))
    (token,) = _BINDING.findall(payload)
    assert payload.index(".erase(") < payload.index(f"return ({token} + ")
    assert WIDE in payload
    assert "(400 * (((2 * 60) * 60) * 1000))" not in payload
    compile_cpp(cpp)


def test_a_payload_builtin_folds_through_the_chart_visitor():
    # nz()'s argument is rendered by the chart's expression visitor, which
    # spells MS as its literal without the tree fold reading it: the fold in
    # _lower_binop is what keeps the product 64-bit (main: (400 * 7200000)).
    cpp = transpile(HEAD + '''const int MS = 7200000
p = request.security(syminfo.tickerid, "60", nz(400 * MS) + close)
if p > 0
    strategy.entry("L", strategy.long)
''')
    payload = next(row for row in cpp.splitlines() if row.strip().startswith("_req_sec_0 ="))
    assert WIDE in payload and "(400 * 7200000)" not in payload
    compile_cpp(cpp)


def test_a_v5_library_folds_its_products_and_refuses_its_bool_comparison():
    head = '//@version=6\nstrategy("T")\nimport pftest/Five/1 as F\n'
    tail = 'if x > 0\n    strategy.entry("L", strategy.long)\n'
    lib = '//@version=5\nlibrary("Five")\n'
    # A library constant: main spelled 400 * MS as the C++ int product.
    cpp = transpile(head + 'x = F.wide()\n' + tail,
                    libraries={"pftest/Five/1": lib + 'const int MS = 7200000\n'
                               'export wide() => 400 * MS\n'})
    assert WIDE in cpp and "(400 * 7200000)" not in cpp
    compile_cpp(cpp)
    with pytest.raises(CompileError) as err:
        transpile(head + 'x = F.eq(close > open, true)\n' + tail,
                  libraries={"pftest/Five/1": lib + 'export eq(bool a, bool b) => a == b ? 1 : 0\n'})
    (diag,) = [d for d in err.value.diagnostics if d.level == Level.ERROR]
    assert diag.message.startswith("library 'pftest/Five/1' is //@version=5: '=='"), diag.message


# ---------------------------------------------------------------------------
# A top-level constant's 64-bit slot takes the 64-bit na of an if without else
# (CG-SILENT item 1 x CG-OPEN-ITEMS 9ad5694)
# ---------------------------------------------------------------------------

WIDE_IF_NA = HEAD + '''const int MS = 7200000
var int e = 300 * MS
up = close > open
e := if up
    300 * MS + bar_index
en = na(e) ? 1 : 0
twn = up ? 0 : 1
ek = na(e) ? 0.0 : e / 1000.0
// @pf-trace en=en
// @pf-trace twn=twn
// @pf-trace ek=ek
// @pf-trace bi=bar_index
'''


def test_the_wide_slot_is_int64_and_takes_its_own_na():
    cpp = transpile(WIDE_IF_NA)
    assert re.search(r"^\s*int64_t e\b", cpp, re.M)
    assert re.search(r"^\s*e = na<int64_t>\(\);$", cpp, re.M)
    assert not re.search(r"^\s*e = na<int>\(\);$", cpp, re.M)


# ---------------------------------------------------------------------------
# A history-read top-level var with a selection initializer
# (CG-SILENT item 4 x CG-XSYM-A2 68b0a1d)
# ---------------------------------------------------------------------------

VAR_SELECTION = HEAD + '''mode = input.string("A", "Mode", options = ["A", "B", "C"])
var float p = switch mode
    "A" => open
    "B" => close
    => high
var string s = switch mode
    "A" => "alpha"
    => "beta"
var float q = if close > open
    close
else
    open
p1 = p[1]
s1 = s[1] == "alpha" ? 1 : s[1] == "beta" ? 2 : 0
q1 = q[1]
// @pf-trace p=p
// @pf-trace p1=p1
// @pf-trace s1=s1
// @pf-trace q=q
// @pf-trace q1=q1
'''


def test_the_selection_runs_at_the_declaration(tmp_path):
    cpp = transpile(VAR_SELECTION)
    assert "<?>" not in cpp
    for name in ("p", "s", "q"):
        assert f"_pf_selection__pf_var_init_{name}" in cpp, name
        assert re.search(rf"^\s*{name}\.push\(na<", cpp, re.M), name
    compile_cpp(cpp)


def test_main_pushed_the_analyzers_spelling_of_the_selection(tmp_path):
    # Main kept a top-level history-read var's first-bar preamble, which
    # pushed the analyzer's spelling of a selection: C++ that never compiled.
    result = _main_cpp(tmp_path, VAR_SELECTION)
    assert result["ok"]
    assert "p.push(<?>);" in result["cpp"]


# ---------------------------------------------------------------------------
# A helper's var TA length in per-value payload copies
# (CG-SILENT item 2 x XSYM-A 2565f5f)
# ---------------------------------------------------------------------------

HELPER_VAR_LENGTH = HEAD + '''h(src) =>
    var int c = 0
    c += 1
    ta.highest(src, math.min(c, 5))
a = request.security(syminfo.tickerid, "60", h(high))
b = request.security(syminfo.tickerid, "60", h(low))
ta_ = request.security(syminfo.tickerid, "60", ta.highest(high, math.min(bar_index + 1, 5)))
tb = request.security(syminfo.tickerid, "60", ta.highest(low, math.min(bar_index + 1, 5)))
// @pf-trace a=a
// @pf-trace b=b
// @pf-trace ta=ta_
// @pf-trace tb=tb
'''


def test_each_payload_copy_windows_by_its_own_count():
    cpp = transpile(HELPER_VAR_LENGTH)
    members = re.findall(r"pineforge::source::SeriesHighest (_sec\d+__ta_highest_\d+);", cpp)
    assert len({m.split("__")[0] for m in members}) == 4, members


# ---------------------------------------------------------------------------
# Chart-feed runs
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgint5_compositions")
    feed = chart_feed_head(engine, base, BARS)
    builds = {
        "binop": Build(BINOP, trace=True),
        "wide_if_na": Build(WIDE_IF_NA, trace=True),
        "var_selection": Build(VAR_SELECTION, overrides={"Mode": "B"}, trace=True),
        "helper_var_length": Build(HELPER_VAR_LENGTH, trace=True),
    }
    return feed, execute_all(engine, feed, base, builds)


def test_the_ordered_expression_reads_the_64_bit_sum(runs):
    _, outcomes = runs
    got = _values(ok(outcomes, "binop").traces["default"], "mixed")
    # [5, 10, 20]: the remove takes 20 (times 0), size() then reads 2.
    assert len(got) == BARS and set(got) == {2880000002.0}


def test_the_unmatched_arm_is_the_wide_na_and_the_arm_keeps_64_bits(runs):
    _, outcomes = runs
    records = ok(outcomes, "wide_if_na").traces["default"]
    en, twn = _values(records, "en"), _values(records, "twn")
    assert len(en) == BARS and en == twn and {0.0, 1.0} <= set(en)
    for down, ek, bi in zip(twn, _values(records, "ek"), _values(records, "bi")):
        assert ek == (0.0 if down else (2160000000 + bi) / 1000.0), (ek, bi)


def test_the_selection_initializes_once_at_its_declaration(runs):
    feed, outcomes = runs
    rows = _chart_rows(feed)
    first = rows[0]
    q0 = first["close"] if first["close"] > first["open"] else first["open"]
    outcome = ok(outcomes, "var_selection")
    for tag, p0, s0 in (("default", first["open"], 1.0), ("override", first["close"], 2.0)):
        records = outcome.traces[tag]
        p, p1 = _values(records, "p"), [_nan(v) for v in _values(records, "p1")]
        s1, q, q1 = _values(records, "s1"), _values(records, "q"), _values(records, "q1")
        assert len(p) == BARS, tag
        assert set(p) == {p0} and set(q) == {q0}, tag
        assert math.isnan(p1[0]) and set(p1[1:]) == {p0}, tag
        assert math.isnan(_nan(q1[0])) and set(q1[1:]) == {q0}, tag
        assert s1[0] == 0.0 and set(s1[1:]) == {s0}, tag


def test_each_copy_equals_its_spelled_out_twin(runs):
    _, outcomes = runs
    records = ok(outcomes, "helper_var_length").traces["default"]
    for name, twin in (("a", "ta"), ("b", "tb")):
        got, want = _values(records, name), _values(records, twin)
        assert len(got) == len(want) == BARS, name
        assert all(same(_nan(x), _nan(y)) for x, y in zip(got, want)), name
    assert _values(records, "a") != _values(records, "b")
