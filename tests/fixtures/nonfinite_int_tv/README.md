# TAIL-C TradingView trade evidence: a Pine int holding an infinity

A public probe written for R5 lane TAIL-C and its unedited TradingView trade export. It contains
no closed or scraped source. Exported on 2026-09-29 with `lab tv --no-note` on
`BINANCE:ETHUSDT.P`, 15-minute chart; `rangeProof` `covered`. The tape's `Date and time` column
is the exporting account's chart timezone, Asia/Taipei (UTC+8).

```bash
lab tv --pine int_inf.pine --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-04
```

| File | sha256 |
|---|---|
| `int_inf.pine` | `2ff7116658ee3b6740c6fe0cd96ddf1a457270692a54c475b0dae997c657e1a8` |
| `int_inf_tv_trades.csv` (67 trades) | `30bae365cf898bc0dcf14300f5898731b2c0eadb7ebc257fb0df1f5e7b7c013f` |

One leg per UTC hour (`hour % 9`), entered at the hh:00 bar's close under
`process_orders_on_close`, closed by `strategy.close_all()` at the hh:15 bar's close with a
comment that reads out `int iInf = math.floor(100 / z)` and `int iNaF = math.floor(z / z)`
(`z = close - close`, a series zero): comparisons against -5, 0 and 5, `str.tostring`, `na()`
and `nz(v, -1)`. Every exit reads `i=11110000,NaN,true,-1|n=0000,NaN,true,-1`: the infinity
orders above every number (`> -5`, `> 0`, `> 5`, `>= 5` hold; `< 5`, `<= 5` do not), while
`==` and `!=` are both false and `str.tostring`, `na()` and `nz()` treat it as na; `floor(NaN)`
compares false throughout. Every leg -- a 0.02 control, a float +Infinity, the int
infinity, the int infinity entered only `if iInf > 0`, na, an int declared na, `floor(NaN)`
and a float -Infinity -- enters: the non-finite ones at the default 0.01.
`tests/test_e2e_nonfinite_int.py` replays the tape on the corpus 15m feed over TradingView's
range; the engine half (an infinite `strategy.entry` quantity trades the default quantity) is
pinned in pineforge-engine's `tests/fixtures/nonfinite_entry_qty`. On a060268 the replay
misses every IINF, GINF and INAF trade and reads `i=00000000` on every exit.
