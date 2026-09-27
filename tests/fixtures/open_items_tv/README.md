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

## TradingView's keyword names

| File | sha256 |
|---|---|
| `kw_names.pine` | `60328ed80923922e52d90a3d65dfef122f2046821e630ceda9c395713526d96a` |
| `kw_names_tv_trades.csv` (7 trades, exported 2026-09-27) | `3c784cada0579524f811b84e1bfa061e4cf5a2c43bee92b459b53d9fdf6e31a7` |
| `kw_order.pine` (slug `pf-oi-kw-order2`) | `82ea1fbaa0a7d37efbac7b839ee89e7871dd0b2a61f4f009efdd5a579b02be35` |
| `kw_order_tv_trades.csv` (7 trades, exported 2026-09-27) | `14aa2486d7a17ef48af5b8453fd25a6736f7cf8d01e87ad19cc25b640d41a1f3` |

`kw_names` calls `nz(x, replacement = -1.0)`, `nz(source = x)`, `nz(source =
x, replacement = -2.0)`, `nz(replacement = -3.0, source = x)`, `fixnan(source
= x)` and `fixnan(x)` with `x` na on every up bar, and `str.repeat(source =
"ab", repeat = 3, separator = ",")`, `str.repeat("ab", repeat = 2)` and
`str.repeat("xy", 2, separator = "-")`; each exit comment spells them. On an
up bar TradingView reads -1, 0, -2, -3 and the last non-na close twice; the
strings are `ab,ab,ab`, `abab` and `xy-xy`. `kw_order` has nz's arguments,
written in either order, push 1 (source) and 2 (replacement) to an array:
TradingView reads 12 both ways, parameter order. (A first export,
`pf-oi-kw-order`, logged tags into strings; its replay tripped two other
PineForge defects, a `var` string array's `array.join` typed as a number and
a variable named `second` read as the built-in.) Two probes spelling
TradingView's rejected names failed at its compiler, "The ... function does
not have an argument with the name ...": `nz(x = x, y = -1.0)` /
`fixnan(x = x)` and `str.repeat("ab", count = 2)`.

## Names spelled like the emitter's temporaries

| File | sha256 |
|---|---|
| `temp_names.pine` | `2e6ab484f2db25802ffee6743de2ebf0e2aaabb12f40615295ce2c08822845e3` |
| `temp_names_tv_trades.csv` (7 trades, exported 2026-09-27) | `c0e0c856acc4b1b48c2e50d9ebf07e95b8af522abc6316cadd487f4e8789eac2` |
| `temp_names_array.pine` (slug `pf-oi-temp-names-array2`) | `de4e47b11ebcf251f8ca2a500fa7a7763d5f230bf8801bba90c018d6414d529c` |
| `temp_names_array_tv_trades.csv` (7 trades, exported 2026-09-27) | `9a59789b95bdf2cf784e0a40e7d6b6a42fcb7d022e49ad10c0b83129e3665ad5` |

`temp_names` declares variables spelled like the C++ temporaries PineForge
generated around nz (`_nz_v`, `_nz_y`), fixnan (`_fixnan_v`), a comparison
(`_pna_l`, `_pna_r`), a history read (`_hv`), math.max / min (`_v0`, `_v1`)
and timestamp (`_yr`, `_min`), and like the locals of its str.* templates
(`s`, `r`, `i`, `p`, `t`), and reads each inside that call; the exit comment
spells the results (`nz(x, _nz_v)` is twice the close on a bar whose `x` is
na, `str.upper(s)` is `UP`/`DN`, `str.repeat("x", i)` is `xxx`/`xx`, ...).
`temp_names_array` does the same with arrays named like the array.* template
locals (`c`, `m`, `it`, `n`, `b`, `idx`) passed to median, mode, indexof,
stdev, variance, the percentiles, covariance, binary_search, sort_indices and
standardize. A first export of it (`pf-oi-temp-names-array`, identical
signals) bound `array.standardize(m)` to an untyped variable, which PineForge
declared as a number (lane item 6); this one reads it through `array.get`.

## Collection results bound to untyped variables

| File | sha256 |
|---|---|
| `untyped_collections.pine` | `d8dbb2eb750162bf327bbf2f2444b874896ff9a94ced187f49bcdc7967ef4580` |
| `untyped_collections_tv_trades.csv` (7 trades, exported 2026-09-27) | `69e2763551f8cf51e0bdc951a25c08aee421bdc632022de1665d9e91380b6127` |

Untyped variables bound to `matrix.row(m, 0)`, `m.row(1)`, `matrix.col(m,
1)`, `matrix.eigenvalues(m)` and to `array.standardize` / `abs` /
`sort_indices` in both forms; each exit comment spells a read of each (a
sum, max, min or element) and the bar's `close % 7`.

## A function and another function's local series sharing a name

| File | sha256 |
|---|---|
| `tails_b.pine` (lane W2's `pf-w2-f04_tails_b`) | `0f53dc88d7b4396f0e44697e725663dd9699c2f2e311d65ab51b63d7dbeac911` |
| `tails_b_tv_trades.csv` (7 trades, exported 2026-09-26 by lane W2) | `16f790439beb79a1cf4e016a0b7a68578b61896aca44fccfb164554a0e275863` |
| `func_local_name.pine` | `bd51e5234829be1cca10c50bf24220014eeabff232cfd6b919f7565aec564805` |
| `func_local_name_tv_trades.csv` (7 trades, exported 2026-09-27) | `188ad424004ec5f9d4c8d4d3b4e85f32676c00e1721aff9b6bcd5e88cadb2e1d` |

`tails_b` is lane W2's function-tail probe, unedited: `fHist` keeps a float
series `f` and a string function `f(x)` is defined after it (W2 replayed a
variant without the collision because this one did not compile).
`func_local_name` keeps a float series `g` beside a string function `g`, a
float series `k` beside an int function `k`, and an int series `m` beside a
float function `m` defined after it; each exit comment spells what the three
functions keeping them returned, `k(close)`, `g(m(close))` and the close.

## color.from_gradient's arguments

| File | sha256 |
|---|---|
| `from_gradient_args.pine` | `184cfe38626841b1e2b2472269315db8153f6c341b81123fb5ca1e1b51a0f063` |
| `from_gradient_args_tv_trades.csv` (7 trades, exported 2026-09-27) | `8f5b4d06db8755f85648f53adf549be9d81e007082e1d7295110e12a9dadf612` |

Counting probes sit in color.from_gradient's value, bottom and top slots, by
position and by keyword, and a helper keeping a series (`s := nz(s[1]) +
v`, stored into an array) is its value. Each exit comment spells every
probe's count minus `bar_index + 1` and the stored series minus `bar_index
+ 1`: 0 each on TradingView, which evaluates every argument once per bar
although the colour only tints the chart.
