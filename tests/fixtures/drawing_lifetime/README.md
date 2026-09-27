# Drawing lifetime tapes (lanes W8B-SIGSTATE-2 and CG-W9-MISC)

Which drawings does TradingView delete, and what does a deleted one read? Each
directory is one `lab tv --no-note` export (channel `ws-report-v1`,
`rangeProof: covered`, BINANCE:ETHUSDT.P 15, 2025-04-01 .. 2025-05-01), byte
for byte: `strategy.pine`, `tv_trades.csv` (times at UTC+8), `metrics.json`,
`meta.json`. The probes are synthetic, written for these lanes; no closed or
scraped strategy is involved. Every readout is an entry whose comment carries
the values the script read on its bar (the entry fills at the next bar's open).
The `w8b-*` tapes are lane W8B-SIGSTATE-2's (copied from
`exec/W8B-SIGSTATE-2-scratch/synth/tapes/`), the `w9dg-*` tapes lane
CG-W9-MISC's.

```sh
source ~/code/pineforge-workflow/campaign/env.sh
lab tv --pine <probe>.pine --symbol BINANCE:ETHUSDT.P --interval 15 \
  --from 2025-04-01 --to 2025-05-01 --out <dir>/<tape> --slug <tape> --no-note --json
```

The rules, and the tapes that pin them:

1. **A deleted drawing reads like a na handle.** Every getter returns na, every
   setter does nothing (`box.set_top` / `line.set_y2` after the delete still
   read na) and `na()` is true: `w8b-box-evict-readout` (`deleted:NaN`),
   `w8b-box-delete-setter`, `w8b-line-delete-collect`,
   `w8b-drawing-na-after-delete` (`deleted:1100`). A getter of a na handle
   reads na too (`w8b-box-evict-setter`: D is still na at `set1` / `set5`).
2. **The collection.** With `max_<kind>_count = N`, the live count of a kind
   may reach N + 5; the new drawing that makes it N + 6 deletes the oldest
   collectable drawings of that kind until N remain, and each reads na as in
   1. `w8b-box-gc-steps` (N = 100: 0 na through box 105, then n - 100 at
   n = 106 + 6k), `w8b-box-gc-cap100`, `w8b-box-evict-timing` (N = 5: the box
   an array holds dies when the eleventh is drawn), `w9dg-label-collect2`
   (labels alike), `w9dg-default-cap` (N omitted: 50, the box dies at 56).
   Linefills are never collected: `w9dg-default-cap`'s 128 linefills, all
   in an array, never read na.
3. **Which drawings are collectable.** A `var` holds its drawing, wherever it
   is declared (`w8b-box-evict-readout2`, `-readout3`: a `var box` after 1000
   more boxes on a 100-box chart, `-setter`, `-array`, `-timing`;
   `w9dg-pin-holders3`: a function's `var`). A drawing held only by an array,
   an object field (`w9dg-pin-holders3`: U, W), a local of an `if` block, a
   function or a `for` loop (`w9dg-pin-locals`), or a plain non-var global,
   even one that holds it when the collection runs (`w9dg-pin-holders3`: P),
   is collectable, as is one a `var` held before it was reassigned
   (`w9dg-pin-holders3`: O). A non-var variable whose history is read holds
   its drawing (`w9dg-pin-holders2`: S, carried by `s := s[1]`).

| tape | rows | readouts | tv_trades.csv sha256 |
|---|---:|---|---|
| `w8b-box-evict-readout` | 8 | `five:111`, `six-samebar:111`, `six-nextbar:111`, `deleted:NaN` | `d08ae8ee7be3374c9197ad305d47c5815b84c18a9bd9845c00c8a345c2cc64af` |
| `w8b-box-evict-readout2` | 8 | A/B (var) read 111/222 up to 40 bars after leaving the five newest | `3d02151a0f55e71619eb6d89c5270c118c3cc5a4f804b39e0ac77fea1c251050` |
| `w8b-box-evict-readout3` | 14 | A (var) reads 111 after 99 .. 1000 more boxes (N = 100) | `e2e25cc33072ca5e3504ff48105c13c12d00b63935c1423032aac2408cc28d9e` |
| `w8b-box-evict-setter` | 8 | var A/B extended or not, D na then 444 | `7fc9a243de3b148a06f42a909f31f885838df082638e4ec78a64633c81242c39` |
| `w8b-box-evict-array` | 8 | array-only B dies (`111/NaN/111`) | `19185bb44e18a9abeac3da17f3b76e9ed1dbe9eb0fad2acbd3be7d0da3e99bc1` |
| `w8b-box-evict-timing` | 18 | B dies on the eleventh box (`111\|NaN\|333`) | `4cd7162b9b32f22eadaf14cf41e04d6eaaef624389bb3848ac04c2fc91ac4f5a` |
| `w8b-box-gc-cap100` | 62 | boxes 100-102 read at n = 201, na at 202 | `4761d022317d44aa405bf71e13a665845ff5365222a3e404e3d6ff28daf83538` |
| `w8b-box-gc-steps` | 120 | dead count n - 100 at n = 106 + 6k | `daab96f7e1c0330f50b06a2d6100bb72f7e47746ac3e901f1600b4840929971e` |
| `w8b-box-delete-setter` | 8 | deleted D stays `NaN/NaN` under set_top; collected E alike | `736d96419c44fcb1440b91ef6ef41be4508a9bf67e71f9b2f446a0ff62d80d34` |
| `w8b-line-delete-collect` | 6 | deleted D and F, collected E: `NaN/NaN` | `1006d86d8d454c0c15f74f55b1e5d6230a34721f4468f1aa949809fe14a6e80b` |
| `w8b-drawing-na-after-delete` | 6 | `new:0000`, `deleted:1100`, `collected:1111` | `801d0b635a79dd1358152298fee10649366a76f5069376cae7fcaead962f0dd5` |
| `w9dg-pin-holders3` | 6 | `first:NaN/NaN/103/NaN/NaN/NaN/107` (U P F Q W O R) | `ad3ea87137d949f6e3536ebe9cf8f3900419e107cd7de170fca24f58da0f9c1c` |
| `w9dg-pin-locals` | 6 | block, function, loop locals and a bar-scoped global: all na | `f3d3ae8ae6e9a7fcb8cf25fd0110d7323cb11d368ccd5b943a6ed830c8e9b9f2` |
| `w9dg-label-collect2` | 10 | label B dies on the eleventh label (`111\|NaN\|333\|NaN`) | `c3aed04061372459d1eaa875d6db6b537ff522fee7278c52ef8b0de804e7e73b` |
| `w9dg-default-cap` | 28 | boxes `56:6/0` .. `128:78/0`; linefills never | `2bed4e6d437fdb1d4f0695648539b8ef8d7b203a2a7920102cf9824cdc520189` |
| `w9dg-pin-holders` | 6 | also a `map<int, box>` value: collected (evidence only) | `7098c7ae594890b2144947a90af4aea28dbcaa001bea0baa94a4e8f6ac041aeb` |
| `w9dg-pin-holders2` | 6 | S (`s := s[1]`) held: `first:NaN/102/103/NaN/NaN/107` (evidence only) | `684f00bdd64d614ecd17c944b6f134ea599bf4b62fdafc4a8232f85b84cbd144` |
| `w9dg-label-collect` | 10 | a collected label's text is na (evidence only) | `3ae3510e142d37efa99ce2b2479980cdd179bc5e276a6d2655941de42162d48e` |

The three evidence-only tapes do not replay through PineForge today: a
`map<int, box>` is outside the supported map subset, a non-var drawing whose
history is read declares as `Series<double>` and does not compile, and `na()`
of a string does not compile. `-holders3` and `-label-collect2` are their
replayable variants.

`tests/test_e2e_drawing_lifetime.py` replays the fifteen replayable tapes end
to end -- `transpile_json`, the built runtime, `run_strategy.py` over the corpus
15m feed from 2025-04-01 00:00 UTC, where TradingView's chart starts, so
`bar_index` is the tape's -- and requires every trade and every readout value
to be TradingView's. The values come back through `@pf-trace` lines appended to
the source, each evaluated only on its readout's bars. The pre-lane build
(codegen `d7e095f`) misses thirteen of them: it reads a deleted or evicted
drawing's data, never calls a deleted drawing na, evicts at exactly the cap and
halts on `box.get_top` of a na box (`w8b-box-evict-setter`); the two tapes that
only pin `var` drawings pass on both builds.
