# CG-SILENT TradingView trade evidence

Synthetic probes written for lane CG-SILENT and their unedited TradingView
trade exports. They contain no closed or scraped source. Each was exported on
2026-09-28 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran each from 2025-04-01 00:00 UTC (the export's requested and
returned range, `rangeProof: covered`). Every probe trades a fixed quantity
of 1, enters on the bars at minute 0 and 30 and closes on the bars at minute
15 and 45; each close's comment spells the values the probe reads on that
bar, so a tape's exit `Signal`s are TradingView's values, one sample per
close (336 trades each). The tapes' `Date and time` column is the exporting
account's chart timezone, Asia/Taipei (UTC+8).

| Probe | sha256 of the `.pine` | Tape sha256 |
|---|---|---|
| `cgs_int64_const` | `1f34842025040d843af4f9ceeffdb0ac511be7709caa46671d0ed069a8eaf423` | `248797fe3664df23238beb09bf99dfccb54068036b6ed69010912bbe6b0c9b16` |
| `cgs_sec_var_len` | `bbfd2f23bf0e2afba662436a49a92990eaee9844f76b9f9e09a92bd873c2a4e6` | `b6d62e8aa55f93bccbf5651e6babe900fff22ccaa09cb25e502623a156b968db` |

Each exit Signal joins these fields with `|`, in this order:

- `cgs_int64_const`: `a`, `b`, `c`, `d`, `e`, `g`, `h`, `k`, `w`
- `cgs_sec_var_len`: `h60`, `l60`, `k60`, `p60`, `q60`, `h240`, `hon`, `hChart`

What each tape shows:

- `cgs_int64_const`: Pine's `int` is 64-bit. `400 * 7200000` (`a`), `400 *
  MS` over `const int MS = 7200000` (`b`), `400 * step` over `int step = 2 *
  60 * 60 * 1000` (`c`), `int d = 300 * MS`, `var int e = 300 * MS`, the
  literal `3000000000` (`g`), `2000000000 + 2000000000` (`h`), `-300 * MS`
  (`k`) and `time + 400 * MS - time` (`w`) read 2880000000, 2160000000,
  3000000000, 4000000000 and -2160000000 on every bar, and the window `rel <
  400 * step` holds on every bar of the week, so every entry is placed.
- `cgs_sec_var_len`: a helper-local `var` read as the length of `ta.highest`
  / `ta.lowest` inside `request.security` holds its own value on each call
  site's requested bars: `var int c = -1; c += 1` with `c % 5 + 1` on the
  60- and 240-minute bars (`h60`, `h240`) and on the chart (`hChart`), `n :=
  n + 1` (`l60`), a `var` never reassigned (`k60`), `p += 2` over `int p = 3`
  (`p60`, a length of 5) and a local an if arm rebinds on the requested bars'
  candles (`q60`). With `lookahead_on` (`hon`) TradingView reads the completed
  requested bar from its first chart bar, which the engine reproduces under
  its `historical_security_lookahead_projection` run flag.
