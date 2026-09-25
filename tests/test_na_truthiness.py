"""Pine's two conversion rules at numeric boundaries.

The engine represents floating ``na`` as NaN and integer ``na`` as the type's
minimum value.  C++ truthiness is different for both values, so these tests
pin the generated helper at every boolean boundary that used to rely on an
implicit conversion.  The runtime row is the compact semantic witness for
``if`` / ternary / ``and`` / ``or``; the TradingView probe used for the lane is
kept in ``/tmp/CG-NABOOL-scratch`` because campaign tapes are evidence, not
repository fixtures.
"""

from __future__ import annotations

from pineforge_codegen import transpile

from tests._compile import run_emitted_tu


TRUTH_PINE = """//@version=6
strategy("na truth")
var bool ifNa = false
var bool ternaryNa = false
var bool andNa = false
var bool orNa = false
var bool notNa = false
if na
    ifNa := true
ternaryNa := na ? true : false
andNa := na and true
orNa := na or true
notNa := not na
"""


def test_numeric_boolean_boundaries_use_pine_truthiness() -> None:
    cpp = transpile(TRUTH_PINE)
    # A numeric condition is guarded once at each boundary.  The explicit
    # bool() path shares the same helper, so there is no second NaN-truth rule.
    assert cpp.count("_pf_bool_v") >= 5
    assert "is_na(_pf_bool_v) ? false : (_pf_bool_v != 0)" in cpp
    assert "if (na<double>())" not in cpp


def test_int_na_is_reified_as_integer_sentinel() -> None:
    cpp = transpile(
        "//@version=6\nstrategy(\"na int\")\n"
        "var int slot = int(na)\n"
    )
    assert "is_na(_pf_v) ? na<int>() : (int)_pf_v" in cpp
    assert "(int)(na<double>())" not in cpp


_TRUTH_DRIVER = r"""
#include <cstdio>
int main() {
    GeneratedStrategy s;
    pineforge::Bar b{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&b, 1);
    std::printf("%d %d %d %d %d\n", s.ifNa, s.ternaryNa, s.andNa, s.orNa, s.notNa);
}
"""


def test_na_truth_rows_match_pine_false_semantics() -> None:
    out = run_emitted_tu(
        transpile(TRUTH_PINE), _TRUTH_DRIVER, opt="-O0", label="na-truth"
    )
    # na is false in every boolean context: the final `or true` is true,
    # while the other three rows remain false; `not na` is true.
    assert out.strip() == "0 0 0 1 1"


_INT_DRIVER = r"""
#include <cstdio>
#include <limits>
int main() {
    GeneratedStrategy s;
    pineforge::Bar b{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&b, 1);
    std::printf("%d\n", s.slot == std::numeric_limits<int>::min());
}
"""


def test_int_na_runtime_row_is_integer_na() -> None:
    out = run_emitted_tu(
        transpile(
            "//@version=6\nstrategy(\"na int\")\n"
            "var int slot = int(na)\n"
        ),
        _INT_DRIVER,
        opt="-O0",
        label="int-na",
    )
    assert out.strip() == "1"
