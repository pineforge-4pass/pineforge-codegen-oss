# A user call's history below a lazy edge (lane TAIL-G)

A history read `f(...)[k]` on a user function's call that sits in the lazily
evaluated right operand of `and`/`or`, or in a ternary's arm, reads the call's
value `k` executions of its scope ago: `k` bars at the top level, `k` calls
inside a function. The lazy edge gates only the read; the call runs on every
execution of its scope. So `isNew(s) => inS(s) and not inS(s)[1]` is true on
the first bar of every session.

`tg-lazyhist-btc15` is one `lab tv` export (channel `ws-report-v1`,
`rangeProof` covered), byte for byte: `strategy.pine`, `tv_trades.csv` (times
at UTC+8), `metrics.json`, `meta.json`. `metrics.json` `tvTradesCsvHash` is
the sha256 of `tv_trades.csv`. The probe is synthetic (lane TAIL-G); no closed
or scraped strategy is involved. The command (`--no-note`: no campaign note was
recorded):

```sh
source ~/code/pineforge-workflow/campaign/env.sh
lab tv --pine tg-lazyhist-btc15.pine --symbol BINANCE:BTCUSDT --interval 15 \
  --from 2025-04-07 --to 2025-04-10 --slug tg-lazyhist-btc15 --out <dir>/tg-lazyhist-btc15 --no-note --json
```

| tape | trades | tv_trades.csv sha256 |
|---|---:|---|
| `tg-lazyhist-btc15` | 288 | `cf258e419e30230e1a2c617badeda8cff61d082dea83f5db7fdd336cbfa9d964` |

The probe alternates two positions and closes one on every chart bar; each
exit's comment spells what the script read on the bar before its fill, in
three `|` fields:

- `isNew("0000-0800")` and `isNew("0800-1600")` (`1`/`0`), with
  `isNew(s) => inS(s) and not inS(s)[1]` and
  `inS(s) => not na(time(timeframe.period, s))`;
- inside functions called on every bar, on the `bar_index % 3 == 0` bars
  (`t(x) => x`): `bar_index % 3 == 0 and t(bar_index)[1] == bar_index - 1`,
  the same with `bar_index - 3`, `... and na(t(bar_index)[1])`, and
  `bar_index - (bar_index % 3 == 0 ? t(bar_index)[1] : -1)` (`-` off those
  bars, `n` for na);
- the last two shapes again at the top level.

On all 95 `bar_index % 3 == 0` readings TradingView reads `1001` in functions
and `01` at the top level: `t(bar_index)[1]` is `bar_index - 1`, never na and
never the previous time the operand ran (`bar_index - 3`). `isNew` is true on
the first bar of every session of the range (04-07 08:00, 04-08 00:00, 04-08
08:00, 04-09 00:00, 04-09 08:00 UTC). Before this lane the codegen pushed the
call's history only where the operand ran, reading `0103` and `03` there, and
`isNew` true once in a whole run.

The BTCUSDT feed does not ship with this repository, so
`tests/test_e2e_lazy_call_history.py` checks the tape's own structure and pins
the rule on the corpus's ETH-USDT 15m feed, bar by bar, against the spelled-out
every-bar reads.
