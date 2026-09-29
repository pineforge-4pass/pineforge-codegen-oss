# TAIL-A TradingView trade evidence

Synthetic probes written for lane TAIL-A (a `request.security` payload that
reads the history of a global bound to a multi-statement user function,
reached through two helpers' parameters) and their unedited TradingView trade
exports. They contain no closed or scraped source. Each was exported on
2026-09-28 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes>-eth15 --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran each from 2025-04-01 00:00 UTC to 2025-04-08 00:00 UTC (the
export's requested and returned range, `rangeProof: covered`). The tapes'
`Date and time` column is the exporting account's chart timezone,
Asia/Taipei (UTC+8). Each probe opens a position on every bar and closes the
previous one with process_orders_on_close, so every chart bar but the first
books an exit whose `Signal` spells what the requests read on that bar:

    a|b|c|t0|t1

- `a`: `pick(cs, true, bucket)`, with `pick(_x, _on, _tf) => _on ?
  held(syminfo.tickerid, _tf, _x) : _x` and `held(_sym, _tf, _x) =>
  request.security(_sym, _tf, _x[1], gaps_off, lookahead_on)`;
- `b`: the same of `os`;
- `c`: `request.security(syminfo.tickerid, bucket, cs[1], gaps_off,
  lookahead_on)`, the payload written at the top level;
- `t0`, `t1`: the requested bar's `time` and `time_close`, in epoch days.

`cs` and `os` are `smoother(kind, cSrc[lag], len, sigma, offs)` over the
close and the open, a multi-statement helper computing a triple EMA, a Hull
average (its lengths computed from `_len`) and an ALMA and selecting one;
`cSrc = useHeikin ? request.security(ticker.heikinashi(syminfo.tickerid),
timeframe.period, close, lookahead_off) : close`. The bucket on this chart is
"120" (the timeframe multiplier times the `Mult` input, 8).

| Probe | Defaults | sha256 of the `.pine` | Tape sha256 | Trades |
|---|---|---|---|---|
| `taila_nested_bucket` | Kind ALMA, Lag 0 | `5c6b42f431597158b9ab3fefb5476d04db9d11f82dca80155adcf6e2886edca2` | `d6d012659353fefe16c117d0d711ccddd104b30e42b93295e50734000b8d998e` | 673 |
| `taila_nested_bucket_hull_lag` | Kind HULL, Lag 1 | `34c724c205fb55e38e336c1cfee0eb69993e48ba1fbd91c49e1a4af4d0d5fea8` | `0764f320249155041334ea29e94f7db9675c4a838b074d3828dab4f66733dffc` | 673 |

The second probe is the first with other defaults; its tape is what the first
reads under the overrides `Kind=HULL`, `Lag=1`. There `cSrc[1]` is inlined
at every read of the helper's `_src` (the EMA, the two inner `ta.wma` and the
ALMA): its history is pushed once per completed requested bar.
