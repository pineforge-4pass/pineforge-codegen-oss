"""Regression: ``request.security`` / ``request.security_lower_tf`` timeframe
arguments that depend on an ``input.*()``-derived script variable must be
emitted in ``configure_security_evaluators()`` over the INPUT SOURCE READS
(``get_input_string(...)`` etc.), never over the uninitialised class member.

``configure_security_evaluators()`` runs once at setup, BEFORE the first
``on_bar()`` assigns members from their inputs. A member holding an input
value (e.g. ``exec_tf_mode``) is therefore still its C++ default ("") at
registration time, so a conditional timeframe such as
``exec_tf_mode == "15" ? "240" : "60"`` takes the wrong branch and registers
the security at the wrong timeframe. The fix resolves input-derived leaves —
anywhere in the tf expression, including inside a ternary *condition* — to the
input getter that is valid at registration time.

See oscar-lab MTF confluence root-cause (data/ campaign, KI series).
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


PRELUDE = '//@version=6\nstrategy("t")\n'


def _register_lines(src: str) -> list[str]:
    """Return the trimmed ``register_security*`` lines from the generated
    ``configure_security_evaluators()`` body (order preserved)."""
    return [
        ln.strip()
        for ln in transpile(src).splitlines()
        if "register_security_eval(" in ln
        or "register_security_lower_tf_eval(" in ln
    ]


# ---------------------------------------------------------------------------
# R1 (GREEN guard) — a tf that is a variable DIRECTLY assigned an input.*()
# call already resolves to the input getter (the var is in _input_var_to_call).
# The fix must keep this working.
# ---------------------------------------------------------------------------

def test_r1_direct_input_var_tf_uses_input_getter():
    src = (
        PRELUDE
        + 'tf = input.string("60", "TF")\n'
        + "x = request.security(syminfo.tickerid, tf, close)\nplot(x)\n"
    )
    line = _register_lines(src)[0]
    assert 'get_input_string("TF"' in line, line
    # The bare uninitialised member must not be the timeframe argument.
    assert "0, tf," not in line, line


# ---------------------------------------------------------------------------
# R2 (RED on HEAD) — conditional timeframe over an input-derived var. The
# ternary CONDITION reads the member on HEAD; it must read the input getter.
# ---------------------------------------------------------------------------

def test_r2_conditional_tf_over_input_uses_input_getter():
    src = (
        PRELUDE
        + 'mode = input.string("15", "Exec", options=["5", "15"])\n'
        + 'tf = mode == "15" ? "240" : "60"\n'
        + "x = request.security(syminfo.tickerid, tf, close)\nplot(x)\n"
    )
    line = _register_lines(src)[0]
    # Registration-time-valid input read, not the default-"" member.
    assert 'get_input_string("Exec"' in line, line
    assert "(mode == std::string" not in line, line


def test_r2_inline_conditional_tf_over_input():
    # The tf expression is written inline at the call site (no intermediate
    # var) — same defect, exercises the _resolve_security_tf fallthrough.
    src = (
        PRELUDE
        + 'mode = input.string("15", "Exec", options=["5", "15"])\n'
        + 'x = request.security(syminfo.tickerid, mode == "15" ? "240" : "60", close)\nplot(x)\n'
    )
    line = _register_lines(src)[0]
    assert 'get_input_string("Exec"' in line, line
    assert "(mode == std::string" not in line, line


# ---------------------------------------------------------------------------
# R3 (RED on HEAD) — request.security_lower_tf variant of the same defect.
# ---------------------------------------------------------------------------

def test_r3_lower_tf_conditional_over_input_uses_input_getter():
    src = (
        PRELUDE
        + 'mode = input.string("15", "Exec", options=["5", "15"])\n'
        + 'sub = mode == "15" ? "5" : "1"\n'
        + "a = request.security_lower_tf(syminfo.tickerid, sub, close)\n"
        + "plot(array.size(a))\n"
    )
    line = _register_lines(src)[0]
    assert line.startswith("register_security_lower_tf_eval("), line
    assert 'get_input_string("Exec"' in line, line
    assert "(mode == std::string" not in line, line


def test_lower_tf_runtime_persistent_timeframe_leaf_initializes_series_metadata_first():
    src = (
        PRELUDE
        + "var int seconds = 60\n"
        + "seconds := int(close)\n"
        + 'string sub = seconds > 30 ? "1" : "5"\n'
        + "a = request.security_lower_tf(syminfo.tickerid, sub, close)\n"
        + "plot(array.size(a))\n"
    )
    cpp = transpile(src)
    registrations = [
        ln.strip()
        for ln in cpp.splitlines()
        if "register_security_lower_tf_eval(" in ln
    ]
    # A reassigned chart value selects per bar, after its assignment and the
    # declaration of sub. Setup registers both independent intrabar streams.
    assert registrations == [
        'register_security_lower_tf_eval(0, "1", input_tf_);',
        'register_security_lower_tf_eval(1, "5", input_tf_);',
    ]
    body = cpp.split("void on_source_bar(", 1)[1]
    selected = re.search(r"const std::string _pf_selected_tf = sub(?:\[0\])?;", body)
    assert selected is not None
    assignment = re.search(r"seconds(?:\[0\])? = ", body)
    declaration = re.search(r"sub(?:\[0\])? = ", body)
    assert assignment is not None and declaration is not None
    assert assignment.start() < declaration.start() < selected.start()
    assert "current_bar_.close" in body[assignment.start():declaration.start()]
    assert "seconds" in body[declaration.start():selected.start()]
    assert 'std::string("1")' in body[declaration.start():selected.start()]
    assert 'std::string("5")' in body[declaration.start():selected.start()]
    assert (
        '    if (_pf_selected_tf == "1") return _req_sec_lower_tf_0;\n'
        '    if (_pf_selected_tf == "5") return _req_sec_lower_tf_1;\n'
        '    _PF_ENGINE_INVARIANT("Requested timeframe matched no registered timeframe", std::logic_error);\n'
        '}())'
    ) in body
    for sec_id in (0, 1):
        assert f"std::vector<double> _req_sec_lower_tf_{sec_id}{{}};" in cpp
        assert f"_req_sec_lower_tf_{sec_id}.clear();" in cpp
        assert f"_req_sec_lower_tf_{sec_id}.push_back(" in cpp
    compile_cpp(cpp, label="lower-tf-runtime-persistent-leaf")


# ---------------------------------------------------------------------------
# Oscar shape — str.length()-gated override ternary whose auto branch nests a
# second input-derived conditional. Both legs must resolve to input getters.
# ---------------------------------------------------------------------------

def test_oscar_shape_override_and_auto_both_resolve_inputs():
    src = (
        PRELUDE
        + 'exec_tf_mode = input.string("15", "Execution mode", options=["5", "15"])\n'
        + 'ctx_mtf_in = input.string("", "Override MTF")\n'
        + 'auto_mtf = exec_tf_mode == "15" ? "60" : "15"\n'
        + "tf_mtf = str.length(ctx_mtf_in) > 0 ? ctx_mtf_in : auto_mtf\n"
        + "x = request.security(syminfo.tickerid, tf_mtf, close)\nplot(x)\n"
    )
    line = _register_lines(src)[0]
    # Override leg reads the override input; auto leg reads the exec-mode input.
    assert 'get_input_string("Override MTF"' in line, line
    assert 'get_input_string("Execution mode"' in line, line
    # No uninitialised member survives anywhere in the tf expression.
    assert "exec_tf_mode ==" not in line, line
    assert "ctx_mtf_in.length" not in line, line


# ---------------------------------------------------------------------------
# GREEN guards — behaviour that must NOT change.
# ---------------------------------------------------------------------------

def test_green_literal_tf_unchanged():
    src = PRELUDE + 'x = request.security(syminfo.tickerid, "240", close)\nplot(x)\n'
    assert _register_lines(src) == [
        'register_security_eval(0, "240", input_tf_, false, false);'
    ]


def test_green_timeframe_period_tf_unchanged():
    # A tf sourced from timeframe.period registers against script_tf_.
    src = (
        PRELUDE
        + "x = request.security(syminfo.tickerid, timeframe.period, close)\nplot(x)\n"
    )
    line = _register_lines(src)[0]
    assert "script_tf_" in line, line
    assert "get_input_string" not in line, line


def test_green_series_dependent_tf_characterized():
    # Replace the old frozen-condition pin: each literal arm now registers
    # independently, and the chart chooses its current requested value per bar.
    src = (
        PRELUDE
        + 'tf = close > open ? "60" : "15"\n'
        + "x = request.security(syminfo.tickerid, tf, close)\nplot(x)\n"
    )
    lines = _register_lines(src)
    assert lines == [
        'register_security_eval(0, "60", input_tf_, false, false);',
        'register_security_eval(1, "15", input_tf_, false, false);',
    ]
    assert all("current_bar_" not in line for line in lines)


def test_green_non_tf_security_args_unaffected():
    # The expression / gaps / lookahead args are untouched by the tf fix.
    src = (
        PRELUDE
        + 'mode = input.string("15", "Exec", options=["5", "15"])\n'
        + 'tf = mode == "15" ? "240" : "60"\n'
        + "x = request.security(syminfo.tickerid, tf, close, "
          "barmerge.gaps_on, barmerge.lookahead_on)\nplot(x)\n"
    )
    line = _register_lines(src)[0]
    # gaps_on -> true (4th register arg), lookahead_on -> true (3rd).
    assert line.endswith("input_tf_, true, true);"), line
