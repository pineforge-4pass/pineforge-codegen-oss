"""nz and fixnan take a numeric, bool or color source; Pine v6 refuses a string."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp


HEADER = '//@version=6\nstrategy("na functions of a string")\n'


@pytest.mark.parametrize(
    "body,function,line,col,end_col",
    [
        pytest.param(
            'string msg = str.tostring(close)\nstring result = nz(msg, "fallback")\n'
            "plot(str.length(result))\n",
            "nz", 4, 20, 23,  # The string source `msg` spans columns 20 through 22.
            id="nz-with-replacement",
        ),
        pytest.param(
            "string msg = str.tostring(close)\nstring result = nz(msg)\n"
            "plot(str.length(result))\n",
            "nz", 4, 20, 23,
            id="nz-source-only",
        ),
        pytest.param(
            "string msg = str.tostring(close)\nstring result = fixnan(msg)\n"
            "plot(str.length(result))\n",
            "fixnan", 4, 24, 27,  # `fixnan(` ends at column 23; the source spans 24 through 26.
            id="fixnan",
        ),
        pytest.param(
            "msg = str.tostring(close)\nstring result = nz(msg)\nplot(str.length(result))\n",
            "nz", 4, 20, 23,  # An undeclared variable initialized by a str call is a string too.
            id="nz-of-an-inferred-string-variable",
        ),
        pytest.param(
            "string result = nz(str.tostring(close))\nplot(str.length(result))\n",
            "nz", 3, 32, None,  # The source is the str.tostring(close) call; a call sits at its parenthesis.
            id="nz-of-a-str-call",
        ),
        pytest.param(
            'string result = nz("abc", "x")\nplot(str.length(result))\n',
            "nz", 3, 20, None,  # The source is the string literal "abc".
            id="nz-of-a-string-literal",
        ),
    ],
)
def test_nz_and_fixnan_refuse_a_string_source(
    body: str, function: str, line: int, col: int, end_col: int | None,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body, filename="na_function.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2047"
    assert diagnostic.args == {"callee_name": function}
    assert diagnostic.message == (
        f"Cannot call '{function}' with a string argument: "
        "its source must be a numeric, bool or color series."
    )
    assert render_diagnostic(diagnostic.code, diagnostic.args) == (
        diagnostic.message, diagnostic.hint,
    )
    assert diagnostic.location.file == "na_function.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
    if end_col is not None:
        assert diagnostic.location.end_col == end_col


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("x = nz(close - open)\nplot(x)\n", id="nz-float"),
        pytest.param("x = nz(close - open, 1.5)\nplot(x)\n", id="nz-float-with-replacement"),
        pytest.param("x = nz(bar_index)\nplot(x)\n", id="nz-int"),
        pytest.param("x = fixnan(close - open)\nplot(x)\n", id="fixnan-float"),
        pytest.param(
            "tfm = timeframe.multiplier * 1\nx = nz(close * tfm)\nplot(x)\n",
            id="nz-of-a-timeframe-product",
        ),
        # The string is converted before the na function sees it.
        pytest.param(
            "string msg = str.tostring(close)\nx = nz(str.length(msg), 0)\nplot(x)\n",
            id="nz-of-a-string-length",
        ),
        pytest.param(
            "string msg = str.tostring(close)\nx = nz(str.tonumber(msg), 0.0)\nplot(x)\n",
            id="nz-of-a-parsed-number",
        ),
    ],
)
def test_nz_and_fixnan_keep_their_numeric_sources(
    body: str, request: pytest.FixtureRequest,
) -> None:
    cpp = transpile(HEADER + body)
    compile_cpp(cpp, label=request.node.name)
