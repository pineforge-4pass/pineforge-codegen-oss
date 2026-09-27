# Bar-magnifier declaration tapes (lanes MAG-INTRABAR and CG-W9-MISC)

Does TradingView run a strategy on its bar magnifier because the script
declares `use_bar_magnifier = true`? Each directory is one `lab tv` export
(channel `ws-report-v1`, `rangeProof: covered`, BINANCE:ETHUSDT.P 15,
2026-01-29 .. 2026-02-01), byte for byte: `strategy.pine`, `tv_trades.csv`
(times at UTC+8), `metrics.json`, `meta.json`. Both scripts are synthetic: a
market long every other chart bar while flat inside a 12-hour window,
bracketed at +-400 ticks, so both legs often sit inside one chart bar and the
exit taken depends on the intrabar path.

| tape | declares | trades | tv_trades.csv sha256 | provenance |
|---|---|---:|---|---|
| `mi-fx-eth-15` | `use_bar_magnifier=true` | 24 | `260265796c5247fdbd2bb0049793e722553f039cad108b484c68d391081e19fe` | R5 lane MAG-INTRABAR, copied byte-identical from pineforge-engine `tests/fixtures/magnifier_intrabars/mi-fx-eth-15` (engine main 1e360bd3) |
| `w9mag-fx-eth-15-off` | `use_bar_magnifier=false` | 24 | `c1091b17b9d6aa73dc0c0806ee5fcbc27f8aaaad0988b5d29d5e4fc37249db37` | lane CG-W9-MISC, `lab tv --no-note` of the same script with the magnifier off (and its own title and header comment) |

TradingView's two tapes enter on the same bars at the same prices and differ
on 7 of the 24 exits: with the magnifier the bracket leg the intrabar path
reaches first fills, without it the chart bar's own rule decides.

`tests/test_e2e_bar_magnifier_flag.py` is the host: it builds each script,
reads the library's `strategy_declares_bar_magnifier()` export and runs a
declaring script on the corpus 1m feed with `input_tf = 1`, `script_tf = 15`
and `runtime_overrides.bar_magnifier`, any other on the corpus 15m chart feed,
over the tapes' window. Each run books every trade of its own tape, and only
of its own tape. The pre-lane build (codegen `d7e095f`) exports nothing, so the
declaring script runs on the chart path and misses those 7 exits.

```sh
source ~/code/pineforge-workflow/campaign/env.sh
lab tv --pine w9mag-fx-eth-15-off.pine --symbol BINANCE:ETHUSDT.P --interval 15 \
  --from 2026-01-29 --to 2026-02-01 --out <dir>/w9mag-fx-eth-15-off \
  --slug w9mag-fx-eth-15-off --no-note --json
```
