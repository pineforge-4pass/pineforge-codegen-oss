# XSYM-A TradingView trade evidence

Synthetic probes written for lane XSYM-A (requests PineForge has no data for,
and `request.security` contexts reached through helpers) and their unedited
TradingView trade exports. They contain no closed or scraped source. Each was
exported on 2026-09-27 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes>-eth15 --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran each from 2025-04-01 00:00 UTC (the export's requested and
returned range, `rangeProof: covered`). The tapes' `Date and time` column is
the exporting account's chart timezone, Asia/Taipei (UTC+8). A probe that
reads values enters on the bars at minute 0 and 30 and closes on the bars at
minute 15 and 45, each close's comment spelling the values it reads on that
bar, so a tape's exit `Signal`s are TradingView's values; sized at 100% of
equity, some entries are refused for margin, which leaves their bars without
a trade.

| Probe | sha256 of the `.pine` | Tape sha256 | Trades |
|---|---|---|---|
| `xa_import_ta` | `0160f73a95bde5ecee656451f8db5eac0e630855508d56487f627d5d7106dcdb` | `6179eb2113fc891932d09fd26eeebdf4e2d4b2ec485ec605ea57620c2c83d707` | 265 |
| `xa_import_ta_plain` | `9b88a881cb79073304137daae3f078b54871896382f810c74569e7f7b3a76cdb` | the `xa_import_ta` tape, byte for byte | 265 |
| `xa_nested_tf_only` | `27f33344047cc3a98e97b3c6b10f16645997c3d15baef7f8c9bac1249606352e` | `074114f03fdcc89f437d39e9da3ad4a89ac6d373ccd3f631b9d94ee7df3611c1` | 265 |
| `xa_nested_tf` | `8e4d22fb09244a1d0d59c2261c79a47db956a12378e5383303b266c10b01a047` | `1d9062c25c02b58e9e6403bff4360ed79bb9be8375b2d4518f52287b3cc4c83e` | 265 |
| `xa_watchlist` | `4c895a319d7d833533e81db47c34003d11e262e6b6b10ca56a6e88985989dc90` | `c0d9e5e69c71f20db434521cf44e65ca4af179c8cf49718692753a8bb8634780` | 22 |
| `xa_watchlist_plain` | `73968ddcaa90b019987a9388b517615779d26cc64573568473ca93de7e06e42a` | the `xa_watchlist` tape, byte for byte | 22 |

What each tape shows:

- `xa_import_ta`, `xa_import_ta_plain`: `import TradingView/ta/7` beside
  built-in `ta.sma`, `ta.highest`, `ta.rsi` and `ta.tr` changes no trade: the
  tape of the script without the import is the same file. Exit Signals:
  `f`, `hi`, `r`, `tr`.
- `xa_nested_tf_only`: `g(tf) => request.security(syminfo.tickerid, tf,
  ta.sma(close, 3))` reached through `h(tf) => g(tf)` and `k(tf) => h(tf)`
  as `h(tfA)`, `h(tfB)`, `k("60")`, `k(tfB)` (inputs 60 and 240): each call
  reads its own timeframe's SMA. Exit Signals: `a`, `b`, `c`, `d`. The same
  probe exported on NASDAQ:AAPL 15 over 2025-04-01..2025-05-01 (tape sha256
  `d5ba8f84b8a1520f1f35e09a760eaa9ec260ee33034b2c9ec3ea62ecfb803dfd`, 267
  trades, the last one still open) is replayed outside this repository, on
  the AAPL lane feed the tests cannot reach.
- `xa_nested_tf`: the symbol and the timeframe through two helper levels,
  `g(sym, tf) => request.security(sym, tf, ta.sma(close, 3))`, `h(sym, tf) =>
  g(sym, tf)`, `k(tf) => h(syminfo.tickerid, tf)`, called as
  `h(syminfo.tickerid, tfA)`, `h(syminfo.tickerid, tfB)`, `k("60")`,
  `k(tfB)` and `h(ticker.heikinashi(syminfo.tickerid), tfA)`: the last reads
  the Heikin-Ashi 60-minute SMA. Exit Signals: `a` ... `e`. The NASDAQ:AAPL
  15 export over 2025-04-01..2025-05-01 (tape sha256
  `0a40b6b98084d0ff1e6ab776f1a1ab007cf2700770701ec3fa275e0f56ee874b`, 267
  trades, the last one open) is replayed outside this repository.
- `xa_watchlist`, `xa_watchlist_plain`: a watchlist of three other symbols
  (`BINANCE:BTCUSDT`, `OANDA:EURUSD`, `TVC:DXY`) through a helper returning a
  bool pair, and a `request.financial`, whose values reach only an `alert()`
  and table cells, beside an SMA-cross strategy; the plain probe is the same
  strategy with the watchlist deleted. The two tapes are one file on
  BINANCE:ETHUSDT.P 15 (22 trades) and one file on NASDAQ:AAPL 15 over
  2025-04-01..2025-05-01 (tape sha256
  `b1cd05f0bc7ee1dec26ca03a1a65fd5dc31ed0d12bb211ca221ca2e206418075`, 20
  trades; kept outside this repository). These probes send no comments: the
  replay compares trade times and prices.
