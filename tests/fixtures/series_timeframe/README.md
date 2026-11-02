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
selection. They do not execute the runtime or establish runtime value
agreement. The state measurements cover these two expressions and two
timeframes; longer TA windows, persistent `var`/`varip` state and other
symbols were not measured. Persistent requested state and other symbols
keep a located refusal.
