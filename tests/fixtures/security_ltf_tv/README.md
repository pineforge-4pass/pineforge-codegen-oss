# request.security_lower_tf computed timeframe: TradingView trade evidence

A synthetic probe written for lane CG-W9-SEC and its unedited TradingView
trade export. It contains no closed or scraped source. It was exported on
2026-09-28 with

```bash
lab tv --pine w9sec_ltf_computed_tf.pine --slug pf-w9sec-ltf-computed-tf --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran it from 2025-04-01 00:00 UTC (`rangeProof: covered`). The
probe enters on the bars at minute 0 and 30 and closes on the bars at minute
15 and 45; each close's comment spells the values it reads on that bar
(265 trades). The `Date and time` column is Asia/Taipei (UTC+8).

| Probe | sha256 of the `.pine` | Tape sha256 |
|---|---|---|
| `w9sec_ltf_computed_tf` | `647cbe4919821bcbb2e3ea590d6a71ae1a2b674b02d044a10da7641a3bc3768f` | `b199cd719d5829aab3125b1ebd1416ce1409982db98e7ab35d9630f8c6677d5d` |

Each exit Signal joins `ltfDecl`, `array.size(a)`, `ltf`, `array.size(b)`
and `b`'s first element with `|`.

What the tape shows: on a 15-minute chart both timeframes are "5" -- one a
declaration expression over `timeframe.in_seconds()`, the other reached
through `:=` reassignments (one inside an input-guarded `if`) -- and each
request returns the chart bar's three 5-minute intrabars, on all 265
closes. TradingView computes a simple timeframe on the first bar,
reassignments included.
