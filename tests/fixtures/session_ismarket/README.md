# session.* TradingView tapes

The public tapes cover futures, forex, crypto and non-US-listed stocks.
Each directory holds `strategy.pine`, `tv_trades.csv`, `metrics.json` and,
where present, `meta.json`. `metrics.json` `tvTradesCsvHash` is the sha256
of `tv_trades.csv`, and its `sourceArtifactHash` that of `strategy.pine`.

Each probe reverses its position at the close of every chart bar
(`process_orders_on_close=true`), so every entry is one chart bar, dated at
its open, and its Signal is the flags TradingView evaluated there.
`tests/test_e2e_session_ismarket.py` replays every tape on flat bars stamped
at the tape's entry times: the flags are a function of the bars' times and
the symbol's session and timezone only.

## `hm-g236-*`: session.ismarket (engine lane H-MEASURE, row G2-36)

The Signal is `M1` when `session.ismarket` is true, `M0` when it is false.

| tape | chart | window | bars | flags | tv_trades.csv sha256 | exported (UTC) | campaign note |
|---|---|---|---|---|---|---|---|
| `hm-g236-es1-60-dst-mar` | CME_MINI:ES1! 60 | 2025-03-02 .. 03-14 (US DST 03-09) | 210 | all `M1` | `c1a6b73b032a51b6e8007d6fdbe4b59dc6aaa09ff7eafdceab7914ff46c9443d` | 2026-09-25 19:00:57 | `tv-tape-hm-g236-es1-60-dst-mar-c1a6b73b` |
| `hm-g236-es1-60-dst-nov` | CME_MINI:ES1! 60 | 2025-10-26 .. 11-07 (US DST 11-02) | 209 | all `M1` | `a7fbd62928dc49caf7bcdc6c9d8569139eab036860aa375aa0fc2c7db9b408b9` | 2026-09-25 19:01:22 | `tv-tape-hm-g236-es1-60-dst-nov-a7fbd629` |
| `hm-g236-es1-60-thanksgiving` | CME_MINI:ES1! 60 | 2025-11-23 .. 12-05 | 192 | all `M1` | `52512b7df99b764b8f13f8101b7e37481bdad5dfbf86620868f11fe175efaf8f` | 2026-09-25 19:01:26 | `tv-tape-hm-g236-es1-60-thanksgiving-52512b7d` |
| `hm-g236-eurusd-60-dst-mar` | OANDA:EURUSD 60 | 2025-03-02 .. 03-14 | 220 | all `M1` | `60713d2c22442ffe14070fddb1c5cd7fbf6fc6054c0b532b4c4275de305decf1` | 2026-09-25 19:01:34 | `tv-tape-hm-g236-eurusd-60-dst-mar-60713d2c` |
| `hm-g236-xauusd-60-dst-mar` | OANDA:XAUUSD 60 | 2025-03-02 .. 03-14 | 210 | all `M1` | `ebc226aea010125fb66b7cda3a86563846a243463723816ee529f8f044c54cf6` | 2026-09-25 19:01:37 | `tv-tape-hm-g236-xauusd-60-dst-mar-ebc226ae` |
| `hm-g236-eth-60-24x7` | BINANCE:ETHUSDT.P 60 | 2025-03-07 .. 03-11 | 97 | all `M1` | `96b7e8890b41a9e8aa60698a43cdffd5f42c55bb8d2d0223f7985c13b5040b74` | 2026-09-25 19:01:29 | `tv-tape-hm-g236-eth-60-24x7-96b7e889` |

## `cgim-flags-*`: every session flag (codegen lane CG-ISMARKET)

The same six charts and windows, with a probe that carries every flag in its
Signal, a letter and a digit each: `M` session.ismarket, `P`
session.ispremarket, `Q` session.ispostmarket, `F` session.isfirstbar, `L`
session.islastbar, `f` session.isfirstbar_regular, `l`
session.islastbar_regular. The six `strategy.pine` files are one source.

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

## Private every-flag tapes

TradingView trade list (Strategy Tester > List of trades) for a US-listed stock,
kept in a private repository and overlaid by the lab onto the paired engine tree
at `tests/codegen-fixtures/session_ismarket/`. These session-flag tapes are read
by `tests/test_e2e_session_ismarket.py`.

## `cgim-flags-xauusd-1d`: a daily chart (codegen lane CG-ISMARKET)

The every-flag probe on OANDA:XAUUSD 1D, 2025-01-01 .. 04-01.

| tape | chart | window | bars | flags | tv_trades.csv sha256 |
|---|---|---|---|---|---|
| `cgim-flags-xauusd-1d` | OANDA:XAUUSD 1D | 2025-01-01 .. 04-01 | 64 | all `M1P0Q0F1L1f1l1` | `a7ce0e8eb771a27f9a0a1f9fc9edba042927556e91856d6207d0b2f9faebc410` |

TradingView stamps each daily bar at 17:00 America/New_York, the break of the
`1800-1700` session, and flags it in market and as its session day's first and
last bar: a daily bar holds whole session days.

## Private flag-history tapes

TradingView trade list (Strategy Tester > List of trades) for a US-listed stock,
kept in a private repository and overlaid by the lab onto the paired engine tree
at `tests/codegen-fixtures/session_ismarket/`. These flag-history tapes are read
by `tests/test_e2e_session_history.py`; the extended-hours tape also supplies the
replay bars in `tests/test_e2e_cgint4_compositions.py`.

### Private function-history tape

TradingView trade list (Strategy Tester > List of trades) for a US-listed stock,
kept in a private repository and overlaid by the lab onto the paired engine tree
at `tests/codegen-fixtures/session_ismarket/`. This function-history tape is read
by `tests/test_e2e_session_history.py`.

## `cgs2-flags-*-15`: sessions with two windows (codegen lane CG-SESSION-2)

The every-flag probe of `cgim-flags-*` above (the same `strategy.pine`, sha256
`78898de72eeff09ef33a75e40f94da2b8ec9fbc301264183ba048e2e5f130225`) on three
15-minute charts whose regular session has two windows, 2025-03-03 .. 03-15.

| tape | chart | windows (exchange time) | bars | flags | tv_trades.csv sha256 | exported (UTC) |
|---|---|---|---|---|---|---|
| `cgs2-flags-tse7203-15` | TSE:7203 15 | 09:00-11:30, 12:30-15:30 Asia/Tokyo | 230 | all `M1P0Q0` | `d882fbe5d556d3c3fe5d2884adde2e28113a57c0b90d03072bf31185dca2ab9e` | 2026-09-26 09:27:40 |
| `cgs2-flags-hkex700-15` | HKEX:700 15 | 09:30-12:00, 13:00-16:00 Asia/Hong_Kong | 220 | all `M1P0Q0` | `45dd27e0365a9d4e942a478b457e3eb741fe912b2dba819bae5d8ea2c5d9dd36` | 2026-09-26 09:28:07 |
| `cgs2-flags-zc1-15` | CBOT:ZC1! 15 | 19:00-07:45, 08:30-13:20 America/Chicago | 710 | all `M1P0Q0` | `7a69b0f608ad98aa0055cf16fa201505792dfa1e7dbd2288cffc019a9f528e49` | 2026-09-26 09:28:16 |

TradingView flags every bar of the three charts in market and none pre- or
post-market: no bar opens between the windows or after the close, and these
symbols have no extended session. Each session day's first bar opens at the
first window's open (09:00, 09:30, 19:00) and its last in the last window
(15:30, 15:45, 13:15). TSE:7203's last bar opens at 15:30, the published end
of the afternoon window, and TradingView keeps it in the session.

## `ksw-flags-*`: the same three charts at 60 and 240 minutes (engine lane K-SESSION-WINDOWS)

The every-flag probe (the `cgim-flags-*` source with its title and header
comment changed, `strategy.pine` sha256
`1a78deccefb408d955112da398240e8fc5b146d126c40e514ee24078e4af83d4`) on
TSE:7203, HKEX:700 and CBOT:ZC1!, 2025-03-03 .. 03-15.

| tape | chart | bars | tv_trades.csv sha256 | exported (UTC) |
|---|---|---|---|---|
| `ksw-flags-tse7203-60` | TSE:7203 60 | 70 | `8ab368d71a6e8a87e27c6b2c0f312a2e911261de73100761a1ba4e27ce6ec657` | 2026-09-27 14:56:18 |
| `ksw-flags-tse7203-240` | TSE:7203 240 | 20 | `30134cad170cac95eb251511479b34a69bf9733bb26d694805e6c1df9b5eab56` | 2026-09-27 14:56:29 |
| `ksw-flags-hkex700-60` | HKEX:700 60 | 60 | `2ec27bec172e1955cb9b3859c3b0c7831d30a8c64718e9c8aa1c3eb6bb0074a2` | 2026-09-27 14:56:15 |
| `ksw-flags-hkex700-240` | HKEX:700 240 | 20 | `749048b62deb91a92aa670f1ad4ad9a33c92b8d86ea0ef9b6b6a4f68be6bdb8a` | 2026-09-27 14:56:25 |
| `ksw-flags-zc1-60` | CBOT:ZC1! 60 | 190 | `bb1094fb0ae4d4bb0916387117c1b2f774fc23e1157240b1eccf57809c42fd55` | 2026-09-27 14:56:22 |
| `ksw-flags-zc1-240` | CBOT:ZC1! 240 | 50 | `64b998d56cf824a6f1fcbbc04c5ced77a3fd0c85661956c413457cb5f0310231` | 2026-09-27 14:56:33 |

Every bar is in market and none pre- or post-market (`M1P0Q0`). TSE:7203's
60-minute bar that opens at 12:00, inside the 11:30-12:30 lunch break, and
CBOT:ZC1!'s that opens at 08:00, inside the 07:45-08:30 break, are in market
like every other bar. The TSE:7203 and CBOT:ZC1! bars open where the engine
aggregates the `cgs2-flags-*-15` tapes' bars. HKEX:700's 60-minute bars open
at 09:30, 10:00, 11:00, 13:00, 14:00 and 15:00, and TradingView flags both the
09:30 and the 10:00 bar `isfirstbar`; its 240-minute bars open at 09:30 and
13:00, and both are `isfirstbar` and `islastbar` (each `_regular` twin alike).
