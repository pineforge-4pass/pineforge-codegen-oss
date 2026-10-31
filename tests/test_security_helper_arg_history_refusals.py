"""Located refusals added with argument history and unchanged call contexts.

These shapes exercise the refusals the previous composer gave them. A
reassigned source is admitted when unconditionally and fully overwritten before
the request; late, conditional and recurrent overwrites remain refused. Native
rows reuse the neighbouring harness and its paired-engine guard.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict
from typing import NamedTuple

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import Subscript
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._legacy_cpp import (
    without_id_sites_receipt, without_regenerated_host_reads, without_restore_script_state,
)
from tests.test_security_helper_arg_history import (
    _CHART_CLOSES, _HOURLY_OPENS, _HOUR_MS, _assert_completed, _assert_sampled_sites,
    _assert_values, _difference, _run_requested, _sma, _source as _requested_source,
)
from tests.test_security_helper_local_var import _compile_and_run, _eval_body


_OLD = (
    "request.security helper-parameter history currently requires a direct "
    "OHLC/time bar-series binding"
)
_MESSAGES = {
    "PF-E3011": _OLD,
    "PF-E3094": _OLD + "; derived helper-parameter history requires a non-negative constant integer offset",
    "PF-E3095": "derived helper-parameter history capacity is not representable by the requested argument-history store",
    "PF-E3096": "derived helper-parameter history requires an unconditional call outside branches and loops",
    "PF-E3097": "derived helper-parameter history requires dependency-aware helper evaluation for this body",
    "PF-E3098": "composing derived helper-parameter and helper-result history is not yet supported",
    "PF-E3099": "derived helper-parameter history cannot replay this mutable global on requested-bar recomputation",
    "PF-E3100": "derived helper-parameter history currently supports numeric scalar arguments only",
    "PF-E3101": "this parameter type or width has no lossless requested argument-history store",
}


class _Refusal(NamedTuple):
    definitions: str
    payload: str
    code: str
    location: tuple[int, int] = (3, 10)
    method_sha256: str | None = None


_ROWS = [
    pytest.param(_Refusal(
        "f(x) => x[bar_index % 3] - x\n", "f(close - open)", "PF-E3094",
        method_sha256="1599a040ecfb28fec6e2b692c19222f38f6edd0401a323a4f8c8d2e606124955",
    ), id="dynamic-offset"),
    pytest.param(_Refusal(
        "n = input.int(2)\nf(x) => x[n] - x\n", "f(close - open)", "PF-E3094",
        (4, 10), "295c763ead51e01ed6d518ccca9e8dcd7a8531fd369a5701a73b31ad8e2cdec5",
    ), id="input-offset"),
    pytest.param(_Refusal(
        "f(x) => x[2147483647] - x\n", "f(close - open)", "PF-E3095",
    ), id="capacity-overflows-int"),
    pytest.param(_Refusal(
        "f(x) => x[2] - x\n"
        "h(d) =>\n    r = 0.0\n    if d > 0\n        r := f(d)\n    r\n",
        "h(close - open)", "PF-E3096",
        method_sha256="f52586134258afe742164ba4e5dc025e2743dc569e8501c1b1582f2fef927d4a",
    ), id="branch-in-root-helper"),
    pytest.param(_Refusal(
        "f(x) => x[2] - x\n"
        "h(d) =>\n    r = 0.0\n    for i = 0 to 1\n        r := f(d)\n    r\n",
        "h(close - open)", "PF-E3096",
    ), id="loop-in-root-helper"),
    pytest.param(_Refusal(
        "f(x) => x[2] - x\nh(d) =>\n    var float r = f(d)\n    r\n",
        "h(close - open)", "PF-E3096",
    ), id="once-initializer-in-root-helper"),
    pytest.param(_Refusal(
        "f(x) => x[2] - ta.sma(x, 3)\n", "f(close - open)", "PF-E3097",
    ), id="ta-consumes-sampled-parameter"),
    pytest.param(_Refusal(
        "g(y) => y[1]\nf(x) => g(x)\n", "f(close - open)", "PF-E3097",
        method_sha256="e258a84824629fd6a2df1775ecda69ca4ff16ac3e158a3f6d48b4c766b2cd407",
    ), id="forwarded-history-call"),
    pytest.param(_Refusal(
        "f(x) => x[2] - x\n", "f(close - open)[1]", "PF-E3098",
        method_sha256="3272053e3ecd2f0146bf14ab771b1121a62bd9240d2ac67041dfbfa86f6f80e3",
    ), id="helper-result-history"),
    pytest.param(_Refusal(
        "var float v = 1.0\nv += 2.0\nf(x) => x[2] - x\n", "f(v)", "PF-E3099",
        (5, 10),
    ), id="recurrent-global-compound-write"),
    pytest.param(_Refusal(
        "var float v = 1.0\nv := nz(v[1]) + 2.0\nf(x) => x[2] - x\n",
        "f(v)", "PF-E3099", (5, 10),
    ), id="recurrent-global-history"),
    pytest.param(_Refusal(
        "f(x) => x[1]\n", "f(str.tostring(close))", "PF-E3100",
        method_sha256="20c2d8b216a945acfad7d2990cf5157e448b793ea2ef728aadd4df3c35a03baf",
    ), id="string-argument"),
    pytest.param(_Refusal(
        "f(x) => x[1]\n", "f(close > open)", "PF-E3100",
    ), id="bool-argument"),
    pytest.param(_Refusal(
        "f(x) => x[1]\n", "f(time + 9007199254740993)", "PF-E3101",
    ), id="integer-without-lossless-store"),
]


def _source(row: _Refusal, envelope: str = "root") -> str:
    definitions, payload = row.definitions, row.payload
    if envelope == "builtin":
        payload = f"nz({payload})"
    elif envelope == "method":
        definitions += (
            "method wrap(float receiver, float d) => "
            + payload.replace("close - open", "d") + "\n"
        )
        payload = "close.wrap(close - open)"
    return (
        "//@version=6\nstrategy('probe')\n" + definitions
        + 'out = request.security(syminfo.tickerid, "60", ' + payload + ")\nplot(out)\n"
    )


def _assert_refusal(call, row: _Refusal, *, old: bool = False) -> None:
    with pytest.raises(CompileError) as failure:
        call()
    diagnostic, = failure.value.diagnostics
    assert diagnostic.code == ("PF-E3011" if old else row.code)
    assert diagnostic.phase == Phase.CODEGEN
    assert (diagnostic.location.line, diagnostic.location.col) == row.location
    expected = _OLD if old else _MESSAGES[row.code]
    assert diagnostic.message == expected
    assert diagnostic.hint is None
    if row.code == "PF-E3094" and not old:
        assert diagnostic.message.startswith(_OLD + "; ")


@pytest.mark.parametrize("row", _ROWS)
@pytest.mark.parametrize("requested", [True, False], ids=["requested", "legacy"])
@pytest.mark.parametrize("entry", ["declarations", "expression"])
def test_located_refusal_reaches_both_composer_callers_and_both_modes(
    row, requested, entry,
):
    source = _source(row)
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    generator = CodeGen(Analyzer(program).analyze())
    item = generator._security_calls[0]
    # A generator without this inventory falls back to its original composer
    # and reports PF-E3011, making the missing support visible.
    inventory = getattr(generator, "_security_argument_inventory", lambda _item: {})(item)
    generator._security_arg_inventory = {0: inventory}
    generator._security_arg_histories = {}
    generator._security_arg_history_sec = 0
    generator._security_arg_history_discovering = True
    generator._security_requested_calls = requested
    if entry == "declarations":
        operation = lambda: generator._collect_security_ohlc_hist_fields(item["expr_node"])
    else:
        expression = item["expr_node"]
        if isinstance(expression, Subscript):
            # The result-history dispatcher reaches this call after member
            # registration. Keep the inventory's result-history refusal flag.
            expression = expression.object
        operation = lambda: generator._build_security_expr(
            0, expression, None, {}, emitted_lines=[],
        )
    _assert_refusal(operation, row)


def _assert_mutable_source_composer_refusal(source, row, requested, entry):
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    generator = CodeGen(Analyzer(program).analyze())
    item = generator._security_calls[0]
    inventory = getattr(generator, "_security_argument_inventory", lambda _item: {})(item)
    generator._security_arg_inventory = {0: inventory}
    generator._security_arg_histories = {}
    generator._security_arg_history_sec = 0
    generator._security_arg_history_discovering = True
    generator._security_requested_calls = requested
    if entry == "declarations":
        operation = lambda: generator._collect_security_ohlc_hist_fields(item["expr_node"])
    else:
        operation = lambda: generator._build_security_expr(
            0, item["expr_node"], None, {}, emitted_lines=[],
        )
    _assert_refusal(operation, row)
    _assert_refusal(lambda: transpile(source), row)


@pytest.mark.parametrize("requested", [True, False], ids=["requested", "legacy"])
@pytest.mark.parametrize("entry", ["declarations", "expression"])
def test_conditional_source_overwrite_refuses_requested_argument_history(requested, entry):
    row = _Refusal(
        'var float src = input.source(close, "S")\n'
        'if close > open\n    src := open\n'
        'f(_s) => _s[1]\n',
        "f(src)", "PF-E3099", (6, 12),
    )
    _assert_mutable_source_composer_refusal(_source(row), row, requested, entry)


@pytest.mark.parametrize("requested", [True, False], ids=["requested", "legacy"])
@pytest.mark.parametrize("entry", ["declarations", "expression"])
def test_recurrent_source_overwrite_refuses_requested_argument_history(requested, entry):
    row = _Refusal(
        'var float src = input.source(close, "S")\n'
        'src := nz(src[1]) + 1\n'
        'f(_s) => _s[1]\n',
        "f(src)", "PF-E3099", (5, 12),
    )
    _assert_mutable_source_composer_refusal(_source(row), row, requested, entry)


@pytest.mark.parametrize("requested", [True, False], ids=["requested", "legacy"])
@pytest.mark.parametrize("entry", ["declarations", "expression"])
def test_source_overwrite_after_request_refuses_requested_argument_history(requested, entry):
    source = (
        '//@version=6\nstrategy("probe")\n'
        'var float src = input.source(close, "S")\n'
        'f(_s) => _s[1]\n'
        'x = request.security(syminfo.tickerid, "60", f(src))\n'
        'src := open\nplot(x)\n'
    )
    row = _Refusal("", "f(src)", "PF-E3099", (4, 12))
    _assert_mutable_source_composer_refusal(source, row, requested, entry)


def test_mutable_source_history_uses_last_full_overwrite_before_request():
    feeds = {"chart15": (15, _CHART_CLOSES)}
    overwritten = _run_requested(transpile(_requested_source(
        'var float src = input.source(close, "S")\n'
        'src := close\nsrc := open\n'
        'f(_s) => _s[1]\n',
        "f(src)",
    )), feeds)["chart15"]
    direct = _run_requested(transpile(_requested_source(
        "f(_s) => _s[1]\n", "f(open)",
    )), feeds)["chart15"]
    expected = [math.nan, *_HOURLY_OPENS[:-1]]
    _assert_completed(direct, expected)
    _assert_completed(overwritten, expected)
    assert [(row.timestamp, row.complete) for row in overwritten] == [
        (row.timestamp, row.complete) for row in direct
    ]
    for observed, control in zip(overwritten, direct):
        if math.isnan(control.value):
            assert math.isnan(observed.value), (observed, control)
        else:
            assert observed.value == control.value, (observed, control)


def _assert_unchanged_method(row, monkeypatch):
    method_source = _source(row, "method")
    candidate = transpile_full(method_source)
    # This is the old path's actual lowering, with the new inventory
    # disabled, rather than a handwritten C++ approximation of that path.
    with monkeypatch.context() as legacy:
        legacy.setattr(CodeGen, "_security_argument_inventory", lambda self, item: {},
                       raising=False)
        previous = transpile_full(method_source)
    assert candidate["cpp"] == previous["cpp"]
    assert [asdict(d) for d in candidate["diagnostics"]] == [
        asdict(d) for d in previous["diagnostics"]
    ]
    assert "PF-W3008" in [d.code for d in candidate["diagnostics"]]
    body = _eval_body(candidate["cpp"], 0, None)
    assert "wrap" in body
    assert "_arg_hist_" not in body
    if row.method_sha256 is not None:
        # Compare the earlier method emission with the rollback hook removed:
        # requested state now survives that hook, so its body is excluded from
        # the compatibility hash. The id-sites receipt exports are additive
        # metadata the pinned emission predates.
        assert hashlib.sha256(
            without_regenerated_host_reads(
                without_id_sites_receipt(without_restore_script_state(candidate["cpp"]))
            ).encode()
        ).hexdigest() == row.method_sha256


@pytest.mark.parametrize("row", _ROWS)
def test_product_refusal_and_unchanged_builtin_and_method_envelopes(row, monkeypatch):
    _assert_refusal(lambda: transpile(_source(row)), row)
    _assert_refusal(lambda: transpile(_source(row, "builtin")), row, old=True)
    _assert_unchanged_method(row, monkeypatch)


def test_reporter_inventory_admission_preserves_each_non_inventory_edge(monkeypatch):
    row = _Refusal(
        "f(x) => x[2] - x\na = ta.sma(close, 3)\nb = f(a)\n", "b", "",
        method_sha256="67c196a5569ba5b4560a12ee13f4e04c5d09a5a2cfbcc41c4ae51dad7279529d",
    )
    # This helper call through a global value admits argument history.
    # Its builtin and method forms keep their existing lowering.
    cpp = transpile(_source(row))
    _assert_sampled_sites(cpp, 1)
    _assert_refusal(lambda: transpile(_source(row, "builtin")), row, old=True)
    _assert_unchanged_method(row, monkeypatch)
    for payload in ("b + nz(b)", "close > open ? b : na"):
        boundary = row._replace(payload=payload)
        _assert_refusal(lambda: transpile(_source(boundary)), boundary, old=True)


def _sample_member(cpp: str) -> tuple[str, int]:
    cells = re.findall(r"Series<double>\s+(_sec0_arg_hist_\d+)\{(\d+)\};", cpp)
    assert len(cells) == 1, cells
    name, capacity = cells[0]
    return name, int(capacity)


def test_argument_ring_survives_restore_script_state():
    cpp = transpile(_source(_Refusal(
        "f(x) => x[2] - x\n", "f(close - open)", "",
    )))
    member, capacity = _sample_member(cpp)
    assert capacity == 3
    # Exercise the generated hooks as COOF does. Both the current sample and
    # retained slots change, and the live ring grows from two to three slots.
    # No generated C++ is replaced or normalized for this runtime check.
    driver = """
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    auto& ring = strategy.SAMPLE;
    ring.push(2.0);
    ring.push(3.0);
    strategy.snapshot_script_state();
    ring.update(30.0);
    ring.push(40.0);
    ring.push(50.0);
    if (ring.size() != 3 || ring[0] != 50.0 || ring[1] != 40.0 || ring[2] != 30.0)
        return 1;
    // No evaluation is replayed: the ring is exactly what the last evaluator left.
    strategy.restore_script_state();
    if (ring.size() != 3 || ring[0] != 50.0 || ring[1] != 40.0 || ring[2] != 30.0)
        return 2;
    strategy.prepare_script_run(nullptr, 0, false);
    if (ring.size() != 0 || !is_na(ring[0])) return 3;
    for (int i = 0; i < 8; ++i) ring.push(i);
    if (ring.size() != 3 || ring[0] != 7.0 || ring[2] != 5.0) return 4;
    std::cout << "preserved\\n";
    return 0;
}
""".replace("SAMPLE", member)
    assert _compile_and_run(cpp + driver).strip() == "preserved"


def test_offset_600_retains_601_samples_across_restore_and_new_runs():
    cpp = transpile(_source(_Refusal(
        "f(x) => x[600] - x\n", "f(close - open)", "",
    )))
    member, capacity = _sample_member(cpp)
    assert capacity == 601
    driver = """
#include <iostream>
#include <stdexcept>
#include <vector>
struct CapacityObserver : GeneratedStrategy {
    int pass = 0;
    int seen = 0;
    int fail_after = 0;
    bool interrupted = false;
    bool capacity_exceeded = false;
    void evaluate_security(int sec_id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(sec_id, bar, complete);
        if (SAMPLE.size() > 601) capacity_exceeded = true;
        if (sec_id != 0 || !complete) return;
        ++seen;
        if (fail_after && seen == fail_after) {
            interrupted = true;
            throw std::runtime_error("deliberate interrupted requested run");
        }
        if (!fail_after)
            std::cout << pass << ' ' << seen << ' ' << _req_sec_0 << ' '
                      << SAMPLE.size() << '\\n';
    }
};
int main() {
    CapacityObserver strategy;
    // Restore preserves requested progression; lifecycle resets retain the
    // explicit capacity 601, including after the ring has wrapped.
    for (int i = 1; i <= 601; ++i) strategy.SAMPLE.push(i);
    strategy.snapshot_script_state();
    strategy.SAMPLE.update(9999.0);
    strategy.SAMPLE.push(10000.0);
    strategy.restore_script_state();
    // update replaces 601; push appends 10000 and evicts 1.
    if (strategy.SAMPLE.size() != 601 || strategy.SAMPLE[0] != 10000.0
        || strategy.SAMPLE[1] != 9999.0 || strategy.SAMPLE[600] != 2.0) return 1;
    std::vector<Bar> bars;
    for (int i = 0; i < 601; ++i) {
        const double close = i + 1;
        bars.push_back(Bar{0.0, close, 0.0, close, 1.0, 3600000LL * i});
    }
    for (int pass = 0; pass < 2; ++pass) {
        strategy.seen = 0;
        strategy.pass = pass;
        strategy.run(bars.data(), 601, "60", "60");
    }
    // An interrupted engine run cannot start another run on that object.
    // Check its ring separately, then use a fresh instance for pass 2.
    CapacityObserver interrupted;
    interrupted.fail_after = 4;
    try { interrupted.run(bars.data(), 601, "60", "60"); }
    catch (const std::exception&) {}
    if (!interrupted.interrupted || interrupted.capacity_exceeded
        || interrupted.SAMPLE.size() > 601) return 2;
    for (int i = 1; i <= 601; ++i) {
        interrupted.SAMPLE.push(i);
        if (interrupted.SAMPLE.size() > 601) return 3;
    }
    if (interrupted.SAMPLE.size() != 601 || interrupted.SAMPLE[600] != 1.0
        || interrupted.SAMPLE[0] != 601.0) return 4;
    CapacityObserver fresh;
    fresh.pass = 2;
    fresh.run(bars.data(), 601, "60", "60");
    return 0;
}
""".replace("SAMPLE", member)
    output = _compile_and_run(cpp + driver)
    rows = [line.split() for line in output.splitlines()]
    assert len(rows) == 3 * 601
    for pass_ in range(3):
        run = rows[pass_ * 601 : (pass_ + 1) * 601]
        assert [int(row[0]) for row in run] == [pass_] * 601
        assert [int(row[1]) for row in run] == list(range(1, 602))
        assert [int(row[3]) for row in run] == list(range(1, 602))
        values = [float(row[2]) for row in run]
        assert all(math.isnan(value) for value in values[:600])
        assert values[-1] == -600.0


@pytest.mark.parametrize("producer", ["local-var", "overwritten-global", "bound-call-twice"])
def test_argument_producer_runs_once_before_sampling_and_rolls_back_on_recompute(producer):
    definitions = "f(x) => x[2] - x\n"
    if producer == "overwritten-global":
        definitions += "var float v = na\nv := close - open\n"
        payload = "f(v)"
        completed_samples = [2.0, 3.0, 4.0, 5.0, 6.0]
    else:
        definitions += "g() =>\n    var float v = 1.0\n    v += 2.0\n    v\n"
        payload = "f(g())"
        completed_samples = [3.0, 5.0, 7.0, 9.0, 11.0]
        if producer == "bound-call-twice":
            definitions += "b = f(g())\n"
            payload = "b + b"
    cpp = transpile(_requested_source(definitions, payload, lookahead="on"))
    member, capacity = _sample_member(cpp)
    assert capacity == 3
    body = _eval_body(cpp, 0, None)
    assert body.count(member + ".push(") == body.count(member + ".update(") == 1

    bars = []
    for hour, (open_, children) in enumerate(zip(_HOURLY_OPENS, _CHART_CLOSES)):
        for child, close in enumerate(children):
            timestamp = hour * _HOUR_MS + child * 15 * 60 * 1000
            fields = (open_, max(open_, close), min(open_, close), close, 1.0)
            bars.append("Bar{" + ", ".join(map(repr, fields)) + f", {timestamp}}}")
            open_ = close
    driver = """
#include <iomanip>
#include <iostream>
struct ProducerObserver : GeneratedStrategy {
    void evaluate_security(int sec_id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(sec_id, bar, complete);
        if (sec_id == 0)
            std::cout << bar.timestamp << ' ' << complete << ' '
                      << bar.open << ' ' << bar.close << ' ' << _req_sec_0 << ' '
                      << SAMPLE[0] << ' ' << SAMPLE.size() << '\\n';
    }
};
int main() {
    std::cout << std::setprecision(17);
    ProducerObserver strategy;
    Bar bars[] = {BARS};
    strategy.run(bars, 20, "15", "15");
    return 0;
}
""".replace("SAMPLE", member).replace("BARS", ",\n".join(bars))
    rows = [line.split() for line in _compile_and_run(cpp + driver).splitlines()]
    assert rows and all(len(row) == 7 for row in rows)
    completed = [row for row in rows if row[1] == "1"]
    assert [int(row[0]) for row in completed] == [hour * _HOUR_MS for hour in range(5)]
    _assert_values([float(row[5]) for row in completed], completed_samples)
    expected = _difference(completed_samples)
    if producer == "bound-call-twice":
        expected = [2 * value for value in expected]
    _assert_values([float(row[4]) for row in completed], expected)
    for row in rows:
        hour = int(row[0]) // _HOUR_MS
        assert int(row[6]) == min(hour + 1, 3), row
        sample = float(row[5])
        if producer == "overwritten-global":
            assert sample == float(row[3]) - float(row[2]), row
        else:
            # Every provisional evaluation of this requested slot must see
            # the same rolled-back local-var result, never another increment.
            assert sample == completed_samples[hour], row


def test_ta_argument_variants_keep_original_lexical_bindings_before_each_sample():
    cpp = transpile(_requested_source(
        "f(float x, int n) => x[n] - x\ng(y) => ta.sma(y, 3)\n",
        "f(g(close), 2) + f(g(open), 2)",
    ))
    _assert_sampled_sites(cpp, 2)
    closes = [row[-1] for row in _CHART_CLOSES]
    close_diffs = _difference(_sma(closes, 3))
    open_diffs = _difference(_sma(list(_HOURLY_OPENS), 3))
    expected = [left + right for left, right in zip(close_diffs, open_diffs)]
    # Each call has its own g-parameter TA variant. Five hourly samples give
    # four na outputs and then (-6) + (-4) = -10.
    _assert_completed(_run_requested(cpp)["chart15"], expected)
