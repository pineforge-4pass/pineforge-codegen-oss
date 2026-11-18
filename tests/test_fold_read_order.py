"""Stable scalar folding must respect the execution point of each read."""

from __future__ import annotations

import re

import pytest

import pineforge_codegen
from pineforge_codegen.ast_nodes import BinOp, FuncCall, FuncDef, Identifier, IfStmt


def _generate(source: str):
    gen, ctx, cpp, _support, _sites = pineforge_codegen._generate(
        source, True, "fold-read-order.pine", {}
    )
    assert (ctx.ast.annotations or {}).get("parse_recovery_count", 0) == 0
    return gen, ctx, cpp


def _calls(gen, root, name: str, namespace=None):
    return [
        node for node in gen._walk_ast(root)
        if isinstance(node, FuncCall)
        and gen._resolve_callee(node.callee) == (name, namespace)
    ]


def _compact(expression: str) -> str:
    return re.sub(r"[\s()]", "", expression)


def test_entry_condition_reads_initial_value_before_later_write():
    gen, ctx, cpp = _generate("""//@version=6
strategy("read before write")
horizon = 7
if bar_index % horizon == 0
    strategy.entry("L", strategy.long)
horizon := 11
plot(horizon)
""")
    condition = next(stmt.condition for stmt in ctx.ast.body if isinstance(stmt, IfStmt))
    assert isinstance(condition, BinOp) and condition.op == "=="
    remainder = condition.left
    assert isinstance(remainder, BinOp) and remainder.op == "%"
    assert isinstance(remainder.right, Identifier)
    # Trace the divisor through the same visitor used in the emitted condition.
    assert gen._visit_expr(remainder.right) == "7"
    assert gen._visit_expr(condition) in cpp
    plot = _calls(gen, ctx.ast, "plot")[0]
    assert gen._visit_expr(plot.args[0]) == "11"


@pytest.mark.parametrize(
    "middle",
    (
        pytest.param("""plot(horizon)
horizon := 11
plot(horizon)
""", id="direct-writes"),
        pytest.param("""if timeframe.isintraday
    plot(horizon)
    horizon := 11
    plot(horizon)
""", id="stable-branch-writes"),
    ),
)
def test_reads_between_writes_use_each_prefix_value(middle: str):
    gen, ctx, _cpp = _generate(
        '//@version=6\nstrategy("successive stable writes")\nhorizon = 7\n'
        + middle
        + "horizon := 13\nplot(horizon)\n"
    )
    plots = _calls(gen, ctx.ast, "plot")
    assert len(plots) == 3
    assert [gen._visit_expr(plot.args[0]) for plot in plots] == ["7", "11", "13"]


def test_ema_before_stable_selection_keeps_earlier_length():
    gen, ctx, cpp = _generate("""//@version=6
strategy("length before selection")
len = 10
plot(ta.ema(close, len))
if timeframe.isintraday
    len := 20
plot(ta.ema(close, len))
""")
    sites = [site for site in ctx.ta_call_sites if site.class_name == "ta::EMA"]
    assert len(sites) == 2
    first, second = sites
    plan = gen._ta_dynamic_plan(first)
    assert plan is not None and plan["kind"] == "first_call"
    first_length = gen._ta_dynamic_ctor_arg(first, plan, 0, gen._visit_expr)
    assert _compact(first_length) == "10"
    assert re.search(r'simple_ta_length\(\(*10\)*,\s*"ema",\s*"length"\)', cpp)
    # The later site retains the final stable fold and its one-time reset.
    assert gen._ta_dynamic_plan(second) is None
    args, runtime = gen._ta_run_ctor_args(second)
    assert runtime and len(args) == 1
    assert "?20:10" in _compact(args[0])
    assert f"{second.member_name} = {second.class_name}({args[0]});" in cpp


@pytest.mark.parametrize("context", ("function", "request"))
def test_read_without_chart_execution_position_uses_runtime_variable(context: str):
    if context == "function":
        middle = """read_average() =>
    ta.ema(close, horizon)
plot(read_average())
"""
    else:
        middle = 'plot(request.security(syminfo.tickerid, "60", ta.ema(close, horizon)))\n'
    gen, ctx, cpp = _generate(
        '//@version=6\nstrategy("deferred scalar read")\nhorizon = 7\n'
        + middle
        + "horizon := 11\nplot(horizon)\n"
    )
    if context == "function":
        root = next(stmt for stmt in ctx.ast.body if isinstance(stmt, FuncDef))
    else:
        root = _calls(gen, ctx.ast, "security", "request")[0].args[2]
    reads = [
        node for node in gen._walk_ast(root)
        if isinstance(node, Identifier) and node.name == "horizon"
    ]
    assert reads
    assert {gen._visit_expr(read) for read in reads} <= {"horizon", "horizon[0]"}
    sites = [site for site in ctx.ta_call_sites if site.class_name == "ta::EMA"]
    assert len(sites) == 1
    site = sites[0]
    plan = gen._ta_dynamic_plan(site)
    assert plan is not None and plan["kind"] == "first_call"
    assert gen._ta_dynamic_ctor_arg(site, plan, 0, gen._visit_expr) in {
        "horizon", "horizon[0]",
    }
    assert (
        f"pineforge::source::FirstCallBound<{site.class_name}>" in cpp
    )


@pytest.mark.parametrize(
    ("bindings", "literal", "reset_pattern"),
    (
        pytest.param(
            "len = 10\nlen := 14\n",
            "14",
            None,
            id="all-reads-after-final-write",
        ),
        pytest.param(
            'preset = input.string("Fast", options=["Fast", "Slow"])\n'
            "len = 14\nlen := 12\n"
            "switch preset\n"
            '    "Fast" => len := 9\n'
            '    "Slow" => len := 21\n',
            None,
            r'get_input_string.*"Fast".*\?9:.*get_input_string.*"Slow".*\?21:12',
            id="stable-selection-after-final-write",
        ),
        pytest.param(
            'len = input.int(10, "Length")\n',
            "10",
            r'get_input_int"Length",10',
            id="input-without-writes",
        ),
    ),
)
def test_final_fold_and_input_constructor_paths_are_preserved(
    bindings: str, literal: str | None, reset_pattern: str | None
):
    gen, ctx, cpp = _generate(
        '//@version=6\nstrategy("stable length control")\n'
        + bindings
        + "plot(ta.ema(close, len))\n"
    )
    sites = [site for site in ctx.ta_call_sites if site.class_name == "ta::EMA"]
    assert len(sites) == 1
    site = sites[0]
    assert gen._ta_dynamic_plan(site) is None
    if literal is not None:
        assert f"{site.member_name}({literal})" in cpp
    args, runtime = gen._ta_run_ctor_args(site)
    assert len(args) == 1
    if reset_pattern is None:
        assert not runtime and args == [literal]
    else:
        assert runtime
        assert re.search(reset_pattern, _compact(args[0]))
        assert f"{site.member_name} = {site.class_name}({args[0]});" in cpp
