"""Color helper state uses the requested clock and preserves packed ARGB."""

from __future__ import annotations

import re
import subprocess

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Phase
from tests import _compile as compile_env


REPORTER = """//@version=6
strategy('Color state')
f(x) =>
    var color c = color.red
    c := x > 0 ? color.green : color.red
    c
c = request.security(syminfo.tickerid, '60', f(close - open))
plot(close, color=c)
"""

HEADER = '//@version=6\nstrategy("helper color state")\n'
# Each pair is a requested two-minute candle. Both signs, and a change of
# sign within a requested candle, exercise new slots and recomputations.
DELTAS = (-1, -1, 1, 1, 1, -1, -1, -1, 1, 1, -1, -1)

EPOCH_COUNTER_STATE = (
    HEADER + "h() =>\n"
    "    var int n = 0\n"
    "    n += 1\n"
    "    n\n"
    "g() =>\n"
    "    n = time\n"
    "    n\n"
    'out = request.security(syminfo.tickerid, "60", h())\n'
    "plot(out + g())\n"
)


def _run_values(source, work, *, chart_tf="1"):
    # Transpile before an environment skip, so the regression is visible even
    # without native inputs. Native compilation/execution belongs in the CI job that has the native engine.
    cpp = transpile(source)
    compile_env.skip_if_no_engine_lib()
    work.mkdir()
    bars = ",\n".join(
        f"Bar{{100.0, 102.0, 98.0, {100 + delta}.0, 1.0, {i * 60000}}}"
        for i, delta in enumerate(DELTAS)
    )
    driver = f"""
#include <iomanip>
#include <iostream>
int main() {{
    Bar bars[] = {{{bars}}};
    std::cout << std::setprecision(17);
    for (int count = 1; count <= {len(DELTAS)}; ++count) {{
        GeneratedStrategy strategy;
        strategy.run(bars, count, "{chart_tf}", "{chart_tf}");
        std::cout << strategy.out << "\\n";
    }}
}}
"""
    cpp_path, executable = work / "probe.cpp", work / "probe"
    cpp_path.write_text(cpp + driver, encoding="utf-8")
    command = [
        compile_env._COMPILER, "-std=c++17", "-O2",
        *compile_env.STRATEGY_FP_FLAGS,
        *compile_env._include_flags(isolate_headers=True),
        str(cpp_path), str(compile_env._ENGINE_LIB), "-pthread",
        "-o", str(executable),
    ]
    built = subprocess.run(command, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(executable)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    values = [float(value) for value in ran.stdout.splitlines()]
    assert len(values) == len(DELTAS)
    return values


def test_reporter_color_state_translates_through_the_double_series():
    cpp = transpile(REPORTER)
    body = cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]
    assert "std::unordered_map<std::string, Series<double>> _security_helper_series_;" in cpp
    assert "@var_seed" in body
    assert "static_cast<double>(_pf_color)" in body
    assert "(int64_t)_pf_v" in body
    assert re.search(r'_security_helper_series_\["[^"]+"\]\.update\(', body)


@pytest.mark.parametrize("declaration", [
    "var color palette = color.rgb(19, 71, 139, 25)",
    "var palette = color.red",
    "var palette = color.new(color.fuchsia, 50)",
    "var color palette = na",
])
def test_color_state_uses_the_declarations_pine_type(declaration):
    cpp = transpile(
        HEADER + "f(x) =>\n"
        f"    {declaration}\n"
        "    if x > 0\n"
        "        palette := color.new(color.aqua, 50)\n"
        "    palette\n"
        'out = request.security(syminfo.tickerid, "2", f(close - open))\n'
        "plot(close, color=out)\n"
    )
    assert "@var_seed" in cpp
    assert "static_cast<double>(_pf_color)" in cpp
    assert "(int64_t)_pf_v" in cpp


@pytest.mark.parametrize("hint, down, up", [
    ("color ", "color.red", "color.green"),
    ("", "color.new(color.fuchsia, 50)", "color.new(color.aqua, 25)"),
])
def test_requested_color_matches_a_helper_without_var(tmp_path, hint, down, up):
    tail = (
        'out = request.security(syminfo.tickerid, "2", f(close - open), '
        "lookahead=barmerge.lookahead_on)\n"
        "plot(close, color=out)\n"
    )
    subject = (
        HEADER + "f(x) =>\n"
        f"    var {hint}palette = {down}\n"
        f"    palette := x > 0 ? {up} : {down}\n"
        "    palette\n" + tail
    )
    control = HEADER + f"f(x) => x > 0 ? {up} : {down}\n" + tail
    actual = _run_values(subject, tmp_path / "state")
    expected = _run_values(control, tmp_path / "control")
    assert len(set(expected)) == 2
    assert actual == expected
    # Compare the complete unsigned packed value, including the high alpha
    # byte; the expected colors themselves come only from the control run.
    assert all(value == int(value) and value > 2**31 for value in expected)


def test_color_state_persists_when_the_next_requested_bar_does_not_assign(tmp_path):
    subject = (
        HEADER + "f(x) =>\n"
        "    var color palette = color.red\n"
        "    if x > 0\n"
        "        palette := color.new(color.green, 50)\n"
        "    palette\n"
        'out = request.security(syminfo.tickerid, "1", f(close - open))\n'
        "plot(close, color=out)\n"
    )
    control = (
        HEADER + "var color palette = color.red\n"
        "if close - open > 0\n"
        "    palette := color.new(color.green, 50)\n"
        "out = palette\n"
        "plot(close, color=out)\n"
    )
    actual = _run_values(subject, tmp_path / "state")
    expected = _run_values(control, tmp_path / "chart")
    first_up = next(i for i, delta in enumerate(DELTAS) if delta > 0)
    assert expected[first_up] != expected[0]
    assert any(delta <= 0 for delta in DELTAS[first_up + 1:])
    assert all(value == expected[first_up] for value in expected[first_up:])
    assert actual == expected


def test_na_color_seed_and_reassignment_match_chart_state(tmp_path):
    statements = (
        "var color palette = na\n"
        "if close > open\n"
        "    palette := color.new(color.orange, 50)\n"
        "else if close < open\n"
        "    palette := na\n"
        "adjusted = color.new(palette, 25)\n"
    )
    # color.new must see an int64_t na color on read, rather than a double
    # NaN cast implicitly to an integer. Test the missingness inside the helper.
    result = "na(adjusted) ? -1.0 : color.t(adjusted)"
    subject = (
        HEADER + "f() =>\n"
        + "".join("    " + line + "\n" for line in statements.splitlines())
        + f"    {result}\n"
        'out = request.security(syminfo.tickerid, "1", f())\n'
        "plot(out)\n"
    )
    control = HEADER + statements + f"out = {result}\nplot(out)\n"
    actual = _run_values(subject, tmp_path / "state")
    expected = _run_values(control, tmp_path / "chart")
    assert len(set(expected)) == 2
    assert actual == expected


def test_color_helper_var_state_is_accepted():
    transpile(REPORTER)


def test_epoch_local_keeps_counter_state_accepted():
    cpp = transpile(EPOCH_COUNTER_STATE)
    declarations = {line.strip() for line in cpp.splitlines()}
    assert "int n = na<int>();" in declarations
    assert "int64_t n = current_bar_.timestamp;" in declarations


def test_epoch_local_counter_state_compiles():
    # Keep the native skip confined to this test so it cannot hide refusals.
    compile_env.compile_cpp(transpile(EPOCH_COUNTER_STATE))


VARIP_STATE = (
    HEADER + "f(x) =>\n"
    "    {kind} float state = 0.0\n"
    "    state += x\n"
    "    state\n"
    'out = request.security(syminfo.tickerid, "2", f(close - open), '
    "lookahead=barmerge.lookahead_on)\n"
    "plot(out)\n"
)


def _requested_state_model(deltas, size=2):
    """What a state that adds the requested candle's close - open once per
    COMPLETED requested candle reads after each prefix of the one-minute bars,
    with lookahead on.

    Every bar opens at 100.0 and closes at 100 + delta, so a requested candle
    of ``size`` bars (open: its first bar's open, close: its last bar's close)
    adds the delta of its last bar. After ``count`` bars the state is the sum
    of the candles completed before the one holding bar ``count`` plus that
    candle's partial value, which the next evaluation rolls back: the
    documented behaviour of a persistent variable evaluated once per
    requested bar. Computed from the definition, not from a build."""
    model = []
    for count in range(1, len(deltas) + 1):
        completed = (count - 1) // size
        committed = sum(deltas[size * m + size - 1] for m in range(completed))
        model.append(float(committed + deltas[count - 1]))
    return model


# Recorded, not computed (2026-11-05): the twelve values the released build
# (codegen v1.10.0 at 47f0e8a5 on engine v1.10.0 at 59f98ba8) returns after each
# prefix of the twelve one-minute bars (DELTAS), lookahead on, for two forms of the
# same running state: ``ta.cum(close - open)`` inside the requested expression (the
# built-in accumulator, REQUESTED_CUM below) and a requested helper holding ``var``
# state (VARIP_STATE with kind="var"). Both returned this list; the release refuses
# a requested helper's ``varip`` (PF-E3027). The first form has no helper state and
# the second is the release's helper lowering, not the one these rows test, so the
# list is an oracle that lowering did not produce.
RELEASE_REQUESTED_STATE = [-1.0, -1.0, 0.0, 0.0, 1.0, -1.0, -2.0, -2.0, -1.0, -1.0, -2.0, -2.0]

REQUESTED_CUM = (
    HEADER
    + 'out = request.security(syminfo.tickerid, "2", ta.cum(close - open), '
    "lookahead=barmerge.lookahead_on)\n"
    "plot(out)\n"
)


def test_the_recorded_release_values_tell_rollback_from_kept_partials():
    # The recorded list is the documented behaviour (the model above, computed
    # from the definition) and it differs from a state that keeps the partial
    # evaluations of a requested candle, so it can fail a lowering that keeps
    # them.
    assert _requested_state_model(DELTAS) == RELEASE_REQUESTED_STATE
    kept, naive = 0.0, []
    for delta in DELTAS:
        kept += delta
        naive.append(kept)
    assert RELEASE_REQUESTED_STATE != naive
    assert len(set(RELEASE_REQUESTED_STATE)) > 2


@pytest.mark.parametrize("form", ["cum", "var", "varip"])
def test_requested_state_forms_equal_the_recorded_release_values(form, tmp_path):
    # A requested helper admits varip state on the historical requested-bar
    # protocol (the runtime request contexts lowering): a requested bar
    # advances the state once, when it completes, and its partial evaluations
    # roll back. Each form is held to the list recorded from the released
    # build, not to another build of this tree, so an equality of two wrong
    # runs cannot pass. ``cum`` is the independent path (no helper state at
    # all); whether TradingView keeps varip and var apart in a requested
    # helper on a live bar is not measured by these rows.
    source = REQUESTED_CUM if form == "cum" else VARIP_STATE.format(kind=form)
    assert _run_values(source, tmp_path / form) == RELEASE_REQUESTED_STATE


def test_requested_helper_collection_state_is_refused():
    collection = (
        HEADER + "f() =>\n"
        "    var array<float> state = array.new<float>()\n"
        "    state.size()\n"
        'out = request.security(syminfo.tickerid, "2", f())\n'
    )
    with pytest.raises(CompileError, match="supports only int, float, bool and string values") as caught:
        transpile(collection)
    assert [(d.code, d.phase) for d in caught.value.diagnostics] == [
        ("PF-E3093", Phase.CODEGEN),
    ]
