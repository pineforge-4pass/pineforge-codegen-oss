# CG-OPEN-ITEMS TradingView trade evidence

Synthetic public probes written for lane CG-OPEN-ITEMS and their unedited
TradingView trade exports. They contain no closed or scraped source. Each was
exported with

```bash
lab tv --pine <name>.pine --slug pf-oi-<name> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-03
```

(range proof `covered`); the tapes' `Date and time` column is the exporting
account's chart timezone, Asia/Taipei (UTC+8). Every probe enters a long at
00:00, 06:00, 12:00 and 18:00 UTC and closes it a bar later with an exit
comment spelling the values under test (7 trades each).
`tests/_tv_tapes.py` replays them.

## A history read at a fractional index

| File | sha256 |
|---|---|
| `dyn_hist_index.pine` | `508a330f0d12fcaa1c3035a1b1ccb1967d0aa58a6e43f2f46d5c1970171030ea` |
| `dyn_hist_index_tv_trades.csv` (7 trades, exported 2026-09-27) | `7222c6727736ad6164db38e5e13190e6b245b914ad372b15dae25442c7ea5595` |

`lag = (45 - 1) / (2 * 4)` is 5.5 and `29 / 10` is 2.9 (Pine v6 divides ints
as floats). Each exit comment spells a read at the fractional index, then the
reads at the integers on either side: `ta.tr(true)[lag]`,
`ta.highest(high, 3)[2.9]`, `(close - open)[lag]`, a user call's `f(close)[2.9]`
and `ta.sma(close, 3)[lag]` under a lazy `and` and in a ternary arm.
TradingView truncates: every fractional read equals the read at the lower
integer.
