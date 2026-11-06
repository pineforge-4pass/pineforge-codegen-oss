"""Where a ``var`` array is built.

A ``var`` array declared inside a block was built nowhere (the first-bar
preamble skipped it and no declaration-site statement existed), so it stayed
an empty array. A ``var`` array whose initializer reads an input the script
later reassigns was built in the preamble, before that input was read, so it
held 0 where Pine holds the input's value. Both are now built by a one-shot
statement at the declaration; a top-level ``var`` aggregate that reads only
constants, bar fields or inputs the script keeps stays in the preamble.
"""

import re

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("var aggregate init", initial_capital = 100000)\n'


def _preamble(cpp: str) -> str:
    start = cpp.index("if (!_var_initialized) {")
    return cpp[start:cpp.index("_var_initialized = true;", start)]


def test_var_array_in_a_block_is_built_where_the_block_first_reaches_it():
    cpp = transpile(
        HEADER
        + "float seen = -1000\n"
        "if bar_index >= 8\n"
        "    var arr = array.new<float>(size=2, initial_value=float(bar_index + 2))\n"
        "    seen := arr.get(0)\n"
        "plot(seen)\n"
    )
    assert re.search(
        r"if \(!_pf_var_init_arr\) \{\n[^\n]*\barr\b[^\n]*_pf_array_new_size", cpp
    ), cpp
    assert "_pf_array_new_size" not in _preamble(cpp)


def test_var_array_reading_a_reassigned_input_is_built_after_the_input_is_read():
    cpp = transpile(
        HEADER
        + 'x = input.float(2.0, "Seed", minval=1.0, maxval=4.0)\n'
        "var float[] values = array.new_float(2, x)\n"
        "x := x * 2.0\n"
        "plot(array.get(values, 0))\n"
    )
    read = cpp.index('x = get_input_double("Seed", 2.0);')
    build = cpp.index("_pf_array_new_size")
    assert read < build
    assert re.search(
        r"if \(!_pf_var_init_values\) \{\n[^\n]*\bvalues\b[^\n]*_pf_array_new_size", cpp
    ), cpp


def test_top_level_var_array_without_a_statement_read_stays_in_the_preamble():
    cpp = transpile(
        HEADER
        + "var a = array.new<float>(size=2, initial_value=float(bar_index + 2))\n"
        "plot(a.get(0))\n"
    )
    # ad7bb8c stages keyword arguments once, in parameter order.
    assert (
        "a = ([&](){ auto _pf_array_new_size_arg = (2); "
        "auto _pf_array_new_initial_value_arg = ((double)((pine_bar_index() + 2))); "
        "return [&](auto _pf_array_new_size, auto _pf_array_new_value){"
    ) in _preamble(cpp)
    assert "_pf_var_init_a" not in cpp


def test_var_array_reading_an_input_the_script_keeps_stays_in_the_preamble():
    cpp = transpile(
        HEADER
        + 'n = input.int(3, "N", minval=1, maxval=5)\n'
        "var arr = array.new_float(n, 0.0)\n"
        "plot(arr.size())\n"
    )
    assert "_pf_var_init_arr" not in cpp
    assert "arr = [&](auto _pf_array_new_size" in _preamble(cpp)
