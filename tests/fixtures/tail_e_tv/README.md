# TAIL-E TradingView trade evidence

Synthetic probes written for lane TAIL-E and their unedited TradingView trade
exports. They contain no closed or scraped source. Each was exported on
2026-09-28 / 09-29 (UTC) with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes>-<chart> --no-note \
  --symbol <symbol> --interval <interval> --from <from> --to <to>
```

TradingView ran each from the `from` date at 00:00 UTC (the export's
requested and returned range, `rangeProof: covered`). The tapes' `Date and
time` column is the exporting account's chart timezone, Asia/Taipei (UTC+8).
A probe spells the values under test in its orders: most send an entry named
by the text on a flat bar and close the position with the text as the comment
on the next, so every chart bar's values are on the tape (`tests/_tail_e_tapes.py`
reads them); `te_syminfo_timezone` and `te_generic_input` name their entries
`L` and spell the values in both comments, and the replays read the closes.

| Probe | sha256 of the `.pine` | Chart, range | Tape sha256 | Rows |
|---|---|---|---|---|
| `te_syminfo_timezone` | `045cae5566bd94142b1f3fc3e4432dac6b2d59283290021ef71ec76607ae371b` | BINANCE:ETHUSDT.P 15, 2025-04-01..04-02 | `65cde8b3be2a2425e2f5e82b0c8f7d451ec11c3c6d5c6dd3303dd5e7d1c2c51e` | 82 |
| `te_syminfo_timezone` | same | BINANCE:BTCUSDT 1D, 2025-01-01..02-01 | `7dd851704029cd307296c3e854de16cff6be818177d77dab2d96a014b816b87e` | 32 |
| `te_generic_input` | `5d2a0810a02fbc5a3e315b6af1b711c0bfae512ff940da6a418f706c11ff9252` | BINANCE:ETHUSDT.P 15, 2025-04-01..04-02 | `7751d2b2371238922024e21575bd5030544d61771ad298899da5dbea6e06354b` | 96 |
| `te_sec_loop_helper` | `4f269118eee1c69e1a778fb39594ab4a4ab59ea3cfcea74a4f2cff0e036f7bcc` | BINANCE:ETHUSDT.P 15, 2025-04-01..04-08 | `90707961b68b80f2799328f4108fce134649cd9a89d9baadca94621891c159b0` | 672 |
| `te_sec_loop_ltf` | `b9598c42fae92cf299e0cfa67b77811375f00bd4c7b6c98620fede1ae722c3b2` | BINANCE:ETHUSDT.P 15, 2025-04-01..04-08 | `0556e357ec8b59cb487fe1b00f418811848fbfb02035bb4f5cfcdf74cd61b5c7` | 672 |
| `te_nested_untyped` | `f867061011c12e2147651c704c3c47823c0263b5e1e2c9e6c1fde9d82b7d1fef` | BINANCE:ETHUSDT.P 15, 2025-04-01..04-03 | `ec0641ff5408ff94de4c8d5478deb266b822bd2f405e75daeb7677b1a8888dea` | 192 |
| `te_time_bb_chart` | `f99738effbfc0c7899be5236980ad40e58257efc0578a905046b5d4d346136d5` | BINANCE:ETHUSDT.P 15, 2025-04-01..04-03 | `0999f59d862be6fb584e54b517c4fb138c0894cf709eaefed176bff551da3fa5` | 192 |
| `te_time_bb_tf` | `201116af10cfbf6fcbe8a8088f00ac10dcc52783e3f12889a3a32bfda5c93d1b` | BINANCE:ETHUSDT.P 15, 2025-04-01..04-03 | `ea67bc36f891ce04bcbe89e6625338430feab67a00b483112565c60ab1c6636a` | 192 |
| `te_lazy_if_source` | `5140426dee6164e8b8e816c50e96abc157fa8f22f2d0632e496d616467e7679d` | BINANCE:ETHUSDT.P 15, 2025-04-01..04-05 | `f8b4f0cfbd12a66b4ce0b854975594957afd8dff243a58d0968f6e5c6792dcf1` | 384 |

What each tape shows:

- `te_syminfo_timezone`: `syminfo.timezone` is "Etc/UTC" on BINANCE:ETHUSDT.P
  and BINANCE:BTCUSDT (`== "Etc/UTC"` holds, `== "UTC"` does not), beside
  `syminfo.tickerid`, `timeframe.period`, `timeframe.isminutes`,
  `timeframe.multiplier`, `chart.is_standard` and `hour` / `dayofweek` in that
  zone (`tests/test_e2e_syminfo_timezone_spelling.py`).
- `te_generic_input`: `input("pf-tag")` is a string input, `input(3)` an int
  one (`str.tostring(n / 2)` reads "1.5"), `input(true)` a bool one; the
  orders are sized `n * x` = 1.5 (`tests/test_e2e_generic_input_types.py`).
- `te_sec_loop_helper`, `te_sec_loop_ltf`: a request.security helper whose
  `for` loop scores the requested close against its last n opens, smoothed
  through a helper whose if/else chain picks the TA call, and a `while` loop
  that breaks; on the hour with lookahead off and on and on the chart's own
  15 minutes, and on 5 and 3 minutes under the chart
  (`tests/test_e2e_security_helper_loops.py`).
- `te_nested_untyped`: a wrapper called on two written paths whose nested
  helper takes an untyped string parameter choosing its TA arm
  (`tests/test_e2e_nested_instance_param_types.py`).
- `te_time_bb_chart`, `te_time_bb_tf`: `time()` / `time_close()` with
  `bars_back` and `timeframe_bars_back`, forward and back, on the chart's
  timeframe and on the hour, day, week and month
  (`tests/test_time_bar_offsets.py`). The same probes' tapes on NASDAQ:AAPL
  15 and 1D, OANDA:XAUUSD 15, OANDA:EURUSD 1D and BINANCE:BTCUSDT 1D -- session
  breaks, weekends and exchange closures -- are replayed cell by cell by the
  engine's `tests/test_time_bars_back_tapes.cpp` (`tests/fixtures/time_bars_back`
  there).
- `te_lazy_if_source`: `ta.roc`, `ta.change` and `ta.mom` called inside if
  blocks, one nested and one in an else-if, read their own held source
  history (`tests/test_e2e_lazy_if_source_clock.py`).
