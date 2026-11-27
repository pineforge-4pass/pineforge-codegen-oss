"""PF-E5005 points to the written history operator after initializer substitution."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase


@pytest.mark.parametrize("name", [
    "txt", "open", "high", "low", "close", "hl2", "hlc3", "hlcc4", "ohlc4", "volume",
])
def test_unregistered_string_history_reports_bracket(name):
    # The 60-minute request owns a string series. The 240-minute request
    # registers only `held`; replaying its later store reaches a read with
    # no string series on that clock. The successful read has a different
    # source position, as do the initializer and its `close` argument.
    read = f"    previous = {name}[2]"
    source = f"""//@version=6
strategy("location of requested history")
string {name} = str.tostring(close)
h() =>
{read}
    str.length(previous)
kept = request.security(syminfo.tickerid, "60", str.length({name}[1]))
var int held = 0
v = request.security(syminfo.tickerid, "240", held)
held := h()
plot(kept + v)
"""
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="requested-history.pine")
    diagnostic, = caught.value.diagnostics
    assert (diagnostic.level, diagnostic.phase, diagnostic.code) == (
        Level.ERROR, Phase.CODEGEN, "PF-E5005",
    )
    point = diagnostic.location
    assert (point.file, point.line, point.col) == (
        "requested-history.pine", 5, read.index("[") + 1,
    )
