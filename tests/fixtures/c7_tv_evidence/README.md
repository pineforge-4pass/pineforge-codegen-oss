# C7 TradingView pivot formula witness

`pivot_levels_probe.pine` is a public probe. It creates one market order for every
finite `[P, R1, S1, R2, S2, R3, S3, R4, S4, R5, S5]` level of each Pine pivot
type. The order quantity is `100000 + abs(level)`, so the exported quantity
carries the level value while the Signal column carries the type and level.
The first completed period is also exported as `Period-O/H/L/C` and the open of
the next period as `Anchor-O`.

Export command:

```text
source /Users/haoliangwen/code/pineforge-workflow/campaign/env.sh
lab tv --pine tests/fixtures/c7_tv_evidence/pivot_levels_probe.pine \
  --slug c7-pivot-levels-formula-4 --symbol BINANCE:ETHUSDT.P --interval 15 \
  --from 2025-04-01 --to 2025-04-08 --out /tmp/c7-tv-pivot4 --no-note --json
```

The export was covered and returned 55 closed trades (110 CSV rows). The first
period tape was `O=1821.59, H=1842.57, L=1816.64, C=1837.47`, and the next
period open was `1837.48`. Values below are the exported level values, rounded
to four decimals by the CSV quantity field.

| type | levels present | free function | TA1 anchored form | TradingView export |
|---|---|---|---|---|
| Traditional | P, R1, S1, R2, S2, R3, S3, R4, S4, R5, S5 | previous HLC; same rounded tape values; TA1 reports bitwise differences in S3 and R4..S5 from operation order | standard formulas over the anchored period H/L/C | all 11 values match the anchored form |
| Fibonacci | P, R1, S1, R2, S2, R3, S3 | previous HLC formulas | standard Fibonacci formulas over anchored H/L/C | all 7 values match |
| Classic | P, R1, S1, R2, S2, R3, S3, R4, S4 | previous HLC formulas | standard Classic formulas over anchored H/L/C | all 9 values match |
| Woodie | P, R1, S1, R2, S2, R3, S3, R4, S4 | uses previous close in the Woodie P/open slot; differs | uses the next period open (`Anchor-O`) as documented | all 9 values match |
| DM | P, R1, S1 | branches on H/L/C; differs from the documented open/close branches | uses period open and close | all 3 values match |
| Camarilla | P, R1, S1, R2, S2, R3, S3, R4, S4, R5, S5 | previous HLC formulas | standard Camarilla formulas over anchored H/L/C | all 11 values match |

The tape therefore witnesses the new form for every finite level TradingView
exports. It also confirms the engine-lane finding: the existing free function
must be changed for Woodie and DM, and its Traditional high levels need the
same operation order as TradingView if bitwise parity is required. This C7
change leaves the free function untouched and routes those spellings through
`ta::PivotPointLevels`.

## Level-by-level pivot values

The period is `O=1821.59, H=1842.57, L=1816.64, C=1837.47`, followed by an anchor bar opening at `1837.48`. The free function and TA1 columns are values from the built engine at `55467388` with `-ffp-contract=off`; the TradingView column comes from the exported order quantity (`100000 + abs(level)`, four decimal places). `na` is a level the type does not define.

| Type | Level | Free function | TA1 anchored form | TradingView |
|---|---:|---:|---:|---:|
| Traditional | P | 1832.2267 | 1832.2267 | 1832.2266 |
| Traditional | R1 | 1847.8133 | 1847.8133 | 1847.8133 |
| Traditional | S1 | 1821.8833 | 1821.8833 | 1821.8833 |
| Traditional | R2 | 1858.1567 | 1858.1567 | 1858.1566 |
| Traditional | S2 | 1806.2967 | 1806.2967 | 1806.2966 |
| Traditional | R3 | 1873.7433 | 1873.7433 | 1873.7433 |
| Traditional | S3 | 1795.9533 | 1795.9533 | 1795.9533 |
| Traditional | R4 | 1910.0167 | 1889.3300 | 1889.3300 |
| Traditional | S4 | 1754.4367 | 1785.6100 | 1785.6100 |
| Traditional | R5 | 1935.9467 | 1904.9167 | 1904.9166 |
| Traditional | S5 | 1728.5067 | 1775.2667 | 1775.2666 |
| Fibonacci | P | 1832.2267 | 1832.2267 | 1832.2266 |
| Fibonacci | R1 | 1842.1319 | 1842.1319 | 1842.1319 |
| Fibonacci | S1 | 1822.3214 | 1822.3214 | 1822.3214 |
| Fibonacci | R2 | 1848.2514 | 1848.2514 | 1848.2514 |
| Fibonacci | S2 | 1816.2019 | 1816.2019 | 1816.2019 |
| Fibonacci | R3 | 1858.1567 | 1858.1567 | 1858.1566 |
| Fibonacci | S3 | 1806.2967 | 1806.2967 | 1806.2966 |
| Fibonacci | R4 | na | na | na |
| Fibonacci | S4 | na | na | na |
| Fibonacci | R5 | na | na | na |
| Fibonacci | S5 | na | na | na |
| Woodie | P | 1833.5375 | 1833.5425 | 1833.5425 |
| Woodie | R1 | 1850.4350 | 1850.4450 | 1850.4450 |
| Woodie | S1 | 1824.5050 | 1824.5150 | 1824.5150 |
| Woodie | R2 | 1859.4675 | 1859.4725 | 1859.4725 |
| Woodie | S2 | 1807.6075 | 1807.6125 | 1807.6125 |
| Woodie | R3 | 1885.3975 | 1876.3750 | 1876.3750 |
| Woodie | S3 | 1781.6775 | 1798.5850 | 1798.5850 |
| Woodie | R4 | 1911.3275 | 1902.3050 | 1902.3050 |
| Woodie | S4 | 1755.7475 | 1772.6550 | 1772.6550 |
| Woodie | R5 | na | na | na |
| Woodie | S5 | na | na | na |
| Classic | P | 1832.2267 | 1832.2267 | 1832.2266 |
| Classic | R1 | 1847.8133 | 1847.8133 | 1847.8133 |
| Classic | S1 | 1821.8833 | 1821.8833 | 1821.8833 |
| Classic | R2 | 1858.1567 | 1858.1567 | 1858.1566 |
| Classic | S2 | 1806.2967 | 1806.2967 | 1806.2966 |
| Classic | R3 | 1884.0867 | 1884.0867 | 1884.0866 |
| Classic | S3 | 1780.3667 | 1780.3667 | 1780.3666 |
| Classic | R4 | 1910.0167 | 1910.0167 | 1910.0166 |
| Classic | S4 | 1754.4367 | 1754.4367 | 1754.4366 |
| Classic | R5 | na | na | na |
| Classic | S5 | na | na | na |
| DM | P | 1833.5375 | 1834.8125 | 1834.8125 |
| DM | R1 | 1850.4350 | 1852.9850 | 1852.9850 |
| DM | S1 | 1824.5050 | 1827.0550 | 1827.0550 |
| DM | R2 | na | na | na |
| DM | S2 | na | na | na |
| DM | R3 | na | na | na |
| DM | S3 | na | na | na |
| DM | R4 | na | na | na |
| DM | S4 | na | na | na |
| DM | R5 | na | na | na |
| DM | S5 | na | na | na |
| Camarilla | P | 1832.2267 | 1832.2267 | 1832.2266 |
| Camarilla | R1 | 1839.8469 | 1839.8469 | 1839.8469 |
| Camarilla | S1 | 1835.0931 | 1835.0931 | 1835.0930 |
| Camarilla | R2 | 1842.2238 | 1842.2238 | 1842.2238 |
| Camarilla | S2 | 1832.7162 | 1832.7162 | 1832.7161 |
| Camarilla | R3 | 1844.6008 | 1844.6008 | 1844.6007 |
| Camarilla | S3 | 1830.3392 | 1830.3392 | 1830.3392 |
| Camarilla | R4 | 1851.7315 | 1851.7315 | 1851.7315 |
| Camarilla | S4 | 1823.2085 | 1823.2085 | 1823.2085 |
| Camarilla | R5 | 1863.6973 | 1863.6973 | 1863.6973 |
| Camarilla | S5 | 1811.2427 | 1811.2427 | 1811.2426 |

## VWAP first-value evidence

`vwap_first_value_probe.pine` is the public TA1 probe for the first value of
the omitted, explicit daily, and custom-anchor forms. The two TradingView
exports are preserved under `tv-eurusd/` and `tv-eth/`:

| chart | `ta.vwap(close)` | `ta.vwap(close, timeframe.change("1D"))` | `ta.vwap(close, bar_index == 5)` |
|---|---|---|---|
| OANDA:EURUSD, 15m, 2025-04-01 | bar 0 (`V_default_vwap`, 08:00) | first day change (`E_explicit_vwap`, 2025-04-02 05:00) | bar 5 (`A_anchor5_vwap`, 09:15) |
| BINANCE:ETHUSDT.P, 15m, 2025-04-01 00:00 UTC | bar 0 (`V_default_vwap`, 00:00) | first day change (`E_explicit_vwap`, 2025-04-02 00:00) | bar 5 (`A_anchor5_vwap`, 01:15) |

The codegen E2E reproduces the same bar positions on the engine feed. The
explicit daily forms therefore use the anchored shim; only omitted-anchor VWAP
and its band form retain `ta::VWAP` / `ta::VWAPBands`.
