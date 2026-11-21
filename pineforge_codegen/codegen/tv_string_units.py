"""C++ that counts and cuts Pine text in UTF-16 code units.

TradingView measures a Pine string in UTF-16 code units: a character outside
the Basic Multilingual Plane (an emoji) is two units, every other character is
one. The generated code keeps text as UTF-8, whose byte counts differ from the
unit counts for every character that is not plain ASCII (an accent is two
bytes, a CJK character three, an emoji four). ``str.length``, ``str.pos`` and
``str.substring`` count and cut through these translation-unit-local helpers;
the engine is not involved.

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
#include <type_traits>

inline int _pf_u16_length(const std::string& text) {
    std::size_t units = 0;
    for (const char c : text) {
        const unsigned char byte = static_cast<unsigned char>(c);
        if ((byte & 0xC0u) != 0x80u) units += byte >= 0xF0u ? 2 : 1;
    }
    return static_cast<int>(units);
}

inline int _pf_u16_pos(const std::string& text, const std::string& needle) {
    const std::size_t at = text.find(needle);
    // Absent: na, as TradingView answers (issue #382).
    if (at == std::string::npos) return na<int>();
    return _pf_u16_length(text.substr(0, at));
}

// str.match over characters, not UTF-8 bytes (issue #382). The engine's
// matcher reads bytes, so a regex atom would consume one byte of a multi-byte
// character. Each distinct non-ASCII code point of the source and the pattern
// is given one byte of 0x80..0xFF, in ascending code-point order (a range
// between two characters keeps the characters between them), the engine
// matches the mapped text, and the answer is mapped back. ASCII text, and text
// with more distinct non-ASCII characters than there are such bytes, is passed
// to the engine as it is.
inline std::vector<uint32_t> _pf_u8_decode_points(const std::string& text) {
    std::vector<uint32_t> points;
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
        points.push_back(code);
    }
    return points;
}

inline std::string _pf_u8_match(const std::string& source, const std::string& pattern) {
    bool ascii = true;
    for (const char c : source) if (static_cast<unsigned char>(c) >= 0x80u) { ascii = false; break; }
    for (const char c : pattern) if (static_cast<unsigned char>(c) >= 0x80u) { ascii = false; break; }
    if (ascii) return pine_str_match(source, pattern);
    const std::vector<uint32_t> source_points = _pf_u8_decode_points(source);
    const std::vector<uint32_t> pattern_points = _pf_u8_decode_points(pattern);
    std::vector<uint32_t> wide;
    for (const uint32_t p : source_points) if (p >= 0x80u) wide.push_back(p);
    for (const uint32_t p : pattern_points) if (p >= 0x80u) wide.push_back(p);
    std::sort(wide.begin(), wide.end());
    wide.erase(std::unique(wide.begin(), wide.end()), wide.end());
    if (wide.size() > 0x80u) return pine_str_match(source, pattern);
    const auto narrow = [&](const std::vector<uint32_t>& points) {
        std::string out;
        out.reserve(points.size());
        for (const uint32_t p : points) {
            if (p < 0x80u) {
                out.push_back(static_cast<char>(p));
            } else {
                const std::size_t slot = static_cast<std::size_t>(
                    std::lower_bound(wide.begin(), wide.end(), p) - wide.begin());
                out.push_back(static_cast<char>(0x80u + slot));
            }
        }
        return out;
    };
    const std::string mapped = pine_str_match(narrow(source_points), narrow(pattern_points));
    std::string text;
    for (const char c : mapped) {
        const unsigned char byte = static_cast<unsigned char>(c);
        uint32_t code = byte < 0x80u ? byte : wide[byte - 0x80u];
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

template <typename T>
inline std::size_t _pf_u16_substr_position(T value, std::size_t length,
                                           bool clamp_to_length) {
    if constexpr (!std::is_same_v<std::decay_t<T>, bool>) {
        if (is_na(value)) return 0;
    }
    if constexpr (std::is_signed_v<T> || std::is_floating_point_v<T>) {
        if (value < 0) {
            if constexpr (std::is_integral_v<T>) {
                throw std::out_of_range(
                    "basic_string::substr: __pos (which is "
                    + std::to_string(static_cast<std::size_t>(value))
                    + ") > this->size() (which is "
                    + std::to_string(length) + ")");
            }
            throw std::out_of_range("substring position is negative");
        }
    }
    if (value > length) {
        if (clamp_to_length) return length;
        if constexpr (std::is_integral_v<T>) {
            throw std::out_of_range(
                "basic_string::substr: __pos (which is "
                + std::to_string(static_cast<std::size_t>(value))
                + ") > this->size() (which is " + std::to_string(length) + ")");
        }
        throw std::out_of_range("substring position is past the source length");
    }
    return static_cast<std::size_t>(value);
}

template <typename BeginT>
inline std::string _pf_u16_substr_checked(const std::string& text, BeginT begin) {
    const std::size_t length = static_cast<std::size_t>(_pf_u16_length(text));
    const std::size_t begin_pos = _pf_u16_substr_position(begin, length, false);
    return _pf_u16_substr(text, begin_pos, length - begin_pos);
}

template <typename BeginT, typename EndT>
inline std::string _pf_u16_substr_checked(const std::string& text, BeginT begin,
                                          EndT end) {
    const std::size_t length = static_cast<std::size_t>(_pf_u16_length(text));
    const std::size_t begin_pos = _pf_u16_substr_position(begin, length, false);
    const std::size_t end_pos = _pf_u16_substr_position(end, length, true);
    if (begin_pos > end_pos) {
        throw std::out_of_range("substring begin position is after end position");
    }
    return _pf_u16_substr(text, begin_pos, end_pos - begin_pos);
}
"""
