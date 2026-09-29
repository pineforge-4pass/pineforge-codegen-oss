# TAIL-F TradingView trade evidence

Synthetic probes written for lane TAIL-F (the constructs of two open library
chains the population imports: `TradingView/Request/3` with
`TradingView/LibraryCOT/5`, and `thequantscience/XGBoostMini/1`) and their
unedited TradingView trade exports. They contain no closed, scraped or
third-party source. Each was exported on 2026-09-29 with

```bash
lab tv --pine <name>.pine --slug pf-tail-f-<name, underscores as dashes> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-03
```

TradingView ran each from 2025-04-01 00:00 UTC to 2025-04-03 00:00 UTC (the
export's requested and returned range, `rangeProof: covered`). The tapes'
`Date and time` column is the exporting account's chart timezone,
Asia/Taipei (UTC+8). A probe enters on each :00 bar and closes on the :15
bar, the close's comment spelling the values it reads on that bar, so a
tape's exit `Signal`s are TradingView's values.

| Probe | sha256 of the `.pine` | Tape sha256 | Trades |
|---|---|---|---|
| `arm_commas` | `69c07c839f61ec40704391c0a4ffbb028ac757e125362e09015aa509dd80540b` | `f1ff8c39847abea6f68c4c58fe376ef5db7cf3955dabe906fb814d5e8c3d4e9a` | 41 |
| `arm_commas_decl` | `478dc88504526d865f33fc5974570bfdf19858336086767bb3bc1f52a85e44bf` | `66162fda112ecb3c28aebf4844ab851091fe5564372efb8f740d192804648cf0` | 41 |
| `array_ref_args` | `b0208225f3b6a9a8a30f70cc6ebaccf0d9610e9e6c2e070ea5c215d6fa8a7be9` | `ed3ef44f5b0bee31e9f4905961dc24b6d71bb95378856d87b5c677080065d426` | 41 |
| `array_ref_fresh` | `f7688de99fb628a22b863f445f963af0f5dc21cae11b2099dbf570bdf15d0236` | `dbdda31f12614235dbfa04eca2261b02e121afafcfbd4185b66ab5af168fe058` | 41 |
| `overload_qualifiers` | `f7096b6e8d6f29832feeef2a40ab095593658630f69940dd06dcd640b924b947` | `f57d06a1200d73d4e3537ff169288b45191b7a1bba2752313a97056bc5ee0e0d` | 41 |
| `barmerge_values` | `a83175b93dd01016b91ac1d62bfce72688498f2c4c5ab7f885b916baadb2c2be` | `66ba8fd3a105f694184644906e967e2bdc4908afe1d0386086e93a777d95d86e` | 41 |
| `block_local_types` | `af13f0c0ab095d5b2881df1a68052c9302bf00f0225073c16ee37cd99eff3237` | `2da7edfa18e8b0538c8b519f0d7486f9b097cd7e25ea7b10cd01493d5492d8ca` | 41 |
| `int_product` | `a60b3c382967056b8a4297f6ebfc5bc1b78711fa725830f23c2161284df17d36` | `774698ad0028cbd6e9ec996f9656ee56e2260ace7e2d7d4328c54bb74811cdc0` | 41 |

What each tape shows:

- `arm_commas`: a `switch` arm written on its `=>` line may join several
  statements with commas. TradingView runs them left to right (the state
  `(seq * 10 + 1) % 1000` reads 125 on the first exit, where the other order
  reads 1125) and the arm's value is the last statement's, an assignment's
  new value too; a default arm `=> runtime.error(...), ""` (or `, na`) that
  no bar takes stops nothing. The `if` arm's line joins statements the same
  way.
- `arm_commas_decl`: a declaration, typed or not, is one of those statements.
- `array_ref_args` / `array_ref_fresh`: a method's and a function's array
  parameter is the array the caller passes: a push through it reaches a
  variable's array and an object's field; a matrix row, a copy, an
  `array.from` and a function's new array are arrays no variable holds, and
  the push stays in them. `array_ref_args` also passes the array a function
  returns (`getHeld(held2)`, which returns its argument): TradingView's push
  reaches `held2` (its last field reads 6 on the first exit), which
  PineForge, holding arrays by value, refuses to copy. `array_ref_fresh` is
  the same script without that call, the one PineForge replays.
- `overload_qualifiers`: two overloads differing by their parameter's
  qualifier alone (`simple int` and `series int`): a literal, an input and a
  top-level declaration of an input call the simple one; `bar_index % 5` and
  `minute(time)` the series one. `../library_scripts/overloads_import.pine`
  calls the same overloads from the synthetic library `pftest/Overloads/1`.
- `barmerge_values`: `barmerge.*` constants held in variables and compared;
  one passed as the gaps of a `request.security` of another symbol behind a
  gate that never opens.
- `block_local_types`: two blocks declare `k` with different types (a loop
  counter and a string): each is its own variable.
- `int_product`: Pine's `int` is 64-bit: a Park-Miller step `(s * 48271) %
  2147483647` over a `var int` and over an `array<int>` element reads the
  exact sequence (2076553157 on the sixth bar, where a 32-bit product reads
  -767543190), and `3000000 * 1000` reads 3000000000.

`tests/test_e2e_tail_f_tapes.py` replays every tape but `array_ref_args`
(whose returned-array call PineForge refuses; the test pins the refusal) on
the corpus ETH 15m feed from 2025-04-01 00:00 UTC.
