"""Codegen contract for strategy ``calc_on_order_fills`` rollback support."""

from __future__ import annotations

import re

from pineforge_codegen import transpile


def _strategy(header: str, body: str = "") -> str:
    return f'//@version=6\nstrategy("T"{header})\n{body}'


def test_calc_on_order_fills_declaration_and_runtime_override_plumbing():
    cpp = transpile(_strategy(", calc_on_order_fills=true"))

    assert "calc_on_order_fills_ = true;" in cpp
    assert (
        'if (key == "calc_on_order_fills") { calc_on_order_fills_ = '
        '(value == "true" || value == "1"); return; }'
    ) in cpp


def test_calc_on_order_fills_false_and_calc_on_every_tick_are_independent():
    explicit_false = transpile(_strategy(", calc_on_order_fills=false"))
    every_tick_only = transpile(_strategy(", calc_on_every_tick=true"))

    # The override setter is emitted for every strategy.  Limit this check to
    # the constructor body so that it cannot make either assertion pass.
    false_ctor = explicit_false.split("explicit GeneratedStrategy()", 1)[1].split(
        "void set_strategy_override", 1
    )[0]
    every_tick_ctor = every_tick_only.split("explicit GeneratedStrategy()", 1)[
        1
    ].split("void set_strategy_override", 1)[0]
    assert "calc_on_order_fills_ = true;" not in false_ctor
    assert "calc_on_order_fills_ = true;" not in every_tick_ctor


_ROLLBACK_PROBE = '''//@version=6
strategy("COOF state", calc_on_order_fills=true)
type Pt
    float x
    float y
bump(float v) =>
    var float total = 0.0
    total += v
    fixnan(total)
var float scalar = 0.0
var float historical = 0.0
var array<float> xs = array.new<float>()
var mp = map.new<string, float>()
var mx = matrix.new<float>(2, 2, 0.0)
var Pt point = Pt.new(1.0, 2.0)
var line ln = na
scalar += close
historical += close
previous = historical[1]
array.push(xs, scalar)
map.put(mp, "x", scalar)
matrix.set(mx, 0, 0, scalar)
point.x := scalar
ln := line.new(bar_index, close, bar_index + 1, close)
e = ta.ema(close, 3)
f = fixnan(close)
a = bump(close)
b = bump(open)
if e > 0
    strategy.entry("L", strategy.long)
'''


def test_script_state_checkpoint_covers_every_mutable_state_family():
    """Mutation guard: dropping any state family must break this inventory."""
    cpp = transpile(_ROLLBACK_PROBE)
    fields = {
        name: int(index)
        for name, index in re.findall(
            r"_PFCheckpointTraits<decltype\(GeneratedStrategy::(\w+)\)>::snapshot_type _pf_value_(\d+);",
            cpp,
        )
    }

    # Scalar + Series, TA/call-site helpers, all collection kinds, a UDT,
    # drawing handle/arenas, function-local cloned state, and init latches.
    expected = {
        "scalar",
        "historical",
        "_ta_ema_1",
        "_prev_fixnan_1",
        "xs",
        "mp",
        "mx",
        "point",
        "ln",
        "total",
        "total_cs1",
        "_prev_fixnan_2",
        "_prev_fixnan_1_cs1",
        "_pf_lines_",
        "_pf_boxes_",
        "_pf_labels_",
        "_pf_linefills_",
        "_var_initialized",
        "_fvinit_bump_cs0",
        "_fvinit_bump_cs1",
        "_ta_initialized_",
        "_inputs_initialized_",
    }
    assert expected <= fields.keys()

    # Every owned checkpoint field must participate in both directions.  This
    # catches mutations which leave the struct populated but omit snapshot or
    # restore plumbing for one member.
    for name, index in fields.items():
        assert re.search(
            rf"^\s+_PFCheckpointTraits<decltype\(GeneratedStrategy::{re.escape(name)}\)>::take\({re.escape(name)}\),$",
            cpp,
            re.MULTILINE,
        )
        assert (
            f"_PFCheckpointTraits<decltype(GeneratedStrategy::{name})>::restore("
            f"this->{name}, _pf_script_state_checkpoint_->_pf_value_{index});"
            in cpp
        )

    # Full-dataset precalc output is immutable during a broker walk.  It must
    # not be copied per bar; the live TA helper above remains checkpointed for
    # dynamic and magnifier execution.
    assert "GeneratedStrategy::_precalc_" not in cpp
    assert "GeneratedStrategy::_use_precalc" not in cpp
    assert "std::is_copy_constructible_v<_PFScriptState>" in cpp
    assert "std::is_copy_assignable_v<_PFScriptState>" in cpp


def test_commit_replaces_checkpoint_with_current_live_state():
    cpp = transpile(_strategy(""))
    commit = cpp.split("void commit_script_state() override", 1)[1].split(
        "explicit GeneratedStrategy()", 1
    )[0]

    assert "snapshot_script_state();" in commit
    assert "reset(" not in commit


_HISTORY_ADVANCE_PROBE = '''//@version=6
strategy("COOF history", calc_on_order_fills=true, process_orders_on_close=true)
source_input = input.source(close, "Source")
source_prev = source_input[1]
var float carried = 0.0
carried += close
carried_prev = carried[1]
rolling = close + open
rolling_prev = rolling[1]
chart_prev = close[1]
index_prev = bar_index[1]
position_prev = strategy.position_size[1]
ema = ta.ema(close, 2)
ema_prev = ema[1]
[basis, upper, lower] = ta.bb(close, 2, 2.0)
upper_prev = upper[1]
inline_prev = ta.highest(high, 2)[1]
history_arg(float src) =>
    src[1]
series_prev = history_arg(close + open)
if barstate.isnew
    strategy.entry("L", strategy.long)
'''


def test_post_fill_recalc_updates_current_history_slot_but_barstate_stays_new():
    """Post-C fill recalcs must not turn Pine's current bar into ``[1]``.

    The engine deliberately keeps historical ``barstate.isnew`` true during a
    fill recalculation.  History advancement therefore has a separate runtime
    predicate: every rolling member updates its existing current-bar slot when
    the ordinary-close checkpoint is restored, while ``barstate.isnew`` keeps
    lowering to ``is_first_tick_``.
    """
    cpp = transpile(_HISTORY_ADVANCE_PROBE)
    on_bar = cpp.split("void on_bar(const Bar& bar) override {", 1)[1].split(
        "\n    }", 1
    )[0]

    expected_writes = {
        "_s_close": "current_bar_.close",
        "bar_index": "pine_bar_index()",
        "_strat_position_size": "signed_position_size()",
        "rolling": "(current_bar_.close + current_bar_.open)",
    }
    for member, value in expected_writes.items():
        assert (
            f"if (history_advances_new_bar()) {member}.push({value});" in on_bar
        )
        assert f"else {member}.update({value});" in on_bar

    # A persistent series carries its current value into a genuinely new bar,
    # but updates that same slot during post-fill execution.
    assert (
        "if (history_advances_new_bar()) carried.push(carried[0]);" in on_bar
    )

    # All VarDecl series families use the shared push/update lowering: an
    # input.source history, a TA result, and a history-referenced tuple field.
    for member in ("source_input", "ema", "upper"):
        assert f"if (history_advances_new_bar()) {member}.push(" in on_bar
        assert f"else {member}.update(" in on_bar

    # TA state and temporary history buffers obey the same history predicate.
    # Temporary buffers are named class members: fill recalculation rollback
    # cannot restore a function-local static, and two source sites must never
    # share one history stream.
    assert "history_advances_new_bar() ? _ta_ema_1.compute(" in on_bar
    assert re.search(
        r"history_advances_new_bar\(\) \? _ta_highest_\d+\.compute\(", on_bar
    )
    assert "static thread_local Series" not in cpp
    assert re.search(
        r"if \(history_advances_new_bar\(\)\) _hist_call_\d+\.push\(_hv\);",
        on_bar,
    )
    assert re.search(
        r"if \(history_advances_new_bar\(\)\) _series_arg_\d+\.push\(_sv\);",
        on_bar,
    )

    # Mutation guard: coupling barstate.isnew to history advancement would make
    # it false during historical fill recalcs, contrary to Pine semantics.
    assert "if (is_first_tick_) {" in on_bar
    assert "if (history_advances_new_bar()) {\n            strategy_entry" not in on_bar


_INLINE_BUFFER_PROBE = '''//@version=6
strategy("COOF inline members", calc_on_order_fills=true, process_orders_on_close=true)
history_arg(float src) =>
    src[1]
wrapped(float src) =>
    inline_prev = ta.highest(src, 2)[1]
    bridged_prev = history_arg(src + 1.0)
    inline_prev + bridged_prev
top_inline_a = ta.highest(high, 2)[1]
top_inline_b = ta.lowest(low, 2)[1]
top_bridge_a = history_arg(close + open)
top_bridge_b = history_arg(high - low)
wrapped_a = wrapped(close)
wrapped_b = wrapped(open)
if barstate.isnew
    strategy.entry("L", strategy.long)
'''


def _checkpoint_fields(cpp: str) -> dict[str, int]:
    return {
        name: int(index)
        for name, index in re.findall(
            r"decltype\(GeneratedStrategy::(\w+)\) _pf_value_(\d+);", cpp
        )
    }


def test_inline_history_buffers_are_owned_independent_and_clear_at_bar_zero():
    """Mutation guard for both synthesized temporary-Series families.

    This kills three tempting regressions: moving a buffer back into a local
    static, collapsing separate source sites/UDF variants onto one stream, or
    clearing only when a conditional site happens to execute on bar zero.

    The clear assertion is deliberately narrow. It preserves the legacy
    synthetic-buffer run-start guard; it does not claim that every generated
    member/init latch supports full same-handle reruns.
    """
    cpp = transpile(_INLINE_BUFFER_PROBE)
    fields = _checkpoint_fields(cpp)
    hist_members = re.findall(r"^\s+Series<double> (_hist_call_\d+);$", cpp, re.MULTILINE)
    arg_members = re.findall(r"^\s+Series<double> (_series_arg_\d+);$", cpp, re.MULTILINE)

    # Two top-level sites plus one site in each wrapped() call-site clone.
    assert len(hist_members) == 4
    assert len(set(hist_members)) == 4
    assert len(arg_members) == 4
    assert len(set(arg_members)) == 4
    assert "static thread_local Series" not in cpp

    on_bar = cpp.split("void on_bar(const Bar& bar) override {", 1)[1].split(
        "\n    }", 1
    )[0]
    for member in hist_members + arg_members:
        index = fields[member]
        assert f"if (history_advances_new_bar() && bar_index_ == 0) {member}.clear();" in on_bar
        assert f"if (history_advances_new_bar()) {member}.push(" in cpp
        assert f"else {member}.update(" in cpp
        assert re.search(rf"^\s+{re.escape(member)},$", cpp, re.MULTILINE)
        assert (
            f"this->{member} = _pf_script_state_checkpoint_->_pf_value_{index};"
            in cpp
        )

    wrapped_cs0 = cpp.split("double wrapped_cs0(", 1)[1].split("\n    }", 1)[0]
    wrapped_cs1 = cpp.split("double wrapped_cs1(", 1)[1].split("\n    }", 1)[0]
    assert set(re.findall(r"_hist_call_\d+", wrapped_cs0)).isdisjoint(
        re.findall(r"_hist_call_\d+", wrapped_cs1)
    )
    assert set(re.findall(r"_series_arg_\d+", wrapped_cs0)).isdisjoint(
        re.findall(r"_series_arg_\d+", wrapped_cs1)
    )


_NESTED_SYNTHETIC_ONLY_PROBE = '''//@version=6
strategy("COOF nested synthetic", calc_on_order_fills=true)
history_arg(float src) =>
    src[1]
leaf(float src) =>
    history_arg(src + 1.0)
left(float src) =>
    leaf(src)
right(float src) =>
    leaf(src)
left_value = left(close)
right_value = right(open)
'''


def test_nested_synthetic_only_helpers_dispatch_to_distinct_leaf_instances():
    """Pure wrappers must preserve the synthetic leaf's call-path identity."""
    cpp = transpile(_NESTED_SYNTHETIC_ONLY_PROBE)
    left = cpp.split("double left_cs0(", 1)[1].split("\n    }", 1)[0]
    right = cpp.split("double right_cs0(", 1)[1].split("\n    }", 1)[0]

    assert "leaf_cs0(src)" in left
    assert "leaf_cs1(src)" in right
    assert "leaf_cs0(src)" not in right

    leaf0 = cpp.split("double leaf_cs0(", 1)[1].split("\n    }", 1)[0]
    leaf1 = cpp.split("double leaf_cs1(", 1)[1].split("\n    }", 1)[0]
    assert set(re.findall(r"_series_arg_\d+", leaf0)).isdisjoint(
        re.findall(r"_series_arg_\d+", leaf1)
    )


_SWITCH_ARM_SYNTHETIC_PROBE = '''//@version=6
strategy("COOF switch synthetic", calc_on_order_fills=true)
passthrough(float src) =>
    src
wrapped(float src, int mode) =>
    switch mode
        1 => passthrough(src)[1]
        => src
first = wrapped(close, 1)
second = wrapped(open, 1)
'''


def test_switch_arm_tuple_history_marks_wrapper_stateful_and_isolates_calls():
    """SwitchStmt.cases stores arms as tuples; state discovery must enter them."""
    cpp = transpile(_SWITCH_ARM_SYNTHETIC_PROBE)
    assert "double wrapped_cs0(" in cpp
    assert "double wrapped_cs1(" in cpp

    members = re.findall(r"^\s+Series<double> (_hist_call_\d+);$", cpp, re.MULTILINE)
    assert len(members) == 2
    body0 = cpp.split("double wrapped_cs0(", 1)[1].split("\n    }", 1)[0]
    body1 = cpp.split("double wrapped_cs1(", 1)[1].split("\n    }", 1)[0]
    assert set(re.findall(r"_hist_call_\d+", body0)).isdisjoint(
        re.findall(r"_hist_call_\d+", body1)
    )


_UDT_METHOD_SYNTHETIC_PROBE = '''//@version=6
strategy("COOF UDT synthetic", calc_on_order_fills=true)
type Box
    float bias
passthrough(float src) =>
    src
history_arg(float src) =>
    src[1]
method measure(Box self, float src) =>
    call_prev = passthrough(src + self.bias)[1]
    arg_prev = history_arg(src - self.bias)
    call_prev + arg_prev
var Box bx = Box.new(1.0)
first = bx.measure(close)
second = bx.measure(open)
'''


def test_udt_method_synthetic_history_isolated_per_source_call_site():
    """UDT methods need the same per-call-site state identity as plain UDFs."""
    cpp = transpile(_UDT_METHOD_SYNTHETIC_PROBE)
    assert "double _udt_Box_measure_cs0(" in cpp
    assert "double _udt_Box_measure_cs1(" in cpp
    assert "first = _udt_Box_measure_cs0(" in cpp
    assert "second = _udt_Box_measure_cs1(" in cpp

    hist_members = re.findall(r"^\s+Series<double> (_hist_call_\d+);$", cpp, re.MULTILINE)
    arg_members = re.findall(r"^\s+Series<double> (_series_arg_\d+);$", cpp, re.MULTILINE)
    assert len(hist_members) == 2
    assert len(arg_members) == 2
    fields = _checkpoint_fields(cpp)
    assert set(hist_members + arg_members) <= fields.keys()

    body0 = cpp.split("double _udt_Box_measure_cs0(", 1)[1].split("\n    }", 1)[0]
    body1 = cpp.split("double _udt_Box_measure_cs1(", 1)[1].split("\n    }", 1)[0]
    assert set(re.findall(r"_(?:hist_call|series_arg)_\d+", body0)).isdisjoint(
        re.findall(r"_(?:hist_call|series_arg)_\d+", body1)
    )


def test_every_strategy_history_member_pushes_or_updates():
    body = "\n".join(
        f"v_{name} = strategy.{name}[1]"
        for name in (
            "position_size",
            "closedtrades",
            "opentrades",
            "wintrades",
            "losstrades",
            "equity",
            "netprofit",
            "openprofit",
            "initial_capital",
        )
    )
    cpp = transpile(_strategy(", calc_on_order_fills=true", body))
    on_bar = cpp.split("void on_bar(const Bar& bar) override {", 1)[1].split(
        "\n    }", 1
    )[0]

    for name in (
        "position_size",
        "closedtrades",
        "opentrades",
        "wintrades",
        "losstrades",
        "equity",
        "netprofit",
        "openprofit",
        "initial_capital",
    ):
        member = f"_strat_{name}"
        assert f"if (history_advances_new_bar()) {member}.push(" in on_bar
        assert f"else {member}.update(" in on_bar
        assert not re.search(rf"^\s*{re.escape(member)}\.push\(", on_bar, re.MULTILINE)
