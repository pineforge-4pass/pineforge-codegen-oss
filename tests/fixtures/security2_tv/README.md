# CG-SECURITY-2 TradingView trade evidence

Synthetic probes written for lane CG-SECURITY-2 (`request.security` helper
shapes) and their unedited TradingView trade exports. They contain no closed
or scraped source. Each was exported on 2026-09-26 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran each from 2025-04-01 00:00 UTC (the export's requested and
returned range, `rangeProof: covered`). Every probe enters on the bars at
minute 0 and 30 and closes on the bars at minute 15 and 45; each close's
comment spells the values the probe reads on that bar, so a tape's exit
`Signal`s are TradingView's values, one sample per close. TradingView books
265 of the closes: sized at 100% of equity, some entries are refused for
margin, which leaves their bars without a trade. The tapes' `Date and time`
column is the exporting account's chart timezone, Asia/Taipei (UTC+8).

| Probe | sha256 of the `.pine` | Tape (265 trades) sha256 |
|---|---|---|
| `sec2_var_ctx` | `c89451fa8774a9e0d70ae9f1e7ce238271c0886be49d2ea52b453ba1e4ddbef6` | `1b04d1a2fb368dc0f23175b12596d8b6d868a4f559799a95d9cf312b4916fe12` |
| `sec2_mixed_tuple` | `20dd8524329e9dfa784f04287f7f2a6003efd3c44bcc7fc9a460827e1a4e2f78` | `51786867a273b3e14cdf3337d6da93923df03a8efe147d26958121abe9800077` |
| `sec2_tuple_string` | `8d8ce2c6043b2ba50301df21de60f081ddcfd95df803df1378cb664ac4625495` | `f9c5ed763fa25d8d1fb7092a6a94e72902fb2c5eee154a73e67c5f8bdb97b826` |
| `sec2_tuple_assign` | `20b360748e333ed6e1c78a95008a2264e4dac323263544f5f05e28d765fa6217` | `85d6afd374a2f2f51af6af729d8b87450ce6a52efdf2822106f2ff130ccc99be` |
| `sec2_var_string` | `236e64805a76671a40836077562b43b9d59edc900568cb10c32e417ff6974d6a` | `4d3a373f9d2d492db9d51885da1b7fe8e2ca403fd061163308f7d9e5f1994715` |
| `sec2_ta_len_choice` | `fd4db49b3f9aa2386bcefc528da3031ce900ebdb79436321d92327111ced09d9` | `91872c1ba519cae55e5ea1a2886cf1a270a4bd8bab1ed816fc8410eefaf74e02` |
| `sec2_hist_index` | `de8540e6e5880cb725822f732cdcd3885a0c95fc4bf1ba607f9b84c0eaa2a790` | `5c7007b2c1fb2aa834cacaec0d71574a88e1fdcdf5592556b0fdf9964f29f1b2` |
| `sec2_hist_index_55` | `06511bf654d553a0c928a7d049fde42002b62857ff0d8865fab8e6e55d876711` | the `sec2_hist_index` tape, byte for byte |
| `sec2_payload_udf` | `9996faf846c972f92672e54d135f34f3319adf7e194f114854eb7313d909626e` | `609366cc79bbe55c32372d49e789fb6a3a0e252b38ed5126cc646bef1c3e599b` |

Each exit Signal joins these fields with `|`, in this order:

- `sec2_var_ctx`: `c60`, `c240`, `c60on`, `cChart`, `u60`, `u240`, `u60on1`, `uChart`
- `sec2_mixed_tuple`: `b1`, `r1`, `t1`, `w1`, `b2`, `r2`, `t2`, `w2`
- `sec2_tuple_string`: `d1`, `x1`, `b1`, `d2`, `x2`, `b2`, `sg`
- `sec2_tuple_assign`: `bMid`, `bW`, `bR`, `lastU`, `cMid`, `cW`, `cR`
- `sec2_var_string`: `s60`, `s240`, `sChart`
- `sec2_ta_len_choice`: `s60`, `sChart`
- `sec2_hist_index`, `sec2_hist_index_55`: `n`, `e15`, `e60`
- `sec2_payload_udf`: `a1` ... `a6`

What each tape shows:

- `sec2_var_ctx`: a helper's `var` state is kept per call site and advances
  once per requested bar. Three calls of one counter helper (60, 240, 60 with
  `lookahead_on`) and its chart call each count their own bars from 00:00
  UTC; a helper latching the last up-candle close reads the requested bars'
  candles, also at `[1]` under `lookahead_on`. On the last chart bar of a
  requested bar, `lookahead_off` already reads that bar.
- `sec2_mixed_tuple`, `sec2_tuple_string`: a helper tuple mixes bool, float,
  int and string elements. Under `gaps_on`, a chart bar that completes no
  requested bar reads `false` for a bool element, `na` for a numeric one and
  an empty (`na`) string; so does a string payload.
- `sec2_tuple_assign`: tuple declarations in a helper -- a `ta.bb` result, a
  nested helper's tuple, `_` placeholders -- and a helper read at `[1]` with
  `lookahead_on`, beside the same helper on the chart.
- `sec2_var_string`: a `var string` state machine in a helper, its `if`
  conditions guarding `ta.crossover` / `ta.crossunder` behind a lazy `and`;
  before the first 4-hour bar completes the string reads empty.
- `sec2_ta_len_choice`: an SMA length chosen by comparing an `input.string`
  with its options, passed through a helper into `request.security`.
- `sec2_hist_index`, `sec2_hist_index_55`: `ta.sma(...)[n]` in a payload with
  `n = math.min(290, math.max(1, math.round(nMin / 15)))` from an
  `input.int`. With `nMin` 55 TradingView's `n` is 4 (`55 / 15` is a float,
  3.67, rounded), the tape of `nMin` 60 byte for byte.
- `sec2_payload_udf`: user functions, a user method and a TA call under
  builtin calls in a payload (`nz(f())`, `close.g()`, `nz(ta.sma(close, 3))`,
  `k(close)` reading `nz(src[1])`): every value is the requested bar's.
