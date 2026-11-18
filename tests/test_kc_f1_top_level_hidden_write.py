"""Top-level selection writes must not resurrect an earlier scalar fold."""

from __future__ import annotations

import os
import re
import sys

import pytest


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pineforge_codegen  # noqa: E402
from pineforge_codegen.ast_nodes import (  # noqa: E402
    Assignment,
    Identifier,
    IfStmt,
    SwitchStmt,
    VarDecl,
)
from pineforge_codegen.errors import CompileError  # noqa: E402


_PREFIX = """//@version=6
strategy("hidden top-level write")
horizon = 7
horizon := 11
"""


def _compile(selection: str):
    source = _PREFIX + selection + "plot(horizon)\n"
    filename = "kc-f1-top-level-hidden-write.pine"
    public_cpp = pineforge_codegen.transpile(
        source, check_support=True, filename=filename, libraries={}
    )
    ast, _pragmas, _budget = pineforge_codegen._parse_bounded(source, filename)
    assert (ast.annotations or {}).get("parse_recovery_count", 0) == 0
    gen, _ctx, cpp, _support, _sites = pineforge_codegen._generate(
        source, True, filename, {}
    )
    assert cpp == public_cpp
    assert "class GeneratedStrategy" in public_cpp
    return ast, gen


@pytest.mark.parametrize(
    ("selection", "selection_type"),
    (
        pytest.param(
            """choice = if high > low
    horizon := 13
    horizon
else
    horizon
""",
            IfStmt,
            id="if",
        ),
        pytest.param(
            """choice = switch
    high > low =>
        horizon := 13
        horizon
    =>
        horizon
""",
            SwitchStmt,
            id="switch",
        ),
    ),
)
def test_top_level_selection_write_invalidates_scalar_fold(
    selection: str, selection_type: type
):
    ast, gen = _compile(selection)
    bindings = [
        node for node in ast.body
        if isinstance(node, VarDecl) and node.name == "choice"
    ]
    assert len(bindings) == 1
    value = bindings[0].value
    assert isinstance(value, selection_type)
    branch = value.body if isinstance(value, IfStmt) else value.cases[0][1]
    assert isinstance(branch[0], Assignment)
    assert isinstance(branch[0].target, Identifier)
    assert branch[0].target.name == "horizon"
    assert branch[0].op == ":="
    assert "horizon" in gen._find_reassigned_vars()
    assert "horizon" not in gen._stable_runtime_vars
    assert "horizon" not in gen._known_vars


def test_without_selection_binding_preserves_scalar_fold():
    _ast, gen = _compile("")
    assert "horizon" in gen._stable_runtime_vars
    assert gen._known_vars.get("horizon") == 11


@pytest.mark.parametrize(
    "selection",
    (
        pytest.param(
            """choice = if high > low
    horizon
else
    horizon
""",
            id="if",
        ),
        pytest.param(
            """choice = switch
    high > low =>
        horizon
    =>
        horizon
""",
            id="switch",
        ),
    ),
)
def test_read_only_top_level_selection_preserves_scalar_fold(selection: str):
    _ast, gen = _compile(selection)
    assert "horizon" in gen._stable_runtime_vars
    assert gen._known_vars.get("horizon") == 11


def _compile_ema(source: str):
    filename = "stable-selection-length.pine"
    cpp = pineforge_codegen.transpile(
        source, check_support=True, filename=filename, libraries={}
    )
    _gen, ctx, generated_cpp, _support, _sites = pineforge_codegen._generate(
        source, True, filename, {}
    )
    assert (ctx.ast.annotations or {}).get("parse_recovery_count", 0) == 0
    assert generated_cpp == cpp
    assert len(ctx.ta_call_sites) == 1
    site = ctx.ta_call_sites[0]
    assert site.class_name == "ta::EMA"
    return cpp, site.member_name


@pytest.mark.parametrize(
    ("before", "after", "tail"),
    (
        pytest.param(
            """bump(x) =>
    len = x
    len := len + 1
    len
""",
            "",
            "plot(bump(close))\n",
            id="function-local",
        ),
        pytest.param(
            "",
            """for i = 0 to 1
    len = i
    len := len + 1
""",
            "",
            id="for-local",
        ),
        pytest.param(
            "",
            """i = 0
while i < 1
    len = i
    len := len + 1
    i += 1
""",
            "",
            id="while-local",
        ),
        pytest.param(
            "",
            """switch
    high > low =>
        len = 1
        len := len + 1
""",
            "",
            id="switch-local",
        ),
    ),
)
def test_local_namesake_write_preserves_global_ema_fold(
    before: str, after: str, tail: str
):
    source = (
        '//@version=6\nstrategy("function-local namesake")\n'
        + before
        + "len = 10\nlen := 14\n"
        + after
        + "plot(ta.ema(close, len))\n"
        + tail
    )
    cpp, member = _compile_ema(source)
    # The EMA constructor initializer must retain the global literal length.
    assert f"{member}(14)" in cpp


_EMA_PREFIX = """//@version=6
strategy("stable selection length")
preset = input.string("Fast", options=["Fast", "Slow"])
len = 14
len := 12
"""

_STABLE_SWITCH = """switch preset
    "Fast" => len := 9
    "Slow" => len := 21
"""

_STABLE_SWITCH_VALUE = """switch preset
    "Fast" =>
        len := 9
        len
    "Slow" =>
        len := 21
        len
    =>
        len
"""

_STABLE_IF = """if preset == "Fast"
    len := 9
    len
else if preset == "Slow"
    len := 21
    len
else
    len
"""


@pytest.mark.parametrize(
    "selection",
    (
        pytest.param(_STABLE_SWITCH, id="switch-statement"),
        pytest.param(
            """switch
    preset == "Fast" => len := 9
    preset == "Slow" => len := 21
""",
            id="switch-stable-conditions",
        ),
        pytest.param("choice = " + _STABLE_SWITCH_VALUE, id="switch-declaration"),
        pytest.param(
            "choice = 0\nchoice := " + _STABLE_SWITCH_VALUE, id="switch-assignment"
        ),
        pytest.param("choice = " + _STABLE_IF, id="if-declaration"),
        pytest.param("choice = 0\nchoice := " + _STABLE_IF, id="if-assignment"),
    ),
)
def test_stable_selection_write_sizes_ema_from_live_input(selection: str):
    cpp, member = _compile_ema(
        _EMA_PREFIX + selection + "plot(ta.ema(close, len))\n"
    )
    resets = re.findall(
        rf"\b{re.escape(member)} = ta::EMA\(([^\n]*)\);", cpp
    )
    assert resets, "EMA must be reconstructed from the live preset choice"
    for reset in resets:
        # The EMA reset's nested ternary must read the live preset and choose
        # Fast -> 9, Slow -> 21, unmatched -> incoming 12, in source order.
        compact = re.sub(r"[\s()]", "", reset)
        assert re.search(
            r'get_input_string[^?]*==[^?]*"Fast"[^?]*\?9:[^?]*'
            r'get_input_string[^?]*==[^?]*"Slow"[^?]*\?21:12\b',
            compact,
        ), reset


@pytest.mark.parametrize(
    "selection",
    (
        pytest.param(
            """switch
    high > low => len := 9
""",
            id="series-switch-condition",
        ),
        pytest.param(
            """switch close
    1 => len := 9
""",
            id="series-switch-subject",
        ),
        pytest.param(
            """choice = 0
choice := if high > low
    len := 9
    len
else
    len
""",
            id="series-if-assignment",
        ),
        pytest.param(
            """choice = 0
choice := switch
    high > low =>
        len := 9
        len
    =>
        len
""",
            id="series-switch-assignment",
        ),
        pytest.param(
            """for i = 0 to 1
    len := 9
""",
            id="global-for-write",
        ),
        pytest.param(
            """while high > low
    len := 9
    break
""",
            id="global-while-write",
        ),
    ),
)
def test_unmodeled_or_series_global_write_still_refuses_ema(selection: str):
    source = _EMA_PREFIX + selection + "plot(ta.ema(close, len))\n"
    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        pineforge_codegen.transpile(
            source, check_support=True, filename="series-length.pine", libraries={}
        )
