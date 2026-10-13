# session.* TradingView tapes

The public tapes cover crypto and non-US-listed stocks.
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
| `hm-g236-eth-60-24x7` | BINANCE:ETHUSDT.P 60 | 2025-03-07 .. 03-11 | 97 | all `M1` | `96b7e8890b41a9e8aa60698a43cdffd5f42c55bb8d2d0223f7985c13b5040b74` | 2026-09-25 19:01:29 | `tv-tape-hm-g236-eth-60-24x7-96b7e889` |

## `cgim-flags-*`: every session flag (codegen lane CG-ISMARKET)

The same public chart and window, with a probe that carries every flag in its
Signal, a letter and a digit each: `M` session.ismarket, `P`
session.ispremarket, `Q` session.ispostmarket, `F` session.isfirstbar, `L`
session.islastbar, `f` session.isfirstbar_regular, `l`
session.islastbar_regular. The public probes share one source.

| tape | chart | window | bars | flags (bars) | tv_trades.csv sha256 | exported (UTC) |
|---|---|---|---|---|---|---|
| `cgim-flags-eth-60-24x7` | BINANCE:ETHUSDT.P 60 | 2025-03-07 .. 03-11 | 97 | `M1P0Q0F1L0f1l0` 5, `M1P0Q0F0L1f0l1` 4, `M1P0Q0F0L0f0l0` 88 | `0a077e8b7af95d0a9279b0182d7024c140240ecd47d0d648a82e81cbbc61a5fe` | 2026-09-25 21:56:04 |

Every bar is in market and none is in an extended session (`P0Q0`); each
session day opens with an `F1` bar and closes with an `L1` bar, the
`_regular` flags equal to the plain ones. Every tape's last bar is `L0`: its
session day goes on past the window.

## Private every-flag tapes

TradingView trade list for a US-listed stock,
kept in a private repository and overlaid by the lab onto the paired engine tree
at `tests/codegen-fixtures/session_ismarket/`. These session-flag tapes are read
by `tests/test_e2e_session_ismarket.py`.

## Private session-market and daily tapes

TradingView trade lists for US futures, forex and spot metals, kept in a private
repository and overlaid by the lab onto the paired engine tree at
`tests/codegen-fixtures/session_ismarket/`. These session-market and
every-flag tapes are read by `tests/test_e2e_session_ismarket.py`.

## Private flag-history tapes

TradingView trade list for a US-listed stock,
kept in a private repository and overlaid by the lab onto the paired engine tree
at `tests/codegen-fixtures/session_ismarket/`. These flag-history tapes are read
by `tests/test_e2e_session_history.py`; the extended-hours tape also supplies the
replay bars in `tests/test_e2e_cgint4_compositions.py`.

### Private function-history tape

TradingView trade list for a US-listed stock,
kept in a private repository and overlaid by the lab onto the paired engine tree
at `tests/codegen-fixtures/session_ismarket/`. This function-history tape is read
by `tests/test_e2e_session_history.py`.

## `cgs2-flags-*-15`: sessions with two windows (codegen lane CG-SESSION-2)

The every-flag probe of `cgim-flags-*` above (the same `strategy.pine`, sha256
`78898de72eeff09ef33a75e40f94da2b8ec9fbc301264183ba048e2e5f130225`) on the public
15-minute charts whose regular session has two windows.

| tape | chart | windows (exchange time) | bars | flags | tv_trades.csv sha256 | exported (UTC) |
|---|---|---|---|---|---|---|
| `cgs2-flags-tse7203-15` | TSE:7203 15 | 09:00-11:30, 12:30-15:30 Asia/Tokyo | 230 | all `M1P0Q0` | `d882fbe5d556d3c3fe5d2884adde2e28113a57c0b90d03072bf31185dca2ab9e` | 2026-09-26 09:27:40 |
| `cgs2-flags-hkex700-15` | HKEX:700 15 | 09:30-12:00, 13:00-16:00 Asia/Hong_Kong | 220 | all `M1P0Q0` | `45dd27e0365a9d4e942a478b457e3eb741fe912b2dba819bae5d8ea2c5d9dd36` | 2026-09-26 09:28:07 |

TradingView flags every bar of the public charts in market and none pre- or
post-market: no bar opens between the windows or after the close, and these
symbols have no extended session. Each session day's first bar opens at the
first window's open (09:00, 09:30) and its last in the last window
(15:30, 15:45). TSE:7203's last bar opens at 15:30, the published end
of the afternoon window, and TradingView keeps it in the session.

## `ksw-flags-*`: the same public charts at 60 and 240 minutes (engine lane K-SESSION-WINDOWS)

The every-flag probe (the `cgim-flags-*` source with its title and header
comment changed, `strategy.pine` sha256
`1a78deccefb408d955112da398240e8fc5b146d126c40e514ee24078e4af83d4`) on
the public TSE:7203 and HKEX:700 charts.

| tape | chart | bars | tv_trades.csv sha256 | exported (UTC) |
|---|---|---|---|---|
| `ksw-flags-tse7203-60` | TSE:7203 60 | 70 | `8ab368d71a6e8a87e27c6b2c0f312a2e911261de73100761a1ba4e27ce6ec657` | 2026-09-27 14:56:18 |
| `ksw-flags-tse7203-240` | TSE:7203 240 | 20 | `30134cad170cac95eb251511479b34a69bf9733bb26d694805e6c1df9b5eab56` | 2026-09-27 14:56:29 |
| `ksw-flags-hkex700-60` | HKEX:700 60 | 60 | `2ec27bec172e1955cb9b3859c3b0c7831d30a8c64718e9c8aa1c3eb6bb0074a2` | 2026-09-27 14:56:15 |
| `ksw-flags-hkex700-240` | HKEX:700 240 | 20 | `749048b62deb91a92aa670f1ad4ad9a33c92b8d86ea0ef9b6b6a4f68be6bdb8a` | 2026-09-27 14:56:25 |

Every bar is in market and none pre- or post-market (`M1P0Q0`). TSE:7203's
60-minute bar that opens at 12:00, inside the 11:30-12:30 lunch break, is in
market like every other bar. The TSE:7203 bars open where the engine
aggregates the `cgs2-flags-*-15` tapes' bars. HKEX:700's 60-minute bars open
at 09:30, 10:00, 11:00, 13:00, 14:00 and 15:00, and TradingView flags both the
09:30 and the 10:00 bar `isfirstbar`; its 240-minute bars open at 09:30 and
13:00, and both are `isfirstbar` and `islastbar` (each `_regular` twin alike).

## Private two-window tapes

TradingView trade lists for US futures, kept in a private repository and
overlaid by the lab onto the paired engine tree at
`tests/codegen-fixtures/session_ismarket/`. These every-flag tapes are read
by `tests/test_e2e_session_windows.py`.
