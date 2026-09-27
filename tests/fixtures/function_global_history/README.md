# Function-scope history tapes (lane CG-W9-FN)

TradingView's rule for function blocks: "the history of series variables used
inside Pine functions is created through each successive call to the
function". Inside a user function, `x[k]` on a script-scope variable (`var`
or not) or on `bar_index` reads the history of that call site, not the
chart's. The chart built-ins (`open`, `high`, `low`, `close`, `volume`,
`time`, `hl2`, ...) keep the chart's history.

Each directory is one `lab tv` export (channel `ws-report-v1`, `rangeProof`
covered), byte for byte: `strategy.pine`, `tv_trades.csv` (times at UTC+8),
`metrics.json`, `meta.json`. `metrics.json` `tvTradesCsvHash` is the sha256
of `tv_trades.csv`. The probes are synthetic; no closed or scraped strategy is
involved. Every probe computes its values on 2025-04-01 and encodes each as
one later trade: side = sign of the value, quantity = |value| + 1. The
command, run once per tape on BINANCE:ETHUSDT.P 15 (`--no-note`: no campaign
note was recorded):

```sh
source ~/code/pineforge-workflow/campaign/env.sh
lab tv --pine <probe>.pine --symbol BINANCE:ETHUSDT.P --interval 15 \
  --from 2025-04-01 --to 2025-04-03 --out <dir>/<tape> --slug <tape> --no-note --json
```

| tape | lane | trades | tv_trades.csv sha256 |
|---|---|---:|---|
| `w8e-r4-fn-global-history` | W8E-EXITS | 4 | `96be3d3fbf6ed865d0758dea6086e87ba48830a82486ceb69d9f1fe754417666` |
| `w8e-r5-fn-builtin-history` | W8E-EXITS | 6 | `d6ded64b3de4b5d7be78bbc80df8ced2d13844f4258060ea511554ae54ceea16` |
| `w9-fn-history-model` | CG-W9-FN | 7 | `e3ec407cd47c1eba3602e43bb6eed0da1837645b2ba491897e01b845f7ef184c` |
| `w9-fn-history-conditional` | CG-W9-FN | 5 | `878d93629818a0b44f3a92e479e8e989869f190558fbdada32d41f9e27db0d63` |

What TradingView reads (bar index differences; the calls sit 8 bars apart,
at 10:00 and 12:00 UTC, unless stated):

| tape / trade | shape | TradingView | the chart's history would read |
|---|---|---:|---:|
| r4 DV, DN, DB | `fv() => gv[1]` (`var`), `fn() => gn[1]`, `fb() => bar_index[1]` | -8 | -1 |
| r4 DC | `fc() => close[1]` minus the chart's `close[1]` | 0 | 0 |
| r5 DO .. DHL | `open[1]`, `high[1]`, `low[1]`, `volume[1]`, `time[1]`, `hl2[1]` in a function, minus the chart's | 0 | 0 |
| model A | `gv[2]`, one call site called at 10:00, 10:15 and 12:00 | -7 | -2 |
| model B1 / B2 | `gv[1]`, two call sites of one function: 10:00 + 12:00, and 11:00 + 12:00 | -8 / -4 | -1 / -1 |
| model C | `outer() => inner()`, `inner() => gv[1]`; `inner()` is also called on every bar | -8 | -1 |
| model D | `gl[1]` in a 3-iteration loop, `gl := bar_index * 10 + i` before each call; iteration 1 at 12:00 minus `bar_index * 10` | -78 | -78 |
| model E | `gm[1]`, `gm := bar_index` before the call and `bar_index + 1000` after it | -8 | 999 |
| model G | `ge[1]` called on every bar, `ge` set the same way as `gm` | -1 | 999 |
| conditional H11, I11, J11 | calls at 10:00 (`b = false`), 11:00, 12:00; `b ? gv[1] : -1.0`, `if b` then `r := gv[1]`, and a plain `gv[1]`, at 11:00 | -4 | -1 |
| conditional H12, I12 | the same two shapes at 12:00 | -4 | -1 |

So a call site's history holds one slot per chart bar: the value the global
had at that site's latest call at or before the bar (A reads the 10:15 call
two bars back from 12:00, not the call before last), `na` before its first
call, and the last call of a bar in a loop wins (D). The value is the one
the call saw (E, G), also for a function called on every bar (G). Each call
site keeps its own history (B), a nested call included (C). A call whose body
skips the read still records the value (H11, I11 read the 10:00 call, not
`na`). This is the same clock as a history-reading user-function parameter
(codegen #109).

`tests/test_e2e_function_global_history.py` replays each tape end to end --
`transpile_json`, the built runtime, `run_strategy.py` over the corpus 15m
feed in the tape's own window, with TradingView's BINANCE:ETHUSDT.P lot of
0.0001 as the `qty_step` -- and requires every trade (entry and exit time,
side, price, quantity) to be the tape's. The pre-lane build (`LEGACY`,
`cg/tvdefaults` at `d7e095f`) read the chart's history and misses the r4,
model and conditional tapes; the r5 built-ins tape passes on both builds.
