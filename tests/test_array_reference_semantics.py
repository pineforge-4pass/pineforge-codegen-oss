"""Array field identity (missing ID, shallow object copy) and detached array copies, observed through emitted strategies."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("array references")\n'
DRIVER = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bars[] = {
        {100.0, 104.0, 99.0, 103.0, 10.0, 1743466500000LL},
        {103.0, 105.0, 98.0, 102.0, 11.0, 1743467400000LL},
    };
    strategy.run(bars, 2);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    std::cout << strategy.observed << '\n';
}
'''


def _observe(body: str) -> str:
    return run_emitted_tu(
        transpile(HEADER + body), DRIVER, opt="-O0",
        label="array reference semantics",
    ).strip()


@pytest.mark.parametrize("call", ["a.copy()", "array.copy(a)"])
def test_array_copy_stays_detached(call):
    assert _observe(f'''a = array.from(10, 20)
copied = {call}
copied.set(0, 99)
observed = a.get(0)
''') == "10"


@pytest.mark.parametrize("element", ["int", "float", "bool", "string"])
@pytest.mark.parametrize("construction", [
    "Book.new()", "Book.new(na)", "Book.new(stamps = na)",
])
def test_missing_array_field_has_na_id(element, construction):
    assert _observe(f'''type Book
    array<{element}> stamps
b = {construction}
observed = na(b.stamps) ? 1 : 0
''') == "1"


@pytest.mark.parametrize("element", ["int", "float", "bool", "string"])
def test_empty_array_field_has_non_na_id(element):
    assert _observe(f'''type Book
    array<{element}> stamps
b = Book.new(array.new<{element}>())
observed = na(b.stamps) ? 0 : 1
''') == "1"


def test_missing_and_valid_fields_in_same_record():
    assert _observe('''type Book
    array<int> stamps
implicit = Book.new()
explicit = Book.new(stamps = na)
present = Book.new(array.new<int>())
observed = na(implicit.stamps) and na(explicit.stamps) and not na(present.stamps) ? 1 : 0
''') == "1"


def test_missing_array_field_method_stops_safely():
    cpp = transpile(HEADER + '''type Book
    array<int> stamps
b = Book.new()
observed = b.stamps.size()
''')
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bar{100.0, 104.0, 99.0, 103.0, 10.0, 1743466500000LL};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << '\n';
}
'''
    error = run_emitted_tu(
        cpp, driver, opt="-O0", label="missing array field access",
    )
    assert "ID of array is 'na'" in error


def test_wide_field_can_be_na_and_can_be_rebound():
    assert _observe('''type Book
    array<int> stamps
missing = Book.new(stamps = na)
present = Book.new(array.new<int>(1, time))
bool startedMissing = na(missing.stamps)
missing.stamps := array.new<int>(1, time)
present.stamps := na
observed = startedMissing and not na(missing.stamps) and missing.stamps.get(0) == time and na(present.stamps) ? 1 : 0
''') == "1"


def test_array_field_default_na_keeps_id_state():
    # A field default of na is a constant TradingView accepts, and the field reads na.
    # A non-constant default such as array.new<int>() is not pinned here: TradingView
    # refuses that declaration at compile time (CE10133, "The default value cannot be a
    # function, variable or calculation"), so PineForge's answer to it is not a contract.
    assert _observe('''type Book
    array<int> stamps = na
b = Book.new()
observed = na(b.stamps) ? 1 : 0
''') == "1"


@pytest.mark.parametrize("copy", ["b.copy()", "Book.copy(b)"])
@pytest.mark.parametrize("construction", [
    "array.from(10)", "array.new<int>(1, time)",
])
def test_object_copy_shares_array_field(copy, construction):
    assert _observe(f'''type Book
    array<int> stamps
b = Book.new({construction})
other = {copy}
other.stamps.set(0, 99)
observed = b.stamps.get(0)
''') == "99"


def test_object_copy_of_copy_shares_array_field():
    assert _observe('''type Book
    array<int> stamps
b = Book.new(array.from(10))
other = b.copy()
third = other.copy()
third.stamps.set(0, 99)
observed = b.stamps.get(0) == 99 and other.stamps.get(0) == 99 ? 1 : 0
''') == "1"


def test_object_copy_rebind_detaches_only_its_own_field():
    assert _observe('''type Book
    array<int> stamps
b = Book.new(array.from(10))
other = b.copy()
other.stamps := array.from(77)
other.stamps.set(0, 99)
observed = b.stamps.get(0) == 10 and other.stamps.get(0) == 99 ? 1 : 0
''') == "1"


def test_object_copy_shares_stable_array_alias():
    assert _observe('''type Book
    array<int> stamps
var source = array.new<int>(1, 10)
b = Book.new(source)
other = b.copy()
other.stamps.set(0, 99)
observed = source.get(0) == 99 and b.stamps.get(0) == 99 ? 1 : 0
''') == "1"


def test_object_copy_preserves_missing_field():
    assert _observe('''type Book
    array<int> stamps
b = Book.new()
other = b.copy()
observed = na(b.stamps) and na(other.stamps) ? 1 : 0
''') == "1"


def test_copy_keeps_scalar_fields_independent_alongside_shared_array():
    assert _observe('''type Book
    array<int> stamps
    int count
b = Book.new(array.from(10), 3)
other = b.copy()
other.stamps.set(0, 99)
other.count := 7
observed = b.stamps.get(0) == 99 and b.count == 3 and other.count == 7 ? 1 : 0
''') == "1"


def test_object_without_array_fields_copies_values():
    assert _observe('''type Reading
    int value
b = Reading.new(10)
other = b.copy()
other.value := 99
observed = b.value == 10 and other.value == 99 ? 1 : 0
''') == "1"


@pytest.mark.parametrize("copy", ["b.copy()", "Book.copy(b)"])
def test_retained_object_array_copy_still_refused(copy):
    with pytest.raises(
        CompileError,
        match=r"Book\.copy\(\) is unsupported because direct array field\(s\) stamps",
    ):
        transpile(HEADER + f'''type Book
    array<int> stamps
var b = Book.new(array.from(10))
other = {copy}
''')
