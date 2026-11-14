"""Emit Unicode default casing over runtime UTF-8 strings.

Generate complete, sorted case tables from Python's standard Unicode database,
including expansions and supplementary letters. The emitted comment records
that database's version; the compiled strategy needs no Unicode library.
Locale-specific tailoring and normalization are not default casing.

Default lowercase has one contextual rule, Final_Sigma. Cased is the union of
Lowercase, Uppercase and General_Category=Lt. Obtain Case_Ignorable from the
same database through that rule: after A+SIGMA, a cased character is ignorable
iff SIGMA stays final; an uncased character is ignorable iff following it with
A makes SIGMA nonfinal. Ignoring takes precedence for overlapping properties.

The captureless lambda receives its argument outside its local scope, once.
Adjacent WTF-8 high/low surrogates form one code point before casing, just as
they do in UTF-16. NUL, isolated surrogate pieces and malformed bytes survive.
"""

from __future__ import annotations

from functools import lru_cache
import unicodedata


@lru_cache(maxsize=1)
def _context_ranges() -> tuple[tuple[int, int, int], ...]:
    ranges: list[tuple[int, int, int]] = []
    for code in range(0x110000):
        char = chr(code)
        cased = char.islower() or char.isupper() or char.istitle()
        if cased:
            ignorable = ("A\u03a3" + char).lower()[1] == "\u03c2"
        else:
            ignorable = ("A\u03a3" + char + "A").lower()[1] == "\u03c3"
        flags = int(cased) | (int(ignorable) << 1)
        if flags:
            if ranges and ranges[-1][1] + 1 == code and ranges[-1][2] == flags:
                start, _, _ = ranges[-1]
                ranges[-1] = (start, code, flags)
            else:
                ranges.append((code, code, flags))
    return tuple(ranges)


_CONTEXT_CPP = r"""
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
        if (!(_pf_case_props & 2u)) _pf_case_next_cased = (_pf_case_props & 1u) != 0;
    }
    bool _pf_case_before = false;
"""

_CONTEXT_SELECT_CPP = r"""
        if (_pf_case_code == 0x3A3u && _pf_case_before && !_pf_case_after[_pf_case_i]) {
            _pf_case_append(0x3C2u);
        } else
"""

_CONTEXT_ADVANCE_CPP = r"""
        const unsigned _pf_case_props = _pf_case_flags(_pf_case_code);
        if (!(_pf_case_props & 2u)) _pf_case_before = (_pf_case_props & 1u) != 0;
"""

_BODY_CPP = r"""[](const std::string& _pf_case_text) {
    // Unicode @VERSION@ default @DIRECTION@, independent of the C locale.
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
        // WTF-8 encodes isolated surrogate units as three bytes; keep them.
        // Preserve each malformed byte with a value outside the Unicode range.
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
    mapping = str.upper if upper else str.lower
    rows = []
    for code in range(0x110000):
        char = chr(code)
        converted = mapping(char)
        if converted != char:
            rows.append((code, len(converted), *(ord(part) for part in converted)))
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
        context = _CONTEXT_CPP.replace("@PROPERTIES@", properties)
    result = _BODY_CPP
    for key, value in (
        ("@VERSION@", unicodedata.unidata_version),
        ("@DIRECTION@", "uppercase" if upper else "lowercase"),
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
