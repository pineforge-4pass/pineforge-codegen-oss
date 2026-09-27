# W2 lowering-trio TradingView trade evidence

Synthetic public probes written for lane W2-CG-LOWERING-TRIO and their
unedited TradingView trade exports. They contain no closed or scraped source.
Each was exported with `lab tv --no-note`; the tapes' `Date and time` column is
the exporting account's chart timezone, Asia/Taipei (UTC+8).

## fixnan over a stateful call

```bash
lab tv --pine fixnan_adx.pine --slug pf-w2-fixnan_adx --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-05-01
```

| File | sha256 |
|---|---|
| `fixnan_adx.pine` | `2451cb3c343058e2de6281c49dd9f361c7fd2419bec94883882de52cb140ab2f` |
| `fixnan_adx_tv_trades.csv` (143 trades, exported 2026-09-26) | `79d381418832bf0d27919a0179a09fd7f23e3d4acad804d491f3c32b3dbb2fee` |

`fixnan_adx` is TradingView's DMI/ADX helper pair (`plus = fixnan(100 *
ta.rma(plusDM, len) / truerange)` inside a user function) plus a top-level
`fixnan` whose stateful argument is `na` on every :30 bar. Every exit comment
spells ADX, +DI and -DI to six decimals, then the held value on the exit bar
and on the bar before. TradingView's run starts at the window, so its first
days are the indicators' warm-up; the replay compares the trades entered from
2025-04-06 on, when the RMA seeds have decayed below 1e-15.

## Argument evaluation counts

```bash
lab tv --pine eval_counts.pine --slug pf-w2-eval_counts --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-03
```

| File | sha256 |
|---|---|
| `eval_counts.pine` | `c964d87c5a86d1f9371b50cfed824f39ffc9a63e6eea4c72253314b2744664d6` |
| `eval_counts_tv_trades.csv` (7 trades, exported 2026-09-27) | `434db5b37ecce89ac1a700b9c88d5c53e35be3a0fff2d39c36bbf93c86eb1809` |

`eval_counts` passes a counting probe (a function adding one to `cnt[k]` per
call) through one argument slot of each builtin whose lowering used to read or
skip it: `nz`'s replacement, `fixnan`, `math.round`'s precision, the
`str.startswith` / `endswith` / `substring` / `replace` / `replace_all` /
`repeat` slots and `array.lastindexof` / `binary_search*` / `join` /
`concat`. Every exit comment spells `cnt[k] - (bar_index + 1)` per slot:
TradingView reads `0` for every slot (each argument once per bar), a negative
count for the right side of a false `and` (22) and an untaken `?:` arm (23),
then `str.repeat("ab", 3, ",")` (`ab,ab,ab`) and the size of the concatenated
array (`6`).

## A function's last statement

Each probe was exported with `lab tv --no-note --symbol BINANCE:ETHUSDT.P
--interval 15 --from 2025-04-01 --to 2025-04-03` (7 trades each). Every exit
comment spells what the functions returned on the exit bar, then the bar's
close (and open).

| Probe (`--slug`) | File | sha256 (source / tape) | Exported |
|---|---|---|---|
| `pf-w2-f04_reassign_tails` | `tail_reassign` | `36da06f7c3814d7c82d8d355055b65b9beaf7b87a09b5c23d4249c004b6b2e8e` / `16f790439beb79a1cf4e016a0b7a68578b61896aca44fccfb164554a0e275863` | 2026-09-27 |
| `pf-w2-f04_decl_tail` | `tail_decl` | `1805668718480ce53e486669aec542f70a923963ee9c3b0ed84775496afc094c` / `a50e1ad3af7c09cc7f67d7aa56f765f66f89cd640b416068a0f96c30cd3504c7` | 2026-09-26 |
| `pf-w2-f04_var_decl_tail` | `tail_var_decl` | `9e44cd5ce0669c300c882928b20f9e7d79a9e6fad82ed9f8b5dff7d5d3d09f25` / `6f8733bf5ded8ed0e4ee13d72d48f0156752cf4433059553191b35943ce38b73` | 2026-09-26 |
| `pf-w2-f04_tuple_decl_tail` | `tail_tuple_decl` | `f5fdfd4313c428672e8cae33426e6a5886b46fa86ab6222a08c59df67dbf3960` / `857b0bd714f42ce1a483655ac12649f84cf3056cbcc893a6d610e3502e516f88` | 2026-09-26 |
| `pf-w2-f04_loop_tail` | `tail_loop` | `1186f863b5bbcfe841d759664fb417586b31d435d098d85d5c18159b14a0da09` / `7090026567367b824db9f6acec09565e25d45dd09714cbce31c1ced9a98e3884` | 2026-09-26 |
| `pf-w2-f04_loop_edges2` | `tail_loop_edges` | `90e1183c8810cdbe37c149abf6b1f976bea65a47fd95586e1bb2f245bd5479d1` / `cff81955e5647a30c2afcdd242c7f4434e0cde93660b76a613d65ac50b5225a1` | 2026-09-27 |
| `pf-w2-f04_loop_if_tails` | `tail_loop_if` | `e29c204ea67eea6fe787980d4c185a71991df433c6fc8c5fd32fd2b8bafb338b` / `b49765c3b2e6c3bb90b2cedc0c94fecbbfb8064b2dfd1a773db262cbfc72ecec` | 2026-09-28 |

- `tail_reassign`: `y := v * 2`, `y += v`, `-=`, `*=`, `/=` (a float target
  and an int right side), `%=`, a series `f := 0.5 * nz(f[1]) + v`, a UDT
  field `a.x := v + 3`, an if whose arms end in `:=`, a switch whose arms do,
  and a top-level if-expression whose arms do. TradingView returns each new
  value.
- `tail_decl`: `b = a * 3` last returns b. `tail_var_decl`: `var float first
  = v` last returns the first bar's close on every bar.
- `tail_tuple_decl`: `[p, q] = pair(v)` last returns the tuple.
- `tail_loop`: a `for` and a `while` ending in `s := s + v * i` return 6v; `for
  i = 1 to 0` counts down and runs twice. `tail_loop_edges`: a `break` or
  `continue` before the last statement keeps the previous iteration's value
  (3v), a `while` that never runs returns `NaN`, a `for ... in` returns 6v.
- `tail_loop_if`: a loop whose body ends in an if without else, an else-if
  chain without a final else or a switch without a default arm returns `NaN`
  when the last iteration runs no arm, and the arm's value (2v, 4v) when it
  does. The same probe family showed a function ending in an if without
  else that does not run returns `NaN` too (`pf-w2-f04_if_tails`, kept with
  the lane's evidence); PineForge still returns 0.0 there.

Two neighbours are TradingView compile errors, so they have no tape: a tuple
reassignment `[p, q] := pair(v)` ("Syntax error at input ':='", CE10156), and
a loop whose body ends in `if ... break`, which TradingView types void ("Void
expression cannot be assigned to a variable", CE10098).
