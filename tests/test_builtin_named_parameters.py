"""Callable parameters shadow built-in variables without losing history."""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import FuncDef, MethodDef
from pineforge_codegen.errors import CompileError, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._e2e import transpile_json
from tests._pine_libraries import library_sources
from tests.test_builtin_named_locals_scope import (
    _LOCAL_NAMES, _normalize_local, _outer_binding_source,
)
from tests.test_builtin_named_parameters_reference import reference
from tests.test_runtime_var_initialization import _compile_and_run


_SMALLEST = '''//@version=6
strategy("t", overlay = true, initial_capital = 10000)
f(close) => close[1]
v = f(close)
d = v - close[1]
// @pf-trace v=v
// @pf-trace d=d
plot(v)
'''

_TRADES = '''//@version=6
strategy("function parameters named like built-ins", overlay = true, initial_capital = 10000)
f(high, low, close, n) =>
    float s = 0.0
    for i = 1 to n
        s += high[i] - close[i - 1]
    s / n
v = f(high, low, close, 5)
// @pf-trace v=v
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
'''

_TRADES_CONTROL = '''//@version=6
strategy("function parameters named like built-ins", overlay = true, initial_capital = 10000)
f(h, l, c, n) =>
    float s = 0.0
    for i = 1 to n
        s += h[i] - c[i - 1]
    s / n
v = f(high, low, close, 5)
// @pf-trace v=v
m = ta.sma(v, 20)
if ta.crossover(v, m)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(v, m)
    strategy.close("Long")
'''

_SWAPPED = '''//@version=6
strategy("history of another argument", overlay = true, initial_capital = 10000)
f(high) => high[1]
v = f(low)
w = low[1]
// @pf-trace v=v
// @pf-trace w=w
'''

_HEADER = '//@version=6\nstrategy("parameter history", overlay = true)\n'
_BAR_COUNTS = tuple(range(1, 65))

_DRIVER = r'''
#include <iomanip>
#include <iostream>
#include <vector>

template <typename T>
double observed_value(const T& value) {
    return is_na(value) ? na<double>() : static_cast<double>(value);
}
template <typename T>
double observed_value(const Series<T>& value) {
    return observed_value(value.current());
}

int main() {
    std::vector<Bar> bars;
    for (int i = 0; i < 64; ++i) {
        double close = 100.0 + i * i;
        bars.push_back(Bar{close - 5.0 + i % 3, close + 10.0 + i % 7,
                           close - 12.0 - i % 5, close, 1000.0 + i,
                           1700000000000LL + i * 60000LL});
    }
    for (int count : {@COUNTS@}) {
        GeneratedStrategy strategy;
        strategy.run(bars.data(), count, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << std::setprecision(17) << @MEMBERS@ << "\n";
    }
}
'''


def _values(source, members, *, libraries=None):
    printed = ' << " " << '.join(
        f"observed_value(strategy.{member})" for member in members
    )
    driver = _DRIVER.replace(
        "@COUNTS@", ", ".join(map(str, _BAR_COUNTS)),
    ).replace("@MEMBERS@", printed)
    output = _compile_and_run(transpile(source, libraries=libraries) + driver)
    rows = [tuple(map(float, line.split())) for line in output.splitlines()]
    assert len(rows) == len(_BAR_COUNTS), output
    assert all(len(row) == len(members) for row in rows), output
    return rows


def _assert_pairs(source, pairs, *, libraries=None):
    members = tuple(member for pair in pairs for member in pair)
    rows = _values(source, members, libraries=libraries)
    for count, row in zip(_BAR_COUNTS, rows):
        for index, pair in enumerate(pairs):
            actual, plain = row[2 * index:2 * index + 2]
            assert actual == pytest.approx(plain, nan_ok=True), (count, pair, row)
    # Every plain form must produce changing, finite values after warmup.
    for index in range(len(pairs)):
        finite = {row[2 * index + 1] for row in rows
                  if math.isfinite(row[2 * index + 1])}
        assert len(finite) > 1, rows
    return rows


def test_parameter_history_reads_the_previous_input_close():
    rows = _values(_SMALLEST, ("v", "d"))
    assert all(math.isnan(value) for value in rows[0]), rows
    for count, (value, difference) in zip(_BAR_COUNTS[1:], rows[1:]):
        # The driver supplies distinct closes, 100 + i*i, at every input bar.
        previous_close = 100.0 + (count - 2) ** 2
        assert value == previous_close, (count, value)
        assert difference == 0.0, (count, difference)


def test_loop_history_and_moving_average_match_renamed_parameters():
    actual = _values(_TRADES, ("v", "m"))
    control = _values(_TRADES_CONTROL, ("v", "m"))
    for count, row, expected in zip(_BAR_COUNTS, actual, control):
        assert row == pytest.approx(expected, nan_ok=True), (count, row, expected)
    assert math.isnan(control[0][0]), control
    assert all(math.isfinite(value) for value in control[-1]), control
    assert control[-1] != control[-2], control


def test_history_reads_the_series_passed_under_another_name():
    rows = _assert_pairs(_SWAPPED, (("v", "w"),))
    for count, (value, _plain) in zip(_BAR_COUNTS[1:], rows[1:]):
        previous_index = count - 2
        previous_low = 100.0 + previous_index ** 2 - 12.0 - previous_index % 5
        assert value == previous_low, (count, value)


_HISTORY_CASES = [
    pytest.param(
        "method lag1(float close) => close[1]\n"
        "v = open.lag1()\nw = open[1]\n"
        "// @pf-trace v=v\n// @pf-trace w=w\n",
        (("v", "w"),),
        id="method-receiver",
    ),
    pytest.param(
        "f(close) => close[1]\nv = f(close = open)\nw = open[1]\n"
        "// @pf-trace v=v\n// @pf-trace w=w\n",
        (("v", "w"),),
        id="keyword-argument",
    ),
    pytest.param(
        "g(close) => close[1]\nf(high) => g(high)\nv = f(low)\nw = low[1]\n"
        "// @pf-trace v=v\n// @pf-trace w=w\n",
        (("v", "w"),),
        id="forwarded-argument",
    ),
    pytest.param(
        "f(hl2) => hl2[2]\nv = f(close)\nw = close[2]\n"
        "// @pf-trace v=v\n// @pf-trace w=w\n",
        (("v", "w"),),
        id="derived-price-name",
    ),
    pytest.param(
        "f(close) => close[1]\nv = f(open)\nu = f(low)\n"
        "w1 = open[1]\nw2 = low[1]\n"
        "// @pf-trace v=v\n// @pf-trace u=u\n"
        "// @pf-trace w1=w1\n// @pf-trace w2=w2\n",
        (("v", "w1"), ("u", "w2")),
        id="independent-call-sites",
    ),
]


@pytest.mark.parametrize("body,pairs", _HISTORY_CASES)
def test_parameter_history_matches_plain_series(body, pairs):
    _assert_pairs(_HEADER + body, pairs)


_CONTROL_CASES = [
    pytest.param(
        "f(close) => ta.sma(close, 3)\nv = f(open)\nw = ta.sma(open, 3)\n",
        id="moving-average-argument",
    ),
    pytest.param(
        "f(close) => close * 2\nv = f(open)\nw = open * 2\n",
        id="history-free-argument",
    ),
    pytest.param(
        "f(time) => time[1]\nv = f(close)\nw = close[1]\n",
        id="time-named-argument",
    ),
    pytest.param(
        "f(x) =>\n    close = x * 2\n    close[1]\n"
        "v = f(open)\no2 = open * 2\nw = o2[1]\n",
        id="body-local-history",
    ),
]


@pytest.mark.parametrize("body", _CONTROL_CASES)
def test_existing_argument_and_local_behaviors_match_plain_series(body):
    source = _HEADER + body + "// @pf-trace v=v\n// @pf-trace w=w\n"
    _assert_pairs(source, (("v", "w"),))


def test_parameter_reassignment_diagnostic_keeps_the_authored_spelling():
    source = _HEADER + (
        "adjust(float close) =>\n    close := close + 1.0\n    close\n"
        "result = adjust(open)\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="parameters.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2059"
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.message == "Function parameter 'close' cannot be reassigned."
    assert diagnostic.location.file == "parameters.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (4, 5)
    assert "__pfhist" not in str(caught.value)


@pytest.mark.parametrize(
    "body,keyword",
    [
        pytest.param(
            "f({parameter}) => {parameter}[1]\nv = f(clsoe = open)\n",
            "clsoe", id="function",
        ),
        pytest.param(
            "method f(float self, float close) => self + close[1]\n"
            "v = open.f(clsoe = low)\n",
            "clsoe", id="method",
        ),
        pytest.param(
            "f({parameter}) => {parameter}[1]\nv = f(close__pfhist1 = open)\n",
            "close__pfhist1", id="private-name-is-not-a-keyword",
        ),
        pytest.param(
            "f({parameter}) => {parameter}[1]\n"
            "g(float src = f(close__pfhist1 = open)) => src\nv = g()\n",
            "close__pfhist1", id="unknown-keyword-in-default",
        ),
    ],
)
def test_unknown_keywords_keep_their_authored_diagnostic(
    body, keyword, reference, tmp_path,
):
    pine = tmp_path / "parameters.pine"
    if "{parameter}" in body:
        pine.write_text(_HEADER + body.format(parameter="close"), encoding="utf-8")
        actual = transpile_json(pine)
        assert actual["ok"] is False, actual
        assert actual["diagnostics"], actual
        assert "__pfhist" not in repr(actual), actual
        message = (
            "Cannot infer the per-callsite primitive type of history parameter "
            "'close' in callable 'f' variant cs0; declare its type explicitly."
        )
        for diagnostic in actual["diagnostics"]:
            assert diagnostic["code"] == "PF-E2016", diagnostic
            assert diagnostic["message"] == message
            assert (diagnostic["line"], diagnostic["col"]) == (3, 1)

        control_pine = tmp_path / "control.pine"
        control_pine.write_text(
            _HEADER + body.format(parameter="c"), encoding="utf-8",
        )
        control = transpile_json(control_pine)
        assert control["ok"] is False, control
        assert control["diagnostics"], control
        assert "__pfhist" not in repr(control), control
        expected_diagnostics = []
        for diagnostic in control["diagnostics"]:
            assert diagnostic["args"]["param"] == "c", diagnostic
            expected_diagnostics.append({
                **diagnostic,
                "message": diagnostic["message"].replace("'c'", "'close'"),
                "args": {**diagnostic["args"], "param": "close"},
            })
        assert actual["diagnostics"] == expected_diagnostics
        assert actual["error"] == control["error"].replace("'c'", "'close'")
        return

    pine.write_text(_HEADER + body, encoding="utf-8")
    expected = transpile_json(pine, root=reference)
    actual = transpile_json(pine)
    assert actual["ok"] == expected["ok"], (actual, expected)
    assert actual["diagnostics"] == expected["diagnostics"]
    if not actual["ok"]:
        (diagnostic,) = actual["diagnostics"]
        assert diagnostic["code"] == "PF-E2027"
        assert diagnostic["message"].endswith(
            f"unknown keyword argument '{keyword}'",
        )


def test_typed_parameter_keywords_defaults_and_ordinary_names_keep_their_slots():
    source = _HEADER + (
        "f(series float close, float src, simple int n = 2) => close[n] + src\n"
        "v = f(src = high, close = low, n = 3)\nw = low[3] + high\n"
        "u = f(low, open)\nz = low[2] + open\n"
    )
    _assert_pairs(source, (("v", "w"), ("u", "z")))


def test_overload_keywords_follow_the_selected_scalar_definition():
    source = _HEADER + (
        "f(float close, int n = 1) => close[n]\n"
        "f(int high, int n = 2) => high[n] + 1000\n"
        "v = f(close = open, n = 1)\nw = open[1]\n"
        "u = f(high = bar_index, n = 2)\nz = bar_index[2] + 1000\n"
    )
    _assert_pairs(source, (("v", "w"), ("u", "z")))


def test_qualifier_overloads_keep_their_existing_final_keyword_binding():
    source = _HEADER + (
        "f(simple float close) => close[1]\n"
        "f(series float close) => close[2]\n"
        "v = f(close = open)\nw = open[2]\n"
    )
    _assert_pairs(source, (("v", "w"),))


@pytest.mark.parametrize(
    "first,second",
    [
        pytest.param(
            "holder.shift(close = open)", "low.shift(close = high)",
            id="member-syntax",
        ),
        pytest.param(
            "shift(high = holder, close = open)",
            "shift(high = low, close = high)",
            id="function-syntax",
        ),
    ],
)
def test_receiver_type_selects_the_method_parameter_keywords(first, second):
    source = _HEADER + (
        "type Holder\n    float offset\n"
        "method shift(Holder high, float close) => high.offset + close[1]\n"
        "method shift(float high, float close) => high + close[2]\n"
        "holder = Holder.new(5.0)\n"
        f"v = {first}\nw = 5.0 + open[1]\n"
        f"u = {second}\nz = low + high[2]\n"
    )
    _assert_pairs(source, (("v", "w"), ("u", "z")))


@pytest.mark.parametrize("version", [5, 6])
def test_exported_library_parameter_keywords_remain_public(version):
    library = (
        f'//@version={version}\nlibrary("History")\n'
        "export previous(series float close, simple int n = 1) => close[n]\n"
        "export forwarded(series float high) => previous(close = high, n = 2)\n"
        "export method shifted(float close, float high) => close + high[1]\n"
    )
    source = _HEADER + (
        "import examples/History/1 as history\n"
        "v = history.forwarded(high = low)\nw = low[2]\n"
        "u = low.shifted(high = open)\nz = low + open[1]\n"
    )
    _assert_pairs(
        source, (("v", "w"), ("u", "z")),
        libraries={"examples/History/1": library},
    )


def test_exported_span_uses_the_callers_authored_keyword_names():
    source = _HEADER + (
        "import pftest/Signals/1 as signals\n"
        "v = signals.span(low = high, high = low)\nw = low - high\n"
    )
    _assert_pairs(
        source, (("v", "w"),),
        libraries=library_sources("pftest/Signals/1", "pftest/Base/1"),
    )


@pytest.mark.parametrize("name", _LOCAL_NAMES)
@pytest.mark.parametrize("method", [False, True], ids=["function", "method"])
def test_builtin_parameter_inventory_registers_scoped_history(name, method):
    definition = (
        f"method f(float {name}, float src, simple int n = 2)"
        if method else f"f(float {name}, float src, simple int n = 2)"
    )
    source = _HEADER + definition + f" => {name}[n] + src\n"
    source += "v = low.f(open)\n" if method else "v = f(low, open)\n"
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    context = Analyzer(program).analyze()
    (callable_node,) = [
        node for node in program.body if isinstance(node, (FuncDef, MethodDef))
    ]
    private, *ordinary = callable_node.params
    assert private.startswith(name + "__pfhist"), callable_node.params
    assert ordinary == ["src", "n"]
    owner = "float.f" if method else "f"
    assert context.func_series_vars[owner] == {private}


def test_requested_helper_parameter_matches_ordinary_parameter_cpp():
    source = _outer_binding_source(parameter=True)
    control = transpile(source.replace("second", "aa"))
    cpp = transpile(source)
    assert "second__pfhist" in cpp
    assert _normalize_local(cpp, "second", control) == control
