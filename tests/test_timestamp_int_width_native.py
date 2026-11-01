"""Issue 285: native values against direct Pine expressions on the same bars.

The supplied C1--C4 TradingView measurements establish the finite timestamp,
missingness and lifetime expectations. These rows also log the direct Pine
reference on every bar; no expected value comes from generated C++.
"""

import pytest

from tests.test_bool_string_inference import native_env, _messages, _run_native


HEADER = '//@version=6\nstrategy("Timestamp integer width", overlay=true)\n'
INPUT = 'start = input.time(timestamp("2024-01-01T00:00:00"), "Start")\n'
BOOK = "type Book\n    array<int> stamps\n"


ROWS = [
    pytest.param(
        '''start = input.time(timestamp("2020-01-01T00:00:00"), "Start")
stop = input.time(timestamp("2034-07-01T00:00:00"), "Stop")
inWindow(s, e) => time >= s and time <= e
observed = inWindow(start, stop) ? 1 : 0
reference = time >= start and time <= stop ? 1 : 0
if inWindow(start, stop) and bar_index % 20 == 0
    strategy.entry("L", strategy.long)
if bar_index % 20 == 10
    strategy.close("L")
''', "observed == reference", id="case1-window"),
    pytest.param(
        '''identity(value) => value
observed = identity(1577836800000)
reference = 1577836800000
''', "observed == reference", id="case1-literal"),
    pytest.param(
        INPUT + '''identity(int value) => value
observed = identity(start)
reference = start
''', "observed == reference", id="case1-int-input"),
    pytest.param(
        INPUT + '''identity(simple int value) => value
observed = identity(start)
reference = start
''', "observed == reference", id="case1-simple-input"),
    pytest.param(
        '''type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
var s = Stamp.new()
s.store(time)
observed = s.t
reference = time
''', "observed == reference", id="case2-store"),
    pytest.param(
        BOOK + '''var b = Book.new(array.new<int>())
b.stamps.unshift(time)
observed = b.stamps.get(0)
reference = time
''', "observed == reference", id="case3-persistent"),
    pytest.param(
        INPUT + '''var stamps = array.from(start)
observed = stamps.get(0)
reference = start
''', "observed == reference", id="case4-array-from"),
    pytest.param(
        BOOK + '''a = Book.new(array.from(time))
b = Book.new(stamps = array.new<int>(1, time))
c = Book.new(array.new_int(1, time))
observed = a.stamps.get(0) + b.stamps.get(0) + c.stamps.get(0)
reference = time * 3
''', "observed == reference", id="C3-03-constructors"),
    pytest.param(
        BOOK + '''store(array<int> values, int stamp) =>
    values.unshift(stamp)
    [values.get(0), values.size()]
method latest(Book self) =>
    self.stamps.get(0)
var b = Book.new(array.new<int>())
[throughFunction, count] = store(values = b.stamps, stamp = time)
throughMethod = b.latest()
observed = throughFunction + throughMethod
reference = time * 2
''', "observed == reference and count == bar_index + 1",
        id="C3-07-call-boundaries"),
    pytest.param(
        BOOK + '''var persistent = Book.new(array.new<int>())
varip intrabar = Book.new(array.new<int>())
ordinary = Book.new(array.new<int>())
persistent.stamps.push(time)
intrabar.stamps.push(time)
ordinary.stamps.push(time)
observed = persistent.stamps.last() + intrabar.stamps.last() + ordinary.stamps.get(0)
reference = time * 3
''', ("observed == reference and persistent.stamps.size() == bar_index + 1"
      " and intrabar.stamps.size() == bar_index + 1 and ordinary.stamps.size() == 1"),
        id="C3-11-var-varip-values"),
    pytest.param(
        BOOK + '''b = Book.new(array.from(time))
observed = bar_index == 0 ? int(na) : (b[1]).stamps.get(0)
reference = time[1]
''', "(na(observed) and na(reference)) or observed == reference",
        id="C3-09-object-history"),
    pytest.param(
        BOOK + '''b = Book.new(array.from(time))
copied = b.stamps.copy()
sliced = b.stamps.slice(0, 1)
observed = copied.get(0) + sliced.pop()
reference = time * 2
''', "observed == reference", id="C3-18-copy-slice-pop"),
    pytest.param(
        BOOK + '''append(Book book) =>
    alias = book.stamps
    alias.push(time)
var b = Book.new(array.new<int>())
append(b)
observed = b.stamps.last()
reference = time
''', "observed == reference", id="C3-25-local-alias"),
    pytest.param(
        INPUT + '''a = array.from(start, start + 17, int(na))
observed = a.get(0) + a.get(1)
reference = start * 2 + 17
''', "observed == reference and a.size() == 3 and na(a.get(2))",
        id="C4-03-elements-na"),
    pytest.param(
        INPUT + '''a = array.from(start + bar_index)
previous = a[1]
observed = bar_index == 0 ? int(na) : previous.get(0)
reference = bar_index == 0 ? int(na) : start + bar_index - 1
''', "(na(observed) and na(reference)) or observed == reference",
        id="C4-07-array-history"),
    pytest.param(
        INPUT + '''make(int value) => array.from(value)
write(array<int> values, int value) =>
    values.set(0, value)
    values.get(0)
a = make(start)
returned = write(a, start + 29)
observed = a.get(0) + returned
reference = (start + 29) * 2
''', "observed == reference", id="C4-09-function-array"),
    pytest.param(
        INPUT + '''pair(int value) => [array.from(value), value]
[a, scalar] = pair(start)
observed = a.get(0) + scalar
reference = start * 2
''', "observed == reference", id="C4-11-tuple-array"),
    pytest.param(
        INPUT + '''heldRead(int value) =>
    var a = array.from(value)
    a.get(0)
int x = na
int y = na
if bar_index >= 3
    var a = array.from(start + bar_index)
    x := a.get(0)
    y := heldRead(start + bar_index)
observed = x + y
reference = bar_index < 3 ? int(na) : (start + 3) * 2
''', "(na(observed) and na(reference)) or observed == reference",
        id="C4-18-delayed-var"),
    pytest.param(
        '''identity(int value) => value
positive = identity(4294967313)
negative = identity(-4294967313)
observed = positive - negative
reference = 8589934626
''', "observed == reference", id="C1-05-signed-literals"),
    pytest.param(
        INPUT + '''identity(int value) => value
increment(int value) => value + 1
int missing = na
missingIdentity = identity(missing)
missingSum = increment(missing)
observed = increment(start)
reference = start + 1
''', "observed == reference and na(missingIdentity) and na(missingSum)",
        id="C1-10-na-parameter"),
    pytest.param(
        INPUT + '''hold(int value) =>
    var int saved = value
    saved
holdIntrabar(int value) =>
    varip int saved = value
    saved
observed = hold(start + bar_index) + holdIntrabar(start + bar_index)
var reference = (start + bar_index) * 2
''', "observed == reference", id="C1-14-persistent-calls"),
    pytest.param(
        '''type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
    self.t
var s = Stamp.new()
int missing = na
observed = s.store(missing)
reference = int(na)
''', "na(observed) and na(reference) and na(s.t)", id="C2-07-na"),
    pytest.param(
        '''method identity(int self) => self
observed = time.identity()
reference = time
''', "observed == reference", id="C2-22-int-receiver"),
    pytest.param(
        '''type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
var s = Stamp.new()
s.store(time)
int sampled = s.t
observed = sampled[1]
reference = time[1]
''', "(na(observed) and na(reference)) or observed == reference",
        id="C2-10-field-alias-history"),
    pytest.param(
        '''type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
    self.t
var s = Stamp.new()
int value = bar_index % 2 == 0 ? time : na
observed = s.store(value)
reference = bar_index % 2 == 0 ? time : int(na)
''', ("((na(observed) and na(reference)) or observed == reference)"
      " and (bar_index % 2 == 0 ? s.t == time : na(s.t))"),
        id="C2-23-mixed-na-time"),
    pytest.param(
        INPUT + '''a = array.from(start)
alias = a
a := array.from(start + 23)
observed = a.get(0) + alias.get(0)
reference = start * 2 + 23
''', "observed == reference", id="C4-08-rebind-alias"),
    pytest.param(
        INPUT + '''a = array.from(start, float(start) + 0.5)
observed = a.get(0) + a.get(1)
reference = float(start) * 2.0 + 0.5
''', "observed == reference", id="C4-R2-mixed-numeric"),
    pytest.param(
        '''type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
    self.t
var s = Stamp.new()
observed = s.store(bar_index % 2 == 0 ? time : na)
reference = bar_index % 2 == 0 ? time : int(na)
''', "(na(observed) and na(reference)) or observed == reference",
        id="C2-na-selection-argument"),
    pytest.param(
        '''type Stamp
    int value = 0
type Book
    array<int> values
method store(Stamp self, int value) =>
    self.value := value
append(Book book, Stamp stamp) =>
    book.values.push(stamp.value)
var stamp = Stamp.new()
var book = Book.new(array.new<int>())
stamp.store(time)
append(book, stamp)
observed = book.values.last()
reference = time
''', "observed == reference", id="C3-scalar-field-source"),
    pytest.param(
        BOOK + '''requestedStamp() =>
    b = Book.new(array.new<int>(1, time))
    b.stamps.get(0)
observed = request.security(syminfo.tickerid, timeframe.period, requestedStamp(), gaps = barmerge.gaps_off, lookahead = barmerge.lookahead_off)
reference = time
''', "observed == reference", id="C3-16-security"),
]


@pytest.mark.parametrize("body, condition", ROWS)
def test_timestamp_int_width_matches_pine_reference(
        tmp_path, native_env, body, condition):
    source = (HEADER + body + f"ok = ({condition}) ? 1 : 0\n"
              'log.info("observed {0} {1}", bar_index, observed)\n'
              'log.info("Pine reference {0} {1}", bar_index, reference)\n'
              'log.info("ok {0} {1}", bar_index, ok)\n')
    stderr = _run_native(tmp_path, native_env, source)
    count = native_env[2]
    assert _messages(stderr, "observed", count) == _messages(
        stderr, "Pine reference", count)
    assert _messages(stderr, "ok", count) == {bar: "1" for bar in range(count)}
