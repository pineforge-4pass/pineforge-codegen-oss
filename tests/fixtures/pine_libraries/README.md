# Synthetic Pine libraries

Clean-room libraries written for PineForge's library inliner tests (lanes
XSYM-C and CG-SESSION-REPIN), laid out as `PINEFORGE_PINE_LIBRARIES` lays a
library out: `<user>/<name>/<version>.pine`. Every file carries an SPDX
Apache-2.0 header and is published nowhere; the `pftest` user does not exist
on TradingView.

No third-party library source belongs here. The open TradingView libraries
the lane was measured against (`richardgong1988/HanJinSignals26/4` and `/15`,
`jdehorty/MLExtensions/2`, `jdehorty/KernelFunctions/2`, `TradingView/ta/7`,
`TradingView/RelativeValue/2`) are pinned as private evidence only and cited
by import path.

| Library | `//@version` | What it exercises |
|---|---|---|
| `pftest/Base/1` | 6 | An export, a private helper and a type an importing library uses: the transitive import. |
| `pftest/V5Rules/1` | 5 | The functions of the synthetic v5 strategy `xc_v5_lib` (`../xsym_lib_tv`), one per v5 rule PineForge implements: int division by v5 qualifiers, strict `and`/`or` beside a lazy `?:`, a `for` end fixed before the first iteration, v5's color constants, numbers as conditions, a bool na where v5 reads it as a bool and across the library boundary, `timeframe.period`; and `at()`, a negative array index. |
| `pftest/W11ColorV5/1` | 5 | The color reads of the synthetic v5 strategy `w11-color-v5-eth15` (`../color_tv`): `r,g,b,t` of the seventeen named colors and `color.t` over a transparency sweep. |
| `pftest/Signals/1` | 6 | String signals with `na`, private helpers and a private constant, per-call-site state, locals and parameters that collide with a script's names, a type with a method, an enum, an `export const`, a transitive import, and an export PineForge refuses that only a script calling it reaches. |

Scripts importing them live in `../library_scripts`: `signals_import.pine`
reads every export of `pftest/Signals/1`, and `signals_spelled.pine` is the
same script with both libraries written in as user code, renamed by hand
(`tests/test_e2e_library_inline.py`). `v5rules_import.pine` is
`xc_v5_lib`'s global code calling `pftest/V5Rules/1`
(`tests/test_e2e_library_v5.py`), and `w11_color_v5_import.pine`
`w11-color-v5-eth15`'s calling `pftest/W11ColorV5/1`
(`tests/test_e2e_color_tapes.py`).
