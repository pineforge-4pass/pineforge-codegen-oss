"""Selection joins preserve array types and their historical receivers."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer.types import TypeHelper
from pineforge_codegen.ast_nodes import Identifier
from pineforge_codegen.codegen.types import TypeInferer
from pineforge_codegen.symbols import TypeSpec
from tests._compile import run_emitted_tu


@pytest.mark.parametrize("inferer_type", [TypeHelper, TypeInferer], ids=["analyzer", "emitter"])
def test_selection_join_keeps_exact_array_element_type(inferer_type):
    inferer = inferer_type()
    left = Identifier(name="left")
    right = Identifier(name="right")
    int_array = TypeSpec.array(TypeSpec.primitive("int"))
    assert inferer._selection_type_spec([
        (left, int_array), (right, int_array),
    ]) == int_array

    # Numeric scalar widening must not unify arrays with different elements.
    for element in ("float", "bool", "string"):
        other_array = TypeSpec.array(TypeSpec.primitive(element))
        assert inferer._selection_type_spec([
            (left, int_array), (right, other_array),
        ]) is None
        assert inferer._selection_type_spec([
            (left, other_array), (right, int_array),
        ]) is None


@pytest.mark.parametrize("choice, expected", [
    (0, "{-1, 2, 2, 2}"),
    (1, "{-1, 3, 3, 3}"),
])
def test_selected_array_history_compiles_and_reads_previous_size(choice, expected):
    # The explicit arm has two elements; the default arm has three.
    source = f'''//@version=6
strategy("Selection array history")
choice = input.int({choice})
var values = switch choice
    0 => array.from(2, 5)
    => array.from(7, 9, 11)
older = na(values[1]) ? -1 : (values[1]).size()
plot(older)
var observed = array.new_int()
observed.push(older)
'''
    cpp = transpile(source)
    driver = f'''
#include <cassert>
#include <iostream>
int main() {{
    GeneratedStrategy strategy;
    std::vector<Bar> feed;
    for (int i = 0; i < 4; ++i)
        feed.push_back(Bar{{1.0, 1.0, 1.0, 1.0, 1.0, i * int64_t{{60000}}}});
    strategy.run(feed.data(), 4);
    assert(strategy.last_error().empty());
    assert((strategy.observed == std::vector<int>{expected}));
    std::cout << "ok\\n";
}}
'''
    assert run_emitted_tu(
        cpp, driver, opt="-O2", label="selection array history",
    ) == "ok\n"
