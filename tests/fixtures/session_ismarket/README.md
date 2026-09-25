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

## `cgim-flags-*`: every session flag (codegen lane CG-ISMARKET)

The same six charts and windows, exported for this lane on 2026-09-25 (UTC)
with a probe that carries every flag in its Signal, a letter and a digit each:
`M` session.ismarket, `P` session.ispremarket, `Q` session.ispostmarket, `F`
session.isfirstbar, `L` session.islastbar, `f` session.isfirstbar_regular, `l`
session.islastbar_regular. The six `strategy.pine` files are one source. The
command, run once per row (`--no-note`: no campaign note was recorded):

```sh
source ~/code/pineforge-workflow/campaign/env.sh
lab tv --pine session_flags_probe.pine --slug <tape> --symbol <chart> \
  --interval 60 --from <from> --to <to> --out <dir>/<tape> --no-note --json
```

| tape | chart | window | bars | flags (bars) | tv_trades.csv sha256 | exported (UTC) |
|---|---|---|---|---|---|---|
| `cgim-flags-es1-60-dst-mar` | CME_MINI:ES1! 60 | 2025-03-02 .. 03-14 | 210 | `M1P0Q0F1L0f1l0` 10, `M1P0Q0F0L1f0l1` 9, `M1P0Q0F0L0f0l0` 191 | `be5bf635932cf5f460509b2f491ba283e904f4e0317a2f3c3c0d9f1c523f4fa1` | 2026-09-25 21:55:48 |
| `cgim-flags-es1-60-dst-nov` | CME_MINI:ES1! 60 | 2025-10-26 .. 11-07 | 209 | `M1P0Q0F1L0f1l0` 10, `M1P0Q0F0L1f0l1` 9, `M1P0Q0F0L0f0l0` 190 | `b477e11c942c31189e03684ec3d700c1f7a45e7f78c2e2e8f932f59c23fd51d8` | 2026-09-25 21:55:52 |
| `cgim-flags-es1-60-thanksgiving` | CME_MINI:ES1! 60 | 2025-11-23 .. 12-05 | 192 | `M1P0Q0F1L0f1l0` 10, `M1P0Q0F0L1f0l1` 9, `M1P0Q0F0L0f0l0` 173 | `54dfac6128b63df3ffe32e898df3ec0829998a700d2f2d78b7d1aebf140397f4` | 2026-09-25 21:55:56 |
| `cgim-flags-eurusd-60-dst-mar` | OANDA:EURUSD 60 | 2025-03-02 .. 03-14 | 220 | `M1P0Q0F1L0f1l0` 10, `M1P0Q0F0L1f0l1` 9, `M1P0Q0F0L0f0l0` 201 | `b40fbc81f290140da74127637c705c1d35872c92d10a7aa95e7228e8a791d25b` | 2026-09-25 21:55:58 |
| `cgim-flags-xauusd-60-dst-mar` | OANDA:XAUUSD 60 | 2025-03-02 .. 03-14 | 210 | `M1P0Q0F1L0f1l0` 10, `M1P0Q0F0L1f0l1` 9, `M1P0Q0F0L0f0l0` 191 | `308b250a01e1c2e77a74019d61f89e822305a9e76974bcd2b2e1b939a4cadb7e` | 2026-09-25 21:56:01 |
| `cgim-flags-eth-60-24x7` | BINANCE:ETHUSDT.P 60 | 2025-03-07 .. 03-11 | 97 | `M1P0Q0F1L0f1l0` 5, `M1P0Q0F0L1f0l1` 4, `M1P0Q0F0L0f0l0` 88 | `0a077e8b7af95d0a9279b0182d7024c140240ecd47d0d648a82e81cbbc61a5fe` | 2026-09-25 21:56:04 |

Every bar is in market and none is in an extended session (`P0Q0`); each
session day opens with an `F1` bar and closes with an `L1` bar, the
`_regular` flags equal to the plain ones. Every tape's last bar is `L0`: its
session day goes on past the window.
