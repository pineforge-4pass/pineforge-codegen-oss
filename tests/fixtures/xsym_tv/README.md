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

What each tape shows:

- `xa_import_ta`, `xa_import_ta_plain`: `import TradingView/ta/7` beside
  built-in `ta.sma`, `ta.highest`, `ta.rsi` and `ta.tr` changes no trade: the
  tape of the script without the import is the same file. Exit Signals:
  `f`, `hi`, `r`, `tr`.
