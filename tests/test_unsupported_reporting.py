"""Reporting-quality tests for unsupported-but-valid-Pine inputs.

Two holes these pin (see also test_compile_smoke.py regression cases):

* Hole 1 — silent miscompile. An unknown bare function call, or a call into
  an unrecognized namespace, used to fall through the codegen generic emitter
  and be written verbatim as an *undeclared C++ symbol* (e.g.
  ``some_made_up_fn(...)``, ``qux::frobnicate(...)``) with NO diagnostic. The
  user only found out via a cryptic g++ error pointing at generated C++, never
  at their Pine line. Codegen now rejects these loudly with the offending
  node's line/col.

* Hole 2 — the line number was unreachable from ``str(CompileError)``. The
  exception's plain message joined only the diagnostic texts, so
  ``except CompileError as e: print(e)`` showed no location. ``str()`` now
  carries ``file:line:col`` per diagnostic, and ``transpile(filename=...)``
  threads a real filename all the way to ``node.loc``.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, SourceLocation, Diagnostic, Level, Phase


PRELUDE = '//@version=6\nstrategy("T")\n'


def _line_of(err: CompileError) -> int:
    return err.diagnostics[0].location.line


# ---------------------------------------------------------------------------
# Hole 1 — unknown calls are rejected (not emitted as undeclared C++)
# ---------------------------------------------------------------------------

def test_unknown_bare_function_rejected_with_line():
    # offending call on line 5
    src = PRELUDE + "\n\n" + "x = some_made_up_fn(close)\n"
    with pytest.raises(CompileError) as exc:
        transpile(src)
    assert _line_of(exc.value) == 5
    assert "some_made_up_fn" in exc.value.diagnostics[0].message


def test_unknown_namespace_call_rejected_with_line():
    src = PRELUDE + "\n\n" + "x = qux.frobnicate(close)\n"
    with pytest.raises(CompileError) as exc:
        transpile(src)
    assert _line_of(exc.value) == 5
    msg = exc.value.diagnostics[0].message
    assert "qux" in msg


def test_unknown_call_line_tracks_position():
    # same offending call deeper in the file; reported line must follow it
    body = "".join(f"a{i} = close\n" for i in range(8))  # lines 3..10
    src = PRELUDE + body + "x = totally_unknown(close)\n"   # line 11
    with pytest.raises(CompileError) as exc:
        transpile(src)
    assert _line_of(exc.value) == 11


# -- guard must NOT fire on legitimate user-defined / UDT calls --

def test_user_defined_function_still_transpiles():
    src = PRELUDE + "f(x) =>\n    x * 2\n" + "y = f(close)\n"
    out = transpile(src)
    assert "f(" in out or "f_" in out  # user func emitted, no error


def test_udt_new_and_copy_still_transpile():
    src = (
        PRELUDE
        + "type P\n    float v = 0.0\n"
        + "p = P.new(1.0)\n"
        + "q = P.copy(p)\n"
    )
    out = transpile(src)  # must not raise
    assert "P" in out


def test_bare_builtins_still_transpile():
    # na / nz / fixnan / casts have dedicated handlers — must keep working
    for stmt in ("x = na(close)", "x = nz(close, 0.0)", "x = fixnan(close)",
                 "x = int(close)", "x = float(1)"):
        transpile(PRELUDE + stmt + "\n")  # no raise


# ---------------------------------------------------------------------------
# Hole 2 — line number reachable from str() and via filename plumbing
# ---------------------------------------------------------------------------

def test_str_of_compile_error_includes_location():
    src = PRELUDE + "\n\n" + 'x = request.financial("A","B","C")\n'  # line 5
    with pytest.raises(CompileError) as exc:
        transpile(src)
    s = str(exc.value)
    assert ":5:" in s, f"expected line in str(e), got: {s!r}"


def test_transpile_filename_threads_to_diagnostics():
    src = PRELUDE + 'x = request.financial("A","B","C")\n'  # line 3
    with pytest.raises(CompileError) as exc:
        transpile(src, filename="my_strategy.pine")
    loc = exc.value.diagnostics[0].location
    assert loc.file == "my_strategy.pine"
    assert loc.line == 3
    assert "my_strategy.pine:3:" in str(exc.value)


def test_compile_error_str_unit():
    # direct unit on CompileError formatting (no transpile)
    loc = SourceLocation(file="f.pine", line=7, col=3, end_col=5)
    err = CompileError([Diagnostic(Level.ERROR, Phase.ANALYZER, loc, "boom")])
    assert "f.pine:7:3" in str(err)
    assert "boom" in str(err)


# ---------------------------------------------------------------------------
# Hole 1b — unknown *variable reads* that would emit undeclared C++ symbols
# ---------------------------------------------------------------------------

def test_undefined_variable_read_rejected_with_line():
    src = PRELUDE + "\n\n" + "x = undefined_var\n"  # line 5
    with pytest.raises(CompileError) as exc:
        transpile(src)
    assert _line_of(exc.value) == 5
    assert "undefined_var" in exc.value.diagnostics[0].message


@pytest.mark.parametrize("var", ["ask", "bid"])
def test_realtime_only_builtin_read_rejected(var):
    # ask / bid are valid Pine v6 builtins with no batch feed; reading them
    # used to emit `x = ask;` — an undeclared C++ symbol. Reject loudly.
    src = PRELUDE + f"x = {var}\n"  # line 3
    with pytest.raises(CompileError) as exc:
        transpile(src)
    assert _line_of(exc.value) == 3
    assert var in exc.value.diagnostics[0].message


def test_supported_reads_still_transpile():
    # a spread of legitimate reads that must NOT be flagged
    ok = [
        "x = close",
        "x = bar_index",
        "x = hl2",
        "x = math.pi",
        "x = syminfo.mintick",
        "s = timeframe.period",
        "x = strategy.equity",
        "x = na",
        "a = close\nx = a",                       # user var
        "len = input.int(14)\nx = ta.sma(close, len)",  # input-backed var
        "f(p) =>\n    p * 2\nx = f(close)",        # function param
        "arr = array.new<float>(0)\nn = array.size(arr)",  # array var
        "var m = matrix.new<int>(2, 2, 0)\nr = m.rows()",  # matrix var
    ]
    for stmt in ok:
        transpile(PRELUDE + stmt + "\n")  # must not raise


def test_user_function_local_var_not_flagged():
    src = PRELUDE + "f(p) =>\n    q = p + 1\n    q * 2\n" + "x = f(close)\n"
    transpile(src)  # must not raise


def test_enum_member_read_not_flagged():
    src = (
        "//@version=6\n"
        "enum Sig\n    Buy\n    Sell\n"
        'strategy("T")\n'
        "x = input.enum(Sig.Buy, \"s\")\n"
    )
    transpile(src)  # must not raise
