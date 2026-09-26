# K-RUNERR TradingView trade evidence

Public probes written for lane K-RUNERR and their unedited TradingView trade
exports. They contain no closed or scraped source. Exported on 2026-09-26 with
`lab tv --no-note` on `BINANCE:ETHUSDT.P`, 15-minute chart; `rangeProof`
`covered` for both. The tapes' `Date and time` column is the exporting
account's chart timezone, Asia/Taipei (UTC+8).

```bash
lab tv --pine int64_provenance.pine --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-03
lab tv --pine env_facts.pine --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-05
```

| File | sha256 |
|---|---|
| `int64_provenance.pine` | `cc527b5cd2870206f29bf2ab0618c35c97b165c57773e55739bf043fac845751` |
| `int64_provenance_tv_trades.csv` (3 trades) | `7f4485c5fed82b04df0ecb2ac9fcddd2592016f11ff7848edc7475a876dd7cce` |
| `env_facts.pine` | `6e24ea158478b7823d1fbef5ea30b075a18a86ab08f9bfd3c11b5c30ce1e8b90` |
| `env_facts_tv_trades.csv` (3 trades) | `a5dca61a04f13e56b323c8fd2c8b9761e9822830b27c05a0014c798870bf50fc` |

`int64_provenance` spells Pine `int`s that hold an epoch into its entry ids on
bar_index 3 (2025-04-01 00:45 UTC, TradingView's range starting at 00:00): a
`switch` and a ternary over two `const int` `timestamp(...)` values
(`sel=1743468300000|tern=1743468300000|hit=true`), a `var int` copied from a
`time` var and a plain `int` declared from it
(`copy=1743465600000|alias=1743465600000`), and a declared `int` parameter
called with `time` and with that var, plus `t + 1` returned through one
(`f=1743468300000|g=1743465600000|p=1743468300001`).

`env_facts` spells the chart facts the environment-gated oracle probes read.
TradingView's entry ids: `tz=Etc/UTC|tid=BINANCE:ETHUSDT.P`,
`tf=15|m=15|min=true|d=false|std=true` and
`tk=ETHUSDT.P|pre=BINANCE|type=crypto`. Its entries fill at 100% of equity
each, so TradingView closes them by margin call on the fill bar.
