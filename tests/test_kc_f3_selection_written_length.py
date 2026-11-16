"""KEYCUT F3: a hidden write can select a finite SMA length each bar."""

import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp


REVIEWER_SOURCE = """//@version=6
strategy("hidden write feeds sma length")
length = input.int(10, "Length")
selected = if close > open
    length := 20
    length
else
    length
plot(ta.sma(close, length))
"""


def _sma_members(cpp: str) -> dict[int, str]:
    members = re.findall(r"^\s*ta::SMA\s+(_ta_sma_\d+);$", cpp, re.M)
    periods = {}
    for member in members:
        constructor = re.search(rf"\b{member}\((\d+)\)", cpp)
        assert constructor is not None, member
        periods[int(constructor.group(1))] = member
    assert len(periods) == len(members)
    return periods


def test_selection_written_sma_selects_two_advanced_histories() -> None:
    # Failing first on H2b: this uncaught call raises PF-E5092 before any
    # C++ assertions. It must translate without restoring input-only folding.
    cpp = transpile(REVIEWER_SOURCE)
    members = _sma_members(cpp)
    assert set(members) == {10, 20}

    snapshot = re.search(
        r"\b(pf_ta_length_choice_\d+)_selected = length(?:\[0\])?;", cpp,
    )
    assert snapshot is not None
    bank = snapshot.group(1)
    initial = re.search(
        r"\b(pf_ta_length_choice_\d+_initial) = get_input_int\(\"Length\", 10\);",
        cpp,
    )
    assert initial is not None
    initial_name = initial.group(1)
    per_bar_init = re.search(rf"\blength = {initial_name};", cpp)
    assert per_bar_init is not None
    reset = next(
        line for line in cpp.splitlines()
        if f"{members[10]} = ta::SMA(" in line
    )
    assert 'get_input_int("Length", 10)' in reset

    updates = []
    for suffix, period in (("true", 10), ("false", 20)):
        # Member declarations and run resets also assign these names. Only
        # updates after the per-bar snapshot can advance the histories here.
        update = re.compile(rf"\b{bank}_{suffix} = [^\n;]+;").search(
            cpp, snapshot.end(),
        )
        assert update is not None
        member = members[period]
        assert f"{member}.compute(current_bar_.close)" in update.group()
        assert f"{member}.recompute(current_bar_.close)" in update.group()
        updates.append(update.start())

    selection = re.compile(
        r"\bpf_ta_length_choice_\d+_result = [^\n]+;",
    ).search(cpp, snapshot.end())
    assert selection is not None
    # The per-bar length snapshot selects the input-sized or 20-sized SMA
    # AFTER both histories advance. This is the finite-choice proof structure.
    assert f"auto _pna_l = ({bank}_selected);" in selection.group()
    assert f"auto _pna_r = ({initial_name});" in selection.group()
    assert "(_pna_l == _pna_r)" in selection.group()
    assert re.search(
        rf"\?\s*\({bank}_true\)\s*:\s*\({bank}_false\)", selection.group(),
    )
    write = re.search(r"\blength = 20;", cpp)
    assert write is not None
    assert per_bar_init.start() < write.start()
    assert write.start() < snapshot.start() < updates[0] < updates[1] < selection.start()


def test_selection_written_sma_cpp_compiles() -> None:
    compile_cpp(transpile(REVIEWER_SOURCE), label="kc_f3_selection_written_sma")


def test_freezing_an_untitled_input_preserves_its_key_and_manifest() -> None:
    source = REVIEWER_SOURCE.replace('input.int(10, "Length")', "input.int(10)")
    full = transpile_full(source)
    assert [item["title"] for item in full["inputs"]] == ["length"]


def test_switch_writes_inventory_each_fixed_sma_length() -> None:
    source = """//@version=6
strategy("finite switch writes")
window = input.int(3, "Window")
selected = switch
    close > open =>
        window := 5
        window
    close < open =>
        window := 7
        window
    =>
        window
value = ta.sma(close, window)
plot(value)
"""
    cpp = transpile(source)
    assert set(_sma_members(cpp)) == {3, 5, 7}


@pytest.mark.parametrize(
    "source",
    [
        (
            '//@version=6\nstrategy("loop counter stays unsupported")\n'
            "float out = 0.0\n"
            "for length = 2 to 4\n"
            "    out += ta.sma(close, length)\n"
            "plot(out)\n"
        ),
        REVIEWER_SOURCE.replace("length := 20", "length := bar_index + 1"),
    ],
    ids=["sma-loop-counter", "sma-bar-arithmetic"],
)
def test_dynamic_sma_lengths_use_series_sma_without_finite_choices(
    source: str,
) -> None:
    cpp = transpile(source)
    assert re.findall(r"^\s*_PFSeriesSma\s+(\w+);$", cpp, re.M)
    assert "pf_ta_length_choice" not in cpp


@pytest.mark.parametrize(
    "source, code",
    [
        (
            REVIEWER_SOURCE.replace("ta.sma", "ta.ema"),
            "PF-E5009",
        ),
    ],
    ids=["ema-series-length"],
)
def test_series_ema_length_stays_refused(
    source: str, code: str,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert caught.value.diagnostics[0].code == code


def test_plain_input_sma_keeps_its_single_constructor_and_reset() -> None:
    source = """//@version=6
strategy("plain input length")
length = input.int(10, "Length")
plot(ta.sma(close, length))
"""
    cpp = transpile(source)
    members = _sma_members(cpp)
    assert set(members) == {10}
    assert "pf_ta_length_choice" not in cpp
    reset = next(
        line for line in cpp.splitlines()
        if f"{members[10]} = ta::SMA(" in line
    )
    assert 'get_input_int("Length", 10)' in reset
