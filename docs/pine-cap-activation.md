Generated strategies explicitly select Pine intraday-cap compatibility in
their constructor when the engine declares
`PINEFORGE_HAS_EXPLICIT_PINE_CAP_V1`. Selection finishes before
`strategy_create` returns, so host metadata setters see the selected component.
It is constructor configuration, outside script-state reset and rollback.

`strategy.risk.max_intraday_filled_orders(expression)` remains a statement.
The expression is evaluated only where the source executes it, and limit
updates preserve the existing quota and pending obligations. Selection does
not evaluate a limit or move a conditional statement into the constructor.

The capability guard lets new generated source compile against older
supported engine headers, which retain their legacy cap default. On engines
with the explicit capability, bare native construction leaves the Pine cap
unselected. Existing generated source can still select legacy compatibility
through its protected limit assignment; the engine retains prior metadata in
the same configuration owner until that explicit selection. Matching runtime
headers and library are required; this source bridge does not make stale
compiled C++ objects compatible across internal ABI versions.

The cap assignment bridge does not retain the removed
`script_has_strategy_close_` member. Regenerate old source that assigns that
obsolete member; publish the updated codegen before the engine removal.

The cap's three compatibility options retain their existing behavior and
defaults. They belong to the selected Pine component, not universal native
risk settings. This change does not select options using strategy identity
or score, alter a grading metric, or define an external broker execution API.
