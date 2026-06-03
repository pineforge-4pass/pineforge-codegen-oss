"""Defensive-guard regression tests for codegen silent fallthroughs (Phase B / F2.*).

Each codegen dispatch site below used to ``return "0"`` / ``return ""`` on an
unknown namespace member or collection method, silently emitting wrong C++ for
valid-looking Pine. These paths are all rejected upstream by
``support_checker`` (or, for the TA-name case, are reachable only via an
internal codegen invariant violation), so the guards are pure safety nets — but
they must *raise* loudly rather than emit garbage if they ever fire.

The constructs are unreachable from a transpile of valid Pine (the analyzer
rejects them first), so these tests construct the minimal AST nodes directly and
invoke the relevant codegen visit method, mirroring the analyzer unit tests.
"""

import pytest

from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.ast_nodes import (
    FuncCall,
    Identifier,
    MemberAccess,
)


def _codegen() -> CodeGen:
    """Build a CodeGen bound to a minimal valid-strategy context.

    We need a real ``AnalyzerContext`` to instantiate ``CodeGen``; the synthetic
    AST nodes used below are then fed directly to individual visit methods,
    bypassing the analyzer (which would reject them upstream — that is exactly
    why the codegen guard is defensive).
    """
    src = '//@version=6\nstrategy("T")\nplot(close)\n'
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    ctx = Analyzer(ast).analyze()
    return CodeGen(ctx)


# --- F2.1: timeframe.<unknown> member access ---------------------------------


def test_unknown_timeframe_member_raises():
    cg = _codegen()
    node = MemberAccess(object=Identifier(name="timeframe"), member="bogus")
    with pytest.raises(ValueError, match=r"unhandled timeframe\.bogus"):
        cg._visit_member_access(node)


def test_known_timeframe_member_still_ok():
    """Sanity: a real timeframe member must NOT raise (guard is narrow)."""
    cg = _codegen()
    node = MemberAccess(object=Identifier(name="timeframe"), member="period")
    assert cg._visit_member_access(node) == "script_tf_"


# --- F2.2: syminfo.<unknown> member access -----------------------------------


def test_unknown_syminfo_member_raises():
    cg = _codegen()
    node = MemberAccess(object=Identifier(name="syminfo"), member="bogus")
    with pytest.raises(ValueError, match=r"unhandled syminfo\.bogus"):
        cg._visit_member_access(node)


def test_known_syminfo_member_still_ok():
    cg = _codegen()
    node = MemberAccess(object=Identifier(name="syminfo"), member="mintick")
    # Should resolve to whatever SYMINFO_MEMBER_MAP maps mintick to, not raise.
    assert cg._visit_member_access(node)  # non-empty, no exception


# --- F2.3: strategy.<unknown>(...) call catch-all ----------------------------


def test_unknown_strategy_call_raises():
    cg = _codegen()
    node = FuncCall(
        callee=MemberAccess(object=Identifier(name="strategy"), member="bogus"),
        args=[],
    )
    with pytest.raises(ValueError, match=r"unhandled strategy\.bogus"):
        cg._visit_strategy_call("bogus", node)


# --- F2.4: bare barssince(...) -----------------------------------------------


def test_bare_barssince_call_raises():
    cg = _codegen()
    node = FuncCall(callee=Identifier(name="barssince"), args=[])
    with pytest.raises(ValueError, match=r"bare barssince"):
        cg._visit_func_call(node)


# --- F2.5: array / map unknown method ----------------------------------------


def test_unknown_array_method_raises():
    cg = _codegen()
    with pytest.raises(ValueError, match=r"unhandled array method 'bogus'"):
        cg._array_method_expr("arr", "bogus", [])


def test_unknown_map_method_raises():
    cg = _codegen()
    with pytest.raises(ValueError, match=r"unhandled map method 'bogus'"):
        cg._map_method_expr("m", "bogus", [])


# --- F2.6: malformed TA member name ------------------------------------------


class _FakeTASite:
    """Minimal stand-in for TACallSite carrying only the member_name field used
    by ``_ta_name_from_site``. A real site always follows the
    ``_ta_<name>_<n>`` convention; a shorter name is an internal-bug signal."""

    def __init__(self, member_name: str) -> None:
        self.member_name = member_name


def test_malformed_ta_member_name_raises():
    cg = _codegen()
    # "_ta".split("_") == ['', 'ta'] -> 2 parts -> malformed branch.
    with pytest.raises(ValueError, match=r"malformed TA member name"):
        cg._ta_name_from_site(_FakeTASite("_ta"))


def test_well_formed_ta_member_name_ok():
    cg = _codegen()
    assert cg._ta_name_from_site(_FakeTASite("_ta_rsi_1")) == "rsi"
    assert cg._ta_name_from_site(_FakeTASite("_ta_vwap_bands_2")) == "vwap_bands"
