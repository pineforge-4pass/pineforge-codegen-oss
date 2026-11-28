"""#466 review: what a conditional helper call's own clock must keep working.

The first #466 change gave ``x[k]`` of a bar-field argument a history of its
own when its written call is conditional: one entry per requested bar, holding
the last completed call's argument. Its review named four defects; each test
here has one of them as its target:

* a ``ta.*`` site the evaluator computes where it opens still reads the
  requested-bar history of that argument, which must stay declared (F1);
* state a helper keeps on such a call -- a local read with history, a
  ``var`` -- advances with the call's completed runs (F2);
* only ``request.security`` keeps one entry per requested bar for such a
  call; the other request kinds keep the bar field's own history (F3);
* a name the caller binds is never read as a bar field for its spelling (F4).

No value here is TradingView's. As in the #466 rows, the expectations are the
issue's call-site rule on a synthetic feed: there is no stored fixture and no
capture date.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

import pineforge_codegen.codegen.security as security_module
from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import FuncCall, Identifier, NumberLiteral, Subscript
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp, run_emitted_tu
from tests._e2e import reference_codegen, transpile_json


_BASE = "9b17c40e074a1bcab7ec581b5e71f13b171deb42"
_START = 1_743_465_600_000
_BAR_MS = 15 * 60_000
_TAKEN = (0, 2, 7, 8, 12, 15)


# --- F1 ---------------------------------------------------------------------

_HOISTED_TA = {
    # The ta.sma site is reached twice (``v + v``): the evaluator computes it
    # once where it opens, not inside the conditional call.
    "reached-twice": """//@version=6
strategy("Hoisted argument history A")
f(float x) => ta.sma(x[1], 5)
twice(float v) => v + v
plot(request.security(syminfo.tickerid, "15", close > open ? twice(f(close)) : na))
""",
    # A multi-statement helper anywhere in the payload keeps every ta.* site
    # in that opening block.
    "beside-a-multi-statement-helper": """//@version=6
strategy("Hoisted argument history B")
f(float x) => ta.sma(x[1], 5)
g() =>
    a = high - low
    a
plot(request.security(syminfo.tickerid, "15", (close > open ? f(close) : na) + g()))
""",
}


@pytest.mark.parametrize("name", sorted(_HOISTED_TA))
def test_hoisted_ta_argument_history_is_declared(name):
    cpp = transpile(_HOISTED_TA[name])
    used = set(re.findall(r"\b_sec\d+_hist_\w+", cpp))
    assert used, "NOT COMPARED: the unit reads no requested bar-field history member"
    declared = set(re.findall(r"\bSeries<[^<>]+>\s+(_sec\d+_hist_\w+)", cpp))
    assert used <= declared, (
        f"read but never declared: {sorted(used - declared)}; declared {sorted(declared)}"
    )
    compile_cpp(cpp, label=f"hoisted ta argument history ({name})")


# --- F2 ---------------------------------------------------------------------

_STATE_DRIVER = r"""
#include <iomanip>
#include <iostream>
#include <vector>
struct Observer : GeneratedStrategy {
    void evaluate_security(int id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(id, bar, complete);
        if (id == 0)
            std::cout << bar.timestamp << ' ' << complete << ' ' << bar.close << ' '
                      << VALUES << '\n';
    }
};
int main() {
    Observer strategy;
    std::cout << std::setprecision(17);
    std::vector<Bar> bars = {BARS};
    strategy.run(bars.data(), bars.size(), "5", "5");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 1;
    }
    return 0;
}
"""

# name -> (script, the C++ the driver prints after the close, dense column?)
_CALL_STATE = {
    # The #466 row's own shape, its helper counting its runs through a local
    # read with history. The second call site runs on every requested bar.
    "local-series-counter": (
        """//@version=6
strategy("Requested call state")
calls(int x) =>
    float c = 0.0
    c := nz(c[1], 0.0) + 1.0
    x[1] > 0 ? c : c
sample() =>
    sparse = close > open ? calls(time) : -2
    dense = calls(time)
    [nz(sparse, -1), nz(dense, -1)]
[s, d] = request.security(syminfo.tickerid, "15", sample(), lookahead=barmerge.lookahead_on)
plot(s)
plot(d)
""",
        "std::get<0>(_req_sec_0) << ' ' << std::get<1>(_req_sec_0)",
        True,
    ),
    # A ``var`` counter. Under ``sample()``'s ternary the declaration is
    # refused ("inside conditional control flow",
    # tests/test_security_helper_local_var.py); a global's initializer is
    # the spelling that reaches the same conditional call.
    "var-counter-through-global": (
        """//@version=6
strategy("Requested call state")
calls(int x) =>
    var float n = 0.0
    n += 1.0
    x[1] > 0 ? n : n
counted = close > open ? calls(time) : -2
v = request.security(syminfo.tickerid, "15", counted, lookahead=barmerge.lookahead_on)
plot(v)
""",
        "_req_sec_0",
        False,
    ),
}


@pytest.mark.parametrize("name", sorted(_CALL_STATE))
def test_helper_state_counts_completed_calls(name):
    # Three five-minute children per requested bar. The first child takes the
    # opposite branch from the completed bar, so a call can run on a
    # provisional evaluation only, or first on a later evaluation of its bar.
    # Neither may leave a slot behind or overwrite the previous call's.
    source, values, has_dense = _CALL_STATE[name]
    times = [_START + i * _BAR_MS for i in range(16)]
    bars = [
        f"Bar{{10, 11, 9, {11 if take else 9}, 1, {timestamp + child * 300_000}LL}}"
        for i, timestamp in enumerate(times)
        for child, take in enumerate((i not in _TAKEN, i in _TAKEN, i in _TAKEN))
    ]
    output = run_emitted_tu(
        transpile(source),
        _STATE_DRIVER.replace("VALUES", values).replace("BARS", ",\n".join(bars)),
        opt="-O2", label=f"helper state on a conditional call ({name})",
    )
    rows = [line.split() for line in output.splitlines()]
    width = 5 if has_dense else 4
    assert all(len(row) == width and row[1] in ("0", "1") for row in rows), (
        f"NOT COMPARED: expected {width} columns with a completion flag; received {rows}"
    )
    completed = [int(t) for t, complete, *_ in rows if complete == "1"]
    assert completed == times, f"NOT COMPARED: completed opens {completed}, expected {times}"
    index = {t: i for i, t in enumerate(times)}
    observed = {int(t) for t, *_ in rows}
    assert observed == index.keys(), (
        f"NOT COMPARED: evaluator opens {sorted(observed)}, expected {times}"
    )
    completed_takes = [index[int(t)] for t, flag, close, *_ in rows
                       if flag == "1" and float(close) > 10]
    assert completed_takes == list(_TAKEN), (
        f"NOT COMPARED: completed calls {completed_takes}, expected {_TAKEN}"
    )
    reversals = {(index[int(t)] in _TAKEN, float(close) > 10)
                 for t, flag, close, *_ in rows if flag == "0"}
    assert {(True, False), (False, True)} <= reversals, (
        f"NOT COMPARED: expected both provisional branch reversals, observed {reversals}"
    )
    for timestamp, complete, close, sparse, *dense in rows:
        i = index[int(timestamp)]
        # A run sees the count its last completed run left, plus itself.
        completed_before = sum(j < i for j in _TAKEN)
        expected = completed_before + 1 if float(close) > 10 else -2
        assert float(sparse) == expected, (i, complete, close, sparse, expected)
        if has_dense:
            assert float(dense[0]) == i + 1, (i, complete, dense[0], i + 1)


def test_var_counter_under_the_conditional_call_keeps_its_refusal():
    # The review's script for F2, as written. Its ``var`` sits in a helper a
    # ternary arm calls, which was refused before #466 and still is: the
    # ``var`` row above reaches the conditional call through a global.
    with pytest.raises(CompileError, match="inside conditional control flow"):
        transpile("""//@version=6
strategy("F2")
cnt(int x) =>
    var int n = 0
    n += 1
    x[1] > 0 ? n : n
sample() => close > open ? cnt(time) : -2
plot(request.security(syminfo.tickerid, "15", sample(), lookahead=barmerge.lookahead_on))
""")


def test_global_read_inside_a_conditional_call_is_lowered_around_it():
    # ``g`` is a statement of the requested context. The conditional call
    # reads it first; the other arm reads the same local, which only exists
    # there when the global's statements stay outside the call.
    cpp = transpile("""//@version=6
strategy("Global read in a conditional call")
scaled(float v) =>
    a = v * 2
    a
g = scaled(close)
f(float x) => x[1] + g
plot(request.security(syminfo.tickerid, "15", close > open ? f(close) : g))
""")
    body = cpp[cpp.index("void _eval_security_0("):]
    declared = re.search(r"\b\w+ (_sec0_scaled_\d+_a) = ", body)
    assert declared is not None, "NOT COMPARED: the global's helper local is not declared"
    assert "_req_sec_0 = " in body, "NOT COMPARED: the evaluator assigns no result"
    assert declared.start() < body.index("_req_sec_0 = "), (
        f"{declared.group(1)} is declared inside the payload expression, "
        "out of reach of its other reads"
    )
    compile_cpp(cpp, label="global read inside a conditional helper call")


# --- F3 and F4 --------------------------------------------------------------

def _registered_ring_types(request_flags, argument, caller_frames=()):
    """Element types of the rings, one entry per requested bar, discovery
    registers for ``lag(argument)``'s ``x[1]``, the call being conditional."""
    call = FuncCall(callee=Identifier(name="lag"), args=[argument])
    frame = security_module._SecurityHelperArgumentFrame(
        {"x": argument}, tuple(caller_frames), call=call)
    emitter = security_module.SecurityEmitter()
    emitter.ctx = SimpleNamespace(identifier_binding_scopes={})
    emitter._security_calls = [{"sec_id": 0, **request_flags}]
    emitter._security_arg_history_sec = 0
    emitter._security_arg_history_discovering = True
    emitter._security_lazy_call_ids = {id(call)}
    emitter._security_sparse_arg_histories = {}
    emitter._security_sparse_call_paths = {}
    read = Subscript(object=Identifier(name="x"), index=NumberLiteral(value=1))
    registered = emitter._security_register_sparse_argument(
        read, argument, (*caller_frames, frame))
    types = [cell.completed_type
             for cell in emitter._security_sparse_arg_histories.get(0, {}).values()]
    assert registered == bool(types), (
        f"NOT COMPARED: registration answered {registered}, rings {types}"
    )
    return types


_OTHER_REQUEST_KINDS = ("is_lower_tf_array", "foreign", "heikinashi", "runtime_context")


@pytest.mark.parametrize("kind", _OTHER_REQUEST_KINDS)
def test_only_request_security_keeps_one_entry_per_requested_bar(kind):
    assert _registered_ring_types({}, Identifier(name="close")) == ["double"], (
        "NOT COMPARED: request.security registered no ring of one entry per requested bar"
    )
    assert _registered_ring_types({kind: True}, Identifier(name="close")) == []


_KIND_REQUESTS = {
    "is_lower_tf_array": ('v = request.security_lower_tf(syminfo.tickerid, "1", sample())\n'
                          "plot(array.size(v))\n"),
    "foreign": 'v = request.security("PF:A", "60", sample())\nplot(v)\n',
    "heikinashi": ('v = request.security(ticker.heikinashi(syminfo.tickerid), "15", sample())\n'
                   "plot(v)\n"),
    "runtime_context": ('chosen = bar_index >= 0 ? "D" : str.tostring(bar_index)\n'
                        "v = request.security(syminfo.tickerid, chosen, sample())\nplot(v)\n"),
}


def _same_outcome_as_base(source, tmp_path):
    reference = reference_codegen(_BASE)
    assert reference is not None, f"NOT COMPARED: base {_BASE} unavailable; head has source"
    path = tmp_path / "script.pine"
    path.write_text(source, encoding="utf-8")
    before = transpile_json(path, root=reference)
    after = transpile_json(path)
    assert after == before, (
        f"head ok={after['ok']}, base ok={before['ok']}; "
        f"head diagnostics {after['diagnostics']}, base diagnostics {before['diagnostics']}"
    )
    return before


@pytest.mark.parametrize("kind", _OTHER_REQUEST_KINDS)
def test_other_request_kinds_keep_the_base_outcome(kind, tmp_path):
    # The whole answer of the base for a conditional helper call in each
    # other kind of request: its C++, or its refusal, byte for byte.
    _same_outcome_as_base(
        '//@version=6\nstrategy("Other request kinds")\n'
        "lag(float x) => x[1]\n"
        "sample() => close > open ? lag(close) : lag(open)\n"
        + _KIND_REQUESTS[kind], tmp_path)


def test_a_caller_binding_is_not_a_bar_field_by_its_spelling():
    # ``wrapped(close)`` binding a parameter spelled ``time``, then
    # ``lag(time)``: the argument is the caller's float, not the bar's time.
    assert _registered_ring_types({}, Identifier(name="time")) == ["int64_t"], (
        "NOT COMPARED: the requested bar's own time registered no int64_t ring"
    )
    outer = FuncCall(callee=Identifier(name="wrapped"), args=[Identifier(name="close")])
    caller = security_module._SecurityHelperArgumentFrame(
        {"time": outer.args[0]}, (), call=outer)
    assert _registered_ring_types({}, Identifier(name="time"), (caller,)) == []


def test_parameter_spelled_time_keeps_the_base_outcome(tmp_path):
    # The review's script for F4. The analyzer gives a callable's parameter
    # spelled like a builtin a private name before codegen, so this compares
    # what that leaves: the base's answer, whatever it is.
    _same_outcome_as_base("""//@version=6
strategy("Caller parameter spelled time")
lag(float x) => x[1]
wrapped(float time) => lag(time)
sample() => close > open ? wrapped(close) : na
plot(request.security(syminfo.tickerid, "15", sample()))
""", tmp_path)


# --- Byte control -----------------------------------------------------------

def test_dense_helper_state_keeps_base_cpp(tmp_path):
    # ``dir`` has a literal seed, even though its later stores read the bar.
    # Keep the entire base result for this concordance shape.
    before = _same_outcome_as_base("""//@version=6
strategy("Dense helper state")
int almaLen = 9
int sdLen = 9
float almaFactor = 1.5

f_alma_sig(float src) =>
    float alma = ta.alma(src, almaLen, 0.85, 4.0)
    float dev = ta.stdev(src, sdLen)
    float upper = alma + almaFactor * dev
    float lower = alma - almaFactor * dev
    float prevUpper = nz(upper[1], upper)
    float prevLower = nz(lower[1], lower)
    upper := upper < prevUpper or src[1] > prevUpper ? upper : prevUpper
    lower := lower > prevLower or src[1] < prevLower ? lower : prevLower
    var int dir = 0
    dir := src > upper ? 1 : src < lower ? -1 : nz(dir[1], 0)
    [dir, alma]

[sig15, alma15] = request.security(syminfo.tickerid, "15", f_alma_sig(close[1]), lookahead=barmerge.lookahead_off)
plot(sig15 + alma15)
""", tmp_path)
    assert before["ok"] and before["cpp"], f"NOT COMPARED: base produced no C++: {before}"
    assert "security_series_slot_is_new(0)" in before["cpp"], (
        "NOT COMPARED: the control lowers no requested-bar slot"
    )
