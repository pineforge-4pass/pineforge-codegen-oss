"""A package exception survives a pickle round trip (public issue #457).

A ``concurrent.futures.ProcessPoolExecutor`` worker sends an exception it
raised to the parent as a pickle, and ``BaseException`` has a pickle rebuilt by
calling the class with ``args``. ``CompileError`` takes its diagnostics and
keeps the message in ``args``: the parent called it with the message, raised
``AttributeError: 'str' object has no attribute 'location'`` while it read the
worker's result and marked the pool broken, so every task that shared the pool
failed with ``BrokenProcessPool`` in place of the one refused script failing
with its compile message. ``CodegenInvariantError`` takes an AST node: its
pickle loaded, with the message of a ``str`` node.

The classes here are the ones a caller of the package can receive:
``CompileError`` from every ``transpile`` entry point, ``CodegenInvariantError``
from ``CodeGen`` used directly, and ``RequestFeedInventoryError`` from the
request inventory, which has no constructor of its own and always loaded.
"""

from __future__ import annotations

import multiprocessing
import pickle
from concurrent.futures import ProcessPoolExecutor

import pytest

from pineforge_codegen import transpile_full
from pineforge_codegen.ast_nodes import Identifier
from pineforge_codegen.errors import (
    CodegenInvariantError, CompileError, Diagnostic, Level, Phase, SourceLocation,
)
from pineforge_codegen.limits import limit_error
from pineforge_codegen.request_feed_inventory import RequestFeedInventoryError

# The reporter's script: a version 5 declaration, refused at its first line.
REFUSED_SOURCE = "//@version=5\nstrategy('x')\n"

PROTOCOLS = (
    pytest.param(pickle.HIGHEST_PROTOCOL, id="highest"),
    pytest.param(2, id="protocol-2"),
)

# A worker is a new interpreter that imports the package before its first task.
WORKER_TIMEOUT_SECONDS = 120


class _HostError(CompileError):
    """A caller's subclass; it adds nothing of its own."""


def _round_trip(error, protocol):
    return pickle.loads(pickle.dumps(error, protocol))


def _refused_script_error() -> CompileError:
    with pytest.raises(CompileError) as caught:
        transpile_full(REFUSED_SOURCE)
    return caught.value


def _several_diagnostics() -> list[Diagnostic]:
    """An error with a hint, a warning and a note, each at its own position."""
    return [
        Diagnostic(
            Level.ERROR, Phase.ANALYZER, SourceLocation("strategy.pine", 7, 5, 11),
            "Undefined variable 'length'", hint="Declare 'length' above line 7.",
        ),
        Diagnostic(
            Level.WARNING, Phase.PARSER, SourceLocation("strategy.pine", 2, 1, 9),
            "first part; second part",
        ),
        Diagnostic(
            Level.NOTE, Phase.CODEGEN, SourceLocation("lib/trend.pine", 31, 18, 19),
            "café", hint="one line\n    and another",
        ),
    ]


def _diagnostic_facts(diagnostic: Diagnostic) -> tuple:
    location = diagnostic.location
    return (
        diagnostic.code, diagnostic.args, diagnostic.level, diagnostic.phase,
        diagnostic.message, diagnostic.hint,
        (location.file, location.line, location.col, location.end_col),
    )


def _assert_same_compile_error(loaded: CompileError, error: CompileError) -> None:
    # An error with no diagnostic has an empty message and always loaded.
    assert error.diagnostics and str(error)
    assert type(loaded) is type(error)
    assert str(loaded) == str(error)
    assert loaded.args == error.args
    assert all(type(diagnostic) is Diagnostic for diagnostic in loaded.diagnostics)
    assert [_diagnostic_facts(d) for d in loaded.diagnostics] == [
        _diagnostic_facts(d) for d in error.diagnostics
    ]
    # The rendering a caller prints reads every field of every diagnostic.
    assert loaded.format(REFUSED_SOURCE) == error.format(REFUSED_SOURCE)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_refused_script_error_round_trip_keeps_message_and_diagnostics(protocol):
    error = _refused_script_error()
    _assert_same_compile_error(_round_trip(error, protocol), error)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_error_with_several_diagnostics_round_trip_keeps_each_one(protocol):
    error = CompileError(_several_diagnostics())
    # A host can read the codes before it pickles the error: each diagnostic
    # then holds its code beside its fields, and that travels with it.
    codes = [diagnostic.code for diagnostic in error.diagnostics]
    loaded = _round_trip(error, protocol)
    assert [diagnostic.code for diagnostic in loaded.diagnostics] == codes
    _assert_same_compile_error(loaded, error)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_limit_error_round_trip_keeps_its_marker_and_notes(protocol):
    error = limit_error(
        "AST nesting depth exceeds 512 nodes.",
        SourceLocation("deep.pine", 40, 3, 4), Phase.PARSER,
    )
    error.add_note("refused while compiling deep.pine")
    loaded = _round_trip(error, protocol)
    # ``limit`` marks a refusal that no other lowering of the script recovers.
    assert loaded.limit is True
    assert loaded.__notes__ == ["refused while compiling deep.pine"]
    assert vars(loaded).keys() == vars(error).keys()
    _assert_same_compile_error(loaded, error)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_compile_error_subclass_round_trips_as_its_own_class(protocol):
    error = _HostError(_several_diagnostics())
    loaded = _round_trip(error, protocol)
    assert type(loaded) is _HostError
    _assert_same_compile_error(loaded, error)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_codegen_invariant_error_round_trip_keeps_its_message_and_node(protocol):
    node = Identifier(loc=SourceLocation("value.pine", 3, 9, 14), name="close")
    error = CodegenInvariantError(node)
    assert str(error) == "No value lowering for AST node Identifier"
    loaded = _round_trip(error, protocol)
    assert type(loaded) is CodegenInvariantError
    # Its pickle loaded before as well, as "No value lowering for AST node str".
    assert str(loaded) == str(error)
    assert loaded.args == error.args
    assert loaded.node_kind == "Identifier"
    assert loaded.node == node
    assert loaded.node.loc == node.loc


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_request_feed_inventory_error_round_trips(protocol):
    error = RequestFeedInventoryError("primary_chart_timeframe is not a wire token")
    loaded = _round_trip(error, protocol)
    assert type(loaded) is RequestFeedInventoryError
    assert str(loaded) == str(error)
    assert loaded.args == error.args


def test_refused_script_in_a_process_pool_worker_raises_compile_error_in_the_parent():
    expected = _refused_script_error()
    still_running = "the pool still runs a task"
    # ``spawn`` starts the worker as a new interpreter on every platform. With
    # one worker the second task waits behind the refused script: it failed
    # with the pool when the parent could not rebuild the error.
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        refused = pool.submit(transpile_full, REFUSED_SOURCE)
        other = pool.submit(len, REFUSED_SOURCE)
        with pytest.raises(CompileError) as caught:
            refused.result(timeout=WORKER_TIMEOUT_SECONDS)
        assert other.result(timeout=WORKER_TIMEOUT_SECONDS) == len(REFUSED_SOURCE)
        # The pool is not broken: it takes another task and runs it.
        late = pool.submit(len, still_running)
        assert late.result(timeout=WORKER_TIMEOUT_SECONDS) == len(still_running)
    _assert_same_compile_error(caught.value, expected)
