# W2 lowering-trio TradingView trade evidence

Synthetic public probes written for lane W2-CG-LOWERING-TRIO and their
unedited TradingView trade exports. They contain no closed or scraped source.
Each was exported with `lab tv --no-note`; the tapes' `Date and time` column is
the exporting account's chart timezone, Asia/Taipei (UTC+8).

## fixnan over a stateful call

```bash
lab tv --pine fixnan_adx.pine --slug pf-w2-fixnan_adx --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-05-01
```

| File | sha256 |
|---|---|
| `fixnan_adx.pine` | `2451cb3c343058e2de6281c49dd9f361c7fd2419bec94883882de52cb140ab2f` |
| `fixnan_adx_tv_trades.csv` (143 trades, exported 2026-09-26) | `79d381418832bf0d27919a0179a09fd7f23e3d4acad804d491f3c32b3dbb2fee` |

`fixnan_adx` is TradingView's DMI/ADX helper pair (`plus = fixnan(100 *
ta.rma(plusDM, len) / truerange)` inside a user function) plus a top-level
`fixnan` whose stateful argument is `na` on every :30 bar. Every exit comment
spells ADX, +DI and -DI to six decimals, then the held value on the exit bar
and on the bar before. TradingView's run starts at the window, so its first
days are the indicators' warm-up; the replay compares the trades entered from
2025-04-06 on, when the RMA seeds have decayed below 1e-15.

## Argument evaluation counts

```bash
lab tv --pine eval_counts.pine --slug pf-w2-eval_counts --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-03
```

| File | sha256 |
|---|---|
| `eval_counts.pine` | `c964d87c5a86d1f9371b50cfed824f39ffc9a63e6eea4c72253314b2744664d6` |
| `eval_counts_tv_trades.csv` (7 trades, exported 2026-09-27) | `434db5b37ecce89ac1a700b9c88d5c53e35be3a0fff2d39c36bbf93c86eb1809` |

`eval_counts` passes a counting probe (a function adding one to `cnt[k]` per
call) through one argument slot of each builtin whose lowering used to read or
skip it: `nz`'s replacement, `fixnan`, `math.round`'s precision, the
`str.startswith` / `endswith` / `substring` / `replace` / `replace_all` /
`repeat` slots and `array.lastindexof` / `binary_search*` / `join` /
`concat`. Every exit comment spells `cnt[k] - (bar_index + 1)` per slot:
TradingView reads `0` for every slot (each argument once per bar), a negative
count for the right side of a false `and` (22) and an untaken `?:` arm (23),
then `str.repeat("ab", 3, ",")` (`ab,ab,ab`) and the size of the concatenated
array (`6`).
