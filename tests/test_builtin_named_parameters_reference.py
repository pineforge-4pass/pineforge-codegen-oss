"""Unrelated callable C++ stays byte-identical to the reference tree.

One row is no longer unrelated: a call of an overloaded function with the result
of an untyped function now binds by the type of that result. That row keeps the
part of its reference comparison that still holds, and pins the new binding and
the values it computes (``_assert_identity_forward_runs_the_float_declaration``).
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import FuncDef
from pineforge_codegen.library_modules import parse_library_module
from tests._compile import run_emitted_tu
from tests._e2e import reference_codegen, transpile_json
from tests._pine_libraries import Layout, library_text
from tests.test_builtin_named_locals_scope import (
    _builtin_history_source, _chart_local_source, _outer_binding_source,
)
from tests.test_function_overload_resolution import NAMED_DEFAULTS, PROBE
from tests.test_method_function_syntax_calls import SCALAR_SOURCE, TUPLE_SOURCE
from tests.test_udf_series_parameter_history import _SOURCE


CPP_REFERENCE = "a156a7d2785c8729e54da7eb01c5d41ddd03df14"


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    assert tree is not None, "The pinned C++ reference commit must be available"
    return tree


_SOURCES = [
    pytest.param(PROBE, (), id="scalar-overloads"),
    pytest.param(NAMED_DEFAULTS, (), id="named-default-overloads"),
    pytest.param(_SOURCE, (), id="ordinary-history-parameters"),
    pytest.param(
        TUPLE_SOURCE.format(receiver="self", call="holder.pair(value=x)"),
        (), id="tuple-method",
    ),
    pytest.param(
        SCALAR_SOURCE.format(receiver="self", call="holder.shifted(value=x)"),
        (), id="scalar-method",
    ),
    pytest.param(
        _builtin_history_source(), (), id="unshadowed-chart-history",
    ),
    pytest.param(
        _builtin_history_source(requested=True), (), id="requested-builtin-history",
    ),
    pytest.param(
        _outer_binding_source(parameter=False), (), id="requested-global-binding",
    ),
    pytest.param(
        _chart_local_source("close", string=False), (), id="builtin-named-local",
    ),
    pytest.param(
        '//@version=6\nstrategy("library function")\n'
        "import pftest/Base/1 as base\nv = base.twice(close)\n",
        ("pftest/Base/1",), id="imported-function",
    ),
    pytest.param(
        '//@version=6\nstrategy("library type")\n'
        "import pftest/Signals/1 as signals\n"
        "b = signals.Bar2.new(low, high)\nv = b.width()\n",
        ("pftest/Signals/1", "pftest/Base/1"), id="imported-method",
    ),
    pytest.param(
        '''//@version=6
strategy("requested UDF history scope")
history(float src) => src[1]
outer(float src) => history(src)
chart = outer(close)
requested = request.security(syminfo.tickerid, "60", outer(close))
requestedLower = request.security_lower_tf(
    syminfo.tickerid, "1", outer(close)
)
''',
        (), id="requested-history-parameters",
    ),
]


def _cpp_pair(source, paths, reference, tmp_path):
    layout = Layout(tmp_path)
    environment = layout.pin_all("callable-history", source, *paths)
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference, extra_env=environment)
    after = transpile_json(pine, extra_env=environment)
    assert before["ok"], before
    assert after["ok"], after
    return before["cpp"], after["cpp"]


# The script of the ``scalar-overloads`` row as TradingView compiled it: the row's
# source and two plot calls, which publish the two values the measurement compared.
_FORWARD_PLOTS = "plot(unknownResult)\nplot(floatResult)\n"
_MEASURED_FORWARD_SCRIPT = '''//@version=6
strategy("function overloads")
probe(float value) => value
probe(int value) => value + 1
identity(value) => value
floatResult = probe(close - open)
intResult = probe(2)
unknownResult = probe(identity(close - open))
plot(unknownResult)
plot(floatResult)
'''
# The bars of the native run, as (open, high, low, close). Every close - open has a
# fraction, of either sign and at several magnitudes. Some are not the decimal
# difference of the prices written here: in binary64, 2094.88 - 2105.37 is not -10.49.
_FORWARD_BARS = (
    (10.0, 15.0, 9.0, 12.5),
    (100.1, 103.0, 99.0, 102.35),
    (2105.37, 2110.0, 2090.0, 2094.88),
    (0.75, 1.0, 0.25, 0.3),
    (64250.1, 64260.0, 64249.0, 64250.4),
)


def _naming(cpp: str, name: str) -> list[str]:
    """The lines of ``cpp`` that name ``name`` and do not name the forwarded result."""
    return [
        line for line in cpp.splitlines()
        if re.search(rf"\b{name}\b", line) and not re.search(r"\bunknownResult\b", line)
    ]


def _assignments(cpp: str, name: str) -> list[str]:
    """The statements of ``cpp`` that assign the script variable ``name``."""
    return [
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith(name + " = ")
    ]


def _forwarded_declarations(cpp: str) -> list[str]:
    """The ``probe`` declarations, by their emitted names, that the one statement
    assigning ``unknownResult`` from the overloaded call runs."""
    statements = [line for line in _assignments(cpp, "unknownResult") if "probe" in line]
    assert len(statements) == 1, statements
    return re.findall(r"\b(\w*probe\w*)\(", statements[0])


def _forward_driver() -> str:
    """Runs each bar on a strategy of its own and prints the three results, the
    floats with every digit a binary64 number needs to be read back exactly."""
    bars = "".join(
        f"        {{{bar[0]!r}, {bar[1]!r}, {bar[2]!r}, {bar[3]!r}, 1.0, {index * 60000}}},\n"
        for index, bar in enumerate(_FORWARD_BARS)
    )
    return r'''
#include <iomanip>
#include <iostream>
#include <limits>
int main() {
    Bar bars[] = {
''' + bars + r'''    };
    std::cout << std::setprecision(std::numeric_limits<double>::max_digits10);
    for (Bar& bar : bars) {
        GeneratedStrategy strategy;
        strategy.run(&bar, 1, "", "");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << strategy.unknownResult << " " << strategy.floatResult << " "
                  << strategy.intResult << "\n";
    }
}
'''


def _assert_identity_forward_runs_the_float_declaration(source, before, after):
    """The ``scalar-overloads`` row: what replaced its whole-C++ comparison.

    ``identity`` declares no type and returns its argument, here the float
    ``close - open``. The reference tree could not prove that type and left the call
    on the last declaration, ``probe(int)``, so it stored the result as an int. The
    call now binds by the value's type and runs ``probe(float)``, so this row's C++
    cannot equal the reference's any more.

    Basis, measured on 2026-11-18. TradingView compiled ``_MEASURED_FORWARD_SCRIPT``
    without an error or a warning. On all 187 completed, aligned pairs of its two
    plots the values were equal, 180 of them with a fractional ``close - open``; a
    bar without a plotted value is not in that count and is not taken as equal.
    PineForge, run natively on the open and close of those 187 bars, returned
    exactly ``close - open`` (the same binary64 number) for both names on every bar,
    and 3 for ``probe(2)``. The measurement covers this script, not every untyped
    forward.
    """
    # The measured script is this row's source and the two plot calls, to the byte.
    assert source + _FORWARD_PLOTS == _MEASURED_FORWARD_SCRIPT
    assert len(_MEASURED_FORWARD_SCRIPT.encode("utf-8")) == 266

    # What still equals the reference: everything before the storage of the
    # forwarded result, and every line of the two calls the change does not reach.
    reference_storage = "    int unknownResult = 0;\n"
    current_storage = "    double unknownResult = 0.0;\n"
    assert before.count(reference_storage) == 1 and current_storage not in before
    assert after.count(current_storage) == 1 and reference_storage not in after
    assert after[:after.index(current_storage)] == before[:before.index(reference_storage)]
    for name in ("floatResult", "intResult"):
        unchanged = _naming(before, name)
        # At least the member and the statement that assigns it.
        assert len(unchanged) >= 2, unchanged
        assert _naming(after, name) == unchanged

    # The binding: the reference ran the last declaration, this tree runs the first,
    # which is the float one. The plot calls leave the three assignments as they are.
    assert _forwarded_declarations(before) == ["probe"]
    assert _forwarded_declarations(after) == ["pf_overload_probe_1"]
    assert "    double pf_overload_probe_1(double value) {\n" in after
    measured = transpile(_MEASURED_FORWARD_SCRIPT)
    for name in ("floatResult", "intResult", "unknownResult"):
        assert _assignments(after, name), name
        assert _assignments(measured, name) == _assignments(after, name)

    # The values, from the bars this test feeds: the forwarded result and the direct
    # float call are both close - open, bit for bit, where the int declaration could
    # only return a whole number. probe(2) is the int declaration's own 2 + 1.
    rows = run_emitted_tu(
        after, _forward_driver(), opt="-O0",
        label="identity result forwarded to an overload",
    ).splitlines()
    assert len(rows) == len(_FORWARD_BARS), rows
    for (open_, _high, _low, close), row in zip(_FORWARD_BARS, rows):
        difference = close - open_
        assert difference != int(difference), "each bar needs a fractional close - open"
        unknown, direct, integer = row.split()
        assert float(unknown) == difference, (row, difference)
        assert float(direct) == difference, (row, difference)
        assert int(integer) == 2 + 1, row


@pytest.mark.parametrize("source,paths", _SOURCES)
def test_scripts_without_builtin_named_parameters_keep_complete_cpp(
    source, paths, reference, tmp_path,
):
    before, after = _cpp_pair(source, paths, reference, tmp_path)
    if source == PROBE:
        _assert_identity_forward_runs_the_float_declaration(source, before, after)
        return
    assert after == before


def test_library_span_keeps_its_existing_private_names(reference, tmp_path):
    module = parse_library_module("pftest/Signals/1", library_text("pftest/Signals/1"))
    (span,) = module.functions["span"]
    assert isinstance(span, FuncDef)
    assert span.params == ["high", "low"]
    source = (
        '//@version=6\nstrategy("library parameter interface")\n'
        "import pftest/Signals/1 as signals\n"
        "v = signals.span(low = high, high = low)\n"
    )
    before, after = _cpp_pair(
        source, ("pftest/Signals/1", "pftest/Base/1"), reference, tmp_path,
    )
    # The inliner qualifies these parameters before the history pass sees them.
    assert "Signals_v1__span__high" in after
    assert "Signals_v1__span__low" in after
    assert after == before
