# CG-SILENT-2 TradingView trade evidence

Synthetic probes written for lane CG-SILENT-2 and their unedited TradingView
trade exports. They contain no closed or scraped source. Each was exported on
2026-09-28 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

(the follow-up probes `cgs2_int64_followup`, `cgs2_int64_followup2`,
`cgs2_float_time_method` and `cgs2_array_loop_bool_copy` on 2026-09-29).
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
| `cgs2_int64_products` | `4d9d562f85b4c71c1c834f9f05e224167b025d8e5c6b2044edd59bc9c470aaa7` | `722e13660813c2f2f1c77681e859a4d6bd4fc638b80f3cc0149382e2197411fc` |
| `cgs2_int64_followup` | `0a10d5b45a2399d6c98f30f9bf7381f1e151457b6d7987e593d7ba057a303ef7` | `0a274b52b4bad2cac32ba8053d46f0905586817839d4ee55390a0583d6c322d9` |
| `cgs2_int64_followup2` | `4b94979a31143cb9a24d7c8adcd236bf4dbc20320983931e0caad8c313b5030e` | `b9f4759b0b330aca7b12762a9100d5e36735805b8aa17e400191823951701ca2` |
| `cgs2_float_time_local` | `f0dc91a0e879e824cdfd27d6d64aec9ac204b355db7a2cf31e7d1004906913fe` | `81a90b86cc23133dae6ab1e7313aecbf7363adb7603317b2ce714765fb9c051e` |
| `cgs2_float_time_method` | `8e6fd4cf5ddf2bf55a74bd244e9fcb951d088f686864bae20ef9c786ea884b82` | `81a90b86cc23133dae6ab1e7313aecbf7363adb7603317b2ce714765fb9c051e` |
| `cgs2_color_arrays` | `1eed73a34e1bddf73e0272a7cf5b5fb1b00cf72e5986a4c9b694e1ff0f422a15` | `3210a6c6870137852c358d123a6c6a5fa535aab0b7a15922bddb5e54351598fa` |
| `cgs2_negated_constants` | `82ce0a41a9c6fecd10d5877aa17a6e45b310d447192c4f568cb63d5eaa2137a1` | `1f68c782f58f08ba3492b1bc1fed6188a73b79a28349b5c9dc346119b8758a46` |

Each exit Signal joins these fields with `|`, in this order:

- `cgs2_color_keywords`: `c1` .. `c7`, `p1`, `p3`, each `red,green,blue,transparency`
- `cgs2_untyped_params`: `a`, `b`, `c`, `d`, `e`, `f`, `g`, `h` (4 decimals), `k`, `w`
- `cgs2_int64_products`: `a`, `e`, `bar_index`, `b`, `cd`, `f`, `p`, `q`, `u.v`, `u.big()`, `u.konst()`
- `cgs2_int64_followup`: `s1`, `ms`, `x / 1000000000`, `x < 0` (`neg` / `nonneg`), `e / 86400000`, `yq / 1000000` (`na` for an na value)
- `cgs2_int64_followup2`: `s1 / 10**9`, `s3 / 10**9`, `e / 86400000`, `x - 3000000000`, `y / 10**9`
- `cgs2_float_time_local`: `firstD`, `firstE` (the first bar's `f()` and `g()`), `d`, `e`
- `cgs2_float_time_method`: the same of the methods `u.dt()` and `u.dc()`
- `cgs2_color_arrays`: `A[0]`, `A[1]`, `B[0]`, `D[0]`, `E[0]`, `F[0]` (each `red,green,blue,transparency`), the four sizes' sum
- `cgs2_negated_constants`: `-NEG * 2`, `+NEG`, `-FNEG`, `-NEG`

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
- `cgs2_int64_products`: Pine's `int` is 64-bit. `days * 86400000` over
  `days = input.int(30)` reads 2592000000 (`a`, and `int e`), `bar_index *
  7200000` passes int32 at bar 299 and reaches 4831200000 on the last close
  (`b`), `time - days * 86400000` is 30 days back (`cd`), `ms(n) => n *
  60000` over `days * 1440` 2592000000 (`f`), a tuple's `3000000000` and
  product keep theirs (`p`, `q`), and so do a UDT's `int v = 3000000000`,
  `this.v * 2` (6000000000) and a method selecting `3000000000`.
- `cgs2_int64_followup`: a float array's elements times 1000 sum to 4500
  and a map's float value times 252 is 63 (`s1`, `ms`: a loop element is no
  32-bit int); an int that is na at hours divisible by 3 makes `n *
  1000000000` na, and never negative (`x`, and a function's `int` result
  `e`), else the hour times 10**9; a 60-minute request of such a global
  times 10**6 is na where the global is (`yq`).
- `cgs2_int64_followup2`: an int array's elements 3, 5, 7 and a map's int
  value 4 times 10**9 (15 and 4 after dividing back), a function's declared
  `int d = days` times a day (30), a helper's `n * 1000` over 3000000 read
  in a function whose float parameter is also `n` (1.5 over 3*10**9), and a
  60-minute request of `int hh = hour(time, "UTC")` times 10**9 (the hour).
- `cgs2_float_time_local`: `f() => float x = time; x - x[1]` is na on the
  first bar (`firstD`), where `x[1]` does not exist, and the bar spacing
  (900000) afterwards; `g()`'s explicit `na(y[1])` test reads -1 there.
- `cgs2_float_time_method`: the same of a method's locals (its tape is
  byte for byte `cgs2_float_time_local`'s). TradingView refuses a method
  that does not read its first argument, hence the probe's `this.v * 0`.
- `cgs2_color_arrays`: arrays of colors built by `array.from`, declared
  `array<color>` or not, from literals, `color.new` / `color.rgb` calls,
  color variables and a conditional color, hold every element's channels
  and transparency.
- `cgs2_negated_constants`: a sign over a negative constant (`NEG = -5`,
  `FNEG = -2.5`) reads 10, -5, 2.5 and 5.
