# strategy() default tapes (lane TV-DEFAULTS)

TradingView changed three Pine v6 `strategy()` defaults around
2026-09-24T20:45Z. For a v6 script that omits them it now uses
`initial_capital = 100000`, `default_qty_type = strategy.percent_of_equity`
and `default_qty_value = 100`, where it used 1000000, `strategy.fixed` and 1.
The value default is 100 whatever the quantity type: 100 contracts under
`strategy.fixed` and 100 of the account currency under `strategy.cash`.
Pine v5 still uses 1000000 / `strategy.fixed` / 1. No other `strategy()`
default moved: `currency`, `commission_type`, `commission_value`, `slippage`,
`margin_long` / `margin_short` (100 in v6, 0 in v5), `pyramiding`,
`process_orders_on_close`, `close_entries_rule` and `use_bar_magnifier`
export the same tape omitted as given explicitly at their documented default.
The whole matrix (every parameter, Pine v5 and v6, BINANCE:BTCUSDT 15,
NYSE:F 15, BINANCE:ETHUSDT.P 15 and CME_MINI:ES1! 15, each parameter omitted,
at its old default and at a value that moves the tape) is the engine lane
TV-DEFAULTS report; these seven tapes are the rows this repository replays.

Each directory is one `lab tv` export (channel `ws-report-v1`, `rangeProof`
covered), byte for byte: `strategy.pine`, `tv_trades.csv` (times at UTC+8),
`metrics.json`, `meta.json`. `metrics.json` `tvTradesCsvHash` is the sha256
of `tv_trades.csv`. The probes are synthetic, written for this lane; no closed
or scraped strategy is involved. Every probe trades the same signal (a
10/30-bar SMA cross, long only, entered from flat and closed on the opposite
cross), so the tapes differ only in how TradingView sized each entry. The
command, run once per row (`--no-note`: no campaign note was recorded):

```sh
source ~/code/pineforge-workflow/campaign/env.sh
lab tv --pine <probe>.pine --symbol BINANCE:ETHUSDT.P --interval 15 \
  --from 2025-04-01 --to 2025-05-01 --out <dir>/<tape> --slug <tape> --no-note --json
```

| tape | trades | `strategy()` declares | first entry (2025-04-01 23:30 UTC+8, 1910) | tv_trades.csv sha256 |
|---|---:|---|---|---|
| `tvd-all-omit-v6-eth15` | 42 | nothing | 52.356 = 100% of 100000 | `30d90f4509ec6d5623b191685d050fca90b53fbe142404f9c146a8fe2989ed19` |
| `tvd-cap-omit-v6-eth15` | 42 | `default_qty_type = strategy.percent_of_equity, default_qty_value = 100` | 52.356 = 100% of 100000 | `30d90f4509ec6d5623b191685d050fca90b53fbe142404f9c146a8fe2989ed19` |
| `tvd-qty-value1-v6-eth15` | 58 | `initial_capital = 1000000, default_qty_value = 1` | 5.2356 = 1% of 1000000 | `830e24358640c613ab4a379c28a991131e77509763c30dbdf6b97e3e4052c287` |
| `tvd-qty-typefixed-v6-eth15` | 58 | `initial_capital = 1000000, default_qty_type = strategy.fixed` | 100 contracts | `7198c515b7852a220af3d467130ab99029304cace3c602e7a8063b4889d6e1e8` |
| `tvd-qty-typecash-v6-eth15` | 58 | `initial_capital = 1000000, default_qty_type = strategy.cash` | 0.0523 = 100 USDT | `9bff4e749ac8250cd601f613739420c46753298912c43c4673c5798d32d6fa56` |
| `tvd-qty-fixed1-v6-eth15` | 58 | `initial_capital = 1000000, default_qty_type = strategy.fixed, default_qty_value = 1` | 1 contract | `df7cc20f01d8a81a2b4f202d17ab280c935d04be2e7a5e772485e4cff206edb8` |
| `tvd-cap-x1m-v6-eth15` | 42 | `default_qty_type = strategy.percent_of_equity, default_qty_value = 100, initial_capital = 1000000` | 523.5602 = 100% of 1000000 | `c86447297abb3b8bdf948da033bf78256e62f74eb4118d359b7c719063926a11` |

Every probe also declares `overlay = true`. The 42-trade tapes enter with
100% of equity: TradingView's default margin of 100% in v6 refuses the 16
entries whose fill price rose above the price the order was sized at, which
the 58-trade tapes, sized at a fraction of equity, all fill. The two
all-omitted and capital-omitted tapes are byte-identical: with the quantity
declared as 100% of equity, the omitted capital alone is the 100000.

`tests/test_e2e_strategy_defaults.py` replays each tape end to end --
`transpile_json`, the built runtime, `run_strategy.py` over the corpus 15m
feed in the tape's own window, with TradingView's BINANCE:ETHUSDT.P lot of
0.0001 as the `qty_step` -- and requires every trade (entry and exit time,
price, quantity) to be the tape's. The pre-lane build (`LEGACY`, codegen
main at `a4259656`) left the three parameters to the source host's own
defaults, 1000000 / `strategy.fixed` / 1, and misses the five tapes that
omit one of them; the two tapes that declare every one pass on both builds,
whose C++ is byte-identical.
