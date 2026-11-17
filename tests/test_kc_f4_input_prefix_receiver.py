"""A static TA site must reject precalc for a runtime-assigned UDT receiver."""

from __future__ import annotations

import os
import sys

import pytest


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pineforge_codegen  # noqa: E402
from pineforge_codegen.ast_nodes import Identifier, MemberAccess  # noqa: E402


@pytest.mark.parametrize("receiver", ("inputBox", "positionBox"))
def test_runtime_udt_receiver_does_not_use_input_namespace_precalc(receiver: str):
    source = f"""//@version=6
strategy("na receiver")
type Snapshot
    float value = 0.0
var Snapshot {receiver} = na
plot(ta.sma({receiver}.value, 5))
{receiver} := Snapshot.new(close)
"""
    filename = f"kc-f4-{receiver}.pine"
    public_cpp = pineforge_codegen.transpile(
        source, check_support=True, filename=filename, libraries={}
    )
    ast, _pragmas, _budget = pineforge_codegen._parse_bounded(source, filename)
    assert (ast.annotations or {}).get("parse_recovery_count", 0) == 0
    gen, ctx, cpp, _support, _sites = pineforge_codegen._generate(
        source, True, filename, {}
    )
    assert cpp == public_cpp
    assert "class GeneratedStrategy" in public_cpp

    sites = [site for site in ctx.ta_call_sites if site.owner_func is None]
    assert len(sites) == 1
    site = sites[0]
    assert site.class_name == "ta::SMA"
    assert len(site.compute_args) == 1
    source_arg = site.compute_args[0]
    assert isinstance(source_arg, MemberAccess)
    assert isinstance(source_arg.object, Identifier)
    assert source_arg.object.name == receiver
    assert source_arg.member == "value"

    # The na initializer makes the site static when the call is analyzed,
    # so the later runtime assignment must be caught by the precalc check.
    assert site.is_static is True
    assert gen._ta_site_uses_precalc(site) is False, (
        f"{receiver}.value cannot be precalculated from a runtime-assigned receiver"
    )
