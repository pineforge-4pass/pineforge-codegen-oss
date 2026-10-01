# CG-UDT-HIST TradingView evidence

Synthetic probes written for lane CG-UDT-HIST: the history-referencing
operator `[]` on a user-defined object or a drawing reference. They contain no
closed or scraped source. The nine tapes were exported on 2026-10-01 with

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
| `udth_ref` | `5178eff6149ed81011fcce5aa500d37e82744fa9489a3715df61788b71ac1cc6` | `910974482111febd112c033345fc6ab4ec15d958eb012bd61d323f9f20eb8457` |
| `udth_box` | `4e8a85224e48f8855e2757e7e023ea1831283ff7748434f0bd06beb8a9aab6da` | `4dabbfcbf046105ca20a95667e34ec739f4a51abd1b6129e1630a5d88dc7a73c` |
| `udth_box2` | `b5d708f0fb4e768bbd93fa176bbbc6855f53796890f77cd268d272fab22ffedc` | `cfa95ba9fec652be739849c87e771fd35f38fe2f0aebdaaf9a1774dc7e487ee3` |
| `udth_fn` | `145049fbc565293c252cd21e7f699059f9506dfd42866df3b70e3fb6f4ee78bf` | `fa80ad3b44b3aac686eeff59255bcdabc1cfc205e2f6a2774958333b716d385e` |
| `udth_method` | `173cd269088722f178d3061854d7682c00e349081bb8d11d21bd5a49d899c4e8` | `a248251a257aab65d63a5b0a57b7652eb9e4be09926d49ebd524cbfcdc64a217` |
| `udth_line_eq` | `7ae562f6938d69fff32aa0da04256cd2309d481ae986bc2bddc054aa97406078` | `9508b255e755711c2fa0a709b9c4daacd73f521b538353906a90a9f8c09804da` |
| `udth_expr` | `45b3341865d98a7927133b50d68a0a4b916b7f3fd2a0295a59adb3094bfe3103` | `d8a3a7df90ab803decd7a9026e479b3a2a09967b4ccf8f6b2e1b7973e19d893f` |
| `udth_fn2` | `74c76921bc57cd276f1a6db8b5bec17d40a6507e4db483e4d1259f1ce40829df` | `9a898dc7ae39b87af0b59279270f8db3b03843e51d8993b551700df4bed68432` |
| `udth_drawparam` | `879060be6c4e18eb68e19f1c13424f9d47f5db205e54a5bf308e3fd89872692e` | `0257f5ce785bf2fa31becc106819c89b8af57877d6e82a1e2b9ebab8ce99a2d1` |

Each exit Signal joins these fields with `|`, in this order:

- `udth_ref`: `d1`, `n2`, `v1`, `w1`, `i1`, `mb`, `firstNa`
- `udth_box`: `t1`, `vt`, `bt2` (4 decimals), `dl`, `firstNa`
- `udth_box2`: `d1`, `d3`, `ly` (4 decimals), `lx`, `vt`
- `udth_fn`: `a`, `b`, `l`, `t`, `blk`
- `udth_method`: `m`, `k`
- `udth_line_eq`: `firstNe`, `firstEq`, `firstNa`, `firstNaNe` (the first
  bar's), then `|` and `s1` .. `s10`, each `1` (true) or `0` (false)
- `udth_expr`: `f1`, `c1`, `t1`
- `udth_fn2`: `k1`, `k2`, `f1`, `p1`
- `udth_drawparam`: `m1`, `p1`, `y1`, `t1`, `t2`, `t3`, `u1`, `s1`

What each tape shows (the fields of the bars below `bar_index` 2 or 3 are the
probes' `-1` / `x` placeholders):

- `udth_ref`: a variable of a user-defined type holds a reference, and its
  history holds the references it held. `acc.total - (acc[1]).total` is 0 for
  a `var` object incremented every bar (`d1`): `acc[1]` is the same object,
  read as it is now. A fresh object per bar, changed on the next bar through
  `p1 = c[1]; p1.n += 1`, reads that change two bars later through
  `(c[2]).n` (`n2` is 1). `(c[1]).v` is the previous bar's object (`v1`:
  `bar_index - (c[1]).v` is 1), a variable reassigned on its bar keeps the
  last reference of the bar (`c2 := Cell.new(v = 2)`: `w1` is 2), a nested
  object reads through the history (`(o[1]).inner.v`, `i1` is 0), a variable
  holding an object or na keeps either (`mb`), and `c[1]` is na on the first
  bar (`firstNa`).
- `udth_box`: a drawing reference's history holds the references too. A box
  drawn on every bar reads the previous bar's box (`box.get_top(b[1]) -
  high[1]` is 0, `t1`), a `var` box changed every bar reads as it is now
  through `vb[1]` (`vt` is 0), and `box.set_bottom(b[1], -1.0)` reaches the
  box `b[2]` reads on the next bar (`bt2` is -1).
- `udth_box2`: a box deleted through `b[1]` (on every fourth bar) is na
  through its history afterwards: `na(b[1])` is true on its bar (`d1`) and
  `box.get_top(b[3])` is na two bars later (`d3`); a line's and a label's
  history read their drawings (`ly`, `lx` are 0); a `var box` redrawn on every
  third bar reads, through `vb[1]`, the box it held on the previous bar
  (`vt`).
- `udth_fn`: inside a function and an `if` block the history of a
  user-defined object counts the executions of its scope, not bars:
  `prevParam(x) => (x[1]).v` reads the previous call's argument, at a call on
  every bar (`a`: `bar_index - 1`) and at one on every other bar (`b`: the
  argument of two bars back, `(bar_index - 2) * 100`, na at its first call);
  a function's local (`l`) and a function's box (`t`) read the previous
  call's, and a local of an `if` block that runs on every other bar reads the
  previous run's (`blk`: `(bar_index - 2) * 7`).
- `udth_method`: a method's receiver alike: `this[1]` is the receiver of
  the previous call (`m`), at a call on every other bar too (`k`). The
  method sits in its own probe because TradingView refused `udth_fn` with it
  (`CE10271`, "Could not find method or method reference").
- `udth_line_eq`: `==` and `!=` compare line and label references by
  identity, na included: on the first bar `l != l[1]` is true and `l ==
  l[1]` false where `l[1]` is na, and two na lines are equal (`nl == nl[1]`,
  `firstNa`; `s7`); `l != l[1]` and `lb != lb[1]` hold on every bar, a `var`
  equals its history (`vl == vl[1]`, `vlb == vlb[1]`), a line equals its
  alias and not another line, and a line deleted through `l[1]` still equals
  itself (`l[1] == l[1]`, `s9`). Every close reads `1011|1111011111`.
- `udth_expr`: the history of an expression whose value is an object is the
  reference it produced at its previous evaluation, and below a lazy edge
  (a ternary's arm, here guarded by `na(...)`) it is kept on every bar:
  `(o.inner[1]).v` reads the previous bar's `o.inner` of a `var` object whose
  field is reassigned every bar (`f1`: `bar_index - 1`), `(mk(bar_index *
  7)[1]).v` the previous call's new object (`c1`: `(bar_index - 1) * 7`) and
  `((bar_index % 2 == 0 ? a : b)[1]).v` the previous bar's selection (`t1`:
  `2 * (bar_index - 1)` after an even bar).
- `udth_fn2`: in a function too: a `var` object read through `s[1]` is the
  object itself at each of two call sites (`k1`: `bar_index`, `k2`:
  `bar_index * 100`, the field set before the read), a typed parameter's
  object field `p.inner[1]` is the previous call's (`f1`: `(bar_index - 1) *
  10`) and a selection of two parameters `(cond ? a : b)[1]` the previous
  call's (`p1`).
- `udth_drawparam`: drawings through parameters and receivers. A box
  receiver's and a box parameter's history read with a built-in method,
  `(this[1]).get_top()` and `(x[1]).get_top()`, is the previous call's box
  (`m1`, `p1`: `bar_index - 1`), and so is a line parameter's `(x[1]).get_y1()`
  (`y1`: `3 * (bar_index - 1)`). A drawing passed to a drawing parameter is
  the drawing itself: a box's history `topOf(b[1])` (`t1`), a `var` box's
  history, the box as it is now (`t2`: `2 * bar_index`), a new box (`t3`),
  a user method on a box's history `(b[1]).topM()` (`u1`) and a line beside
  its history `span(l, l[1])` (`s1`: 3).

## Spellings TradingView refuses or stops on

TradingView's pine-facade compiler (`save/new_draft`, 2026-10-01) refuses
these probes, so they have no tape:

| Probe | TradingView's answer |
|---|---|
| `udth_noparen` | `CE10011` "User variable identifiers should not contain '.'" at the `.` of `c[1].v` |
| `udth_method_noparen` | `CE10010` "Cannot use a method directly after the history-referencing operator. Reference the history of the object first by enclosing it in parentheses, and then call the method, e.g., `(myVar[10]).myMethod()` instead of `myVar[10].myMethod()`." |
| `udth_eq` | `CE10123` "Cannot call "operator !=" with argument "expr0"="c". An argument of "Cell" type was used but a "simple string" is expected." (`operator ==` alike) |
| `udth_box_eq` | `CE10123`, `operator !=` with an argument of "series box" type (`vb == b`, without history, alike) |

The same `CE10123` refuses `==` on two `var box`es, two `linefill`s, two
`table`s, two arrays and two `chart.point`s, and `CE10187` ("Cannot compare a
value to "na" directly. Use the "na()" function instead.") refuses `l1 ==
na` for a line: of the reference types, only `line` and `label` compare.

TradingView refuses the history of a field holding a value, written either
way: `c.v[1]` (`udth_field_noparen`) and `(c.v)[1]` (`udth_field_paren`) are
`CE10290` "Cannot use the history-referencing operator on fields of
user-defined types. Reference the history of the object first by enclosing
it in parentheses, and then request the field, e.g. "(object[1]).field"
instead of "object.field[1]"." A field holding an object takes it
(`udth_expr`).

`udth_na_field` compiles, and its run stops on the first bar with
`RE10041` "Error on bar 0: Cannot access the 'Cell.v' field of an undefined
object. The object is 'na'.": `(c[1]).v` where `c[1]` is na. PineForge's run
stops there too ("UDT access on na or invalid object ID").

The Pine v6 User Manual agrees: "Type system", section "Value vs. reference
types" (user-defined types and the drawing types are reference types;
"Variables of reference types hold these object references; they do not
store objects directly"), "Objects", section "Copying objects" ("objects are
assigned by reference"), and "Migration guide to v6", section "History of UDT
fields" ("use the syntax `(myObject[10]).field` - ensure the object's
historical reference is wrapped in parentheses, otherwise it is invalid").

## What PineForge refuses besides

- The history of a reference inside a `request.security` expression, written
  there or in a function it calls: TradingView reads the references the
  variable held on the requested timeframe's bars, which PineForge does not
  keep (the expression read the chart's history, and never compiled).
- The history of a `chart.point` in a script that changes a `chart.point`
  field: PineForge holds a point as a value, so the history of a changed point
  would read its old value. Points no field write changes read alike either
  way, and their history is kept.
