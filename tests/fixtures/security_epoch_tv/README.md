# Epochs through request.security: TradingView trade evidence

Synthetic probes for lane CG-W9-SEC (W9-CG-EPOCH-INT64) and their unedited
TradingView trade exports. They contain no closed or scraped source.
`w9sec_epoch_var_int_daily` and `w9sec_epoch_int_param_tuple` are the draft's
two witness scripts (lane W8D-NOTRADES, which exported them on
BINANCE:BTCUSDT); `w9sec_epoch_security` spells the values. Each was exported
on 2026-09-28 with

```bash
lab tv --pine <name>.pine --slug pf-<name, underscores as dashes> --no-note \
  --symbol BINANCE:ETHUSDT.P --interval 15 --from 2025-04-01 --to 2025-04-08
```

TradingView ran each from 2025-04-01 00:00 UTC (`rangeProof: covered`). The
`Date and time` column is Asia/Taipei (UTC+8); an exit with no `Signal` is
TradingView closing the open trade at the range's end.

| Probe | sha256 of the `.pine` | Tape sha256 | Trades |
|---|---|---|---|
| `w9sec_epoch_security` | `8b0b25f5426b765489b5d101b4fe6054e8311514b973ea5bf61adb9c1b98204a` | `a818b5985c124f092fe1aec0ccf3c00af23953aaf458bcddfacd3977223f55a6` | 265 |
| `w9sec_epoch_var_int_daily` | `3c8b3bad99d96153311204e4894af7b39ed3673fc926787e28de6bdaf6c31edc` | `f202268eb1cfeb3490afea0ebbe65b4db21101303bfc1801edbec26fc6ecaefe` | 7 |
| `w9sec_epoch_int_param_tuple` | `80e7a979b7fd1f5b8c218b46223ff7b073f23a8e51a31a778ca8e40719b29007` | `a0863984b64a83b17656ab24ce120ff52ff55fe27438ec8fac45cb3c188336e9` | 18 |

What each tape shows:

- `w9sec_epoch_security`: each exit Signal joins `last` (a `var int` latched
  to `request.security(t, "60", time)`), `changes` (how often it changed),
  `f_ok(t2)` (a typed `int` parameter fed element 2 of `[time, time[1],
  time[2]]` requested on 60) and `f_age(t1)` (minutes from `t1` to the chart
  bar, through an `int` parameter; `-1` while `t1` is `na`). `last` reads the
  full epoch of the last completed hour (1743465600000 ...) and changes once
  per hour.
- `w9sec_epoch_var_int_daily`: a `var int` latched to the 60-minute epoch
  counts the hours of each UTC day: an entry at 03:00 UTC and a close at 06:00
  UTC every day.
- `w9sec_epoch_int_param_tuple`: `f_ok(int t) => not na(t)` fed `t1` of a
  60-minute `[time, time[1]]` gates an SMA crossover: 18 trades.
