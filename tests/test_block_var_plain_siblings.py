"""Persistent block state is independent of a plain sibling declaration."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from tests._e2e import reference_codegen, transpile_json
from tests._legacy_cpp import legacy_cpp
from tests.test_e2e_series_var_init import _SHAPES as _SERIES_INITIALIZERS
from tests.test_runtime_var_initialization import _RUNTIME_PINE, _compile_and_run
from tests.test_udf_series_parameter_history import _SOURCE as _ARGUMENT_HISTORY


CPP_REFERENCE = "a156a7d2785c8729e54da7eb01c5d41ddd03df14"
_CLOSES = tuple(100.0 + index for index in range(120))

# The source and its distinct-name control are kept verbatim.
_SOURCE = """//@version=6
strategy("var in sibling if block", overlay = true)
var int n = -1
if time >= timestamp("UTC", 2024, 1, 1, 0, 0)
    n += 1
var float kept = na
var float other = na
if n >= 0 and n % 10 == 8
    sp = close * 2
    other := sp
if n >= 0 and n % 10 == 9
    var float sp = close
    kept := sp
if n == 99
    strategy.entry("L", strategy.long, qty = kept == close[90] ? 0.01 : 0.02)
if n == 100
    strategy.close_all()
"""

_CONTROL = """//@version=6
strategy("var in sibling if block", overlay = true)
var int n = -1
if time >= timestamp("UTC", 2024, 1, 1, 0, 0)
    n += 1
var float kept = na
var float other = na
if n >= 0 and n % 10 == 8
    sp = close * 2
    other := sp
if n >= 0 and n % 10 == 9
    var float sp2 = close
    kept := sp2
if n == 99
    strategy.entry("L", strategy.long, qty = kept == close[90] ? 0.01 : 0.02)
if n == 100
    strategy.close_all()
"""

_PLAIN_BLOCK = """if n >= 0 and n % 10 == 8
    sp = close * 2
    other := sp
"""


def _run(source: str, *members: str, closes: tuple = _CLOSES) -> tuple[float, ...]:
    prices = ", ".join(repr(value) for value in closes)
    output = ' << " " << '.join(f"strategy.{name}" for name in members)
    driver = f"""
#include <iomanip>
#include <iostream>

int main() {{
    const double closes[] = {{{prices}}};
    Bar bars[{len(closes)}];
    for (int i = 0; i < {len(closes)}; ++i) {{
        const double value = closes[i];
        bars[i] = Bar{{value, value, value, value, 1.0,
                      1704067200000LL + i * 3600000LL}};
    }}
    GeneratedStrategy strategy;
    strategy.run(bars, {len(closes)});
    if (!strategy.last_error().empty()) {{
        std::cerr << strategy.last_error();
        return 2;
    }}
    std::cout << std::setprecision(17) << {output} << '\\n';
}}
"""
    return tuple(float(value) for value in _compile_and_run(
        transpile(source) + driver,
    ).split())


@pytest.fixture(scope="module")
def distinct_name_values():
    return _run(_CONTROL, "kept", "other", "n")


def test_distinct_names_keep_the_first_value(distinct_name_values):
    assert distinct_name_values == (_CLOSES[9], 2 * _CLOSES[118], 119)


def test_sibling_var_matches_the_verbatim_control(distinct_name_values):
    observed = _run(_SOURCE, "kept", "other", "n")
    assert observed == distinct_name_values
    assert observed[0] == _CLOSES[9]


def _variant(source: str, shape: str) -> str:
    if shape == "var-before-plain":
        source = source.replace(_PLAIN_BLOCK, "").replace(
            "if n == 99\n", _PLAIN_BLOCK + "if n == 99\n",
        )
    elif shape == "varip":
        source = source.replace("var float sp", "varip float sp")
    elif shape == "plain-var-plain":
        source = source.replace(
            "var float other = na\n",
            "var float other = na\nvar float third = na\n",
        ).replace(
            "if n == 99\n",
            "if n >= 0 and n % 10 == 0\n"
            "    sp = close * 3\n"
            "    third := sp\n"
            "if n == 99\n",
        )
    elif shape == "for":
        source = source.replace(
            _PLAIN_BLOCK,
            "for index = 0 to 0\n"
            "    sp = close * 2\n"
            "    other := sp\n",
        )
    elif shape == "while":
        source = source.replace(
            _PLAIN_BLOCK,
            "int remaining = 1\n"
            "while remaining > 0\n"
            "    sp = close * 2\n"
            "    other := sp\n"
            "    remaining -= 1\n",
        )
    elif shape == "switch":
        source = source.replace(
            _PLAIN_BLOCK,
            "switch\n"
            "    n >= 0 and n % 10 == 8 =>\n"
            "        sp = close * 2\n"
            "        other := sp\n",
        )
    else:
        raise AssertionError(f"Unknown block shape: {shape}")
    source = re.sub(r"\bsp2\b", "saved", source)
    return re.sub(r"\bsp\b", "pending", source)


@pytest.mark.parametrize(
    ("shape", "last_plain"),
    [
        pytest.param("var-before-plain", 118, id="var-before-plain"),
        pytest.param("varip", 118, id="varip"),
        pytest.param("plain-var-plain", 118, id="plain-var-plain"),
        pytest.param("for", 119, id="plain-for-body"),
        pytest.param("while", 119, id="plain-while-body"),
        pytest.param("switch", 118, id="plain-switch-case"),
    ],
)
def test_persistent_block_matches_distinct_name_control(shape, last_plain):
    # A different price range and spelling exercise the declaration rule
    # independently of the verbatim source.
    closes = tuple(700.0 + 3 * index for index in range(120))
    members = ("kept", "other")
    if shape == "plain-var-plain":
        members += ("third",)
    expected = _run(_variant(_CONTROL, shape), *members, closes=closes)
    assert expected[:2] == (closes[9], 2 * closes[last_plain])
    if shape == "plain-var-plain":
        assert expected[2] == 3 * closes[110]
    assert _run(_variant(_SOURCE, shape), *members, closes=closes) == expected


_HISTORY = """//@version=6
strategy("persistent block history")
var float kept = na
var float other = na
var float previous = na
var bool firstMissing = false
if bar_index % 2 == 0
    pending = close * 2
    other := pending
if bar_index >= 3
    var float {name} = close
    if bar_index == 3
        firstMissing := na({name}[1])
    previous := {name}[1]
    {name} += 1.0
    kept := {name}
"""


def test_history_and_reassignment_belong_to_the_persistent_declaration():
    members = ("kept", "other", "previous", "firstMissing")
    expected = _run(_HISTORY.format(name="saved"), *members)
    assert expected == (
        _CLOSES[3] + len(_CLOSES) - 3,
        2 * _CLOSES[118],
        _CLOSES[3] + len(_CLOSES) - 4,
        1,
    )
    assert _run(_HISTORY.format(name="pending"), *members) == expected


_SHADOWS = """//@version=6
strategy("persistent block nested reads")
var float kept = na
var float other = na
var float nested = na
var float after = na
if bar_index % 10 == 8
    pending = close * 2
    other := pending
if bar_index % 10 == 9
    var float {name} = close
    if close > 0
        kept := {name}
{shadow}
    after := {name}
"""


@pytest.mark.parametrize(
    ("shadow", "nested"),
    [
        pytest.param(
            "    if close > 0\n"
            "        pending = close * 3\n"
            "        nested := pending",
            3 * _CLOSES[-1],
            id="nested-declaration",
        ),
        pytest.param(
            "    for pending = 0 to 1\n"
            "        nested := pending",
            1,
            id="counter-binder",
        ),
        pytest.param(
            "    for pending in array.from(close, open)\n"
            "        nested := pending",
            _CLOSES[-1],
            id="collection-binder",
        ),
    ],
)
def test_nested_reads_preserve_declaration_and_loop_shadows(shadow, nested):
    members = ("kept", "other", "nested", "after")
    expected = _run(_SHADOWS.format(name="saved", shadow=shadow), *members)
    assert expected == (_CLOSES[9], 2 * _CLOSES[118], nested, _CLOSES[9])
    assert _run(_SHADOWS.format(name="pending", shadow=shadow), *members) == expected


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    assert tree is not None, f"The C++ reference commit {CPP_REFERENCE} is required"
    return tree


_UNCHANGED_PAIRS = [
    pytest.param(
        """//@version=6
strategy("sibling persistent flags")
var bool upper = false
var bool lower = false
if close > open
    var bool valid = close > high
    upper := valid
if close <= open
    var bool valid = close < low
    lower := valid
""",
        id="two-vars",
    ),
    pytest.param(
        _SOURCE.replace("    var float sp = close\n", "    sp = close\n"),
        id="two-plain-doubles",
    ),
    pytest.param(_CONTROL, id="different-names"),
    pytest.param(
        """//@version=6
strategy("nested declaration shadow")
var float kept = na
if close > 0
    var float pending = close
    if close > open
        pending = close * 2
        kept := pending
""",
        id="shadow-within-one-outer-block",
    ),
]

_UNCHANGED_DIRECT_DECLARATIONS = [
    pytest.param(
        _SOURCE.replace(
            "var float kept = na\n",
            declaration + "\nvar float kept = na\n",
        ),
        id=identity,
    )
    for declaration, identity in [
        ("float sp = close", "direct-plain"),
        ("var float sp = close", "direct-var"),
        ("varip float sp = close", "direct-varip"),
        (
            "pair(float source) => [source, source * 2]\n"
            "[sp, unused] = pair(close)",
            "direct-tuple",
        ),
    ]
]


# Sources from the existing runtime-initializer, series-initializer and
# argument-history tests pin the complete output of unrelated routes.
_UNCHANGED_SCRIPTS = [
    pytest.param(_RUNTIME_PINE, id="runtime-scalar-seeds"),
    pytest.param(_SERIES_INITIALIZERS, id="series-initializer-shapes"),
    pytest.param(_ARGUMENT_HISTORY, id="argument-history"),
    pytest.param(
        """//@version=6
strategy("runtime scalar dependency order")
length = input.int(3, "Length")
emaValue = ta.ema(close, length)
plainValue = emaValue + 2.0
var int fromInput = length
var float fromTA = emaValue
var float fromPlain = plainValue
var float directInput = input.float(4.5, "Direct Seed")
""",
        id="runtime-dependencies",
    ),
    pytest.param(
        """//@version=6
strategy("if else mixed var init")
length = input.float(4.0, "Length")
if close > 0
    var float branchValue = length
    branchValue += 1.0
else
    var float branchValue = 7.0
    branchValue += 2.0
""",
        id="if-else-persistent-seeds",
    ),
    pytest.param(
        """//@version=6
strategy("switch mixed var init")
switch
    close > 10 =>
        var float caseValue = low
        caseValue += 1.0
    close > 0 =>
        var float caseValue = 2.0
        caseValue += 2.0
    =>
        var float caseValue = high
        caseValue += 3.0
""",
        id="switch-persistent-seeds",
    ),
    pytest.param(
        """//@version=6
strategy("if expression runtime var init")
float result = if close > 0
    var float branchState = low
    branchState
else
    var float branchState = high
    branchState
""",
        id="if-expression-seeds",
    ),
    pytest.param(
        """//@version=6
strategy("switch expression runtime var init")
float result = switch
    close > 10 =>
        var float caseState = low
        caseState
    =>
        var float caseState = high
        caseState
""",
        id="switch-expression-seeds",
    ),
]


@pytest.mark.parametrize("source", _UNCHANGED_PAIRS + _UNCHANGED_SCRIPTS)
def test_unaffected_scripts_keep_complete_cpp(source, reference, tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    # A script that calls timestamp() carries the timezone stop block and arm:
    # compare through the legacy normalizer, which models them.
    assert legacy_cpp(transpile(source)) == legacy_cpp(before["cpp"])


@pytest.mark.parametrize("source", _UNCHANGED_DIRECT_DECLARATIONS)
def test_direct_declarations_keep_reference_outcome(source, reference, tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    actual = transpile_json(pine)
    assert actual["ok"] == before["ok"], (actual, before)
    if before["ok"]:
        assert actual["cpp"] == before["cpp"]
    else:
        assert actual["error"] == before["error"]
        assert actual["diagnostics"] == before["diagnostics"]
