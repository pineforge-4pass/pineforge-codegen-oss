"""Method parameters cannot be reassigned in Pine v6."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Phase


@pytest.mark.parametrize(
    ("source", "parameter"),
    [
        pytest.param(
            '''//@version=6
strategy("Method receiver")
method adjusted(float self) =>
    self := self + 1.0
    self
if close.adjusted() > close
    strategy.entry("L", strategy.long)
''',
            "self",
            id="receiver-reassignment",
        ),
        pytest.param(
            '''//@version=6
strategy("Method parameter")
method adjusted(float self, float offset) =>
    offset += 1.0
    self + offset
if close.adjusted(2.0) > close
    strategy.entry("L", strategy.long)
''',
            "offset",
            id="ordinary-parameter-compound-write",
        ),
    ],
)
def test_method_parameter_write_has_a_located_diagnostic(
    source: str, parameter: str,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="method-parameter.pine")

    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2059"
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.message == f"Function parameter '{parameter}' cannot be reassigned."
    assert diagnostic.location.file == "method-parameter.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (4, 5)
