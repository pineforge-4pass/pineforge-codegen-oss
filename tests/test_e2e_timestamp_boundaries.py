"""Date guards and stored epochs agree with their direct-value controls."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok,
    skip_unless_e2e_env, trade_count,
)


WINDOW = '''//@version=6
strategy("timestamp date window")
start = input.time(timestamp("2020-01-01T00:00:00"), "Start")
stop = input.time(timestamp("2034-07-01T00:00:00"), "Stop")
inWindow(s, e) => time >= s and time <= e
if inWindow(start, stop) and bar_index % 20 == 0
    strategy.entry("L", strategy.long)
if bar_index % 20 == 10
    strategy.close("L")
'''
WINDOW_CONTROL = WINDOW.replace("if inWindow(start, stop)", "if time >= start and time <= stop")

METHOD = '''//@version=6
strategy("timestamp method")
type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
var s = Stamp.new()
got = s.store(time)
ok = s.t == time and got == time ? 1 : 0
// @pf-trace ok=ok
plot(ok)
'''
METHOD_CONTROL = METHOD.replace("int value", "value")

ARRAY_FROM = '''//@version=6
strategy("timestamp array")
start = input.time(timestamp("2024-01-01T00:00:00"), "Start")
var stamps = array.from(start)
ok = stamps.get(0) == start ? 1 : 0
// @pf-trace ok=ok
plot(ok)
'''
ARRAY_FROM_CONTROL = ARRAY_FROM.replace("array.from(start)", "array.from(float(start))")
ARRAY_FIELD = '''//@version=6
strategy("timestamp array field")
type Book
    array<int> stamps
var b = Book.new(array.new<int>())
b.stamps.unshift(time)
ok = b.stamps.get(0) == time ? 1 : 0
// @pf-trace ok=ok
plot(ok)
'''
ARRAY_FIELD_CONTROL = ARRAY_FIELD.replace("array<int>", "array<float>").replace(
    "array.new<int>()", "array.new<float>()")


def test_date_window_and_inline_control_compile():
    # The two input epochs feed int64 bounds; a bool comparison does not change the return family.
    cpp = transpile(WINDOW)
    compile_cpp(cpp)
    compile_cpp(transpile(WINDOW_CONTROL))


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("timestamp_boundaries")
    feed = chart_feed_head(engine, base, 81)
    # Overridden time inputs must propagate the runtime value, not just the source default.
    window_overrides = {"Start": 946684800000, "Stop": 2208988800000}
    array_overrides = {"Start": 1893456000000}
    return execute_all(engine, feed, base, {
        "window": Build(WINDOW, overrides=window_overrides),
        "window_control": Build(WINDOW_CONTROL, overrides=window_overrides),
        "method": Build(METHOD, trace=True),
        "method_control": Build(METHOD_CONTROL, trace=True),
        "array_from": Build(ARRAY_FROM, overrides=array_overrides, trace=True),
        "array_from_control": Build(ARRAY_FROM_CONTROL, overrides=array_overrides, trace=True),
        "array_field": Build(ARRAY_FIELD, trace=True),
        "array_field_control": Build(ARRAY_FIELD_CONTROL, trace=True),
    })


def test_date_guard_matches_inline_comparison(outcomes):
    # The corpus bars fall inside both wide date windows; each modulo-20 entry closes at +10.
    subject = ok(outcomes, "window")
    control = ok(outcomes, "window_control")
    for blob in control.trades.values():
        assert trade_count(blob) > 0
    assert_same_runs(subject, control)


@pytest.mark.parametrize("name", ["method", "array_from", "array_field"])
def test_epoch_storage_matches_control_on_every_bar(outcomes, name):
    # A stored/returned timestamp equals its source exactly, so every traced ok is 1.
    subject = ok(outcomes, name)
    control = ok(outcomes, name + "_control")
    assert_same_runs(subject, control)
    for records in subject.traces.values():
        values = [record["value"] for record in records if record["name"] == "ok"]
        assert values
        assert all(value == 1 for value in values)


def test_integer_array_field_runs_with_full_timestamps(outcomes):
    subject = ok(outcomes, "array_field")
    values = [r["value"] for r in subject.traces["default"] if r["name"] == "ok"]
    assert values
    assert all(value == 1 for value in values)
