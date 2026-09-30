# Pine intraday-cap activation

This note came with the explicit cap attachment
([#127](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/127)). It
now also covers the execution-adapter attachment that followed
([#128](https://github.com/pineforge-4pass/pineforge-codegen-oss/pull/128)) and
describes codegen 1.0.0 with engine `v1.0.0`.

Generated strategies explicitly select Pine intraday-cap compatibility in
their constructor. When the engine defines
`PINEFORGE_HAS_EXPLICIT_PINE_EXECUTION_ADAPTER_V1`, the constructor calls
`attach_pine_execution_adapter()`, which attaches the Pine execution adapter
and enables the cap; when it defines only `PINEFORGE_HAS_EXPLICIT_PINE_CAP_V1`,
it calls `enable_pine_intraday_cap()`. Selection finishes before
`strategy_create` returns, so host metadata setters see the selected component.
It is constructor configuration, outside script-state reset and rollback.

`strategy.risk.max_intraday_filled_orders(expression)` remains a statement.
The expression is evaluated only where the source executes it, and limit
updates preserve the existing quota and pending obligations. Selection does
not evaluate a limit or move a conditional statement into the constructor.

Engine `v1.0.0` defines both macros in `pineforge/source/pine_strategy_host.hpp`,
the header the generated C++ includes; every engine commit with that header
does, since the header and the macros arrived there together (engine #253). So
the adapter branch is the one that compiles today. The cap-only branch and the
path with neither macro date from the transition in #127 and #128, when the
generated C++ still derived from `BacktestEngine`. On engines with the explicit
capability, bare native construction leaves the Pine cap unselected. Matching
runtime headers and library are required; this source bridge does not make
stale compiled C++ objects compatible across internal ABI versions.

Engine `v1.0.0` no longer has the `script_has_strategy_close_` member that the
C++ of codegen 0.10.4 and earlier assigns, so that C++ does not compile there;
regenerate it with codegen 1.0.0.

The cap's three compatibility options retain their existing behavior and
defaults. They belong to the selected Pine component, not universal native
risk settings. This change does not select options using strategy identity
or score, alter a grading metric, or define an external broker execution API.
