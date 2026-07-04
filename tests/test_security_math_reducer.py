"""Regression: a rolling/stateful math reducer (e.g. ``math.sum``) inside
``request.security`` must resolve to its committed ``_secval_*`` helper, not the
scalar-math "unsupported" ``0.0`` fallback.

``request.security(tickerid, "D", math.sum(high[1] - low[1], 10) / 10, ...)``
precomputes ``math.sum`` as a ``math::Sum`` TA site into ``_secval_N`` (exactly
like a ``ta.*`` reducer). But ``_build_security_expr`` routed the ``math.*`` node
to the scalar-math lowering (``_build_security_math_call``, which can only emit
inline scalar ops) BEFORE the TA-site lookup — so it emitted
``0.0 /* unsupported: math.sum */`` and the whole security silently returned 0.
The dispatch must return the committed ``_secval_`` for a math call that is a
precomputed TA site. Scalar math (abs/round/min/max/...) is not a TA site and
must still lower inline.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile


def test_math_sum_in_security_resolves_to_committed_secval():
    src = """//@version=6
strategy("t", overlay=true)
adr = request.security(syminfo.tickerid, "D", math.sum(high[1] - low[1], 10) / 10, lookahead=barmerge.lookahead_off)
if not na(adr) and adr > 0
    strategy.entry("L", strategy.long)
plot(close)
"""
    cpp = transpile(src)
    # The reducer must be precomputed as a rolling math::Sum helper...
    assert "math::Sum" in cpp
    # ...and it must NOT degrade to the scalar-math unsupported fallback.
    assert "/* unsupported: math.sum */" not in cpp
    # The request.security committed value must reference the _secval_ helper
    # (here: (_secval_N / 10)), not a literal 0.0.
    assert re.search(r"_req_sec_\d+\s*=\s*\(_secval_\d+ / 10\)", cpp), cpp


def test_scalar_math_in_security_still_lowers_inline():
    # math.abs is not a TA site: it must keep lowering inline, unaffected by the
    # reducer fix (guards against over-broadly rerouting all math.* calls).
    src = """//@version=6
strategy("t", overlay=true)
d = request.security(syminfo.tickerid, "D", math.abs(close - open), lookahead=barmerge.lookahead_off)
if not na(d) and d > 0
    strategy.entry("L", strategy.long)
plot(close)
"""
    cpp = transpile(src)
    assert "std::abs" in cpp
    assert "/* unsupported: math.abs */" not in cpp
