"""A rebuilt strategy.convert_to_account/convert_to_symbol call keeps its call site.

``CodeGen._currency_conversion_site`` numbers the conversion calls of the source in
source order. It looked a call up by ``id(node)``, so a copy of a call that the
translator builds after that walk (security timeframe-input substitution, a
replayed global) raised a bare ``AssertionError``. ``transpile()`` converts only
``CodegenInvariantError`` into a ``CompileError``, so the error escaped to the
caller. A call must be found by its source position instead, and nothing but a
``CompileError`` may leave ``transpile()`` for these scripts.
"""

import re
from dataclasses import replace

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import FuncCall, Identifier, MemberAccess
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.limits import iter_ast_nodes
from pineforge_codegen.parser import Parser

_CONVERSION = re.compile(r"strategy\.convert_to_(?:account|symbol)\(")
_SITE = re.compile(r"/\*call_site=\*/(\d+)")

BODIES = {
    "A1_literal_tf": (
        'tf = "60"\n'
        "cv = request.security(syminfo.tickerid, tf, strategy.convert_to_account(close))\n"
        "sv = request.security(syminfo.tickerid, tf, strategy.convert_to_symbol(close))\n"
        "plot(cv + sv)"
    ),
    "A2_input_tf": (
        'tf = input.timeframe("60", "tf")\n'
        "cv = request.security(syminfo.tickerid, tf, strategy.convert_to_account(close))\n"
        "sv = request.security(syminfo.tickerid, tf, strategy.convert_to_symbol(close))\n"
        "plot(cv + sv)"
    ),
    "B1_series_tf": (
        'useA = input.bool(true, "useA")\n'
        'tfA = input.timeframe("60", "A")\n'
        'tfB = input.timeframe("240", "B")\n'
        "tf = useA ? tfA : tfB\n"
        "cv = request.security(syminfo.tickerid, tf, strategy.convert_to_account(close))\n"
        "sv = request.security(syminfo.tickerid, tf, strategy.convert_to_symbol(close))\n"
        "plot(cv + sv)"
    ),
    "C1_global_alias_var": (
        'tf = input.timeframe("60", "tf")\n'
        "var float acc = 0.0\n"
        "acc := acc + strategy.convert_to_account(close)\n"
        "alias = acc\n"
        "r = request.security(syminfo.tickerid, tf, alias)\n"
        "plot(r)"
    ),
    "C2_global_plain": (
        'tf = input.timeframe("60", "tf")\n'
        "base = strategy.convert_to_symbol(close)\n"
        "r = request.security(syminfo.tickerid, tf, base * 2)\n"
        "plot(r)"
    ),
    "C3_global_plain_alias": (
        'tf = input.timeframe("60", "tf")\n'
        "base = strategy.convert_to_account(close)\n"
        "alias = base\n"
        "r = request.security(syminfo.tickerid, tf, alias + strategy.convert_to_symbol(open))\n"
        "plot(r)"
    ),
    "D1_conversion_in_tf_literal_arg": (
        'tf0 = input.timeframe("60", "tf")\n'
        'tf = strategy.convert_to_account(1.0) > 0 ? tf0 : "D"\n'
        "r = request.security(syminfo.tickerid, tf, close)\n"
        "plot(r)"
    ),
    # The timeframe-input substitution rebuilds (dataclasses.replace) a call only when
    # one of its arguments holds an input-derived leaf, so these pass an input in.
    "G1_conversion_in_tf_input_arg": (
        'amt = input.float(1.0, "amt")\n'
        'tf0 = input.timeframe("60", "tf")\n'
        'tf = strategy.convert_to_account(amt) > 0 ? tf0 : "D"\n'
        "r = request.security(syminfo.tickerid, tf, close)\n"
        "plot(r)"
    ),
    "G2_conversion_in_tf_direct_argument": (
        'amt = input.float(1.0, "amt")\n'
        'tf0 = input.timeframe("60", "tf")\n'
        'r = request.security(syminfo.tickerid, strategy.convert_to_symbol(amt) > 0 ? tf0 : "D", close)\n'
        "plot(r)"
    ),
    "G3_conversion_global_alias_in_tf": (
        'amt = input.float(1.0, "amt")\n'
        "conv = strategy.convert_to_account(amt)\n"
        'tf0 = input.timeframe("60", "tf")\n'
        'tf = conv > 0 ? tf0 : "D"\n'
        "r = request.security(syminfo.tickerid, tf, close)\n"
        "plot(r)"
    ),
    "H1_replayed_mutable_global_in_tf": (
        'amt = input.float(1.0, "amt")\n'
        "var float acc = 0.0\n"
        "acc := acc + strategy.convert_to_account(amt)\n"
        'tf = acc > 0 ? "60" : "D"\n'
        "r = request.security(syminfo.tickerid, tf, close)\n"
        "plot(r)"
    ),
    "H2_conversion_input_arg_in_expression": (
        'amt = input.float(1.0, "amt")\n'
        'tf = input.timeframe("60", "tf")\n'
        "r = request.security(syminfo.tickerid, tf, strategy.convert_to_account(amt))\n"
        "plot(r)"
    ),
    "E1_helper": (
        'tf = input.timeframe("60", "tf")\n'
        "f(x) => strategy.convert_to_account(x) + strategy.convert_to_symbol(x)\n"
        "r = request.security(syminfo.tickerid, tf, f(close))\n"
        "plot(r)"
    ),
    "F1_tuple": (
        'tf = input.timeframe("60", "tf")\n'
        "[a, b] = request.security(syminfo.tickerid, tf, "
        "[strategy.convert_to_account(close), strategy.convert_to_symbol(open)])\n"
        "plot(a + b)"
    ),
}


def script(body, dynamic_requests=None):
    extra = "" if dynamic_requests is None else f", dynamic_requests={dynamic_requests}"
    return f'//@version=6\nstrategy("T", overlay=true{extra})\n{body}\n'


def outcome(source):
    """("ok", cpp), ("refused", CompileError) or ("escaped", the exception)."""
    try:
        return "ok", transpile(source)
    except CompileError as error:
        return "refused", error
    except BaseException as error:  # noqa: BLE001 - what leaves transpile() is the subject
        return "escaped", error


def test_a_rebuilt_conversion_call_keeps_the_call_site_of_its_source_call():
    # The timeframe-input substitution rebuilds a call with dataclasses.replace: a new
    # object at the same source span. It must number like the call it was copied from.
    source = script(
        'amt = input.float(1.0, "amt")\n'
        "a = strategy.convert_to_account(amt)\n"
        "b = strategy.convert_to_symbol(amt)\n"
        "c = strategy.convert_to_account(close)"
    )
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    generator = CodeGen(Analyzer(ast).analyze())
    calls = [
        node for node, _depth in iter_ast_nodes(generator.ctx.ast)
        if isinstance(node, FuncCall) and isinstance(node.callee, MemberAccess)
        and isinstance(node.callee.object, Identifier) and node.callee.object.name == "strategy"
        and node.callee.member in ("convert_to_account", "convert_to_symbol")
    ]
    assert len(calls) == 3
    for ordinal, call in enumerate(calls):
        rebuilt = replace(call, args=list(call.args))
        assert rebuilt is not call and rebuilt.loc == call.loc
        assert generator._currency_conversion_site(call) == ordinal
        assert generator._currency_conversion_site(rebuilt) == ordinal


@pytest.mark.parametrize("dynamic_requests", [None, "false"],
                         ids=["dynamic-default", "dynamic-false"])
@pytest.mark.parametrize("name", sorted(BODIES))
def test_rebuilt_conversion_call_never_escapes_transpile(name, dynamic_requests):
    source = script(BODIES[name], dynamic_requests)
    kind, value = outcome(source)
    assert kind != "escaped", f"{type(value).__name__} escaped transpile(): {value}"
    if kind == "refused":
        assert value.diagnostics, "a refusal must carry its located diagnostics"
        return
    calls = len(_CONVERSION.findall(source))
    sites = [int(site) for site in _SITE.findall(value)]
    assert sites, "the conversion helper call carries no call site"
    # A rebuilt copy shares the call site of the call it was copied from.
    assert all(0 <= site < calls for site in sites), (sites, calls)
