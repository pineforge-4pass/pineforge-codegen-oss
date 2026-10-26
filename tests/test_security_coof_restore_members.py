"""Requested progression survives COOF; chart state and merge cells roll back."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile_full


_CHART = """//@version=6
strategy("requested restore inventory", calc_on_order_fills=true)
var float chartOwned = 0.0
varip int callbacks = 0
chartOwned += close
callbacks += 1
chartAverage = ta.ema(close, 3)
chartPrevious = close[1]
plot(chartOwned + chartAverage + chartPrevious)
"""
_MATRIX = "var matrix<float> chartGrid = matrix.new<float>(1, 1, 0.0)\n"
_PAYLOADS = (
    (
        "numeric helper and seed",
        "h() =>\n    var int n = 0\n    n += 1\n    n\n",
        "h()",
    ),
    (
        "string helper and seed",
        'h() =>\n    var string state = ""\n'
        '    state := close > open ? "up" : "down"\n    state\n',
        "h()",
    ),
    (
        "TA binding variants",
        "h(float src) => ta.sma(src, 2)\n",
        "h(close) + h(open)",
    ),
    (
        "OHLC and TA histories and clock",
        "",
        "close[1] + ta.ema(close, 2)[1] + bar_index",
    ),
    (
        "mutable global and latch",
        "var float total = 0.0\ntotal += close\n",
        "total",
    ),
    (
        "expression and session histories",
        "h() => close * 2\n",
        "h()[1] + (session.ismarket[1] ? 1.0 : 0.0)",
    ),
    (
        "argument history ring",
        "f(x) => x[2] - x\n",
        "f(close - open)",
    ),
)


def _hook(cpp: str, name: str) -> str:
    start = cpp.index(f"    void {name}() override {{\n")
    return cpp[start:cpp.index("\n    }\n", start)]


def _inventory(cpp: str) -> dict[str, int]:
    start = cpp.index("    struct _PFScriptState {\n")
    end = cpp.index("    };", start)
    fields = re.findall(
        r"decltype\(GeneratedStrategy::(\w+)\)[^;\n]* _pf_value_(\d+);",
        cpp[start:end],
    )
    assert fields, cpp[start:end]
    assert [int(index) for _, index in fields] == list(range(len(fields)))
    inventory = {name: int(index) for name, index in fields}
    assert len(inventory) == len(fields)

    snapshot = _hook(cpp, "snapshot_script_state")
    if ">::take(" in snapshot:
        copied = re.findall(r">::take\((\w+)\),", snapshot)
    else:
        copied = re.findall(r"^\s+(\w+),$", snapshot, re.M)
    assert copied == list(inventory), snapshot
    assert _hook(cpp, "commit_script_state").split("{", 1)[1].strip() == (
        "snapshot_script_state();"
    )
    return inventory


def _restored(cpp: str) -> dict[str, int]:
    restore = _hook(cpp, "restore_script_state")
    assignments = re.findall(
        r"this->(\w+)(?: = |, )_pf_script_state_checkpoint_->_pf_value_(\d+)",
        restore,
    )
    assert len(assignments) == len(set(name for name, _ in assignments)), restore
    return {name: int(index) for name, index in assignments}


def _evaluators(cpp: str) -> list[str]:
    return re.findall(
        r"void _eval_security_\d+\(const Bar& bar, bool is_complete\) \{\n"
        r"(.*?)\n    \}",
        cpp,
        re.S,
    )


def _referenced_members(body: str, inventory: dict[str, int]) -> set[str]:
    return set(re.findall(r"\b\w+\b", body)) & inventory.keys()


def _helper_maps(cpp: str) -> set[str]:
    return set(re.findall(
        r"std::unordered_map<std::string, Series<[^;]+>> (\w+);", cpp,
    ))


def _result_cells(inventory: dict[str, int]) -> set[str]:
    return {name for name in inventory if name.startswith("_req_sec_")}


_FUNCTION_HEAD = re.compile(
    r"^\s*(?:[\w:<>,\s\*&]+?)\s+(\w+)\((?:[^;{}]*)\)\s*(?:const)?\s*(?:override)?\s*\{\s*$"
)
_LIFECYCLE_RESETS = {"prepare_script_run", "clear_security"}


def _chart_side_mentions(cpp: str, skipped: set[str]) -> list[str]:
    """Lines outside the evaluators, the rollback hooks, the state struct,
    plain declarations, constructor initialiser lists and the lifecycle
    resets that name a member the restore no longer touches. The chart body
    never reads requested progression directly; it reads result cells.

    One documented exception (a corpus fixture, not asserted here): a
    requested TA whose length is a var input is re-created by the chart's
    first-bar block, as before. That block writes the object; it touches no
    history or map, and the restore used to hand it back to a fresh object
    the same way."""
    for body in _evaluators(cpp):
        cpp = cpp.replace(body, "")
    for hook in ("snapshot_script_state", "restore_script_state", "commit_script_state"):
        cpp = cpp.replace(_hook(cpp, hook), "")
    start = cpp.index("    struct _PFScriptState {\n")
    cpp = cpp[:start] + cpp[cpp.index("    };", start):]
    function = "<class>"
    found: list[str] = []
    for line in cpp.splitlines():
        head = _FUNCTION_HEAD.match(line)
        if head:
            function = head.group(1)
        if function in _LIFECYCLE_RESETS or line.strip().startswith("explicit GeneratedStrategy()"):
            continue
        for name in skipped:
            if not re.search(rf"\b{re.escape(name)}\b", line):
                continue
            if re.match(
                rf"^\s*[\w:<>,\s\*&]+?\s+{re.escape(name)}\s*(?:=[^;]*|\{{[^;]*\}})?;\s*$",
                line,
            ):
                continue
            found.append(f"{function}: {line.strip()}")
    return found


@pytest.mark.parametrize("matrix", ("", _MATRIX), ids=("ordinary", "handles"))
@pytest.mark.parametrize("label,helpers,payload", _PAYLOADS, ids=[p[0] for p in _PAYLOADS])
def test_requested_members_are_copied_but_never_restored(matrix, label, helpers, payload):
    result = transpile_full(
        _CHART + matrix + helpers
        + 'finer = request.security(syminfo.tickerid, "5", '
        + payload + ", lookahead=barmerge.lookahead_on)\n"
    )
    cpp = result["cpp"]
    assert not any("keeps its user calls" in d.message for d in result["diagnostics"])
    inventory = _inventory(cpp)
    bodies = _evaluators(cpp)
    assert len(bodies) == 1
    results = _result_cells(inventory)
    assert results
    progression = _referenced_members(bodies[0], inventory) - results
    assert progression, (label, bodies[0])
    progression |= _helper_maps(cpp)
    assert _restored(cpp) == {
        name: index for name, index in inventory.items() if name not in progression
    }, (label, _hook(cpp, "restore_script_state"))
    assert results <= _restored(cpp).keys()
    assert _chart_side_mentions(cpp, set(inventory) - set(_restored(cpp))) == []
    assert {"chartOwned", "chartAverage", "chartPrevious"} <= _restored(cpp).keys()
    assert "callbacks" not in inventory
    assert "_pf_var_init_callbacks" not in inventory
    if matrix:
        assert "chartGrid" in _restored(cpp)
        assert ">::restore(" in _hook(cpp, "restore_script_state")
    else:
        assert ">::restore(" not in _hook(cpp, "restore_script_state")


_FALLBACK = """float g = close - open
method m(float x) => x * 2
fallback = request.security(syminfo.tickerid, "5", g.m() - nz(g[1]) + close[1] + ta.ema(close, 2)[1] + bar_index, lookahead=barmerge.lookahead_on)
"""


@pytest.mark.parametrize("matrix", ("", _MATRIX), ids=("ordinary", "handles"))
@pytest.mark.parametrize("mixed", (False, True), ids=("fallback", "mixed"))
def test_chart_fallback_keeps_its_members_in_restore(matrix, mixed):
    clean = (
        'clean = request.security(syminfo.tickerid, "5", '
        "ta.sma(close, 3)[1] + bar_index, lookahead=barmerge.lookahead_on)\n"
        if mixed else ""
    )
    result = transpile_full(_CHART + matrix + _FALLBACK + clean)
    cpp = result["cpp"]
    assert any("keeps its user calls under builtin calls and its methods on the chart's bar"
               in d.message for d in result["diagnostics"])
    inventory = _inventory(cpp)
    bodies = _evaluators(cpp)
    assert len(bodies) == (2 if mixed else 1)
    fallback_members = _referenced_members(bodies[0], inventory)
    assert re.search(r"\b_udt_float_m(?:_cs\d+)?\(", bodies[0]), bodies[0]
    assert re.search(r"\b\w+\.compute\(", bodies[0]), bodies[0]
    assert re.search(r"\b\w+\.push\(", bodies[0]), bodies[0]
    assert re.search(r"\+\+\w+", bodies[0]), bodies[0]
    assert fallback_members <= _restored(cpp).keys()
    assert _helper_maps(cpp) <= _restored(cpp).keys()
    clean_progression = (
        _referenced_members(bodies[1], inventory) - _result_cells(inventory)
        if mixed else set()
    )
    if mixed:
        assert clean_progression
        assert not (clean_progression & fallback_members)
    assert _restored(cpp) == {
        name: index for name, index in inventory.items() if name not in clean_progression
    }


@pytest.mark.parametrize("matrix", ("", _MATRIX), ids=("ordinary", "handles"))
@pytest.mark.parametrize("request_source", (
    '[a, b] = request.security(syminfo.tickerid, "5", [close[1], bar_index], '
    "lookahead=barmerge.lookahead_on)\n",
    'lower = request.security_lower_tf(syminfo.tickerid, "1", close[1] + bar_index)\n',
), ids=("tuple", "lower_tf"))
def test_tuple_and_lower_tf_result_cells_still_restore(matrix, request_source):
    result = transpile_full(_CHART + matrix + request_source)
    cpp = result["cpp"]
    assert not any("keeps its user calls" in d.message for d in result["diagnostics"])
    inventory = _inventory(cpp)
    bodies = _evaluators(cpp)
    assert len(bodies) == 1
    results = _result_cells(inventory)
    assert results
    progression = _referenced_members(bodies[0], inventory) - results
    assert progression
    progression |= _helper_maps(cpp)
    assert _restored(cpp) == {
        name: index for name, index in inventory.items() if name not in progression
    }
    assert results <= _restored(cpp).keys()
    assert _chart_side_mentions(cpp, set(inventory) - set(_restored(cpp))) == []


@pytest.mark.parametrize("matrix", ("", _MATRIX), ids=("ordinary", "handles"))
def test_without_requests_every_snapshot_member_is_restored(matrix):
    cpp = transpile_full(_CHART + matrix)["cpp"]
    assert not _evaluators(cpp)
    assert _restored(cpp) == _inventory(cpp)
