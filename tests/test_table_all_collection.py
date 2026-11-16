"""Table collection receivers retain handles through native array operations."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


_HEADER = '//@version=6\nstrategy("Table collections", overlay=true)\n'
_COUNT_DRIVER = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        {1, 1, 1, 1, 1, 0},
        {2, 2, 2, 2, 1, 60000},
        {3, 3, 3, 3, 1, 120000},
    };
    for (int run = 0; run < 2; ++run) {
        strategy.run(bars, 3, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
        std::cout << strategy.count << "\n";
    }
}
"""


@pytest.mark.parametrize("creation, expected", [
    ("", 0),
    (
        "var table first = table.new(position.top_right, 1, 1)\n"
        "var table second = table.new(position.bottom_left, 1, 1)\n",
        2,
    ),
])
def test_direct_table_all_size_compiles_and_counts_live_tables(creation, expected):
    cpp = transpile(_HEADER + creation + "count = array.size(table.all)\nplot(count)\n")
    compile_cpp(cpp, label="direct table collection")
    result = run_emitted_tu(cpp, _COUNT_DRIVER, opt="-O2", label="direct table collection")
    assert list(map(float, result.split())) == [expected, expected]


def test_table_all_alias_after_delete_compiles_and_counts_no_tables():
    cpp = transpile(_HEADER + """\
var table panel = table.new(position.top_right, 1, 1)
table.delete(panel)
handles = table.all
count = array.size(handles)
plot(count)
""")
    compile_cpp(cpp, label="table collection alias after delete")
    result = run_emitted_tu(
        cpp, _COUNT_DRIVER, opt="-O2", label="table collection alias after delete",
    )
    assert list(map(float, result.split())) == [0, 0]


def test_table_all_handles_support_get_clear_and_iteration():
    cpp = transpile(_HEADER + """\
var table first = table.new(position.top_right, 1, 1)
var table second = table.new(position.bottom_left, 1, 1)
handles = table.all
if barstate.isfirst
    table.delete(array.get(handles, 0))
    array.clear(handles)
    for handle in table.all
        handle.delete()
count = table.all.size() + handles.size()
plot(count)
""")
    compile_cpp(cpp, label="table collection consumers")
    result = run_emitted_tu(
        cpp, _COUNT_DRIVER, opt="-O2", label="table collection consumers",
    )
    assert list(map(float, result.split())) == [0, 0]
