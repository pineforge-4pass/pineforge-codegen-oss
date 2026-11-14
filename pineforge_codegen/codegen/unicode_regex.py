"""Lower Unicode-character regex atoms to ordinary byte-regex expressions.

The runtime regex accepts bytes. A Unicode character is represented by its one
to four byte encoding, retaining an isolated surrogate as three bytes. Each
regex atom is a noncapturing group over those encodings, so a quantifier
repeats characters and new groups do not renumber captures. Class unions, ranges and
complements use the entire character domain, not an inventory of Unicode letters.

Matching still goes through the runtime's string helper, retaining its capture
selection and coded regex errors. Only the matched units are joined back into
ordinary UTF-8. Valid surrogate pairs form one regex character; string lengths
and substring offsets continue to use the unchanged UTF-16 codec.
"""


def _str_match_expr(args: list[str]) -> str:
    """Bind both argument expressions once in declared parameter order."""
    return (
        "[&]() { "
        f"const std::string _pf_match_source = ({args[0]}); "
        f"const std::string _pf_match_pattern = ({args[1]}); "
        "return ::_pf_regex::_pf_match("
        "_pf_match_source, _pf_match_pattern, bar_index_); }()"
    )


UNICODE_REGEX_CPP = r"""
namespace _pf_regex {
using _pf_ranges = std::vector<std::pair<uint32_t, uint32_t>>;
struct _pf_invalid {};

inline std::vector<uint32_t> _pf_characters(const std::string& _pf_text) {
    const auto _pf_decoded = _pf_u16_decode(_pf_text);
    std::vector<uint32_t> _pf_result;
    for (std::size_t _pf_i = 0; _pf_i < _pf_decoded.size(); ++_pf_i) {
        uint32_t _pf_code = _pf_decoded[_pf_i];
        if (_pf_code >= 0xD800u && _pf_code <= 0xDBFFu
                && _pf_i + 1 < _pf_decoded.size()
                && _pf_decoded[_pf_i + 1] >= 0xDC00u
                && _pf_decoded[_pf_i + 1] <= 0xDFFFu) {
            _pf_code = 0x10000u + ((_pf_code - 0xD800u) << 10)
                + (_pf_decoded[++_pf_i] - 0xDC00u);
        }
        _pf_result.push_back(_pf_code);
    }
    return _pf_result;
}

inline _pf_ranges _pf_normalize(_pf_ranges _pf_values) {
    std::sort(_pf_values.begin(), _pf_values.end());
    _pf_ranges _pf_result;
    for (const auto& _pf_value : _pf_values) {
        if (!_pf_result.empty() && _pf_value.first <= _pf_result.back().second + 1)
            _pf_result.back().second = std::max(_pf_result.back().second, _pf_value.second);
        else
            _pf_result.push_back(_pf_value);
    }
    return _pf_result;
}

inline _pf_ranges _pf_complement(_pf_ranges _pf_values) {
    _pf_ranges _pf_result;
    uint32_t _pf_next = 0;
    for (const auto& _pf_value : _pf_normalize(std::move(_pf_values))) {
        if (_pf_next < _pf_value.first)
            _pf_result.emplace_back(_pf_next, _pf_value.first - 1);
        _pf_next = _pf_value.second + 1;
    }
    if (_pf_next <= 0x10FFFFu) _pf_result.emplace_back(_pf_next, 0x10FFFFu);
    return _pf_result;
}

inline std::string _pf_hex(uint32_t _pf_value) {
    const char* _pf_digits = "0123456789abcdef";
    std::string _pf_result = "\\x";
    _pf_result += _pf_digits[(_pf_value >> 4) & 15];
    _pf_result += _pf_digits[_pf_value & 15];
    return _pf_result;
}

inline std::string _pf_byte_range(uint32_t _pf_low, uint32_t _pf_high) {
    if (_pf_low == _pf_high) return _pf_hex(_pf_low);
    return "[" + _pf_hex(_pf_low) + "-" + _pf_hex(_pf_high) + "]";
}

inline std::vector<uint32_t> _pf_bytes(uint32_t _pf_unit) {
    if (_pf_unit < 0x80u) return {_pf_unit};
    if (_pf_unit < 0x800u)
        return {0xC0u | (_pf_unit >> 6), 0x80u | (_pf_unit & 63)};
    if (_pf_unit < 0x10000u)
        return {0xE0u | (_pf_unit >> 12), 0x80u | ((_pf_unit >> 6) & 63),
                0x80u | (_pf_unit & 63)};
    return {0xF0u | (_pf_unit >> 18), 0x80u | ((_pf_unit >> 12) & 63),
            0x80u | ((_pf_unit >> 6) & 63), 0x80u | (_pf_unit & 63)};
}

// The endpoints have the same encoding width. Split at differing prefixes;
// complete intervening prefixes share all their continuation-byte ranges.
// This builds a bounded trie for a range instead of enumerating its members.
inline void _pf_byte_trie(const std::vector<uint32_t>& _pf_low,
                         const std::vector<uint32_t>& _pf_high,
                         std::size_t _pf_at, const std::string& _pf_prefix,
                         std::vector<std::string>& _pf_alternatives) {
    if (_pf_at + 1 == _pf_low.size()) {
        _pf_alternatives.push_back(
            _pf_prefix + _pf_byte_range(_pf_low[_pf_at], _pf_high[_pf_at]));
        return;
    }
    if (_pf_low[_pf_at] == _pf_high[_pf_at]) {
        _pf_byte_trie(_pf_low, _pf_high, _pf_at + 1,
                      _pf_prefix + _pf_hex(_pf_low[_pf_at]), _pf_alternatives);
        return;
    }
    auto _pf_first_high = _pf_low;
    auto _pf_last_low = _pf_high;
    for (std::size_t _pf_i = _pf_at + 1; _pf_i < _pf_low.size(); ++_pf_i) {
        _pf_first_high[_pf_i] = 0xBFu;
        _pf_last_low[_pf_i] = 0x80u;
    }
    _pf_byte_trie(_pf_low, _pf_first_high, _pf_at + 1,
                  _pf_prefix + _pf_hex(_pf_low[_pf_at]), _pf_alternatives);
    if (_pf_low[_pf_at] + 1 < _pf_high[_pf_at]) {
        std::string _pf_middle = _pf_prefix
            + _pf_byte_range(_pf_low[_pf_at] + 1, _pf_high[_pf_at] - 1);
        for (std::size_t _pf_i = _pf_at + 1; _pf_i < _pf_low.size(); ++_pf_i)
            _pf_middle += "[\\x80-\\xbf]";
        _pf_alternatives.push_back(std::move(_pf_middle));
    }
    _pf_byte_trie(_pf_last_low, _pf_high, _pf_at + 1,
                  _pf_prefix + _pf_hex(_pf_high[_pf_at]), _pf_alternatives);
}

inline std::string _pf_atom(_pf_ranges _pf_values) {
    std::vector<std::string> _pf_alternatives;
    for (const auto& _pf_value : _pf_normalize(std::move(_pf_values))) {
        uint32_t _pf_low = _pf_value.first;
        for (uint32_t _pf_limit : {0x7Fu, 0x7FFu, 0xFFFFu, 0x10FFFFu}) {
            if (_pf_low > _pf_value.second || _pf_low > _pf_limit) continue;
            const auto _pf_high = std::min(_pf_value.second, _pf_limit);
            _pf_byte_trie(_pf_bytes(_pf_low), _pf_bytes(_pf_high),
                          0, "", _pf_alternatives);
            _pf_low = _pf_high + 1;
        }
    }
    if (_pf_alternatives.empty()) return "(?!)";
    std::string _pf_result = "(?:";
    for (const auto& _pf_alternative : _pf_alternatives) {
        if (_pf_result.size() > 3) _pf_result += "|";
        _pf_result += _pf_alternative;
    }
    return _pf_result + ")";
}

inline _pf_ranges _pf_class_escape(uint32_t _pf_code) {
    _pf_ranges _pf_result;
    switch (_pf_code) {
        case 'd': case 'D':
            _pf_result = {{'0', '9'}};
            break;
        case 'w': case 'W':
            _pf_result = {{'0', '9'}, {'A', 'Z'}, {'_', '_'}, {'a', 'z'}};
            break;
        case 's': case 'S':
            _pf_result = {{9, 13}, {32, 32}};
            break;
        default: throw _pf_invalid{};
    }
    if (_pf_code == 'D' || _pf_code == 'W' || _pf_code == 'S')
        return _pf_complement(std::move(_pf_result));
    return _pf_result;
}

inline bool _pf_is_class_escape(uint32_t _pf_code) {
    return _pf_code == 'd' || _pf_code == 'D' || _pf_code == 'w'
        || _pf_code == 'W' || _pf_code == 's' || _pf_code == 'S';
}

struct _pf_member {
    _pf_ranges _pf_values;
    bool _pf_single;
    uint32_t _pf_unit;
};

class _pf_pattern {
    const std::vector<uint32_t>& _pf_units;
    std::size_t _pf_at = 0;

    uint32_t _pf_take() {
        if (_pf_at == _pf_units.size()) throw _pf_invalid{};
        return _pf_units[_pf_at++];
    }

    uint32_t _pf_hex_unit(unsigned _pf_count) {
        uint32_t _pf_value = 0;
        while (_pf_count--) {
            const auto _pf_code = _pf_take();
            const int _pf_digit = _pf_code >= '0' && _pf_code <= '9'
                ? static_cast<int>(_pf_code - '0')
                : _pf_code >= 'a' && _pf_code <= 'f' ? static_cast<int>(_pf_code - 'a') + 10
                : _pf_code >= 'A' && _pf_code <= 'F' ? static_cast<int>(_pf_code - 'A') + 10 : -1;
            if (_pf_digit < 0) throw _pf_invalid{};
            _pf_value = (_pf_value << 4) | static_cast<unsigned>(_pf_digit);
        }
        return _pf_value;
    }

    uint32_t _pf_character_escape(uint32_t _pf_code) {
        switch (_pf_code) {
            case 'u': return _pf_hex_unit(4);
            case 'x': return _pf_hex_unit(2);
            case 'b': return 8;
            case 'f': return 12;
            case 'n': return 10;
            case 'r': return 13;
            case 't': return 9;
            case 'v': return 11;
            case '0': return 0;
            case 'c': {
                const auto _pf_letter = _pf_take();
                if (!((_pf_letter >= 'A' && _pf_letter <= 'Z')
                      || (_pf_letter >= 'a' && _pf_letter <= 'z')))
                    throw _pf_invalid{};
                return _pf_letter & 31;
            }
            default: return _pf_code;
        }
    }

    uint32_t _pf_pair(uint32_t _pf_high) {
        if (_pf_high < 0xD800u || _pf_high > 0xDBFFu) return _pf_high;
        const auto _pf_saved = _pf_at;
        uint32_t _pf_low;
        if (_pf_at < _pf_units.size() && _pf_units[_pf_at] >= 0xDC00u
                && _pf_units[_pf_at] <= 0xDFFFu) {
            _pf_low = _pf_take();
        } else if (_pf_at + 1 < _pf_units.size() && _pf_units[_pf_at] == '\\'
                   && _pf_units[_pf_at + 1] == 'u') {
            _pf_at += 2;
            _pf_low = _pf_hex_unit(4);
        } else {
            return _pf_high;
        }
        if (_pf_low < 0xDC00u || _pf_low > 0xDFFFu) {
            _pf_at = _pf_saved;
            return _pf_high;
        }
        return 0x10000u + ((_pf_high - 0xD800u) << 10) + (_pf_low - 0xDC00u);
    }

    // Retain the existing byte engine's named/collating class syntax too.
    // Its classic-locale classes are sets of ASCII units.
    _pf_member _pf_named_member(uint32_t _pf_delimiter) {
        std::string _pf_name;
        for (;;) {
            const auto _pf_code = _pf_take();
            if (_pf_code == _pf_delimiter && _pf_at < _pf_units.size()
                    && _pf_units[_pf_at] == ']') {
                ++_pf_at;
                break;
            }
            if (_pf_code > 127) throw _pf_invalid{};
            _pf_name += static_cast<char>(_pf_code);
        }
        std::regex_traits<char> _pf_traits;
        _pf_traits.imbue(std::locale::classic());
        if (_pf_delimiter != ':') {
            const auto _pf_value = _pf_traits.lookup_collatename(
                _pf_name.begin(), _pf_name.end());
            if (_pf_value.size() != 1) throw _pf_invalid{};
            const auto _pf_unit = static_cast<unsigned char>(_pf_value[0]);
            if (_pf_delimiter == '=') {
                const auto _pf_primary = _pf_traits.transform_primary(
                    _pf_value.begin(), _pf_value.end());
                _pf_ranges _pf_values;
                for (uint32_t _pf_candidate = 0; _pf_candidate < 128; ++_pf_candidate) {
                    const std::string _pf_byte(1, static_cast<char>(_pf_candidate));
                    if (_pf_traits.transform_primary(_pf_byte.begin(), _pf_byte.end())
                            == _pf_primary)
                        _pf_values.emplace_back(_pf_candidate, _pf_candidate);
                }
                return {std::move(_pf_values), false, 0};
            }
            return {{{_pf_unit, _pf_unit}}, true, _pf_unit};
        }
        const auto _pf_mask = _pf_traits.lookup_classname(_pf_name.begin(), _pf_name.end());
        if (_pf_mask == decltype(_pf_mask)()) throw _pf_invalid{};
        _pf_ranges _pf_values;
        for (uint32_t _pf_unit = 0; _pf_unit < 128; ++_pf_unit)
            if (_pf_traits.isctype(static_cast<char>(_pf_unit), _pf_mask))
                _pf_values.emplace_back(_pf_unit, _pf_unit);
        return {std::move(_pf_values), false, 0};
    }

    _pf_member _pf_class_member() {
        auto _pf_code = _pf_take();
        if (_pf_code == '[' && _pf_at < _pf_units.size()
                && (_pf_units[_pf_at] == ':' || _pf_units[_pf_at] == '.'
                    || _pf_units[_pf_at] == '=')) {
            return _pf_named_member(_pf_take());
        }
        uint32_t _pf_unit = _pf_code;
        if (_pf_code == '\\') {
            _pf_code = _pf_take();
            if (_pf_is_class_escape(_pf_code))
                return {_pf_class_escape(_pf_code), false, 0};
            _pf_unit = _pf_character_escape(_pf_code);
        }
        _pf_unit = _pf_pair(_pf_unit);
        return {{{_pf_unit, _pf_unit}}, true, _pf_unit};
    }

    std::string _pf_bracket() {
        const bool _pf_negate = _pf_at < _pf_units.size() && _pf_units[_pf_at] == '^';
        if (_pf_negate) ++_pf_at;
        _pf_ranges _pf_values;
        while (_pf_at < _pf_units.size() && _pf_units[_pf_at] != ']') {
            auto _pf_left = _pf_class_member();
            if (_pf_at + 1 < _pf_units.size() && _pf_units[_pf_at] == '-'
                    && _pf_units[_pf_at + 1] != ']') {
                ++_pf_at;
                const auto _pf_right = _pf_class_member();
                if (!_pf_left._pf_single || !_pf_right._pf_single
                        || _pf_left._pf_unit > _pf_right._pf_unit)
                    throw _pf_invalid{};
                _pf_values.emplace_back(_pf_left._pf_unit, _pf_right._pf_unit);
            } else {
                _pf_values.insert(_pf_values.end(), _pf_left._pf_values.begin(),
                                  _pf_left._pf_values.end());
            }
        }
        if (_pf_take() != ']') throw _pf_invalid{};
        return _pf_atom(_pf_negate ? _pf_complement(std::move(_pf_values))
                                   : std::move(_pf_values));
    }

public:
    explicit _pf_pattern(const std::vector<uint32_t>& _pf_value) : _pf_units(_pf_value) {}

    std::string _pf_compile() {
        std::string _pf_result;
        while (_pf_at < _pf_units.size()) {
            auto _pf_code = _pf_take();
            if (_pf_code == '[') {
                _pf_result += _pf_bracket();
            } else if (_pf_code == '.') {
                _pf_result += _pf_atom(_pf_complement({{10, 10}, {13, 13},
                                                       {0x2028, 0x2029}}));
            } else if (_pf_code == '\\') {
                _pf_code = _pf_take();
                if (_pf_is_class_escape(_pf_code)) {
                    _pf_result += _pf_atom(_pf_class_escape(_pf_code));
                } else if (_pf_code == 'u' || _pf_code == 'x' || _pf_code > 127) {
                    const auto _pf_unit = _pf_pair(_pf_character_escape(_pf_code));
                    _pf_result += _pf_atom({{_pf_unit, _pf_unit}});
                } else {
                    // Assertions, backreferences and ASCII character escapes
                    // retain the byte engine's grammar and capture numbering.
                    _pf_result += '\\';
                    _pf_result += static_cast<char>(_pf_code);
                    if (_pf_code == 'c') _pf_result += static_cast<char>(_pf_take());
                    if (_pf_code >= '1' && _pf_code <= '9')
                        while (_pf_at < _pf_units.size() && _pf_units[_pf_at] >= '0'
                                && _pf_units[_pf_at] <= '9')
                            _pf_result += static_cast<char>(_pf_take());
                }
            } else if (_pf_code == '(') {
                _pf_result += '(';
                if (_pf_at < _pf_units.size() && _pf_units[_pf_at] == '?') {
                    _pf_result += static_cast<char>(_pf_take());
                    if (_pf_at < _pf_units.size() && _pf_units[_pf_at] < 128)
                        _pf_result += static_cast<char>(_pf_take());
                }
            } else if (_pf_code == '{') {
                _pf_result += '{';
                while (_pf_at < _pf_units.size()
                       && ((_pf_units[_pf_at] >= '0' && _pf_units[_pf_at] <= '9')
                           || _pf_units[_pf_at] == ','))
                    _pf_result += static_cast<char>(_pf_take());
                if (_pf_at < _pf_units.size() && _pf_units[_pf_at] == '}')
                    _pf_result += static_cast<char>(_pf_take());
            } else if (_pf_code == ')' || _pf_code == '|' || _pf_code == '^'
                       || _pf_code == '$' || _pf_code == '*' || _pf_code == '+'
                       || _pf_code == '?' || _pf_code == '}') {
                _pf_result += static_cast<char>(_pf_code);
            } else {
                _pf_code = _pf_pair(_pf_code);
                _pf_result += _pf_atom({{_pf_code, _pf_code}});
            }
        }
        return _pf_result;
    }
};

inline std::string _pf_match(const std::string& _pf_source,
                             const std::string& _pf_regex, int64_t _pf_bar) {
    const auto _pf_pattern_units = _pf_characters(_pf_regex);
    std::string _pf_pattern_bytes;
    try {
        _pf_pattern_bytes = _pf_pattern(_pf_pattern_units)._pf_compile();
    } catch (const _pf_invalid&) {
        // If the byte grammar already rejects this spelling, retain its
        // exact runtime reason/context. A range invalid only in characters still
        // needs the same coded invalid-pattern stop.
        (void)::pineforge::pine_str_match(_pf_source, _pf_regex);
        const std::string _pf_message = "Error on bar " + std::to_string(_pf_bar)
            + ": Invalid regex pattern in the 'str.match' function.";
        _PF_STRING_STOP("invalid_regex_pattern", _pf_message.c_str());
    }
    std::string _pf_source_bytes;
    for (const uint32_t _pf_unit : _pf_characters(_pf_source))
        for (const uint32_t _pf_byte : _pf_bytes(_pf_unit))
            _pf_source_bytes += static_cast<char>(_pf_byte);
    const auto _pf_matched = ::pineforge::pine_str_match(_pf_source_bytes, _pf_pattern_bytes);
    const auto _pf_matched_units = _pf_u16_decode(_pf_matched);
    return _pf_u16_encode(_pf_matched_units, 0, _pf_matched_units.size());
}
} // namespace _pf_regex
"""
