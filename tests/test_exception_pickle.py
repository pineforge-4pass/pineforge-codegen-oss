import copy
import multiprocessing
import pickle
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from pineforge_codegen import transpile_full
from pineforge_codegen.ast_nodes import ArgOrder, FuncCall, Identifier, NumberLiteral
from pineforge_codegen.errors import (
    CodegenInvariantError, CompileError, Diagnostic, Level, Phase, SourceLocation,
)
from pineforge_codegen.lexer import Token, TokenType
from pineforge_codegen.library_inline import _LinkError
from pineforge_codegen.library_v5 import _Refusal
from pineforge_codegen.parser import ParseError
from pineforge_codegen.pine_libraries import LibraryResolveError
from pineforge_codegen.security_contexts import _Fallback, _FeedFallback


def _version_diagnostic(filename="<input>"):
    return Diagnostic(
        Level.ERROR,
        Phase.ANALYZER,
        SourceLocation(filename, 1, 1, 1),
        "PineForge supports PineScript v6 only (found //@version=5).",
        "Migrate the script to //@version=6 using TradingView's editor.",
    )


def _compile_error():
    version = _version_diagnostic("example.pine")
    assert version.code == "PF-E2002"
    assert version.args == {"version": 5}
    note = Diagnostic(
        Level.NOTE, Phase.PARSER, SourceLocation("example.pine", 2, 3, 8),
        "A second diagnostic.",
    )
    error = CompileError([version, note])
    # Its message is a snapshot from construction; the diagnostics can change.
    note.message = "An updated diagnostic."
    return error


def _call():
    arg = NumberLiteral(value=1, loc=SourceLocation("example.pine", 2, 5, 6))
    return FuncCall(
        callee=Identifier(name="sum"),
        args=[arg],
        annotations={"call_arg_order": ArgOrder([arg])},
        loc=SourceLocation("example.pine", 2, 1, 7),
    )


def _assert_diagnostic(restored, original):
    assert type(restored) is Diagnostic
    assert type(restored.location) is SourceLocation
    assert restored == original  # All dataclass fields, including the full span and hint.
    assert type(restored.location.line) is type(restored.location.col) is int
    assert restored.level is original.level
    assert restored.phase is original.phase
    assert type(restored.code) is str
    assert restored.code == original.code
    assert restored.args == original.args
    assert restored.user_message == original.user_message


def _assert_restored(restored, original):
    assert restored is not original
    assert type(restored) is type(original)
    assert str(restored) == str(original)
    assert restored.args == original.args
    assert vars(restored) == vars(original)
    if isinstance(original, CompileError):
        for diagnostic, expected in zip(restored.diagnostics, original.diagnostics, strict=True):
            _assert_diagnostic(diagnostic, expected)
    elif isinstance(original, _LinkError):
        _assert_diagnostic(restored.diagnostic, original.diagnostic)
    elif isinstance(original, ParseError):
        assert type(restored.token) is Token
        assert restored.token.type is TokenType.IDENT
    elif isinstance(original, (CodegenInvariantError, _Refusal)):
        assert type(restored.node) is FuncCall
        assert restored.node.loc == original.node.loc
        assert restored.node.args[0].loc == original.node.args[0].loc
        order = restored.node.annotations["call_arg_order"]
        assert type(order) is ArgOrder
        arg, = order
        assert arg is restored.node.args[0]


@pytest.mark.parametrize("make_error", [
    pytest.param(_compile_error, id="CompileError"),
    pytest.param(lambda: CodegenInvariantError(_call()), id="CodegenInvariantError"),
    pytest.param(
        lambda: ParseError("Unexpected name", Token(TokenType.IDENT, "sum", 2, 3)),
        id="ParseError",
    ),
    pytest.param(lambda: _LinkError(_version_diagnostic()), id="_LinkError"),
    pytest.param(lambda: _Refusal(_call(), "Unsupported v5 library call"), id="_Refusal"),
    pytest.param(lambda: _Fallback([10, 20]), id="_Fallback"),
    pytest.param(lambda: _FeedFallback({10: "Its symbol is not known"}), id="_FeedFallback"),
    pytest.param(
        lambda: LibraryResolveError("No library sources are configured"),
        id="LibraryResolveError",
    ),
])
def test_exception_round_trip(make_error):
    original = make_error()
    original.details = {"sources": ["example.pine"]}
    original.add_note("Additional context.")
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        restored = pickle.loads(pickle.dumps(original, protocol=protocol))
        _assert_restored(restored, original)

    shallow = copy.copy(original)
    _assert_restored(shallow, original)
    assert shallow.details is original.details
    deep = copy.deepcopy(original)
    _assert_restored(deep, original)
    assert deep.details["sources"] is not original.details["sources"]


def _slow_result(release):
    # Keep the other task pending until the parent receives the refusal.
    deadline = time.monotonic() + 25
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("The other task was not released")
        time.sleep(0.02)
    return "done"


def _exercise_process_pool(release):
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        other = pool.submit(_slow_result, release)
        refused = pool.submit(transpile_full, "//@version=5\nstrategy('x')\n")
        error = None
        try:
            refused.result(timeout=15)
        except Exception as exc:
            error = exc
            if type(error) is CompileError:
                assert not other.done()
        finally:
            release.touch()
        other_error = other.exception(timeout=10)
        assert type(error) is CompileError, f"refusal: {error!r}; other task: {other_error!r}"
        expected = _version_diagnostic()
        assert str(error) == f"<input>:1:1: {expected.message}"
        diagnostic, = error.diagnostics
        _assert_diagnostic(diagnostic, expected)
        assert other.result(timeout=0) == "done"


def test_a_refused_script_does_not_break_the_spawn_pool(tmp_path):
    # Bound the entire scenario, including executor shutdown after a timeout.
    command = (
        "import sys; from pathlib import Path; "
        "from tests.test_exception_pickle import _exercise_process_pool; "
        "_exercise_process_pool(Path(sys.argv[1]))"
    )
    log = tmp_path / "pool.log"
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(
            [sys.executable, "-c", command, str(tmp_path / "release")],
            cwd=Path(__file__).resolve().parents[1],
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=40,
        )
    assert result.returncode == 0, log.read_text(encoding="utf-8")
