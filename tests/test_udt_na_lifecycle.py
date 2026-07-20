"""Target-typed Pine ``na`` for UDT declarations and lifecycle resets."""

from __future__ import annotations

import re

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests.test_runtime_var_initialization import _compile_and_run


_MULTICALL_SOURCE = r'''//@version=6
strategy("UDT na multicall lifecycle")
type State
    float value
probe(bool gate, bool reset, float seed) =>
    if gate
        var State state = na
        bool startedNull = na(state)
        if startedNull
            state := State.new(seed)
        state.value += 1.0
        if reset
            state := na
        (startedNull ? 1000.0 : 0.0) + (na(state) ? 100.0 : state.value)
    else
        -1.0
never = probe(false, false, 1.0)
early = probe(bar_index >= 1, bar_index == 2, 10.0)
late = probe(bar_index >= 3, false, 100.0)
'''


_METHOD_SOURCE = r'''//@version=6
strategy("UDT na method lifecycle")
type State
    float value
type Carrier
    float seed
method probe(Carrier self, bool gate, bool reset) =>
    if gate
        var State state = na
        bool startedNull = na(state)
        if startedNull
            state := State.new(self.seed)
        state.value += 1.0
        if reset
            state := na
        (startedNull ? 1000.0 : 0.0) + (na(state) ? 100.0 : state.value)
    else
        -1.0
var Carrier firstCarrier = Carrier.new(1.0)
var Carrier secondCarrier = Carrier.new(10.0)
never = firstCarrier.probe(false, false)
early = secondCarrier.probe(bar_index >= 1, bar_index == 2)
late = firstCarrier.probe(bar_index >= 3, false)
'''


_DRIVER = r'''
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 2.0, 0.0, 1.0, 1.0, 0},
        Bar{2.0, 3.0, 1.0, 2.0, 1.0, 60000},
        Bar{3.0, 4.0, 2.0, 3.0, 1.0, 120000},
        Bar{4.0, 5.0, 3.0, 4.0, 1.0, 180000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 4);
    if (!strategy.last_error().empty()) return 2;
    std::cout << std::fixed << std::setprecision(1)
              << strategy.never << " "
              << strategy.early << " "
              << strategy.late << "\n";
}
'''


def test_callable_udt_na_reset_keeps_written_callsites_independent() -> None:
    cpp = transpile(_MULTICALL_SOURCE)
    assert not re.search(r"state(?:_cs\d+)? = na<double>\(\);", cpp)
    for target in ("state", "state_cs1", "state_cs2"):
        assert re.search(rf"^\s+(?:this->)?{target} = State\{{\}};$", cpp, re.M)
    compile_cpp(cpp, label="udt-na-multicall")
    assert _compile_and_run(cpp + _DRIVER) == "-1.0 1011.0 1101.0\n"


def test_method_udt_na_reset_keeps_written_callsites_independent() -> None:
    cpp = transpile(_METHOD_SOURCE)
    assert not re.search(r"state(?:_cs\d+)? = na<double>\(\);", cpp)
    for target in ("state", "state_cs1", "state_cs2"):
        assert re.search(rf"^\s+(?:this->)?{target} = State\{{\}};$", cpp, re.M)
    compile_cpp(cpp, label="udt-na-method")
    assert _compile_and_run(cpp + _DRIVER) == "-1.0 1011.0 1002.0\n"


def test_plain_same_raw_name_with_different_udt_owners_stays_lexical() -> None:
    source = r'''//@version=6
strategy("UDT na owner collision")
type Left
    float value
type Right
    int value
left(bool reset) =>
    Left state = Left.new(1.0)
    if reset
        state := na
    na(state)
right(bool reset) =>
    Right state = Right.new(2)
    if reset
        state := na
    na(state)
leftNull = left(true)
rightNull = right(true)
    '''
    cpp = transpile(source)
    assert "state = Left{};" in cpp
    assert "state = Right{};" in cpp
    compile_cpp(cpp, label="udt-na-owner-collision")


def test_sibling_branch_udt_na_targets_follow_exact_storage_names() -> None:
    source = r'''//@version=6
strategy("UDT na sibling declarations")
type Left
    float value
type Right
    int value
probe(bool chooseLeft) =>
    if chooseLeft
        var Left state = Left.new(1.0)
        state := na
        na(state)
    else
        var Right state = Right.new(2)
        state := na
        na(state)
leftNull = probe(true)
rightNull = probe(false)
'''
    cpp = transpile(source)
    assert "state = Left{};" in cpp
    assert "state__blk1 = Right{};" in cpp
    compile_cpp(cpp, label="udt-na-sibling-branches")


def test_chart_scope_sibling_udt_vars_keep_exact_member_types() -> None:
    source = r'''//@version=6
strategy("chart UDT sibling declarations")
type Left
    float value
type Right
    int value
if close > 0
    var Left state = na
    if na(state)
        state := Left.new(1.0)
    state := na
else
    var Right state = na
    if na(state)
        state := Right.new(2)
    state := na
'''
    cpp = transpile(source)
    assert re.search(r"^\s+Left state;$", cpp, re.M)
    assert re.search(r"^\s+Right state__blk1;$", cpp, re.M)
    assert "state = Left{};" in cpp
    assert "state__blk1 = Right{};" in cpp
    compile_cpp(cpp, label="udt-na-chart-sibling-members")


def test_plain_and_nested_udt_na_contexts_are_target_typed() -> None:
    source = r'''//@version=6
strategy("UDT contextual na")
type Inner
    float value
type Outer
    Inner inner = na
    Inner other
make(bool choose) =>
    Inner local = na
    local := choose ? Inner.new(1.0) : na
    local := if choose
        local
    else
        na
    local := switch choose
        true => local
        => na
    Outer.new(na, local)
var Outer holder = make(true)
holder.inner := na
ok = na(holder.inner) and not na(holder.other)
'''
    cpp = transpile(source)
    assert "Inner inner = Inner{};" in cpp
    assert "Inner local = Inner{};" in cpp
    assert cpp.count("local = Inner{};") >= 3
    assert "holder.inner = Inner{};" in cpp
    assert "Outer{.inner = Inner{}" in cpp
    compile_cpp(cpp, label="udt-na-contexts")


def test_chart_scope_typed_udt_na_is_target_typed_each_bar() -> None:
    source = r'''//@version=6
strategy("chart UDT na lifecycle")
type State
    float value
State state = na
int observed = na(state) ? 1 : 0
'''
    cpp = transpile(source)
    assert "state = State{};" in cpp
    assert "state = na<double>();" not in cpp
    compile_cpp(cpp, label="udt-na-chart-scope")


def test_callable_udt_locals_do_not_retype_same_named_global_scalars() -> None:
    source = r'''//@version=6
strategy("UDT raw-name global isolation")
type State
    float value
float state = 2.0
var float tracker = 3.0
probe() =>
    State state = State.new(9.0)
    State tracker = State.new(10.0)
    state := na
    tracker := na
    na(state) and na(tracker)
bool localNulls = probe()
float retained = state + tracker
'''
    cpp = transpile(source)
    assert re.search(r"^\s+double state(?: = [^;]+)?;$", cpp, re.M)
    assert re.search(r"^\s+double tracker(?: = [^;]+)?;$", cpp, re.M)
    assert "State state = State{.value = 9.0" in cpp
    assert "State tracker = State{.value = 10.0" in cpp
    assert "state = State{};" in cpp
    assert "tracker = State{};" in cpp
    compile_cpp(cpp, label="udt-na-raw-name-global-isolation")


def test_exact_global_udt_drives_methods_fields_and_lvalue_aliases() -> None:
    source = r'''//@version=6
strategy("exact global UDT identity")
type Left
    float value
    float tag
type Right
    float value
    table tag
method read(Left self) => self.value
method read(Right self) => self.value + 100.0
Left state = Left.new(1.0, 7.0)
shadow() =>
    Right state = Right.new(2.0)
    na(state)
mutate() =>
    Left alias = state
    alias.value := 3.0
    alias.value
bool ignored = shadow()
float methodValue = state.read()
float aliasValue = mutate()
float tagValue = state.tag
'''
    cpp = transpile(source)
    assert "_udt_Left_read(state)" in cpp
    assert "Left& alias = state;" in cpp
    assert "tagValue = state.tag;" in cpp
    assert "tagValue = /* drawing field omitted */ 0;" not in cpp
    compile_cpp(cpp, label="udt-exact-global-identity")


def test_global_udt_array_alias_uses_rhs_element_type_not_raw_name() -> None:
    source = r'''//@version=6
strategy("exact global UDT array alias")
type Left
    float value
type Right
    float value
var array<Left> items = array.new<Left>()
if barstate.isfirst
    items.push(Left.new(1.0))
for i = 0 to items.size() - 1
    Left state = items.get(i)
    state.value := 3.0
shadow() =>
    Right state = Right.new(2.0)
    na(state)
bool ignored = shadow()
'''
    cpp = transpile(source)
    assert "Left& state =" in cpp
    assert "Right& state =" not in cpp
    compile_cpp(cpp, label="udt-exact-global-array-alias")


def test_scalar_and_tuple_tombstones_keep_typed_na_reassignment() -> None:
    source = r'''//@version=6
strategy("exact scalar tombstones")
type State
    float value
State state = State.new(5.0)
int tracker = 2
pair() => [1, 2]
shadow() =>
    State tracker = State.new(9.0)
    tracker := na
    na(tracker)
tupleProbe() =>
    [state, other] = pair()
    state := na
    state
bool ignored = shadow()
int tupleValue = tupleProbe()
tracker := na
bool trackerNull = na(tracker)
'''
    cpp = transpile(source)
    assert "state = na<int>();" in cpp
    assert "tracker = na<int>();" in cpp
    compile_cpp(cpp, label="udt-exact-scalar-tuple-tombstones")


def test_callable_primitive_clones_ignore_unrelated_udt_raw_name() -> None:
    source = r'''//@version=6
strategy("UDT callable primitive clone isolation")
type State
    float value
counter(bool reset) =>
    var int state = 1
    if reset
        state := na
    state
shadow() =>
    State state = State.new(4.0)
    na(state)
int first = counter(true)
int second = counter(false)
bool ignored = shadow()
'''
    cpp = transpile(source)
    assert re.search(r"^\s+int state(?: = [^;]+)?;$", cpp, re.M)
    assert re.search(r"^\s+int state_cs1(?: = [^;]+)?;$", cpp, re.M)
    assert "State state_cs1" not in cpp
    compile_cpp(cpp, label="udt-na-callable-primitive-clone-isolation")


def test_primitive_metadata_survives_global_udt_tombstone_registry() -> None:
    source = r'''//@version=6
strategy("exact primitive metadata")
bool enabled = true
array<bool> flags = array.from(enabled, false)
'''
    cpp = transpile(source)
    assert "std::vector<bool> flags;" in cpp
    assert "flags = std::vector<bool>{" in cpp
    compile_cpp(cpp, label="udt-exact-primitive-metadata")


def test_drawing_method_parameter_keeps_exact_lexical_type() -> None:
    source = r'''//@version=6
strategy("exact drawing method parameter")
type Shadow
    float value
method span(line ln) =>
    ln.get_x2() - ln.get_x1()
poison() =>
    Shadow ln = Shadow.new(1.0)
    na(ln)
var line segment = line.new(bar_index, close, bar_index + 1, close)
float measured = segment.span()
bool ignored = poison()
'''
    cpp = transpile(source)
    assert "pf_line_get_x2(_pf_lines_, ln)" in cpp
    assert "pf_line_get_x1(_pf_lines_, ln)" in cpp
    assert "ln.get_x2()" not in cpp
    assert "ln.get_x1()" not in cpp
    compile_cpp(cpp, label="udt-exact-drawing-method-parameter")


def test_stateful_method_edge_uses_exact_global_udt_owner() -> None:
    source = r'''//@version=6
strategy("stateful method exact edge")
type Left
    float value
type Right
    float value
method accumulate(Left self) =>
    var float leftTotal = 0.0
    leftTotal += self.value
    leftTotal
method accumulate(Right self) =>
    var float rightTotal = 0.0
    rightTotal += self.value
    rightTotal
Left state = Left.new(2.0)
wrapped() => state.accumulate()
shadow() =>
    Right state = Right.new(3.0)
    na(state)
bool ignored = shadow()
float first = wrapped()
float second = wrapped()
'''
    cpp = transpile(source)
    assert "_udt_Left_accumulate_cs0(state)" in cpp
    assert "_udt_Left_accumulate_cs1(state)" in cpp
    assert "_udt_Right_accumulate_cs0(state)" not in cpp
    compile_cpp(cpp, label="udt-na-stateful-method-exact-edge")
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {Bar{1.0, 2.0, 0.0, 1.0, 1.0, 1700000000000LL}};
    GeneratedStrategy strategy;
    strategy.run(bars, 1);
    if (!strategy.last_error().empty()) return 8;
    std::cout << strategy.first << " " << strategy.second << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "2 2\n"


def test_direct_udt_ctor_na_selection_return_is_target_typed() -> None:
    source = r'''//@version=6
strategy("UDT nullable selection return")
type State
    float value
choose(bool enabled, float seed) => enabled ? State.new(seed) : na
State missing = choose(false, 1.0)
State present = choose(true, 7.5)
float observed = (na(missing) ? 100.0 : 0.0) +
    (na(present) ? 0.0 : present.value)
'''
    cpp = transpile(source)
    assert re.search(r"State choose\(bool enabled, double seed\)", cpp)
    assert "State{.value = seed, .__pf_na = false}) : (State{})" in cpp
    compile_cpp(cpp, label="udt-na-selection-return")
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {Bar{1.0, 2.0, 0.0, 1.0, 1.0, 1700000000000LL}};
    GeneratedStrategy strategy;
    strategy.run(bars, 1);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.observed << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "107.5\n"


def test_if_and_switch_udt_ctor_na_returns_are_target_typed() -> None:
    bodies = {
        "if": r'''choose(bool enabled) =>
    if enabled
        State.new(2.5)
    else
        na''',
        "switch": r'''choose(bool enabled) =>
    switch enabled
        true => State.new(2.5)
        => na''',
    }
    for label, body in bodies.items():
        source = f'''//@version=6
strategy("UDT {label} nullable return")
type State
    float value
{body}
State observed = choose(false)
'''
        cpp = transpile(source)
        assert re.search(r"State choose\(bool enabled\)", cpp)
        assert "_func_ret = State{};" in cpp
        compile_cpp(cpp, label=f"udt-na-{label}-return")


def test_udt_method_nullable_udt_return_carries_exact_type() -> None:
    source = r'''//@version=6
strategy("UDT method nullable return")
type State
    float value
type Factory
    float seed
method choose(Factory self, bool enabled) =>
    enabled ? State.new(self.seed) : na
var Factory factory = Factory.new(9.25)
State missing = factory.choose(false)
State present = factory.choose(true)
float observed = (na(missing) ? 100.0 : 0.0) + present.value
'''
    cpp = transpile(source)
    assert re.search(
        r"State _udt_Factory_choose(?:_cs\d+)?\(Factory& self, bool enabled\)",
        cpp,
    )
    compile_cpp(cpp, label="udt-method-na-return")
