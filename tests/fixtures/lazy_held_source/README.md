# Lazily executed `ta.mom` held-source tape (lane W8A-SIGSTATE-1)

A `ta.change` / `ta.mom` / `ta.roc` call below a lazy edge (a short-circuit
`and`/`or` right operand or a ternary arm) reads the call's own `source`
history: the source is written only on bars where the call executes and held
on the bars it skips. `source[length]` is the source at the call's latest
execution at or before `length` bars ago, also when the previous execution is
closer than `length` bars.

`w8a-lazy-mom-held` is one `lab tv` export (channel `ws-report-v1`,
`rangeProof` covered), byte for byte: `strategy.pine`, `tv_trades.csv` (times
at UTC+8), `metrics.json`, `meta.json`. `metrics.json` `tvTradesCsvHash` is
the sha256 of `tv_trades.csv`. The probe is synthetic (lane W8A-SIGSTATE-1);
no closed or scraped strategy is involved. The command (`--no-note`: no
campaign note was recorded):

```sh
source ~/code/pineforge-workflow/campaign/env.sh
lab tv --pine w8a-lazy-mom-held.pine --symbol NASDAQ:AAPL --interval 15 \
  --from 2025-04-01 --to 2026-05-01 --out <dir>/w8a-lazy-mom-held --slug w8a-lazy-mom-held --no-note --json
```

| tape | trades | tv_trades.csv sha256 |
|---|---:|---|
| `w8a-lazy-mom-held` | 182 | `c00011e6448a4422b339e75d1a29da9e2ed54b485e573e3b240031ca754f0e66` |

`m = bar_index % 4 != 3 ? ta.mom(close, 3) : na` runs the call on three bars
of every four. On a `bar_index % 4 == 2` bar its previous execution is one bar
back and `bar - 3` was skipped. From 2025-06-02 13:30 to 2025-06-20 20:00 UTC
the probe enters `M` (quantity `1 + round((m + 50) * 10000)`) and `C`, the
same `ta.mom(close, 3)` at top level, on every such bar: 91 cycles. Lane
W8A-SIGSTATE-1 decoded all 91 against the NASDAQ:AAPL 15 closes: `M` is
`close - close[4]` (the close of the execution at `bar - 4`, held over the
skipped `bar - 3`) and `C` is `close - close[3]`, and `M` differs from `C` on
every cycle. Before this lane the codegen read the chart's `close[3]` whenever
the previous execution was closer than the length, which makes `M` equal `C`.

The AAPL feed does not ship with this repository, so
`tests/test_e2e_lazy_held_source.py` checks the tape's own structure (91
cycles, `M` different from `C` on each) and pins the rule on the corpus's
ETH-USDT 15m feed, bar by bar, against the spelled-out `close - close[4]` /
`close - close[3]`.
