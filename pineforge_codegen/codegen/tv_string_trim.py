"""C++ for str.trim, emitted only when the generated body calls it."""

TV_STRING_TRIM_MARKER = "/* __PINEFORGE_TV_STRING_TRIM__ */"
TV_STRING_TRIM_NAME = "_pf_u8_trim"

TV_STRING_TRIM_CPP = r"""
inline std::string _pf_u8_trim(const std::string& _pf_text) {
    // Return the width only for a valid UTF-8 encoding of ECMAScript
    // WhiteSpace or LineTerminator. Invalid bytes stop either edge scan.
    const auto _pf_trim_width = [](const std::string& _pf_value, std::size_t _pf_start,
                                   std::size_t _pf_limit) -> std::size_t {
        const auto _pf_lead = static_cast<unsigned char>(_pf_value[_pf_start]);
        std::uint32_t _pf_point = _pf_lead;
        std::size_t _pf_width = 1;
        if (_pf_lead >= 0xC2u && _pf_lead <= 0xDFu) {
            _pf_width = 2;
            _pf_point &= 0x1Fu;
        } else if (_pf_lead >= 0xE0u && _pf_lead <= 0xEFu) {
            _pf_width = 3;
            _pf_point &= 0x0Fu;
        } else if (_pf_lead >= 0xF0u && _pf_lead <= 0xF4u) {
            _pf_width = 4;
            _pf_point &= 0x07u;
        } else if (_pf_lead >= 0x80u) {
            return 0;
        }
        if (_pf_width > _pf_limit - _pf_start) return 0;
        for (std::size_t _pf_i = 1; _pf_i < _pf_width; ++_pf_i) {
            const auto _pf_byte = static_cast<unsigned char>(_pf_value[_pf_start + _pf_i]);
            if ((_pf_byte & 0xC0u) != 0x80u) return 0;
            _pf_point = (_pf_point << 6) | (_pf_byte & 0x3Fu);
        }
        constexpr std::uint32_t _pf_minimum[] = {0, 0, 0x80u, 0x800u, 0x10000u};
        if (_pf_point < _pf_minimum[_pf_width] || _pf_point > 0x10FFFFu ||
            (_pf_point >= 0xD800u && _pf_point <= 0xDFFFu)) return 0;

        const bool _pf_space =
            (_pf_point >= 0x0009u && _pf_point <= 0x000Du) ||
            _pf_point == 0x0020u || _pf_point == 0x00A0u || _pf_point == 0x1680u ||
            (_pf_point >= 0x2000u && _pf_point <= 0x200Au) ||
            _pf_point == 0x2028u || _pf_point == 0x2029u || _pf_point == 0x202Fu ||
            _pf_point == 0x205Fu || _pf_point == 0x3000u || _pf_point == 0xFEFFu;
        return _pf_space ? _pf_width : 0;
    };

    std::size_t _pf_first = 0;
    std::size_t _pf_end = _pf_text.size();
    while (_pf_first < _pf_end) {
        const auto _pf_width = _pf_trim_width(_pf_text, _pf_first, _pf_end);
        if (_pf_width == 0) break;
        _pf_first += _pf_width;
    }
    while (_pf_end > _pf_first) {
        std::size_t _pf_start = _pf_end - 1;
        while (_pf_start > _pf_first && _pf_end - _pf_start < 4 &&
               (static_cast<unsigned char>(_pf_text[_pf_start]) & 0xC0u) == 0x80u) {
            --_pf_start;
        }
        // A valid prefix followed by a stray continuation byte is not a
        // valid last sequence. Require the decoded width to reach the end.
        if (_pf_trim_width(_pf_text, _pf_start, _pf_end) != _pf_end - _pf_start) break;
        _pf_end = _pf_start;
    }
    return _pf_text.substr(_pf_first, _pf_end - _pf_first);
}
"""
