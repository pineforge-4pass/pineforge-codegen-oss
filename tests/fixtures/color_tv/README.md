# Colors as TradingView reads them back

Copied unchanged from the engine repository, branch `r5/w11-time-color` at
`4d43785d` (lane W11-ENG-TIME-COLOR), `tests/fixtures/color_tv/`, where
`tests/test_color_tapes.cpp` replays them through `include/pineforge/color.hpp`.
Here `tests/test_e2e_color_tapes.py` replays them end to end, through the
transpiler.

TradingView stores a color's transparency `t` (0 opaque, 100 invisible) as
the alpha byte nearest `255 * (100 - t) / 100`. `color.t` reads a byte `a`
back as `100 - a * 100 / 255`, rounded to the nearest whole number. A whole
`t` therefore reads back as itself (`color.new(c, 90)` is 90), and a
fractional one goes through the byte (`color.new(c, 10.5)` reads 11 and
`color.new(c, 20.5)` 20). The named constants are Pine v6's; v5 differs in
`color.red` (#FF5252), `color.teal` (#00897B) and `color.yellow` (#FFEB3B)
only.

Each directory is one `lab tv --no-note` export (channel `ws-report-v1`,
`rangeProof` covered), byte for byte: `strategy.pine`, `tv_trades.csv` (times
at UTC+8), `metrics.json` and `meta.json`, on BINANCE:ETHUSDT.P 15 from
2025-04-01 00:00 UTC. Each probe opens positions on even bars and closes them
on odd bars, and each exit's comment spells what the script read on the bar
before its fill:

- `w11-color-v6-eth15` and its `//@version=5` twin `w11-color-v5-eth15`:
  - `n0` / `n1` / `n2`: `r,g,b,t` of the seventeen named constants;
  - `t<k>:`: `color.t` of `color.new(color.red, k)`, `color.new(color.red,
    k + 0.5)`, `color.rgb(10, 20, 30, k)`, `color.new(#123456, k)` and
    `color.new(color.new(color.blue, 50), k)`, for `k = 0 .. 100`.

PineForge refuses a `//@version=5` script, so the v5 twin is replayed as its
global code calling the synthetic v5 library `pftest/W11ColorV5/1`, which
holds its color reads (`../library_scripts/w11_color_v5_import.pine`,
`../pine_libraries`).

| tape | range | trades | tv_trades.csv sha256 | strategy.pine sha256 |
|---|---|---:|---|---|
| `w11-color-v6-eth15` | 2025-04-01 .. 2025-04-04 | 576 | `a15d2aae0d9c4a4a234e9dd545fcee3b571076626515adfefd01d990096132a6` | `50c3b73997cfdab4ce949d84824f0cd4a4bcc2d4f189848b409002a481604f60` |
| `w11-color-v5-eth15` | 2025-04-01 .. 2025-04-04 | 576 | `6173b30fbb1a0f21ed49b98b1678b51ff8badf500cc171ec0ae3eadc13e5688c` | `bf2fda0670eb73d4bfc2998e2367747199939b2f0966b5f724c7722e00dcad66` |
