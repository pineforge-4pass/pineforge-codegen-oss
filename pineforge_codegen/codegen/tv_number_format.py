"""C++ number formatting emitted for Pine string calls.

The engine's str_tostring(..., "mintick") already performs tick rounding and
keeps trailing zeros, so the generated helper delegates that mode. Its default
str_tostring uses std::to_string, its percent multiplies by 100, and its
volume keeps two decimals; the TradingView tapes refute all three. The engine
str_format only replaces plain {i} and cannot parse MessageFormat styles.
These gaps are handled here in one TU-local formatter shared by str.tostring,
str.format and log.*. No runtime ABI or Python dependency is added.
"""

TV_NUMBER_FORMAT_CPP = r"""
struct _PFTvFormatValue {
    enum class Kind { Number, Text } kind;
    double number = 0.0;
    std::string text;

    template <class T, std::enable_if_t<std::is_arithmetic_v<T> &&
                                        !std::is_same_v<T, bool>, int> = 0>
    _PFTvFormatValue(T value)
        : kind(Kind::Number),
          number(is_na(value) ? na<double>() : static_cast<double>(value)) {}
    _PFTvFormatValue(bool value)
        : kind(Kind::Text), text(value ? "true" : "false") {}
    _PFTvFormatValue(const std::string& value)
        : kind(Kind::Text), text(value) {}
    _PFTvFormatValue(const char* value)
        : kind(Kind::Text), text(value) {}
};

static std::string _pf_tv_trim(const std::string& value) {
    const size_t first = value.find_first_not_of(" \t");
    if (first == std::string::npos) return "";
    const size_t last = value.find_last_not_of(" \t");
    return value.substr(first, last - first + 1);
}

static void _pf_tv_increment_digits(std::string& digits) {
    for (size_t i = digits.size(); i > 0; --i) {
        if (digits[i - 1] != '9') {
            ++digits[i - 1];
            return;
        }
        digits[i - 1] = '0';
    }
    digits.insert(digits.begin(), '1');
}

static std::string _pf_tv_decimal(double value, int min_fraction,
                                  int max_fraction, bool grouping,
                                  int decimal_shift = 0) {
    if (std::isnan(value)) return "NaN";
    if (std::isinf(value)) return value < 0 ? "-Infinity" : "Infinity";
    max_fraction = std::max(0, std::min(max_fraction, 15));
    min_fraction = std::max(0, std::min(min_fraction, max_fraction));

    // C++17's no-precision to_chars gives the shortest round-trip decimal
    // spelling of this binary64 value. All subsequent scaling and rounding
    // operate on its digits, never on a floating-point intermediate.
    char buffer[128];
    const auto converted = std::to_chars(buffer, buffer + sizeof buffer, value);
    if (converted.ec != std::errc{})
        throw std::runtime_error("shortest-decimal conversion failed");
    std::string spelling(buffer, converted.ptr);
    const bool negative = !spelling.empty() && spelling[0] == '-';
    if (negative) spelling.erase(0, 1);

    const size_t exponent_pos = spelling.find_first_of("eE");
    const std::string mantissa = spelling.substr(0, exponent_pos);
    int exponent = 0;
    if (exponent_pos != std::string::npos) {
        size_t i = exponent_pos + 1;
        bool exponent_negative = false;
        if (i < spelling.size() && (spelling[i] == '+' || spelling[i] == '-')) {
            exponent_negative = spelling[i] == '-';
            ++i;
        }
        for (; i < spelling.size(); ++i)
            exponent = exponent * 10 + (spelling[i] - '0');
        if (exponent_negative) exponent = -exponent;
    }

    std::string digits;
    int decimal_point = 0;
    bool after_point = false;
    for (char ch : mantissa) {
        if (ch == '.') {
            after_point = true;
        } else {
            digits += ch;
            if (!after_point) ++decimal_point;
        }
    }
    decimal_point += exponent + decimal_shift;
    const size_t leading_zeroes = digits.find_first_not_of('0');
    if (leading_zeroes == std::string::npos) {
        digits = "0";
        decimal_point = 1;
    } else {
        digits.erase(0, leading_zeroes);
        decimal_point -= static_cast<int>(leading_zeroes);
    }

    // The retained digits form an integer in units of 10^-max_fraction.
    // The first discarded decimal digit decides a half-up tie exactly.
    std::string units;
    if (digits == "0") {
        units = "0";
    } else {
        const int keep = decimal_point + max_fraction;
        if (keep < 0) {
            units = "0";
        } else if (keep == 0) {
            units = digits[0] >= '5' ? "1" : "0";
        } else if (keep >= static_cast<int>(digits.size())) {
            units = digits;
            units.append(static_cast<size_t>(keep) - digits.size(), '0');
        } else {
            units = digits.substr(0, static_cast<size_t>(keep));
            if (digits[static_cast<size_t>(keep)] >= '5')
                _pf_tv_increment_digits(units);
        }
    }
    const bool nonzero = units.find_first_not_of('0') != std::string::npos;
    if (!nonzero) units = "0";
    if (units.size() <= static_cast<size_t>(max_fraction))
        units.insert(0, static_cast<size_t>(max_fraction) + 1 - units.size(), '0');
    const size_t integer_size = units.size() - static_cast<size_t>(max_fraction);
    std::string integer = units.substr(0, integer_size);
    std::string fraction = units.substr(integer_size);
    while (fraction.size() > static_cast<size_t>(min_fraction)
           && fraction.back() == '0') fraction.pop_back();
    if (grouping) {
        std::string grouped;
        for (size_t i = 0; i < integer_size; ++i) {
            if (i && (integer_size - i) % 3 == 0) grouped += ',';
            grouped += integer[i];
        }
        integer = grouped;
    }
    return (negative && nonzero ? "-" : "") + integer
        + (fraction.empty() ? "" : "." + fraction);
}

static std::string _pf_tv_pattern(double value, const std::string& pattern) {
    if (!std::isfinite(value)) return _pf_tv_decimal(value, 0, 0, false);
    const std::string fmt = _pf_tv_trim(pattern);
    const size_t dot = fmt.find('.');
    const std::string integer = fmt.substr(0, dot);
    const std::string fraction = dot == std::string::npos ? "" : fmt.substr(dot + 1);
    const int max_fraction = static_cast<int>(std::count_if(
        fraction.begin(), fraction.end(), [](char c) { return c == '0' || c == '#'; }));
    const int min_fraction = static_cast<int>(std::count(fraction.begin(), fraction.end(), '0'));
    const bool grouping = integer.find(',') != std::string::npos;
    const bool percent = fmt.find('%') != std::string::npos;
    return _pf_tv_decimal(value, min_fraction, max_fraction, grouping,
                          percent ? 2 : 0)
        + (percent ? "%" : "");
}

static std::string pine_str_tostring_tv(double value,
                                        const std::string& format_mode = "",
                                        double mintick = 0.0) {
    if (std::isnan(value)) return "NaN";
    if (std::isinf(value)) return value < 0 ? "-Infinity" : "Infinity";
    if (format_mode == "mintick")
        return pine_str_tostring(value, format_mode, mintick);
    if (format_mode == "percent")
        return _pf_tv_pattern(value, "#.##") + "%";
    if (format_mode == "volume") {
        const double magnitude = std::fabs(value);
        int decimal_shift = 0;
        const char* unit = "";
        if (magnitude >= 1.0e12) { decimal_shift = -12; unit = "T"; }
        else if (magnitude >= 1.0e9) { decimal_shift = -9; unit = "B"; }
        else if (magnitude >= 1.0e6) { decimal_shift = -6; unit = "M"; }
        else if (magnitude >= 1.0e3) { decimal_shift = -3; unit = "K"; }
        return _pf_tv_decimal(value, 0, decimal_shift ? 2 : 0, false,
                              decimal_shift) + unit;
    }
    return _pf_tv_pattern(value, format_mode.empty() ? "#.##########" : format_mode);
}

template <class T, std::enable_if_t<std::is_integral_v<T> &&
                                    !std::is_same_v<T, bool>, int> = 0>
static std::string pine_str_tostring_tv(T value,
                                        const std::string& format_mode = "",
                                        double mintick = 0.0) {
    return pine_str_tostring_tv(
        is_na(value) ? na<double>() : static_cast<double>(value),
        format_mode, mintick);
}

static std::string _pf_tv_number_style(double value, const std::string& style) {
    if (!std::isfinite(value)) return _pf_tv_decimal(value, 0, 0, false);
    const std::string fmt = _pf_tv_trim(style);
    if (fmt.empty()) return _pf_tv_pattern(value, "#,###.###");
    if (fmt == "integer") return _pf_tv_pattern(value, "#,###");
    if (fmt == "percent") return _pf_tv_decimal(value, 0, 0, true, 2) + "%";
    if (fmt == "currency") {
        const std::string digits = _pf_tv_decimal(value, 2, 2, true);
        return digits[0] == '-' ? "-$" + digits.substr(1) : "$" + digits;
    }
    return _pf_tv_pattern(value, fmt);
}

static std::string pine_str_format_tv(
    const std::string& format_string,
    const std::vector<_PFTvFormatValue>& args) {
    std::string result;
    bool quoted = false;
    for (size_t i = 0; i < format_string.size();) {
        const char c = format_string[i];
        if (c == '\'') {
            if (i + 1 < format_string.size() && format_string[i + 1] == '\'') {
                result += '\'';
                i += 2;
            } else {
                quoted = !quoted;
                ++i;
            }
            continue;
        }
        if (c != '{' || quoted) {
            result += c;
            ++i;
            continue;
        }
        const size_t end = format_string.find('}', i + 1);
        if (end == std::string::npos) {
            result += format_string.substr(i);
            break;
        }
        const std::string inside = format_string.substr(i + 1, end - i - 1);
        const size_t first_comma = inside.find(',');
        const std::string index_text = _pf_tv_trim(inside.substr(0, first_comma));
        if (index_text.empty() || !std::all_of(index_text.begin(), index_text.end(),
                                               [](char ch) { return ch >= '0' && ch <= '9'; })) {
            result += format_string.substr(i, end - i + 1);
            i = end + 1;
            continue;
        }
        const size_t index = static_cast<size_t>(std::stoul(index_text));
        if (index >= args.size()) {
            result += format_string.substr(i, end - i + 1);
            i = end + 1;
            continue;
        }
        const auto& arg = args[index];
        if (arg.kind == _PFTvFormatValue::Kind::Text) {
            result += arg.text;
        } else if (first_comma == std::string::npos) {
            result += _pf_tv_number_style(arg.number, "");
        } else {
            const size_t second_comma = inside.find(',', first_comma + 1);
            const std::string type = _pf_tv_trim(inside.substr(
                first_comma + 1, second_comma - first_comma - 1));
            if (type == "number") {
                const std::string style = second_comma == std::string::npos
                    ? "" : inside.substr(second_comma + 1);
                result += _pf_tv_number_style(arg.number, style);
            } else {
                result += format_string.substr(i, end - i + 1);
            }
        }
        i = end + 1;
    }
    return result;
}
"""
