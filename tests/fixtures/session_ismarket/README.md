# session.* TradingView tapes

TradingView's own session flags on every bar of six 60-minute charts. Each
directory is one `lab tv` export (channel `ws-report-v1`, `rangeProof`
covered), byte for byte: `strategy.pine`, `tv_trades.csv` (times at UTC+8,
the `lab tv` rendering), `meta.json`, `metrics.json`. `metrics.json`
`tvTradesCsvHash` is the sha256 of `tv_trades.csv`, and `meta.json`
`pine_sha256` that of `strategy.pine`. The probes are public; no closed or
scraped strategy is involved.

Each probe reverses its position at the close of every chart bar
(`process_orders_on_close=true`), so every entry is one chart bar, dated at
its open, and its Signal is the flags TradingView evaluated there.
`tests/test_e2e_session_ismarket.py` replays every tape on flat bars stamped
at the tape's entry times: the flags are a function of a bar's time and the
symbol's session and timezone only.

## `hm-g236-*`: session.ismarket (engine lane H-MEASURE, row G2-36)

Copied unchanged from the engine repository, branch `r5/hmeasure` at
`2b494497`, `tests/fixtures/session_ismarket/` (H-MEASURE's
`tests/test_session_ismarket_tape.cpp` reads them there). The Signal is `M1`
when `session.ismarket` is true, `M0` when it is false.

| tape | chart | window | bars | flags | tv_trades.csv sha256 | exported (UTC) | campaign note |
|---|---|---|---|---|---|---|---|
| `hm-g236-es1-60-dst-mar` | CME_MINI:ES1! 60 | 2025-03-02 .. 03-14 (US DST 03-09) | 210 | all `M1` | `c1a6b73b032a51b6e8007d6fdbe4b59dc6aaa09ff7eafdceab7914ff46c9443d` | 2026-09-25 19:00:57 | `tv-tape-hm-g236-es1-60-dst-mar-c1a6b73b` |
| `hm-g236-es1-60-dst-nov` | CME_MINI:ES1! 60 | 2025-10-26 .. 11-07 (US DST 11-02) | 209 | all `M1` | `a7fbd62928dc49caf7bcdc6c9d8569139eab036860aa375aa0fc2c7db9b408b9` | 2026-09-25 19:01:22 | `tv-tape-hm-g236-es1-60-dst-nov-a7fbd629` |
| `hm-g236-es1-60-thanksgiving` | CME_MINI:ES1! 60 | 2025-11-23 .. 12-05 | 192 | all `M1` | `52512b7df99b764b8f13f8101b7e37481bdad5dfbf86620868f11fe175efaf8f` | 2026-09-25 19:01:26 | `tv-tape-hm-g236-es1-60-thanksgiving-52512b7d` |
| `hm-g236-eurusd-60-dst-mar` | OANDA:EURUSD 60 | 2025-03-02 .. 03-14 | 220 | all `M1` | `60713d2c22442ffe14070fddb1c5cd7fbf6fc6054c0b532b4c4275de305decf1` | 2026-09-25 19:01:34 | `tv-tape-hm-g236-eurusd-60-dst-mar-60713d2c` |
| `hm-g236-xauusd-60-dst-mar` | OANDA:XAUUSD 60 | 2025-03-02 .. 03-14 | 210 | all `M1` | `ebc226aea010125fb66b7cda3a86563846a243463723816ee529f8f044c54cf6` | 2026-09-25 19:01:37 | `tv-tape-hm-g236-xauusd-60-dst-mar-ebc226ae` |
| `hm-g236-eth-60-24x7` | BINANCE:ETHUSDT.P 60 | 2025-03-07 .. 03-11 | 97 | all `M1` | `96b7e8890b41a9e8aa60698a43cdffd5f42c55bb8d2d0223f7985c13b5040b74` | 2026-09-25 19:01:29 | `tv-tape-hm-g236-eth-60-24x7-96b7e889` |
