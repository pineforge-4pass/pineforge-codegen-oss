# request.security payload source series: TradingView trade evidence

Synthetic probes written for lane CG-W9-SEC (`request.security` payloads
reading a source input and the derived bar series) and their unedited
TradingView trade exports. They contain no closed or scraped source. Each was
exported on 2026-09-28 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran each from 2025-04-01 00:00 UTC (the export's requested and
returned range, `rangeProof: covered`). Every probe enters on the bars at
minute 0 and 30 and closes on the bars at minute 15 and 45; each close's
comment spells the values the probe reads on that bar, so a tape's exit
`Signal`s are TradingView's values, one sample per close (265 trades each).
The tapes' `Date and time` column is the exporting account's chart timezone,
Asia/Taipei (UTC+8).

| Probe | sha256 of the `.pine` | Tape sha256 |
|---|---|---|
| `w9sec_src_payload` | `ff624516777bbf4c405bc2c2943d7aa0bca95d11862908156e94d215f0560277` | `c4b7175088eb8328ddd4f48edf720f8a4836e551c9549f17eb42fb2ecca87cd9` |
| `w9sec_derived_hist` | `e19b93bac11fa83d1c076bea7f7d942aacc7327409a4662ca09032f23a543af9` | `f6ebff0b3727da443c9fa2b0ccb3d8ebe1445f6a70591309dc182acfc889a659` |
| `w9sec_hlcc4_hist` | `faa87f344296a738ce3aa4a0a21a960c1025f0c9de6bcf77d1880a0567ec4c1b` | `e307304e6779e12195930e2d165396a972b85e795f5be5ad001d1a68bd99a712` |

Each exit Signal joins these fields with `|`, in this order:

- `w9sec_src_payload`: `v`, `h1`, `h2`, `r`, `b`, `d`
- `w9sec_derived_hist`: `a`, `b`, `c`, `e`, `f`
- `w9sec_hlcc4_hist`: `a`, `b`, `c`

What each tape shows:

- `w9sec_src_payload`: `src = input.source(ohlc4, "Source")` read in a
  60-minute payload is the requested bar's ohlc4 (`v`); `src[1]` / `src[2]`
  are the ohlc4 of the requested bars before it (`h1`, `h2`), `ta.rsi(src,
  3)[1]` runs on the requested bars' ohlc4 (`r`), and so do `nz(src[1])` and
  `math.max(src, open)` under builtin calls (`b`). A daily payload's `src[1]`
  is the ohlc4 of the day before the last completed day (`d`).
- `w9sec_derived_hist`: `hl2[1]`, `hlc3[2]`, `ohlc4[1]` in a 60-minute
  payload, directly and under `nz` / `math.min`, and `ohlc4[1]` in a daily
  one: each derived series' history belongs to the requested bars.
- `w9sec_hlcc4_hist`: `hlcc4[1]` and `hlcc4` in a 60-minute payload, and
  the chart's own `hlcc4[1]`.
