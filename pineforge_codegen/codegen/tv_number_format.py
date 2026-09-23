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

static std::string _pf_tv_decimal(double value, int min_fraction,
                                  int max_fraction, bool grouping) {
    if (std::isnan(value)) return "NaN";
    if (std::isinf(value)) return value < 0 ? "-Infinity" : "Infinity";
    max_fraction = std::max(0, std::min(max_fraction, 15));
    min_fraction = std::max(0, std::min(min_fraction, max_fraction));
    const bool negative = value < 0;
    const long double magnitude = std::fabs(static_cast<long double>(value));
    const long double scale = std::pow(10.0L, max_fraction);
    // Pine formats the shortest decimal representation at half ties. The
    // small allowance removes binary64 representation noise at that tie
    // (e.g. 1.005 to two places) without changing ordinary decimal rounding.
    const long double rounded = magnitude < 1.0e17L / scale
        ? std::floor(magnitude * scale + 0.5L + 1.0e-12L) / scale
        : magnitude;
    std::ostringstream stream;
    stream.imbue(std::locale::classic());
    stream << std::fixed << std::setprecision(max_fraction) << rounded;
    std::string digits = stream.str();
    const size_t dot = digits.find('.');
    if (dot != std::string::npos) {
        while (digits.size() > dot + 1 + static_cast<size_t>(min_fraction)
               && digits.back() == '0') digits.pop_back();
        if (digits.back() == '.') digits.pop_back();
    }
    if (grouping) {
        const size_t integer_end = digits.find('.');
        const size_t integer_size = integer_end == std::string::npos
            ? digits.size() : integer_end;
        std::string grouped;
        for (size_t i = 0; i < integer_size; ++i) {
            if (i && (integer_size - i) % 3 == 0) grouped += ',';
            grouped += digits[i];
        }
        digits = grouped + digits.substr(integer_size);
    }
    return (negative && rounded != 0.0L ? "-" : "") + digits;
}

static std::string _pf_tv_pattern(double value, const std::string& pattern) {
    const std::string fmt = _pf_tv_trim(pattern);
    const size_t dot = fmt.find('.');
    const std::string integer = fmt.substr(0, dot);
    const std::string fraction = dot == std::string::npos ? "" : fmt.substr(dot + 1);
    const int max_fraction = static_cast<int>(std::count_if(
        fraction.begin(), fraction.end(), [](char c) { return c == '0' || c == '#'; }));
    const int min_fraction = static_cast<int>(std::count(fraction.begin(), fraction.end(), '0'));
    const bool grouping = integer.find(',') != std::string::npos;
    const bool percent = fmt.find('%') != std::string::npos;
    return _pf_tv_decimal(percent ? value * 100.0 : value,
                          min_fraction, max_fraction, grouping)
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
        double divisor = 1.0;
        const char* unit = "";
        if (magnitude >= 1.0e12) { divisor = 1.0e12; unit = "T"; }
        else if (magnitude >= 1.0e9) { divisor = 1.0e9; unit = "B"; }
        else if (magnitude >= 1.0e6) { divisor = 1.0e6; unit = "M"; }
        else if (magnitude >= 1.0e3) { divisor = 1.0e3; unit = "K"; }
        return _pf_tv_pattern(value / divisor, divisor == 1.0 ? "#" : "#.##") + unit;
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
    const std::string fmt = _pf_tv_trim(style);
    if (fmt.empty()) return _pf_tv_pattern(value, "#,###.###");
    if (fmt == "integer") return _pf_tv_pattern(value, "#,###");
    if (fmt == "percent") return _pf_tv_pattern(value * 100.0, "#,###") + "%";
    if (fmt == "currency") {
        const std::string digits = _pf_tv_decimal(std::fabs(value), 2, 2, true);
        return (value < 0 ? "-$" : "$") + digits;
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
