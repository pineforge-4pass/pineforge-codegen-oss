"""Generated cold-state coverage; actual retained-handle runs execute in Cloud."""
import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


SOURCES = [
    '''//@version=6
strategy("cold scalar and indicator state", max_bars_back=211)
length = input.int(5, "Length")
var int count = 7
count += 1
average = ta.sma(close, length)
crossed = ta.crossover(close, average)
prior = close[1]
''',
    '''//@version=6
strategy("cold requested state")
length = input.int(3, "Length")
value = request.security(syminfo.tickerid, "60", ta.ema(close, length))
''',
    '''//@version=6
strategy("cold owned objects", calc_on_order_fills=true, max_lines_count=23)
type LifeRecord
    float value
var LifeRecord item = LifeRecord.new(7)
var array<LifeRecord> items = array.new<LifeRecord>()
items.push(item)
var map<string, float> values = map.new<string, float>()
values.put("value", close)
var matrix<float> table = matrix.new<float>(2, 2, 7)
var line segment = line.new(bar_index, close, bar_index + 1, close)
item.value := close
''',
    '''//@version=6
strategy("cold callable state")
step(float source) =>
    var float total = 7
    total += source
    ta.ema(total, 3)
left = step(close)
right = step(open)
prepare_script_run = left + right
var int n = 17
var float bars = 13
var bool allow_precalculation = true
''',
]


def _preparation(cpp):
    return cpp.split("    void prepare_script_run(", 1)[1].split("\n    }", 1)[0]


@pytest.mark.parametrize("source", SOURCES)
def test_cold_state_categories_compile_against_lifecycle_runtime(source):
    compile_cpp(transpile(source), label="generated cold script state")


@pytest.mark.parametrize("source", SOURCES)
def test_every_checkpoint_member_has_a_cold_reset_action(source):
    cpp = transpile(source)
    preparation = _preparation(cpp)
    members = set(re.findall(r"decltype\(GeneratedStrategy::(\w+)\)", cpp))
    assert members
    for member in members:
        assert re.search(r"\b" + re.escape(member) + r"(?: = |\.reset_for_run\(\))", preparation)
    assert preparation.index("_pf_script_state_checkpoint_.reset();") < preparation.index(" = ")
    assert "void run(const Bar*" not in cpp
    assert "PINEFORGE_HAS_SCRIPT_RUN_PREPARE_V1" in cpp


def test_constructor_values_caps_and_cache_invalidation_are_preserved():
    cpp = transpile(SOURCES[0])
    preparation = _preparation(cpp)
    assert "count(7)" in cpp
    assert "count = decltype(this->count)(7);" in preparation
    assert "_inputs_initialized_ = false;" in preparation
    assert "_ta_initialized_ = false;" in preparation
    assert "_use_precalc = false;" in preparation
    caches = re.findall(r"^\s+std::vector<double> (_precalc_\w+);$", cpp, re.MULTILINE)
    assert caches
    for cache in caches:
        assert f"this->{cache} = decltype(this->{cache}){{}};" in preparation
    assert re.search(r"_s_close = decltype\(this->_s_close\)[({]211[)}];", preparation)
    assert preparation.index("_use_precalc = false;") < preparation.index("precalculate(bars, n)")
    assert "inputs_.clear" not in preparation
    assert "initial_capital_ =" not in preparation


def test_udt_reset_is_not_assignment_or_a_reused_bar_checkpoint():
    cpp = transpile(SOURCES[2])
    preparation = _preparation(cpp)
    assert ".reset_for_run();" in preparation
    assert "snapshot_script_state()" not in preparation
    assert "restore_script_state()" not in preparation
    assert "_pf_undo_.clear();" in cpp
    assert "_pf_records_.clear();" in cpp
    assert "_pf_checkpoint_active_ = false;" in cpp
    assert "decltype(this->_pf_lines_){23}" in preparation


def test_callback_parameter_names_cannot_shadow_authored_state():
    preparation = _preparation(transpile(SOURCES[3]))
    assert "this->n = decltype(this->n)(17);" in preparation
    assert "this->bars = decltype(this->bars)(13);" in preparation
    assert "this->allow_precalculation = decltype(this->allow_precalculation)(true);" in preparation
