# CG-ARRAY-HIST TradingView evidence

Synthetic probes written for lane CG-ARRAY-HIST: the history-referencing
operator `[]` on an array or a matrix, and an int value given to a float
field of a user-defined object. They contain no closed or scraped source.
The eight tapes were exported on 2026-10-01 with

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
| `ahist_ref` | `67211a289cace62d2e2aa18110103983ecdbdb25324aa3827464fc170b95a8ec` | `86e562fa9f5df457ef9fe30a1bbf1960c7dd03c0a2565cecab6a615bd391677a` |
| `ahist_mtx` | `67a0ff3e20170223c7d8d8516fd0814c7e26e598b031a6195868a633c49561b8` | `fcae222cfe0f6607aa6dd3e0639237f3b712df1e2c13964e17ddb28bdac28892` |
| `ahist_field` | `7affefa82244e8c4680a41e16e8b337d45b7d52c95d94e834c861d2b4106ff1f` | `b38032c0346fd3be6b51a8fcfe340cf1d91fe18c212b4c854fafe884a0d270f4` |
| `ahist_loop` | `4a47cf52a041d1ed87eeddacaa9406195bf7c3c1b806febde3be35e2ba232aa4` | `be73d218de164323058c17f642f44d619b6615d9a851129a5f934c28abc23839` |
| `ahist_more` | `b351e596dbf3f1bc756fdeefe5f5a57f79247bde60182c7488039fbf325ab314` | `565a391ec1f1ceae5d04ccb4e47ac261c244dd25fceb2807d4263323b0bfa52c` |
| `ahist_fn` | `4082e3be4e42ca68ba7b500a56d0849a49213cf89d932387abf1817f87d50837` | `792b12925ab371208a10dbaa3e47e033e1854037b92c8eb2947ecbd41ce2e5be` |
| `ahist_method` | `fc3e1686e7493515fb4813abd4ddff475d36d8adcec010beb4f31e9ad01a6717` | `a248251a257aab65d63a5b0a57b7652eb9e4be09926d49ebd524cbfcdc64a217` |
| `uctor_float` | `c98b31de4b8db20c2cac9426cc58b2a31e98965c989b693e4365195265212916` | `0184c3fb8a603bbd6baa4726a0f7341bd97a5110605ae1c8034c1f96e6604918` |

Each exit Signal joins these fields with `|`, in this order:

- `ahist_ref`: `d1`, `n2`, `g1`, `s1`, `w1`, `lp`, `cy`, `mb`, `firstNa`
- `ahist_mtx`: `vd`, `g1`, `g2`, `r1`, `s1`, `firstNa`
- `ahist_field`: `f1`, `f2`, `f3`, `f4`, `v1`, `v2`
- `ahist_loop`: `l1` .. `l6`
- `ahist_more`: `t1`, `t3`, `t5`, `t6`, `t7`, `t9`, `t11`
- `ahist_fn`: `a1`, `a2`, `l1`, `m1`, `c1`, `blk`
- `ahist_method`: `m1`, `k1`
- `uctor_float`: `d1`, `n2`, `w2`, `d3`, `d4`

What each tape shows (the fields of the bars below `bar_index` 2 or 3 are the
probes' `-1` placeholders):

- `ahist_ref`: the history of an array is a copy of the array as it was at
  the end of the bar it names, not the array the variable holds now. A `var`
  array that grows by `bar_index` before the reads and by `-1` after them
  reads, through `va[1]`, one element fewer than `va` (`d1` is 1) and the
  previous bar's last element (`n2` is -1): its history is the array as the
  previous bar left it. A fresh array per bar reads the previous bar's
  (`g1`: `bar_index - (b[1]).get(0)` is 1, `s1`: one element), a variable
  reassigned on its bar keeps the last array of the bar (`w1` is 2), an
  array or na keeps either (`mb`), and `na(b[1])` is true on the first bar
  (`firstNa`). A variable bound to its own history on every other bar reads
  the previous bar's array (`cy` is 1). `for v in b[1]` iterates the array
  `b` holds now (`lp` is `bar_index`; `ahist_loop`).
- `ahist_mtx`: a matrix alike. A `var` matrix set to `bar_index` every bar
  reads the previous bar's value through `vm[1]` (`vd` is -1); a fresh
  matrix per bar reads the previous bar's (`g1` is 1) and the one two bars
  back through `matrix.get(mm[2], 1, 0)` (`g2` is 2); `rows()` and
  `matrix.columns()` read its shape (`r1` is 21) and a `matrix<string>` its
  strings (`s1`).
- `ahist_field`: an array or a matrix held by a user-defined object, read
  through the object's history, is the field's array as it is now: the object
  is a reference (fixtures/udt_history_tv). A fresh object per bar reads the
  previous bar's array and matrix (`f1` is 1, `f2` 1, `f3` and `f4` 0), and a
  `var` object whose array grows and whose matrix is set every bar reads them
  as they are now through `hv[1]` (`v1`, `v2`: 0).
- `ahist_loop`: a `for...in` loop over an array's history iterates the array
  the variable holds now: `for v in b[1]` and `for v in b[2]` sum the current
  `[bar_index]` (`l1`, `l3`), `for [i, v] in b[1]` alike (`l4`), and `for v in
  va[1]` counts the current elements of a growing `var` array (`l5`:
  `bar_index + 1`). A variable bound to the history (`bb = b[1]`, `l2`) and
  `array.copy(b[1])` (`l6`) iterate the previous bar's array.
- `ahist_more`: an array changed after its declaration reads its end-of-bar
  copy (`t1`: two elements); an if block's local, read before the block
  changes it, reads the copy the block's previous run left (`t3`: two
  elements); a dynamic offset reads the current array at 0 (`t5`); a variable
  bound to the history and a function given the history read the copy (`t6`,
  `t7`); a `var` array rebound to a new array on every fourth bar reads the
  previous bar's (`t9`); and an array of objects reads, through its history,
  the objects it held as they are now (`t11` is 0).
- `ahist_fn`: in functions, the history of an array or a matrix parameter, of
  a function's local array and of a call's new array is the previous call's,
  as a copy (`a1`: `bar_index - 1`; `a2`: a `var` array passed every bar reads
  one element fewer through `x[1]`; `l1`, `m1`, `c1`); an if block's local
  array reads its block's previous run (`blk`).
- `ahist_method`: a method's array receiver: `this[1]` is the receiver of the
  previous call (`m1`), at a call on every other bar too (`k1`).
- `uctor_float`: an int given to a float field is that number: the bar index
  by keyword (`d1` is 0), an int variable by position, na on every third bar
  (`n2`: `na` there, else 0), int arithmetic by position (`w2` is 1), an
  epoch (`d3` is 0) and a function's int parameter (`d4` is 0).

## Spellings TradingView refuses

TradingView's pine-facade compiler (`save/new_draft`, 2026-10-01) refuses
these probes, so they have no tape:

| Probe | TradingView's answer |
|---|---|
| `ahist_noparen` | `CE10011` "User variable identifiers should not contain '.'" at the `.` of `a[1].size()` |
| `ahist_m_noparen` | `CE10011` alike, at the `.` of `m[1].get(0, 0)` |
| `ahist_field_noparen` | `CE10290` "Cannot use the history-referencing operator on fields of user-defined types. Reference the history of the object first by enclosing it in parentheses, and then request the field, e.g. "(object[1]).field" instead of "object.field[1]"." at `h.xs[1]` |
| `ahist_field_paren` | `CE10290` alike, at `(h.xs)[1]` |
| `ahist_mfield` | `CE10290` alike, at the matrix field's `h.m[1]` |
| `ahist_eq` | `CE10123` "Cannot call "operator ==" with argument "expr0"="a". An argument of "array<float>" type was used but a "simple string" is expected." (and the same for `a[1]`) |

TradingView answers `CE10010` for a method straight after the history of an
object or a drawing (fixtures/udt_history_tv `udth_method_noparen`) and
`CE10011` for one after the history of an array or a matrix.

## What TradingView stops on

These probes compile; TradingView's run stops with a runtime error on the
bar named (the export fails with TradingView's own `request_error`):

| Probe | sha256 of the `.pine` | TradingView's answer |
|---|---|---|
| `ahist_push` | `9875c2a5ece543b942e25dff9172d49aaccc966aa72d2649f41995fa00e7902c` | `RE10051` on bar 1, line 7 (`(a[1]).push(1.0)`): "Cannot modify the elements of a historical array or any slices of that array. Instead of modifying an array referenced by an ID retrieved with the `[]` operator, create a shallow copy of the array with `array.copy()`, then modify the copy or a slice of that copy." |
| `ahist_push_ns` | `74411ff6e4c2e76d22d740f496855adf8d7cc6d17788391e83ed2449f5298a17` | `RE10051` on bar 1, line 7 (`array.push(a[1], 2.0)`) |
| `ahist_bound_push` | `866c7940d63d403e5da523170ccf70e19bdd10492dfa6e0ae024f0c4d55ce45c` | `RE10051` on bar 1, line 8 (`b.push(1.0)` after `b = a[1]`) |
| `ahist_mset` | `2db4e4acccdde8a96e0217e1f72bdf377ba7568e1974ab3c47132c4a19982637` | `RE10051` on bar 1, line 7 (`(m[1]).set(0, 0, 1.0)`), the same text |
| `ahist_na_size` | `f767e93afdad915cc9648f4228ddb8b32452f303018733b1fd90b840a1bbd801` | `RE10052` on bar 0, line 6 (`(a[1]).size()`): "Cannot call array methods when id of array is na." |
| `ahist_na_rows` | `60e4feaccf371c382b3ad677827e85e1dc799a48efc5a153e08a04cf0411f93b` | `RE10053` on bar 0, line 6 (`(m[1]).rows()`): "Cannot call matrix methods when id of matrix is na." |
| `ahist_slice_set` | `c72a7b76eb6caf66f95a1d3dadb09f800ebd3ea4d5302b820c28049d73c046b0` | `RE10051` on bar 1, line 8 (`s.set(0, 5.0)` on `s = (a[1]).slice(0, 1)`) |
| `ahist_param_push` | `da1d2d4e2a2097cb718162ad4e465131932de89530dff9d3c1b6e09c41e9bdba` | `RE10051` on bar 1, in `f` at line 6 (`y.push(1.0)`), called with `f(a[1])` at line 10 |
| `ahist_var_push` | `9627f7d244513905af02a51d64583f50f522ec9542afca0cdada22473f3ba899` | `RE10051` on bar 1, line 8 (`(a[1]).push(1.0)` on a `var` array) |

These run: a copy of the history is an array of its own, which the script may
change (`ahist_copy_push`, `9f833cb564cc333939fc3d1bed80bd7f917284d198c27bc9426e83173278b5f5`:
`array.copy(a[1])`); `(a[0])` is the array itself (`ahist_zero_push`,
`cb7ef91fa3c200337595fa91d12ab508d8103d950bf3d438ab025e735ee84151`); and an
array field of an object read through the object's history changes like any
other array (`ahist_field_push`,
`78751e650ddea44d276fd6a40afc0dfdac9bfec0a3081374dec71167a27a07d7`:
`(h[1]).xs.push(1.0)`).

The Pine v6 User Manual agrees that the history operator applies to arrays
("Arrays", section "History referencing": scripts "interact with past array
instances previously assigned to a variable").
