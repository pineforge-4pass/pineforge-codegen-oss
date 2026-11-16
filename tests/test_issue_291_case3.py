"""Issue #291 case 3: an ordinary string's bare na matches its cast control."""

from pineforge_codegen import transpile


REPORTER = """//@version=6
strategy('repro')
string tag = na
if bar_index % 3 == 0
    tag := 'x'
var int seen = 0
if not na(tag)
    seen += 1
// @pf-trace seen=seen
plot(seen)
"""
CONTROL = REPORTER.replace("string tag = na\n", "string tag = string(na)\n", 1)


def test_non_var_string_na_matches_reporters_cast_control():
    control_cpp = transpile(CONTROL)
    assert "tag = na<std::string>();" in control_cpp
    # Compare the complete translation units, including the per-bar reset,
    # missing-value check, persistent counter and trace from the exact script.
    # The broken lowering assigns na<double>() to tag instead of the string na.
    assert transpile(REPORTER) == control_cpp
