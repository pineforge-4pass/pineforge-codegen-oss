"""TA state a ``request.security`` multi-statement helper reaches: computed
once per requested bar, where the evaluator can compute it.

A multi-statement helper computes its own TA sites where it is inlined, and
the prepass marked every TA site it reached that way -- a global's too
(``s = ta.sma(close, 5)`` read in the body). The evaluator's prologue then
skipped the global's site, so each read computed it again, feeding the
series twice per requested bar when the payload read it twice; a stateful
math reducer (``math.sum``) that was skipped fell through to the scalar
``math.*`` lowering and read ``0.0 /* unsupported: math.sum */``. A global's
TA site is now left to the prologue, and a reducer the prologue did not
compute is computed where the helper is inlined, as any TA site.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile
from tests import _compile as compile_env


def _eval_bodies(cpp: str) -> str:
    return cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]


def test_a_global_ta_a_helper_reads_is_computed_once():
    src = """//@version=6
strategy("global TA through a helper")
s = ta.sma(close, 5)
g() =>
    y = close * 0 + s
    y
v = request.security(syminfo.tickerid, "60", g() + s)
plot(v)
"""
    cpp = transpile(src)
    body = _eval_bodies(cpp)
    member = re.search(r"auto _secval_\w+ = security_series_slot_is_new\(0\) \? (\w+)\.compute", body)
    assert member, body
    assert body.count(f"{member.group(1)}.compute(") == 1, body
    compile_env.compile_cpp(cpp, label="security-global-ta-once")


def test_a_math_reducer_in_a_helper_is_computed_not_zero():
    src = """//@version=6
strategy("math.sum in a helper")
ms = math.sum(close, 5)
g() =>
    y = ms + 1
    y
h() =>
    z = math.sum(close, 3) + 1
    z
v = request.security(syminfo.tickerid, "60", g() + h())
plot(v)
"""
    cpp = transpile(src)
    body = _eval_bodies(cpp)
    assert "unsupported: math.sum" not in body, body
    assert len(re.findall(r"_ta_sum_\d+\w*\.(?:compute|recompute)\(bar\.close\)", body)) >= 2, body
    compile_env.compile_cpp(cpp, label="security-math-sum-helper")
