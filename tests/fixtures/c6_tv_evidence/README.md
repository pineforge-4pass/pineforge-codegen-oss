# C6 TradingView trade evidence

These are public probes written for C6 and their unedited TradingView trade
exports. They contain no closed benchmark source or artifact. Exported on
2026-09-24 for `BINANCE:ETHUSDT.P`, 15-minute chart,
`2025-04-01` through `2025-04-10`:

`ta_tr_bar_zero.pine`, `number_rendering.pine`, `number_edge.pine`, `number_pattern.pine`: `BINANCE:ETHUSDT.P` on a 15-minute chart, from 2025-04-01 through 2025-04-10.

The `ta.tr` tape's entry Signal is `bare-na` on its first chart bar
(`2025-04-01 08:00`). The three number tapes' entry Signals carry each numeric
string, keyed by the prefix before `:`. The E2Es cross-check these Signals
against independent codegen traces and trades on the engine's chart feed.
