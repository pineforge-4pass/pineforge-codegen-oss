# CGINT7 TradingView trade evidence

Synthetic probes written for integration lane CGINT7 (lanes TAIL-A, TAIL-E,
TAIL-F, TAIL-G and TAIL-H picked onto codegen main 90bfa0b) and their
unedited TradingView trade exports. They contain no closed or scraped source.
Each was exported on 2026-09-29 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to <end>
```

TradingView ran each from 2025-04-01 00:00 UTC (the export's requested and
returned range, `rangeProof: covered`). Every probe trades a fixed quantity
of 1. The tapes' `Date and time` column is the exporting account's chart
timezone, Asia/Taipei (UTC+8).

| Probe | End | sha256 of the `.pine` | Tape sha256 | Rows |
|---|---|---|---|---|
| `cgint7_int_products` | 2025-04-08 | `b47011c54ee3b110fda6adc6107017cfee9481fd1fe57acda7e3766d64cf3ede` | `a9ddf761111a7abca93d347017200aea4c11b82c87790e0b636e1373045dfc77` | 312 exits (entries from 12:00 UTC) |
| `cgint7_lazy` | 2025-04-05 | `ce043ca7e55e6ede18b4b390bb1af630340c08879769e01760edde66fb181995` | `c88781858929975242e28bef47476c6e8d18a601b010ae48f602f3a3908305ec` | 384 (one per bar) |

- `cgint7_int_products` enters on the bars at minute 0 and 30 and closes on
  the bars at minute 15 and 45; each close's comment joins `pm`, `ag`, `w`,
  `r`, `dv`, `dd`, `zz`, `fl`, `p`, `q2`, `pr` with `|` (the probe's header
  names each). TradingView computes every integer exactly in 64 bits: a
  Park-Miller step over a `var int` and over an int array's element, a product
  past int32 whether a `%` or `/` reads it or not, an input's, a function
  parameter's, an `na` operand's (`NaN`), and in a 60-minute payload the
  history of a global holding `bar_index * 86400000` (`p`, past int32 from the
  second day), of a script variable's product (`q2`) and a product a `%`
  reads (`pr`). Main 90bfa0b does not compile the probe (the payload's `g[1]`
  indexed a scalar) and, without `p` and `q2`, reads `ag` wrapped on 312 of
  312 exits; TAIL-F's head reads `w`, `zz` and `pr` wrong (285-312 of 312);
  the picks before CGINT7's composition read `p` and `q2` wrapped (283 and 285
  of 312).
- `cgint7_lazy` spells on every bar the last values of `ta.roc` / `ta.change`
  in an if block whose head reads `t(bar_index)[1]` below a lazy `and`, of
  `ta.mom` in a value-form if and a nested if whose heads call `isNew(s) =>
  inS(s) and not inS(s)[1]`, and of a ternary whose head reads the lazy call
  history beside a lazy `ta.change`: `bar|r|c|v|m|b|e`, flat bars as the
  entry's name and bars holding the position as the close's comment. Main,
  TAIL-E's head and TAIL-G's head each read some of `r`, `c`, `v`, `m`, `b`,
  `e` differently (TAIL-G's head: `r`, `c` on 378 of 384 rows; TAIL-E's
  head: `r`, `c` on 378, `m`, `e` on 284, `b` on 95): the fields need both
  rules.
