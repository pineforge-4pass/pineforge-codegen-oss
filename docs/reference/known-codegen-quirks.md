## Known codegen quirks (read before changing)

These bit us once and are now pinned by `test_compile_smoke.py`'s
`test_regression_*` cases. Treat the regression tests as canaries — if
you delete or weaken the special case, the test will tell you.

1. **`year(time)` / `month(time)` / `dayofmonth` / `dayofweek` /
  `hour(time, tz)` / `minute` / `second` / `weekofyear`** — BOTH the
   function-call form and the bare variable form lower to the engine's
   cached, timezone-aware `pine_<field>(ts_ms, tz)` helpers
   (`session_time.hpp`): the variable form via `BAR_BUILTINS`
   (`pine_year(current_bar_.timestamp, syminfo_.timezone)` …), the
   function form via `visit_call.py` (a guarded `pine_hour(ts, tz)`),
   so the two forms agree for present timestamps. A function-call `na`
   timestamp uses epoch zero; `weekofyear(na)` returns 1, as covered
   TradingView UTC and New York tapes show. The two-arg form uses the explicit tz; the
   one-arg form defaults to `syminfo_.timezone` (engine
   `SymInfo::timezone`, "UTC" by default). The old inline
   `setenv("TZ")+localtime_r` lambda (`tz_time_field_lambda`,
   codegen/tables.py) is no longer emitted — per-call tzset churn
   caused a macOS notifyd IPC storm (KI-35) — and has zero call sites.
   Chart display TZ is a separate engine slot (`chart_timezone_`,
   `strategy_set_chart_timezone`) intentionally NOT consulted by these
   builtins; harnesses validating metrics against TV exports for
   non-UTC charts must still set chart_tz to match the chart's TZ at
   export time.
2. **Matrix-returning methods** (`inv` / `pinv` / `transpose` / `copy` /
  `submatrix` / `concat` / `diff` / `mult` / `pow` / `eigenvectors` /
   `kron`) live in `MATRIX_RETURNING_METHODS` (`codegen/tables.py`).
   Both `_register_global_aggregate_member_types` (codegen/base.py) and
   `_visit_var_decl` (codegen/visit_stmt.py) consult this set to declare
   the LHS as `PineMatrix` instead of the analyzer's default `double`.
   Methods returning primitives (`det`, `rank`, `trace`, …) or arrays
   (`row`, `col`, `eigenvalues`) must NOT be in the set: `_type_spec_from_expr`
   types those as arrays in both the method and the namespace form
   (`matrix.row(m, 0)`), as it does the array methods that build a new array
   (`types.ARRAY_RESULT_METHODS`: abs, standardize, sort_indices), so an
   untyped LHS is declared a vector (`tests/test_e2e_untyped_collection_results.py`).
3. **`str.format` / `str.tostring` / `log.*` number text.** The emitted
   helper in `codegen/tv_number_format.py` takes typed format arguments and
   renders TradingView's `#,###.###` default for `str.format`, its
   `{0,number,...}` styles and apostrophe quoting, and `str.tostring`'s
   `#.##########` default, custom `#`/`0`/`%` patterns, percent and volume.
   `format.mintick` delegates to the engine's tick-rounding formatter.
   The helper is emitted when any syntax child calls a formatter
   (`iter_ast_nodes`: `_walk_ast` skipped a tuple literal, so a
   `request.security` tuple payload's `str.tostring` left it undeclared;
   `tests/test_e2e_tostring_tuple_payload.py`).
   Numeric rounding converts a double to its shortest round-trip decimal with
   C++17 `std::to_chars`, shifts the decimal point as digits, then rounds the
   first discarded digit half-up; it uses no floating-point intermediate or
   `long double`. `tests/test_tv_number_format_rounding.py` pins 42 edge
   cases on AppleClang arm64 and GCC 13 Linux amd64.
   The lowering is `visit_call._str_format_expr`, shared with
   `log.info` / `log.warning` / `log.error(fmt, arg0, ...)` (TradingView:
   the second overloads of `log.*()` have "the same parameter signature and
   formatting behaviors as str.format()"; the arguments used to be dropped);
   `log.*(message)` logs its message as is. The engine's `str_format` only
   substitutes plain `{i}`; its `str_tostring` uses six fixed decimals by
   default, multiplies `format.percent` by 100, and retains two decimals for
   every volume. The codegen helper covers those gaps without an engine ABI
   change. `tests/test_e2e_tv_number_rendering.py` pins 28 TradingView order
   Signals, while `tests/test_e2e_log_format.py` pins dynamic log lines.
   `signatures.py` names the first `str.format` parameter `formatString`,
   and the call visitor binds `str.format(formatString="...")`.
4. **Per-call-site TA cloning.** Multiple `ta.sma(close, ...)` call
  sites need separate `ta::SMA` instances (one per call site),
   addressed via `_cs0`, `_cs1`, … suffixes. The analyzer assigns
   call-site indices in `ctx.func_call_cs_map`; the codegen's
   `_active_call_site_idx` machinery threads them through user-defined
   functions. When adding TA dispatch, make sure the new path respects
   `cs_info` / `_func_cs_var_remap`. A helper reached through a second call
   of its caller runs as a context-sensitive instance (`g__ni1`,
   `_build_func_instances`) with fresh copies of the path's TA, `var`,
   fixnan and history-read local (`f := ... f[1]`) state;
   `tests/test_e2e_nested_path_series.py` pins the locals. Such an instance takes the parameter and return types
   of the written call it runs, as that call's `_csN` clone does
   (`type_call_site_idx`): typed from the shared inference, an untyped
   string parameter was a double (`tests/test_e2e_nested_instance_param_types.py`).
5. `**Series<T>` ring buffer.** Bar-related fields (`close`, `high`,
  `low`, `open`, `volume`, derived `hl2/hlc3/ohlc4/hlcc4`) auto-promote
   to `_s_<name>` series whenever the script reads them with `[k]`. The
   analyzer registers them in `ctx.series_bar_fields`; the codegen
   declares `Series<double> _s_close;` etc. and pushes the current bar's
   value at the top of `on_bar`. `pivot_point_levels` reads the
   PREVIOUS bar's HLC (`_s_high[1]`, `_s_low[1]`, `_s_close[1]`) on the
   free-function `anchor=true, developing=false` route. Other forms use the
   TA1 anchored-period class.
6. **`request.security` is strict.** Only `symbol`, `timeframe`,
  `expression`, `gaps`, `lookahead`, and `ignore_invalid_symbol` are
   allowed. Another symbol reads its pinned feed, `ignore_invalid_symbol`
   honoured ("Requests of other symbols" above), or lowers to na or a
   deferred refusal ("Requests with no data").
   Chart-symbol aliases resolve by lexical
   binding, so a local rebind cannot taint an unrelated global. A helper
   parameter is the chart's symbol when every call of its helper binds one
   to it, through further helpers' parameters too (a method's or an
   overloaded helper's parameter stays refused), and each call path's
   symbol, Heikin-Ashi or plain, is part of its request's context
   (`tests/test_security_symbol_helper_param.py`). A ternary
   symbol that can select another symbol registers, when its value can
   reach a trade, by the string the run computes: the chart's arm reads
   the chart and another's its pinned feed, or stops the run where read
   (never the chart's bars in its place); one reaching display sinks only
   warns and keeps its chart-symbol lowering. Safe
   forms include `syminfo.tickerid`, `syminfo.ticker`, and
   `ticker.inherit/standard/heikinashi(<chart sym>)` (`ticker.inherit`'s
   symbol is its second argument). `gaps` and
   `lookahead` must be the literal `barmerge.gaps_*` /
   `barmerge.lookahead_*` member access (codegen does not parse other
   shapes). `barmerge.lookahead_on` is ACCEPTED with a repaint WARNING —
   engine-supported (first-intrabar publication; script-tf publish gating
   for finer-than-chart targets): see `_check_request_security`'s lookahead
   branch and `test_request_security_lookahead_on_kwarg_warns`. The old
   "hard-rejected" wording here was verified stale against the code on
   2026-07-07 — do not restore it. The `timeframe` argument, when a string
   literal, is validated against the Pine v6 TF format at parse time (PR #3).
7. `**SUPPORTED_LOG`** gates `log.{info,warning,error}`. Without it,
  typos like `log.foo("x")` previously emitted a dead empty-string
   statement. Don't remove the gate.
8. `**CLOSED_TRADE_ACCESSOR_METHODS` vs `OPEN_TRADE_ACCESSOR_METHODS`**
  are intentionally asymmetric. `opentrades` has no `exit_*` fields
   in Pine v6; both lack `direction`. `TRADE_ACCESSOR_METHODS` is kept
   as the union for back-compat but new code should prefer the side-
   specific constant.
9. **Preserve `na` at numeric boundaries.** A `double`
  expression reaching an `int` / `int64_t` slot, or an integer crossing
   between those widths, must go through
   `helpers.na_preserving_int_cast` (`is_na(_pf_v) ? na<int>() :
   (int)_pf_v`), applied by `types._coerce_int_slot`. An *implicit*
   narrowing of a NaN is undefined ([conv.fpint]) and the compilers
   disagree: AppleClang arm64 and g++ aarch64 give 0 at every `-O`,
   g++ x86-64 gives `INT_MIN` at `-O0`/`-O1` and 0 from `-O2`. The
   engine's contract (`include/pineforge/na.hpp`) is that an integer
   `na` IS `std::numeric_limits<T>::min()`, which is what `is_na(T)`
   tests. The cast helper checks the original numeric type before narrowing:
   converting `na<int64_t>()` to `double` first loses its sentinel. A
   `double` outside `int` narrows to `na<int>()` too (x86-64's `INT_MIN`; arm64
   saturated an epoch a missed provenance narrowed). Dynamic
   history, matrix and lazy TA indices use `pine_index_int_cast` for the same
   reason. One bench slot booked 2412 trades built at `-O3` and 2411
   (TradingView's count) at `-O1` from the same source. A plain
   `(int)x` cast does NOT fix this — it only silences the warning; the
   `is_na` test is what makes it defined. Where the value is needed is
   decided by `types._emitted_value_is_double`, which is deliberately
   NOT `_infer_type(node) == "double"`: `_infer_type` answers "what
   does this slot hold" (Pine's `int` for `math.round(x)`, whose
   emission is a `double` `std::round(x)`) and falls back to `double`
   for shapes it cannot resolve (integer arithmetic, loop binders,
   colour constants, `bar_index`). Both directions of that mismatch
   are enumerated there; extend it, not `_infer_type`, when adding a
   lowering. The check that keeps the class closed is the compiler:
   `tests/test_na_int_narrowing.py` compiles a per-site battery with
   `-Wfloat-conversion` and requires an empty diagnostic list.
   Numeric values entering a boolean context must use the Pine truthiness
   helper: `na` is false, while C++ would treat both NaN and the integer
   `na` sentinel as true. `types._coerce_bool_expr` covers `if` / `while` /
   ternary / `and` / `or` and user-function or TA bool parameters;
   compile-time literals and already-boolean values are proven non-`na` and
   stay native. `tests/test_na_truthiness.py` pins the generated form and
   runtime rows. The integer narrowing and truthiness batteries together are
   the closed conversion-site check. A plain global int every binding of
   which is a one-argument `math.floor` / `ceil` / `round`, read by an
   ordering comparison and otherwise only by a comparison, `na`, `nz`,
   `str.tostring` or a `strategy.entry` / `strategy.order` quantity, is
   stored as the double its value is (`_nonfinite_int_names`, lane
   TAIL-C): TradingView keeps the +Infinity of a division by zero there,
   which `>` / `>=` order above every number while `==` / `!=` are false
   and every other read reads na (`_nonfinite_int_read`), and an entry
   quantity of it trades the default quantity (the engine's rule). The
   narrowing made it `na<int>()`, which ordered as na, so `if shares > 0`
   never held (`tests/test_e2e_nonfinite_int.py`,
   `fixtures/nonfinite_int_tv`). Any other int keeps its C++ `int`.
   The other direction: a non-constant integer reaching a user-defined
   record's `float` field (`Cell.new(v = bar_index)`, an epoch) is
   converted where the designated initializer would narrow it, an
   integer na to na (`types._coerce_double_slot`): it did not compile
   (`tests/test_e2e_udt_float_field_ctor.py`, tape
   `fixtures/array_history_tv/uctor_float`); an expression of int
   literals a double holds exactly keeps its spelling. A `:=` to such a
   field converts the same way: it compiled, but an int na became
   -2147483648 where TradingView's field is na (tape
   `fixtures/array_history_tv/uassign_float`).
   Float destinations share the same original-type check: an integer `na`
   becomes floating `na` before a write, numeric branch join or float argument.
   Explicit float declarations retain double storage, including integer
   initializers followed by fractional writes. Integer arithmetic checks
   missing operands before computing; integer destinations retain their type.
   `tests/test_e2e_na_float_boundaries.py` checks native values and quantities.
10. **An inline `input.*()` call is one leaf of a TA length.** TA ctor
   args (and derived / user-function lengths) reach the codegen as Pine
   source spellings. `pine_spelling.py` keeps an inline input call whole
   there — keyword args included, strings escaped — and the codegen
   masks its argument text (title string, keyword names) out of every
   identifier scan, so `ta.ema(close, input.int(9, "fast"))` gets the
   same reset, placeholder and manifest entry as
   `len = input.int(9, "fast")` + `ta.ema(close, len)` (issue #132; the
   title used to read as an unknown identifier). An inline call is
   admitted exactly when its bound spelling would be input-backed
   (`_is_stable_inline_input`: not a source input, constant defval); a
   length that is otherwise a series stays refused. A generic `input()` is
   the same leaf in a declared length (`len = input(9) + 1`:
   `_expr_is_stable` admits it through `_is_stable_inline_input`;
   `tests/test_e2e_generic_input_length.py`), and it is the type of its
   default -- `input("x")` a string input, `input(3)` an int one,
   `input(true)` a bool one -- for its member, the static-input
   initialisation and the inline read alike (`_input_getter_for_call`; a
   string default assigned a `std::string` to a double member;
   `tests/test_e2e_generic_input_types.py`). The input manifest
   lists every global-scope input call, inline ones too, with `title` =
   the key the C++ reads it by. `tests/test_e2e_inline_input_ta_length.py`
   pins it end to end, for every TA constructor.
11. **A lookback that is a `compute()` argument goes to `compute()`.** Most
   `ta::` classes take their length in the constructor and only sources in
   `compute()`. `ta::Change` splits it: the constructor's `max_length`
   only bounds the kept history, `compute(src, length = 1)` reads the
   lookback, so `TA_COMPUTE_ARGS["change"] = [0, 1]` (analyzer) sends the
   length to both — without it every `ta.change(src, n)` was a one-bar
   change. `ta::ValueWhen` is the mirror image: `occurrence` always reached
   `compute()`, but the history bound is the constructor's `max_occurrence`
   (default 1, two values kept), so `TA_PERIOD_ARG["valuewhen"] = 2` sends
   it to the constructor as well (it sat in `TA_NO_CTOR`, and every
   `occurrence >= 2` read `na`). `ta.vwap`'s default anchor keeps the
   historical VWAP route; every other anchor is carried in the anchored
   shim's per-bar compute arguments. `ta.alma`'s `floor` and `ta.kc` /
   `ta.kcw`'s `useTrueRange` are constructor arguments in the TA1 shims.
   Feature macros select the exact TA1 classes and the shim branches preserve
   the old lowering when a macro is absent. The routing table is the
   ANALYZER's `TA_COMPUTE_ARGS` (analyzer/tables.py):
   without an entry every non-constructor argument goes to `compute()`, so a
   new TA whose `compute()` in `ta.hpp` does not take every such Pine
   parameter needs one. The one-arg `ta.highest` / `lowest` / `highestbars`
   / `lowestbars(length)` forms (positional or `length=`) read high / low
   (`TA_LENGTH_ONLY_DEFAULT_SOURCE`). `tests/test_e2e_ta_argument_routing.py`
   compares every routed spelling with a spelled-out reference, every warned
   one with the pre-C5 (e6a64cd) build of the same script, and pins every
   refusal. `tests/test_e2e_ta_change_length_and_input_keys.py`
   pins `ta.change` bar by bar against `src - src[n]`,
   `tests/test_e2e_valuewhen_occurrence.py` `ta.valuewhen` against a
   spelled-out chain, `tests/test_e2e_vwap_anchor.py` `ta.vwap` against a
   spelled-out anchored VWAP.
12. **One key per input, whichever path reads it.** A `get_input_*()` read
   keys the input by `_get_input_title(call, ...)`, the string the manifest
   lists: the title's value (`_input_title_value`: a literal, a `+` of
   constants, or a never-reassigned non-`var` global name bound to one;
   `_check_input_titles` refuses any other title before generation), else
   the name of the declaration holding the call
   (`pine_spelling.input_binding_names`: `v = input.*()`, or a call nested
   anywhere in a declaration's value, `var` or not — TradingView: "If not
   specified, the variable name is used as the input's title"), else `""`.
   The name comes from the call node, not from the caller, so the member,
   the TA reset, a
   request.security timeframe and an alias's read (`b = a`) agree; a call
   re-spelled for a derived length carries it as `title=`, since the
   re-parsed node has no declaration. `_input_key_literal` spells the key
   as a C++ literal (a title holding `"` or `\` used to break the TU).
   `input_keys.assign_input_keys` normalizes later explicit duplicate titles
   to `<title>#<n>` before helper copying and TA spelling, starting at 2 and
   taking the next ordinal whose key is neither a provable authored title
   anywhere in the script nor already assigned by the pass;
   the first title and all untitled keys retain their previous spelling.
   Remaining shared keys cannot be overridden apart, so `_check_input_keys` warns
   (see "Input keys" above; an untitled call nested in a plain declaration
   used to be keyed `""`).
   `tests/test_e2e_untitled_input_keys.py`,
   `tests/test_e2e_nested_input_keys.py` and
   `tests/test_e2e_input_title_constant.py` pin it end to end.
13. **A precalculated TA site is built like the live one.** A static chart
   TA site (bar-data `compute()` arguments) is precalculated:
   `prepare_script_run()` calls `precalculate()` when the engine allows it
   (no magnifier, empty timeframes), and a call nested in an expression
   then reads `_precalc_<member>[bar_index_]` (a direct `x = ta.foo(...)`
   assignment does not).
   `precalculate()` builds each site from `_ta_run_ctor_args`, the same
   override-aware arguments as the `_ta_initialized_` reset, never from
   compile-time values — those are the input's default (or the placeholder
   `1` for a length that does not fold), and an override never reached a
   nested call. `tests/test_e2e_precalc_input_override.py` pins every
   spelling against literal-length twins. A length read through a plain
   alias of an input-derived scalar (`emaLen = calcEmaLen` over
   `calcEmaLen = swingLen * emaRatio`) is rebuilt from the inputs too:
   the alias is a derived expression of the name it copies
   (`_collect_known_var`), where the reset read the alias's own member
   before the body set it, an EMA of length 0 for the whole run
   (`tests/test_e2e_alias_ta_length.py`, tapes `fixtures/alias_len_tv`).
14. **String escapes are the Pine manual's.** The lexer (`_read_quoted`)
   reads `\n` / `\t` as U+000A / U+0009 and `\\`, `\"`, `\'` as the
   character; any other `\X` reads as `X` ("the character's meaning does
   not change": `\T`, `\r`, `\u`, `\x`, `\0`, ...). A value reaches C++
   through `_cpp_string_escape` (escapes `\n`, `\r`, `\t`) and a Pine
   re-spelling through `pine_string_literal` (spells `\n`, `\t`). A
   multiline `"""..."""` / `'''...'''` literal (`_read_multiline`) is
   everything up to the first three unescaped delimiter quotes, line breaks
   as U+000A and indentation kept; a single-line literal that continues on
   an indented line reads the break as one space (the manual's deprecated
   line wrapping); one never closed is refused at its opening quote. A
   `// @pf-trace` line inside a multiline or wrapped string is text, not a
   pragma: the pre-pass uses the Pine lexer to exclude string spans.
   `tests/test_e2e_string_escapes.py`
   and `tests/test_e2e_multiline_strings.py` pin each escape and the
   manual's multiline examples through the manifest, an override and
   per-bar `str.*` traces.
15. **Top-level lazy-edge `ta.*` sites whose history is read are hoisted to
    every-bar evaluation.** TradingView (pinned on a daily US-stock probe,
    tapes kept outside this repository) advances a stateful `ta.*` call on EVERY bar when it sits below
    a Pine-v6 lazy `and`/`or` RHS or a ternary arm of a top-level statement
    AND the call's own history is referenced (`ta.sma(close, 5)[1]`; the
    bare twins of every tape are per-execution);
    short-circuiting gates only the value, and `[1]` on it is the previous
    BAR. Without a `[k]` read the reached-only inline compute is TV's clock
    (oliver1002 / louislapis9 / ycelestine77 / quantbyboji / miemomo3 exact at
    100% on it, 2026-09-04). For a `[k]`-read site codegen emits
    `const auto _pf_every_bar_ta_N = <site>;` (plus the site's `_hist_call_*`
    push for a direct `[k]`) BEFORE the statement, in dynamic mode too
    (`codegen/ta.py::_lazy_edge_ta_hoist_plan`, `_emit_lazy_edge_ta_hoists`;
    `tests/test_lazy_edge_ta_every_bar.py`). The rule is per family
    (cadence-7 ternary/lazy-and probes, same tapes) and the hoist is an
    ALLOW-LIST (`LAZY_EVERY_BAR_TA` = highest/lowest/sma/ema) gated on the
    `[k]` read: a broad hoist of every family cost 170 tiers / 30 hard lanes
    on Cloud Run (2026-09-04), so an unpinned family keeps its existing
    lowering until a tape pins it. `change`/`mom`/`roc` (`LAZY_SOURCE_CLOCK_TA`) read the
    call's OWN held `source[length]` -- written only when the call executes,
    held on skipped bars, na before the first execution -- through the
    generated `_PFLazySourceClock` + `_pf_lazy_src_hist_N` members
    (`tests/test_lazy_source_clock*.py`; this replaced the #64 roc3-only
    clock, whose eager chart `source[length]` fallbacks the tapes refute:
    before the first execution, and between executions closer than `length`
    bars, where a US-stock tape kept in a private repository reads the held
    `close[4]` for `ta.mom(close, 3)`, `tests/test_e2e_lazy_held_source.py`);
    `cum`/`barssince`/`valuewhen`/`cross*`/`rising`/`falling`/`math.sum`
    (`LAZY_PER_EXECUTION_TA`) keep the reached-only inline compute, which is
    TradingView's per-execution clock, and never precalc.
    Sites inside `if`/loop/function bodies, `else if` conditions, `var`
    initializers, `request.security` payloads and tuple-returning sites keep
    their existing lowering, but for a `change`/`mom`/`roc` in a top-level
    if block -- an else-if's or a nested if's condition and statements, and
    a value-form if's -- which runs only on the bars its block runs and reads
    the same held source history (`_lazy_edge_ta_hoist_plan`: TradingView's
    `te_lazy_if_source` tape reads `ta.roc(close, 3)` in a nested if from
    the close its third bar held, where the executions' own ring read na;
    `tests/test_e2e_lazy_if_source_clock.py`); loop and switch bodies, a
    `var` initializer and a call read with history keep their lowering. The old "lazy SMA/EMA must not precalc" pins
    (pf-probe-oliver-dual-vol-sma) encoded the refuted per-call clock and were
    re-pinned in `test_codegen_validation_fixes.py`.
    A user function's call read at an offset below such an edge
    (`isNew(s) => inS(s) and not inS(s)[1]`) is the call's value k executions
    of its scope ago -- k bars at the top level, k calls in a function -- as
    TradingView's BINANCE:BTCUSDT 15 tape shows
    (`fixtures/lazy_call_history/tg-lazyhist-btc15`,
    `tests/test_e2e_lazy_call_history.py`): codegen pushes its `_hist_call_*`
    Series once per execution, before the statement, in a top-level statement
    and in a statement of a function body (`codegen/ta.py::
    _lazy_call_history_units`, `_emit_lazy_call_history_hoists`). Only a call
    of a pure function -- one expression of its parameters, bar fields,
    literals, `timeframe.*`/`syminfo.*`, operators and `na`/`nz`/`time`/
    `time_close` or such functions, with arguments of literals, names and
    operators -- is hoisted; any other keeps the call-local push where the
    operand runs, which reads the previous time it ran. An if whose head
    reads such a history and whose block calls a `change`/`mom`/`roc`
    takes both rules: the push before the if, the held source clock in
    its block (`fixtures/cgint7_tv/cgint7_lazy`,
    `tests/test_e2e_cgint7_compositions.py`).
16. **Every call argument is evaluated once.** Pine evaluates each argument
    of a call exactly once per execution; only `and` / `or` and the `?:` arms
    are lazy (a lab tv counting-probe tape, `fixtures/w2_trio_tv/eval_counts`,
    shows every builtin slot below once per bar). A lowering template that
    reads an argument twice, or once per loop iteration, re-runs its C++, so a
    stateful call in it (a `ta.*` compute(), a user function with state) runs
    more than once. `fixnan(x)` did that (TradingView's DMI/ADX helpers then
    double-stepped their RMA), and `nz(x, y)` ran `y` only on na bars. Bind
    such an argument first: `nz` and `fixnan` bind their own, and
    `helpers.evaluate_args_once` binds every argument a template reads
    repeatedly that is not a plain read (`cpp_is_plain_read`: a literal, a
    name or member chain, a literal-offset history read), which keeps plain
    reads byte for byte. `STR_ARGS_READ_REPEATEDLY` and
    `ARRAY_ARGS_READ_REPEATEDLY` (`codegen/tables.py`) list the table-driven
    slots (`math.round`, `str.substring` and `str.replace` bind where they
    lower); add one when a template reads an argument more than once.
    `color.from_gradient` is a warned visual-only stub yielding the na
    colour that still evaluates, once each in parameter order, every
    argument that can have an effect (`_may_have_effects`: any call but a
    pure builtin; `tests/test_e2e_from_gradient_arguments.py`).
    `tests/test_e2e_argument_evaluation_once.py` runs every slot through a
    counting probe and replays the tape; `tests/test_e2e_fixnan_single_eval.py`
    replays a TradingView ADX tape. Keyword arguments take TradingView's
    names -- `nz(source, replacement)`, `fixnan(source)`, `str.repeat(source,
    repeat, separator)` -- and TradingView evaluates them in parameter order
    whatever order they are written in: the support checker binds `nz` and
    `fixnan` to their signatures (`_check_builtin_arguments`, refusing what
    TradingView refuses) and `builtin_keywords` rewrites them positionally
    (`tests/test_e2e_builtin_keyword_names.py`). The same holds for the other
    built-ins whose lowering reads its arguments by position
    (`timeframe.change`, `timeframe.in_seconds`, the trade accessors of
    `strategy.closedtrades` and `strategy.opentrades`, `matrix.new`, and
    `math.random`) and for those that bind by parameter slot
    (`str.format_time`, `timestamp`, `time`, `time_close`): a keyword that
    skips a parameter keeps the later arguments in their own slots, a skipped
    parameter takes its declared default, and a keyword the call does not have
    is refused with `PF-E2027` (`PF-E5090` for math). In Pine v6,
    `when` on the seven strategy order functions instead reports `PF-E2081`
    and explains that v6 removed the argument: wrap the call in an `if`
    statement
    (`tests/test_e2e_keyword_arguments.py`,
    `tests/test_e2e_keyword_arguments_construct.py`,
    `tests/test_e2e_keyword_hole_binding.py`).
17. **A function's last statement is its value.** TradingView returns the
    value of a function's (or an if/switch arm's) last statement, whatever
    the statement: `x := e` and every `x op= e` yield x's new value (typed as
    x: `analyzer._statement_value_type`), `obj.f := e` the field's, a
    declaration `[var] [T] x = e` the variable, a tuple declaration
    `[p, q] = f()` the tuple, and a `for` / `for ... in` / `while` loop the
    value its body's last statement produced on the last iteration that
    reached it (`na` when none did; an if without else or a switch without
    default ending the body is `na` after an iteration that ran no arm).
    `emit_top`'s function emitter and `visit_stmt._emit_body_with_assign`
    handle each through `_statement_value_node` / `_emit_loop_with_assign`,
    and return only a scalar or string value that fits the slot
    (`_tail_value_fits`, judged from the statement, not its emitted local):
    a drawing, UDT or collection handle keeps the statement-then-default
    lowering it always compiled to. Any other last statement falls through
    to the default return. An if without else (an else-if chain without a
    final else, a switch without default) that runs no arm is `na` -- a
    numeric or string na, false for a bool, `na<int64_t>()` for an `int` an
    epoch reaches (see "Epoch ints") -- as a function's last statement,
    nested in a taken arm, and as the value a global, reassigned or local
    variable takes, which does not keep its previous bar's value
    (`visit_stmt._visit_selection_value`;
    `tests/test_e2e_if_without_else_na.py`, tape
    `fixtures/open_items_tv/if_tail_na`).
    A tuple reassignment `[p, q] := f()` is a TradingView syntax error
    (CE10156) and is refused, as are a tuple literal as a variable's value
    (`[a, b] = [x, y]`, `t = [x, y]`: CE10156) and a ternary returning
    tuples (`support_checker._check_tuple_literal_value`,
    `tests/test_tuple_literal_value.py`). `tests/test_e2e_function_tail_value.py` replays
    seven TradingView tapes (`fixtures/w2_trio_tv/tail_*`) and compiles the
    handle shapes.
18. **A script variable read through history in a function body reads its
    call site's history.** TradingView builds the history of a series used
    in a function through each call of it (`lab tv`, BINANCE:ETHUSDT.P 15,
    `tests/fixtures/function_global_history`): in a plain UDF, `x[k]` on a
    script variable (`var` or not) or on `bar_index` is `x` as that call site
    saw it at its latest call at or before `k` bars ago -- one slot per chart
    bar, `na` before the first call, the value at the call even when `x`
    changes later in the bar or the function runs on every bar; each call
    site, a nested one included, has its own. Chart built-ins (`close`,
    `time`, `hl2`, ...) keep the chart's history. The analyzer records the
    reads (`func_global_history_reads` / `_nodes`) and marks the function
    stateful, so every call site gets its own body; each chart-executed body
    owns an `_fn_global_hist_N` Series on the `udf_series_arg` clock (advanced
    in the on_bar preamble, updated at the body's entry;
    `tests/test_e2e_function_global_history.py`). UDT methods, string/color
    variables and request.security-only bodies keep the chart's history. In a
    callable body the clone var remap lists every callable's members for
    nested-instance composition, so a read the analyzer resolved to a global,
    a loop binder or a parameter is not renamed (`_call_site_var_name`):
    renaming a script variable `src` to another callable's `src_cs1` read
    `na`. A callable stateful only through this rule keeps its old shared
    typing when two primitive types meet in one variant, with a WARNING
    (`tests/test_function_global_history_clones.py`).
19. **A binary operator evaluates its left operand first.** C++ leaves the
    order of the operands of `+ - * / %` and of an overloaded operator
    (`std::string`'s `+` and `==`, `std::fmod`) unspecified: AppleClang goes
    left to right, GCC on x86-64 ran `std::string` `operator+`'s right operand
    first. When one operand has an effect
    (`visit_expr._expr_has_ordered_effect`: an array/map/matrix mutation, a
    drawing, an order, a log line, an alert, `runtime.error`, or a user
    function or method doing one or assigning a UDT field) and the other can
    observe it (it is not `_binop_operand_is_order_free`: literals,
    variables, and `str.*`/`math.*`/`color.*`/`ta.*`/`nz`/`na`/cast calls and
    operators over them), `_left_operand_first` emits
    `[&]{ auto __pf_binop_lhs_N = (<left>); return <op>; }()` (`bool` for a
    bool operand: a `std::vector<bool>` element is a proxy), for
    `_visit_binop` and the `request.security` builder alike. `&&`/`||` and
    the relational wrappers already order their operands; every other binop
    keeps its C++ (the 325 corpus, 277 gate and 1,430 population sources are
    byte-identical). TradingView's K-RUNERR tape spells the left-first order
    (`tests/test_e2e_krunerr_array_negative_index.py`);
    `tests/test_binop_operand_order.py` pins the form and the values. A
    call's arguments are ordered only where a lowering binds them:
    `_ordered_user_call_expr` stages a user call with a map effect or a
    temporary receiver, `helpers.evaluate_args_once` (quirk 16) binds the
    arguments a template reads more than once, and the checked `array.*`
    lowerings bind receiver, index and value in turn.

