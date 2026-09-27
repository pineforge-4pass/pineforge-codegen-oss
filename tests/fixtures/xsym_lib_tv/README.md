# XSYM-C TradingView trade evidence

Synthetic probes written for lane XSYM-C (Pine libraries inlined at transpile
time) and their unedited TradingView trade exports. They contain no closed,
scraped or third-party source. Each was exported on 2026-09-28 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes>-eth15 --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to <to>
```

TradingView ran each from 2025-04-01 00:00 UTC (the export's requested and
returned range). The tapes' `Date and time` column is the exporting account's
chart timezone, Asia/Taipei (UTC+8). A probe enters on even bars and closes
on odd bars, each close's comment spelling the values it reads on that bar,
so a tape's exit `Signal`s are TradingView's values.

| Probe | `--to` | sha256 of the `.pine` | Tape sha256 | Trades |
|---|---|---|---|---|
| `xc_string_na` | 2025-04-03 | `806747e96250f7eeaadec626db1b33be0f8ca3fa83244d2a4f500c76ebc79fc9` | `1bee56dc2c9c4e7aa5aba515de5de01456f056d04bc80d2d3df73a30c344119c` | 96 |

What each tape shows:

- `xc_string_na`: string ternaries with a bare `na` arm, as a library returns
  a signal (`cond ? "BUY" : na`): in a function, a chain of them, at the top
  level, nested under another, as tuple elements and as the true arm. `na()`
  of each reads true exactly where TradingView's does (a `-` in the Signal).
