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
(two places for hours, five for the counter and state tapes).

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

With `pytest -s`, each native case prints engine/tape/common/equal/different
and unmatched counts, plus its `join.tsv` path and SHA256. The per-source
join log and `join.sha256` stay beside the compiled strategy in pytest's
temporary directory for the run receipt.
