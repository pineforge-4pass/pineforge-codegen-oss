These Pine v6 strategies use TradingView exports on BTCUSDT one-hour bars
from January 1 through March 1, 2024. Strategy titles and explanatory
comments have descriptive names; executable expressions are preserved.

`tv_bar_quantities.tsv` records the requested-bar-counter strategies,
and `tv_fixed_bar_quantities.tsv` records their fixed-timeframe controls.
The explicit-price and explicit-parity sources request both timeframes and
select their values on the chart. The price if and ternary exports agree.

`tv_hour_entries.tsv` records the three selected-hour sources and their two
fixed controls. `tv_state_entries.tsv` records quantities for the selected
three-bar SMA and one-bar close history, together with each fixed arm's
quantity on the same entry. The arm column uses the signal bar's parity,
not the next bar at which the entry fills. The first entry is retained,
including its warm-up value.

`history-fixed-60.pine`, `history-fixed-240.pine`, `sma-fixed-60.pine` and
`sma-fixed-240.pine` preserve their corresponding parity source's declaration,
quantity expression and entry/close schedule. Only the requested timeframe
becomes a literal and the title gains a suffix naming the fixed arm. The
unused timeframe declaration is retained so every other line stays identical.

The structural tests check independent evaluator storage and chart-side
selection. `tests/test_e2e_series_timeframe_values.py` checks native entry
quantities against the committed tapes when the engine and BTCUSDT one-hour
feed are present. Tape-versus-tape tests are fixture self-consistency checks.
Native differential rows in `tests/test_e2e_timeframe_values.py` add
generated-C++ logs, requested-value traces and literal trade controls for
scalar operators, casts, formatting, defaults and let chains; they skip when
the paired native engine inputs are missing, and they establish
interpreter/C++ agreement only after they run. The state measurements cover
these two expressions and two timeframes; longer TA windows, persistent
`var`/`varip` state and other symbols were not measured. Persistent requested
state keeps a located refusal; other-symbol series requests retain the
existing missing-data lowering.

The native module takes the hourly OHLCV CSV from
`PINEFORGE_BTCUSDT_1H_FEED` and uses the usual `PINEFORGE_ENGINE_INCLUDE`,
`PINEFORGE_ENGINE_LIB`, `PINEFORGE_EIGEN_INCLUDE` and generated-header setup.
It selects the inclusive UTC window January 1 00:00 through March 1 00:00,
2024, and requires every hourly bar. Missing native inputs produce named
skips; an incomplete or malformed feed fails. No warm-up entries are excluded.
Quantities are compared at the tape column's maximum fractional precision
(two places for hours, five for the counter and state tapes), using
`ROUND_HALF_UP` for native values. Equality at that precision is exact with
no tolerance: a change of one unit in the last exported digit is different.

There are two known pre-existing engine differences, measured on main and
every tested revision. For `explicit-parity` and `if-parity`, the engine
omits the second scheduled fill, January 1, 2024 at 18:00 UTC
(1704132000000 ms). Each has 204 engine entries and 205 tape entries; the
other 204 quantities are exactly equal and there are no engine-only entries.
`KNOWN_TAPE_ONLY` requires exactly this cell and logs it as `known-tape-only`.
Any other missing, extra or different entry fails. Restoring the missing
entry also fails with an instruction to verify the fix and empty the table.
This pin MUST be turned into plain equality by emptying `KNOWN_TAPE_ONLY`
when the root fix lands.

For `history-parity` and `sma-parity`, the engine's quantity computation
differs from TradingView's in the last digit. Their tape joins are reports:
the selected source is compared with `selected`, and the two fixed sources
with `fixed_60` and `fixed_240`, using the same half-up column precision.
Each report prints and records exact/different counts, without treating
different quantities as equal. All three sources must still have exactly
the tape's 477 entry times, with no engine-only or tape-only entries.
These reports MUST be turned into exact equality assertions when the
quantity computation's root fix lands. Counts of these quantity differences
are left to the native run to measure.

`test_native_selected_matches_engine_fixed_arm[history|sma]` verifies these
two lowered sources against the engine's own fixed-arm runs. Every raw
engine `Qty` is parsed as a Decimal and compared exactly, without rounding,
with the fixed source selected by the authored schedule. All three entry-time
sets must be identical. The tape's arm and signal bar index are checked
against entry time: the entry bar index is the number of hours since
January 1 00:00 UTC, the signal bar index is one less, and odd signal bars
select `"240"` while even signal bars select `"60"`. Selection never comes
from engine quantities. Each failing entry lists both raw quantities.

The engine-free `test_state_tape_selected_equals_authored_fixed_arm` rows
check exact string equality between every tape `selected` cell and its
chosen `fixed_60` or `fixed_240` cell, and check that same authored schedule.
`test_fixed_arm_fixture_preserves_source` checks that the four control
fixtures differ only by the literal timeframe and title suffix. Comparator
self-checks require a last-digit change to be reported different for every
source, reject raw fixed-arm changes smaller than the tape precision, reject
wrong-arm selection at one entry or all entries, reject missing/extra
entries, and require the known missing cell exhaustively.

The compact counter tapes contain quantity lists rather than timestamps.
Their labels are reconstructed from the fixtures' entry schedule: the first
fill is January 1 11:00 UTC (19:00 UTC+8), then every seven hours, ending
February 29 23:00 UTC, for 205 entries. This calendar is authored independently
of native output; quantities come only from the committed export.
`explicit-parity` uses the `if-parity` export and `explicit-price` uses
`if-price`. Hour and state tapes carry their own UTC+8 labels, converted to
UTC by subtracting eight hours. All native joins use entry times.
The fixed-counter tape is a fixture control; there are no corresponding
fixed-counter Pine sources in this directory.

With `pytest -s`, each tape join prints engine/tape/common/exact/different
and unmatched counts, plus its `join.tsv` path and SHA256. Each fixed-arm
comparison records raw selected and both fixed quantities, the authored arm,
and per-source entries/equal/different counts. Fixed-source equality counts
cover entries selecting that arm, labelled `selected-arm-entries`; the full
477-entry fixed-source tape reports are separate. Every join has a
`join.sha256` beside it in pytest's temporary directory. Compiled strategies
and runner stderr are retained in per-source temporary directories; the
same native runs are reused by the tape reports and fixed-arm assertions.
