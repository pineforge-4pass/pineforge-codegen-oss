"""Cold-review regressions for deferred history and generated-name safety."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests import _compile as compile_env


@pytest.mark.parametrize(
    "source",
    [
        '''//@version=6
strategy("keyword map history")
previous(values) => values[1]
observed = previous(values=map.new<string, int>())
''',
        '''//@version=6
strategy("transitive map history")
previous(values) => values[1]
wrapper(values) => previous(values)
observed = wrapper(map.new<string, int>())
''',
        '''//@version=6
strategy("deep keyword map history")
previous(values) => values[1]
middle(values) => previous(values=values)
wrapper(values) => middle(values)
observed = wrapper(values=map.new<string, int>())
''',
        '''//@version=6
strategy("UDT method map history")
type Holder
    int marker
method previous(Holder self, values) => values[1]
var Holder holder = Holder.new(0)
observed = holder.previous(map.new<string, int>())
''',
    ],
)
def test_deferred_map_history_is_rejected_through_all_call_forms(
    source: str,
) -> None:
    with pytest.raises(CompileError, match="History references on map IDs"):
        transpile(source)


@pytest.mark.parametrize(
    "source",
    [
        '''//@version=6
strategy("nested array map-bearing matrix declaration")
type Outer
    array<map<string, int>> items
var matrix<Outer> values = na
''',
        '''//@version=6
strategy("nested array map-bearing matrix constructor")
type Outer
    array<map<string, int>> items
var matrix<Outer> values = matrix.new<Outer>()
''',
        '''//@version=6
strategy("transitive nested array map-bearing matrix")
type Inner
    array<map<string, int>> items
type Outer
    array<Inner> nested
unused(matrix<Outer> values) => 0
observed = 0
''',
    ],
)
def test_nested_array_maps_mark_udts_map_bearing(source: str) -> None:
    with pytest.raises(
        CompileError,
        match="is not supported when the UDT contains a map field",
    ):
        transpile(source)


@pytest.mark.parametrize("persistent_first", [True, False])
def test_cross_callable_persistent_map_and_scalar_history_fail_closed(
    persistent_first: bool,
) -> None:
    owner = '''owner() =>
    var slot = map.new<string, int>()
    slot.put("x", 1)
    slot.size()
'''
    previous = '''previous() =>
    float slot = close
    slot[1]
'''
    body = owner + previous if persistent_first else previous + owner
    source = (
        '//@version=6\nstrategy("cross callable history collision")\n'
        + body
        + 'owner_value = owner()\nprevious_value = previous()\n'
    )
    with pytest.raises(
        CompileError,
        match="conflict with a persistent map local of the same name",
    ):
        transpile(source)


def test_cross_callable_nonpersistent_map_keeps_scalar_history_valid() -> None:
    source = '''//@version=6
strategy("cross callable nonpersistent map")
owner() =>
    slot = map.new<string, int>()
    slot.put("x", 1)
    slot.size()
previous() =>
    float slot = close
    slot[1]
owner_value = owner()
previous_value = previous()
'''
    compile_env.compile_cpp(
        transpile(source), label="cross-callable-nonpersistent-map"
    )


@pytest.mark.parametrize(
    ("source", "expected_token"),
    [
        (
            '''//@version=6
strategy("pair loop UDF collision")
__pf_map_iter_0() => 7
var pairs = map.new<string, int>()
pairs.put("x", 1)
observed = 0
for [key, value] in pairs
    observed := __pf_map_iter_0() + value
''',
            "auto __pf_map_iter_1 = pairs;",
        ),
        (
            '''//@version=6
strategy("pair loop UDT collision")
type __pf_map_iter_0
    int value
var pairs = map.new<string, int>()
pairs.put("x", 1)
var __pf_map_iter_0 observed = na
for [key, value] in pairs
    observed := __pf_map_iter_0.new(value)
''',
            "auto __pf_map_iter_1 = pairs;",
        ),
    ],
)
def test_pair_loop_temporaries_reserve_callable_and_udt_names(
    source: str, expected_token: str
) -> None:
    cpp = transpile(source)
    assert expected_token in cpp
    assert "auto __pf_map_iter_0 = pairs;" not in cpp
    compile_env.compile_cpp(cpp, label="pair-loop-authored-name-collision")
