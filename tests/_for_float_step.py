"""Diagnostic assertions shared by the loop source inventories."""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase


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
