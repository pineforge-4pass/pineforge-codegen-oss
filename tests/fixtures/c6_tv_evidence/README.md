# C6 TradingView trade evidence

These are public probes written for C6 and their unedited TradingView trade
exports. They contain no closed benchmark source or artifact. Exported on
2026-09-24 with `lab tv` on `BINANCE:ETHUSDT.P`, 15-minute chart,
`2025-04-01` through `2025-04-10`:

```bash
lab tv --pine ta_tr_bar_zero.pine --slug c6-ta-tr-bar-zero \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-10
lab tv --pine number_rendering.pine --slug c6-number-rendering \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-10
lab tv --pine number_edge.pine --slug c6-number-edge \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-10
lab tv --pine number_pattern.pine --slug c6-format-pattern \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-10
```

The `ta.tr` tape's entry Signal is `bare-na` on its first chart bar
(`2025-04-01 08:00`). The three number tapes' entry Signals carry each numeric
string, keyed by the prefix before `:`. The E2Es cross-check these Signals
against independent codegen traces and trades on the engine's chart feed.
