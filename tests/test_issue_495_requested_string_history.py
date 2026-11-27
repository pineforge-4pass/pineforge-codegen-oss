"""#495: a string global's history inside a helper uses requested bars."""

import pytest

from pineforge_codegen import transpile
from tests._e2e import reference_codegen, transpile_json
from tests.test_security_helper_arg_history import _run_requested


_BASE = "11568b99a3272ef1f40ab94cf7debf74f24f4eec"
_ISSUE = """//@version=6
strategy("string history in a requested helper")
string txt = str.tostring(close)
h() =>
    bb = txt[1]
    str.length(bb)
plot(request.security(syminfo.tickerid, "60", h()))
"""
_HOUR = 3_600_000
_CLOSES = (7, 42, 316, 2024, 5)


def _requested_lengths(source, *, provisional=False):
    # Even before the last child updates the chart's own series, chart[1]
    # still sees a six-digit partial close rather than the prior hour's close.
    feed = tuple((800000, 700000, 600000, final) for final in _CLOSES)
    rows = _run_requested(transpile(source), {"chart15": (15, feed)})["chart15"]
    completed = [row for row in rows if row.complete]
    actual = [(row.timestamp, row.close) for row in completed]
    expected = [(i * _HOUR, close) for i, close in enumerate(_CLOSES)]
    assert actual == expected, f"NOT COMPARED: completed bars {actual}, expected {expected}"
    if provisional:
        partial = [row.close for row in rows if row.timestamp == 2 * _HOUR and not row.complete]
        assert any(close != 316 for close in partial), (
            f"NOT COMPARED: expected a provisional read before the skipped close 316, got {partial}"
        )
    return [row.value for row in completed]


@pytest.mark.parametrize("name", [
    "txt", "open", "close", "high", "low", "volume", "hl2", "hlc3", "hlcc4", "ohlc4",
])
def test_string_global_reads_previous_requested_bar(name):
    # No TradingView reading or stored fixture for #495 (no capture date).
    # Inference: missing string history is string na, represented by the
    # empty string, length 0. The related sec2_var_string tape (2026-09-26)
    # shows an empty held request result; it does not measure this history read.
    # Integral closes render without a decimal part under str.tostring.
    expected = [0, *(len(str(close)) for close in _CLOSES[:-1])]
    assert _requested_lengths(_ISSUE.replace("txt", name)) == expected


def test_skipped_string_history_read_still_counts_requested_bars():
    # Two distinct boundaries: the read sits directly inside a builtin,
    # and the global still has a value on each bar where no read occurs.
    source = """//@version=6
strategy("skipped string history read")
txt = close == 7 ? na : str.tostring(close)
h() => close == 316 ? -1 : str.length(txt[1])
plot(request.security(syminfo.tickerid, "60", h(), lookahead=barmerge.lookahead_on))
"""
    # At 42 the previous string was na. At 2024 it was "316", even though
    # the completed bar skipped the read. No TradingView capture for this row.
    assert _requested_lengths(source, provisional=True) == [0, 0, -1, 3, 4]


def test_numeric_history_helper_keeps_base_cpp(tmp_path):
    source = _ISSUE.replace("string txt = str.tostring(close)", "float txt = high - low")
    source = source.replace("str.length(bb)", "bb + 1")
    reference = reference_codegen(_BASE)
    assert reference is not None, f"NOT COMPARED: base {_BASE} unavailable; head has source"
    path = tmp_path / "numeric.pine"
    path.write_text(source, encoding="utf-8")
    base = transpile_json(path, root=reference)
    assert base["ok"] and base["cpp"], f"NOT COMPARED: expected base C++, got {base}"
    ring = "_sec0_expr_hist_0"
    for use in (f"Series<double> {ring}", f"{ring}[_hidx - 1]", f"{ring}.push("):
        assert use in base["cpp"], f"NOT COMPARED: expected base numeric history {use!r}, found none"
    current = transpile(source)
    assert current, f"NOT COMPARED: head C++ is empty; base produced {len(base['cpp'])} chars"
    assert current.encode("utf-8") == base["cpp"].encode("utf-8")
