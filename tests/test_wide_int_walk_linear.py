"""Width reachability visits shared values once within each query."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.types import TypeInferer
from tests.test_bool_string_inference import native_env, _messages, _run_native


HEADER = '//@version=6\nstrategy("Integer width reachability", overlay=true)\n'


def _self_reassignments(size):
    return ("var int n = 0\nif bar_index >= 0\n"
            + "    n := n + 1\n" * size
            + f"observed = n + 0\nreference = (bar_index + 1) * {size}\n")


def _doubled_declarations(size):
    return ("int value0 = bar_index\n"
            + "".join(f"int value{i} = value{i - 1} + value{i - 1}\n"
                      for i in range(1, size + 1))
            + f"observed = value{size} + 0\nreference = bar_index * {1 << size}\n")


def _doubled_callables(size):
    return ("value0() => bar_index\n"
            + "".join(f"value{i}() => value{i - 1}() + value{i - 1}()\n"
                      for i in range(1, size + 1))
            + f"observed = value{size}()\nreference = bar_index * {1 << size}\n")


LARGE_GRAPHS = [
    pytest.param(_self_reassignments, 16, id="self-reassignments-16"),
    pytest.param(_doubled_declarations, 20, id="doubled-declarations-20"),
    pytest.param(_doubled_callables, 12, id="doubled-callables-12"),
]


@pytest.mark.parametrize("body, size", LARGE_GRAPHS)
def test_shared_width_graph_translates(body, size):
    # Completion is the assertion: these graphs exhaust every narrow path.
    transpile(HEADER + body(size) + "plot(observed)\n")


@pytest.mark.parametrize("body, size", LARGE_GRAPHS)
def test_shared_width_graph_matches_pine_reference(
        tmp_path, native_env, body, size):
    source = (HEADER + body(size)
              + 'log.info("observed {0} {1}", bar_index, observed)\n'
              + 'log.info("Pine reference {0} {1}", bar_index, reference)\n'
              + 'log.info("ok {0} {1}", bar_index, observed == reference ? 1 : 0)\n')
    stderr = _run_native(tmp_path, native_env, source)
    count = native_env[2]
    assert _messages(stderr, "observed", count) == _messages(
        stderr, "Pine reference", count)
    assert _messages(stderr, "ok", count) == {bar: "1" for bar in range(count)}


def _max_query_calls(monkeypatch, method_name, source, limit=None):
    walk = getattr(TypeInferer, method_name)
    depth = calls = maximum = 0

    def counted(self, *args, **kwargs):
        nonlocal depth, calls, maximum
        if depth == 0:
            calls = 0
        calls += 1
        # Fail while walking, so a factorial regression reports an assertion
        # without needing to finish its larger query. This bounds work, not time.
        if limit is not None:
            assert calls <= limit, (
                f"{method_name} exceeded {limit} calls in one query: {calls}")
        depth += 1
        try:
            return walk(self, *args, **kwargs)
        finally:
            depth -= 1
            if depth == 0:
                maximum = max(maximum, calls)

    with monkeypatch.context() as patch:
        patch.setattr(TypeInferer, method_name, counted)
        transpile(source)
    assert maximum > 0, f"{method_name} was not exercised"
    return maximum


@pytest.mark.parametrize("method_name, body", [
    pytest.param("_crosses_wide_int_boundary", _self_reassignments,
                 id="boundary-self-reassignments"),
    pytest.param("_crosses_wide_int_boundary", _doubled_declarations,
                 id="boundary-doubled-declarations"),
    pytest.param("_expr_returns_wide_int", _doubled_declarations,
                 id="provenance-doubled-declarations"),
    pytest.param("_func_int_return_uses_wide_history", _doubled_callables,
                 id="return-doubled-callables"),
])
def test_width_query_call_count_is_linear(monkeypatch, method_name, body):
    # Translation issues many independent queries. Compare the largest walk,
    # keeping the number of query roots separate from each walk's complexity.
    small = _max_query_calls(
        monkeypatch, method_name, HEADER + body(6) + "plot(observed)\n")
    large = _max_query_calls(
        monkeypatch, method_name, HEADER + body(12) + "plot(observed)\n",
        limit=3 * small)
    assert 6 <= small < large
    assert 12 <= large <= 3 * small
