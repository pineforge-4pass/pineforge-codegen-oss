# CGINT6 TradingView trade evidence

Synthetic probes written for integration lane CGINT6 (lane CG-SILENT-2 picked
onto codegen main 76a5b26) and their unedited TradingView trade exports. They
contain no closed or scraped source. Each was exported on 2026-09-29 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran each from 2025-04-01 00:00 UTC (the export's requested and
returned range, `rangeProof: covered`). Every probe trades a fixed quantity
of 1, enters on the bars at minute 0 and 30 and closes on the bars at minute
15 and 45; each close's comment spells the values the probe reads on that
bar, so a tape's exit `Signal`s are TradingView's values, one sample per
close. The tapes' `Date and time` column is the exporting account's chart
timezone, Asia/Taipei (UTC+8).

| Probe | sha256 of the `.pine` | Tape sha256 | Exits |
|---|---|---|---|
| `cgint6_compositions` | `9060299ffa5365ebe25c9dc7fa344a9be30cc3d0195a023fb6344705bdafd55e` | `d6df61e99920940e52a5432c46ca5f82ed2c83f0c766450a04a8a248590faf8b` | 312 (entries from 12:00 UTC) |
| `cgint6_const_transp` | `aad36219d8e356a42a59e71f4a461693989711034e94a21b372a1f4f7b814b8c` | `a8762c7827ba7cdfc81084b8e218db31578598d3d47d058581eb120449ba685c` | 336 |
| `cgint6_var_source` | `8bbd7a20cde7cde72931edca509e2ffc8a753c27e608cda30e7dd39ac15fc149` | `b6979f2cffd498d25537e545cffcb74a395ed050639fa6dfaa28a89fbf238d65` | 312 (entries from 12:00 UTC) |

Each exit Signal joins these fields with `|`, in this order:

- `cgint6_compositions`: `ca`, `cb`, `cc`, `a0`, `a1`, `w`, `p`, `hh` (4
  decimals), `cnt`, `gv`
- `cgint6_const_transp`: `d0` .. `d16`
- `cgint6_var_source`: `src`, `s2`, `d`, `y`, `x` (4 decimals), `z`, `e` (4 decimals)

What each tape shows:

- `cgint6_compositions` meets each CG-SILENT-2 item with the main rule it
  shares a function with. A keyword transparency, fractional or not, reads as
  its positional twin through `color.new` and `color.rgb` (`ca`, `cb`, `cc`:
  item 1 with CG-SESSION-REPIN's double transparency). A run-time product past
  int32 beside a constant one keeps 64 bits on the chart (`w = q * 7200000 +
  400 * MS`) and in a `request.security` payload (`p = bar_index * 7200000 +
  400 * step`: item 3 with CGINT5a's constant fold). A helper called on its
  own result whose `var` TA length is a value of each requested bar gives each
  call its own state (`hh = u(u(close))`: item 5c with CGINT5a's helper var
  lengths). A helper's `var int n` counter reads 1, 2, ... on the requested
  bars while another function binds `n` to a 64-bit product (`cnt`, `gv`:
  item 3 with CGINT5a's helper state width). Every field but `a0` replays on
  every exit; `a0` is the constant transparency below.
- `cgint6_const_transp` shows how TradingView reads back a fractional
  transparency. A constant one given to `color.new` is truncated: `color.new(
  color.red, 10.5)`, `10.6` and `10.4` read 10, `99.5` reads 99 and `50.5` 50,
  positional or keyword, bound to a name, a `var` or an array element
  (`d0` .. `d5`, `d8`, `d9`, `d11`, `d12`, `d15`). An input (`d6`), a series
  (`d13`, `d16`) and `color.rgb`'s constant (`d14`) go through the alpha byte
  nearest 255 * (100 - t) / 100 and read 11, as the engine's `new_color` does
  for every transparency the codegen hands it as a double.
- `cgint6_var_source` re-checks CG-SILENT-2 item 5e on main: a `var` holding
  `input.source(close)` (and one holding `input.source(hl2)`) keeps the first
  bar's value, on the chart (`src`, `s2`, `d = src - src[1]` is 0, `y =
  src[2]`) and on the requested bars (`x = ta.sma(src, 3)`, `z = src[1]` at 60
  minutes read the first requested bar's close); `e = ta.sma(src, 5) - s2`
  reads the same constants (-1.155). PineForge reads every field but `e`,
  which it reads na: `precalculate()` computes the chart site over the `var`
  before any bar has run.
