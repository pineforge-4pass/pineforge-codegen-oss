# K-RUNERR TradingView trade evidence

Public probes written for lane K-RUNERR and their unedited TradingView trade
exports. They contain no closed or scraped source. Exported on 2026-09-26 with
`lab tv --no-note` on `BINANCE:ETHUSDT.P`, 15-minute chart (`env_facts` again
on 2026-09-27, lane CG-LINUX-RED); `rangeProof` `covered` for each. The tapes'
`Date and time` column is the exporting account's chart timezone, Asia/Taipei
(UTC+8).

```bash
lab tv --pine int64_provenance.pine --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-03
lab tv --pine int64_cast.pine --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-03
lab tv --pine array_negative_index.pine --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-03
lab tv --pine env_facts.pine --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-05
```

| File | sha256 |
|---|---|
| `int64_provenance.pine` | `cc527b5cd2870206f29bf2ab0618c35c97b165c57773e55739bf043fac845751` |
| `int64_provenance_tv_trades.csv` (3 trades) | `7f4485c5fed82b04df0ecb2ac9fcddd2592016f11ff7848edc7475a876dd7cce` |
| `int64_cast.pine` | `0b4a41d98d7f3b6da524a3c2d1d0409eaa231800cc23cda4daccb61cf1063036` |
| `int64_cast_tv_trades.csv` (2 trades) | `e124a2ec381196546e2c87f0195cd04c8c3ebdf0135ce62b5fab0c4aacbcacbe` |
| `array_negative_index.pine` | `e933c70282c0f1de83e9357a357f62246b871dad18d110c5f37089c29b0f7821` |
| `array_negative_index_tv_trades.csv` (6 trades) | `37381433214bd5be03a12116dd6e200de44ef61fe1b398c912cf4a146a6d58e7` |
| `array_negative_index_verdicts.json` | `7fb6cb4804d98fef0a7b59a8764457a4041f9b372c10e37ceddff94fecf66a33` |
| `env_facts.pine` | `5e40491653a205dcb81b1840852930232314d35028fb531e1bceb7dcf435fa2c` |
| `env_facts_tv_trades.csv` (3 trades) | `2ca208c664a828979ceadfb7a42264affde15886c293cbdb390069997fc76939` |

`int64_provenance` spells Pine `int`s that hold an epoch into its entry ids on
bar_index 3 (2025-04-01 00:45 UTC, TradingView's range starting at 00:00): a
`switch` and a ternary over two `const int` `timestamp(...)` values
(`sel=1743468300000|tern=1743468300000|hit=true`), a `var int` copied from a
`time` var and a plain `int` declared from it
(`copy=1743465600000|alias=1743465600000`), and a declared `int` parameter
called with `time` and with that var, plus `t + 1` returned through one
(`f=1743468300000|g=1743465600000|p=1743468300001`).

`int64_cast` spells Pine `int()` of an epoch on the same bar: `int(time)` and
a `var int` set once to `int(time_close)`
(`it=1743468300000|first=1743466500000`), `int(timestamp(...))` and
`int(t)` returned through an `int` parameter fed `time`
(`ts=1743468300000|fid=1743468300000`).

`array_negative_index` reads Pine v6 negative indices on bar_index 3 and spells
the results into its entry ids: `array.get` at -1 and -5, `array.set` at -2,
`array.insert` at -1 and -5, `array.remove` at -3 on an int array, `get` at -2
and -3 on a string array, `get` at -1 and `.get(-3)` on a float array, and the
method forms `.get(-1)`, `.set(-1, ...)`, `.remove(-1)`.
`array_negative_index_verdicts.json` holds nine one-case probes on
`array.from(1.0, 2.0, 3.0)`, each exported the same way on 2026-09-26 with its
TradingView verdict: eight stop the script ("In 'array.<fn>()' function. Index
<i> is out of bounds, array size is 3.", with TradingView's `ctx`): `get`,
`set`, `insert` and `remove` at -4, `slice` from -2 and to -1, `fill` from -2
and `percentrank` at -1; `get` at -3 runs (`ok:1`). Each probe uses its call's
result: TradingView drops an unused call, so a bare `array.get(a, -4)` does not
stop its script (PineForge evaluates it).

`env_facts` spells the chart facts the environment-gated oracle probes read.
TradingView's entry ids: `tz=Etc/UTC|tid=BINANCE:ETHUSDT.P`,
`tf=15|m=15|min=true|d=false|std=true` and
`tk=ETHUSDT.P|pre=BINANCE|type=crypto`, each 0.01 contract, closed by the
`close_all` two bars later. It declares that size: the 2026-09-26 export of
the same probe without it (`6e24ea15...`, tape `a5dca61a...`) ran after
TradingView's 2026-09-24 change of the Pine v6 `strategy()` defaults, so each
entry was 100% of the 100000 default capital and TradingView closed all three
by margin call on their fill bar, where the engine, sized the same way since
TV-DEFAULTS, fills the first and refuses the other two.
