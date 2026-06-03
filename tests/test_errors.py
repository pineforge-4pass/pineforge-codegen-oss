# tests/test_errors.py
from pineforge_codegen.errors import (
    SourceLocation, Diagnostic, CompileError, Level, Phase
)

def test_diagnostic_creation():
    loc = SourceLocation(file="test.pine", line=5, col=3, end_col=8)
    d = Diagnostic(
        level=Level.ERROR,
        phase=Phase.ANALYZER,
        location=loc,
        message="Undefined variable 'foo'",
        hint="Did you mean 'bar'?"
    )
    assert d.level == Level.ERROR
    assert d.location.line == 5
    assert d.hint == "Did you mean 'bar'?"

def test_compile_error_format():
    source = "x = 1\nvrsi = ta.rsi(close, rsiLen)\ny = 2"
    loc = SourceLocation(file="test.pine", line=2, col=22, end_col=28)
    d = Diagnostic(Level.ERROR, Phase.ANALYZER, loc, "Undefined variable 'rsiLen'")
    err = CompileError([d])
    output = err.format(source)
    assert "error[ANALYZER]" in output
    assert "line 2" in output or ":2:" in output
    assert "rsiLen" in output

def test_compile_error_is_exception():
    d = Diagnostic(Level.ERROR, Phase.LEXER,
                   SourceLocation("f", 1, 1, 1), "bad token")
    err = CompileError([d])
    assert isinstance(err, Exception)
