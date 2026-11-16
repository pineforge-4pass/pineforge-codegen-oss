"""String history locals keep their scalar type in concatenation operands."""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Phase
from tests._compile import compile_cpp, run_emitted_tu


_REFERENCE_JOIN = re.compile(r"std::to_string\((\w+)\[0\]\) \+ (\w+)\)")


def with_string_join_fixed(cpp):
    """The C++ of a codegen older than this fix, with its one wrong join repaired.

    The older emission wrapped the current value of a string local read with
    history in std::to_string when joining it with its previous value (issue
    #354), which does not compile. A test that compares a new emission with an
    older one passes only when that single site is the whole difference.
    """
    assert len(_REFERENCE_JOIN.findall(cpp)) == 1, cpp
    return _REFERENCE_JOIN.sub(r"\1[0] + \2)", cpp)


_REPRODUCTION = """//@version=6
strategy("strlocal-history")
h() =>
    aa = str.tostring(close)
    bb = aa[1]
    aa + bb
v = h()
plot(str.length(v))
"""

_TWO_SITES = """//@version=6
strategy("strlocal-history")
h(float value) =>
    aa = str.tostring(value)
    bb = aa[1]
    aa + bb
v = h(close)
w = h(open)
plot(str.length(v) + str.length(w))
"""

_CHART = """//@version=6
strategy("strlocal-history")
aa = str.tostring(close)
bb = aa[1]
v = aa + bb
plot(str.length(v))
"""

# Closes are 10,20,30; current+previous on bars 1,2 is "2010","3020".
_FORWARD = ("2010", "3020")
# The same rule for opens 1,2,3 gives "21","32" at the independent call site.
_TWO_SITE_VALUES = ("2010 21", "3020 32")
# Swapping the operands puts the previous close before the current close.
_BACKWARD = ("1020", "2030")

_HISTORY_CASES = [
    pytest.param(_REPRODUCTION, "strategy.v", _FORWARD, False, id="reproduction"),
    # A builtin spelling must still read the local string, not the clock.
    pytest.param(
        _REPRODUCTION.replace("aa", "second"),
        "strategy.v", _FORWARD, False, id="builtin-second",
    ),
    pytest.param(
        _REPRODUCTION.replace("aa", "hour"),
        "strategy.v", _FORWARD, False, id="builtin-hour",
    ),
    # A string annotation changes neither the current value nor its history.
    pytest.param(
        _REPRODUCTION.replace("    aa =", "    string aa ="),
        "strategy.v", _FORWARD, False, id="typed-local",
    ),
    # The close replaces the open before the read; history holds prior closes.
    pytest.param(
        _REPRODUCTION.replace(
            "    aa = str.tostring(close)",
            "    aa = str.tostring(open)\n    aa := str.tostring(close)",
        ),
        "strategy.v", _FORWARD, False, id="reassigned-local",
    ),
    pytest.param(
        _TWO_SITES, 'strategy.v << " " << strategy.w',
        _TWO_SITE_VALUES, False, id="two-call-sites",
    ),
    # Moving the declarations to chart scope keeps current+previous unchanged.
    pytest.param(_CHART, "strategy.v", _FORWARD, False, id="chart-scope"),
    pytest.param(
        _REPRODUCTION.replace("    aa + bb", "    bb + aa"),
        "strategy.v", _BACKWARD, True, id="swapped-operands",
    ),
]


_DRIVER = r"""
#include <iostream>

int main() {
    const pineforge::Bar bars[] = {
        {1.0, 10.0, 1.0, 10.0, 1.0, 1743466500000LL},
        {2.0, 20.0, 2.0, 20.0, 1.0, 1743466560000LL},
        {3.0, 30.0, 3.0, 30.0, 1.0, 1743466620000LL},
    };
    for (int count = 2; count <= 3; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 2;
        }
        std::cout << OBSERVED_VALUE << '\n';
    }
}
"""


def _compile_and_check_values(cpp, observed, expected):
    compile_cpp(cpp, label="string-local-history-concat")
    output = run_emitted_tu(
        cpp, _DRIVER.replace("OBSERVED_VALUE", observed),
        opt="-O0", label="string-local-history-concat",
    )
    assert output.splitlines() == list(expected)


@pytest.mark.parametrize("source,observed,expected,swapped", _HISTORY_CASES)
def test_history_string_operands_compile_and_return_the_join(
    source, observed, expected, swapped,
):
    cpp = transpile(source)
    assert "std::to_string(aa" not in cpp
    # Recover allocated storage names from the actual previous-value reads,
    # including builtin spellings and each independent call-site clone.
    members = re.findall(r"\bbb = ([A-Za-z_][A-Za-z_0-9]*)\[1\];", cpp)
    assert members, cpp
    for member in members:
        assert f"std::to_string({member}" not in cpp
        expression = (
            f"(bb + {member}[0])" if swapped else f"({member}[0] + bb)"
        )
        assert expression in cpp
    # Bar 0 executes to seed history. Only bars with an existing previous
    # value are asserted; this does not assume a string-na concatenation rule.
    _compile_and_check_values(cpp, observed, expected)


@pytest.mark.parametrize("name", [
    "open", "high", "low", "close", "hl2", "hlc3", "hlcc4", "ohlc4", "volume",
])
def test_requested_price_named_string_history_keeps_its_refusal(name):
    from tests.test_builtin_named_locals_price_refusal import assert_price_refusal

    source = _REPRODUCTION.replace("aa", name).replace(
        "v = h()", 'v = request.security(syminfo.tickerid, "60", h())',
    )
    diagnostic = assert_price_refusal(source)
    # The history operator is on line 5, immediately after the local's name.
    assert diagnostic.location.line == 5
    assert diagnostic.location.col == len("    bb = ") + len(name) + 1


_CONVERSION_GUARDS = [
    pytest.param(
        "str.tostring(1)", "pine_str_tostring_tv(1,",
        ("201", "301"), id="explicit-string-conversion",
        # tostring(1) is "1"; appending it to closes 20,30 gives "201","301".
    ),
    pytest.param(
        "na", "std::to_string(na<double>())",
        ("20nan", "30nan"), id="numeric-na-conversion",
        # Preserve test_na_numeric_selection's numeric-na text "nan", appended
        # to closes 20,30; this pins existing emission, not Pine string-na.
    ),
]


@pytest.mark.parametrize("operand,fragment,expected", _CONVERSION_GUARDS)
def test_existing_string_and_numeric_conversions_are_preserved(
    operand, fragment, expected,
):
    # No history promotion here: both guards already compile on the old path.
    source = _REPRODUCTION.replace(
        "    bb = aa[1]\n    aa + bb", f"    aa + {operand}",
    )
    cpp = transpile(source)
    assert fragment in cpp
    assert "std::to_string(aa" not in cpp
    _compile_and_check_values(cpp, "strategy.v", expected)


def test_bare_integer_still_cannot_be_added_to_a_string_history_local():
    # The analyzer's string/string + overload cannot accept an integer 1.
    source = _REPRODUCTION.replace("    aa + bb", "    aa + 1")
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == "PF-E2066"
    assert diagnostic.args == {"op": "+", "left": "string", "right": "int"}
