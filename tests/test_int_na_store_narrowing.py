"""An int that can be na crosses into int storage with its na kept.

C1 declares an int global that can be na (``int q = switch ... => na``) as a
``double`` (NaN is its na). Every store of it into an integer element, tuple
slot or parameter must narrow na-preserving: an implicit double to int
conversion of NaN is undefined and gave 0 on the engine runs, where Pine
stores na. A store of a present int keeps its spelling.
"""

import pytest

from pineforge_codegen import transpile

HEADER = (
    '//@version=6\nstrategy("int na stores", initial_capital = 100000)\n'
    'missing = input.bool(true, "Missing")\n'
    'fixed = input.int(2, "Fixed", minval = 1, maxval = 2)\n'
)
Q_SWITCH = "int q = switch\n    not missing => fixed\n    => na\n"
# The cast ``na_preserving_int_cast`` spells for a double operand named q.
Q_CAST = (
    "auto _pf_v = (q); if constexpr (std::is_floating_point_v<decltype(_pf_v)>) "
    "return (_pf_v >= -2147483648.0 && _pf_v < 2147483648.0) ? (int)_pf_v : na<int>();"
)


def _emitted_line(cpp: str, needle: str) -> str:
    lines = [line for line in cpp.splitlines() if needle in line]
    assert len(lines) == 1, (needle, lines)
    return lines[0]


ARRAY_READ = "float t = na(arr.get(0)) ? 1.0 : 0.0\n"
MATRIX_READ = "float t = na(m.get(0, 0)) ? 1.0 : 0.0\n"
MAP_READ = 'float t = na(mp.get("q")) ? 1.0 : 0.0\n'


@pytest.mark.parametrize(
    "body, needle",
    [
        pytest.param(
            "arr = array.new_int(1, q)\n" + ARRAY_READ,
            "arr = [&](auto _pf_array_new_size", id="array-new-int",
        ),
        pytest.param(
            "arr = array.new<int>(1, q)\n" + ARRAY_READ,
            "arr = [&](auto _pf_array_new_size", id="array-new-generic",
        ),
        pytest.param(
            "arr = array.new_int(0)\narr.push(q)\n" + ARRAY_READ,
            "arr.push_back(", id="array-push",
        ),
        pytest.param(
            "arr = array.new_int(0)\narr.unshift(q)\n" + ARRAY_READ,
            "arr.insert(arr.begin(),", id="array-unshift",
        ),
        pytest.param(
            "arr = array.new_int(1, 0)\narr.set(0, q)\n" + ARRAY_READ,
            '"index_out_of_bounds", "set"', id="array-set",
        ),
        pytest.param(
            "arr = array.new_int(1, 0)\narr.fill(q)\n" + ARRAY_READ,
            "std::fill(arr.begin(), arr.end(),", id="array-fill",
        ),
        pytest.param(
            "arr = array.new_int(0)\narr.insert(0, q)\n" + ARRAY_READ,
            '"index_out_of_bounds", "insert"', id="array-insert",
        ),
        pytest.param(
            "arr = array.from(q, q)\n" + ARRAY_READ,
            "arr = std::vector<int>{", id="array-from",
        ),
        pytest.param(
            "m = matrix.new<int>(1, 1, q)\n" + MATRIX_READ,
            "m = PineGenericMatrix<int>::new_(1, 1,", id="matrix-new",
        ),
        pytest.param(
            "m = matrix.new<int>(1, 1, 0)\nm.set(0, 0, q)\n" + MATRIX_READ,
            "m.set((int)(0), (int)(0),", id="matrix-set",
        ),
        pytest.param(
            "m = matrix.new<int>(1, 1, 0)\nm.fill(q)\n" + MATRIX_READ,
            "m.fill(", id="matrix-fill",
        ),
        pytest.param(
            'mp = map.new<string, int>()\nmp.put("q", q)\n' + MAP_READ,
            "put(__pf_map_param_arg_0", id="map-put",
        ),
    ],
)
def test_int_held_as_double_is_stored_na_preserving(body, needle):
    cpp = transpile(HEADER + Q_SWITCH + body)
    assert "double q = 0.0;" in cpp
    assert Q_CAST in _emitted_line(cpp, needle)


@pytest.mark.parametrize(
    "body, needle",
    [
        pytest.param(
            "arr = array.new_int(0)\narr.push(fixed)\n" + ARRAY_READ,
            "arr.push_back(", id="array-push-input",
        ),
        pytest.param(
            "arr = array.new_int(1, 5)\n" + ARRAY_READ,
            "arr = [&](auto _pf_array_new_size", id="array-new-literal",
        ),
        pytest.param(
            "arr = array.from(fixed, 3)\n" + ARRAY_READ,
            "arr = std::vector<int>{", id="array-from-present",
        ),
        pytest.param(
            "m = matrix.new<int>(1, 1, fixed)\n" + MATRIX_READ,
            "m = PineGenericMatrix<int>::new_(1, 1,", id="matrix-new-input",
        ),
        pytest.param(
            'mp = map.new<string, int>()\nmp.put("k", fixed)\n' + MAP_READ,
            "put(__pf_map_param_arg_0", id="map-put-input",
        ),
    ],
)
def test_store_of_a_present_int_keeps_its_spelling(body, needle):
    cpp = transpile(HEADER + body)
    line = _emitted_line(cpp, needle)
    assert "auto _pf_v" not in line
    assert "2147483648.0" not in line


PAIR_SOURCE = (
    '//@version=6\nstrategy("tuple na", initial_capital = 100000)\n'
    "pair(bool missing, int fixed) =>\n"
    "    int q = switch\n        not missing => fixed\n        => na\n"
    "    [q, fixed + 5]\n"
    'missing = input.bool(true, "Missing")\n'
    'fixed = input.int(2, "Fixed", minval = 1, maxval = 2)\n'
    "[x, tag] = pair(missing, fixed)\n"
    "float code = na(x) ? 1.0 : 2.0\n"
)


def test_tuple_return_narrows_an_int_held_as_double():
    cpp = transpile(PAIR_SOURCE)
    line = _emitted_line(cpp, "return std::make_tuple(")
    assert "return std::make_tuple([&](){ " + Q_CAST in line


UDT_SOURCE = (
    '//@version=6\nstrategy("udt na field", initial_capital = 100000)\n'
    "type Size\n    int value\n"
    "method read(Size self, int value) =>\n    na(self.value) ? self.value : value\n"
    'missing = input.bool(true, "Missing")\n'
    'fixed = input.int(2, "Fixed", minval = 1, maxval = 2)\n'
    "int q = switch\n    not missing => fixed\n    => na\n"
    "s = Size.new(q)\n"
    "x = s.read(s.value)\n"
    "float code = na(x) ? 1.0 : 2.0\n"
)


def test_udt_int_field_that_may_hold_na_keeps_it_through_parameter_and_return():
    cpp = transpile(UDT_SOURCE)
    call = _emitted_line(cpp, "x = _udt_Size_read(")
    assert (
        "_udt_Size_read(s, [&](){ auto _pf_v = (_pf_udt_Size.read(s).value);" in call
    )
    returned = _emitted_line(cpp, "is_na(_pf_udt_Size.read(self).value)")
    assert "return [&](){ auto _pf_v = (" in returned
    assert "na<int>()" in returned


def test_udt_int_field_that_cannot_hold_na_keeps_its_spelling():
    cpp = transpile(
        '//@version=6\nstrategy("udt present field", initial_capital = 100000)\n'
        "type Counter\n    int n\n"
        "method read(Counter self, int extra) =>\n    na(self.n) ? self.n : extra\n"
        'fixed = input.int(2, "Fixed", minval = 1, maxval = 2)\n'
        "c = Counter.new(fixed)\n"
        "x = c.read(c.n)\n"
        "float code = na(x) ? 1.0 : 2.0\n"
    )
    call = _emitted_line(cpp, "x = _udt_Counter_read(")
    assert "_pf_v" not in call
    returned = _emitted_line(cpp, "is_na(_pf_udt_Counter.read(self).n)")
    assert "_pf_v" not in returned
