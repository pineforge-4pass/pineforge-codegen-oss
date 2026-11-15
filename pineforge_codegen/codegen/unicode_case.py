"""Emit Unicode casing over runtime UTF-8 strings.

Mappings and properties come only from the generated, checked-in Unicode
14.0.0 data module. scripts/gen_unicode_case_data.py binds the three exact
official UCD inputs; no host Unicode database or locale supplies data.
The data pin identifies this implementation, not TradingView's whole dialect.
Locale-specific casing and normalization are not applied.

DerivedCoreProperties supplies Cased and Case_Ignorable. UnicodeData supplies
Nd. SpecialCasing supplies full expansions and the Final_Sigma override.
Ignoring takes precedence for overlapping properties. The cased context also
crosses decimal-number runs without making digits cased or case-ignorable.

The captureless lambda receives its argument outside its local scope, once.
Adjacent WTF-8 high/low surrogates form one code point before casing. NUL,
isolated surrogate pieces and malformed bytes survive.
"""

from __future__ import annotations

from functools import lru_cache


_DATA_VERSION = "14.0.0"
_SOURCE_SHA256 = (
    ("UnicodeData.txt", "36018e68657fdcb3485f636630ffe8c8532e01c977703d2803f5b89d6c5feafb"),
    ("SpecialCasing.txt", "c667b45908fd269af25fd55d2fc5bbc157fb1b77675936e25c513ce32e080334"),
    ("DerivedCoreProperties.txt", "e3eddd7d469cd1b0feed7528defad1a1cc7c6a9ceb0ae4446a6d10921ed2e7bc"),
)
_SOURCE_BYTE_COUNTS = (
    ("UnicodeData.txt", 1897793),
    ("SpecialCasing.txt", 16830),
    ("DerivedCoreProperties.txt", 1040794),
)


@lru_cache(maxsize=1)
def _case_data():
    # Keep unrelated translation paths importable while regenerating data.
    # A missing/mismatched module is a build defect, never a host fallback.
    from . import _unicode_case_data as data

    if (data.DATA_FORMAT != 1 or data.DATA_VERSION != _DATA_VERSION
            or data.SOURCE_SHA256 != _SOURCE_SHA256
            or data.SOURCE_BYTE_COUNTS != _SOURCE_BYTE_COUNTS):
        raise RuntimeError("regenerate Unicode casing data from the pinned UCD inputs")
    return data


def _context_flags(char: str) -> int:
    point = ord(char)
    ranges = _context_ranges()
    low, high = 0, len(ranges)
    while low < high:
        mid = low + (high - low) // 2
        if ranges[mid][1] < point:
            low = mid + 1
        else:
            high = mid
    if low < len(ranges) and ranges[low][0] <= point:
        return ranges[low][2]
    return 0


@lru_cache(maxsize=1)
def _context_ranges() -> tuple[tuple[int, int, int], ...]:
    return _case_data().CONTEXT_RANGES


_CONTEXT_CPP = r"""
    static constexpr uint32_t _pf_tolower_final_map[][@FINAL_WIDTH@] = {
@FINAL_MAPPINGS@
    };
    static constexpr uint32_t _pf_case_properties[][3] = {
@PROPERTIES@
    };
    const auto _pf_case_flags = [](uint32_t _pf_case_code) -> unsigned {
        std::size_t _pf_case_lo = 0;
        std::size_t _pf_case_hi = sizeof(_pf_case_properties) / sizeof(_pf_case_properties[0]);
        while (_pf_case_lo < _pf_case_hi) {
            const std::size_t _pf_case_mid = _pf_case_lo + (_pf_case_hi - _pf_case_lo) / 2;
            if (_pf_case_properties[_pf_case_mid][1] < _pf_case_code)
                _pf_case_lo = _pf_case_mid + 1;
            else
                _pf_case_hi = _pf_case_mid;
        }
        if (_pf_case_lo < sizeof(_pf_case_properties) / sizeof(_pf_case_properties[0])
                && _pf_case_properties[_pf_case_lo][0] <= _pf_case_code)
            return _pf_case_properties[_pf_case_lo][2];
        return 0;
    };
    std::vector<unsigned char> _pf_case_after(_pf_case_codes.size());
    bool _pf_case_next_cased = false;
    for (std::size_t _pf_case_i = _pf_case_codes.size(); _pf_case_i > 0;) {
        --_pf_case_i;
        _pf_case_after[_pf_case_i] = static_cast<unsigned char>(_pf_case_next_cased);
        const unsigned _pf_case_props = _pf_case_flags(_pf_case_codes[_pf_case_i]);
        if (!(_pf_case_props & 6u)) _pf_case_next_cased = (_pf_case_props & 1u) != 0;
    }
    bool _pf_case_before = false;
"""

_CONTEXT_SELECT_CPP = r"""
        bool _pf_case_contextual = false;
        if (_pf_case_before && !_pf_case_after[_pf_case_i]) {
            for (const auto& _pf_case_row : _pf_tolower_final_map) {
                if (_pf_case_row[0] != _pf_case_code) continue;
                for (uint32_t _pf_case_k = 0; _pf_case_k < _pf_case_row[1]; ++_pf_case_k)
                    _pf_case_append(_pf_case_row[_pf_case_k + 2]);
                _pf_case_contextual = true;
                break;
            }
        }
        if (!_pf_case_contextual)
"""

_CONTEXT_ADVANCE_CPP = r"""
        const unsigned _pf_case_props = _pf_case_flags(_pf_case_code);
        if (!(_pf_case_props & 6u)) _pf_case_before = (_pf_case_props & 1u) != 0;
"""

_BODY_CPP = r"""[](const std::string& _pf_case_text) {
    // Unicode @VERSION@ casing, independent of the C locale.
@PROVENANCE@
    static constexpr uint32_t @TABLE@[][@WIDTH@] = {
@MAPPINGS@
    };
    std::vector<uint32_t> _pf_case_codes;
    _pf_case_codes.reserve(_pf_case_text.size());
    for (std::size_t _pf_case_i = 0; _pf_case_i < _pf_case_text.size();) {
        const unsigned char _pf_case_lead = static_cast<unsigned char>(_pf_case_text[_pf_case_i]);
        std::size_t _pf_case_width = _pf_case_lead < 0x80u ? 1
            : _pf_case_lead >= 0xC2u && _pf_case_lead <= 0xDFu ? 2
            : _pf_case_lead >= 0xE0u && _pf_case_lead <= 0xEFu ? 3
            : _pf_case_lead >= 0xF0u && _pf_case_lead <= 0xF4u ? 4 : 0;
        uint32_t _pf_case_code = _pf_case_width == 1 ? _pf_case_lead
            : _pf_case_lead & (0x7Fu >> _pf_case_width);
        bool _pf_case_valid = _pf_case_width && _pf_case_width <= _pf_case_text.size() - _pf_case_i;
        if (_pf_case_valid) {
            for (std::size_t _pf_case_k = 1; _pf_case_k < _pf_case_width; ++_pf_case_k) {
                const unsigned char _pf_case_byte =
                    static_cast<unsigned char>(_pf_case_text[_pf_case_i + _pf_case_k]);
                if ((_pf_case_byte & 0xC0u) != 0x80u) { _pf_case_valid = false; break; }
                _pf_case_code = (_pf_case_code << 6) | (_pf_case_byte & 0x3Fu);
            }
            const uint32_t _pf_case_min = _pf_case_width == 1 ? 0
                : _pf_case_width == 2 ? 0x80u : _pf_case_width == 3 ? 0x800u : 0x10000u;
            _pf_case_valid = _pf_case_valid && _pf_case_code >= _pf_case_min && _pf_case_code <= 0x10FFFFu;
        }
        if (!_pf_case_valid) {
            _pf_case_code = 0x110000u + _pf_case_lead;
            _pf_case_width = 1;
        }
        if (_pf_case_code >= 0xDC00u && _pf_case_code <= 0xDFFFu
                && !_pf_case_codes.empty()
                && _pf_case_codes.back() >= 0xD800u && _pf_case_codes.back() <= 0xDBFFu) {
            _pf_case_codes.back() = 0x10000u
                + ((_pf_case_codes.back() - 0xD800u) << 10) + (_pf_case_code - 0xDC00u);
        } else {
            _pf_case_codes.push_back(_pf_case_code);
        }
        _pf_case_i += _pf_case_width;
    }
@CONTEXT@
    std::string _pf_case_out;
    _pf_case_out.reserve(_pf_case_text.size());
    const auto _pf_case_append = [&_pf_case_out](uint32_t _pf_case_code) {
        if (_pf_case_code >= 0x110000u) {
            _pf_case_out.push_back(static_cast<char>(_pf_case_code - 0x110000u));
        } else if (_pf_case_code < 0x80u) {
            _pf_case_out.push_back(static_cast<char>(_pf_case_code));
        } else if (_pf_case_code < 0x800u) {
            _pf_case_out.push_back(static_cast<char>(0xC0u | (_pf_case_code >> 6)));
            _pf_case_out.push_back(static_cast<char>(0x80u | (_pf_case_code & 0x3Fu)));
        } else if (_pf_case_code < 0x10000u) {
            _pf_case_out.push_back(static_cast<char>(0xE0u | (_pf_case_code >> 12)));
            _pf_case_out.push_back(static_cast<char>(0x80u | ((_pf_case_code >> 6) & 0x3Fu)));
            _pf_case_out.push_back(static_cast<char>(0x80u | (_pf_case_code & 0x3Fu)));
        } else {
            _pf_case_out.push_back(static_cast<char>(0xF0u | (_pf_case_code >> 18)));
            _pf_case_out.push_back(static_cast<char>(0x80u | ((_pf_case_code >> 12) & 0x3Fu)));
            _pf_case_out.push_back(static_cast<char>(0x80u | ((_pf_case_code >> 6) & 0x3Fu)));
            _pf_case_out.push_back(static_cast<char>(0x80u | (_pf_case_code & 0x3Fu)));
        }
    };
    for (std::size_t _pf_case_i = 0; _pf_case_i < _pf_case_codes.size(); ++_pf_case_i) {
        const uint32_t _pf_case_code = _pf_case_codes[_pf_case_i];
@CONTEXT_SELECT@
        {
            std::size_t _pf_case_lo = 0;
            std::size_t _pf_case_hi = sizeof(@TABLE@) / sizeof(@TABLE@[0]);
            while (_pf_case_lo < _pf_case_hi) {
                const std::size_t _pf_case_mid = _pf_case_lo + (_pf_case_hi - _pf_case_lo) / 2;
                if (@TABLE@[_pf_case_mid][0] < _pf_case_code) _pf_case_lo = _pf_case_mid + 1;
                else _pf_case_hi = _pf_case_mid;
            }
            if (_pf_case_lo < sizeof(@TABLE@) / sizeof(@TABLE@[0])
                    && @TABLE@[_pf_case_lo][0] == _pf_case_code) {
                for (uint32_t _pf_case_k = 0; _pf_case_k < @TABLE@[_pf_case_lo][1]; ++_pf_case_k)
                    _pf_case_append(@TABLE@[_pf_case_lo][_pf_case_k + 2]);
            } else {
                _pf_case_append(_pf_case_code);
            }
        }
@CONTEXT_ADVANCE@
    }
    return _pf_case_out;
}"""


@lru_cache(maxsize=2)
def _case_lambda(upper: bool) -> str:
    data = _case_data()
    rows = data.UPPER_ROWS if upper else data.LOWER_ROWS
    width = max(len(row) for row in rows)
    mappings = "\n".join(
        "        {" + ", ".join(f"0x{value:X}u" for value in row) + "},"
        for row in rows
    )
    context = ""
    if not upper:
        properties = "\n".join(
            f"        {{0x{start:X}u, 0x{end:X}u, {flags}u}},"
            for start, end, flags in _context_ranges()
        )
        final_rows = data.LOWER_FINAL_SIGMA_ROWS
        final_mappings = "\n".join(
            "        {" + ", ".join(f"0x{value:X}u" for value in row) + "},"
            for row in final_rows
        )
        context = (_CONTEXT_CPP.replace("@PROPERTIES@", properties)
                   .replace("@FINAL_WIDTH@", str(max(len(row) for row in final_rows)))
                   .replace("@FINAL_MAPPINGS@", final_mappings))
    result = _BODY_CPP
    for key, value in (
        ("@VERSION@", data.DATA_VERSION),
        ("@PROVENANCE@", "\n".join(
            f"    // {name} SHA-256 {digest}" for name, digest in data.SOURCE_SHA256
        )),
        ("@TABLE@", "_pf_toupper_map" if upper else "_pf_tolower_map"),
        ("@WIDTH@", str(width)),
        ("@MAPPINGS@", mappings),
        ("@CONTEXT@", context),
        ("@CONTEXT_SELECT@", "" if upper else _CONTEXT_SELECT_CPP),
        ("@CONTEXT_ADVANCE@", "" if upper else _CONTEXT_ADVANCE_CPP),
    ):
        result = result.replace(key, value)
    return result


def _unicode_case_expr(text: str, *, upper: bool) -> str:
    return f"({_case_lambda(upper)})(({text}))"
