"""Apply the #291 lowering to historical expected C++ only."""

from __future__ import annotations

import re


_CHECKED_HEADER = "#include <pineforge/source/pine_ta_length.hpp>\n"
_FAILURE_HEADERS = "#include <pineforge/run_failure.hpp>\n#include <exception>\n"
_MATH_HEADER = "#include <pineforge/math.hpp>\n"
_EMA_TYPE = re.compile(r"\bta::EMA\b")
# The class alone: ``ta::SupertrendResult`` keeps its name.
_SUPERTREND_TYPE = re.compile(r"\bta::Supertrend\b")
_SUPERTREND_GUARD = (
    "#ifndef PINEFORGE_HAS_PINE_FIXED_SUPERTREND\n"
    '#error "ta.supertrend requires the paired pineforge-engine fixed-argument'
    ' adapter (PINEFORGE_HAS_PINE_FIXED_SUPERTREND)"\n'
    "#endif\n"
)

_OLD_KC_FORWARD = (
    " { bool _pf_prior = ta::ema_na_warmup_flag(); ta::ema_na_warmup_flag() = true;"
    " auto _pf_out = impl_.{method}(src, high, low, close);"
    " ta::ema_na_warmup_flag() = _pf_prior; return _pf_out; }\n"
)
_KC_METHODS = ("compute", "recompute")


def _kc_signature(method: str) -> str:
    return f"    ta::KCResult {method}(double src, double high, double low, double close)"


def _old_kc_class(constructor: str) -> str:
    return (
        "class _PFKC {\n"
        "    ta::KC impl_;\n"
        "public:\n"
        f"    _PFKC(int length, double mult, {constructor}\n"
        + "".join(
            _kc_signature(method) + _OLD_KC_FORWARD.replace("{method}", method)
            for method in _KC_METHODS
        )
        + "};\n"
    )


_OLD_KC = (
    "#ifdef PF_KC_HAS_USE_TRUE_RANGE\n"
    + _old_kc_class("bool use_true_range = true) : impl_(length, mult, use_true_range) {}")
    + "#else\n"
    + _old_kc_class("bool = true) : impl_(length, mult) {}")
    + "#endif\n"
)
_CHECKED_KC = (
    "class _PFKC {\n"
    "    pineforge::source::PineKc impl_;\n"
    "public:\n"
    "    _PFKC(int length, double mult, bool use_true_range = true)"
    " : impl_(length, mult, use_true_range) {}\n"
    "    void observe_owned_storage(pineforge::OwnedStorage& out) const noexcept"
    " { impl_.observe_owned_storage(out); }\n"
    + "".join(
        _kc_signature(method) + " {\n"
        "        struct _pf_seed_scope {\n"
        "            bool _pf_prior = ta::ema_na_warmup_flag();\n"
        "            _pf_seed_scope() { ta::ema_na_warmup_flag() = true; }\n"
        "            ~_pf_seed_scope() { ta::ema_na_warmup_flag() = _pf_prior; }\n"
        "        } _pf_seed;\n"
        f"        return impl_.{method}(src, high, low, close);\n"
        "    }\n"
        for method in _KC_METHODS
    )
    + "};\n"
)

_PRECALCULATE = "    void precalculate(const Bar* bars, int n) {\n"
_REPLAY = "        for (int i = 0; i < n; ++i) {\n"
_PUBLISH = "        _use_precalc = true;\n"
_CACHE_RESIZE = re.compile(r"^        (_precalc_\w+)\.resize\(n\);$", re.M)
_INVALID_LENGTH_HANDLER = (
    "            _use_precalc = true;\n"
    "        } catch (const std::exception& _pf_precalc_error) {\n"
    "            const auto* _pf_precalc_failure =\n"
    "                dynamic_cast<const pineforge::RunFailureInfo*>(&_pf_precalc_error);\n"
    "            if (!_pf_precalc_failure ||\n"
    "                _pf_precalc_failure->run_failure_code() !=\n"
    "                    pineforge::RunFailureCode::pine_invalid_argument) throw;\n"
    "            // The real bar path checks the length at the original call.\n"
)


def _with_checked_precalc(cpp: str) -> str:
    # Move publication into the successful replay. Use the historical loop
    # and cache inventory verbatim, including caches for other TA families.
    assert cpp.count(_PRECALCULATE) == 1
    start = cpp.index(_PRECALCULATE)
    end = cpp.index("\n    }\n", start) + 1
    method = cpp[start:end]
    assert method.count(_REPLAY) == 1
    assert method.endswith(_PUBLISH)
    replay_start = method.index(_REPLAY)
    replay_end = method.index("\n        }\n", replay_start) + len("\n        }\n")
    caches = _CACHE_RESIZE.findall(method[:replay_start])
    assert caches
    replay = method[replay_start:replay_end]
    guarded = "        try {\n" + "".join(
        "    " + line if line != "\n" else line for line in replay.splitlines(keepends=True)
    )
    guarded += _INVALID_LENGTH_HANDLER
    guarded += "".join(f"            {cache}.clear();\n" for cache in caches)
    guarded += "        }\n"
    return (
        cpp[:start] + method[:replay_start] + guarded
        + method[replay_end:-len(_PUBLISH)] + cpp[end:]
    )


def with_checked_ta(cpp: str, *, precalc: bool, kc: bool = False,
                    supertrend: bool = False, ema: bool | None = None) -> str:
    """Expect the checked EMA type, or the checked KC shim when kc is set.

    With ``supertrend``, the fixed-argument Supertrend adapter and the guard
    for its feature macro are expected as well. ``ema`` says whether the
    script also has an EMA object; left out, it is expected exactly when
    neither ``kc`` nor ``supertrend`` is set, as before ``supertrend``
    existed.

    Every other byte comes from the pinned reference, including initializer
    arguments, reset getters, replay computations and requested state.
    """
    if ema is None:
        ema = not kc and not supertrend
    assert cpp.count(_MATH_HEADER) == 1
    assert _CHECKED_HEADER not in cpp
    headers = (
        _CHECKED_HEADER
        + (_SUPERTREND_GUARD if supertrend else "")
        + (_FAILURE_HEADERS if precalc else "")
    )
    cpp = cpp.replace(_MATH_HEADER, headers + _MATH_HEADER, 1)
    if kc:
        assert cpp.count(_OLD_KC) == 1
        cpp = cpp.replace(_OLD_KC, _CHECKED_KC, 1)
    if supertrend:
        cpp, changed = _SUPERTREND_TYPE.subn(
            "pineforge::source::PineFixedSupertrend", cpp
        )
        assert changed
    if ema:
        cpp, changed = _EMA_TYPE.subn("pineforge::source::PineEma", cpp)
        assert changed
    return _with_checked_precalc(cpp) if precalc else cpp


_FLAT_RANGE_HEADER = "#include <pineforge/source/pine_ta_flat_range.hpp>\n"
_CCI_HEADER = "#include <pineforge/source/pine_ta_cci.hpp>\n"
# Kernel class: the Pine source-layer type ta.sar, ta.dmi, ta.mfi, ta.rci or
# ta.cci is built from, and the header that declares it.
_PINE_SOURCE_TYPES = {
    "ta::SAR": ("pineforge::source::PineSar", _CHECKED_HEADER),
    "ta::DMI": ("pineforge::source::PineDmiReference", _CHECKED_HEADER),
    "ta::MFI": ("pineforge::source::PineMfi", _CHECKED_HEADER),
    "ta::RCI": ("pineforge::source::PineRci", _CHECKED_HEADER),
    "ta::CCI": ("pineforge::source::PineCci", _CCI_HEADER),
}


def _before_first(cpp: str, text: str, anchors: tuple[str, ...]) -> str:
    """``text`` written before the first of ``anchors`` the C++ has."""
    for anchor in anchors:
        if anchor in cpp:
            assert cpp.count(anchor) == 1
            return cpp.replace(anchor, text + anchor, 1)
    raise AssertionError("the reference C++ has no include to write a header before")


def with_pine_source_ta(cpp: str) -> str:
    """Expect the Pine source-layer type where the reference spells a kernel
    class that ta.sar, ta.dmi, ta.mfi, ta.rci or ta.cci is now built from,
    and that type's header where the emitter writes it.

    A result type (``ta::DMIResult``) keeps its spelling. Every other byte is
    the reference's, and a script with none of the five is returned unchanged.
    Apply it after ``with_checked_ta``, which may already have written the
    length header.
    """
    needed = set()
    for kernel, (selected, header) in _PINE_SOURCE_TYPES.items():
        cpp, changed = re.subn(rf"\b{re.escape(kernel)}\b", selected, cpp)
        if changed:
            needed.add(header)
    # The emitted order: the length header and its guards, the flat-range
    # header and its guard, the cci header, the failure headers of a checked
    # replay, the math header.
    tail = (_FAILURE_HEADERS + _MATH_HEADER, _MATH_HEADER)
    if _CHECKED_HEADER in needed and _CHECKED_HEADER not in cpp:
        cpp = _before_first(cpp, _CHECKED_HEADER, (_FLAT_RANGE_HEADER,) + tail)
    if _CCI_HEADER in needed and _CCI_HEADER not in cpp:
        cpp = _before_first(cpp, _CCI_HEADER, tail)
    return cpp
