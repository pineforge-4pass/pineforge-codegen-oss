"""The static EMA note explains the reference origin run option."""

from pineforge_codegen import transpile_full
from pineforge_codegen.errors import Level


_MESSAGE = "ta.ema seeds from the first value of the data PineForge runs, so it can differ from TradingView at the start of a run whose reference started where your range starts."
_HINT = "The first-value seed matches TradingView when its chart had history before your range, and the difference has decayed by then. If your TradingView reference starts at your range start, declare it: set series_origin_ms to the open time of its first bar (a run option, passed through syminfo). The indicator is then na for the first length-1 bars and starts from the SMA of the first length bars, as TradingView does. The same holds for ta.macd and ta.tsi; ta.kc and ta.kcw already start that way in every run. Declaring it also starts request.security series at the first whole higher-timeframe bucket at or after that time, and a plain security expression stays na until the first higher-timeframe bar completes."


def test_ema_note_names_both_seeds_and_the_reference_origin() -> None:
    source = '//@version=6\nstrategy("EMA note")\nx = ta.ema(close, 14)\n'
    diagnostics = transpile_full(source)["diagnostics"]
    notes = [d for d in diagnostics if d.code == "PF-W1505"]
    assert len(notes) == 1
    note = notes[0]
    assert note.level == Level.NOTE
    assert note.message == _MESSAGE
    assert note.hint == _HINT
    assert "first value" in note.message and "TradingView" in note.message
    for text in ("series_origin_ms", "SMA", "ta.macd", "ta.tsi", "request.security"):
        assert text in note.hint
    # ta.kc and ta.kcw already start na and seed from the SMA in every run (their
    # generated shim applies the warm-up to the EMA basis), so the note names
    # them apart from the functions the origin changes.
    assert "ta.kc and ta.kcw already start that way in every run" in note.hint
    assert "ta.macd, ta.kc" not in note.hint and "ta.kc and ta.tsi" not in note.hint
    assert "{" not in note.message and "}" not in note.message
    assert "{" not in note.hint and "}" not in note.hint


def test_rsi_does_not_emit_the_ema_note() -> None:
    source = '//@version=6\nstrategy("RSI note")\nx = ta.rsi(close, 14)\n'
    diagnostics = transpile_full(source)["diagnostics"]
    assert not any(d.code == "PF-W1505" for d in diagnostics)
