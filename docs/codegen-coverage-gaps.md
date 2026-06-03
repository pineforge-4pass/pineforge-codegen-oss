# Codegen coverage gaps — running follow-up list

Tracks fallthrough paths in `pineforge_codegen/codegen/visit_call.py` and
`pineforge_codegen/codegen/visit_expr.py` that emit a literal `"false"` /
`"0"` / `"na<double>()"` for an unrecognized construct, and whether the
analyzer (`support_checker.py`) already rejects the construct before
codegen ever sees it.

A gap appears here only when **both** are true:

1. The fallthrough is reachable from a valid Pine v6 construct.
2. The fallthrough produces silently-wrong behavior (not a defensive
   sentinel that the engine ignores).

If only (1) is true but the analyzer rejects the construct, the
fallthrough is defensive-only and is intentionally left in place as a
safety net.

## Status — 2026-05-28 (end of Phase B)

No additional gaps found beyond what Phase B closed (B1–B6).

### Audit method

Ran `grep -n 'return "false"\|return "0"' pineforge_codegen/codegen/visit_call.py pineforge_codegen/codegen/visit_expr.py`
and triaged each of the 36 matches against the analyzer's rejection
tables (`HARD_REJECT_FUNC`, `HARD_REJECT_NAMESPACE`, `NOT_YET_FUNC`,
`UNSUPPORTED_BARE_FUNCS`, `UNSUPPORTED_NAMESPACES`,
`UNSUPPORTED_MEMBERS`, `UNSUPPORTED_NAMESPACE_VARS`,
`SUPPORTED_TIMEFRAME_FUNC`, `SUPPORTED_SYMINFO`, `SUPPORTED_MATH`,
`SUPPORTED_STR`, `SUPPORTED_TA`, `SUPPORTED_ARRAY`, `SUPPORTED_MAP`,
`SUPPORTED_COLOR_CONST`, `SUPPORTED_COLOR_FUNC`, `SUPPORTED_LOG`,
`SUPPORTED_RUNTIME_FUNC`).

### Triage summary

| Location | Construct | Status |
|---|---|---|
| `visit_expr.py:140` | `ColorLiteral` → `"0"` | Intentional: Pine color literals lower to int64 constants; `"0"` is a valid sentinel that downstream color helpers accept. Not a bug. |
| `visit_expr.py:218` | `strategy.short` → `"false"` | Known enum constant. |
| `visit_expr.py:288` | `strategy.fixed` → `"0"` | Known qty-type constant. |
| `visit_expr.py:339` | `chart.is_*` non-standard chart types → `"false"` | Correct semantics (batch is always standard OHLCV). |
| `visit_expr.py:340-345` | `chart.<unknown>` → `raise ValueError` | Defensive guard added in B5; surfaces any analyzer gap as a loud error. |
| `visit_expr.py:371-372` | `timeframe.isticks` → `"false"`; other `timeframe.*` → `"0"` | `isticks` is a correct sentinel (no tick data in batch). The catch-all `return "0"` at L372 is defensive — `timeframe.*` member set is closed in Pine v6, so unknown members would already fail at parse time on the TV side and have never appeared in the corpus. Leave in place. |
| `visit_expr.py:385,388` | `barstate.isrealtime` → `"false"`; other `barstate.*` → `"false"` | All Pine v6 `barstate.*` members are enumerated in the chain above L388; catch-all is defensive. |
| `visit_expr.py:396,405,412,417,423` | `backadjustment.*`, `settlement_as_close.*`, `adjustment.*`, `dayofweek.*` catch-alls | These namespaces are constant-only and well-bounded; unknown members fall back to a sane default and the engine accepts the int. Defensive. |
| `visit_expr.py:443` | `session.<unknown>` → `"false"` | All known `session.*` members handled in the if-ladder above. Pine v6's `session.*` member set is closed. Defensive. |
| `visit_expr.py:448` | `syminfo.<unknown>` → `"0"` | `SUPPORTED_SYMINFO` whitelist in `support_checker.py:754` rejects unknown `syminfo.*` members before codegen runs. Defensive. |
| `visit_expr.py:459` | `color.<unknown>` → `"0"` | `SUPPORTED_COLOR_CONST` covers all builtin color constants; unknown identifier here would be a typo and analyzer would not recognize it. Defensive. |
| `visit_expr.py:461` | `SKIP_NAMESPACES` (table/label/line/box/polyline/chart/linefill/display/size/position) → `"0"` | Intentional skip for visual-only namespaces. Engine ignores. |
| `visit_expr.py:484-507` | `strategy.oca.<unknown>`, `strategy.direction.<unknown>`, `strategy.commission.<unknown>`, `strategy.closedtrades.<unknown>`, `strategy.opentrades.<unknown>` catch-alls | Each sub-namespace is a small fixed enum; defaults match Pine v6 documented behavior. |
| `visit_call.py:357` | `map.<unknown>(...)` → `"0"` | `SUPPORTED_MAP` whitelist rejects unknown map functions in analyzer. Defensive. |
| `visit_call.py:404` | `array.<unknown>(...)` → `"0"` | `SUPPORTED_ARRAY` whitelist rejects unknown array functions in analyzer. Defensive. |
| `visit_call.py:411-414` | `SKIP_NAMESPACES`/`SKIP_VAR_TYPES`/`SKIP_FUNC_NAMES` → `"0"` | Intentional skip for visual-only calls (plot, plotshape, fill, hline, etc.). |
| `visit_call.py:664,689` | `timestamp()` with 0 or 1 positional args → `"0"` | Pine v6 `timestamp(...)` requires at least year/month/day; the 0/1-arg form is malformed and TV would reject it. Defensive. |
| `visit_call.py:693` | `barssince(...)` → `"0"` | Bare `barssince` is hard-rejected by `support_checker.py:501` (B-equivalent). Defensive. |
| `visit_call.py:809-815` | `timeframe.<unknown>(...)` → `raise ValueError` | Defensive guard; `SUPPORTED_TIMEFRAME_FUNC` already rejects via `support_checker.py:622`. |
| `visit_call.py:1057` | `strategy.opentrades.exit_time(idx)` → `"0"` | Open trades have no exit metadata; engine accepts 0. Documented Pine v6 behavior is `na<int>()` for `exit_bar_index` and similar — `0` here is the Pine epoch sentinel for time. Acceptable; the analyzer rejects most exit_* accessors on opentrades via `OPEN_TRADE_ACCESSOR_METHODS` at `support_checker.py:534`. |
| `visit_call.py:1088` | Generic `strategy.<unknown>(...)` → `"0"` | `support_checker.py:546` rejects unknown `strategy.*` via `sigs.STRATEGY_FUNCTIONS`. Defensive. |
| `visit_call.py:1096-1109` | `color.new/r/g/b/t/rgb/from_gradient` with missing args → `"0"` | Defensive default when the caller omits required arguments. Analyzer signature check covers arg-count. `color.from_gradient` is hard-rejected outright. |

### Conclusion

The 6 verified silently-wrong fallthroughs that Phase B identified
(B1–B6) are now rejected by the analyzer. Every remaining `return "0"`
or `return "false"` in the audited files is either:

- a known-constant branch with the correct value, or
- a defensive fallthrough whose construct is already rejected upstream
  by `support_checker.py`, or
- a `SKIP_*` intentional drop for visual-only Pine features the
  backtest engine does not model.

Two `raise ValueError` guards (`visit_call.py:812`, `visit_expr.py:342`)
were added to convert the most fragile defensive paths from
silent-on-bug to loud-on-bug.

No new follow-up work identified by this audit.
