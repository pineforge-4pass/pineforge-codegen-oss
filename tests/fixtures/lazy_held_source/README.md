# Lazily executed `ta.mom` held-source tape (lane W8A-SIGSTATE-1)

A `ta.change` / `ta.mom` / `ta.roc` call below a lazy edge (a short-circuit
`and`/`or` right operand or a ternary arm) reads the call's own `source`
history: the source is written only on bars where the call executes and held
on the bars it skips. `source[length]` is the source at the call's latest
execution at or before `length` bars ago, also when the previous execution is
closer than `length` bars.

TradingView trade list (Strategy Tester > List of trades) for a US-listed stock,
kept in a private repository and overlaid by the lab onto the paired engine tree
at `tests/codegen-fixtures/lazy_held_source/`. The tape checks held-source reads
and is read by `tests/test_e2e_lazy_held_source.py`.

The same test module pins the held-source rule on the public corpus's
ETH-USDT 15m feed, bar by bar, against the spelled-out `close - close[4]` /
`close - close[3]`.
