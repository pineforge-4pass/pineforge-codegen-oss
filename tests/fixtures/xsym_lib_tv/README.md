# XSYM-C TradingView trade evidence

Synthetic probes written for lane XSYM-C (Pine libraries inlined at transpile
time) and their unedited TradingView trade exports. They contain no closed,
scraped or third-party source. Each was exported on 2026-09-28 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes>-eth15 --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to <to>
```

`xc_v5_lib`'s daily tape (`xc_v5_lib_eth1d_tv_trades.csv`) was exported with
`--slug pf-xc-v5-lib-eth1d --interval 1D`. TradingView ran each from
2025-04-01 00:00 UTC (the export's requested and returned range). The tapes'
`Date and time` column is the exporting account's chart timezone,
Asia/Taipei (UTC+8). A probe enters on even bars and closes on odd bars, each
close's comment spelling the values it reads on that bar, so a tape's exit
`Signal`s are TradingView's values.

| Probe | `--to` | sha256 of the `.pine` | Tape sha256 | Trades |
|---|---|---|---|---|
| `xc_string_na` | 2025-04-03 | `806747e96250f7eeaadec626db1b33be0f8ca3fa83244d2a4f500c76ebc79fc9` | `1bee56dc2c9c4e7aa5aba515de5de01456f056d04bc80d2d3df73a30c344119c` | 96 |
| `xc_string_cast_na` | 2025-04-03 | `959560af9d2cca4b8b585c6bbc1f4036b88633316e563b000c9a0c4285b4b2a6` | `ce5bceb00b5577a26cb01b2cbe091796623619783c57ff616aaeb8745a034075` | 96 |
| `xc_string_tuple_select` | 2025-04-03 | `6ed50855bee2a9e2dc63415aff77c5227544dfc2d2945629663431d25f609973` | `71e88d978cb2fd9ff2a590edaf79a3c8bc9cefd2a1b6b647719adc814a34f3c1` | 96 |
| `xc_decl_return` | 2025-04-03 | `485b0260ff4d54ca58cac4fd199f6827a4f93f7ad0ef5dfdd4b3cce632235309` | `44775cee93d17d5b1cce151ef5b74b15ea6b6081ebbaf1c3addd7b451f645a93` | 96 |
| `xc_v5_lib` | 2025-04-03 | `a8735b81c08326f8d80b779fa0895f0a2f538579992a7049d3ca51075798ceae` | `669563c5f0299bb929425f42feb16fe21ee510146b538ccf1327dd938ffc4c77` | 288 |
| `xc_v5_lib` (1D) | 2025-05-01 | `a8735b81c08326f8d80b779fa0895f0a2f538579992a7049d3ca51075798ceae` | `24a5e2d6c7cf766c86d5e936f8055679b509a7d6765e17eee006fa3379ac4475` | 45 |
| `xc_v5_negindex` | 2025-04-02 | `9adc249d9957e2111592609df1f8bc942686c86e0dcb2147b996e47d3ecf375b` | none: a run error | - |

What each tape shows:

- `xc_string_na`: string ternaries with a bare `na` arm, as a library returns
  a signal (`cond ? "BUY" : na`): in a function, a chain of them, at the top
  level, nested under another, as tuple elements and as the true arm. `na()`
  of each reads true exactly where TradingView's does (a `-` in the Signal).
- `xc_string_cast_na`: `string(na)`, Pine's cast of `na` to a string, bare
  and as the arms of a string tuple selected by an `if`: `na()` of it reads
  true on every bar.
- `xc_string_tuple_select`: a string tuple selected by an `if` from a
  function's tuple or `[string(na), string(na)]` (the richardgong1988 probes'
  shape), then read through a ternary with an `na` arm; one `if` takes the
  function (input `A` true), the other the casts (input `B` false).
- `xc_decl_return`: functions whose last statement is a declaration (`float
  result = x * 2`, a typed `int n = ...`, an untyped `r = ...`) or a
  reassignment (`result := ...` over its own history, `acc += 1`, a `var`'s
  `hi := ...`): each returns that variable's value, in its declared type.
- `xc_v5_lib`: a `//@version=5` strategy holding the functions of the
  synthetic v5 library `pftest/V5Rules/1` (`../pine_libraries`) as its own,
  since an unpublished library cannot run on TradingView. Three positions
  close on every odd bar, their Signals spelling what v5 reads: `d` int
  divisions (two const ints round toward zero: `-7 / 2` is `-3`; a typed
  parameter, a reassigned variable, `nz()`, `math.sign()` or a series divide
  as floats), `e` how often each `and` / `or` / `?:` operand ran (both of an
  `and` / `or`, one `?:` arm), `l` a `for` whose end is fixed before the
  first iteration (4 iterations of `0 to lim` while the body lowers `lim`),
  `k` v5's `color.red` / `teal` / `yellow`, `t` numbers as conditions, `n` a
  bool na read as false where v5 casts it and across the call (`FT`), and `f`
  `timeframe.period`: `15`, and `D` on the daily tape, where v6 spells `1D`.
- `xc_v5_negindex`: `array.get(a, -1)` of a 3-element array on bar 10 under
  v5. TradingView returned no tape, only the run error: `{"ctx":{"funcName":
  "get","index":-1,"code":"RE10045","size":3,"bar_index":10},"error":"Error
  on bar {bar_index}: In 'array.{funcName}()' function. Index {index} is out
  of bounds, array size is {size}."}`. Under v6 the same read is the last
  element.
