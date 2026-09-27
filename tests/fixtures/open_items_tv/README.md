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

## An if without else that runs no arm

| File | sha256 |
|---|---|
| `if_tail_na.pine` (slug `pf-oi-if-tail-na2`) | `99b03ab1b8093df6dff07d0b809a7b79abcdf63282e8285a516bea986e3c21c7` |
| `if_tail_na_tv_trades.csv` (7 trades, exported 2026-09-27) | `53e5b8a8dcd07ee359169a6e118e73b36eb3780aac11df5b48be2973405aa6aa` |

Every exit comment spells, for an if without else (an else-if chain without
a final else, a switch without default) whose arms do not run: a function's
float tail, one nested in a taken arm, a switch tail and an else-if tail
(`NaN` each); `na()` of an int and of a string tail (`na`); a bool tail
(`F`); `nz` of a function-local if-value (-7); then whether the bar closed
up, and a global declaration, a global switch and a reassignment whose arm
runs only on an up bar: the close on an up bar, `NaN` on every other bar
(the variable does not keep its previous value). The int tail is read
through `na()` because PineForge passes an int na to a float parameter as
INT_MIN (an open finding of the lane); a first export spelling it through
the float formatter (`pf-oi-if-tail-na`) read `NaN` on TradingView.

## Tuple literals TradingView refuses

These three probes have no tape: `lab tv --no-note` (2026-09-27) failed at
TradingView's compiler, whose error `tests/test_tuple_literal_value.py` pins.

| File | sha256 | TradingView |
|---|---|---|
| `tuple_literal_decl.pine` | `525dcf43a5ba98a680601dc2bddd5d38e47dbdc52426efaf647cd8fb17c00653` | `[a, b] = [close, open]`: "Syntax error at input "["", CE10156, line 5 column 10 |
| `tuple_literal_var.pine` | `1096f167da1256aa6b3ea42772262a7cc537c68e1e972c3254ccb67b57097cc8` | `t = [close, open]`: the same error at line 5 column 5 |
| `tuple_literal_ternary.pine` | `59b28e82ae9258ad2c36432b8717b6e69f38a15d83dedc9d23af14668690cf39` | `[a, b] = close > open ? [close, open] : [open, close]`: "Ternary operations cannot return tuples. Convert the expression into an `if` or `switch` conditional structure to return a tuple.", line 5 |
