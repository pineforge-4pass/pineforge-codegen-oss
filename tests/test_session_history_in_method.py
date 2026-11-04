"""Native order-quantity regressions for GAP-SESSION-METHOD.

The first source is the exact accepted TradingView probe. Sparse method calls
follow the owner's explicit default: the measured plain-function call-site
rule, pending TradingView's odd-bar method capture (QUESTION FOR TV).
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp, run_emitted_tu
from tests._e2e import reference_codegen, transpile_json
from tests.test_e2e_session_history import function_rule, sources


ACCEPTED_METHOD_PROBE = '''//@version=6
strategy("session history read inside a method", overlay = true, pyramiding = 0, default_qty_type = strategy.fixed, default_qty_value = 1, initial_capital = 1000000000, process_orders_on_close = false)
// QUESTION: does TradingView accept a session.* history read (session.ismarket[1], session.isfirstbar[1]) inside a method body, where no qualifier rule applies (the value only feeds an order quantity)?
// If it compiles, the entry quantity per trade shows the value: code = (ismarket[1] ? 1 : 0) + (isfirstbar[1] ? 2 : 0), qty = 1 + code.
type Foo
    float v = 1
method m(Foo self) =>
    float code = (session.ismarket[1] ? 1.0 : 0.0) + (session.isfirstbar[1] ? 2.0 : 0.0)
    self.v + code
foo = Foo.new()
float q = foo.m()
if bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = q)
if bar_index % 7 == 5
    strategy.close("L")
'''


FIRSTBAR_ENTRY_PROBE = '''//@version=6
strategy("previous first session bar sets quantity", pyramiding = 0, default_qty_type = strategy.fixed, initial_capital = 1000000000, process_orders_on_close = false)
type Window
    float base = 1.0
method quantity(Window self) =>
    self.base + (session.ismarket[1] ? 1.0 : 0.0) + (session.isfirstbar[1] ? 2.0 : 0.0)
window = Window.new()
float shares = window.quantity()
if bar_index == 1
    strategy.entry("L", strategy.long, qty = shares)
if bar_index == 3
    strategy.close("L")
'''


# A real engine run, including deferred fills. The driver only reports sizes
# that the broker booked; no generated code is patched or substituted.
DRIVER = r'''
#include <iostream>
#include <vector>

struct SessionStrategy : GeneratedStrategy {
    SessionStrategy() {
        syminfo_.session = "0000-0000";
        syminfo_.timezone = "Etc/UTC";
    }
};

int main() {
    std::vector<Bar> bars;
    for (int i = 0; i < BAR_COUNT; ++i) {
        bars.push_back(Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                           START_MS + i * 900000LL});
    }
    for (int count = FIRST_FILL_COUNT; count <= BAR_COUNT; count += FILL_STRIDE) {
        SessionStrategy strategy;
        strategy.run(bars.data(), count, "15", "15");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << '\n';
            return 1;
        }
        std::cout << "entry " << strategy.live_position_size() << '\n';
    }
    SessionStrategy complete;
    complete.run(bars.data(), BAR_COUNT, "15", "15");
    if (!complete.last_error().empty()) {
        std::cerr << complete.last_error() << '\n';
        return 2;
    }
    std::cout << "closed " << complete.trade_count() << '\n';
    std::cout << "remaining " << complete.live_position_size() << '\n';
}
'''


def _booked_quantities(source: str, *, count: int, start_ms: int,
                       first_fill_count: int, stride: int) -> tuple[list[float], int, float]:
    # Translate before either helper's native-prerequisite skip: the old
    # codegen must fail at PF-E5048 even on a host without the engine.
    cpp = transpile(source)
    compile_cpp(cpp, label="session history in a method")
    driver = (
        f"#define BAR_COUNT {count}\n"
        f"#define START_MS {start_ms}LL\n"
        f"#define FIRST_FILL_COUNT {first_fill_count}\n"
        f"#define FILL_STRIDE {stride}\n"
        + DRIVER
    )
    rows = [line.split() for line in run_emitted_tu(
        cpp, driver, opt="-O2", label="session history method entry quantities",
    ).splitlines()]
    assert all(len(row) == 2 for row in rows), rows
    assert [row[0] for row in rows[-2:]] == ["closed", "remaining"], rows
    assert all(row[0] == "entry" for row in rows[:-2]), rows
    return [float(row[1]) for row in rows[:-2]], int(rows[-2][1]), float(rows[-1][1])


def test_accepted_method_probe_books_27_entries_of_two() -> None:
    # Flat prices suffice: flags depend on timestamps/session. 192 15m bars
    # from 2025-04-01 08:00 UTC reproduce the tape's entry schedule. Neither
    # midnight's first-bar flag lands one bar before an entry decision.
    quantities, closed, remaining = _booked_quantities(
        ACCEPTED_METHOD_PROBE, count=192, start_ms=1743494400000,
        first_fill_count=5, stride=7,
    )
    assert len(quantities) == closed == 27
    assert quantities == [2.0] * 27
    assert remaining == 0.0


def test_method_firstbar_history_books_an_entry_of_four() -> None:
    # Bar zero opens the UTC session; the decision on bar one reads its
    # ismarket=True and isfirstbar=True. The actual fill on bar two must be 4.
    quantities, closed, remaining = _booked_quantities(
        FIRSTBAR_ENTRY_PROBE, count=6, start_ms=1743465600000,
        first_fill_count=3, stride=7,
    )
    assert quantities == [4.0]
    assert closed == 1
    assert remaining == 0.0


SPARSE_METHOD = '''type SessionClock
    float enabled = 1.0
method previous(SessionClock self) =>
    session.ispostmarket[1] and self.enabled > 0
clock = SessionClock.new()
'''


def _native_session_rows(body: str) -> tuple[list[dict[str, bool]], dict[str, list[bool]]]:
    """Observe authored values after each real engine callback, unpatched TU.

    Two extended-hours days, 04:00-19:45 New York, include both a regular-close
    transition and an overnight gap where odd-call and chart histories differ.
    The oracle uses the plain-function tape helpers, with flags from this run.
    """
    source = '''//@version=6
strategy("resolved method call histories", initial_capital=1000000000)
cPost = session.ispostmarket
cPre = session.ispremarket
cMarket = session.ismarket
''' + body + '''
if a or b
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(source)  # The base refuses before any native skip.
    compile_cpp(cpp, label="sparse session history in methods")
    driver = r'''
#include <iostream>
#include <vector>
struct ObservedStrategy : GeneratedStrategy {
    ObservedStrategy() {
        syminfo_.session = "0930-1600";
        syminfo_.timezone = "America/New_York";
    }
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        std::cout << cPost << ' ' << cPre << ' ' << cMarket << ' '
                  << a << ' ' << b << '\n';
    }
};
int main() {
    std::vector<Bar> bars;
    for (int day = 0; day < 2; ++day) {
        for (int quarter = 0; quarter < 64; ++quarter) {
            bars.push_back(Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                1743494400000LL + day * 86400000LL + quarter * 900000LL});
        }
    }
    ObservedStrategy strategy;
    strategy.run(bars.data(), static_cast<int>(bars.size()), "15", "15");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << '\n';
        return 1;
    }
}
'''
    rows = [line.split() for line in run_emitted_tu(
        cpp, driver, opt="-O2", label="sparse method call-site session history",
    ).splitlines()]
    assert len(rows) == 128 and all(len(row) == 5 for row in rows), rows
    assert all(value in {"0", "1"} for row in rows for value in row), rows
    values = [[value == "1" for value in row] for row in rows]
    current = [dict(zip(("ispostmarket", "ispremarket", "ismarket"), row[:3]))
               for row in values]
    assert any(row["ispostmarket"] for row in current)
    assert any(row["ismarket"] for row in current)
    return current, {name: [row[index] for row in values]
                     for index, name in enumerate(("a", "b"), start=3)}


def _assert_odd_calls(current: list[dict[str, bool]], actual: list[bool]) -> None:
    expected = function_rule(current)["b"]
    chart_rule = function_rule(current, by_calls=False)["b"]
    witnesses = [i for i in range(len(current)) if expected[i] != chart_rule[i]]
    assert witnesses and all(i % 2 == 1 for i in witnesses), witnesses
    assert actual == expected
    assert [actual[i] for i in witnesses] != [chart_rule[i] for i in witnesses]
    # The existing tape's source-index helper independently pins [1] to the
    # previous odd call, including false before that call site first runs.
    assert actual == [False if index is None else current[index]["ispostmarket"]
                      for index in sources(len(current))["u"]]


def test_method_called_only_on_odd_bars_reads_its_previous_call() -> None:
    current, got = _native_session_rows(SPARSE_METHOD + '''a = false
b = bar_index % 2 == 1 ? clock.previous() : false
''')
    _assert_odd_calls(current, got["b"])


def test_method_second_call_site_keeps_its_own_previous_call() -> None:
    current, got = _native_session_rows(SPARSE_METHOD + '''a = clock.previous()
b = bar_index % 2 == 1 ? clock.previous() : false
''')
    assert got["a"] == function_rule(current)["a"]
    _assert_odd_calls(current, got["b"])
    witnesses = [i for i in range(1, len(current), 2) if got["a"][i] != got["b"][i]]
    assert witnesses  # Shared state or chart history makes these equal.


def test_same_named_methods_on_distinct_types_keep_resolved_owners() -> None:
    current, got = _native_session_rows(SPARSE_METHOD + '''type RegularClock
    float enabled = 1.0
method previous(RegularClock self) =>
    session.ismarket[1] and self.enabled > 0
regular = RegularClock.new()
a = clock.previous()
b = bar_index % 2 == 1 ? regular.previous() : false
''')
    assert got["a"] == function_rule(current)["a"]
    expected = [False if index is None else current[index]["ismarket"]
                for index in sources(len(current))["u"]]
    assert got["b"] == expected
    assert got["b"] != function_rule(current)["b"]


@pytest.mark.parametrize("body", [
    '''post() => session.ispostmarket[1]
type Relay
    float enabled = 1.0
method previous(Relay self) => post() and self.enabled > 0
relay = Relay.new()
wrap(Relay clock) => clock.previous()
a = wrap(relay)
b = bar_index % 2 == 1 ? wrap(relay) : false
''',
    '''type Relay
    float enabled = 1.0
method previous(Relay self, bool allow = true) => session.ispostmarket[1] and self.enabled > 0 and allow
method forwarded(Relay self) => self.previous(allow=true)
relay = Relay.new()
a = relay.forwarded()
b = bar_index % 2 == 1 ? relay.forwarded() : false
''',
    SPARSE_METHOD + '''makeClock() => SessionClock.new()
a = makeClock().previous()
b = bar_index % 2 == 1 ? makeClock().previous() : false
''',
    SPARSE_METHOD + '''clocks = array.from(clock)
a = false
for activeClock in clocks
    a := activeClock.previous()
b = bar_index % 2 == 1 ? clock.previous() : false
''',
    '''method previous(float receiver) => session.ispostmarket[1] and receiver > 0
a = close.previous()
b = bar_index % 2 == 1 ? (close + open).previous() : false
''',
], ids=["function-through-method-and-wrapper", "method-through-method-with-keyword",
        "typed-return-receiver", "typed-loop-receiver", "scalar-expression-receiver"])
def test_resolved_method_call_paths_keep_independent_histories(body: str) -> None:
    current, got = _native_session_rows(body)
    assert got["a"] == function_rule(current)["a"]
    _assert_odd_calls(current, got["b"])


@pytest.mark.parametrize("payload", ["previous()", "wrapped()"])
def test_requested_clock_helper_keeps_pf_e5048(payload: str) -> None:
    source = f'''//@version=6
strategy("requested helper session history")
previous() => session.isfirstbar[1]
wrapped() => previous()
onChart = previous()
onRequested = request.security(syminfo.tickerid, "60", {payload})
if onRequested or onChart
    strategy.entry("L", strategy.long)
'''
    with pytest.raises(CompileError) as caught:
        transpile(source)
    errors = [diagnostic for diagnostic in caught.value.diagnostics
              if diagnostic.code == "PF-E5048"]
    assert errors, caught.value.diagnostics
    assert all("which a request.security expression evaluates on the requested clock"
               in diagnostic.message for diagnostic in errors)


@pytest.mark.parametrize("body, reason", [
    ('''type Clock
    float enabled = 1.0
method previous(Clock self) => session.ispostmarket[1] and self.enabled > 0
read(untyped) => untyped.previous()
clock = Clock.new()
x = read(clock)
''', "a method, whose calls PineForge cannot always tell apart"),
    ('''type PostClock
    float enabled = 1.0
type MarketClock
    float enabled = 1.0
method previous(PostClock self) => session.ispostmarket[1] and self.enabled > 0
method previous(MarketClock self) => session.ismarket[1] and self.enabled > 0
read(untyped) => untyped.previous()
postClock = PostClock.new()
marketClock = MarketClock.new()
x = read(postClock) or read(marketClock)
''', "a method, whose calls PineForge cannot always tell apart"),
    ('''type Clock
    float enabled = 1.0
method previous(Clock self) => session.ispostmarket[1] and self.enabled > 0
clock = Clock.new()
x = clock.previous()
// @pf-trace unbound=clock.previous()
''', "a method, whose calls PineForge cannot always tell apart"),
    ('''type Clock
    float enabled = 1.0
method previous(Clock self) => session.ispostmarket[1] and self.enabled > 0
clock = Clock.new()
onChart = clock.previous()
x = request.security(syminfo.tickerid, "60", clock.previous()) or onChart
''', "a method, whose calls PineForge cannot always tell apart"),
    ('''previous() => session.ispostmarket[1]
type Clock
    bool value = previous()
x = Clock.new().value
''', "which a UDT field default calls once for every new()"),
], ids=["unresolved-receiver", "unresolved-same-name-owners", "unbound-trace-call",
        "requested-method-and-chart", "udt-default"])
def test_unsafe_method_and_default_paths_keep_pf_e5048(body: str, reason: str) -> None:
    with pytest.raises(CompileError) as caught:
        transpile('//@version=6\nstrategy("unsafe history")\n' + body
                  + 'if x\n    strategy.entry("L", strategy.long)\n')
    errors = [d for d in caught.value.diagnostics if d.code == "PF-E5048"]
    assert errors, caught.value.diagnostics
    assert all(reason in d.message for d in errors), errors


@pytest.mark.parametrize("body", [
    '''type Counter
    float value = 1.0
method positive(Counter self) => self.value > 0
counter = Counter.new()
x = counter.positive()
''',
    '''type Counter
    float value = 1.0
method positive(Counter self) => self.value > 0
post() => session.ispostmarket[1]
counter = Counter.new()
x = counter.positive() and post()
y = bar_index % 2 == 1 ? post() : false
''',
    '''type Counter
    float value = 1.0
method positive(Counter self) =>
    c = color.from_gradient(session.ismarket[1] ? 1.0 : 0.0, 0, 1, color.red, color.green)
    self.value > 0
counter = Counter.new()
x = counter.positive()
y = bar_index % 2 == 1 ? counter.positive() : false
// @pf-trace dropped=counter.positive()
''',
    '''method requested(float receiver) =>
    receiver > 0 and request.security(syminfo.tickerid, "60", session.ismarket[1])
x = close.requested()
''',
], ids=["no-history", "plain-function-history", "dropped-method-read", "direct-requested-read"])
def test_scripts_without_emitted_method_session_history_keep_exact_cpp(body, tmp_path) -> None:
    base = "1e2e72e37047ea22a4fdfc189584668e9567fdee"
    reference = reference_codegen(base)
    assert reference is not None, f"required C++ identity reference {base} unavailable"
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("method noninterference")\n' + body
                    + 'if x\n    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    now, before = transpile_json(pine), transpile_json(pine, reference)
    assert now["ok"] and before["ok"], (now["diagnostics"], before["diagnostics"])
    assert now["cpp"] == before["cpp"]
    compile_cpp(now["cpp"], label="unchanged method emission")
