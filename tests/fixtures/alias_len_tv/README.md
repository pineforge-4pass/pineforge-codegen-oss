# TAIL-H TradingView trade evidence: TA lengths read through an alias

Synthetic probes written for lane TAIL-H and their unedited TradingView trade
exports. They contain no closed or scraped source. Each was exported on
2026-09-29 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran each from 2025-04-01 00:00 UTC (the export's requested and
returned range, `rangeProof: covered`). Every probe trades a fixed quantity
of 1, enters on the bars at minute 0 and 30 and closes on the bars at minute
15 and 45; each close's comment spells the values the probe reads on that
bar, so a tape's exit `Signal`s are TradingView's values, one sample per
close (336 trades each). The tapes' `Date and time` column is the exporting
account's chart timezone, Asia/Taipei (UTC+8).

| Probe | sha256 of the `.pine` | Tape sha256 |
|---|---|---|
| `cgt_alias_len` | `90734d1ea443ce6a22eab6430bdaf00287b952218144bcb7a388dca56762d45a` | `a116da443bf30164d1adb901bea6544abb3172670141e902a5616a3694c525d3` |
| `cgt_alias_len_b6` | `688577f7fd5235eb356834b088cceb4a307d9683184232eda17449ff9652c142` | `ee1d9e16d696b18278404f5a560c4fdf669b224327bc7b0788c2bbb5ff5fe131` |

Each exit Signal joins these fields with `|`, in this order: `e`, `e[1]`,
`s`, `r`, `h`, `up` (`u` or `d`) and `len2`.

What the tapes show: `len = prod` over `prod = a * b` (inputs `A` = 3, `B` =
4) and `len2 = len` hold 12 from the first bar, and `ta.ema(close, len)`,
`ta.sma(close, len2)`, `ta.rsi(close, len)` and `ta.highest(high, len2)` are
the indicators of that length: all na until the 12th bar (the EMA seeded
there with the SMA of its first 12 closes, TradingView's first-bar warmup),
the RSI from the 13th. `cgt_alias_len_b6` is the same script with `B` at 6
(a length of 18).
