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
| `cgs_series_var_init` | `22268f8545f9dc618b82f5b7a994d491087b64a15a440f69e3d74dac786deb52` | `fc1637dd1937347eac59ff1555abf0c5f3a987b2ae986a7baf1d06380067b1ac` |
| `cgs_series_var_init_len5` | `144b8249fa7fed72d03fc0f6bc13ebc8169ff714b40c145776ffe7399ec688a1` | `607e3a17f367bb21da812865b873c48b988c270da37734e19ecae2e51af7db99` |
| `cgs_block_var_init` | `4e262e03e4f7c0ce530fd9f7ac9396c2822cca2ea3db6bc06f28d0aaa2c726cc` | `ee3ecaaff6673e1b44531dfd4f6d9b3c126fbbf2afcdb538faf42835b0507f71` |

Each exit Signal joins these fields with `|`, in this order:

- `cgs_int64_const`: `a`, `b`, `c`, `d`, `e`, `g`, `h`, `k`, `w`
- `cgs_sec_var_len`: `h60`, `l60`, `k60`, `p60`, `q60`, `h240`, `hon`, `hChart`
- `cgs_series_var_init`, `cgs_series_var_init_len5`: `signal`, `signal[1]`,
  `first2[1]`, `firstClose[1]`, `seed[1]`, `fx[1]`
- `cgs_block_var_init`: `fc[1]`, `k[1]`, `fc`, `k`

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
- `cgs_series_var_init`: a `var` read with history initializes once, in
  statement order, on the first bar: `var int signal = direction.neutral`
  reads the field of the `Label` object declared before it (0), `var float
  first2 = b` the global `b = close * 2` of the first bar (3642.94, then + 1
  per bar), `firstClose` the first close, `var float seed = len * 1.5` the
  input's value (4.5 with `Len` at 3) and `var float fx = f(open)` the first
  open + 1. `cgs_series_var_init_len5` is the same script with the input's
  default at 5 (`seed` 7.5): the first probe run with `Len` overridden to 5
  replays its tape.
- `cgs_block_var_init`: a `var` read with history declared in a block
  initializes on the first bar that runs the block (the 03:15 UTC bar), and
  its history before that is na: `fc[1]` and `k[1]` read na there, `fc` the
  03:15 close (1833.75) and `k` 2.5. It trades 330 of the 336 closes (the block
  runs from 03:15).

