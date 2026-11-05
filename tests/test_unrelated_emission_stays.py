"""Integer width fixes retain established C++ for other storage families."""

import pytest

from pineforge_codegen import transpile


HEADER = '//@version=6\nstrategy("Existing storage emission")\n'


@pytest.mark.parametrize("hint", ["", "bool "])
def test_bool_function_local_history_keeps_double_storage(hint):
    cpp = transpile(HEADER + f'''previousDirection() =>
    {hint}rising = close > open
    rising[1]
plot(previousDirection() ? 1 : 0)
''')
    assert "Series<double> rising;" in cpp
    assert "Series<bool> rising;" not in cpp


@pytest.mark.parametrize("hint", ["", "int "])
def test_count_function_local_history_keeps_double_storage(hint):
    cpp = transpile(HEADER + f'''previousCount() =>
    {hint}held = bar_index
    held[1]
plot(previousCount())
''')
    assert "Series<double> held;" in cpp
    assert "Series<int> held;" not in cpp


@pytest.mark.parametrize("body, expected", [
    pytest.param(
        '''type Reading
    int samples = 0
method advance(Reading self) =>
    self.samples := self.samples + 1
    self.samples
var reading = Reading.new()
plot(reading.advance())
''',
        "return _pf_udt_Reading.read(self).samples;",
        id="count-method-return",
    ),
    pytest.param(
        '''type Reading
    int samples = 0
count(Reading row) =>
    int held = row.samples
    held
reading = Reading.new(bar_index)
plot(count(reading))
''',
        "int held = _pf_udt_Reading.read(row).samples;",
        id="count-local-slot",
    ),
    pytest.param(
        '''type Reading
    int samples = 0
keep(int value) => value
reading = Reading.new(bar_index)
observed = keep(reading.samples)
plot(observed)
''',
        "observed = keep(_pf_udt_Reading.read(reading).samples);",
        id="count-parameter-slot",
    ),
])
def test_count_field_reads_keep_the_bare_expression(body, expected):
    assert expected in transpile(HEADER + body)


@pytest.mark.parametrize("body, expected", [
    pytest.param(
        '''age(int stamp) =>
    elapsed = (time - stamp) / 60000.0
    elapsed
var int saved = na
if close > open
    saved := time
plot(age(saved))
''',
        "double elapsed = ((double)((current_bar_.timestamp - stamp)) / (double)(60000.0));",
        id="existing-wide-scalar-parameter",
    ),
    pytest.param(
        '''var stamps = array.new_int()
stamps.push(time)
stamp = stamps.get(0)
elapsed = time - stamp
plot(elapsed)
''',
        "elapsed = (current_bar_.timestamp - stamp);",
        id="existing-wide-array-read",
    ),
    pytest.param(
        '''identity(int value) => value
int saved = time
saved := identity(time)
elapsed = time - saved
plot(elapsed)
''',
        "elapsed = (current_bar_.timestamp - saved);",
        id="existing-wide-global-slot",
    ),
    pytest.param(
        '''identity(int value) => value
var int saved = 3000000001
saved := identity(3000000001)
elapsed = time - saved
plot(elapsed)
''',
        "elapsed = (current_bar_.timestamp - saved);",
        id="existing-wide-literal-slot",
    ),
])
def test_existing_wide_slots_keep_plain_arithmetic(body, expected):
    assert expected in transpile(HEADER + body)
