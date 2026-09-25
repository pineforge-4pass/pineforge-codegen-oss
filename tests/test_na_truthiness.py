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

from pineforge_codegen import transpile, transpile_full

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


def test_int_cast_preserves_wide_integer_na_before_narrowing() -> None:
    pine = (
        '//@version=6\nstrategy("wide int na")\n'
        'var int stamp = time_close\n'
        'stamp := na\n'
        'var int narrowed = na\n'
        'narrowed := int(stamp)\n'
        'indexed = close[stamp]\n'
    )
    cpp = transpile(pine)
    assert "int64_t stamp = na<int64_t>();" in cpp
    assert "auto _pf_v = (stamp); return is_na(_pf_v)" in cpp
    assert "auto _pf_idx_v = (stamp)" in cpp
    driver = r"""
#include <cmath>
#include <cstdio>
#include <limits>
int main() {
    GeneratedStrategy s;
    pineforge::Bar b{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&b, 1);
    std::printf("%d %d\n", s.narrowed == std::numeric_limits<int>::min(),
                std::isnan(s.indexed));
}
"""
    out = run_emitted_tu(
        cpp, driver, opt="-O0", label="wide-int-na",
    )
    assert out.strip() == "1 1"


def test_integer_na_widens_into_timestamp_slot_and_calendar_arg() -> None:
    pine = (
        '//@version=6\nstrategy("wide timestamp na")\n'
        'var int stamp = time_close\n'
        'stamp := int(na)\n'
        'yearMissing = year(int(na))\n'
        'monthMissing = month(int(na))\n'
        'dayMissing = dayofmonth(int(na))\n'
        'weekdayMissing = dayofweek(int(na))\n'
        'hourMissing = hour(int(na))\n'
        'minuteMissing = minute(int(na))\n'
        'secondMissing = second(int(na))\n'
        'weekMissing = weekofyear(int(na))\n'
        'nyYearMissing = year(int(na), "America/New_York")\n'
        'nyMonthMissing = month(int(na), "America/New_York")\n'
        'nyDayMissing = dayofmonth(int(na), "America/New_York")\n'
        'nyWeekMissing = weekofyear(int(na), "America/New_York")\n'
    )
    driver = r"""
#include <cstdio>
int main() {
    GeneratedStrategy s;
    pineforge::Bar b{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&b, 1);
    std::printf("%d %d %d %d %d %d %d %d %d %d %d %d %d\n", is_na(s.stamp),
                s.yearMissing, s.monthMissing, s.dayMissing,
                s.weekdayMissing, s.hourMissing, s.minuteMissing,
                s.secondMissing, s.weekMissing, s.nyYearMissing,
                s.nyMonthMissing, s.nyDayMissing, s.nyWeekMissing);
}
"""
    out = run_emitted_tu(
        transpile(pine), driver, opt="-O0", label="wide-timestamp-na"
    )
    assert out.strip() == "1 1970 1 1 5 0 0 0 1 1969 12 31 1"


_COLOR_PINE = """//@version=6
strategy("color na conversions")
c1 = color.new(color.red, na)
c2 = color.new(na, 20)
c3 = color.rgb(na, 0, 0, 10)
c4 = color.rgb(255, 0, 0, na)
color explicitNa = na
"""

_COLOR_DRIVER = r"""
#include <cstdio>
int main() {
    GeneratedStrategy s;
    pineforge::Bar b{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&b, 1);
    std::printf("%d %d %d %d %d %d\n", is_na(s.c1), is_na(s.c2),
                pine_color::t(s.c1), pine_color::t(s.c4),
                pine_color::r(s.c3), is_na(s.explicitNa));
}
"""


def test_color_na_conversions_match_tradingview_rows() -> None:
    """The covered `lab tv` color probe pins these five observable rows.

    Missing alpha means transparency 100, a missing RGB channel becomes 0,
    and a missing base color remains `na`.  The sixth row pins the typed
    color slot's 64-bit sentinel, which was formerly lost in a double slot.
    """
    out = run_emitted_tu(
        transpile(_COLOR_PINE), _COLOR_DRIVER, opt="-O0", label="color-na"
    )
    assert out.strip() == "0 1 100 100 0 1"


_PERCENTILE_PINE = """//@version=6
strategy("percentile na conversions")
a = array.from(1.0, 2.0, 3.0)
nearest = array.percentile_nearest_rank(a, na)
linear = array.percentile_linear_interpolation(a, na)
"""

_PERCENTILE_DRIVER = r"""
#include <cmath>
#include <cstdio>
int main() {
    GeneratedStrategy s;
    pineforge::Bar b{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&b, 1);
    std::printf("%.0f %d\n", s.nearest, std::isnan(s.linear));
}
"""


def test_percentile_na_rows_match_tradingview() -> None:
    """TV returns the first sorted value for nearest-rank(na), and na for linear."""
    out = run_emitted_tu(
        transpile(_PERCENTILE_PINE), _PERCENTILE_DRIVER,
        opt="-O0", label="percentile-na",
    )
    assert out.strip() == "1 1"


_BOOL_SLOTS_PINE = """//@version=6
strategy("bool slot na")
type Holder
    bool flag
src = ta.sma(close, 20)
bool plain = src
var bool persistent = false
persistent := src
h = Holder.new(src)
a = array.new_bool(1, src)
m = matrix.new<bool>(1, 1, src)
m.set(0, 0, src)
"""

_BOOL_SLOTS_DRIVER = r"""
#include <cstdio>
int main() {
    GeneratedStrategy s;
    pineforge::Bar b{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&b, 1);
    std::printf("%d %d %d %d\n", (int)s.plain, (int)s.persistent,
                (int)(bool)s.a[0], (int)s.m.get(0, 0));
}
"""


def test_na_into_scalar_and_collection_bool_slots_is_false() -> None:
    """An na-valued SMA must not become true at any emitted bool slot."""
    out = run_emitted_tu(
        transpile(_BOOL_SLOTS_PINE), _BOOL_SLOTS_DRIVER,
        opt="-O0", label="bool-slots-na",
    )
    assert out.strip() == "0 0 0 0"


def test_bool_series_history_alias_keeps_bool_storage() -> None:
    cpp = transpile(
        '//@version=6\nstrategy("bool history alias")\n'
        'flag = close > open\nprior = flag[1]\n'
        'signal = flag and prior\n'
    )
    assert "Series<bool> flag" in cpp
    assert "bool prior = false;" in cpp
    assert "prior = flag[1];" in cpp


def test_repeat_with_na_count_warns_about_nullable_string_gap() -> None:
    result = transpile_full(
        '//@version=6\nstrategy("repeat na")\n'
        's = str.repeat("x", int(na))\n'
    )
    warnings = [d.message for d in result["diagnostics"]]
    assert any("str.repeat count can be na" in message for message in warnings)


def test_repeat_keyword_count_and_invalid_number_spelling() -> None:
    valid = transpile_full(
        '//@version=6\nstrategy("repeat keyword")\n'
        's = str.repeat("x", count=int(na))\n'
    )
    assert any("str.repeat count can be na" in d.message
               for d in valid["diagnostics"])
    # TradingView rejects `number=`. This was an uncaught IndexError in the
    # visitor; keep the prior no-refusal contract with an explicit warning.
    invalid = transpile_full(
        '//@version=6\nstrategy("repeat invalid keyword")\n'
        's = str.repeat("x", number=int(na))\n'
    )
    assert any("missing its count argument" in d.message
               for d in invalid["diagnostics"])


def test_collection_history_gap_warns_without_refusing() -> None:
    result = transpile_full(
        '//@version=6\nstrategy("collection history gap")\n'
        'a = array.from(1.0, 2.0)\nv = a[1]\n'
    )
    assert any("array history indexing uses the current collection" in d.message
               for d in result["diagnostics"])


_REPLACE_PINE = """//@version=6
strategy("replace na occurrence")
s = str.replace("aba", "a", "z", int(na))
"""

_REPLACE_DRIVER = r"""
#include <cstdio>
int main() {
    GeneratedStrategy s;
    pineforge::Bar b{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&b, 1);
    std::printf("%s\n", s.s.c_str());
}
"""


def test_replace_na_occurrence_uses_first_match_as_tradingview_does() -> None:
    out = run_emitted_tu(
        transpile(_REPLACE_PINE), _REPLACE_DRIVER,
        opt="-O0", label="replace-na-occurrence",
    )
    assert out.strip() == "zba"
