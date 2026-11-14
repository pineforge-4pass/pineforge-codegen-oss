"""Unit checks of the byte-pattern compiler, not Pine replay expectations."""

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


def _compiler_tu():
    return transpile('//@version=6\nstrategy("regex compiler")\n'
                     'result = str.match("abc", "b")\n')


def test_pattern_compiler_preserves_capture_targets_under_whole_match_group():
    driver = r'''
#include <cassert>
static void check(const std::string& pattern, const std::string& subject,
                  const std::vector<std::string>& captures) {
    const auto units = _pf_regex::_pf_characters(pattern);
    const std::string compiled = _pf_regex::_pf_pattern(units)._pf_compile();
    const std::regex re(compiled);
    std::smatch match;
    assert(std::regex_search(subject, match, re));
    assert(match.size() == captures.size() + 2);
    assert(match[1].str() == match[0].str());
    for (std::size_t i = 0; i < captures.size(); ++i)
        assert(match[i + 2].str() == captures[i]);
}
int main() {
    check(R"((x)(y)\2\1)", "xyyx", {"x", "y"});
    check(R"((?:q)(x)(?=y)(y)\2\1)", "qxyyx", {"x", "y"});
    check(R"((a)(b)(c)(d)(e)(f)(g)(h)(i)\9)", "abcdefghii",
          {"a", "b", "c", "d", "e", "f", "g", "h", "i"});
    check(R"((a)(b)(c)(d)(e)(f)(g)(h)(i)(j)\10)", "abcdefghijj",
          {"a", "b", "c", "d", "e", "f", "g", "h", "i", "j"});
    check(R"((é+)\1)", "éééé", {"éé"});
    check(R"(\(x\))", "(x)", {});
    check(R"([(]x[)])", "(x)", {});
    // Two backslashes and the digit are a literal, not a reference to shift.
    check(R"(\\1)", R"(\1)", {});
    const auto units = _pf_regex::_pf_characters(R"(\99999999999999999999)");
    const std::string compiled = _pf_regex::_pf_pattern(units)._pf_compile();
    assert(compiled == R"((\100000000000000000000))");
}
'''
    assert run_emitted_tu(_compiler_tu(), driver, opt="-O0",
                          label="regex capture compiler") == ""


def test_whole_match_wrapper_cannot_balance_invalid_user_parentheses():
    driver = r'''
#include <cassert>
int main() {
    for (const std::string pattern : {")(", ")a(", "(", "a)", "(?:x"}) {
        const auto units = _pf_regex::_pf_characters(pattern);
        bool refused = false;
        try { (void)_pf_regex::_pf_pattern(units)._pf_compile(); }
        catch (const _pf_regex::_pf_invalid&) { refused = true; }
        assert(refused);
    }
    // A synthetic group cannot make a nonexistent user reference valid.
    const auto units = _pf_regex::_pf_characters(R"(\1)");
    const std::string compiled = _pf_regex::_pf_pattern(units)._pf_compile();
    bool refused = false;
    try { (void)std::regex(compiled); }
    catch (const std::regex_error& error) {
        refused = error.code() == std::regex_constants::error_backref;
    }
    assert(refused);
}
'''
    assert run_emitted_tu(_compiler_tu(), driver, opt="-O0",
                          label="regex wrapper grammar") == ""
