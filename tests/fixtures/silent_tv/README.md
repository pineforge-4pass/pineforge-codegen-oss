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

Each exit Signal joins these fields with `|`, in this order:

- `cgs_int64_const`: `a`, `b`, `c`, `d`, `e`, `g`, `h`, `k`, `w`

What each tape shows:

- `cgs_int64_const`: Pine's `int` is 64-bit. `400 * 7200000` (`a`), `400 *
  MS` over `const int MS = 7200000` (`b`), `400 * step` over `int step = 2 *
  60 * 60 * 1000` (`c`), `int d = 300 * MS`, `var int e = 300 * MS`, the
  literal `3000000000` (`g`), `2000000000 + 2000000000` (`h`), `-300 * MS`
  (`k`) and `time + 400 * MS - time` (`w`) read 2880000000, 2160000000,
  3000000000, 4000000000 and -2160000000 on every bar, and the window `rel <
  400 * step` holds on every bar of the week, so every entry is placed.
