"""A declaration cannot give an object the name of a built-in namespace.

TradingView refused the two destructuring declarations it was shown, one at
chart scope and one inside a function, with CE10093 at the name ``adjustment``
(the measured scripts below). The tuple programs that used to pin an accepted
namespace-named record are pinned here as refused, and their other checks run
on the same program with the record renamed. A parameter is not a declaration
statement: TradingView's answer for a namespace-named record parameter has not
been captured, so the parameter rows pin today's lowering, not TradingView's
acceptance.

The refusal names namespaces. TradingView's second measurement (relayed
2026-11-17 16:40) refused ``str``, ``backadjustment`` and
``settlement_as_close`` the same way and accepted the record named ``time``,
warning only that the name shadows the built-in variable (CW10011): ``time``
is a built-in value that qualifies no member, so it is no namespace. The rows
for ``time`` below pin that acceptance by running the emitted code.

TradingView's third measurement (decoded and qualified 2026-11-17 19:31) is
pinned byte for byte in ``_OBJECT_NAME_PROBES``: one nine-line script under
five record names. It refused ``dayofweek``, ``footprint`` and ``volume_row``
with CE10093 at the name, and it compiled ``timenow`` with the CW10011
shadowing warning it gave ``time``; the same script with the record named
``holder`` compiled without a warning. Only that control's plot was received:
the value TradingView computes through a record named ``timenow`` is
unmeasured, and no value checked below is a TradingView measurement.
"""

import hashlib
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import TupleAssign
from pineforge_codegen.codegen.base import CodeGen
from pineforge_codegen.codegen.types import TypeInferer
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import UNSUPPORTED_NAMESPACES, check_support
from tests._compile import run_emitted_tu


_HEADER = """//@version=6
strategy("namespace named record receivers")
type Holder
    float factor
"""

_CONTROLS = """none_value = adjustment.none
dividends_value = adjustment.dividends
splits_value = adjustment.splits
"""

_BARS = (
    (100.0, 102.0, 99.0, 101.5),
    (200.0, 201.0, 197.0, 197.25),
)

_DRIVER = r"""
#include <iomanip>
#include <iostream>

int main() {
    pineforge::Bar bars[] = {
BAR_INITIALIZERS
    };
    for (auto& bar : bars) {
        GeneratedStrategy strategy;
        strategy.run(&bar, 1);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 2;
        }
        std::cout << std::setprecision(17) << MEMBER_VALUES << '\n';
    }
}
"""


def _values_on_each_bar(cpp: str, *members: str) -> list[list[float]]:
    """These members of a fresh strategy instance after it ran each one bar."""
    driver = _DRIVER.replace(
        "BAR_INITIALIZERS",
        ",\n".join(
            f"        {{{open_}, {high}, {low}, {close}, 10.0, 1743466500000LL}}"
            for open_, high, low, close in _BARS
        ),
    ).replace(
        "MEMBER_VALUES",
        " << ' ' << ".join(f"strategy.{member}" for member in members),
    )
    output = run_emitted_tu(
        cpp,
        driver,
        opt="-O0",
        label="namespace named record receiver",
    )
    return [list(map(float, line.split())) for line in output.splitlines()]


def _assert_field_values(body: str) -> None:
    actual = _values_on_each_bar(
        transpile(_HEADER + body + _CONTROLS),
        "actual", "none_value", "dividends_value", "splits_value",
    )
    # A fresh instance initializes its persistent local from each input bar.
    # The unshadowed namespace constants retain their existing integer values.
    assert actual == [
        [close - open_, 0, 1, 2]
        for open_, _high, _low, close in _BARS
    ]


# Parameters and method receivers. These rows pin how a record parameter under
# a namespace's name is lowered today; TradingView's answer for it is unknown.

@pytest.mark.parametrize("name", ["adjustment", "backadjustment"])
def test_typed_parameter_keeps_float_field(name: str) -> None:
    _assert_field_values(
        f"""read_field(Holder {name}) =>
    var value = {name}.factor
    value
actual = read_field(Holder.new(close - open))
"""
    )


def test_untyped_parameter_keeps_float_field() -> None:
    _assert_field_values(
        """read_field(adjustment) =>
    var value = adjustment.factor
    value
actual = read_field(Holder.new(close - open))
"""
    )


def test_method_receiver_keeps_float_field() -> None:
    _assert_field_values(
        """method read_field(Holder adjustment) =>
    var value = adjustment.factor
    value
record = Holder.new(close - open)
actual = record.read_field()
"""
    )


# The tuple programs, by the name their record takes. Under a namespace's name
# each is refused; under any other name its checks below still run.

def _tuple_inside_function(name: str) -> str:
    return f"""pair(float factor) =>
    [Holder.new(factor), 0]
read_field() =>
    [{name}, _] = pair(close - open)
    var value = {name}.factor
    value
actual = read_field()
"""


def _tuple_beside_namespace_reader(name: str) -> str:
    return f"""pair(float factor) =>
    [Holder.new(factor), 0]
read_field() =>
    [{name}, _] = pair(close - open)
    {name}.factor
read_namespace() =>
    adjustment.none + adjustment.dividends + adjustment.splits
actual = read_field()
namespace_value = read_namespace()
"""


def _tuple_in_two_scopes(name: str) -> str:
    return f"""pair(float factor) =>
    [Holder.new(factor), 0]
[{name}, _] = pair(close - open)
read_field() =>
    [{name}, _] = pair(open - close)
    {name}.factor
actual = read_field()
global_actual = {name}.factor
"""


def _tuple_at_chart_scope(name: str) -> str:
    return f"""pair(float factor) =>
    [Holder.new(factor), 0]
[{name}, _] = pair(close - open)
actual = {name}.factor
"""


def test_tuple_element_inside_function_keeps_float_field() -> None:
    _assert_field_values(_tuple_inside_function("holder"))


def test_callable_tuple_does_not_shadow_namespace_in_sibling_function() -> None:
    cpp = transpile(_HEADER + _tuple_beside_namespace_reader("holder"))
    assert "_pf_udt_Holder.read(holder).factor" in cpp
    for member in ("none", "dividends", "splits"):
        assert f"_pf_udt_Holder.read(holder).{member}" not in cpp
        assert f"_pf_udt_Holder.read(adjustment).{member}" not in cpp
    # The callable reads the record its tuple bound; the sibling's unbound
    # reads stay the namespace constants 0, 1 and 2.
    assert _values_on_each_bar(cpp, "actual", "namespace_value") == [
        [close - open_, 0 + 1 + 2]
        for open_, _high, _low, close in _BARS
    ]


def test_global_and_callable_tuple_receivers_keep_record_lowering() -> None:
    cpp = transpile(_HEADER + _tuple_in_two_scopes("holder"))
    assert "global_actual = _pf_udt_Holder.read(holder).factor;" in cpp
    # Each receiver reads the record its own declaration bound: the callable
    # local holds open - close, the global close - open.
    assert _values_on_each_bar(cpp, "actual", "global_actual") == [
        [open_ - close, close - open_]
        for open_, _high, _low, close in _BARS
    ]


# A record under a built-in value's name. TradingView accepted the measured
# chart-scope shape with its record named ``time`` (relayed 2026-11-17 16:40:
# compiled, warning CW10011). ``timenow`` is the inventory's other built-in
# variable that qualifies no member. An earlier note here said TradingView was
# not shown it; that is superseded. TradingView has since compiled a
# chart-scope record named ``timenow`` with the same warning: the ``timenow``
# script of ``_OBJECT_NAME_PROBES`` below (qualified 2026-11-17 19:31). That
# measurement covers the one chart-scope script and its plot was not received,
# so the values these rows check come from the bars this file feeds, not from
# TradingView.
_VALUE_NAMED_RECORDS = {
    "chart-scope": _tuple_at_chart_scope,
    "inside-function": _tuple_inside_function,
}


@pytest.mark.parametrize("name", ["time", "timenow"])
@pytest.mark.parametrize("scope", sorted(_VALUE_NAMED_RECORDS))
def test_record_tuple_target_named_like_a_builtin_value_reads_its_record(
    scope: str, name: str,
) -> None:
    # The record holds each bar's own close - open, read back through the
    # name; the namespace constants beside it keep their values.
    _assert_field_values(_VALUE_NAMED_RECORDS[scope](name))


# TradingView's refusals of the two measured scripts, byte for byte: the
# script, its SHA-256, and the span TradingView's compiler reported for
# CE10093 "Invalid object name: adjustment. Namespaces of built-ins cannot be
# used." as line, first column and last column (read on 2026-11-17). No value
# was measured: neither script plots.
_MEASURED_REFUSALS = {
    "global": (
        "//@version=6\n"
        'strategy("tuple qualifier same scope global")\n'
        "type Holder\n"
        "    float factor\n"
        "pair(float x) =>\n"
        "    [Holder.new(x), 0]\n"
        "[adjustment, _] = pair(close - open)\n"
        "none_value = adjustment.none\n",
        "60819e6dd3ecf824f7c40414139088a9bde94cf80fb7195bf0bda6b726bb2415",
        (7, 2, 11),
    ),
    "callable": (
        "//@version=6\n"
        'strategy("tuple qualifier same scope callable")\n'
        "type Holder\n"
        "    float factor\n"
        "pair(float x) =>\n"
        "    [Holder.new(x), 0]\n"
        "read_none(float x) =>\n"
        "    [adjustment, _] = pair(x)\n"
        "    adjustment.none\n"
        "none_value = read_none(close - open)\n",
        "82e3e3c147c95f7ea611945f4f39cfc9af4dac64eb788e48ea328fbfdcf65656",
        (8, 6, 15),
    ),
}


def _assert_object_name_refusal(source: str, name: str, line: int, col: int):
    """The one translation-time diagnostic of ``source``: the object name."""
    filename = "object-name.pine"
    with pytest.raises(CompileError) as caught:
        transpile(source, filename=filename)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == "PF-E2065"
    assert diagnostic.message == (
        f"Invalid object name: {name}. Namespaces of built-ins cannot be used."
    )
    assert diagnostic.args == {"name": name}
    assert diagnostic.hint is None
    location = diagnostic.location
    assert location.file == filename
    # The refused identifier itself, as the script wrote it: neither the
    # bracket that opens the declaration nor a later read of the name.
    assert (location.line, location.col, location.end_col) == (
        line, col, col + len(name),
    )
    assert source.splitlines()[line - 1][col - 1:location.end_col - 1] == name
    return caught.value


@pytest.mark.parametrize("scope", sorted(_MEASURED_REFUSALS))
def test_measured_script_is_refused_at_its_namespace_named_target(scope: str) -> None:
    source, digest, (line, first, last) = _MEASURED_REFUSALS[scope]
    assert hashlib.sha256(source.encode()).hexdigest() == digest
    error = _assert_object_name_refusal(source, "adjustment", line, first)
    # TradingView counts the last column of the name; a location here ends
    # one column after it.
    assert error.diagnostics[0].location.end_col - 1 == last


@pytest.mark.parametrize("scope", sorted(_MEASURED_REFUSALS))
def test_measured_script_under_a_valid_name_reads_the_record_of_each_bar(
    scope: str,
) -> None:
    source = _MEASURED_REFUSALS[scope][0]
    # The same script with the record renamed and its declared field read: no
    # other character separates it from the refused one.
    control = source.replace("adjustment.none", "holder.factor").replace(
        "adjustment", "holder",
    )
    assert control != source and "adjustment" not in control
    # Each bar's own close - open went through the tuple into the record.
    assert _values_on_each_bar(transpile(control), "none_value") == [
        [close - open_] for open_, _high, _low, close in _BARS
    ]


# TradingView's second measurement, as relayed on 2026-11-17 16:40: the name
# it refused with CE10093 and the span it reported, as line, first column and
# last column. The relay carried neither the scripts' bytes nor their digests,
# so the declaration below rebuilds the measured chart-scope shape (the record
# is the first target of the tuple on line 7); it is not the measured bytes.
_RELAYED_REFUSAL_SPANS = {
    "backadjustment": (7, 2, 15),
    "settlement_as_close": (7, 2, 20),
    "str": (7, 2, 4),
}


@pytest.mark.parametrize("name", sorted(_RELAYED_REFUSAL_SPANS))
def test_relayed_refusal_is_reproduced_at_the_refused_name(name: str) -> None:
    line, first, last = _RELAYED_REFUSAL_SPANS[name]
    source = _HEADER + f"""pair(float factor) =>
    [Holder.new(factor), 0]
[{name}, _] = pair(close - open)
"""
    error = _assert_object_name_refusal(source, name, line, first)
    assert error.diagnostics[0].location.end_col - 1 == last


# TradingView's third measurement, byte for byte (decoded and qualified
# 2026-11-17 19:31): one nine-line script under five record names. Each row
# is the script, its SHA-256, its length in bytes and, where TradingView
# refused it, the span its compiler reported as line, first column and last
# column. TradingView's compiler answers:
#
#   holder      compiled, with no error and no warning.
#   timenow     compiled, with CW10011 'Shadowing built-in variable "timenow"'
#               reported twice: at the name (line 7, columns 2 to 8) and at
#               the whole declaration (line 7, columns 1 to 33).
#   dayofweek, footprint, volume_row
#               refused with CE10093 "Invalid object name: <name>. Namespaces
#               of built-ins cannot be used." at the name.
#
# Values: TradingView's plot was received for ``holder`` only. The plot of
# ``timenow`` is unmeasured and a refused script plots nothing, so no value a
# test below checks is a TradingView measurement.
_OBJECT_NAME_PROBES = {
    "holder": (
        "//@version=6\n"
        'strategy("object name probe")\n'
        "type Holder\n"
        "    float factor\n"
        "pair(float x) =>\n"
        "    [Holder.new(x), 0]\n"
        "[holder, _] = pair(close - open)\n"
        "factor_value = holder.factor\n"
        "plot(factor_value)\n",
        "337aa01dfa382b1291978fadbbd9adb88c02f2fc98d127c81f449dfd75f0f84f",
        193,
        None,
    ),
    "timenow": (
        "//@version=6\n"
        'strategy("object name probe")\n'
        "type Holder\n"
        "    float factor\n"
        "pair(float x) =>\n"
        "    [Holder.new(x), 0]\n"
        "[timenow, _] = pair(close - open)\n"
        "factor_value = timenow.factor\n"
        "plot(factor_value)\n",
        "29f2efaf2189ee0012334c04eb55ff70875a8ff52209e6de195f7793087e9d4f",
        195,
        None,
    ),
    "dayofweek": (
        "//@version=6\n"
        'strategy("object name probe")\n'
        "type Holder\n"
        "    float factor\n"
        "pair(float x) =>\n"
        "    [Holder.new(x), 0]\n"
        "[dayofweek, _] = pair(close - open)\n"
        "factor_value = dayofweek.factor\n"
        "plot(factor_value)\n",
        "66dd6e934aa86b543aedad1c676dfc7d3752f417175b2b0cf3ff5ecdb5e81c54",
        199,
        (7, 2, 10),
    ),
    "footprint": (
        "//@version=6\n"
        'strategy("object name probe")\n'
        "type Holder\n"
        "    float factor\n"
        "pair(float x) =>\n"
        "    [Holder.new(x), 0]\n"
        "[footprint, _] = pair(close - open)\n"
        "factor_value = footprint.factor\n"
        "plot(factor_value)\n",
        "beb698102e6f0dc492221c9a0040b73f86f37dd7a116a03e7e9ce5cb9ce9c90d",
        199,
        (7, 2, 10),
    ),
    "volume_row": (
        "//@version=6\n"
        'strategy("object name probe")\n'
        "type Holder\n"
        "    float factor\n"
        "pair(float x) =>\n"
        "    [Holder.new(x), 0]\n"
        "[volume_row, _] = pair(close - open)\n"
        "factor_value = volume_row.factor\n"
        "plot(factor_value)\n",
        "ec37abf56725e257e11881ba92a660cc39498513b6e3769bd4db7780f3ad1df5",
        201,
        (7, 2, 11),
    ),
}
_REFUSED_PROBES = sorted(
    name for name, row in _OBJECT_NAME_PROBES.items() if row[3] is not None
)
_ACCEPTED_PROBES = sorted(
    name for name, row in _OBJECT_NAME_PROBES.items() if row[3] is None
)


def _measured_probe(name: str) -> str:
    """The measured script of ``name``, checked against its recorded identity."""
    source, digest, size, _span = _OBJECT_NAME_PROBES[name]
    encoded = source.encode()
    assert len(encoded) == size
    assert hashlib.sha256(encoded).hexdigest() == digest
    return source


@pytest.mark.parametrize("name", _REFUSED_PROBES)
def test_measured_probe_is_refused_at_its_namespace_named_target(name: str) -> None:
    source = _measured_probe(name)
    line, first, last = _OBJECT_NAME_PROBES[name][3]
    error = _assert_object_name_refusal(source, name, line, first)
    # TradingView counts the last column of the name; a location here ends
    # one column after it.
    assert error.diagnostics[0].location.end_col - 1 == last
    # Only the name separates this script from the one TradingView compiled:
    # renamed, it is the ``holder`` script byte for byte, which the rows
    # below translate and run.
    assert source.replace(name, "holder") == _measured_probe("holder")


@pytest.mark.parametrize("name", _ACCEPTED_PROBES)
def test_measured_probe_under_an_accepted_name_translates_its_record_read(
    name: str,
) -> None:
    cpp = transpile(_measured_probe(name))
    # Translating is the acceptance. The script's one field read must also be
    # lowered as a read of the record its declaration bound under that name,
    # however the emitter spells the storage, not as a built-in's member.
    receivers = re.findall(
        r"_pf_udt_Holder\.read\(([A-Za-z_][A-Za-z_0-9]*)\)\.factor", cpp,
    )
    assert any(name in receiver for receiver in receivers), receivers


@pytest.mark.parametrize("name", _ACCEPTED_PROBES)
def test_measured_probe_under_an_accepted_name_reads_the_record_of_each_bar(
    name: str,
) -> None:
    # An input-derived control, not a TradingView value: the emitted code is
    # compiled and run on this file's bars, and each bar's own close - open
    # goes through the tuple into the record and back out of its field.
    assert _values_on_each_bar(
        transpile(_measured_probe(name)), "factor_value",
    ) == [[close - open_] for open_, _high, _low, close in _BARS]


# One rule for every destructuring target, at chart scope and inside a
# function: the body, the refused name, and its 1-based line and column. The
# four header lines put the helper on lines 5 and 6.
_REFUSED_OBJECT_NAMES = [
    # The programs that pinned an accepted namespace-named record.
    pytest.param(_tuple_inside_function("adjustment"), "adjustment", 8, 6,
                 id="inside-function"),
    pytest.param(_tuple_beside_namespace_reader("adjustment"), "adjustment", 8, 6,
                 id="beside-namespace-reader"),
    pytest.param(_tuple_in_two_scopes("adjustment"), "adjustment", 7, 2,
                 id="two-scopes-adjustment"),
    # Another member of the inventory: the rule reads the name from the set.
    pytest.param(_tuple_in_two_scopes("backadjustment"), "backadjustment", 7, 2,
                 id="two-scopes-backadjustment"),
    # The record is the second target: its own token is located, after a
    # first target that holds a number under an ordinary name.
    pytest.param(
        """swap(float factor) =>
    [0, Holder.new(factor)]
[count, adjustment] = swap(close - open)
actual = adjustment.factor
""",
        "adjustment", 7, 9, id="second-target",
    ),
    # A function's local named like a built-in variable is stored under a
    # private spelling; the refusal keeps the written name and its span.
    pytest.param(
        """pair(float factor) =>
    [Holder.new(factor), 0]
read_field() =>
    [dayofweek, _] = pair(close - open)
    dayofweek.factor
actual = read_field()
""",
        "dayofweek", 8, 6, id="renamed-function-local",
    ),
    # A namespace TradingView refused at chart scope, declared inside a
    # function. The record is never read, so only its declaration is judged.
    pytest.param(
        """pair(float factor) =>
    [Holder.new(factor), 0]
read_field() =>
    [str, _] = pair(close - open)
    close
actual = read_field()
""",
        "str", 8, 6, id="inside-function-str",
    ),
]


@pytest.mark.parametrize("body, name, line, col", _REFUSED_OBJECT_NAMES)
def test_record_tuple_target_named_like_a_namespace_is_refused(
    body: str, name: str, line: int, col: int,
) -> None:
    error = _assert_object_name_refusal(_HEADER + body, name, line, col)
    assert "__pfhist" not in str(error)


def test_number_under_a_namespace_name_beside_a_record_is_not_refused() -> None:
    cpp = transpile(
        _HEADER
        + """pair(float factor) =>
    [Holder.new(factor), 0]
[holder, adjustment] = pair(close - open)
actual = holder.factor
"""
    )
    # The refusal reads each element's own type: the second element is a
    # number, so its name is left alone, as a declared float of that name is,
    # and the record beside it is still read as a record. TradingView was not
    # asked about a destructured number: this pins the rule as written.
    assert "actual = _pf_udt_Holder.read(holder).factor;" in cpp


# The support check refuses a member of a built-in namespace PineForge has no
# data for (``UNSUPPORTED_NAMESPACES``) by the spelling of its receiver. Where
# a declaration statement of the script binds that receiver at the read, the
# check holds its refusal behind analysis, which refuses an object declared
# under a namespace's name at the name. The rows below pin both sides of that
# order for every name of the table. They are PineForge's own diagnostics, not
# TradingView's: TradingView was shown the chart-scope scripts of
# ``_OBJECT_NAME_PROBES`` only.

def _assert_unsupported_namespace_refusal(source: str, name: str, line: int) -> None:
    """The one error of ``source``: the support check's refusal of the member
    of the built-in namespace ``name`` that ``line`` reads."""
    filename = "unsupported-namespace.pine"
    with pytest.raises(CompileError) as caught:
        transpile(source, filename=filename)
    error, = [d for d in caught.value.diagnostics if d.level is Level.ERROR]
    assert error.phase is Phase.ANALYZER
    assert error.message == f"{name}.factor: {UNSUPPORTED_NAMESPACES[name]}"
    assert "is not supported in PineForge batch backtests" in error.message
    assert error.code != "PF-E2065"
    assert error.location.file == filename
    assert error.location.line == line
    assert f"{name}.factor" in source.splitlines()[line - 1]


@pytest.mark.parametrize("name", sorted(UNSUPPORTED_NAMESPACES))
@pytest.mark.parametrize("build, line, col", [
    pytest.param(_tuple_at_chart_scope, 7, 2, id="chart-scope"),
    pytest.param(_tuple_inside_function, 8, 6, id="inside-function"),
    # A chart declaration and a callable's own, each read where it is bound:
    # the chart declaration is the first one analysis meets.
    pytest.param(_tuple_in_two_scopes, 7, 2, id="two-scopes"),
])
def test_record_under_an_unsupported_namespace_name_is_refused_at_its_declaration(
    build, line: int, col: int, name: str,
) -> None:
    _assert_object_name_refusal(_HEADER + build(name), name, line, col)


# Reads no declaration statement binds where they are read. The body, and the
# line of its read: the four header lines put the body's first line on line 5.
_UNBOUND_UNSUPPORTED_NAMESPACE_READS = {
    # Nothing declares the name: the built-in namespace itself.
    "builtin-namespace": ("actual = {name}.factor\n", 5),
    # The record is declared after the read.
    "declared-after-the-read": (
        """pair(float factor) =>
    [Holder.new(factor), 0]
actual = {name}.factor
[{name}, _] = pair(close - open)
""",
        7,
    ),
    # The record is another function's local.
    "declared-in-a-sibling-function": (
        """pair(float factor) =>
    [Holder.new(factor), 0]
make() =>
    [{name}, _] = pair(close - open)
    close
read_field() =>
    {name}.factor
actual = read_field()
""",
        11,
    ),
    # The record is declared in a block that closed before the read.
    "declared-in-a-closed-block": (
        """pair(float factor) =>
    [Holder.new(factor), 0]
if close > open
    [{name}, _] = pair(close - open)
actual = {name}.factor
""",
        9,
    ),
    # A parameter and a loop variable are no declaration statement.
    "parameter": (
        """read_field({name}) =>
    {name}.factor
actual = read_field(Holder.new(close - open))
""",
        6,
    ),
    "loop-variable": (
        """float actual = 0.0
for {name} = 0 to 1
    actual += {name}.factor
""",
        7,
    ),
}


@pytest.mark.parametrize("name", sorted(UNSUPPORTED_NAMESPACES))
@pytest.mark.parametrize("case", sorted(_UNBOUND_UNSUPPORTED_NAMESPACE_READS))
def test_unsupported_namespace_member_no_declaration_binds_stays_refused(
    case: str, name: str,
) -> None:
    body, line = _UNBOUND_UNSUPPORTED_NAMESPACE_READS[case]
    _assert_unsupported_namespace_refusal(
        _HEADER + body.replace("{name}", name), name, line,
    )


@pytest.mark.parametrize("name", sorted(UNSUPPORTED_NAMESPACES))
def test_number_under_an_unsupported_namespace_name_keeps_the_member_refusal(
    name: str,
) -> None:
    # A declaration statement binds the receiver, so the refusal is held; a
    # number is no object, so analysis has no object name to refuse, and the
    # held refusal is raised as the support check wrote it.
    _assert_unsupported_namespace_refusal(
        _HEADER + f"float {name} = close - open\nactual = {name}.factor\n",
        name, 6,
    )


@pytest.mark.parametrize("name", sorted(UNSUPPORTED_NAMESPACES))
def test_support_check_reports_the_refusal_it_holds_behind_a_declaration(
    name: str,
) -> None:
    # Imported here: a tree without the held order fails this test alone.
    from pineforge_codegen.support_checker import refusals_await_analysis

    def support_errors(body: str):
        source = _HEADER + body
        program = Parser(Lexer(source).tokenize(), source=source).parse()
        diagnostics = check_support(program)
        errors = [d for d in diagnostics if d.level is Level.ERROR]
        return errors, refusals_await_analysis(program, diagnostics)

    # Holding drops nothing: the check still returns its error for the read.
    errors, held = support_errors(_tuple_at_chart_scope(name))
    error, = errors
    assert error.message == f"{name}.factor: {UNSUPPORTED_NAMESPACES[name]}"
    assert error.location.line == 8
    assert held
    # A read of the built-in namespace is not held, alone or beside a held one.
    errors, held = support_errors(f"actual = {name}.factor\n")
    assert [error.location.line for error in errors] == [5] and not held
    errors, held = support_errors(
        f"early = {name}.factor\n" + _tuple_at_chart_scope(name)
    )
    assert [error.location.line for error in errors] == [5, 9] and not held


# A tuple receiver's record type is its declaration's storage, inferred from
# the helper the declaration calls. The tests below measure that inference.

# The lexical state a tuple inference replaces with its callee's, and the
# width mode a caller can set around one declaration.
_INFERENCE_CONTEXT = (
    "_current_func_param_types", "_current_func_param_specs",
    "_current_func_series_param_types", "_current_func_local_types",
    "_active_func_name", "_wide_int_epoch_only", "_wide_int_float_origins",
)

_PLAIN_HEADER = '//@version=6\nstrategy("tuple storage contexts")\n'

# The script header, the innermost result, what each level reads twice, what
# it returns, and the field the chart reads.
_NESTED_LEVELS = {
    # A record receiver: the read asks for the record type of its tuple.
    "record": (
        _HEADER, "[Holder.new(x), 0]", "h.factor", "[Holder.new(a + b), 0]",
        ".factor",
    ),
    # A bare scalar: the read asks for the stored type of its tuple element.
    "scalar": (_PLAIN_HEADER, "[x, 0]", "h", "[a + b, 0]", ""),
}


def _nested_helpers(shape: str, depth: int) -> str:
    """``depth`` helpers, each destructuring the tuple of the one below it."""
    header, leaf, read, result, field = _NESTED_LEVELS[shape]
    source = header + f"f0(float x) =>\n    {leaf}\n"
    for level in range(1, depth + 1):
        source += (
            f"f{level}(float x) =>\n"
            f"    [h, _] = f{level - 1}(x)\n"
            f"    a = {read}\n"
            f"    b = {read} * 2.0\n"
            f"    {result}\n"
        )
    return source + (
        f"[record, _] = f{depth}(close - open)\n"
        f"actual = record{field}\n"
    )


def _lexical_context(emitter) -> tuple:
    return tuple(
        frozenset(value.items()) if isinstance(value, dict) else value
        for value in (getattr(emitter, attr, None) for attr in _INFERENCE_CONTEXT)
    )


class _TupleInferenceWork:
    """What the emitter's own tuple inference did during one translation."""

    def __init__(self) -> None:
        # Running storage queries, outermost first: [key, inferred a body].
        self.asks: list[list] = []
        self.scans = 0               # width scans entered
        self.deepest = 0             # most queries nested in one another
        self.outermost = 0           # queries no other query was running for
        self.outermost_inferred = 0  # ... that inferred their own declaration
        self.shared = 0              # nested queries answered without a body
        self.largest_query = 0       # most declarations one query inferred
        self.repeated: list[str] = []


def _measure_tuple_inference(monkeypatch) -> _TupleInferenceWork:
    """Count calls of the real inference methods; each call still runs them.

    A storage query (``_tuple_binding_cpp_types``) that infers its
    declaration walks the called helper's body (``_infer_tuple_types``). One
    outermost query must do that once for each declaration and lexical
    context it reaches. A width scan may change what a later inference
    answers, so only a query no scan started in is held to that.

    The check runs inside the translation: a repeat fails at once, where the
    walk it starts would otherwise visit the helper once per call path.
    """
    work = _TupleInferenceWork()
    storage_query = CodeGen._tuple_binding_cpp_types
    body_inference = CodeGen._infer_tuple_types
    width_scan = CodeGen._int_width_scan
    query: dict = {}

    def counted_query(self, node):
        outermost = not work.asks
        if outermost:
            work.outermost += 1
            query["inferred"] = set()
            query["scans"] = work.scans
        ask = [(id(node), _lexical_context(self)), False]
        work.asks.append(ask)
        work.deepest = max(work.deepest, len(work.asks))
        try:
            return storage_query(self, node)
        finally:
            work.asks.pop()
            if outermost:
                work.outermost_inferred += int(ask[1])
                if query["scans"] == work.scans:
                    work.largest_query = max(
                        work.largest_query, len(query["inferred"])
                    )
            elif not ask[1]:
                work.shared += 1

    def counted_body(self, func_node, count):
        if work.asks and not work.asks[-1][1]:
            ask = work.asks[-1]
            ask[1] = True
            if query["scans"] == work.scans:
                if ask[0] in query["inferred"]:
                    work.repeated.append(func_node.name)
                    pytest.fail(
                        f"one tuple storage query inferred {func_node.name} "
                        "again for the same declaration and lexical context"
                    )
                query["inferred"].add(ask[0])
        return body_inference(self, func_node, count)

    def counted_scan(self, *args, **kwargs):
        work.scans += 1
        return width_scan(self, *args, **kwargs)

    monkeypatch.setattr(CodeGen, "_tuple_binding_cpp_types", counted_query)
    monkeypatch.setattr(CodeGen, "_infer_tuple_types", counted_body)
    monkeypatch.setattr(CodeGen, "_int_width_scan", counted_scan)
    return work


@pytest.mark.parametrize("depth", [3, 9, 22])
@pytest.mark.parametrize("shape", sorted(_NESTED_LEVELS))
def test_one_tuple_storage_query_infers_each_nested_helper_once(
    shape: str, depth: int, monkeypatch
) -> None:
    work = _measure_tuple_inference(monkeypatch)
    cpp = transpile(_nested_helpers(shape, depth))
    assert not work.repeated and not work.asks
    # One declaration in each helper that destructures, and the chart's. A
    # query on the chart's reaches them all, each inside the one above it,
    # and infers each of them once however often a level reads its tuple.
    declarations = depth + 1
    assert work.deepest == declarations
    assert work.largest_query == declarations
    assert work.shared > 0
    # No answer outlives its query: the registries of the code being emitted
    # differ from one query to the next, so each infers its own declaration.
    assert work.outermost_inferred == work.outermost > 0
    if shape == "record":
        # Every level still reads the record its own tuple bound, and the
        # chart reads the last one.
        assert cpp.count("double a = _pf_udt_Holder.read(h).factor;") == depth
        assert cpp.count("double b = (_pf_udt_Holder.read(h).factor * 2.0);") == depth
        assert f"    std::tuple<Holder, int> f{depth}(double x) {{" in cpp
        assert "actual = _pf_udt_Holder.read(record).factor;" in cpp


_STORAGE_CONTEXTS = {
    "nested record helpers": _nested_helpers("record", 4),
    "nested scalar helpers": _nested_helpers("scalar", 4),
    # One record through a field read and write, an alias and a method.
    "record consumers": _HEADER + """method bump(Holder this, float amount) =>
    this.factor := this.factor + amount
    this.factor
pair(float value) =>
    [Holder.new(value), 0]
inspect(float value) =>
    [h, _] = pair(value)
    before = h.factor
    h.factor := before + 2.0
    alias = h
    after_write = alias.factor
    after_method = alias.bump(3.0)
    through_original = h.factor
    [before, after_write, after_method, through_original]
[before_value, written_value, method_value, aliased_value] = inspect(close - open)
""",
    # Two declarations of one spelling, each bound by its own written call.
    "one spelling in two scopes": _HEADER + _tuple_in_two_scopes("holder"),
    # One untyped helper whose written calls pass a float and a string.
    "call-site families": _PLAIN_HEADER + """pair(seed) => [seed, seed]
[numberA, numberB] = pair(close)
[wordA, wordB] = pair("row")
plot(numberA)
""",
    # Function and block declarations reusing a chart tuple's names, in a
    # float and an int family.
    "function and block shadows": _PLAIN_HEADER + """pair(float seed) => [seed + 0.25, seed * 2.0]
[value, other] = pair(close)
readGlobals() => value * 10.0 + other
readLocals(float seed) =>
    [value, other] = pair(seed)
    value * 10.0 + other
float blockOut = 0.0
if close > 0.0
    [value, other] = pair(open)
    blockOut := value * 10.0 + other
localOut = readLocals(open + 1.0)
globalOut = readGlobals()
countPair(int seed) => [seed + 2, seed * 3]
[countValue, countOther] = countPair(int(close))
readCounts() => countValue * 10 + countOther
float blockCountOut = 0.0
if close > 0.0
    [countValue, countOther] = countPair(int(open))
    blockCountOut := countValue * 10 + countOther
countOut = readCounts()
plot(globalOut)
""",
    # A selection over another tuple's names, with history and state.
    "selection over tuple names": _PLAIN_HEADER + """once(float seed) =>
    var int calls = 0
    calls += 1
    [seed + calls, seed - calls, calls]
[held, _, callCount] = once(close)
[selected, selectedCount] = if close > open
    [held + 1.0, callCount * 2]
else
    [held - 1.0, callCount * 3]
readSelected() => selected + selectedCount
previous = held[1]
observed = readSelected()
plot(observed)
""",
}


def _written_tuple_declarations(source: str) -> dict[tuple[int, int], int]:
    """Where ``source`` writes each tuple declaration, as line and column,
    with the number of names it binds: what its storage queries can ask for.

    Read from this module's own parse of the same text, never from the
    translation under test.
    """
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    written: dict[tuple[int, int], int] = {}
    seen: set[int] = set()
    pending: list = [program]
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, TupleAssign):
            written[(node.loc.line, node.loc.col)] = len(node.names)
        if isinstance(node, (list, tuple)):
            pending.extend(node)
        elif isinstance(node, dict):
            pending.extend(node.values())
        elif not isinstance(node, type):
            pending.extend(getattr(node, "__dict__", {}).values())
    return written


class _StorageDemand:
    """One call the emitter made of its tuple storage query."""

    def __init__(self, emitter, node, infer, outermost: bool) -> None:
        loc = getattr(node, "loc", None)
        self.by_emitter = isinstance(emitter, CodeGen)
        self.declaration = isinstance(node, TupleAssign)
        self.position = None if loc is None else (loc.line, loc.col)
        self.width = len(node.names) if self.declaration else None
        self.inference = callable(infer)
        self.outermost = outermost   # no other storage query was running
        self.inside = getattr(emitter, "_active_func_name", None)
        self.answered = False        # the call returned to the emitter
        self.types = None            # ... with this answer


def _observe_storage_queries(patch, answer) -> list[_StorageDemand]:
    """Answer the emitter's storage query with ``answer`` and record every
    call the emitter makes of it, in the order the calls start.

    The record grows only when the emitter calls the patched method. Nothing
    is asserted in here, under whatever caller the emitter runs an inference
    in: the test reads the record once the translation has returned.
    """
    demands: list[_StorageDemand] = []
    running: list[_StorageDemand] = []

    def observed(emitter, node, infer):
        demand = _StorageDemand(emitter, node, infer, outermost=not running)
        demands.append(demand)
        running.append(demand)
        try:
            types = answer(emitter, node, infer)
        finally:
            running.pop()
        demand.types = tuple(types) if isinstance(types, (list, tuple)) else types
        demand.answered = True
        return types

    # No ``raising=False``: a tree without the query refuses this patch
    # instead of taking a method that nothing calls.
    patch.setattr(CodeGen, "_tuple_storage_query", observed)
    return demands


@pytest.mark.parametrize("case", sorted(_STORAGE_CONTEXTS))
def test_shared_tuple_storage_keeps_the_translation_of_each_context(
    case: str, monkeypatch
) -> None:
    source = _STORAGE_CONTEXTS[case]
    # The emitter's own storage query. A tree without one shares nothing:
    # the translations compared below would be one translation made twice.
    shared_query = getattr(CodeGen, "_tuple_storage_query", None)
    assert callable(shared_query), (
        "CodeGen has no _tuple_storage_query: there is no shared tuple "
        "storage to compare with separate inference"
    )
    written = _written_tuple_declarations(source)
    assert written, "this context writes no tuple declaration"
    shared = transpile(source)

    def on_its_own(emitter, node, infer):
        # Infer every storage query on its own, as each read did before
        # the queries shared: no declaration, written call or scope may be
        # typed by an answer inferred for another.
        return infer(node)

    asked: dict[str, list[_StorageDemand]] = {}
    translated: dict[str, str] = {}
    for mode, answer in (("shared", shared_query), ("on its own", on_its_own)):
        with monkeypatch.context() as patch:
            asked[mode] = _observe_storage_queries(patch, answer)
            translated[mode] = transpile(source)

    # Equal translations measure nothing unless the emitter asked the patched
    # query while it translated this source: each time as the emitter, for a
    # tuple declaration this source writes, with the inference that types
    # it, and taking the answer back.
    for mode, demands in asked.items():
        assert demands, f"the emitter never asked its storage query ({mode})"
        for demand in demands:
            assert demand.by_emitter and demand.inference, mode
            assert demand.declaration and demand.position in written, (
                mode, demand.position,
            )
            assert written[demand.position] == demand.width, (
                mode, demand.position,
            )
            assert demand.answered, (mode, demand.position)

    # Observing the emitter's own query changes nothing, and neither does
    # inferring every query on its own.
    assert translated["shared"] == shared
    assert translated["on its own"] == shared

    # The queries the emitter made while no other was running are the same
    # either way: the same declarations in the same order, inside the same
    # callable, each with the same answer.
    def outermost(mode: str) -> list[tuple]:
        return [
            (demand.position, demand.inside, demand.types)
            for demand in asked[mode] if demand.outermost
        ]

    assert outermost("on its own") == outermost("shared")


class _StorageQuery(TypeInferer):
    """The emitter's real storage query, without a whole emitter."""


def test_tuple_storage_answer_ends_with_its_query_context_scan_and_guards() -> None:
    query = _StorageQuery()
    outer, inner, third = (
        TupleAssign(names=[name]) for name in ("outer", "inner", "third")
    )
    guard = "_tuple_udt_type_queries"
    inferred: list[str] = []

    def infer_inner(node):
        inferred.append("inner")
        return ["double"]

    def ask_inner():
        return query._tuple_storage_query(inner, infer_inner)

    def outer_query(step) -> tuple[list[str], list[str]]:
        """One outermost query asking for ``inner`` before and after ``step``."""
        def infer_outer(node):
            types = ask_inner()
            step()
            return types + ask_inner()

        del inferred[:]
        return query._tuple_storage_query(outer, infer_outer), list(inferred)

    def nothing() -> None:
        pass

    # One query infers a declaration once; the next query infers it again.
    assert outer_query(nothing) == (["double", "double"], ["inner"])
    assert outer_query(nothing) == (["double", "double"], ["inner"])
    assert "_tuple_storage_memo" not in vars(query)

    # The callee context is compared by value: an equal one shares, and
    # another parameter family is inferred for itself.
    def bind(cpp_type: str):
        def step() -> None:
            query._current_func_param_types = {"x": cpp_type}
        return step

    bind("double")()
    assert outer_query(bind("double"))[1] == ["inner"]
    assert outer_query(bind("int64_t"))[1] == ["inner", "inner"]
    del query._current_func_param_types

    # A width scan can change an answer: nothing inferred before one starts
    # is shared after it, while the queries it makes share within themselves.
    def width_scan() -> None:
        with query._int_width_scan():
            pass

    assert outer_query(width_scan) == (["double", "double"], ["inner", "inner"])
    with query._int_width_scan():
        assert outer_query(nothing)[1] == ["inner"]

    # An inference that met a running declaration holds the guard's fallback,
    # not a type: it is inferred again for every read.
    def infer_meeting_outer(node):
        inferred.append("inner")
        met = query._tuple_storage_guard(guard, id(outer)) is None
        return ["bool"] if met else ["double"]

    def infer_outer_cycle(node):
        return (query._tuple_storage_query(inner, infer_meeting_outer)
                + query._tuple_storage_query(inner, infer_meeting_outer))

    del inferred[:]
    running = query._tuple_storage_guard(guard, id(outer))
    running.add(id(outer))
    try:
        assert query._tuple_storage_query(outer, infer_outer_cycle) == ["bool", "bool"]
    finally:
        running.remove(id(outer))
    assert inferred == ["inner", "inner"]

    # An answer that passed a guard is not reused while that declaration
    # runs, and holds again once it stopped.
    def infer_passing_third(node):
        inferred.append("inner")
        passed = query._tuple_storage_guard(guard, id(third)) is not None
        return ["double"] if passed else ["bool"]

    def ask_passing():
        return query._tuple_storage_query(inner, infer_passing_third)

    def infer_around_third(node):
        types = ask_passing() + ask_passing()
        running = query._tuple_storage_guard(guard, id(third))
        running.add(id(third))
        try:
            types += ask_passing()
        finally:
            running.remove(id(third))
        return types + ask_passing()

    del inferred[:]
    assert query._tuple_storage_query(outer, infer_around_third) == [
        "double", "double", "bool", "double",
    ]
    assert inferred == ["inner", "inner"]
