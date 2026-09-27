# Synthetic Pine libraries

Clean-room libraries written for PineForge's library inliner tests (lane
XSYM-C), laid out as `PINEFORGE_PINE_LIBRARIES` lays a library out:
`<user>/<name>/<version>.pine`. Every file carries an SPDX Apache-2.0 header
and is published nowhere; the `pftest` user does not exist on TradingView.

No third-party library source belongs here. The open TradingView libraries
the lane was measured against (`richardgong1988/HanJinSignals26/4` and `/15`,
`jdehorty/MLExtensions/2`, `jdehorty/KernelFunctions/2`, `TradingView/ta/7`,
`TradingView/RelativeValue/2`) are pinned as private evidence only and cited
by import path.

| Library | `//@version` | What it exercises |
|---|---|---|
| `pftest/Base/1` | 6 | An export, a private helper and a type an importing library uses: the transitive import. |
| `pftest/Signals/1` | 6 | String signals with `na`, private helpers and a private constant, per-call-site state, locals and parameters that collide with a script's names, a type with a method, an enum, an `export const`, a transitive import, and an export PineForge refuses that only a script calling it reaches. |

Scripts importing them live in `../library_scripts`: `signals_import.pine`
reads every export of `pftest/Signals/1`, and `signals_spelled.pine` is the
same script with both libraries written in as user code, renamed by hand
(`tests/test_e2e_library_inline.py`).
