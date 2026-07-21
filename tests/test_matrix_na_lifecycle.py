"""Pine v6 target-typed ``na`` lifecycle for matrix IDs."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests.test_runtime_var_initialization import _compile_and_run


_DIRECT_SOURCE = r'''//@version=6
strategy("matrix na direct lifecycle")
probe(bool gate, float seed) =>
    if gate
        var matrix<float> state = na
        bool startedNull = na(state)
        if startedNull
            state := matrix.new<float>(1, 1, seed)
        state.set(0, 0, state.get(0, 0) + 1.0)
        if bar_index > 10
            state := na
        (startedNull ? 1000.0 : 0.0) + state.get(0, 0)
    else
        -1.0
never = probe(false, 1.0)
early = probe(bar_index >= 1, 10.0)
late = probe(bar_index >= 2, 100.0)
'''


_METHOD_SOURCE = r'''//@version=6
strategy("matrix na method lifecycle")
type Carrier
    float seed
method probe(Carrier self, bool gate) =>
    if gate
        var matrix<float> state = na
        bool startedNull = na(state)
        if startedNull
            state := matrix.new<float>(1, 1, self.seed)
        state.set(0, 0, state.get(0, 0) + 1.0)
        if bar_index > 10
            state := na
        (startedNull ? 1000.0 : 0.0) + state.get(0, 0)
    else
        -1.0
var Carrier firstCarrier = Carrier.new(1.0)
var Carrier secondCarrier = Carrier.new(10.0)
never = firstCarrier.probe(false)
early = secondCarrier.probe(bar_index >= 1)
late = firstCarrier.probe(bar_index >= 2)
'''


_DRIVER = r'''
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 2.0, 0.0, 1.0, 1.0, 0},
        Bar{2.0, 3.0, 1.0, 2.0, 1.0, 60000},
        Bar{3.0, 4.0, 2.0, 3.0, 1.0, 120000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 2;
    std::cout << std::fixed << std::setprecision(1)
              << strategy.never << " "
              << strategy.early << " "
              << strategy.late << "\n";
}
'''


def _assert_cloned_state_is_target_typed(cpp: str) -> None:
    assert not re.search(r"state(?:_cs\d+)? = na<double>\(\);", cpp)
    for target in ("state", "state_cs1", "state_cs2"):
        assert re.search(
            rf"^\s+(?:this->)?{target} = PineMatrix\{{\}};$", cpp, re.M
        )


def test_callable_matrix_na_lifecycle_keeps_written_callsites_independent() -> None:
    cpp = transpile(_DIRECT_SOURCE)
    _assert_cloned_state_is_target_typed(cpp)
    compile_cpp(cpp, label="matrix-na-direct-lifecycle")
    assert _compile_and_run(cpp + _DRIVER) == "-1.0 12.0 1101.0\n"


def test_method_matrix_na_lifecycle_keeps_written_callsites_independent() -> None:
    cpp = transpile(_METHOD_SOURCE)
    _assert_cloned_state_is_target_typed(cpp)
    compile_cpp(cpp, label="matrix-na-method-lifecycle")
    assert _compile_and_run(cpp + _DRIVER) == "-1.0 12.0 1002.0\n"


def test_matrix_na_reset_is_reachable_and_reinitializes_on_next_bar() -> None:
    source = r'''//@version=6
strategy("matrix na reachable reset")
probe(bool gate, bool clear) =>
    var matrix<float> state = na
    bool startedNull = na(state)
    float result = -1.0
    if gate
        if startedNull
            state := matrix.new<float>(1, 1, 0.0)
        state.set(0, 0, state.get(0, 0) + 1.0)
        result := (startedNull ? 1000.0 : 0.0) + state.get(0, 0)
        if clear
            state := na
    result
value = probe(bar_index > 0, bar_index == 2)
var float step0 = na
var float step1 = na
var float step2 = na
var float step3 = na
var float step4 = na
if bar_index == 0
    step0 := value
if bar_index == 1
    step1 := value
if bar_index == 2
    step2 := value
if bar_index == 3
    step3 := value
if bar_index == 4
    step4 := value
'''
    cpp = transpile(source)
    assert "state = PineMatrix{};" in cpp
    driver = r'''
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 2.0, 0.0, 1.0, 1.0, 0},
        Bar{2.0, 3.0, 1.0, 2.0, 1.0, 60000},
        Bar{3.0, 4.0, 2.0, 3.0, 1.0, 120000},
        Bar{4.0, 5.0, 3.0, 4.0, 1.0, 180000},
        Bar{5.0, 6.0, 4.0, 5.0, 1.0, 240000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 5);
    if (!strategy.last_error().empty()) return 2;
    std::cout << std::fixed << std::setprecision(1)
              << strategy.step0 << " " << strategy.step1 << " "
              << strategy.step2 << " " << strategy.step3 << " "
              << strategy.step4 << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "-1.0 1001.0 2.0 1001.0 2.0\n"


def test_all_supported_matrix_element_families_use_typed_na_ids() -> None:
    source = r'''//@version=6
strategy("typed matrix na families")
matrix<float> floats = na
matrix<int> ints = na
matrix<string> strings = na
matrix<bool> bools = na
bool startedNull = na(floats) and na(ints) and na(strings) and na(bools)
floats := matrix.new<float>(0, 0)
ints := matrix.new<int>(0, 0)
strings := matrix.new<string>(0, 0)
bools := matrix.new<bool>(0, 0)
bool emptyIdsAreValid = not na(floats) and not na(ints) and not na(strings) and not na(bools)
floats := na
ints := na
strings := na
bools := na
bool resetNull = na(floats) and na(ints) and na(strings) and na(bools)
'''
    cpp = transpile(source)
    expected = {
        "floats": "PineMatrix",
        "ints": "PineGenericMatrix<int>",
        "strings": "PineGenericMatrix<std::string>",
        "bools": "PineGenericMatrix<bool>",
    }
    for name, cpp_type in expected.items():
        assert cpp.count(f"{name} = {cpp_type}{{}};") == 2
        assert f"{name} = na<double>();" not in cpp
    compile_cpp(cpp, label="matrix-na-element-families")
    driver = r'''
#include <iostream>
int main() {
    Bar bar{1.0, 2.0, 0.0, 1.0, 1.0, 0};
    GeneratedStrategy strategy;
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.startedNull << " "
              << strategy.emptyIdsAreValid << " "
              << strategy.resetNull << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "1 1 1\n"


def test_matrix_na_selection_arms_inherit_the_declared_target_type() -> None:
    source = r'''//@version=6
strategy("matrix na selections")
matrix<int> ternaryValue = bar_index > 0 ? matrix.new<int>(0, 0) : na
matrix<string> ifValue = if bar_index > 0
    matrix.new<string>(0, 0)
else
    na
matrix<bool> switchValue = switch bar_index
    0 => na
    => matrix.new<bool>(0, 0)
ternaryValue := bar_index > 1 ? na : matrix.new<int>(0, 0)
ifValue := if bar_index > 1
    na
else
    matrix.new<string>(0, 0)
switchValue := switch bar_index
    2 => na
    => matrix.new<bool>(0, 0)
'''
    cpp = transpile(source)
    assert "PineGenericMatrix<int>{}" in cpp
    assert "PineGenericMatrix<std::string>{}" in cpp
    assert "PineGenericMatrix<bool>{}" in cpp
    assert not re.search(
        r"(?:ternaryValue|ifValue|switchValue) = na<double>\(\);", cpp
    )
    compile_cpp(cpp, label="matrix-na-selections")


def test_type_only_matrix_contexts_emit_headers_and_target_typed_na() -> None:
    sources = {
        "float-global": r'''//@version=6
strategy("typed na float global")
matrix<float> state = na
bool startedNull = na(state)
''',
        "generic-parameter": r'''//@version=6
strategy("typed na generic parameter")
isNull(matrix<int> state) => na(state)
bool startedNull = isNull(na)
''',
        "generic-udt-field": r'''//@version=6
strategy("typed na generic UDT field")
type Holder
    matrix<int> state = na
var Holder defaulted = Holder.new()
var Holder provided = Holder.new(na)
provided.state := na
bool startedNull = na(defaulted.state) and na(provided.state)
''',
    }
    for label, source in sources.items():
        cpp = transpile(source)
        assert "#include <pineforge/matrix.hpp>" in cpp
        if label.startswith("generic"):
            assert "#include <pineforge/generic_matrix.hpp>" in cpp
            assert "PineGenericMatrix<int>{}" in cpp
        assert not re.search(r"(?:state|\.state) = na<double>\(\)", cpp)
        compile_cpp(cpp, label=f"matrix-na-{label}")


def test_explicit_typed_na_matrix_rejects_element_type_change() -> None:
    source = r'''//@version=6
strategy("typed na matrix mismatch")
matrix<int> state = na
state := matrix.new<float>(1, 1, 0.0)
'''
    with pytest.raises(
        CompileError,
        match=r"element type mismatch.*expected PineGenericMatrix<int>.*got PineMatrix",
    ):
        transpile(source)


@pytest.mark.parametrize(
    ("label", "assignment"),
    [
        ("identifier", "state := other"),
        ("ternary", "state := bar_index == 0 ? other : na"),
        (
            "if",
            """state := if bar_index == 0
    other
else
    na""",
        ),
        (
            "switch",
            """state := switch bar_index
    0 => other
    => na""",
        ),
        ("method-return", "state := other.copy()"),
        ("mixed-arms", "state := bar_index == 0 ? same : other"),
    ],
)
def test_matrix_reassignment_rejects_complete_incompatible_rhs(
    label: str,
    assignment: str,
) -> None:
    source = f'''//@version=6
strategy("matrix reassignment {label}")
matrix<int> state = na
matrix<int> same = matrix.new<int>(1, 1, 0)
matrix<float> other = matrix.new<float>(1, 1, 0.0)
{assignment}
'''
    with pytest.raises(
        CompileError,
        match=r"element type mismatch.*expected PineGenericMatrix<int>.*got PineMatrix",
    ):
        transpile(source)


def test_callable_local_matrix_reassignment_rejects_identifier_type_change() -> None:
    source = r'''//@version=6
strategy("callable matrix reassignment mismatch")
probe() =>
    matrix<int> state = na
    matrix<float> other = matrix.new<float>(1, 1, 0.0)
    state := other
    0
value = probe()
'''
    with pytest.raises(
        CompileError,
        match=r"element type mismatch.*expected PineGenericMatrix<int>.*got PineMatrix",
    ):
        transpile(source)


@pytest.mark.parametrize(
    "assignment",
    [
        "holder.state := other",
        """holder.state := if bar_index == 0
    other
else
    na""",
    ],
)
def test_udt_field_matrix_reassignment_rejects_element_type_change(
    assignment: str,
) -> None:
    source = f'''//@version=6
strategy("UDT field matrix reassignment mismatch")
type Holder
    matrix<int> state = na
var Holder holder = Holder.new()
matrix<float> other = matrix.new<float>(1, 1, 0.0)
{assignment}
'''
    with pytest.raises(
        CompileError,
        match=r"element type mismatch.*expected PineGenericMatrix<int>.*got PineMatrix",
    ):
        transpile(source)


def test_matrix_reassignment_complete_rhs_accepts_same_element_type() -> None:
    source = r'''//@version=6
strategy("same-type matrix reassignments")
matrix<int> state = na
matrix<int> other = matrix.new<int>(1, 1, 0)
state := other
state := bar_index == 0 ? other : na
state := if bar_index == 0
    other
else
    na
state := switch bar_index
    0 => other.copy()
    => na
'''
    cpp = transpile(source)
    compile_cpp(cpp, label="matrix-na-same-type-reassignments")


def test_global_inferred_matrix_na_selection_keeps_matrix_type() -> None:
    source = r'''//@version=6
strategy("inferred matrix na global")
var base = matrix.new<int>(0, 0)
selected = bar_index > 0 ? na : base
selectedNull = na(selected)
'''
    cpp = transpile(source)
    assert "PineGenericMatrix<int> selected;" in cpp
    assert "selected = na<double>();" not in cpp
    compile_cpp(cpp, label="matrix-na-inferred-global")
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 2.0, 0.0, 1.0, 1.0, 0},
        Bar{2.0, 3.0, 1.0, 2.0, 1.0, 60000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 2);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.selectedNull << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "1\n"


def test_callable_inferred_matrix_na_selections_keep_matrix_type() -> None:
    source = r'''//@version=6
strategy("inferred matrix na callable")
probe(int mode) =>
    base = matrix.new<int>(0, 0)
    ternaryValue = mode == 0 ? na : base
    ifValue = if mode == 1
        na
    else
        base
    switchValue = switch mode
        2 => na
        => base
    (na(ternaryValue) ? 100 : 0) + (na(ifValue) ? 10 : 0) + (na(switchValue) ? 1 : 0)
mode0 = probe(0)
mode1 = probe(1)
mode2 = probe(2)
'''
    cpp = transpile(source)
    for name in ("ternaryValue", "ifValue", "switchValue"):
        assert re.search(
            rf"^\s+PineGenericMatrix<int> {name}(?: =|;)", cpp, re.M
        )
        assert f"{name} = na<double>();" not in cpp
    compile_cpp(cpp, label="matrix-na-inferred-callable")
    driver = r'''
#include <iostream>
int main() {
    Bar bar{1.0, 2.0, 0.0, 1.0, 1.0, 0};
    GeneratedStrategy strategy;
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.mode0 << " " << strategy.mode1 << " "
              << strategy.mode2 << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "100 10 1\n"


def test_global_direct_matrix_constructors_infer_nullable_selection_types() -> None:
    source = r'''//@version=6
strategy("direct matrix constructor selections")
ternaryValue = bar_index == 0 ? matrix.new<int>(0, 0) : na
ifValue = if bar_index == 0
    matrix.new<string>(0, 0)
switchValue = switch bar_index
    0 => matrix.new<bool>(0, 0)
ternaryNull = na(ternaryValue)
ifNull = na(ifValue)
switchNull = na(switchValue)
'''
    cpp = transpile(source)
    expected = {
        "ternaryValue": "PineGenericMatrix<int>",
        "ifValue": "PineGenericMatrix<std::string>",
        "switchValue": "PineGenericMatrix<bool>",
    }
    for name, cpp_type in expected.items():
        assert f"{cpp_type} {name};" in cpp
        assert f"{name} = na<double>();" not in cpp
    assert "ifValue = PineGenericMatrix<std::string>{};" in cpp
    assert "switchValue = PineGenericMatrix<bool>{};" in cpp
    compile_cpp(cpp, label="matrix-na-direct-constructor-selections")
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 2.0, 0.0, 1.0, 1.0, 0},
        Bar{2.0, 3.0, 1.0, 2.0, 1.0, 60000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 2);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.ternaryNull << " "
              << strategy.ifNull << " "
              << strategy.switchNull << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "1 1 1\n"


def test_missing_if_switch_arms_reset_reassigned_matrix_and_map_ids() -> None:
    source = r'''//@version=6
strategy("nullable collection unmatched reset")
var matrix<int> matrixState = na
var map<string, int> mapState = na
if bar_index == 0
    matrixState := matrix.new<int>(0, 0)
    mapState := map.new<string, int>()
matrixState := switch bar_index
    0 => matrixState
mapState := if bar_index == 0
    mapState
var bool matrixWasValid = false
var bool mapWasValid = false
if bar_index == 0
    matrixWasValid := not na(matrixState)
    mapWasValid := not na(mapState)
matrixIsNull = na(matrixState)
mapIsNull = na(mapState)
'''
    cpp = transpile(source)
    assert "matrixState = PineGenericMatrix<int>{};" in cpp
    assert "mapState = PineMap<std::string, int>{};" in cpp
    compile_cpp(cpp, label="nullable-collection-unmatched-reset")
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 2.0, 0.0, 1.0, 1.0, 0},
        Bar{2.0, 3.0, 1.0, 2.0, 1.0, 60000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 2);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.matrixWasValid << " "
              << strategy.mapWasValid << " "
              << strategy.matrixIsNull << " "
              << strategy.mapIsNull << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "1 1 1 1\n"


def test_udt_method_matrix_na_arguments_use_the_declared_parameter_type() -> None:
    source = r'''//@version=6
strategy("typed matrix method arguments")
type Carrier
    float seed
method isNull(Carrier self, matrix<int> state = na) =>
    na(state)
var Carrier carrier = Carrier.new(1.0)
identifierPositional = carrier.isNull(na)
identifierKeyword = carrier.isNull(state = na)
identifierOmitted = carrier.isNull()
temporaryPositional = Carrier.new(2.0).isNull(na)
temporaryKeyword = Carrier.new(3.0).isNull(state = na)
temporaryOmitted = Carrier.new(4.0).isNull()
'''
    cpp = transpile(source)
    assert "PineGenericMatrix<int> state" in cpp
    assert "na<double>()" not in cpp
    assert cpp.count("PineGenericMatrix<int>{}") >= 6
    compile_cpp(cpp, label="matrix-na-udt-method-arguments")
    driver = r'''
#include <iostream>
int main() {
    Bar bar{1.0, 2.0, 0.0, 1.0, 1.0, 0};
    GeneratedStrategy strategy;
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.identifierPositional << " "
              << strategy.identifierKeyword << " "
              << strategy.identifierOmitted << " "
              << strategy.temporaryPositional << " "
              << strategy.temporaryKeyword << " "
              << strategy.temporaryOmitted << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "1 1 1 1 1 1\n"


def test_nullable_matrix_builtin_returns_keep_receiver_element_type() -> None:
    source = r'''//@version=6
strategy("nullable matrix builtin returns")
var matrix<float> base = matrix.new<float>(1, 1, 2.0)
methodInv = bar_index == 0 ? na : base.inv()
methodPinv = if bar_index == 0
    base.pinv()
else
    na
functionalCopy = switch bar_index
    0 => matrix.copy(base)
    => na
functionalTranspose = bar_index == 0 ? na : matrix.transpose(base)
functionalInv = if bar_index > 0
    matrix.inv(base)
methodInvNull = na(methodInv)
methodPinvNull = na(methodPinv)
functionalCopyNull = na(functionalCopy)
functionalTransposeNull = na(functionalTranspose)
functionalInvNull = na(functionalInv)
'''
    cpp = transpile(source)
    for name in (
        "methodInv",
        "methodPinv",
        "functionalCopy",
        "functionalTranspose",
        "functionalInv",
    ):
        assert f"PineMatrix {name};" in cpp
        assert f"{name} = na<double>();" not in cpp
    assert "functionalInv = PineMatrix{};" in cpp
    compile_cpp(cpp, label="matrix-na-builtin-return-selections")
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 2.0, 0.0, 1.0, 1.0, 0},
        Bar{2.0, 3.0, 1.0, 2.0, 1.0, 60000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 2);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.methodInvNull << " "
              << strategy.methodPinvNull << " "
              << strategy.functionalCopyNull << " "
              << strategy.functionalTransposeNull << " "
              << strategy.functionalInvNull << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "0 1 1 0 0\n"


def test_global_persistent_collection_selections_initialize_once() -> None:
    source = r'''//@version=6
strategy("global persistent collection selections")
var matrix<int> explicitMatrix = if bar_index == 0
    matrix.new<int>(1, 1, 7)
else
    na
var map<string, int> explicitMap = switch bar_index
    0 => map.new<string, int>()
    => na
var matrix<int> missingMatrix = if bar_index > 0
    matrix.new<int>(1, 1, 9)
var map<string, int> missingMap = switch bar_index
    1 => map.new<string, int>()
explicitMatrixNull = na(explicitMatrix)
explicitMapNull = na(explicitMap)
missingMatrixNull = na(missingMatrix)
missingMapNull = na(missingMap)
'''
    cpp = transpile(source)
    assert "/* unknown */" not in cpp
    assert "missingMatrix = PineGenericMatrix<int>{};" in cpp
    assert "missingMap = PineMap<std::string, int>{};" in cpp
    for name in (
        "explicitMatrix",
        "explicitMap",
        "missingMatrix",
        "missingMap",
    ):
        assert f"if (!_pf_var_init_{name})" in cpp
        assert f"_pf_var_init_{name} = true;" in cpp
    compile_cpp(cpp, label="global-persistent-collection-selections")
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 2.0, 0.0, 1.0, 1.0, 0},
        Bar{2.0, 3.0, 1.0, 2.0, 1.0, 60000},
        Bar{3.0, 4.0, 2.0, 3.0, 1.0, 120000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.explicitMatrixNull << " "
              << strategy.explicitMapNull << " "
              << strategy.missingMatrixNull << " "
              << strategy.missingMapNull << "\n";
}
'''
    # The explicit constructor arms run on bar 0 and persist. The missing
    # fallbacks become typed null on bar 0 and must not retry on bars 1/2.
    assert _compile_and_run(cpp + driver) == "0 0 1 1\n"


def test_callable_persistent_collection_selections_initialize_on_first_reach() -> None:
    source = r'''//@version=6
strategy("callable persistent collection selections")
probe(bool gate) =>
    int result = -1
    if gate
        var matrix<int> explicitMatrix = if bar_index == 1
            matrix.new<int>(1, 1, 7)
        else
            na
        var map<string, int> explicitMap = switch bar_index
            1 => map.new<string, int>()
            => na
        var matrix<int> missingMatrix = if bar_index > 1
            matrix.new<int>(1, 1, 9)
        var map<string, int> missingMap = switch bar_index
            2 => map.new<string, int>()
        result := (not na(explicitMatrix) ? 1000 : 0) +
            (not na(explicitMap) ? 100 : 0) +
            (na(missingMatrix) ? 10 : 0) +
            (na(missingMap) ? 1 : 0)
    result
value = probe(bar_index >= 1)
var int step0 = -99
var int step1 = -99
var int step2 = -99
if bar_index == 0
    step0 := value
if bar_index == 1
    step1 := value
if bar_index == 2
    step2 := value
'''
    cpp = transpile(source)
    assert "/* unknown */" not in cpp
    assert "this->missingMatrix = PineGenericMatrix<int>{};" in cpp
    assert "this->missingMap = PineMap<std::string, int>{};" in cpp
    for name in (
        "explicitMatrix",
        "explicitMap",
        "missingMatrix",
        "missingMap",
    ):
        assert re.search(rf"if \(!this->_pf_var_init_.*{name}", cpp)
    compile_cpp(cpp, label="callable-persistent-collection-selections")
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 2.0, 0.0, 1.0, 1.0, 0},
        Bar{2.0, 3.0, 1.0, 2.0, 1.0, 60000},
        Bar{3.0, 4.0, 2.0, 3.0, 1.0, 120000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.step0 << " "
              << strategy.step1 << " "
              << strategy.step2 << "\n";
}
'''
    # The declaration is first reached on bar 1. Its constructor/null choices
    # persist on bar 2 even though every condition has flipped by then.
    assert _compile_and_run(cpp + driver) == "-1 1111 1111\n"
