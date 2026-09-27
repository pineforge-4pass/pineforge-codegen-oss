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
