# CG-POPFIX TradingView trade evidence

Public probes written for CG-POPFIX and their unedited TradingView trade
exports. They contain no closed or scraped source. Exported on 2026-09-26
with `lab tv --no-note` on `BINANCE:ETHUSDT.P`, 15-minute chart. The tapes'
`Date and time` column is the exporting account's chart timezone,
Asia/Taipei (UTC+8).

```bash
lab tv --pine varip_coof_on.pine --slug pf-popfix-varip-coof-on-1m --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-05-01
lab tv --pine varip_coof_off.pine --slug pf-popfix-varip-coof-off-1m --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-05-01
lab tv --pine alert_freq_values.pine --slug pf-popfix-alert-freq-values --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-03
```

| File | sha256 |
|---|---|
| `varip_coof_on.pine` | `6240083c3d74b0ade987fe8db5b2b14c02bcb6d025e1fbf26e6b12f6af6ce15b` |
| `varip_coof_on_tv_trades.csv` (174 trades) | `41237133ac01ac31057c45efbd6b8a2e1feaca52e42ee18b89a47efcd392c0f9` |
| `varip_coof_off.pine` | `618ce5f97ac1456fab60b563f820bc0e265f1c686dfe12858f70097f4389b1d8` |
| `varip_coof_off_tv_trades.csv` (174 trades) | `861e26557f7d1fde5eb4f2d51822c30ff987f3bc0a06ea0656757949d0efa084` |
| `alert_freq_values.pine` | `5957a75532b4107ab28fc77d1fa0faeae8220b7e33cd5adc34bee0379d00f61d` |
| `alert_freq_values_tv_trades.csv` (48 trades) | `075f277410261be2a32594783fe68fbbd849c85d7c9cc7ea22a86d9f327c5024` |

`varip_coof_*` count one `var` and one `varip` counter per execution and
spell `d = varip - var` into every order comment. With calc_on_order_fills
off, `d` stays 0: a historical bar executes once. With it on, each fill's
recalculation adds one to `d`: TradingView rolls `var` back to the bar's
committed state before a recalculation and leaves `varip` alone. The same
probes over 2025-04-01..2026-05-01 (2,364 trades each; the calc_on_order_fills
tape ends at `d` = 4,727) and a `use_bar_magnifier = true` variant were
exported beside them and match the same way (full-year exports are kept with
the lane's evidence, not here).

`alert_freq_values` reads `alert.freq_all`, `alert.freq_once_per_bar` and
`alert.freq_once_per_bar_close` as values: its entry Signals spell
`all|once_per_bar|once_per_bar_close`, its exit Signals the selected constant
and whether it equals `alert.freq_all`.
