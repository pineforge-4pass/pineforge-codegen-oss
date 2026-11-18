"""C++ that counts and cuts Pine text in UTF-16 code units.

TradingView measures a Pine string in UTF-16 code units: a character outside
the Basic Multilingual Plane (an emoji) is two units, every other character is
one. The generated code keeps text as UTF-8, whose byte counts differ from the
unit counts for every character that is not plain ASCII (an accent is two
bytes, a CJK character three, an emoji four). String measurement, search and
substring use these translation-unit-local helpers. String-array joins also
use the same units, so adjacent surrogate halves recompose across parts and
separators. Concatenation uses the same decode/re-encode path, preserving
unpaired halves; the engine is not involved.

A cut between the two units of an emoji leaves a lone surrogate. UTF-8 has no
form for it, so each half is kept as its own three-byte sequence, the form
``str.split`` gives its parts: the length of such a half is one unit.

The emitted C++ uses only ordinary byte types: the units are ``uint16_t`` in a
``std::vector``, because the emitter contract bars the wide character types
(``tests/test_codegen_ascii.py``).

The block is emitted only when the body emits a call of one of the helpers
(a call that is only a plot argument emits nothing). The emitter holds the
block's place with ``TV_STRING_UNITS_MARKER`` until the body is generated.
"""

TV_STRING_UNITS_MARKER = "/* __PINEFORGE_TV_STRING_UNITS__ */"

TV_STRING_UNITS_CPP = r"""
#include <stdexcept>

inline int _pf_u16_length(const std::string& text) {
    std::size_t units = 0;
    for (const char c : text) {
        const unsigned char byte = static_cast<unsigned char>(c);
        if ((byte & 0xC0u) != 0x80u) units += byte >= 0xF0u ? 2 : 1;
    }
    return static_cast<int>(units);
}

inline std::vector<uint16_t> _pf_u16_decode(const std::string& text) {
    std::vector<uint16_t> units;
    units.reserve(text.size());
    std::size_t i = 0;
    while (i < text.size()) {
        const unsigned char lead = static_cast<unsigned char>(text[i]);
        std::size_t width = lead >= 0xF0u ? 4 : lead >= 0xE0u ? 3 : lead >= 0xC0u ? 2 : 1;
        uint32_t code = lead;
        if (width > text.size() - i) {
            width = 1;
        } else if (width > 1) {
            code = lead & (0xFFu >> (width + 1));
            for (std::size_t k = 1; k < width; ++k)
                code = (code << 6) | (static_cast<unsigned char>(text[i + k]) & 0x3Fu);
        }
        i += width;
        if (code >= 0x10000u) {
            code -= 0x10000u;
            units.push_back(static_cast<uint16_t>(0xD800u + (code >> 10)));
            units.push_back(static_cast<uint16_t>(0xDC00u + (code & 0x3FFu)));
        } else {
            units.push_back(static_cast<uint16_t>(code));
        }
    }
    return units;
}

inline std::size_t _pf_u16_find(const std::string& _pf_text,
                                const std::string& _pf_needle) {
    const auto _pf_text_units = _pf_u16_decode(_pf_text);
    const auto _pf_needle_units = _pf_u16_decode(_pf_needle);
    if (_pf_needle_units.empty()) return 0;
    const auto _pf_at = std::search(
        _pf_text_units.begin(), _pf_text_units.end(),
        _pf_needle_units.begin(), _pf_needle_units.end());
    if (_pf_at == _pf_text_units.end()) return std::string::npos;
    return static_cast<std::size_t>(_pf_at - _pf_text_units.begin());
}

inline int _pf_u16_pos(const std::string& _pf_text, const std::string& _pf_needle) {
    const std::size_t _pf_at = _pf_u16_find(_pf_text, _pf_needle);
    return _pf_at == std::string::npos ? na<int>() : static_cast<int>(_pf_at);
}

inline bool _pf_u16_contains(const std::string& _pf_text,
                             const std::string& _pf_needle) {
    return _pf_u16_find(_pf_text, _pf_needle) != std::string::npos;
}

inline bool _pf_u16_startswith(const std::string& _pf_text,
                               const std::string& _pf_needle) {
    const auto _pf_text_units = _pf_u16_decode(_pf_text);
    const auto _pf_needle_units = _pf_u16_decode(_pf_needle);
    return _pf_needle_units.size() <= _pf_text_units.size()
        && std::equal(_pf_needle_units.begin(), _pf_needle_units.end(),
                      _pf_text_units.begin());
}

inline bool _pf_u16_endswith(const std::string& _pf_text,
                             const std::string& _pf_needle) {
    const auto _pf_text_units = _pf_u16_decode(_pf_text);
    const auto _pf_needle_units = _pf_u16_decode(_pf_needle);
    return _pf_needle_units.size() <= _pf_text_units.size()
        && std::equal(_pf_needle_units.rbegin(), _pf_needle_units.rend(),
                      _pf_text_units.rbegin());
}

// The units from begin to end, as UTF-8. A high surrogate that the cut leaves
// without its low one (or the reverse) is a lone half, kept as three bytes.
inline std::string _pf_u16_encode(const std::vector<uint16_t>& units, std::size_t begin,
                                  std::size_t end) {
    std::string text;
    text.reserve((end - begin) * 3);
    for (std::size_t i = begin; i < end; ++i) {
        uint32_t code = units[i];
        if (code >= 0xD800u && code < 0xDC00u && i + 1 < end) {
            const uint32_t next = units[i + 1];
            if (next >= 0xDC00u && next < 0xE000u) {
                code = 0x10000u + ((code - 0xD800u) << 10) + (next - 0xDC00u);
                ++i;
            }
        }
        if (code < 0x80u) {
            text.push_back(static_cast<char>(code));
        } else if (code < 0x800u) {
            text.push_back(static_cast<char>(0xC0u | (code >> 6)));
            text.push_back(static_cast<char>(0x80u | (code & 0x3Fu)));
        } else if (code < 0x10000u) {
            text.push_back(static_cast<char>(0xE0u | (code >> 12)));
            text.push_back(static_cast<char>(0x80u | ((code >> 6) & 0x3Fu)));
            text.push_back(static_cast<char>(0x80u | (code & 0x3Fu)));
        } else {
            text.push_back(static_cast<char>(0xF0u | (code >> 18)));
            text.push_back(static_cast<char>(0x80u | ((code >> 12) & 0x3Fu)));
            text.push_back(static_cast<char>(0x80u | ((code >> 6) & 0x3Fu)));
            text.push_back(static_cast<char>(0x80u | (code & 0x3Fu)));
        }
    }
    return text;
}

inline std::string _pf_u16_normalize(const std::string& text) {
    const std::vector<uint16_t> units = _pf_u16_decode(text);
    return _pf_u16_encode(units, 0, units.size());
}

inline std::string _pf_u16_concat(const std::string& left, const std::string& right) {
    return _pf_u16_normalize(left + right);
}

template<class _pf_StringArray>
inline std::string _pf_u16_join(const _pf_StringArray& _pf_parts,
                                const std::string& _pf_separator) {
    const auto _pf_separator_units = _pf_u16_decode(_pf_separator);
    std::vector<uint16_t> _pf_join_units;
    for (std::size_t _pf_i = 0; _pf_i < _pf_parts.size(); ++_pf_i) {
        if (_pf_i != 0)
            _pf_join_units.insert(_pf_join_units.end(),
                                  _pf_separator_units.begin(), _pf_separator_units.end());
        const auto _pf_part_units = _pf_u16_decode(_pf_parts[_pf_i]);
        _pf_join_units.insert(_pf_join_units.end(),
                              _pf_part_units.begin(), _pf_part_units.end());
    }
    return _pf_u16_encode(_pf_join_units, 0, _pf_join_units.size());
}

// std::string::substr in units: past the end it throws std::out_of_range with
// the text substr gives (the units are counted), and a count beyond the end is
// cut to the end.
inline std::string _pf_u16_substr(const std::string& text, std::size_t begin,
                                  std::size_t count = std::string::npos) {
    bool ascii = true;
    for (const char c : text) {
        if (static_cast<unsigned char>(c) >= 0x80u) { ascii = false; break; }
    }
    if (ascii) return text.substr(begin, count);
    const std::vector<uint16_t> units = _pf_u16_decode(text);
    if (begin > units.size())
        throw std::out_of_range("basic_string::substr: __pos (which is " + std::to_string(begin)
                                + ") > this->size() (which is " + std::to_string(units.size()) + ")");
    const std::size_t end = count > units.size() - begin ? units.size() : begin + count;
    return _pf_u16_encode(units, begin, end);
}
"""
