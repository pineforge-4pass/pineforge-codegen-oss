# Host member names: a TradingView tape

One TradingView export (channel `ws-report-v1`, `rangeProof` covered), byte
for byte: `strategy.pine`, `tv_trades.csv` (times at UTC+8, the exporter's
rendering), `metrics.json` and `meta.json`. `metrics.json` `tvTradesCsvHash`
is the sha256 of `tv_trades.csv`, and its `sourceArtifactHash` that of
`strategy.pine`. The probe is public; no closed or scraped strategy is
involved.

## `cgs2-hostnames-aapl-15-reg` (codegen lane CG-SESSION-2, finding F8)

The probe declares script variables named like members the generated C++
reads on the engine host -- `session_isfirstbar_`, `session_islastbar_`,
`bar_index_`, `current_bar_`, `syminfo_` -- and reverses its position at the
close of every bar (`process_orders_on_close=true`), so each Entry row is one
chart bar. Its Signal carries, per pair, the built-in the name used to hide
and then the variable: `F` session.isfirstbar, session_isfirstbar_; `L`
session.islastbar, session_islastbar_; `B` barstate.isfirst, bar_index_; `M`
minute(time), `C` current_bar_; `Z` syminfo.timezone == "America/New_York",
syminfo_.

NASDAQ:AAPL 15, regular session, 2025-03-03 .. 03-15, exported with `lab tv`
(`--no-note`: no campaign note) on 2026-09-26 17:46:33 (UTC); a first attempt
a minute earlier failed at TradingView's pine-facade (`save/new_draft` 500, a
statement timeout) before compiling anything:

```sh
lab tv --pine host_names_probe.pine --slug cgs2-hostnames-aapl-15-reg \
  --symbol NASDAQ:AAPL --interval 15 --from 2025-03-03 --to 2025-03-15 \
  --out <dir>/cgs2-hostnames-aapl-15-reg --no-note --json
```

| tape | bars | tv_trades.csv sha256 | strategy.pine sha256 |
|---|---|---|---|
| `cgs2-hostnames-aapl-15-reg` | 260 | `517badbba40384413374a7f3c0f59224812e2abf6c8accb6b603de768483fef4` | `30efda634ce15fc91781e92c5f2b129847df6f0f76dcb8e9ad73d227ebc16498` |

TradingView compiles the probe and keeps every built-in apart from the
variables: session.isfirstbar and session.islastbar flag the 10 session opens
and closes, barstate.isfirst the first bar, while `session_isfirstbar_` is
`bar_index % 2 == 0`, `current_bar_` `bar_index % 4`, `bar_index_` 7 and
`syminfo_` 2.5 on every bar. `tests/test_host_member_names.py` replays the
probe on the tape's bars and matches all ten values on all 260; the pre-lane
codegen (7a39cb3) did not compile it, and without `current_bar_` and
`syminfo_` read session.isfirstbar, session.islastbar and barstate.isfirst
from the variables (120, 91 and 1 bars off TradingView).
