"""Native timestamp values against direct Pine expressions on the same bars.

The supplied TradingView measurements establish the finite timestamp,
missingness and lifetime expectations. These rows also log the direct Pine
reference on every bar; no expected value comes from generated C++.
"""

import pytest

from tests.test_bool_string_inference import native_env, _messages, _run_native


HEADER = '//@version=6\nstrategy("Timestamp integer width", overlay=true)\n'
INPUT = 'start = input.time(timestamp("2024-01-01T00:00:00"), "Start")\n'
CHART_INPUT = 'input.time(timestamp("2025-01-06T14:15:00"), "d")\n'
BOOK = "type Book\n    array<int> stamps\n"


def _row(body, condition, *, id, first_bar_only=False):
    return pytest.param(body, condition, first_bar_only, id=id)


ROWS = [
    # A short difference can survive narrowing both endpoints. Check the
    # held epoch too, including locals returned in a tuple.
    # The first bar has no predecessor, so its reported spacing is 0.
    _row(
        '''y() => 0
spacing() =>
    y = time
    [bar_index == 0 ? 0 : y - y[1], y]
[observed, epoch] = spacing()
reference = bar_index == 0 ? 0 : time - time[1]
log.info("epoch {0} {1}", bar_index, epoch)
log.info("epoch reference {0} {1}", bar_index, time)
''', ("((na(observed) and na(reference)) or observed == reference)"
      " and epoch == time"),
        id="untyped-epoch-local-history-spacing"),
    _row(
        '''f() => 0
spacing() =>
    int f = time
    [bar_index == 0 ? 0 : f - f[1], f]
[observed, epoch] = spacing()
reference = bar_index == 0 ? 0 : time - time[1]
log.info("epoch {0} {1}", bar_index, epoch)
log.info("epoch reference {0} {1}", bar_index, time)
''', ("((na(observed) and na(reference)) or observed == reference)"
      " and epoch == time"),
        id="typed-epoch-local-history-spacing"),
    _row(
        '''fmt(x) => str.tostring(x)
spacing() =>
    int f = time
    [f - f[1], f]
unrelated() =>
    int f = bar_index
    f
[spacingValue, epoch] = spacing()
observed = bar_index == 0 ? 0 : spacingValue
reference = bar_index == 0 ? 0 : time - time[1]
count = unrelated()
log.info("epoch {0} {1}", bar_index, epoch)
log.info("epoch reference {0} {1}", bar_index, time)
''', ("((na(observed) and na(reference)) or observed == reference)"
      " and epoch == time and count == bar_index"),
        id="epoch-history-local-in-tuple"),
    # The default is the chart's second bar. Anchor the independent epoch to
    # its first timestamp; the overnight gap must not affect this reference.
    # Exercise a nonzero += contribution even on the first bar.
    _row(
        "int x = " + CHART_INPUT + '''x += 60000 * ((bar_index % 3) + 1)
observed = x
reference = time[bar_index] + 900000 + 60000 * ((bar_index % 3) + 1)
''', "observed == reference", first_bar_only=True,
        id="typed-time-input-global-add-assign"),
    _row(
        "x = " + CHART_INPUT + '''x := x + 60000
observed = x
reference = time[bar_index] + 900000 + 60000
''', "observed == reference", first_bar_only=True,
        id="untyped-time-input-global-reassign"),
    _row(
        "int x = " + CHART_INPUT + '''plot(x)
observed = x
reference = time[bar_index] + 900000
''', "observed == reference and ((time >= x) == (time >= reference))",
        id="typed-time-input-copy"),
    _row(
        "x = " + CHART_INPUT + '''y = x
observed = y
reference = time[bar_index] + 900000
''', "observed == reference", id="untyped-time-input-copy"),
    _row(
        "var int x = " + CHART_INPUT + '''observed = x
reference = time[bar_index] + 900000
''', "observed == reference", id="typed-var-time-input-copy"),
    _row(
        "var int x = " + CHART_INPUT + '''observed = time >= x ? 1 : 0
reference = time >= time[bar_index] + 900000 ? 1 : 0
''', "observed == reference", id="typed-var-time-input-window"),
    _row(
        "var x = " + CHART_INPUT + '''observed = time >= x ? 1 : 0
reference = time >= time[bar_index] + 900000 ? 1 : 0
''', "observed == reference", id="untyped-var-time-input-window"),
    _row(
        "var int x = " + CHART_INPUT + '''if bar_index > 0
    x += 60000
observed = x
reference = time[bar_index] + 900000 + 60000 * bar_index
''', "observed == reference", id="typed-var-time-input-global-update"),
    _row(
        "var x = " + CHART_INPUT + '''x := x + 60000
observed = x
reference = time[bar_index] + 900000 + 60000 * (bar_index + 1)
''', "observed == reference", id="untyped-var-time-input-global-reassign"),
    _row(
        "int a = " + CHART_INPUT + '''int b = a
b += 1
observed = b
reference = time[bar_index] + 900000 + 1
''', "observed == reference", id="typed-time-input-global-copy-add-assign"),
    _row(
        "x = " + CHART_INPUT + '''y = x
y += 1
observed = y
reference = time[bar_index] + 900000 + 1
''', "observed == reference", id="untyped-time-input-global-copy-add-assign"),
    _row(
        '''var int x = na
if bar_index == 1
    x := ''' + CHART_INPUT + '''if bar_index > 1
    x += 60000
observed = x
reference = bar_index == 0 ? na : float(time[bar_index]) + 900000.0 + 60000.0 * (bar_index - 1)
''', "(na(observed) and na(reference)) or observed == reference",
        id="nullable-time-input-global-late-update"),
    # Unchanged non-var inputs used only for comparison keep the full epoch.
    # The threshold falls inside the chart so both comparison results occur.
    _row(
        "startTime = " + CHART_INPUT + '''observed = time >= startTime ? 1 : 0
reference = time >= time[bar_index] + 900000 ? 1 : 0
''', "observed == reference", id="untyped-time-input-window-control"),
    _row(
        "int startTime = " + CHART_INPUT + '''observed = time >= startTime ? 1 : 0
reference = time >= time[bar_index] + 900000 ? 1 : 0
''', "observed == reference", id="typed-time-input-window-control"),
    # This feed has 60 bars; the offset exercises a product above int32.
    _row(
        '''var int g = 3000000001
g := g + 0
var int z = 0
if bar_index > 1
    int g = bar_index + 72
    z := g * 30000001
observed = z
reference = bar_index > 1 ? (float(bar_index) + 72.0) * 30000001.0 : 0.0
''', "observed == reference", id="block-shadow-integer-product-storage"),
    _row(
        '''h() =>
    var int n = 0
    n += 1
    n
g() =>
    n = time
    n
observed = request.security(syminfo.tickerid, "15", h())
reference = bar_index + 1
epoch = g()
log.info("epoch {0} {1}", bar_index, epoch)
log.info("epoch reference {0} {1}", bar_index, time)
''', "observed == reference and epoch == time",
        id="requested-counter-beside-same-named-epoch-local"),
    _row(
        '''start = input.time(timestamp("2020-01-01T00:00:00"), "Start")
stop = input.time(timestamp("2034-07-01T00:00:00"), "Stop")
inWindow(s, e) => time >= s and time <= e
observed = inWindow(start, stop) ? 1 : 0
reference = time >= start and time <= stop ? 1 : 0
if inWindow(start, stop) and bar_index % 20 == 0
    strategy.entry("L", strategy.long)
if bar_index % 20 == 10
    strategy.close("L")
''', "observed == reference", id="timestamp-window-parameters"),
    _row(
        '''identity(value) => value
observed = identity(1577836800000)
reference = 1577836800000.0
''', "observed == reference", id="untyped-literal-parameter"),
    _row(
        INPUT + '''identity(int value) => value
observed = identity(start)
reference = float(start)
''', "observed == reference", id="typed-time-input-parameter"),
    _row(
        INPUT + '''identity(simple int value) => value
observed = identity(start)
reference = float(start)
''', "observed == reference", id="simple-time-input-parameter"),
    _row(
        '''type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
var s = Stamp.new()
s.store(time)
observed = s.t
reference = time
''', "observed == reference", id="method-stores-timestamp-field"),
    _row(
        BOOK + '''var b = Book.new(array.new<int>())
b.stamps.unshift(time)
observed = b.stamps.get(0)
reference = time
''', "observed == reference", id="persistent-array-field-timestamp"),
    _row(
        BOOK + '''var b = Book.new(array.new<int>())
array.push(value = time, id = b.stamps)
observed = b.stamps.get(b.stamps.size() - 1)
reference = time
''', "observed == reference", id="array-field-keyword-push"),
    _row(
        INPUT + '''var stamps = array.from(start)
observed = stamps.get(0)
reference = float(start)
''', "observed == reference", id="array-from-time-input"),
    _row(
        BOOK + '''a = Book.new(array.from(time))
b = Book.new(stamps = array.new<int>(1, time))
c = Book.new(array.new_int(1, time))
observed = a.stamps.get(0) + b.stamps.get(0) + c.stamps.get(0)
reference = time * 3
''', "observed == reference", id="array-field-constructors"),
    _row(
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
        id="array-field-function-and-method-results"),
    _row(
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
        id="var-and-varip-array-field-values"),
    _row(
        BOOK + '''b = Book.new(array.from(time))
observed = bar_index == 0 ? na : (b[1]).stamps.get(0)
reference = time[1]
''', "(na(observed) and na(reference)) or observed == reference",
        id="array-field-object-history"),
    _row(
        BOOK + '''b = Book.new(array.from(time))
copied = b.stamps.copy()
sliced = b.stamps.slice(0, 1)
observed = copied.get(0) + sliced.pop()
reference = time * 2
''', "observed == reference", id="array-field-copy-slice-pop"),
    _row(
        BOOK + '''append(Book book) =>
    alias = book.stamps
    alias.push(time)
var b = Book.new(array.new<int>())
append(b)
observed = b.stamps.last()
reference = time
''', "observed == reference", id="array-field-local-alias-write"),
    _row(
        INPUT + '''a = array.from(start, start + 17, int(na))
observed = a.get(0) + a.get(1)
reference = float(start) * 2.0 + 17.0
''', "observed == reference and a.size() == 3 and na(a.get(2))",
        id="array-from-timestamp-and-missing-elements"),
    _row(
        INPUT + '''int counted = 0
for i = 0 to 2
    a = array.from(start + i)
    counted += a.get(0)
int items = 0
for j in array.from(0, 1, 2)
    a = array.from(start + j)
    items += a.get(0)
int repeated = 0
int k = 0
while k < 3
    a = array.from(start + k)
    repeated += a.get(0)
    k += 1
observed = counted
reference = 3.0 * float(start) + 3.0
log.info("counted {0} {1}", bar_index, counted)
log.info("items {0} {1}", bar_index, items)
log.info("repeated {0} {1}", bar_index, repeated)
''', "counted == reference and items == reference and repeated == reference",
        id="array-from-timestamp-in-three-loop-bodies"),
    _row(
        INPUT + '''a = array.from(start + bar_index)
previous = a[1]
observed = bar_index == 0 ? na : previous.get(0)
reference = bar_index == 0 ? na : float(start) + bar_index - 1.0
''', "(na(observed) and na(reference)) or observed == reference",
        id="array-from-timestamp-history"),
    _row(
        INPUT + '''make(int value) => array.from(value)
write(array<int> values, int value) =>
    values.set(0, value)
    values.get(0)
a = make(start)
returned = write(a, start + 29)
observed = a.get(0) + returned
reference = (float(start) + 29.0) * 2.0
''', "observed == reference", id="array-function-result-and-write"),
    _row(
        '''positiveIdentity(int value) => value
negativeIdentity(int value) => value
positive = positiveIdentity(4294967313)
negative = negativeIdentity(-4294967313)
observed = positive - negative
reference = 4294967313.0 - (-4294967313.0)
''', "observed == reference", id="signed-literal-parameters"),
    _row(
        INPUT + '''identity(int value) => value
increment(int value) => value + 1
int missing = na
missingIdentity = identity(missing)
missingSum = increment(missing)
observed = increment(start)
reference = float(start) + 1.0
''', "observed == reference and na(missingIdentity) and na(missingSum)",
        id="wide-parameter-missing-identity-and-sum"),
    _row(
        INPUT + '''hold(int value) =>
    var int saved = value
    saved
holdIntrabar(int value) =>
    varip int saved = value
    saved
observed = hold(start + bar_index) + holdIntrabar(start + bar_index)
var reference = (float(start) + bar_index) * 2.0
''', "observed == reference", id="persistent-scalar-call-parameters"),
    _row(
        '''type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
    self.t
var s = Stamp.new()
int missing = na
observed = s.store(missing)
reference = float(na)
''', "na(observed) and na(reference) and na(s.t)", id="method-stores-missing-field"),
    _row(
        '''method identity(int self) => self
observed = time.identity()
reference = time
''', "observed == reference", id="integer-method-receiver"),
    _row(
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
        id="timestamp-field-alias-history"),
    _row(
        '''type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
    self.t
var s = Stamp.new()
int value = bar_index % 2 == 0 ? time : na
observed = s.store(value)
reference = bar_index % 2 == 0 ? float(time) : na
''', ("((na(observed) and na(reference)) or observed == reference)"
      " and (bar_index % 2 == 0 ? s.t == time : na(s.t))"),
        id="method-stores-alternating-timestamp-and-missing"),
    _row(
        INPUT + '''a = array.from(start)
alias = a
a := array.from(start + 23)
observed = a.get(0) + alias.get(0)
reference = float(start) * 2.0 + 23.0
''', "observed == reference", id="array-rebinding-preserves-alias"),
    _row(
        INPUT + '''a = array.from(start, float(start) + 0.5)
observed = a.get(0) + a.get(1)
reference = float(start) * 2.0 + 0.5
''', "observed == reference", id="array-from-timestamp-and-float"),
    _row(
        '''type Stamp
    int t = 0
method store(Stamp self, int value) =>
    self.t := value
    self.t
var s = Stamp.new()
observed = s.store(bar_index % 2 == 0 ? time : na)
reference = bar_index % 2 == 0 ? float(time) : na
''', "(na(observed) and na(reference)) or observed == reference",
        id="method-nullable-timestamp-argument"),
    _row(
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
''', "observed == reference", id="array-field-from-scalar-field"),
    _row(
        BOOK + '''requestedStamp() =>
    b = Book.new(array.new<int>(1, time))
    b.stamps.get(0)
observed = request.security(syminfo.tickerid, timeframe.period, requestedStamp(), gaps = barmerge.gaps_off, lookahead = barmerge.lookahead_off)
reference = time
''', "observed == reference", id="array-field-on-requested-bars"),
    _row(
        INPUT + '''scale(int stamp) => stamp == start ? 30000001 : 0
var int g = 3000000001
g := g + 0
var int z = 3000000000
z := g * scale(start)
observed = z == 90000003030000001 ? 1 : 0
reference = 1
''', "observed == reference", id="wide-constant-product-with-timestamp-parameter"),
    _row(
        INPUT + '''type Direction
    int neutral
method choose(Direction self, int stamp) =>
    self.neutral := stamp == start ? 7 : 0
direction = Direction.new(0)
direction.choose(start)
var int signal = direction.neutral
previous = signal[1]
observed = signal
reference = 7
''', "observed == reference and (bar_index == 0 ? na(previous) : previous == 7)",
        id="field-series-var-initializes-after-method-call"),
    _row(
        INPUT + '''identity(int stamp) => stamp
historyValue(int value) => value[1]
int incoming = bar_index % 2 == 0 ? na : identity(start)
selected = historyValue(incoming)
y = selected + 1
observed = bar_index > 0 and bar_index % 2 == 0 ? y : 0
reference = bar_index > 0 and bar_index % 2 == 0 ? float(start) + 1.0 : 0.0
''', ("observed == reference and (bar_index > 0 and bar_index % 2 == 0"
      " ? selected == start : na(selected))"),
        id="nullable-parameter-history-after-timestamp-call"),
    _row(
        INPUT + '''var stamps = array.from(start)
var int s = 42
var st = array.new_int(1, 42)
s := stamps.get(0) == start ? 1000000001 : 42
s := (s * 48271) % 2147483647
v = (array.get(st, 0) * 48271) % 2147483647
observed = s + v
reference = (1000000001.0 * 48271.0) % 2147483647.0 + (42.0 * 48271.0) % 2147483647.0
''', "observed == reference", id="integer-products-after-timestamp-array-read"),
    _row(
        '''delta(int value) => value - 9007199254740992
observed = delta(9007199254741009)
reference = 9007199254741009.0 - 9007199254740992.0
''', "observed == reference", id="wide-parameter-binary64-subtraction"),
]


@pytest.mark.parametrize("body, condition, first_bar_only", ROWS)
def test_timestamp_int_width_matches_pine_reference(
        tmp_path, native_env, body, condition, first_bar_only):
    source = (HEADER + body + f"ok = ({condition}) ? 1 : 0\n"
              'log.info("observed {0} {1}", bar_index, observed)\n'
              'log.info("Pine reference {0} {1}", bar_index, reference)\n'
              'log.info("ok {0} {1}", bar_index, ok)\n')
    stderr = _run_native(tmp_path, native_env, source)
    count = native_env[2]
    observed = _messages(stderr, "observed", count)
    reference = _messages(stderr, "Pine reference", count)
    ok = _messages(stderr, "ok", count)
    # Later bars depend on a separate defect: changed input-backed globals
    # do not restart from their input each bar.
    bars = range(1 if first_bar_only else count)
    assert {bar: observed[bar] for bar in bars} == {
        bar: reference[bar] for bar in bars}
    assert {bar: ok[bar] for bar in bars} == {bar: "1" for bar in bars}
