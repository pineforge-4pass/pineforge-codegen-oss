# CG-XSYM-A2 TradingView trade evidence

Synthetic probes written for lane CG-XSYM-A2 (`request.security` payload
defects XSYM-A left open) and their unedited TradingView trade exports. They
contain no closed or scraped source. Each was exported on 2026-09-28 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes>-eth15 --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran each from 2025-04-01 00:00 UTC to 2025-04-08 00:00 UTC (the
export's requested and returned range, `rangeProof: covered`). The tapes'
`Date and time` column is the exporting account's chart timezone,
Asia/Taipei (UTC+8). Each probe enters on the bars at minute 0 and 30 and
closes on the bars at minute 15 and 45 with a fixed 0.01 contract, each
close's comment spelling the values it reads on that bar, so a tape's exit
`Signal`s are TradingView's values.

| Probe | sha256 of the `.pine` | Tape sha256 | Trades |
|---|---|---|---|
| `xa2_source_payload` | `521892b5626a7cc54570a6fc6f9acd7a7fcf633cd4efcbb81c732e5572b0c625` | `36f1dfb551510ce7850ecd2826d85e0afdc8c7db718dd64484d47896cbb908f4` | 336 |
| `xa2_source_payload_high` | `6d628d3d8fb1cee71a672577d97c815d243a065444fb8cb7b0413ccd39e4010e` | `437a6275190a8960969c9b6eefdb5b62d0c9bf9deeaed582ca0a12ab536f7967` | 336 |
| `xa2_source_param` | `59f293fecfcc122fe4f78f54e17b59536492c936c02f279119e5e3c7f8a98ca5` | `542b8804cb3ff7573c460b96f1305c9762f6a1119691b9f7a120d950d101616c` | 336 |
| `xa2_source_param_low` | `94943784f3ccbdd6349ef19e7b5882859f129d5fce6c49b5858bea3561ec3735` | `2eb1b764d7af492c31cc608519cd96fae167190dd4cc9892ecc3baa0263f64ae` | 336 |
| `xa2_single_context` | `7b5aa0e73aa5973ac112f6de0df961e576eb4d76fdeb2c3570bcef4a75d7a6ad` | `17d1b3579d9dadcd041e906c0a0e7f7e9fd741d60261af8dec2d969db504431d` | 336 |
| `xa2_price_later` | `b4cd503f23935ab3ad82372b5e71f6e59f9c61a53f509e9f3ca0581053dbc00b` | `b259c487d8cce5fd993cea40ca4237af04c5ca9689b7d148e54064fc0f0ac456` | 336 |
| `xa2_prologue_order` | `3bde7c98eceab704bd43fb5e137ba3bd1553cc28915e70aa03cefb33c91e92ac` | `b8780e1273a8f3750499b52b6a9ee55d69b58b8610d99858237242ac1e534003` | 336 |
| `xa2_var_selection` | `064e0569d79c1f6ff8e89d828437bb246032ff3dda7370634e0e018824127e9c` | `affdf36033bee1003bfff749088c9bd76f1272b547c09cc9ef17c2204062319f` | 336 |

What each tape shows:

- `xa2_source_payload`: payloads reading `src = input.source(close, "Src")`
  and `hs = input.source(hl2, "HL Src")` on the requested bars: `src` and
  `src[1]` on 60 minutes, `logeq(src, n)` with `logeq(_s, _l) =>
  ta.change(_s, _l) / _s[_l]` and `n = input.int(2, "N")` on 240 minutes
  (job-1361's shape), `ta.sma(hs, 3)`, a multi-statement helper's
  `ta.rsi(_s, 5) - 50` of `src`, and `nz(hs[2]) - src` on 60 minutes. Exit
  Signals: `a` ... `f`, four decimals.
- `xa2_source_payload_high`: the same probe with the defaults `high` and
  `ohlc4`; its tape is what the first probe reads under the overrides
  `Src=high`, `HL Src=ohlc4`.
- `xa2_source_param`: payloads reading a parameter of the helper holding the
  request, bound to `src`: `g(_e) => request.security(syminfo.tickerid, "60",
  _e[1])`, `h(_v) => request.security(syminfo.tickerid, "240", ta.sma(_v,
  3))` and `k(_v) => request.security(syminfo.tickerid, "60", _v - open)`.
  Exit Signals: `a`, `b`, `c`, four decimals.
- `xa2_source_param_low`: the same probe with the default `low`; its tape is
  what the first probe reads under the override `Src=low`.
- `xa2_single_context`: request.security helpers whose symbol or timeframe
  parameter every call passes one value, with no TA of their own:
  `f(string sym) => request.security(sym, "60", close[1])`,
  `g(tf) => request.security(syminfo.tickerid, tf, close)` as `g("240")`
  and `h(string tf, int n) => request.security(syminfo.tickerid, tf,
  high[1]) + n` as `h("60", 1)`. Exit Signals: `a`, `b`, `c`, four
  decimals.
- `xa2_price_later`: payloads reading the history of the derived price
  series (`hl2[1]` on 60 minutes; `ohlc4[2] - hlc3[1] + hlcc4[1] - hlcc4` on
  240), `g(_e) => request.security(syminfo.tickerid, "60", _e[1])` called
  as `g(hl2)` and as `g(ma)` with `ma = ta.sma(close, 3)` declared after
  `g`, `u(_x) => ta.sma(_x, 3)` read in a payload as `u(s5)` with
  `s5 = ta.sma(close, 5)` declared after `u`, `h(_v, n) =>
  request.security(syminfo.tickerid, "240", ta.sma(_v, n))` called as
  `h(body, len)` with `len = input.int(4, "Len")` and `body = close - open`
  declared after `h`, and the chart's own `hlcc4[1]`. Exit Signals: `a` ...
  `f`, `k`, four decimals. An earlier export of the probe read `u(ema5)`
  with `ema5 = ta.ema(close, 5)`; every column but that one matched, which
  diverges on the engine's approximated EMA warmup, so the probe was
  re-exported with an SMA.
- `xa2_prologue_order`: a payload TA reading, through a multi-statement
  helper with an if-branch (`f(_v) => ... if close > open ... r + _v`), an
  SMA declared after the helpers: `u(_x) => ta.sma(f(_x), 3)` as `u(s5)`
  with `s5 = ta.sma(close, 5)` on 60 minutes, and `g(_e) =>
  request.security(syminfo.tickerid, "240", ta.sma(f(_e), 3))` as `g(s6)`
  with `s6 = ta.sma(close, 6)`. Exit Signals: `a`, `b`, four decimals.
- `xa2_var_selection`: `var` declarations whose initializer is a `switch`
  (`var float first`, `var string tag` on the input `mode`, default `"B"`) or
  an `if` expression (`var int dir`), and a callable's `var float p = switch
  mode` read at `p[1]`: each is selected once, on the first bar. Exit
  Signals: `first`, `tag`, `dir`, `g`.
