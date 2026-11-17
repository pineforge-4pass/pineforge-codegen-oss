"""Project a frozen C++ reference onto the checked EMA-family emission.

A reference build from before the checked EMA-family adapters
(tests/test_checked_ema_family_adapters.py) differs from this build, for a
script with such an object, in exactly three written places:

* the object's type: ``ta::EMA`` / ``ta::TSI`` / ``ta::MACD`` are the paired
  ``pineforge::source::Pine*`` adapters (``codegen/ta.py::_TA_CHECKED_CLASSES``;
  the two Keltner shims keep their names);
* the include of the engine header that declares them;
* the precalculation replay, when one of those objects is precalculated: the
  replay loop runs inside a handler that lets the run continue only for the
  engine's invalid-argument stop (the real bar path then reports the length at
  the original call), and drops the caches it had begun to fill.

``with_checked_ema_family`` applies those three changes to the reference text
and nothing else, so a whole-C++ comparison keeps guarding every other byte.
Each step requires the reference shape it rewrites: a reference that does not
have it fails here instead of comparing equal by accident. The text below is
the contract, written by hand; it is never produced by a call of the code
under test. The handler's behaviour is tested where it is emitted
(test_checked_ema_family_adapters.py).
"""

from __future__ import annotations

import re

_CHECKED_TYPES = {
    "ta::EMA": "pineforge::source::PineEma",
    "ta::TSI": "pineforge::source::PineTsi",
    "ta::MACD": "pineforge::source::PineMacd",
}
# The classes whose precalculated replay is guarded.
_CHECKED_CLASSES = (*_CHECKED_TYPES, "_PFKC", "_PFKCW")

TA_LENGTH_HEADER = "#include <pineforge/source/pine_ta_length.hpp>\n"
_MATH_HEADER = "#include <pineforge/math.hpp>\n"
_REPLAY_HEADERS = "#include <pineforge/run_failure.hpp>\n#include <exception>\n"

_PRECALCULATE = "    void precalculate(const Bar* bars, int n) {\n"
_METHOD_END = "\n    }\n"
_LOOP_OPEN = "        for (int i = 0; i < n; ++i) {\n"
_LOOP_CLOSE = "\n        }\n"
_REPLAY_DONE = "\n        _use_precalc = true;"
_HANDLER = (
    "            _use_precalc = true;\n"
    "        } catch (const std::exception& _pf_precalc_error) {\n"
    "            const auto* _pf_precalc_failure =\n"
    "                dynamic_cast<const pineforge::RunFailureInfo*>(&_pf_precalc_error);\n"
    "            if (!_pf_precalc_failure ||\n"
    "                _pf_precalc_failure->run_failure_code() !=\n"
    "                    pineforge::RunFailureCode::pine_invalid_argument) throw;\n"
    "            // The real bar path checks the length at the original call.\n"
)


def _type_pattern(name: str) -> str:
    return rf"\b{re.escape(name)}\b"


def _precalculated_members(cpp: str) -> tuple[int, int, list[str]]:
    """``(start, end, members)`` of the reference's ``precalculate`` method.

    ``members`` are the objects it fills a cache for, in emission order.
    """
    assert cpp.count(_PRECALCULATE) == 1, "expected one precalculate method"
    start = cpp.index(_PRECALCULATE)
    end = cpp.index(_METHOD_END, start)
    members = re.findall(
        r"^        _precalc_(\w+)\.resize\(n\);$", cpp[start:end], re.M)
    assert members, "a precalculate method fills at least one cache"
    return start, end, members


def _replay_is_checked(cpp: str, members: list[str]) -> bool:
    """Whether one precalculated object is of a checked class (its member
    declaration in the reference names the class)."""
    classes = "|".join(re.escape(name) for name in _CHECKED_CLASSES)
    return any(
        re.search(rf"^    (?:{classes}) {re.escape(member)};$", cpp, re.M)
        for member in members
    )


def _with_guarded_replay(cpp: str) -> str:
    start, end, members = _precalculated_members(cpp)
    if not _replay_is_checked(cpp, members):
        return cpp
    body = cpp[start:end]
    assert body.count(_LOOP_OPEN) == 1, "expected one replay loop"
    loop_at = body.index(_LOOP_OPEN)
    loop_end = body.index(_LOOP_CLOSE, loop_at) + len(_LOOP_CLOSE)
    tail = body[loop_end:]
    assert tail.endswith("\n" + _REPLAY_DONE), "the reference ends by enabling the caches"
    loop = "".join(
        "    " + line if line.strip() else line
        for line in body[loop_at:loop_end].splitlines(keepends=True)
    )
    guarded = (
        body[:loop_at]
        + "        try {\n"
        + loop
        + _HANDLER
        + "".join(f"            _precalc_{member}.clear();\n" for member in members)
        + "        }\n"
        + tail[:-len(_REPLAY_DONE)]
    )
    assert _MATH_HEADER in cpp[:start], "the includes precede the class"
    assert _REPLAY_HEADERS not in cpp
    return (cpp[:start] + guarded + cpp[end:]).replace(
        _MATH_HEADER, _REPLAY_HEADERS + _MATH_HEADER, 1)


def with_checked_ema_family(cpp: str) -> str:
    """``cpp`` (a reference from before the checked adapters) as this build
    writes the same script; unchanged when it holds no EMA-family object."""
    if not any(re.search(_type_pattern(name), cpp) for name in _CHECKED_TYPES):
        return cpp
    assert cpp.count(_MATH_HEADER) == 1, "expected one math include"
    if _PRECALCULATE in cpp:
        # Before the types are renamed: the reference's member declarations
        # decide whether the replay is a checked one.
        cpp = _with_guarded_replay(cpp)
    for name, checked in _CHECKED_TYPES.items():
        cpp = re.sub(_type_pattern(name), checked, cpp)
    if TA_LENGTH_HEADER not in cpp:
        # The adapters' header goes before the replay headers and the math one.
        anchor = _REPLAY_HEADERS + _MATH_HEADER if _REPLAY_HEADERS in cpp else _MATH_HEADER
        assert cpp.count(anchor) == 1
        cpp = cpp.replace(anchor, TA_LENGTH_HEADER + anchor, 1)
    return cpp
