# session.* TradingView tapes

TradingView's own session flags on every bar of eight 60-minute charts and
one daily chart. Each
directory is one TradingView export (channel `ws-report-v1`, `rangeProof`
covered), byte for byte: `strategy.pine`, `tv_trades.csv` (times at UTC+8,
the exporters' rendering), `metrics.json`, and for a `lab tv` export
`meta.json`. `metrics.json` `tvTradesCsvHash` is the sha256 of
`tv_trades.csv`, and its `sourceArtifactHash` that of `strategy.pine`. The
probes are public; no closed or scraped strategy is involved.

Each probe reverses its position at the close of every chart bar
(`process_orders_on_close=true`), so every entry is one chart bar, dated at
its open, and its Signal is the flags TradingView evaluated there.
`tests/test_e2e_session_ismarket.py` replays every tape on flat bars stamped
at the tape's entry times: the flags are a function of the bars' times and
the symbol's session and timezone only.

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

## `cgim-flags-aapl-60-*`: NASDAQ:AAPL with and without extended hours (codegen lane CG-ISMARKET)

The every-flag probe on NASDAQ:AAPL 60, 2025-03-03 .. 03-15 (US DST 03-09),
exported on 2026-09-25 (UTC) through the WebSocket exporter
`pinescript-scrapper/scripts/tv-ws-backtest.mjs` (sha256
`f2dc0bb0e86b4a8685edaec7d11da1f7d89dd4c18c0aac0c9bb91c75ea79012b`, last
changed in `35fe8338b`). The exporter asks TradingView for the regular
session. The extended-hours tape ran a scratch copy that differs in one line,
the symbol descriptor's `session: process.env.CGIM_TV_SESSION || "regular"`
(sha256 `15d8afae63844c35481e94ba14000720a9e8f0f500d28c51bc2f8cb8189f4f06`),
with `CGIM_TV_SESSION=extended`; the regular tape ran the same copy without
it. Every other byte on the wire is the exporter's. These exports write
`metrics.json` but no `meta.json`; its `sourceArtifactHash` is the sha256 of
`strategy.pine`.

```sh
node tv-ws-backtest-session.mjs --pine-dir <group> --write-into-dir \
  --symbol NASDAQ:AAPL --interval 60 --from 2025-03-03 --to 2025-03-15 --concurrency 1
```

| tape | session | bars | flags by bar open (America/New_York), every day | tv_trades.csv sha256 | exported (UTC) |
|---|---|---|---|---|---|
| `cgim-flags-aapl-60-ext` | extended | 160 | 04:00 `M0P1Q0F1L0f0l0`, 05:00-09:00 `M0P1Q0F0L0f0l0`, 10:00 `M1P0Q0F0L0f1l0`, 11:00-14:00 `M1P0Q0F0L0f0l0`, 15:00 `M1P0Q0F0L0f0l1`, 16:00-18:00 `M0P0Q1F0L0f0l0`, 19:00 `M0P0Q1F0L1f0l0` | `c61119c7077dcf01cf89b13c77931a52bff4c1078e63c0e58c3e687f93db0802` | 2026-09-25 23:04:04 |
| `cgim-flags-aapl-60-reg` | regular | 70 | 09:30 `M1P0Q0F1L0f1l0`, 10:30-14:30 `M1P0Q0F0L0f0l0`, 15:30 `M1P0Q0F0L1f0l1` | `163434a171c27026622b65d0777118120f7660a2a97b2e320091aaf1c9bc6a57` | 2026-09-25 23:04:06 |

The extended chart's bars open on the hour, and TradingView flags each by its
open time: the 09:00 bar, which holds the 09:30 open, is pre-market, and the
16:00 bar is post-market. There isfirstbar / islastbar are the extended day's
first and last bars and the `_regular` twins the regular day's.

## `cgim-flags-xauusd-1d`: a daily chart (codegen lane CG-ISMARKET)

The every-flag probe on OANDA:XAUUSD 1D, 2025-01-01 .. 04-01, exported with
`lab tv` on 2026-09-26 00:09:26 (UTC), `--no-note`:

```sh
lab tv --pine session_flags_probe.pine --slug cgim-flags-xauusd-1d \
  --symbol OANDA:XAUUSD --interval 1D --from 2025-01-01 --to 2025-04-01 \
  --out <dir>/cgim-flags-xauusd-1d --no-note --json
```

| tape | chart | window | bars | flags | tv_trades.csv sha256 |
|---|---|---|---|---|---|
| `cgim-flags-xauusd-1d` | OANDA:XAUUSD 1D | 2025-01-01 .. 04-01 | 64 | all `M1P0Q0F1L1f1l1` | `a7ce0e8eb771a27f9a0a1f9fc9edba042927556e91856d6207d0b2f9faebc410` |

TradingView stamps each daily bar at 17:00 America/New_York, the break of the
`1800-1700` session, and flags it in market and as its session day's first and
last bar: a daily bar holds whole session days.

## `cgs2-hist-aapl-15-*`: the flags read with a history offset (codegen lane CG-SESSION-2)

One probe on NASDAQ:AAPL 15, 2025-03-03 .. 03-15 (US DST 03-09), without and
with extended hours, exported on 2026-09-26 (UTC). Its Signal is three
seven-bit groups in the flag order `M P Q F L f l` above -- `C` the flags on
the bar, `H` the flags `[1]`, `T` the flags `[2]` -- then one bit each: `B`
session.ispremarket[1] assigned in a block run on every third bar, `U`
session.ispostmarket[1] read by a function called on odd bars, `Z`
session.islastbar[1] on the lazy right side of an `and` reached on even bars,
`K` session.isfirstbar[k] with k = 1 or 2 by bar, `D` session.ismarket[0], `W`
session.ismarket[64]. The two `strategy.pine` files are one source.

The regular tape is a `lab tv` export (`--no-note`: no campaign note):

```sh
lab tv --pine session_history_probe.pine --slug cgs2-hist-aapl-15-reg \
  --symbol NASDAQ:AAPL --interval 15 --from 2025-03-03 --to 2025-03-15 \
  --out <dir>/cgs2-hist-aapl-15-reg --no-note --json
```

`lab tv` asks TradingView for the regular session only, so the extended tape
ran lane CG-ISMARKET's one-line copy of the WebSocket exporter (sha256
`15d8afae63844c35481e94ba14000720a9e8f0f500d28c51bc2f8cb8189f4f06`, see
`cgim-flags-aapl-60-*` above) with `CGIM_TV_SESSION=extended`; it writes no
`meta.json`.

```sh
CGIM_TV_SESSION=extended node tv-ws-backtest-session.mjs --pine-dir <group> \
  --write-into-dir --symbol NASDAQ:AAPL --interval 15 --from 2025-03-03 \
  --to 2025-03-15 --concurrency 1
```

| tape | session | bars | tv_trades.csv sha256 | exported (UTC) |
|---|---|---|---|---|
| `cgs2-hist-aapl-15-reg` | regular | 260 | `d4f5ed467ea7ac82fc90e4c0e6cc125c70c47b11762e342d11f946e89cffc737` | 2026-09-26 09:25:49 |
| `cgs2-hist-aapl-15-ext` | extended | 639 | `a6dcfe7eacbc5704c91eac765ab93e7115ceb49d16e5440e8a42e70a2d9952da` | 2026-09-26 09:26:24 |

TradingView reads a flag's history by bars at the top level of a script: on
all 899 bars `H` and `T` are the `C` of one and two bars back, `B`, `Z`, `K`,
`D` and `W` the flag of the bar their offset names, and every read before the
first bar is false. Inside a function it reads the function's calls: `U` is
session.ispostmarket two bars back, on the function's previous call, which
differs from the previous bar's flag on 7 extended-hours bars. The extended
tape lacks one bar (2025-03-06 18:30 ET), which TradingView's data does not
hold.

## `cgs2-flags-*-15`: sessions with two windows (codegen lane CG-SESSION-2)

The every-flag probe of `cgim-flags-*` above (the same `strategy.pine`, sha256
`78898de72eeff09ef33a75e40f94da2b8ec9fbc301264183ba048e2e5f130225`) on three
15-minute charts whose regular session has two windows, 2025-03-03 .. 03-15,
exported with `lab tv` on 2026-09-26 (UTC), `--no-note`:

```sh
lab tv --pine session_flags_probe.pine --slug <tape> --symbol <chart> \
  --interval 15 --from 2025-03-03 --to 2025-03-15 --out <dir>/<tape> --no-note --json
```

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
