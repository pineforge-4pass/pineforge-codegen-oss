# CG-SILENT-2 TradingView trade evidence

Synthetic probes written for lane CG-SILENT-2 and their unedited TradingView
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
| `cgs2_color_keywords` | `8b6fe869cc75746d67ebac83a3afe5e587cc78a788b83080357d1895aa2af4e3` | `c4eae9c1135d0720d8a228886017a17aedad3ad9ef2ab3ffe718411b205a5b27` |
| `cgs2_untyped_params` | `4c2fe6cb226387a3aee28d1267c843f1d9624b0185e423785fbc454a64f7f5ac` | `79c59e1642d32ad94757089076607e333fb971218b54a54d61f2e334e1e70179` |

Each exit Signal joins these fields with `|`, in this order:

- `cgs2_color_keywords`: `c1` .. `c7`, `p1`, `p3`, each `red,green,blue,transparency`
- `cgs2_untyped_params`: `a`, `b`, `c`, `d`, `e`, `f`, `g`, `h` (4 decimals), `k`, `w`

What each tape shows:

- `cgs2_color_keywords`: a keyword argument of `color.new`, `color.rgb` and
  `color.r` / `g` / `b` / `t` reads as the positional argument of its
  parameter, whatever order the keywords are written in: `color.new(#1E90FF,
  transp = 40)` (`c1`) equals `color.new(#1E90FF, 40)` (`p1`), and
  `color.rgb(10, 20, 30, transp = 80)` (`c3`) equals `color.rgb(10, 20, 30,
  80)` (`p3`) on every close. The transparencies are multiples of 20, which
  the engine's 8-bit alpha round-trips exactly.
- `cgs2_untyped_params`: a function whose parameter has no declared type
  keeps each call's argument type: `s(x) => str.tostring(x)` reads "5" for
  `s(5)` and "2.5" for `s(2.5)`, `twice(x) => x * 2` 6 and 2.5, `pad(r)`
  over `r = close / 1000.0` the fraction (`01.82638`), `frac(x) => x -
  math.floor(x)` 0 and the fraction, and `wrap(y) => twice(y) + 1` 5 and 2.5
  through the forwarded parameter.
