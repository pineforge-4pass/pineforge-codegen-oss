"""Pine ``int`` is 64-bit: epoch-millisecond arrays and int-literal products.

Round 8 family U (latibonit15 execution-signals-confluence, six lanes; lab tv
u-lati-levels-nq15 vs the engine, 2026-09-05): the script keeps its level
creation times in ``array.new_int()`` and expires a level when
``(time - existingTime) > 90 * 24 * 60 * 60 * 1000``. Emitted as
``std::vector<int>`` the stamp truncated and the literal product wrapped to
-813 934 592 in C++ ``int`` arithmetic, so every nearby level "expired" one
day after creation where TradingView blocks the duplicate for three months.
"""
from pineforge_codegen import transpile


def _gen(body: str) -> str:
    return transpile(f'//@version=6\nstrategy("T")\n{body}\n')


LEVEL_MACHINE = '''
var float[] levelPrices = array.new_float()
var int[]   levelTimes  = array.new_int()
if close > open
    threeMonths = 90 * 24 * 60 * 60 * 1000
    if array.size(levelPrices) > 0
        for i = array.size(levelPrices) - 1 to 0
            existingTime = array.get(levelTimes, i)
            if (time - existingTime) > threeMonths
                array.remove(levelPrices, i)
                array.remove(levelTimes, i)
    array.push(levelPrices, open)
    array.push(levelTimes, time)
if array.size(levelPrices) > 3
    strategy.entry("L", strategy.long)
'''


def test_int_array_holding_time_is_int64():
    cpp = _gen(LEVEL_MACHINE)
    assert "std::vector<int64_t> levelTimes;" in cpp
    # Every constructor of the member spells the wide element type too.
    assert "std::vector<int> levelTimes" not in cpp
    assert "levelTimes = std::vector<int>()" not in cpp
    assert "levelTimes = std::vector<int64_t>()" in cpp
    # The element read keeps the epoch: the local is not narrowed.
    assert "int64_t existingTime = " in cpp
    assert " int existingTime = " not in cpp
    # A float array is untouched.
    assert "std::vector<double> levelPrices;" in cpp


def test_int_literal_product_beyond_int32_is_folded_to_a_64_bit_literal():
    cpp = _gen(LEVEL_MACHINE)
    assert "static_cast<int64_t>(7776000000LL)" in cpp
    assert "((((90 * 24) * 60) * 60) * 1000)" not in cpp


def test_in_range_int_literal_arithmetic_is_unchanged():
    cpp = _gen("oneDay = 24 * 60 * 60 * 1000\nif close > oneDay\n    strategy.entry(\"L\", strategy.long)\n")
    assert "((24 * 60) * 60) * 1000" in cpp
    assert "static_cast<int64_t>(" not in cpp.split("oneDay = ")[1].split("\n")[0]


def test_method_form_push_of_time_widens_the_array():
    cpp = _gen('''
var times = array.new<int>()
times.push(time)
if times.size() > 0 and time - times.get(0) > 30 * 24 * 60 * 60 * 1000
    strategy.entry("L", strategy.long)
''')
    assert "std::vector<int64_t> times;" in cpp
    assert "times = std::vector<int64_t>()" in cpp
    assert "static_cast<int64_t>(2592000000LL)" in cpp


def test_int_array_without_epoch_values_stays_narrow():
    cpp = _gen('''
var int[] counts = array.new_int()
array.push(counts, 3)
c = array.get(counts, 0)
if c > 1
    strategy.entry("L", strategy.long)
''')
    assert "std::vector<int> counts;" in cpp
    assert "counts = std::vector<int>()" in cpp
    assert "int64_t" not in cpp.split("std::vector<int> counts;")[1].split("counts = std::vector<int>()")[0]
