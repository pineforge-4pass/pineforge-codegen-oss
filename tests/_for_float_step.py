"""Loop-step sources and diagnostic assertions shared by the inventories."""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase


DIVISION_STARTS = {
    "literal-division": ("", "4 / 2", "int"),
    "named-division": ("s = 4 / 2\n", "s", "int"),
    "helper-division": ("startValue() => 4 / 2\n", "startValue()", "int"),
    "series-division": ("", "bar_index / 2", "double"),
}

# prefix, start, end, value twin, twin end, counter family. Twins share all
# declarations and the end expression, differing only in the start expression.
# The default-step sums are 10.5 for odd n and 12 for even n; the integral
# constant sums to 20.
DIVISION_VALUE_STARTS = {
    "fractional-input": (
        "n = input.int(5)\n", "n / 2", "n / 2 + 2", "n * 0.5", "n / 2 + 2", "double",
    ),
    "fractional-series": (
        "n = 5 + bar_index % 2\n",
        "n / 2", "n / 2 + 2", "n * 0.5", "n / 2 + 2", "double",
    ),
    "fractional-constant": ("", "5 / 2", "4.5", "2.5", "4.5", "double"),
    "integral-constant": ("", "4 / 2", "6", "2", "6", "int"),
}
DIVISION_VALUE_SUMS = {
    "fractional-input": (10.5, 10.5),
    "fractional-series": (10.5, 12),
    "fractional-constant": (10.5, 10.5),
    "integral-constant": (20, 20),
}


def loop_step_source(
    start: str, *, prefix: str = "", end: str = "6", step: str | None = "0.5",
) -> str:
    by = "" if step is None else f" by {step}"
    return (
        '//@version=6\nstrategy("Loop step types", currency=currency.NONE)\n'
        + prefix + "float acc = 0.0\n"
        f"for i = {start} to {end}{by}\n"
        "    acc += i\n"
        "plot(acc)\n"
    )


def assert_float_step_refused(
    source: str, *, counter: str = "i", location: tuple[int, int, int] | None = None,
) -> None:
    if location is None:
        headers = [
            (line_number, match)
            for line_number, line in enumerate(source.splitlines(), 1)
            if (match := re.match(
                rf"^\s*for {re.escape(counter)} = .* by (?P<step>.+)$", line,
            ))
        ]
        assert len(headers) == 1
        line_number, header = headers[0]
        location = (line_number, header.start("step") + 1, header.end("step") + 1)
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="loop.pine")
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2079"
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.message == (
        "The step of a 'for' loop has type float, "
        f"but counter '{counter}' has type int."
    )
    assert diagnostic.hint == "Use an int step or a float counter."
    assert diagnostic.args == {
        "step_type": "float", "counter_name": counter, "counter_type": "int",
    }
    assert diagnostic.location.file == "loop.pine"
    assert (
        diagnostic.location.line, diagnostic.location.col, diagnostic.location.end_col,
    ) == location
